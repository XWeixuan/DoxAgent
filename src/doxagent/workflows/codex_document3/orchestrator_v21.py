"""Explicit staged four-node orchestration and one-node V3 maintenance."""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from uuid import uuid4

from .assets_v21 import ATLAS_NAMES, ATLAS_ROOT
from .inputs_v21 import InputPreparerV21, utc
from .integration_v21 import (
    directory_batches,
    edit_batches,
    edit_inheritance,
    parse_review,
    source_directory,
)
from .runner_v21 import RunnerV21, reference_path
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
from .timing_v21 import HostTiming
from .validation_v21 import (
    accept_policy,
    final_identities,
    normalize_agenda,
    records,
    replace_structurally,
    safe_path,
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
    return directory_batches(items)


def build_policy_output(path, prefix):
    if not path.startswith(prefix) or not path.endswith(".json"):
        return False
    relative = path[len(prefix) :]
    return relative.startswith("policies/") or (
        "/" not in relative and relative.startswith("policy")
    )


def build_result_records(files, prefix):
    parsed, bad, seen, alternatives = [], [], set(), []
    standard = ("results.jsonl", "result.jsonl", "result.json", "results.json")
    extra = sorted(
        path[len(prefix) :]
        for path in files
        if path.startswith(prefix)
        and path[len(prefix) :] not in standard
        and re.fullmatch(r"results?(?:[_-][^/]+)?\.jsonl?", path[len(prefix) :], re.IGNORECASE)
    )
    for name in (*standard, *extra):
        path = prefix + name
        if path not in files:
            continue
        if name != "results.jsonl":
            alternatives.append(path)
        raw = files[path]
        if name.lower().endswith(".json"):
            try:
                value = json.loads(raw)
                raw = "\n".join(
                    canonical(item) for item in (value if isinstance(value, list) else [value])
                )
            except (ValueError, TypeError):
                bad.append({"path": path, "line": 1, "error": "invalid result JSON"})
                continue
        values, errors = records(raw, ResearchResult)
        bad.extend({"path": path, **error} for error in errors)
        for line, result in values:
            normalized = []
            for ref in result.policies:
                try:
                    ref = safe_path(ref)
                    normalized.append(ref if ref.startswith("output/") else prefix + ref)
                except ValueError:
                    normalized.append(ref)
            result = result.model_copy(update={"policies": normalized})
            identity = canonical(result.model_dump(mode="json"))
            if identity not in seen:
                parsed.append((line, result))
                seen.add(identity)
    return parsed, bad, ";".join(alternatives) or None


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


class PilotPhasePaused(RuntimeError):
    """Pilot-only boundary after a durable phase checkpoint."""

    def __init__(self, phase: str, run_id: str):
        self.phase = phase
        self.run_id = run_id
        super().__init__(f"Pilot paused after {phase}: {run_id}")


class Document3OrchestratorV21:
    orchestration_version = "v2.1"
    orchestration_revision = 3

    def _pilot_boundary(self, phase, run_id):
        if getattr(self, "pilot_stop_after_phase", None) == phase:
            raise PilotPhasePaused(phase, run_id)

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
            if (
                not result.get("commit")
                and result.get("orchestration_revision") != self.orchestration_revision
            ):
                raise ValueError(
                    "Active legacy D3 run requires a new run; frozen old tasks are preserved"
                )
            await self._verify_frozen(run_id, result)
            return result
        assets = self.runner.frozen_assets(mode)  # fail before providers or Worker dispatch
        prepared = await prepare()
        for material in (
            prepared["topology"]
            .get("owner_profiles", {})
            .get("OPEN_EVENT", {})
            .get("primary_materials", [])
        ):
            for name, filename in ATLAS_NAMES.items():
                if material["ref"] == f"{ATLAS_ROOT}/{filename}" and name in assets:
                    material.update(availability="available", sha256=assets[name]["sha256"])
        prepared["files"]["context/document3/v21/topology.json"] = canonical(prepared["topology"])
        for entry in prepared["manifest"]:
            if entry["path"] == "context/document3/v21/topology.json":
                content = prepared["files"][entry["path"]]
                entry.update(sha256=digest(content), size_bytes=len(content.encode()))
        manifest_path = "context/document3/v21/input_manifest.json"
        prepared["files"][manifest_path] = canonical(prepared["manifest"])
        observed = (
            self.policy_repository.get_current_version(identity["ticker"])
            if self.policy_repository
            else 0
        )
        payload = {
            "mode": mode,
            "orchestration_revision": self.orchestration_revision,
            "review_schema_hash": digest(Review.model_json_schema()),
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
        shell_input = run.get("shell_discovery_input")
        if shell_input and digest(shell_input["files"]) != shell_input["sha256"]:
            raise ValueError("frozen Shell Discovery input integrity mismatch")
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
        material_timing = HostTiming()
        material_timing.step("material_resolve")
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
        if phase in {"build", "integration"}:
            for previous_key, record in self.state.tasks(run_id).items():
                if previous_key.startswith("discovery:") and record.get("status") in {
                    "COMPLETED",
                    "PARTIAL",
                }:
                    inputs.update(
                        (path, content)
                        for path, content in record.get("files", {}).items()
                        if path.startswith("output/work/v21/discovery/")
                    )
        inputs.update(extra or {})
        if phase == "integration" or (phase == "build" and task.get("round") == "supplement"):
            inputs.update(self.policy_reference_context(run_id))
            task = {**task, "policy_catalog": "context/document3/v21/policy_catalog.json"}
        task = {
            **task,
            "owner_profile": run["prepared"]["topology"].get("owner_profiles", {}).get(owner, {}),
            "owner_profiles": run["prepared"]["topology"].get("owner_profiles", {}),
            "shared_research_root": "context/document3/v21/shared/",
            "input_diagnostics": run["prepared"]["warnings"],
            "allowed_research_owners": run["prepared"]["topology"]["research_owners"],
            "route_aliases": run["prepared"]["topology"].get("route_aliases", {}),
            "review_contract": {
                "cumulative": True,
                "edit_requests": "Explicit pending full-policy edits; [] means no edits",
                "research_requests": (
                    "Only source of supplement topics; refs and relations are read-only references"
                ),
            },
        }
        late_files, late_leads = self._late_materials(run_id)
        if phase == "integration":
            inputs.update(late_files)
            research_context = {}
            for name in ("agenda", "main_results", "supplement_agenda", "supplement_results"):
                if name in run:
                    ref = f"context/document3/v21/research_context/{name}.json"
                    inputs[ref] = canonical(run[name])
                    research_context[name] = ref
            task = {**task, "late_leads": late_leads, "research_context": research_context}
        if phase in {"build", "integration"}:
            outputs = [*outputs, f"output/work/v21/late/{digest(key)[:24]}.jsonl"]
        material_spans = material_timing.finish()
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
        view_timing = HostTiming()
        view_timing.step("view_write")
        await self._write_view(run_id)
        result.setdefault("host_spans", []).extend([*material_spans, *view_timing.finish()])
        self.state.save_task(run_id, key, result)
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

    def collect_discovery_materials(self, run_id, owner_ids):
        files, leads, diagnostics, missing, producers = {}, [], [], [], []
        for owner in owner_ids:
            task = self.state.task(run_id, f"discovery:{owner}")
            main = f"output/work/v21/discovery/{owner}.jsonl"
            accepted = task.get("files", {})
            if main not in accepted or task["status"] == "FAILED":
                missing.append(f"Discovery unavailable:{owner}")
            producers.append(
                {
                    "owner": owner,
                    "task_key": f"discovery:{owner}",
                    "status": task["status"],
                    "files": [],
                }
            )
            for path in (main, f"output/work/v21/discovery/{owner}.late.jsonl"):
                if path not in accepted:
                    continue
                text = accepted[path]
                files[path] = text
                parsed, bad = records(text, Lead)
                leads.extend(
                    {"owner": owner, "ref": f"{path}#L{line}", "lead": lead.model_dump(mode="json")}
                    for line, lead in parsed
                )
                diagnostics.extend({"path": path, **item} for item in bad)
                producers[-1]["files"].append(
                    {
                        "ref": path,
                        "sha256": digest(text),
                        "snapshot": task.get("snapshots", {}).get(path),
                    }
                )
        return {
            "files": files,
            "leads": leads,
            "diagnostics": diagnostics,
            "missing": missing,
            "producers": producers,
        }

    async def _write_view(self, run_id):
        run = self.state.run(run_id)
        tasks = self.state.tasks(run_id)
        drafts = self.state.drafts(run_id)
        view = {
            **{
                k: v
                for k, v in run.items()
                if k not in {"prepared", "assets", "basis", "final_candidates", "scan_inputs"}
            },
            "input_manifest": run.get("prepared", {}).get("manifest", []),
            "material_refs": list(run.get("prepared", {}).get("files", {})),
            "basis_paths": list(run.get("basis", {})),
            "candidate_paths": list(run.get("final_candidates", {})),
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

    def policy_reference_context(self, run_id):
        run = self.state.run(run_id)
        drafts = self.state.drafts(run_id)
        policies = dict(run.get("basis", {}))
        for path in run.get("supplement_paths", []):
            if path in drafts:
                policies[path] = drafts[path]["policy"]
        # Completed final batches are available immediately to subsequent editors.
        for key, record in self.state.tasks(run_id).items():
            if key.startswith("integration:final:"):
                for path in record.get("candidate_paths", []):
                    if path in drafts:
                        policies[path] = drafts[path]["policy"]
        files = {path: canonical(policy) for path, policy in policies.items()}
        files["context/document3/v21/policy_catalog.json"] = canonical(
            [
                {
                    "ref": path,
                    "local_path": reference_path(path, files[path]),
                    "policy_id": policy["policy_id"],
                    "title": policy["title"],
                    "sha256": digest(files[path]),
                    "source_directory": source_directory(path),
                    "source_round": "main"
                    if path in run.get("basis", {})
                    else "supplement"
                    if path in run.get("supplement_paths", [])
                    else "final",
                }
                for path, policy in policies.items()
            ]
        )
        for name in (
            "agenda",
            "main_results",
            "supplement_agenda",
            "supplement_results",
            "review",
            "post_supplement_review",
        ):
            if name in run:
                files[f"context/document3/v21/research_context/{name}.json"] = canonical(run[name])
        return files

    def _task_json(self, record, path, model, previous=None):
        content = record.get("files", {}).get(path)
        if content is None:
            return None
        try:
            if model is Review:
                raw = json.loads(content)
                if not isinstance(raw, dict):
                    return None
                edits = raw.get("edit_requests", (previous or {}).get("edit_requests", []))
                base, diagnostics = structured(canonical({**raw, "edit_requests": []}), Review)
                parsed, edit_errors = parse_review(
                    {**base.model_dump(mode="json"), "edit_requests": edits}, previous
                )
                diagnostics.extend(edit_errors)
            else:
                parsed, diagnostics = structured(content, model)
            run_id = record.get("persistence_run_id")
            if run_id:
                latest = self.state.run(run_id)
                parse_issues = dict(latest.get("parse_issues", {}))
                parse_issues[path] = diagnostics
                updates = {"parse_issues": parse_issues}
                if diagnostics:
                    record.setdefault("parse_diagnostics", []).extend(diagnostics)
                    updates["diagnostics"] = [*latest["diagnostics"], *diagnostics]
                    if model is Review:
                        updates["missing"] = [
                            *latest["missing"],
                            *[d["error"] for d in diagnostics if d.get("error")],
                        ]
                    self.state.save_task(run_id, record["task_key"], record)
                self.state.update(run_id, **updates)
            return parsed
        except (ValueError, TypeError):
            return None

    def _inheritance(self, run, run_id):
        policies = {}
        text = run["prepared"]["files"].get("context/document3/v21/shared/previous_policy_set.json")
        if text:
            policies.update((p["policy_id"], p) for p in json.loads(text).get("policies", []))
        policies.update((p["policy_id"], p) for p in run.get("basis", {}).values())
        for path in run.get("supplement_paths", []):
            value = self.state.get_draft(run_id, path)
            if value:
                policies[value["policy"]["policy_id"]] = value["policy"]
        return policies

    async def _accept_build_wave(self, run_id, key, record, prefix, names, inheritance):
        cached = record.get("acceptance")
        if cached and cached.get("result_parser_revision") == 2:
            return record["acceptance"]
        acceptance_timing = HostTiming()
        acceptance_timing.step("result_reacceptance" if cached else "policy_acceptance")
        values, errors, alternative = build_result_records(record.get("files", {}), prefix)
        results, declared = {}, set()
        diagnostics = [{"task": key, **error} for error in errors]
        if alternative:
            diagnostics.append({"task": key, "warning": f"noncanonical result path:{alternative}"})
        for line, result in values:
            if result.topic in names:
                results.setdefault(result.topic, []).append(result.model_dump(mode="json"))
                declared.update(result.policies)
            else:
                diagnostics.append({"task": key, "line": line, "warning": "unknown topic result"})
        if cached:
            # Repair derived Result recognition without accepting Policy bodies twice.
            combined = {canonical(d): d for d in [*cached["diagnostics"], *diagnostics]}
            cached.update(
                results=results, diagnostics=list(combined.values()), result_parser_revision=2
            )
            record.setdefault("host_spans", []).extend(acceptance_timing.finish())
            self.state.save_task(run_id, key, record)
            return cached
        paths = []
        acceptance_calls = 0
        for path, text in record.get("files", {}).items():
            try:
                safe_path(path)
                if not path.startswith(prefix) or not path.endswith(".json"):
                    continue
                if path not in declared and not build_policy_output(path, prefix):
                    continue
                raw = json.loads(text)
                if not isinstance(raw, dict):
                    raise ValueError("Policy is not an object")
                inherited = inheritance.get(raw.get("policy_id"))
                if not inherited:
                    raw.pop("policy_id", None)
                acceptance_calls += 1
                policy, errors = accept_policy(
                    raw, state=self.state, run_id=run_id, path=path, inherited=inherited
                )
                diagnostics.extend({"path": path, "error": e} for e in errors)
                accepted_draft = self.state.get_draft(run_id, path)
                if policy and accepted_draft and accepted_draft.get("raw_hash") == digest(raw):
                    paths.append(path)
                    await self.workspace.write_text(run_id, path, policy.model_dump_json(indent=2))
                    await self.workspace.write_text(
                        record["workspace_run_id"], path, policy.model_dump_json(indent=2)
                    )
            except (ValueError, TypeError) as exc:
                diagnostics.append({"path": path, "error": str(exc)[:300]})
        accepted = {
            "results": results,
            "paths": paths,
            "diagnostics": diagnostics,
            "result_parser_revision": 2,
        }
        record["acceptance"] = accepted
        record.setdefault("host_metrics", {}).update(
            policy_candidates=len(paths),
            policy_acceptance_calls=acceptance_calls,
        )
        record.setdefault("host_spans", []).extend(acceptance_timing.finish())
        self.state.save_task(run_id, key, record)
        return accepted

    async def _build(self, run_id, agenda, *, supplement=False):
        run = self.state.run(run_id)
        owners = run["prepared"]["topology"]["research_owners"]
        agenda, route_errors = normalize_agenda(
            agenda.model_dump(mode="json"),
            owners,
            run["prepared"]["topology"].get("route_aliases"),
            fallback_owner=run["prepared"]["topology"].get("fallback_owner"),
        )
        by_name = {t.name: t for t in agenda.topics}
        queues = defaultdict(list)
        for ordinal, names in enumerate(agenda.waves):
            owner = by_name[names[0]].owner
            queues[owner].append((ordinal, names))
        phase = "supplement" if supplement else "research"
        inheritance = self._inheritance(run, run_id)
        accepted = {}

        async def owner_queue(owner, waves):
            for ordinal, names in waves:
                prefix = f"output/work/v21/{phase}/{owner}/{ordinal:04d}/"
                key = f"{phase}:{owner}:{ordinal}"
                record = await self._turn(
                    run_id,
                    owner,
                    "build",
                    key,
                    [prefix],
                    {
                        "topics": [by_name[n].model_dump(mode="json") for n in names],
                        "round": phase,
                        "ordinal": ordinal,
                        "result_delivery": {
                            "preferred_path": prefix + "results.jsonl",
                            "accepted_names": [
                                "results.jsonl",
                                "result.jsonl",
                                "result.json",
                                "results.json",
                                "result_<slug>.json",
                                "result-<slug>.json",
                                "results_<slug>.jsonl",
                                "results-<slug>.jsonl",
                            ],
                            "scope": (
                                "Wave root only; named result/results files accept JSON or JSONL. "
                                "Use exact task Topic names and actual wave-local Policy paths."
                            ),
                        },
                        "owner_shell": next(
                            (
                                s
                                for s in run["prepared"]["topology"]["shells"]
                                if s["slot"] == owner
                            ),
                            None,
                        ),
                    },
                )
                accepted[key] = await self._accept_build_wave(
                    run_id, key, record, prefix, names, inheritance
                )

        await asyncio.gather(*(owner_queue(owner, waves) for owner, waves in queues.items()))
        results, paths, diagnostics = {}, [], [{"warning": e} for e in route_errors]
        for key in sorted(accepted):
            value = accepted[key]
            paths.extend(value["paths"])
            diagnostics.extend(value["diagnostics"])
            for topic, entries in value["results"].items():
                results.setdefault(topic, []).extend(entries)
        paths = list(dict.fromkeys(paths))
        missing = []
        for topic in agenda.topics:
            entries = results.get(topic.name, [])
            if len({canonical(value) for value in entries}) != 1:
                missing.append(f"{phase} result missing/conflicting:{topic.name}")
            else:
                for path in entries[0]["policies"]:
                    if path not in paths:
                        missing.append(f"{phase} result Policy unavailable:{topic.name}:{path}")
        return results, paths, missing, diagnostics

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
        owners = run["prepared"]["topology"]["research_owners"]
        if "scan" not in run:
            topology = run["prepared"]["topology"]
            open_owners = topology.get("open_owners", [o for o in owners if o == "OPEN"])
            shell_owners = topology.get("shell_owners", [o for o in owners if o not in open_owners])

            async def discovery(owner, *, shell_input=None):
                return await self._turn(
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
                            (s for s in topology["shells"] if s["slot"] == owner), None
                        ),
                        "discovery_stage": "open_after_shells" if shell_input else "shell",
                        **(
                            {
                                "prior_shell_discovery": shell_input["index_ref"],
                                "objective": (
                                    "Find substantive additional Leads using your primary "
                                    "materials and completed Shell Leads"
                                ),
                                "shell_discovery_sha256": shell_input["sha256"],
                            }
                            if shell_input
                            else {}
                        ),
                    },
                    shell_input["files"] if shell_input else None,
                )

            if "shell_discovery_input" not in run:
                self.state.update(run_id, phase="SHELL_DISCOVERY")
                await asyncio.gather(*(discovery(owner) for owner in shell_owners))
                collected = self.collect_discovery_materials(run_id, shell_owners)
                index_ref = "context/document3/v21/discovery_inputs/shells/index.json"
                index = {
                    "source_kind": "accepted_discovery_snapshot",
                    "stage": "discovery",
                    "shells": topology["shells"],
                    **{k: v for k, v in collected.items() if k != "files"},
                }
                files = {**collected["files"], index_ref: canonical(index)}
                shell_input = {"files": files, "index_ref": index_ref, "sha256": digest(files)}
                self.state.update(run_id, shell_discovery_input=shell_input, phase="OPEN_DISCOVERY")
            else:
                shell_input = run["shell_discovery_input"]
            await asyncio.gather(
                *(discovery(owner, shell_input=shell_input) for owner in open_owners)
            )
            collected = self.collect_discovery_materials(run_id, owners)
            self.state.update(
                run_id,
                phase="PLANNING",
                scan=collected["leads"],
                scan_inputs=collected["files"],
                discovery_missing=collected["missing"],
                diagnostics=[*run["diagnostics"], *collected["diagnostics"]],
            )
        await self._write_view(run_id)
        self._pilot_boundary("discovery", run_id)
        run = self.state.run(run_id)
        if "agenda" not in run:
            leads = run["scan"]
            raw_inputs = run["scan_inputs"]
            discovery_missing = run["discovery_missing"]
            diagnostics = list(run["diagnostics"])
            cumulative = None
            planning_batches = list(batches(leads)) or [[]]
            for i, batch in enumerate(planning_batches):
                extra = dict(raw_inputs)
                if cumulative:
                    extra["context/document3/v21/planning_previous.json"] = canonical(
                        cumulative.model_dump(mode="json")
                    )
                planning_task = await self._turn(
                    run_id,
                    "GLOBAL",
                    "planning",
                    f"planning:{i}",
                    ["output/work/v21/agenda.json"],
                    {"leads": batch, "ordinal": i, "final_batch": i == len(planning_batches) - 1},
                    extra,
                )
                candidate = self._task_json(planning_task, "output/work/v21/agenda.json", Agenda)
                if candidate:
                    cumulative = candidate
            agenda, warnings = normalize_agenda(
                cumulative.model_dump(mode="json") if cumulative else {"topics": [], "waves": []},
                owners,
                run["prepared"]["topology"].get("route_aliases"),
                fallback_owner=run["prepared"]["topology"].get("fallback_owner"),
            )
            missing = [*discovery_missing, *([] if cumulative else ["Planning agenda unavailable"])]
            self.state.update(
                run_id,
                agenda=agenda.model_dump(mode="json"),
                missing=missing,
                diagnostics=[*diagnostics, *({"warning": w} for w in warnings)],
                phase="BUILD",
            )
        await self._write_view(run_id)
        self._pilot_boundary("planning", run_id)
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
                integration_schedule=schedule or [[]],
                missing=[*run["missing"], *missing],
                diagnostics=[*run["diagnostics"], *diagnostics],
                phase="INTEGRATION",
            )
        await self._write_view(run_id)
        self._pilot_boundary("build", run_id)
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
                        "focus_directories": list(
                            dict.fromkeys(source_directory(x["path"]) for x in batch)
                        ),
                        "final_batch": i == len(run["integration_schedule"]) - 1,
                    },
                    extra,
                )
                parsed = self._task_json(
                    batch_task,
                    "output/work/v21/review.json",
                    Review,
                    review.model_dump(mode="json") if review else None,
                )
                if parsed:
                    review = parsed
                if parsed is None or batch_task["status"] == "FAILED":
                    integration_missing.append(f"Integration main batch unavailable:{i}")
            wave_source = self._task_json(
                batch_task, "output/work/v21/supplement_agenda.json", Agenda
            )
            supplement, diagnostics = normalize_agenda(
                {
                    "topics": [t.model_dump(mode="json") for t in review.research_requests]
                    if review
                    else [],
                    "waves": wave_source.waves if wave_source else [],
                },
                owners,
                run["prepared"]["topology"].get("route_aliases"),
                fallback_owner=run["prepared"]["topology"].get("fallback_owner"),
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
                diagnostics=[
                    *self.state.run(run_id)["diagnostics"],
                    *({"warning": d} for d in diagnostics),
                ],
                missing=[*self.state.run(run_id)["missing"], *integration_missing],
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
            refs = list(run["supplement_paths"])
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
                        {
                            "stage": "post_supplement_review",
                            "batch": [x["path"] for x in batch],
                            "focus_directories": list(
                                dict.fromkeys(source_directory(x["path"]) for x in batch)
                            ),
                        },
                        extra,
                    )
                    parsed = self._task_json(
                        item, "output/work/v21/post_supplement_review.json", Review, review
                    )
                    if parsed:
                        review = parsed.model_dump(mode="json")
                    if parsed is None or item["status"] == "FAILED":
                        missing.append(f"Integration supplement batch unavailable:{i}")
            self.state.update(
                run_id,
                post_supplement_review=review,
                affected_paths=refs,
                post_review_focus=refs,
                missing=list(dict.fromkeys([*self.state.run(run_id)["missing"], *missing])),
            )
            run = self.state.run(run_id)
        if "final_candidates" not in run:
            drafts = self.state.drafts(run_id)
            effective_review = run.get("post_supplement_review") or run.get("review")
            editable = dict(run["basis"])
            editable.update((p, drafts[p]["policy"]) for p in run["supplement_paths"])
            schedule, edit_errors = edit_batches(effective_review, editable)
            schedule = run.get("final_schedule", schedule)
            self.state.update(
                run_id,
                final_schedule=schedule,
                diagnostics=[*run["diagnostics"], *edit_errors],
                missing=[*run["missing"], *[e["error"] for e in edit_errors]],
            )
            self.state.update(
                run_id,
                pending_edit_targets=list(
                    dict.fromkeys(p for batch in schedule for p in batch["related_targets"])
                ),
                reference_count=len(editable),
                pending_research_topics=[
                    t["name"] for t in (effective_review or {}).get("research_requests", [])
                ],
            )
            final_candidates = {}
            unfinished_edit_targets = []
            inheritable = self._inheritance(run, run_id)
            inheritable.update(
                (drafts[path]["policy"]["policy_id"], drafts[path]["policy"])
                for path in run["supplement_paths"]
            )
            for i, batch in enumerate(schedule):
                incomplete = False
                extra = {}
                extra["context/document3/v21/review_frozen.json"] = canonical(effective_review)
                prefix = f"output/work/v21/final/policies/{i:04d}/"
                task = await self._turn(
                    run_id,
                    "GLOBAL",
                    "integration",
                    f"integration:final:{i}",
                    [prefix],
                    {
                        "stage": "final_write",
                        "ordinal": i,
                        **batch,
                        "edit_task_contract": {
                            "batch": "Scheduling anchors for the five-directory grouping",
                            "related_targets": (
                                "All resolved Policy targets in assigned edit_requests"
                            ),
                            "edit_requests": (
                                "Authoritative instructions and complete explicit target lists"
                            ),
                            "policy_catalog": (
                                "Full read-only reference pool; reading does not expand edit scope"
                            ),
                        },
                    },
                    extra,
                )
                for path, text in task.get("files", {}).items():
                    if path.endswith(".json"):
                        try:
                            raw = json.loads(text)
                            if not isinstance(raw, dict):
                                raise ValueError("Policy is not an object")
                            inherited = edit_inheritance(
                                raw.get("policy_id"),
                                batch["related_targets"],
                                editable,
                                inheritable,
                            )
                            if not inherited:
                                raw.pop("policy_id", None)
                            policy, errors = accept_policy(
                                raw, state=self.state, run_id=run_id, path=path, inherited=inherited
                            )
                            if errors:
                                incomplete = True
                                latest = self.state.run(run_id)
                                self.state.update(
                                    run_id,
                                    diagnostics=[
                                        *latest["diagnostics"],
                                        *[{"path": path, "error": e} for e in errors],
                                    ],
                                    missing=[*latest["missing"], f"final edit incomplete:{path}"],
                                )
                            accepted_draft = self.state.get_draft(run_id, path)
                            if (
                                policy
                                and accepted_draft
                                and accepted_draft.get("raw_hash") == digest(raw)
                            ):
                                final_candidates[path] = policy.model_dump(mode="json")
                                await self.workspace.write_text(
                                    run_id, path, canonical(final_candidates[path])
                                )
                                await self.workspace.write_text(
                                    task["workspace_run_id"],
                                    path,
                                    canonical(final_candidates[path]),
                                )
                        except (ValueError, TypeError) as exc:
                            incomplete = True
                            latest = self.state.run(run_id)
                            self.state.update(
                                run_id,
                                missing=[*latest["missing"], f"final candidate invalid:{path}"],
                                diagnostics=[
                                    *latest["diagnostics"],
                                    {"path": path, "error": str(exc)},
                                ],
                            )
                task["candidate_paths"] = [p for p in final_candidates if p.startswith(prefix)]
                if not task["candidate_paths"]:
                    latest = self.state.run(run_id)
                    self.state.update(
                        run_id, missing=[*latest["missing"], f"final edit unavailable:{i}"]
                    )
                if incomplete or not task["candidate_paths"] or task["status"] != "COMPLETED":
                    unfinished_edit_targets.extend(batch["related_targets"])
                self.state.save_task(run_id, f"integration:final:{i}", task)
            self.state.update(
                run_id,
                final_candidates=final_candidates,
                pending_edit_targets=list(dict.fromkeys(unfinished_edit_targets)),
            )
        run = self.state.run(run_id)
        candidates = {p: PolicyV3.model_validate(v) for p, v in run["final_candidates"].items()}
        drafts = self.state.drafts(run_id)
        candidates.update(
            {p: PolicyV3.model_validate(drafts[p]["policy"]) for p in run["supplement_paths"]}
        )
        extra = {p: policy.model_dump_json(indent=2) for p, policy in candidates.items()}
        extra["context/document3/v21/basis.json"] = canonical(run["basis"])
        extra["context/document3/v21/review_frozen.json"] = canonical(
            run.get("post_supplement_review") or run.get("review")
        )
        consolidation_task = await self._turn(
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
        consolidation = self._task_json(
            consolidation_task, "output/work/v21/consolidation.json", Consolidation
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
            pending_research_topics=[g["name"] for g in coverage["remaining_gaps"]],
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
