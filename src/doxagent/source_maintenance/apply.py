"""Narrow image updates with immutable receipts and compare-before rollback."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import httpx

from .containers import Docker
from .evidence import redact, write_json
from .policy import SourcePolicy
from .repository import MaintenanceRepository
from .schema import ActionKind, ActionReceipt, DeploymentManifest, Incident, ProposedAction
from .settings import MaintenanceSettings
from .workspace import safe_file

SERVICES = {"v2-site-access", "v2-message-bus", "v2-content-enrichment"}


class ApplyController:
    def __init__(
        self,
        settings: MaintenanceSettings,
        repository: MaintenanceRepository,
        policies: dict[str, SourcePolicy],
        docker: Docker | None = None,
        client: httpx.Client | None = None,
    ):
        self.settings, self.repository, self.policies = settings, repository, policies
        self.docker = docker or Docker(settings)
        self.client = client

    def api(self, method: str, route: str, payload: dict | None = None) -> dict:
        if not self.settings.site_token_file:
            raise ValueError("limited Site Access maintenance token required")
        headers = {"Authorization": "Bearer " + self.settings.site_token_file.read_text().strip()}
        if self.client:
            response = self.client.request(method, route, json=payload, headers=headers)
        else:
            with httpx.Client(base_url=self.settings.site_access_url, timeout=45) as client:
                response = client.request(method, route, json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"maintenance capability request failed ({response.status_code})")
        return response.json()

    def snapshot(self, incident: Incident) -> dict:
        values = {}
        for source in incident.source_ids:
            policy = self.policies.get(source)
            if policy and policy.site_id:
                values[policy.site_id] = self.api(
                    "GET", "/v1/maintenance/snapshot/" + policy.site_id
                )
        return redact(values)

    def canary(self, incident: Incident) -> dict:
        results = []
        for source in incident.source_ids:
            policy = self.policies.get(source)
            if not policy or not policy.canaries:
                return {"passed": False, "reason": "trusted_source_canary_not_configured"}
            for index, value in enumerate(policy.canaries):
                payload = json.loads(json.dumps(value))
                payload["source_id"] = source
                payload["request"]["operation_id"] = (
                    f"{incident.incident_id}:canary:{source}:{index}"
                )
                results.append(self.api("POST", "/v1/maintenance/canary", payload))
        return {"passed": all(r.get("passed") for r in results), "results": results}

    def evidence_views(self, incident: Incident, snapshot: dict, manifest) -> dict:
        logs = {}
        try:
            containers = self._containers()
            services = {
                s
                for source in incident.source_ids
                if source in self.policies
                for s in self.policies[source].services
            }
            for service in sorted(services & SERVICES):
                if service in containers:
                    logs[service] = self.docker.run(
                        [
                            "logs",
                            "--timestamps",
                            "--since",
                            incident.first_observed_at.isoformat(),
                            "--tail",
                            "200",
                            containers[service]["Id"],
                        ],
                        timeout=10,
                        include_stderr=True,
                    )[-16000:]
        except RuntimeError:
            logs["unavailable"] = "bounded logs unavailable; other evidence preserved"
        return redact(
            {
                "get_runtime_status": snapshot,
                "get_access_events": {s: v.get("events", []) for s, v in snapshot.items()},
                "get_deployment_manifest": manifest.model_dump(mode="json") if manifest else {},
                "get_bounded_log": logs,
            }
        )

    def _containers(self) -> dict[str, dict]:
        # Fixed known service labels, not model-provided container names.
        text = self.docker.run(["ps", "--format", "{{json .}}"])
        rows = [json.loads(x) for x in text.splitlines() if x]
        found = {}
        for row in rows:
            item = self.docker.inspect(row["Names"])
            service = (
                item["Config"].get("Labels", {}).get("com.docker.compose.service") if item else ""
            )
            if service in SERVICES:
                if service in found:
                    raise ValueError("ambiguous production service container")
                found[service] = item
        return found

    def execute(
        self,
        incident: Incident,
        action: ProposedAction,
        *,
        key: str,
        worktree: Path | None = None,
        manifest: DeploymentManifest | None = None,
        changed: list[str] | None = None,
    ) -> ActionReceipt:
        if self.settings.mode != "apply" or not self.settings.enabled:
            raise RuntimeError("automatic actions disabled")
        resource = (
            "SERVICE:source-update"
            if action.kind == ActionKind.APPLY_SOURCE_PATCH
            else incident.resource_key
        )
        receipt = self.repository.prepare_action(
            ActionReceipt(
                action_id=key,
                incident_id=incident.incident_id,
                resource_key=resource,
                kind=action.kind,
                target=action.target,
            )
        )
        if receipt.status in {"SUCCEEDED", "ROLLED_BACK"}:
            return receipt
        if receipt.status == "FAILED":
            raise RuntimeError("failed action requires explicit reconciliation, not blind replay")
        try:
            if action.kind == ActionKind.REQUEST_HUMAN_MAINTENANCE:
                receipt.after = {
                    "human_action": str(action.parameters.get("reason", "manual review"))
                }
            elif action.kind == ActionKind.APPLY_SOURCE_PATCH:
                if worktree is None or manifest is None:
                    raise ValueError("exact manifest and sealed candidate required")
                self._deploy(incident, receipt, worktree, manifest, changed or [])
            elif action.kind == ActionKind.ROLLBACK_SOURCE_UPDATE:
                prior = next(
                    a
                    for a in self.repository.actions(incident.incident_id)
                    if a.action_id == action.parameters["action_id"]
                )
                self.rollback(prior)
                receipt.after = prior.model_dump(mode="json")
            else:
                if action.kind not in {
                    ActionKind.RECONNECT_EXTERNAL_DRIVER,
                    ActionKind.SELECT_EXISTING_COMBINATION,
                    ActionKind.PATCH_SOURCE_PARAMETERS,
                }:
                    raise ValueError("action outside capability")
                sites = {
                    self.policies[s].site_id for s in incident.source_ids if s in self.policies
                }
                if (
                    action.kind != ActionKind.RECONNECT_EXTERNAL_DRIVER
                    and action.target not in sites
                ):
                    raise ValueError("action target outside incident")
                payload = dict(action.parameters)
                if action.kind == ActionKind.PATCH_SOURCE_PARAMETERS:
                    policy = next(p for p in self.policies.values() if p.site_id == action.target)
                    if not set(payload.get("parameters", {})) <= set(policy.parameter_fields):
                        raise ValueError("recipe parameter not approved")
                payload["kind"] = action.kind.value
                if action.kind != ActionKind.RECONNECT_EXTERNAL_DRIVER:
                    before = self.api("GET", "/v1/maintenance/snapshot/" + action.target)
                    receipt.before = before
                    if action.kind == ActionKind.SELECT_EXISTING_COMBINATION:
                        receipt.before["canary"] = payload.get("canary")
                    payload["site_id"] = action.target
                receipt.status = "RUNNING"
                self.repository.save_action(receipt)
                receipt.after = self.api("POST", "/v1/maintenance/action", payload)
            receipt.status = "SUCCEEDED"
        except Exception as exc:
            receipt.status = "RECONCILE" if receipt.after.get("images") else "FAILED"
            receipt.error = str(redact(str(exc)))
            self.repository.save_action(receipt)
            raise
        self.repository.save_action(receipt)
        return receipt

    def _deploy(self, incident, receipt, path, manifest, changed):
        if not changed:
            raise ValueError("no source patch to apply")
        deployment = receipt.after.get("images")
        if deployment:
            # Crash reconciliation: apply only missing intended image, no rebuild/no replay.
            current = self._containers()
            for service, expected in deployment.items():
                if current[service]["Image"] not in {expected, receipt.before["images"][service]}:
                    raise RuntimeError("deployment drift during interrupted apply")
            if all(current[s]["Image"] == x for s, x in deployment.items()):
                self._publish_manifest(receipt, manifest)
                return
        else:
            recent = [
                a
                for a in self.repository.actions()
                if a.created_at >= self.repository.clock() - timedelta(days=1)
                and a.kind == ActionKind.APPLY_SOURCE_PATCH
                and a.resource_key == receipt.resource_key
                and a.action_id != receipt.action_id
                and a.status in {"SUCCEEDED", "ROLLED_BACK", "RUNNING"}
            ]
            if len(recent) >= self.settings.resource_daily_deploys:
                raise RuntimeError("resource daily deployment budget exhausted")
            ownership: dict[str, set[str]] = {}
            for source in incident.source_ids:
                if source in self.policies:
                    for service, paths in self.policies[source].services.items():
                        ownership.setdefault(service, set()).update(paths)
            current = self._containers()
            affected = {
                s: sorted(set(changed) & paths)
                for s, paths in ownership.items()
                if set(changed) & paths
            }
            if not affected or not set(affected) <= SERVICES:
                raise ValueError("no approved target service mapping")
            if (
                set(changed)
                - {x for v in affected.values() for x in v}
                - {t for p in self.policies.values() for t in p.tests}
            ):
                raise ValueError("unmapped source patch file")
            for service in affected:
                if current[service]["Image"] != manifest.images.get(service):
                    raise RuntimeError("live image differs from exact deployment manifest")
            receipt.before = {
                "images": {s: current[s]["Image"] for s in affected},
                "manifest": manifest.model_dump(mode="json"),
            }
            images = {}
            context = (
                self.settings.root
                / "incidents"
                / incident.incident_id
                / "build"
                / receipt.action_id
            )
            for service, files in affected.items():
                build = context / service
                build.mkdir(parents=True, exist_ok=True)
                lines = ["ARG BASE_IMAGE", "FROM ${BASE_IMAGE}"]
                for relative in files:
                    source = safe_file(path, relative)
                    target = safe_file(build, relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
                    lines.append("COPY " + json.dumps([relative, "/app/" + relative]))
                (build / "Dockerfile").write_text("\n".join(lines) + "\n")
                tag = f"doxagent-source-maintenance:{receipt.action_id}-{service}"
                pinned = (
                    "doxagent-source-maintenance-baseline:"
                    + manifest.images[service].split(":")[-1]
                )
                self.docker.run(["image", "tag", manifest.images[service], pinned])
                self.docker.run(
                    [
                        "build",
                        "-f",
                        str(build / "Dockerfile"),
                        "--build-arg",
                        "BASE_IMAGE=" + pinned,
                        "--tag",
                        tag,
                        str(build),
                    ],
                    timeout=180,
                )
                images[service] = json.loads(self.docker.run(["image", "inspect", tag]))[0]["Id"]
            receipt.after = {
                "images": images,
                "files": {
                    x: hashlib.sha256(safe_file(path, x).read_bytes()).hexdigest() for x in changed
                },
                "candidate_commit": incident.context.get("candidate_commit"),
                "code_return": "LOCAL_BRANCH_ONLY_NOT_MERGED",
            }
            receipt.status = "RUNNING"
            self.repository.save_action(receipt)  # persist BEFORE changing live services
            deployment = images
        override = (
            self.settings.root / "incidents" / incident.incident_id / "deployment.override.json"
        )
        write_json(override, {"services": {s: {"image": i} for s, i in deployment.items()}})
        drain = "v2-site-access" in deployment
        if drain:
            self.api("POST", "/v1/maintenance/action", {"kind": "DRAIN"})
        try:
            self.docker.compose(override, list(deployment))
        finally:
            if drain:
                self.api("POST", "/v1/maintenance/action", {"kind": "UNDRAIN"})
        self._publish_manifest(receipt, manifest)

    def _publish_manifest(self, receipt: ActionReceipt, manifest: DeploymentManifest) -> None:
        if not receipt.after.get("candidate_commit"):
            raise ValueError("committed candidate missing; cannot publish deployed source lineage")
        published = manifest.model_copy(
            update={
                "source_commit": receipt.after["candidate_commit"],
                "images": {**manifest.images, **receipt.after["images"]},
                "file_hashes": {**manifest.file_hashes, **receipt.after["files"]},
                "applied_patches": list(
                    dict.fromkeys([*manifest.applied_patches, receipt.action_id])
                ),
            }
        )
        payload = published.model_dump(mode="json")
        receipt.after["manifest"] = payload
        write_json(
            self.settings.root
            / "incidents"
            / receipt.incident_id
            / (receipt.action_id + "-deployment-manifest.json"),
            payload,
        )
        write_json(self.settings.root / "deployment-head.json", payload)

    def rollback(self, receipt: ActionReceipt) -> None:
        if self.settings.mode != "apply" or not self.settings.enabled:
            raise RuntimeError("rollback disabled")
        if receipt.status == "ROLLED_BACK":
            return
        if receipt.kind == ActionKind.APPLY_SOURCE_PATCH:
            current = self._containers()
            after = receipt.after["images"]
            for service, image in after.items():
                if current[service]["Image"] not in {image, receipt.before["images"][service]}:
                    raise RuntimeError("human/service revision changed; rollback requires review")
            path = self.settings.root / "incidents" / receipt.incident_id / "rollback.override.json"
            write_json(
                path, {"services": {s: {"image": i} for s, i in receipt.before["images"].items()}}
            )
            self.docker.compose(path, list(after))
            head = self.settings.root / "deployment-head.json"
            published = receipt.after.get("manifest")
            if (
                head.is_file()
                and published
                and json.loads(head.read_text())["images"] == published["images"]
            ):
                write_json(head, receipt.before["manifest"])
        elif receipt.kind in {
            ActionKind.SELECT_EXISTING_COMBINATION,
            ActionKind.PATCH_SOURCE_PARAMETERS,
        }:
            current = self.api("GET", "/v1/maintenance/snapshot/" + receipt.target)
            if receipt.kind == ActionKind.PATCH_SOURCE_PARAMETERS:
                if current["spec"]["revision"] != receipt.after["revision"]:
                    raise RuntimeError("human configuration changed; do not overwrite")
                before = receipt.before["spec"]
                changed = {}
                for section in ("body", "crawler"):
                    original = (before.get(section) or {}).get("parameters", {})
                    present = (current["spec"].get(section) or {}).get("parameters", {})
                    for name in set(original) | set(present):
                        value = original.get(name)
                        if value != present.get(name):
                            changed[f"{section}.parameters.{name}"] = value
                if not changed:
                    raise RuntimeError("parameter removal needs explicit review")
                self.api(
                    "POST",
                    "/v1/maintenance/action",
                    {
                        "kind": receipt.kind,
                        "site_id": receipt.target,
                        "expected_revision": current["spec"]["revision"],
                        "parameters": changed,
                    },
                )
            else:
                if current["runtime"]["generation"] != receipt.after["generation"]:
                    raise RuntimeError("human runtime generation changed; do not overwrite")
                old = receipt.before["runtime"]["active_combination_id"]
                if not old or not receipt.before.get("canary"):
                    raise RuntimeError("no prior verified combination to restore")
                canary = dict(receipt.before["canary"])
                canary["operation_id"] += ":rollback"
                self.api(
                    "POST",
                    "/v1/maintenance/action",
                    {
                        "kind": receipt.kind,
                        "site_id": receipt.target,
                        "combination_id": old,
                        "expected_generation": current["runtime"]["generation"],
                        "canary": canary,
                    },
                )
        else:
            raise ValueError("action is not reversible")
        receipt.status = "ROLLED_BACK"
        self.repository.save_action(receipt)
