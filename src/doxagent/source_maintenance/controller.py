"""Persistent bounded stage advancement; original news pipeline remains the owner."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .apply import ApplyController
from .containers import Docker
from .evidence import EvidenceStore, redact, write_json
from .health import diagnose, recovered
from .policy import SourcePolicy, validate_candidate
from .repository import MaintenanceRepository
from .schema import (
    ActionKind,
    DeploymentManifest,
    Incident,
    ProposedAction,
    RepairReport,
    RepairRound,
    Stage,
)
from .settings import MaintenanceSettings
from .workspace import Workspaces


class Controller:
    def __init__(
        self,
        settings: MaintenanceSettings,
        repository: MaintenanceRepository,
        *,
        backend=None,
        docker=None,
        workspace=None,
        policies=None,
    ):
        self.settings, self.repository = settings, repository
        self.policies = policies or self._load_policies()
        self.docker = docker or Docker(settings)
        self.backend = backend or ApplyController(settings, repository, self.policies, self.docker)
        self.workspace = workspace or (
            Workspaces(settings.root, settings.source_repository)
            if settings.source_repository
            else None
        )
        self.evidence = EvidenceStore(settings.root, repository)
        self.owner = "source-maintenance-" + uuid4().hex

    def _load_policies(self) -> dict[str, SourcePolicy]:
        path = self.settings.policy_path
        if not path:
            return {}
        return {s: SourcePolicy.model_validate(p) for s, p in json.loads(path.read_text()).items()}

    def tick(self) -> int:
        if not self.settings.enabled:
            return 0
        now = self.repository.clock()
        samples = self.repository.samples()
        allowed = self.settings.sources
        if allowed:
            samples = [s for s in samples if s.source_id in allowed]
        for incident in diagnose(samples, now):
            if incident.failure_class == "DRIVER":
                incident.affected_bindings = sorted(
                    {
                        b
                        for s in samples
                        if s.source_id in incident.source_ids and s.enabled and s.eligible
                        for b in ([s.binding_id] if s.binding_id else s.affected_bindings)
                    }
                )
            self.repository.observe(incident)
        count = 0
        for current in self.repository.incidents():
            if (
                current.stage == Stage.HUMAN_REQUIRED
                and current.context.get("reason")
                not in {"subscription_required", "entitlement_missing"}
                and recovered(samples, current, since=current.last_observed_at)
            ):
                lease = self.repository.claim(current.incident_id, self.owner)
                if lease:
                    lease.stage = Stage.RECOVERED
                    lease.recovered_at = now
                    lease.context["observation_since"] = current.last_observed_at.isoformat()
                    self.repository.save(lease)
            if current.stage in {
                Stage.CANCELLED,
                Stage.STABLE,
                Stage.HUMAN_REQUIRED,
                Stage.REVIEW_REQUIRED,
                Stage.ROLLED_BACK,
            }:
                if self.settings.mode != "shadow":
                    self._retire_rounds(current)
                continue
            if self.settings.mode == "shadow":
                self.evidence.pack(current)
                continue
            item = self.repository.claim(
                current.incident_id, self.owner, seconds=self.settings.round_seconds + 60
            )
            if not item:
                continue
            try:
                self._step(item, samples, now)
            except RuntimeError as exc:
                # Transient capacity/SDK/service failure leaves a durable next tick opportunity.
                item.context["last_error"] = redact(str(exc))
            except (ValueError, KeyError) as exc:
                item.stage = Stage.REVIEW_REQUIRED
                item.human_action = str(redact(str(exc)))
            finally:
                self.repository.save(item)
                if item.stage in {Stage.REVIEW_REQUIRED, Stage.HUMAN_REQUIRED, Stage.CANCELLED}:
                    self._retire_rounds(item)
            count += 1
        from .issues import render

        render(self.repository, self.settings.root / "message-source-issues.md")
        self.evidence.archive_closed()
        return count

    def _retire_rounds(self, incident: Incident) -> None:
        for round_ in self.repository.rounds(incident.incident_id):
            if round_.status in {"PENDING", "RUNNING", "VERIFY"}:
                self.docker.stop_worker(round_)
                round_.status = incident.stage.value
                self.repository.save_round(round_)

    def _step(self, item, samples, now):
        if item.stage == Stage.OBSERVED:
            item.stage = Stage.TRIAGE
            return
        if item.stage == Stage.TRIAGE:
            if item.failure_class == "HUMAN":
                item.stage = Stage.HUMAN_REQUIRED
                item.human_action = (
                    "Use xRDP Source Login Maintenance; verify actual article "
                    "access in the existing Profile. No Cookie/identity replacement."
                )
                return
            if item.failure_class in {"CAPACITY", "SCHEDULER_STALE"}:
                item.stage = Stage.REVIEW_REQUIRED
                item.human_action = (
                    "Capacity/scheduler issue: diagnose shared core, no IP switching."
                )
                return
            snapshot = self.backend.snapshot(item)
            item.context["evidence_views"] = {"get_runtime_status": snapshot}
            manifest = self._manifest()
            if hasattr(self.backend, "evidence_views"):
                item.context["evidence_views"] = self.backend.evidence_views(
                    item, snapshot, manifest
                )
            evidence = self.evidence.pack(
                item, manifest.model_dump(mode="json") if manifest else {}
            )
            item.context["evidence_path"] = str(evidence)
            if item.failure_class == "DRIVER" and self.settings.mode == "apply":
                item.stage = Stage.AUTO_RECOVERY
            else:
                item.stage = Stage.CODING
            return
        if item.stage == Stage.AUTO_RECOVERY:
            try:
                self.backend.execute(
                    item,
                    ProposedAction(
                        kind=ActionKind.RECONNECT_EXTERNAL_DRIVER, target="external_chrome"
                    ),
                    key=item.incident_id + "-driver-reconnect",
                )
            except RuntimeError as exc:
                item.context["deterministic_recovery_error"] = str(redact(str(exc)))
                item.stage = Stage.CODING
                return
            item.context["observation_since"] = now.isoformat()
            item.stage = Stage.OBSERVE
            return
        if item.stage == Stage.CODING:
            round_ = self._round(item, now)
            if now >= round_.deadline:
                self.docker.stop_worker(round_)
                round_.status = "TIMED_OUT"
                self.repository.save_round(round_)
                item.context.pop("round_id", None)
                return
            receipt = self.docker.agent(round_)
            round_.status = "RUNNING" if receipt is None else "VERIFY"
            if receipt:
                round_.thread_id, round_.turn_id = receipt["thread_id"], receipt["turn_id"]
                round_.usage = receipt.get("usage", {})
                round_.report = RepairReport.model_validate(receipt["report"])
                round_.feedback = None
                item.stage = Stage.VERIFY
            self.repository.save_round(round_)
            return
        if item.stage == Stage.VERIFY:
            round_ = self._round(item, now)
            report = round_.report
            assert report is not None
            if report.human_action:
                item.stage, item.human_action = Stage.HUMAN_REQUIRED, report.human_action
                round_.status = "HUMAN_REQUIRED"
                self.repository.save_round(round_)
                return
            policies = [self.policies[s] for s in item.source_ids if s in self.policies]
            if not policies:
                raise ValueError("no site-local code ownership policy; review required")
            changed = validate_candidate(Path(round_.worktree), report, policies)
            tests = sorted(
                {
                    t
                    for p in policies
                    for t in [*p.tests, p.normal_fixture_test, p.failure_fixture_test]
                    if t
                }
            )
            if now >= round_.deadline:
                round_.status = "TIMED_OUT"
                self.repository.save_round(round_)
                item.context.pop("round_id", None)
                item.stage = Stage.CODING
                return
            # Existing fixtures are frozen from the baseline, not replaced by model-written tests.
            trusted_tests = []
            for target in tests:
                relative, _, suffix = target.partition("::")
                original = Workspaces.original(Path(round_.worktree), relative)
                if original:
                    from .workspace import safe_file

                    frozen = safe_file(
                        Path(round_.worktree), ".maintenance/frozen-tests/" + relative
                    )
                    frozen.parent.mkdir(parents=True, exist_ok=True)
                    frozen.write_text(original, encoding="utf-8")
                    for parent in Path(relative).parents:
                        fixture_path = (parent / "conftest.py").as_posix()
                        fixture = Workspaces.original(Path(round_.worktree), fixture_path)
                        if fixture:
                            destination = safe_file(
                                Path(round_.worktree), ".maintenance/frozen-tests/" + fixture_path
                            )
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            destination.write_text(fixture, encoding="utf-8")
                    trusted_tests.append(
                        str(frozen.relative_to(round_.worktree)) + ("::" + suffix if suffix else "")
                    )
            validation = self.docker.verify(Path(round_.worktree), tests + trusted_tests)
            if not validation["passed"]:
                round_.feedback = {"for_turn_id": round_.turn_id, "validation": redact(validation)}
                round_.status = "PENDING"
                round_.receipt["feedback_count"] = round_.receipt.get("feedback_count", 0) + 1
                round_.feedback["attempt"] = round_.receipt["feedback_count"]
                self.repository.save_round(round_)
                item.stage = Stage.CODING
                return
            round_.candidate_commit = self.workspace.commit(
                Path(round_.worktree), "Source maintenance candidate " + item.incident_id
            )
            round_.receipt["validation"] = redact(validation)
            round_.receipt["candidate_hashes"] = {
                relative: hashlib.sha256(
                    (Path(round_.worktree) / relative).read_bytes()
                ).hexdigest()
                for relative in changed
            }
            round_.status = "VERIFIED"
            self.repository.save_round(round_)
            item.context.update(candidate_commit=round_.candidate_commit, changed_files=changed)
            item.stage = Stage.APPLY if self.settings.mode == "apply" else Stage.REVIEW_REQUIRED
            if self.settings.mode != "apply":
                item.human_action = "Verified candidate branch only; apply mode is disabled."
            return
        if item.stage == Stage.APPLY:
            round_ = self._round(item, now)
            report = round_.report
            assert report is not None
            # Recheck exact candidate after Worker exit; receipt is not authorization.
            policies = [self.policies[s] for s in item.source_ids if s in self.policies]
            validate_candidate(Path(round_.worktree), report, policies)
            for relative, digest in round_.receipt.get("candidate_hashes", {}).items():
                if (
                    hashlib.sha256((Path(round_.worktree) / relative).read_bytes()).hexdigest()
                    != digest
                ):
                    raise ValueError("candidate changed after independent verification")
            changed = item.context.get("changed_files", [])
            actions = report.proposed_actions
            if changed and not any(a.kind == ActionKind.APPLY_SOURCE_PATCH for a in actions):
                raise ValueError("candidate code has no source patch proposal")
            manifest = DeploymentManifest.model_validate(round_.receipt["baseline_manifest"])
            current_manifest = self._manifest()
            if current_manifest != manifest:
                completed = [
                    a
                    for a in self.repository.actions(item.incident_id)
                    if a.action_id.startswith(round_.round_id + "-")
                    and a.after.get("manifest")
                    == (current_manifest.model_dump(mode="json") if current_manifest else None)
                ]
                if not completed:
                    raise ValueError(
                        "deployment baseline changed since coding; explicit rebase required"
                    )
            for index, action in enumerate(actions):
                self.backend.execute(
                    item,
                    action,
                    key=f"{round_.round_id}-{index}",
                    worktree=Path(round_.worktree),
                    manifest=manifest,
                    changed=changed,
                )
                if action.kind == ActionKind.REQUEST_HUMAN_MAINTENANCE:
                    item.stage = Stage.HUMAN_REQUIRED
                    item.human_action = str(action.parameters.get("reason", "manual maintenance"))
                    return
            item.stage = Stage.OBSERVE
            item.context["observation_since"] = now.isoformat()
            item.context["deployment_round"] = round_.round_id
            canary = self.backend.canary(item)
            item.context["canary"] = canary
            if not canary.get("passed"):
                for receipt in reversed(self.repository.actions(item.incident_id)):
                    if (
                        receipt.kind == ActionKind.APPLY_SOURCE_PATCH
                        and receipt.status == "SUCCEEDED"
                    ):
                        self.backend.rollback(receipt)
                        item.stage = Stage.ROLLED_BACK
                        return
                item.stage = Stage.REVIEW_REQUIRED
                item.human_action = "Live canary failed; no successful recovery claimed."
            return
        if item.stage in {Stage.OBSERVE, Stage.WAITING_WINDOW, Stage.RECOVERED}:
            since = datetime.fromisoformat(item.context["observation_since"])
            eligible = [
                s
                for s in samples
                if s.completed_at >= since and s.eligible and s.source_id in item.source_ids
            ]
            if not eligible:
                item.stage = Stage.WAITING_WINDOW
                return
            if recovered(samples, item, since=since):
                if item.recovered_at is None:
                    item.recovered_at = now
                latest = max(eligible, key=lambda s: s.completed_at)
                fresh = (now - latest.completed_at).total_seconds() <= (
                    86400
                    if latest.window == "CLOSED_SWEEP"
                    else max(3 * latest.target_interval_seconds, 300)
                )
                item.stage = (
                    Stage.STABLE
                    if now - item.recovered_at >= timedelta(hours=24) and fresh
                    else Stage.RECOVERED
                )
                return
            # Recovery needs observations of every affected binding; no-news isn't regression.
            failures = [s for s in eligible if s.status == "FAILED"]
            if len({s.operation_id for s in failures}) >= 3:
                deployed = [
                    a
                    for a in self.repository.actions(item.incident_id)
                    if a.status == "SUCCEEDED" and a.kind == ActionKind.APPLY_SOURCE_PATCH
                ]
                if deployed:
                    self.backend.rollback(deployed[-1])
                    item.stage = Stage.ROLLED_BACK
                else:
                    item.stage = Stage.CODING
                    item.context.pop("round_id", None)

    def _manifest(self) -> DeploymentManifest | None:
        head = self.settings.root / "deployment-head.json"
        path = head if head.is_file() else self.settings.manifest_path
        return DeploymentManifest.model_validate_json(path.read_text()) if path else None

    def _round(self, item, now) -> RepairRound:
        saved = item.context.get("round_id")
        if saved:
            return next(r for r in self.repository.rounds(item.incident_id) if r.round_id == saved)
        unfinished = [
            r
            for r in self.repository.rounds(item.incident_id)
            if r.status in {"PENDING", "RUNNING", "VERIFY"}
        ]
        if unfinished:
            # Recover a crash between atomic round creation and the incident CAS save.
            round_ = unfinished[-1]
            item.context["round_id"] = round_.round_id
            item.round_count = max(item.round_count, round_.number)
            return round_
        if item.round_count - item.context.get("retry_round_base", 0) >= self.settings.max_rounds:
            raise ValueError("incident coding budget exhausted")
        manifest = self._manifest()
        if manifest is None or self.workspace is None:
            raise ValueError("missing exact deployed source baseline; diagnosis only")
        worktree = self.workspace.prepare(item.incident_id, manifest)
        evidence_path = self.evidence.pack(item, manifest.model_dump(mode="json"))
        context = worktree / ".maintenance" / "context.json"
        context.parent.mkdir(parents=True, exist_ok=True)
        context.write_bytes(evidence_path.read_bytes())
        # Candidate metadata must never appear in the code patch/commit.
        exclude = self.settings.root / "repository.git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        if not exclude.is_file() or ".maintenance/" not in exclude.read_text():
            with exclude.open("a") as f:
                f.write("\n.maintenance/\n")
        round_ = RepairRound(
            incident_id=item.incident_id,
            number=item.round_count + 1,
            started_at=now,
            deadline=now + timedelta(seconds=self.settings.round_seconds),
            worktree=str(worktree),
            evidence_path=str(context),
            thread_id=next(
                (
                    r.thread_id
                    for r in reversed(self.repository.rounds(item.incident_id))
                    if r.thread_id
                ),
                None,
            ),
            receipt={"baseline_manifest": manifest.model_dump(mode="json")},
        )
        self.repository.begin_round(
            round_,
            daily_rounds=self.settings.daily_rounds,
            daily_seconds=self.settings.daily_wall_seconds,
        )
        item.round_count += 1
        item.context["round_id"] = round_.round_id
        write_json(context.parent / "round.json", round_.model_dump(mode="json"))
        return round_
