"""Explicit staged four-node orchestration and one-node V3 maintenance."""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime
from uuid import uuid4

from .inputs_v21 import InputPreparerV21, utc
from .runner_v21 import RunnerV21
from .schema_v21 import (
    Agenda,
    Consolidation,
    Lead,
    MaintenancePatchV3,
    PolicySetV3,
    PolicyV3,
    ResearchResult,
    Review,
    RunResultV21,
    StagedHandoffV21,
)
from .state_v21 import canonical, digest
from .validation_v21 import (
    accept_policy,
    final_identities,
    normalize_agenda,
    records,
    replace_structurally,
    structured,
)


def batches(items, *, count=20, size=128 * 1024):
    current, total = [], 0
    for item in items:
        length = len(canonical(item).encode())
        if current and (len(current) >= count or total + length > size):
            yield current
            current, total = [], 0
        current.append(item)
        total += length
    if current:
        yield current


def policy_batches(items):
    groups = defaultdict(list)
    for item in items:
        groups[item["path"].rsplit("/policies/", 1)[0]].append(item)
    return [batch for group in groups.values() for batch in batches(group)] or [[]]


def delta_has_work(value):
    if isinstance(value, dict) and "reference_view_delta" in value:
        return bool(value["reference_view_delta"].strip() or value.get("removed_event_ids"))
    return bool(value)


def feed_has_work(value):
    if (
        isinstance(value, dict)
        and value.get("contract_version") == "persistent-runtime.o3-maintenance-feed.v1"
    ):
        return any(
            value.get(k)
            for k in ("trade_candidates", "trade_records", "badcase_records", "w3_coverage_gaps")
        ) or delta_has_work(value.get("reference_view_delta"))
    return bool(value)


class Document3OrchestratorV21:
    orchestration_version = "v2.1"

    def __init__(self, *, input_preparer, agent_runner, state, node_assets, policy_repository=None):
        self.state = state
        self.runner = RunnerV21(agent_runner, state, node_assets)
        self.workspace = self.runner.workspace
        self.preparer = InputPreparerV21(input_preparer, self.workspace)
        self.policy_repository = policy_repository

    async def _freeze(self, run_id, identity, mode, prepare):
        existing = self.state.run(run_id)
        if existing:
            result = self.state.start(run_id, identity, existing)
            await self._verify_frozen(run_id, result)
            return result
        assets = self.runner.frozen_assets(mode)  # fail before providers or Worker dispatch
        prepared = await prepare()
        manifest_path = "context/document3/v21/input_manifest.json"
        prepared["files"][manifest_path] = canonical(prepared["manifest"])
        observed = (
            self.policy_repository.get_current_version(identity["ticker"])
            if self.policy_repository
            else 0
        )
        payload = {
            "mode": mode,
            "phase": "PREPARE",
            "prepared": prepared,
            "assets": assets,
            "identity": identity,
            "missing": [],
            "diagnostics": [],
            "supplement_dispatched": False,
            "version_floor": observed or 0,
            "owner_workspaces": {
                owner: f"{run_id}-{owner.lower()}" for owner in prepared["topology"]["owners"]
            },
        }
        if mode == "maintain":
            payload.update(base=prepared["base"], base_coverage=prepared["base_coverage"])
            payload["version_floor"] = max(
                payload["version_floor"], prepared["base"]["policy_set_version"]
            )
        frozen = self.state.start(run_id, identity, payload)
        for path, content in frozen["prepared"]["files"].items():
            await self.workspace.write_text(run_id, path, content)
        return frozen

    async def _verify_frozen(self, run_id, run):
        for path, text in run.get("prepared", {}).get("files", {}).items():
            try:
                current = await self.workspace.read_text(run_id, path)
            except FileNotFoundError:
                await self.workspace.write_text(run_id, path, text)
                continue
            if current.content != text:
                self.state.update(
                    run_id, phase="INTEGRITY_ERROR", integrity_error=f"frozen input:{path}"
                )
                raise ValueError(f"frozen V21 input integrity mismatch:{path}")
        for task in self.state.tasks(run_id).values():
            for path, text in task.get("files", {}).items():
                snapshot = task["snapshots"][path]
                try:
                    current = await self.workspace.read_text(run_id, snapshot)
                except FileNotFoundError as exc:
                    self.state.update(
                        run_id,
                        phase="INTEGRITY_ERROR",
                        integrity_error=f"missing accepted snapshot:{snapshot}",
                    )
                    raise ValueError(
                        f"accepted task snapshot integrity mismatch:{snapshot}"
                    ) from exc
                if current.content != text:
                    self.state.update(
                        run_id,
                        phase="INTEGRITY_ERROR",
                        integrity_error=f"accepted snapshot:{snapshot}",
                    )
                    raise ValueError(f"accepted task snapshot integrity mismatch:{snapshot}")

    async def _turn(self, run_id, owner, phase, key, outputs, task, extra=None):
        run = self.state.run(run_id)
        inputs = dict(run["prepared"]["files"])
        current_shell = next(
            (s for s in run["prepared"]["topology"]["shells"] if s["slot"] == owner), None
        )
        for path in list(inputs):
            if path.startswith("context/document3/v21/d2/") and (
                not current_shell or path != current_shell["path"]
            ):
                inputs.pop(path)
        for entry in run["prepared"]["manifest"]:
            if entry["availability"] != "available":
                inputs.pop(entry["path"], None)
        inputs.update(extra or {})
        task = {**task, "input_diagnostics": run["prepared"]["warnings"]}
        late_files, late_leads = self._late_materials(run_id)
        if phase == "integration":
            inputs.update(late_files)
            task = {**task, "late_leads": late_leads}
        if phase in {"build", "integration"}:
            outputs = [*outputs, f"output/work/v21/late/{digest(key)[:24]}.jsonl"]
        await self._write_view(run_id)
        result = await self.runner.turn(
            run_id=run_id,
            ticker=run["identity"]["ticker"],
            owner=owner,
            phase=phase,
            key=key,
            as_of=datetime.fromisoformat(run["prepared"]["as_of"]),
            inputs=inputs,
            outputs=outputs,
            task=task,
        )
        await self._write_view(run_id)
        return result

    def _late_materials(self, run_id):
        files, leads = {}, []
        for record in self.state.tasks(run_id).values():
            for path, text in record.get("files", {}).items():
                if path.endswith(".late.jsonl") or path.startswith("output/work/v21/late/"):
                    files[path] = text
                    parsed, _ = records(text, Lead)
                    leads.extend(
                        {"ref": f"{path}#L{line}", "lead": lead.model_dump(mode="json")}
                        for line, lead in parsed
                    )
        return files, leads

    async def _write_view(self, run_id):
        run = self.state.run(run_id)
        tasks = self.state.tasks(run_id)
        drafts = self.state.drafts(run_id)
        view = {
            **run,
            "task_metrics": {
                "total": len(tasks),
                "by_status": dict(Counter(t["status"] for t in tasks.values())),
                "attempts": sum(t["attempt_count"] for t in tasks.values()),
                "readable_records": sum(len(t.get("files", {})) for t in tasks.values()),
                "readable_policy_count": len(drafts),
                "readable_condition_count": sum(
                    len(d["policy"]["activation_conditions"]) for d in drafts.values()
                ),
                "quarantined_policy_count": len(
                    {
                        i["path"]
                        for i in run.get("diagnostics", [])
                        if isinstance(i, dict)
                        and i.get("error")
                        and i.get("path")
                        and "/policies/" in i["path"]
                        and i["path"] not in drafts
                    }
                ),
                "quarantined_condition_count": sum(
                    len(d.get("errors", [])) for d in drafts.values()
                ),
                "main_tasks": {
                    k: t["status"] for k, t in tasks.items() if k.startswith("research:")
                },
                "supplement_tasks": {
                    k: t["status"] for k, t in tasks.items() if k.startswith("supplement:")
                },
            },
        }
        await self.workspace.write_text(
            run_id, "artifacts/document3/v21_state.json", canonical(view)
        )

    async def _json(self, run_id, path, model):
        try:
            content = (await self.workspace.read_text(run_id, path)).content
            if content is None:
                return None
            value, issues = structured(content, model)
            parsed = self.state.run(run_id).get("parse_issues", {})
            parsed[path] = issues
            self.state.update(run_id, parse_issues=parsed)
            return value
        except (ValueError, FileNotFoundError):
            return None

    async def _build(self, run_id, agenda, *, supplement=False):
        by_name = {t.name: t for t in agenda.topics}
        queues = defaultdict(list)
        owners = self.state.run(run_id)["prepared"]["topology"]["owners"]
        for ordinal, names in enumerate(agenda.waves):
            public_owner = by_name[names[0]].owner
            targets = [slot for slot, name in owners.items() if name == public_owner]
            alias = (
                self.state.run(run_id)["prepared"]["topology"]
                .get("route_aliases", {})
                .get(public_owner)
            )
            slot = (
                alias or public_owner
                if public_owner in {"OPEN", "GLOBAL"}
                else alias or targets[0]
                if len(targets) == 1
                else alias or "OPEN"
            )
            queues[slot].append((ordinal, names))
        phase = "supplement" if supplement else "research"

        async def owner_queue(owner, waves):
            for ordinal, names in waves:
                prefix = f"output/work/v21/{phase}/{owner}/{ordinal:04d}/"
                task = {
                    "topics": [by_name[n].model_dump(mode="json") for n in names],
                    "round": phase,
                    "ordinal": ordinal,
                    "owner_shell": next(
                        (
                            s
                            for s in self.state.run(run_id)["prepared"]["topology"]["shells"]
                            if s["slot"] == owner
                        ),
                        None,
                    ),
                }
                accepted_task = await self._turn(
                    run_id, owner, "build", f"{phase}:{owner}:{ordinal}", [prefix], task
                )
                for path, text in accepted_task.get("files", {}).items():
                    if path.startswith(prefix + "policies/") and path.endswith(".json"):
                        try:
                            raw = json.loads(text)
                            basis = self.state.run(run_id).get("basis", {})
                            inherited = next(
                                (
                                    p
                                    for p in basis.values()
                                    if p["policy_id"] == raw.get("policy_id")
                                ),
                                None,
                            )
                            if not inherited and raw.get("policy_id") not in self.state.run(run_id)[
                                "prepared"
                            ].get("previous_policy_ids", []):
                                raw.pop("policy_id", None)
                            policy, _ = accept_policy(
                                raw, state=self.state, run_id=run_id, path=path, inherited=inherited
                            )
                            if policy:
                                await self.workspace.write_text(
                                    run_id, path, policy.model_dump_json(indent=2)
                                )
                                await self.workspace.write_text(
                                    accepted_task["workspace_run_id"],
                                    path,
                                    policy.model_dump_json(indent=2),
                                )
                        except (ValueError, TypeError):
                            continue

        await asyncio.gather(*(owner_queue(owner, waves) for owner, waves in queues.items()))
        results, candidate_paths, diagnostics = {}, [], []
        drafts = self.state.drafts(run_id)
        for owner, waves in queues.items():
            for ordinal, names in waves:
                key = f"{phase}:{owner}:{ordinal}"
                task = self.state.task(run_id, key)
                prefix = f"output/work/v21/{phase}/{owner}/{ordinal:04d}/"
                raw = task.get("files", {}).get(prefix + "results.jsonl", "")
                parsed, bad = records(raw, ResearchResult)
                diagnostics.extend({"task": key, **b} for b in bad)
                for line, result in parsed:
                    if result.topic in names:
                        results.setdefault(result.topic, []).append(result.model_dump(mode="json"))
                    else:
                        diagnostics.append(
                            {"task": key, "line": line, "warning": "unknown topic result"}
                        )
                for path, text in task.get("files", {}).items():
                    if path.startswith(prefix + "policies/") and path.endswith(".json"):
                        try:
                            raw_policy = json.loads(text)
                            # Only a basis identity is eligible for explicit inheritance.
                            basis = self.state.run(run_id).get("basis", {})
                            allowed = {p["policy_id"] for p in basis.values()} | set(
                                self.state.run(run_id)["prepared"].get("previous_policy_ids", [])
                            )
                            if path not in drafts and raw_policy.get("policy_id") not in allowed:
                                raw_policy.pop("policy_id", None)
                            inherited = next(
                                (
                                    p
                                    for p in basis.values()
                                    if p["policy_id"] == raw_policy.get("policy_id")
                                ),
                                None,
                            )
                            policy, errors = accept_policy(
                                raw_policy,
                                state=self.state,
                                run_id=run_id,
                                path=path,
                                inherited=inherited,
                            )
                            diagnostics.extend({"path": path, "error": e} for e in errors)
                            if policy:
                                candidate_paths.append(path)
                                await self.workspace.write_text(
                                    run_id, path, policy.model_dump_json(indent=2)
                                )
                                await self.workspace.write_text(
                                    task["workspace_run_id"], path, policy.model_dump_json(indent=2)
                                )
                        except (ValueError, TypeError) as exc:
                            diagnostics.append({"path": path, "error": str(exc)[:300]})
        missing = []
        for topic in agenda.topics:
            values = results.get(topic.name, [])
            if len({canonical(v) for v in values}) != 1:
                missing.append(f"{phase} result missing/conflicting:{topic.name}")
            else:
                for path in values[0]["policies"]:
                    if path not in candidate_paths:
                        missing.append(f"{phase} result Policy unavailable:{topic.name}:{path}")
        return results, list(dict.fromkeys(candidate_paths)), missing, diagnostics

    async def initialize(
        self,
        *,
        ticker,
        as_of=None,
        cutoff_at=None,
        document2_run_id=None,
        source_global_run_id=None,
        event_library_version=None,
        additional_materials=(),
        run_id=None,
    ):
        ticker = ticker.upper()
        as_of = utc(as_of or cutoff_at or datetime.now().astimezone())
        run_id = run_id or f"d3v21-{ticker.lower()}-{uuid4().hex[:24]}"
        identity = {
            "ticker": ticker,
            "mode": "initialize",
            "version": "v2.1",
            "schema": "document3.v3",
            "as_of": as_of.isoformat(),
            "document2_run_id": document2_run_id,
            "source_global_run_id": source_global_run_id,
            "event_library_version": event_library_version,
            "additional_materials": [
                str(x) if not isinstance(x, dict) else x for x in additional_materials
            ],
        }
        run = await self._freeze(
            run_id,
            identity,
            "initialize",
            lambda: self.preparer.prepare(
                ticker=ticker,
                as_of=as_of,
                document2_run_id=document2_run_id,
                source_global_run_id=source_global_run_id,
                event_library_version=event_library_version,
                additional_materials=additional_materials,
            ),
        )
        if run.get("commit"):
            return await self._publish(run_id)
        owners = run["prepared"]["topology"]["owners"]
        if "agenda" not in run:
            self.state.update(run_id, phase="DISCOVERY")
            await asyncio.gather(
                *(
                    self._turn(
                        run_id,
                        owner,
                        "discovery",
                        f"discovery:{owner}",
                        [
                            f"output/work/v21/discovery/{owner}.jsonl",
                            f"output/work/v21/discovery/{owner}.late.jsonl",
                        ],
                        {
                            "owner_shell": next(
                                (
                                    s
                                    for s in run["prepared"]["topology"]["shells"]
                                    if s["slot"] == owner
                                ),
                                None,
                            )
                        },
                    )
                    for owner in owners
                )
            )
            leads, raw_inputs, diagnostics, discovery_missing = [], {}, [], []
            for owner in owners:
                task = self.state.task(run_id, f"discovery:{owner}")
                path = f"output/work/v21/discovery/{owner}.jsonl"
                raw = task.get("files", {}).get(path, "")
                if path not in task.get("files", {}) or task["status"] == "FAILED":
                    discovery_missing.append(f"Discovery unavailable:{owner}")
                raw_inputs[path] = raw
                parsed, bad = records(raw, Lead)
                leads.extend(
                    {"owner": owner, "ref": f"{path}#L{line}", "lead": lead.model_dump(mode="json")}
                    for line, lead in parsed
                )
                diagnostics.extend({"path": path, **b} for b in bad)
            self.state.update(run_id, phase="PLANNING", scan=leads, scan_inputs=raw_inputs)
            cumulative = None
            planning_batches = list(batches(leads)) or [[]]
            for i, batch in enumerate(planning_batches):
                extra = dict(raw_inputs)
                if cumulative:
                    extra["context/document3/v21/planning_previous.json"] = canonical(
                        cumulative.model_dump(mode="json")
                    )
                await self._turn(
                    run_id,
                    "GLOBAL",
                    "planning",
                    f"planning:{i}",
                    ["output/work/v21/agenda.json"],
                    {"leads": batch, "ordinal": i, "final_batch": i == len(planning_batches) - 1},
                    extra,
                )
                candidate = await self._json(run_id, "output/work/v21/agenda.json", Agenda)
                if candidate:
                    cumulative = candidate
            agenda, warnings = normalize_agenda(
                cumulative.model_dump(mode="json") if cumulative else {"topics": [], "waves": []},
                owners,
                run["prepared"]["topology"].get("route_aliases"),
            )
            missing = [*discovery_missing, *([] if cumulative else ["Planning agenda unavailable"])]
            self.state.update(
                run_id,
                agenda=agenda.model_dump(mode="json"),
                missing=missing,
                diagnostics=[*diagnostics, *warnings],
                phase="BUILD",
            )
        run = self.state.run(run_id)
        agenda = Agenda.model_validate(run["agenda"])
        if "basis" not in run:
            results, paths, missing, diagnostics = await self._build(run_id, agenda)
            drafts = self.state.drafts(run_id)
            basis = {p: drafts[p]["policy"] for p in paths}
            # Freeze complete accepted B bytes and the batch schedule before Integration.
            for path, policy in basis.items():
                await self.workspace.write_text(
                    run_id,
                    f"artifacts/document3/checkpoints/basis/{digest(path)}.json",
                    canonical(policy),
                )
            schedule = policy_batches([{"path": p, "policy": v} for p, v in basis.items()])
            self.state.update(
                run_id,
                basis=basis,
                main_results=results,
                integration_schedule=schedule,
                missing=[*run["missing"], *missing],
                diagnostics=[*run["diagnostics"], *diagnostics],
                phase="INTEGRATION",
            )
        run = self.state.run(run_id)
        if "review" not in run:
            review = None
            integration_missing = []
            for i, batch in enumerate(run["integration_schedule"]):
                extra = {x["path"]: canonical(x["policy"]) for x in batch}
                extra.update(run.get("scan_inputs", {}))
                extra["context/document3/v21/policy_index.json"] = canonical(
                    [
                        {"path": p, "policy_id": v["policy_id"], "title": v["title"]}
                        for p, v in run["basis"].items()
                    ]
                )
                if review:
                    extra["context/document3/v21/review_previous.json"] = canonical(
                        review.model_dump(mode="json")
                    )
                batch_task = await self._turn(
                    run_id,
                    "GLOBAL",
                    "integration",
                    f"integration:main:{i}",
                    ["output/work/v21/review.json", "output/work/v21/supplement_agenda.json"],
                    {
                        "stage": "review",
                        "batch": [x["path"] for x in batch],
                        "final_batch": i == len(run["integration_schedule"]) - 1,
                    },
                    extra,
                )
                parsed = await self._json(run_id, "output/work/v21/review.json", Review)
                if parsed:
                    review = parsed
                if parsed is None or batch_task["status"] == "FAILED":
                    integration_missing.append(f"Integration main batch unavailable:{i}")
            supplement = await self._json(run_id, "output/work/v21/supplement_agenda.json", Agenda)
            if supplement is None and review:
                supplement = Agenda(topics=review.research_requests, waves=[])
            supplement, diagnostics = normalize_agenda(
                supplement.model_dump(mode="json") if supplement else {"topics": [], "waves": []},
                owners,
                run["prepared"]["topology"].get("route_aliases"),
            )
            frozen = canonical(supplement.model_dump(mode="json"))
            await self.workspace.write_text(
                run_id, "artifacts/document3/checkpoints/supplement_agenda.json", frozen
            )
            # Atomic flag is durable before the first supplement dispatch.
            self.state.update(
                run_id,
                review=review.model_dump(mode="json") if review else None,
                supplement_agenda=supplement.model_dump(mode="json"),
                supplement_hash=digest(frozen),
                supplement_dispatched=True,
                diagnostics=[*run["diagnostics"], *diagnostics],
                missing=[*run["missing"], *integration_missing],
                phase="SUPPLEMENT",
            )
        run = self.state.run(run_id)
        if "supplement_paths" not in run:
            supplement = Agenda.model_validate(run["supplement_agenda"])
            results, paths, missing, diagnostics = await self._build(
                run_id, supplement, supplement=True
            )
            self.state.update(
                run_id,
                supplement_paths=paths,
                supplement_results=results,
                missing=[*run["missing"], *missing],
                diagnostics=[*run["diagnostics"], *diagnostics],
                phase="FINALIZE",
            )
        run = self.state.run(run_id)
        if "post_supplement_review" not in run:
            affected = set()
            for relation in (run.get("review") or {}).get("relations", []):
                affected.update(relation["policies"])
            for topic in run["supplement_agenda"]["topics"]:
                affected.update(topic.get("ref", []))
            basis_paths = [
                p
                for p, policy in run["basis"].items()
                if p in affected or policy["policy_id"] in affected
            ]
            refs = list(dict.fromkeys([*run["supplement_paths"], *basis_paths]))
            drafts = self.state.drafts(run_id)
            schedule = run.get("post_supplement_schedule") or policy_batches(
                [{"path": p, "policy": drafts[p]["policy"]} for p in refs]
            )
            self.state.update(run_id, post_supplement_schedule=schedule)
            review = run.get("review")
            missing = list(run["missing"])
            if refs:
                for i, batch in enumerate(schedule):
                    extra = {x["path"]: canonical(x["policy"]) for x in batch}
                    extra["context/document3/v21/review_previous.json"] = canonical(review)
                    extra["context/document3/v21/policy_index.json"] = canonical(
                        [
                            {"path": p, "policy_id": v["policy_id"], "title": v["title"]}
                            for p, v in run["basis"].items()
                        ]
                    )
                    item = await self._turn(
                        run_id,
                        "GLOBAL",
                        "integration",
                        f"integration:supplement:{i}",
                        ["output/work/v21/post_supplement_review.json"],
                        {"stage": "post_supplement_review", "batch": [x["path"] for x in batch]},
                        extra,
                    )
                    parsed = await self._json(
                        run_id, "output/work/v21/post_supplement_review.json", Review
                    )
                    if parsed:
                        review = parsed.model_dump(mode="json")
                    if parsed is None or item["status"] == "FAILED":
                        missing.append(f"Integration supplement batch unavailable:{i}")
            self.state.update(
                run_id, post_supplement_review=review, affected_paths=refs, missing=missing
            )
            run = self.state.run(run_id)
        if "final_candidates" not in run:
            drafts = self.state.drafts(run_id)
            refs = run["affected_paths"] if run["supplement_paths"] else list(run["basis"])
            # Final writes are finite file batches; only structural application follows.
            schedule = run.get("final_schedule") or policy_batches(
                [{"path": p, "policy": drafts[p]["policy"]} for p in refs]
            )
            self.state.update(run_id, final_schedule=schedule)
            final_candidates = {}
            for i, batch in enumerate(schedule):
                extra = {x["path"]: canonical(x["policy"]) for x in batch}
                extra["context/document3/v21/review_frozen.json"] = canonical(
                    run.get("post_supplement_review")
                )
                prefix = f"output/work/v21/final/policies/{i:04d}/"
                task = await self._turn(
                    run_id,
                    "GLOBAL",
                    "integration",
                    f"integration:final:{i}",
                    [prefix],
                    {"stage": "final_write", "ordinal": i, "batch": [x["path"] for x in batch]},
                    extra,
                )
                for path, text in task.get("files", {}).items():
                    if path.endswith(".json"):
                        try:
                            raw = json.loads(text)
                            inherited = next(
                                (
                                    v
                                    for v in run["basis"].values()
                                    if v["policy_id"] == raw.get("policy_id")
                                ),
                                None,
                            )
                            if not inherited:
                                raw.pop("policy_id", None)
                            policy, _ = accept_policy(
                                raw, state=self.state, run_id=run_id, path=path, inherited=inherited
                            )
                            if policy:
                                final_candidates[path] = policy.model_dump(mode="json")
                        except (ValueError, TypeError):
                            continue
            self.state.update(run_id, final_candidates=final_candidates)
        run = self.state.run(run_id)
        candidates = {p: PolicyV3.model_validate(v) for p, v in run["final_candidates"].items()}
        drafts = self.state.drafts(run_id)
        candidates.update(
            {p: PolicyV3.model_validate(drafts[p]["policy"]) for p in run["supplement_paths"]}
        )
        extra = {p: policy.model_dump_json(indent=2) for p, policy in candidates.items()}
        extra["context/document3/v21/basis.json"] = canonical(run["basis"])
        extra["context/document3/v21/review_frozen.json"] = canonical(run.get("review"))
        await self._turn(
            run_id,
            "GLOBAL",
            "integration",
            "integration:consolidation",
            ["output/work/v21/consolidation.json"],
            {
                "stage": "consolidation",
                "main_agenda": run["agenda"],
                "supplement_agenda": run["supplement_agenda"],
                "missing": run["missing"],
            },
            extra,
        )
        consolidation = await self._json(
            run_id, "output/work/v21/consolidation.json", Consolidation
        )
        basis = [PolicyV3.model_validate(v) for v in run["basis"].values()]
        missing = list(run["missing"])
        if consolidation:
            final, replacement_receipt = replace_structurally(
                basis, candidates, consolidation.replacements
            )
            final, id_conflicts = final_identities(final)
            if id_conflicts:
                missing.extend(f"final Policy ID conflict:{p}" for p in id_conflicts)
            if replacement_receipt["failed"]:
                missing.append("replacement groups failed")
            all_topics = [*agenda.topics, *Agenda.model_validate(run["supplement_agenda"]).topics]
            coverage = consolidation.model_dump(mode="json")
            final_ids = {p.policy_id for p in final}
            paths = {
                **{p: v["policy_id"] for p, v in run["basis"].items()},
                **{p: v.policy_id for p, v in candidates.items()},
            }
            covered = set()
            for item in coverage["coverage"]:
                resolved = [paths.get(p) for p in item["policies"]]
                if any(p not in final_ids for p in resolved):
                    missing.append(f"coverage points outside F:{item['topic']}")
                else:
                    covered.add(item["topic"])
                item["policy_ids"] = [p for p in resolved if p in final_ids]
            for topic in all_topics:
                if topic.name not in covered:
                    missing.append(f"coverage disposition missing:{topic.name}")
            # Explicit final disposition can close an unusable historical candidate.
            missing = [
                m
                for m in missing
                if not any(
                    m.startswith(f"research result Policy unavailable:{topic}:")
                    or m.startswith(f"supplement result Policy unavailable:{topic}:")
                    for topic in covered
                )
            ]
            removed = {
                pid
                for i in replacement_receipt["successful"]
                for pid in consolidation.replacements[i].before
            }
            for issue in run["diagnostics"]:
                if (
                    isinstance(issue, dict)
                    and issue.get("error")
                    and issue.get("path") in run["basis"]
                ):
                    if run["basis"][issue["path"]]["policy_id"] not in removed:
                        missing.append(f"retained draft structural omission:{issue['path']}")
            if consolidation.remaining_gaps:
                missing.append("remaining research gaps")
            for request in (run.get("post_supplement_review") or {}).get("research_requests", []):
                if request["name"] not in covered:
                    coverage["remaining_gaps"].append(
                        {"name": request["name"], "reason": request["brief"]}
                    )
                    missing.append(f"post-supplement request remains:{request['name']}")
            closed_refs = {
                ref for topic in all_topics if topic.name in covered for ref in topic.ref
            }
            _, late_leads = self._late_materials(run_id)
            for entry in late_leads:
                if entry["ref"] not in closed_refs:
                    coverage["remaining_gaps"].append(
                        {"name": entry["lead"]["name"], "reason": entry["lead"]["lead"]}
                    )
                    missing.append(f"unresearched late lead:{entry['ref']}")
        else:
            final, id_conflicts = final_identities(basis)
            missing.extend(f"final Policy ID conflict:{p}" for p in id_conflicts)
            replacement_receipt = {"successful": [], "failed": {}}
            coverage = {
                "coverage": [],
                "remaining_gaps": [],
                "summary": "Integration consolidation unavailable",
                "publish_empty": False,
            }
            missing.append("Integration consolidation unavailable")
        if run.get("review") is None:
            missing.append("Integration review unavailable")
        if any(
            i.get("error")
            for i in self.state.run(run_id)
            .get("parse_issues", {})
            .get("output/work/v21/consolidation.json", [])
        ):
            missing.append("Consolidation locally malformed records")
        # Only current required missing facts affect PARTIAL, not historical diagnostics.
        status = "PARTIAL" if missing else "COMPLETE"
        self.state.update(
            run_id,
            final=[p.model_dump(mode="json") for p in final],
            coverage=coverage,
            replacement_receipt=replacement_receipt,
            missing=list(dict.fromkeys(missing)),
            status=status,
        )
        return await self._publish(run_id)

    async def _publish(self, run_id):
        from .runtime_projection import project_policy_set_v3

        run = self.state.run(run_id)
        if not run.get("commit"):
            version = self.state.reserve(run_id, run["identity"]["ticker"], run["version_floor"])
            published_at = datetime.now().astimezone().isoformat()
            self.state.update(
                run_id,
                commit={
                    "version": version,
                    "published_at": published_at,
                    "final_hash": digest(run["final"]),
                    "coverage_hash": digest(run["coverage"]),
                    "publish_empty": run["coverage"].get("publish_empty", False),
                },
                phase="PUBLISH",
            )
            run = self.state.run(run_id)
        commit = run["commit"]
        if (
            digest(run["final"]) != commit["final_hash"]
            or digest(run["coverage"]) != commit["coverage_hash"]
        ):
            raise ValueError("publication checkpoint integrity mismatch")
        policy_set = PolicySetV3(
            ticker=run["identity"]["ticker"],
            policy_set_version=commit["version"],
            as_of=run["prepared"]["as_of"],
            publication_state=run["status"],
            document2_ref=run["prepared"].get("document2_ref"),
            event_library_ref=run["prepared"].get("event_library_ref"),
            policies=run["final"],
            published_at=commit["published_at"],
        )
        metadata = {
            **run["coverage"],
            "schema_version": "document3.coverage.v3",
            "ticker": policy_set.ticker,
            "policy_set_version": commit["version"],
            "as_of": policy_set.as_of.isoformat(),
            "publication_state": policy_set.publication_state,
            "host_missing": run["missing"],
        }
        markdown = (
            f"# {policy_set.ticker} Document3 V3\n\n"
            f"Version: {commit['version']} · {run['status']} · staged\n\n"
            f"As of: {policy_set.as_of.isoformat()}\n\n"
            f"Coverage: {canonical(run['coverage'])}\n\n"
        )
        for policy in policy_set.policies:
            markdown += f"## {policy.title}\n\n{policy.transmission}\n\n"
            for condition in policy.activation_conditions:
                markdown += (
                    f"- {condition.condition_id} / {condition.decision} / "
                    f"{condition.trigger_layer}: {condition.criterion}\n"
                    f"  - Reference: {condition.calibration.reference_state}\n"
                    f"  - Boundary: {condition.calibration.trigger_boundary}\n"
                )
            markdown += "\n"
        content = {
            "document3.json": policy_set.model_dump_json(indent=2),
            "runtime_projection.json": canonical(project_policy_set_v3(policy_set)),
            "coverage_map.json": canonical(metadata),
            "document3.md": markdown,
        }
        paths, hashes = {}, {}
        for name, text in content.items():
            path = f"artifacts/document3/releases/{commit['version']}/{name}"
            await self.workspace.write_text(run_id, path, text)
            await self.workspace.write_text(run_id, f"output/final/{name}", text)
            file = await self.workspace.read_text(run_id, path)
            if file.content != text or digest(file.content) != digest(text):
                raise ValueError("release four-file checksum mismatch")
            paths[name], hashes[name] = path, digest(text)
        published = await self.workspace.publish(run_id, list(paths.values()))
        release_manifest = None
        for item in published.files:
            if item.relative_path.startswith("published/") and item.relative_path.endswith(
                "/manifest.json"
            ):
                file = await self.workspace.read_text(run_id, item.relative_path)
                value = json.loads(file.content)
                entries = {r["relative_path"]: r["sha256"] for r in value["artifacts"]}
                if entries == {paths[name]: hashes[name] for name in paths}:
                    release_manifest = item.relative_path
                    break
        if release_manifest is None:
            raise ValueError("checksum release manifest unavailable")
        release_root = release_manifest.rsplit("/", 1)[0]
        for name, path in paths.items():
            copy_path = release_root + "/" + path.removeprefix("artifacts/")
            copied = await self.workspace.read_text(run_id, copy_path)
            if copied.content != content[name] or copied.sha256 != hashes[name]:
                raise ValueError("published release four-file checksum mismatch")
        receipt = {
            **commit,
            "files": paths,
            "hashes": hashes,
            "projection": project_policy_set_v3(policy_set),
            "release_manifest_path": release_manifest,
        }
        self.state.save_staged_v3(run_id, policy_set, receipt)
        self.state.update(run_id, phase="STAGED", handoff=receipt)
        await self._write_view(run_id)
        return RunResultV21(
            run_id=run_id,
            ticker=policy_set.ticker,
            mode=run["mode"],
            status=run["status"],
            missing=run["missing"],
            handoff=StagedHandoffV21(
                policy_set_version=commit["version"],
                files=paths,
                hashes=hashes,
                release_manifest_path=release_manifest,
            ),
        )

    async def maintain(
        self,
        *,
        ticker,
        base_policy_version,
        as_of,
        maintenance_feed=None,
        delta=None,
        event_library_version=None,
        additional_materials=(),
        explicit_maintenance=False,
        run_id=None,
    ):
        ticker, as_of = ticker.upper(), utc(as_of)
        if hasattr(maintenance_feed, "model_dump"):
            maintenance_feed = maintenance_feed.model_dump(mode="json")
        if hasattr(delta, "model_dump"):
            delta = delta.model_dump(mode="json")
        if delta is None and isinstance(maintenance_feed, dict):
            delta = maintenance_feed.get("reference_view_delta")
        for value in (maintenance_feed, delta):
            if isinstance(value, dict) and value.get("ticker", ticker).upper() != ticker:
                raise ValueError("V21 maintenance feed/delta ticker mismatch")
        run_id = run_id or f"d3v21-{ticker.lower()}-{uuid4().hex[:24]}"
        identity = {
            "ticker": ticker,
            "mode": "maintain",
            "version": "v2.1",
            "schema": "document3.v3",
            "as_of": as_of.isoformat(),
            "base_policy_version": base_policy_version,
            "event_library_version": event_library_version,
            "feed": maintenance_feed,
            "delta": delta,
            "explicit": explicit_maintenance,
            "additional_materials": [
                str(x) if not isinstance(x, dict) else x for x in additional_materials
            ],
        }
        existing = self.state.run(run_id)
        if existing:
            run = self.state.start(run_id, identity, existing)
            await self._verify_frozen(run_id, run)
        else:
            base = self.state.get_staged_v3(ticker, base_policy_version)
            if base is None:
                raise ValueError("V21 MAINTAIN requires an explicit staged document3.v3 base")
            base_row = self.state.get_staged_by_run(self._base_run_id(ticker, base_policy_version))
            base_file = await self.workspace.read_text(
                self._base_run_id(ticker, base_policy_version),
                base_row[1]["files"]["document3.json"],
            )
            if (
                digest(base_file.content) != base_row[1]["hashes"]["document3.json"]
                or PolicySetV3.model_validate_json(base_file.content) != base
            ):
                raise ValueError("base PolicySet snapshot hash mismatch")
            coverage_file = base_row[1]["files"]["coverage_map.json"]
            coverage_text = (
                await self.workspace.read_text(
                    self._base_run_id(ticker, base_policy_version), coverage_file
                )
            ).content
            if digest(coverage_text) != base_row[1]["hashes"]["coverage_map.json"]:
                raise ValueError("base coverage hash mismatch")
            base_coverage = json.loads(coverage_text)
            if (
                not feed_has_work(maintenance_feed)
                and not delta_has_work(delta)
                and not base_coverage.get("remaining_gaps")
                and not explicit_maintenance
                and (
                    event_library_version is None
                    or (
                        base.event_library_ref
                        and event_library_version == base.event_library_ref.version
                    )
                )
            ):
                # NOOP needs no business assets and no Agent session.
                payload = {
                    "mode": "maintain",
                    "identity": identity,
                    "phase": "NOOP",
                    "status": "NOOP",
                }
                self.state.start(run_id, identity, payload)
                await self._write_view(run_id)
                return RunResultV21(
                    run_id=run_id,
                    ticker=ticker,
                    mode="maintain",
                    status="NOOP",
                    policy_set_version=base_policy_version,
                )

            async def prepare():
                prepared = await self.preparer.prepare(
                    ticker=ticker,
                    as_of=as_of,
                    event_library_version=event_library_version,
                    additional_materials=additional_materials,
                )
                prepared["document2_ref"] = (
                    base.document2_ref.model_dump(mode="json") if base.document2_ref else None
                )
                frozen_delta = delta
                reader = self.preparer.legacy._event_library_reader
                if (
                    frozen_delta is None
                    and reader
                    and hasattr(reader, "reference_view_delta")
                    and prepared["event_library_ref"]
                ):
                    frozen_delta = reader.reference_view_delta(
                        ticker,
                        from_version=base.event_library_ref.version
                        if base.event_library_ref
                        else 0,
                        to_version=prepared["event_library_ref"]["version"],
                    )
                    if hasattr(frozen_delta, "model_dump"):
                        frozen_delta = frozen_delta.model_dump(mode="json")
                prepared["base"] = base.model_dump(mode="json")
                prepared["base_coverage"] = base_coverage
                for name, value in {
                    "base_policy_set.json": base.model_dump(mode="json"),
                    "base_coverage.json": base_coverage,
                    "maintenance_feed.json": maintenance_feed,
                    "delta.json": frozen_delta,
                }.items():
                    prepared["files"][f"context/document3/v21/shared/{name}"] = canonical(value)
                return prepared

            run = await self._freeze(run_id, identity, "maintain", prepare)
            run = self.state.update(
                run_id,
                base=base.model_dump(mode="json"),
                base_coverage=base_coverage,
                version_floor=max(run["version_floor"], base_policy_version),
            )
        if run.get("status") == "NOOP":
            await self._write_view(run_id)
            return RunResultV21(
                run_id=run_id,
                ticker=ticker,
                mode="maintain",
                status="NOOP",
                policy_set_version=base_policy_version,
            )
        if run.get("commit"):
            return await self._publish(run_id)
        if run.get("status") == "DEGRADED":
            return RunResultV21(
                run_id=run_id,
                ticker=ticker,
                mode="maintain",
                status="DEGRADED",
                missing=run["missing"],
            )
        task = await self._turn(
            run_id,
            "MAINTAIN",
            "maintain",
            "maintain:0",
            ["output/work/v21/maintenance_patch.json"],
            {"base_policy_set_version": base_policy_version},
        )
        patch = await self._json(
            run_id, "output/work/v21/maintenance_patch.json", MaintenancePatchV3
        )
        if patch is None or patch.base_policy_set_version != base_policy_version:
            missing = ["maintenance patch unavailable or wrong base"]
            self.state.update(run_id, status="DEGRADED", missing=missing, phase="DEGRADED")
            await self._write_view(run_id)
            return RunResultV21(
                run_id=run_id, ticker=ticker, mode="maintain", status="DEGRADED", missing=missing
            )
        base = PolicySetV3.model_validate(run["base"])
        final = {p.policy_id: p for p in base.policies}
        grouped = defaultdict(list)
        malformed_upserts = []
        for i, raw in enumerate(patch.upsert_policies):
            if not isinstance(raw, dict):
                malformed_upserts.append(f"maintenance upsert is not an object:{i}")
                continue
            if raw.get("policy_id") is not None and not isinstance(raw["policy_id"], str):
                malformed_upserts.append(f"maintenance invalid Policy ID:{i}")
                continue
            grouped[raw.get("policy_id") or f"new:{i}"].append((i, raw))
        missing, diagnostics = malformed_upserts, []
        patch_issues = (
            self.state.run(run_id)
            .get("parse_issues", {})
            .get("output/work/v21/maintenance_patch.json", [])
        )
        missing.extend(
            f"maintenance patch malformed:{i['field']}:{i['ordinal']}"
            for i in patch_issues
            if i.get("error")
        )
        retire = set(patch.retire_policy_ids)
        for pid, group in grouped.items():
            if pid in retire or len({canonical(raw) for _, raw in group}) > 1:
                missing.append(f"maintenance conflicting upsert/retire:{pid}")
                retire.discard(pid)
                continue
            i, raw = group[0]
            if raw.get("policy_id") and pid not in final:
                missing.append(f"unknown inherited Policy:{pid}")
                continue
            policy, errors = accept_policy(
                raw,
                state=self.state,
                run_id=run_id,
                path=f"output/work/v21/maintenance/{i:04d}.json",
                inherited=final[pid].model_dump(mode="json") if pid in final else None,
            )
            if errors:
                missing.extend(f"maintenance structural:{e}" for e in errors)
            if policy:
                final[policy.policy_id] = policy
        for pid in retire:
            if pid in final:
                del final[pid]
            else:
                diagnostics.append(f"unknown retire:{pid}")
        gaps = (
            [g.model_dump(mode="json") for g in patch.remaining_gaps]
            if "remaining_gaps" in patch.model_fields_set
            else run["base_coverage"].get("remaining_gaps", [])
        )
        if any(i.get("field") == "remaining_gaps" and i.get("error") for i in patch_issues):
            gaps = list(
                {
                    canonical(g): g
                    for g in [*run["base_coverage"].get("remaining_gaps", []), *gaps]
                }.values()
            )
        if gaps:
            missing.append("remaining research gaps")
        event = (
            patch.event_library_ref.model_dump(mode="json")
            if patch.event_library_ref
            else run["prepared"].get("event_library_ref")
            or (base.event_library_ref.model_dump(mode="json") if base.event_library_ref else None)
        )
        actual_refs = [
            base.event_library_ref.model_dump(mode="json") if base.event_library_ref else None,
            run["prepared"].get("event_library_ref"),
        ]
        if (
            patch.event_library_ref
            and patch.event_library_ref.model_dump(mode="json") not in actual_refs
        ):
            missing.append("maintenance Event snapshot binding mismatch")
            event = run["prepared"].get("event_library_ref") or actual_refs[0]
        values = [p.model_dump(mode="json") for p in final.values()]
        same = canonical(values) == canonical([p.model_dump(mode="json") for p in base.policies])
        if (
            same
            and event
            == (base.event_library_ref.model_dump(mode="json") if base.event_library_ref else None)
            and gaps == run["base_coverage"].get("remaining_gaps", [])
            and not missing
        ):
            self.state.update(run_id, status="NOOP", phase="NOOP")
            await self._write_view(run_id)
            return RunResultV21(
                run_id=run_id,
                ticker=ticker,
                mode="maintain",
                status="NOOP",
                policy_set_version=base_policy_version,
            )
        prepared = dict(run["prepared"])
        prepared["event_library_ref"] = event
        coverage = {
            **run["base_coverage"],
            "remaining_gaps": gaps,
            "summary": patch.summary,
            "publish_empty": not final
            and bool(retire.intersection(p.policy_id for p in base.policies)),
            "maintenance_retired_ids": sorted(
                retire.intersection(p.policy_id for p in base.policies)
            ),
        }
        self.state.update(
            run_id,
            final=values,
            prepared=prepared,
            coverage=coverage,
            missing=missing,
            diagnostics=diagnostics,
            status="PARTIAL" if missing else "COMPLETE",
            patch_fields=sorted(patch.model_fields_set),
            patch_snapshot=task.get("files"),
        )
        return await self._publish(run_id)

    def _base_run_id(self, ticker, version):
        with self.state.connect() as db:
            row = db.execute(
                "SELECT run_id FROM codex_document3_v21_staged_sets WHERE ticker=? AND version=?",
                (ticker, version),
            ).fetchone()
            return row[0]
