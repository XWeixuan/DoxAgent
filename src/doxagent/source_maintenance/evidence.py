"""Frozen bounded evidence. Remote text is data, never action authorization."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from .repository import MaintenanceRepository
from .schema import Incident, Stage

_SECRET = re.compile(r"(token|password|secret|authorization|cookie|api.?key|credential)", re.I)
_VALUES = re.compile(
    r"(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|"
    r"(?i:password|token|api_key|cookie)\s*[:=]\s*[^\s,;]+)"
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if _SECRET.search(str(k)) else redact(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value[:1000]]
    if isinstance(value, str):
        return _VALUES.sub("[REDACTED]", value)[:16000]
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")
    )
    temp.replace(path)


class EvidenceStore:
    def __init__(self, root: Path, repository: MaintenanceRepository):
        self.root, self.repository = root, repository

    def freeze(self, incident: Incident, name: str, payload: Any) -> Path:
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError("invalid evidence name")
        encoded = (
            json.dumps(redact(payload), ensure_ascii=False, sort_keys=True, indent=2, default=str)
            + "\n"
        ).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        path = self.root / "incidents" / incident.incident_id / "evidence" / f"{name}-{digest}.json"
        if not path.exists():
            write_json(path, json.loads(encoded))
        self.repository.save_evidence(
            incident.incident_id, digest, {"path": str(path), "sha256": digest, "name": name}
        )
        return path

    def pack(self, incident: Incident, extra: dict | None = None) -> Path:
        rows = [
            s.model_dump(mode="json")
            for s in self.repository.samples()
            if s.sample_id in incident.context.get("sample_ids", [])
        ]
        state = incident.model_dump(mode="json", exclude={"owner", "lease_until", "updated_at"})
        return self.freeze(
            incident,
            "context",
            {
                "untrusted_evidence": True,
                "incident": state,
                "samples": rows,
                "deployment": extra or {},
                "timezone_labels": ["UTC", "America/New_York", "Asia/Shanghai"],
            },
        )

    def archive_closed(self, *, days: int = 30) -> None:
        """Archive old terminal evidence only; never delete Git candidates or SDK sessions."""
        cutoff = self.repository.clock() - timedelta(days=days)
        for incident in self.repository.incidents():
            if (
                incident.stage not in {Stage.STABLE, Stage.CANCELLED, Stage.ROLLED_BACK}
                or incident.updated_at >= cutoff
            ):
                continue
            folder = self.root / "incidents" / incident.incident_id / "evidence"
            if folder.is_symlink() or not folder.is_dir():
                continue
            files = sorted(p for p in folder.glob("*.json") if p.is_file() and not p.is_symlink())
            if not files:
                continue
            archive = self.root / "archives" / (incident.incident_id + ".zip")
            archive.parent.mkdir(parents=True, exist_ok=True)
            temp = archive.with_suffix(".tmp")
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as output:
                if archive.is_file():
                    with zipfile.ZipFile(archive) as previous:
                        for name in previous.namelist():
                            if name not in {p.name for p in files}:
                                output.writestr(name, previous.read(name))
                for path in files:
                    output.writestr(path.name, path.read_bytes())
            temp.replace(archive)
            with zipfile.ZipFile(archive) as saved:
                for path in files:
                    if saved.read(path.name) == path.read_bytes():
                        path.unlink()
            self.repository.save_evidence(
                incident.incident_id, "archive", {"path": str(archive), "compressed": True}
            )
            return  # finite cleanup per tick, active incidents are untouched


def evidence_app(store: EvidenceStore, token: str):
    """Only frozen, incident-associated data; no SQL, paths or arbitrary command inputs."""
    import hmac

    from fastapi import FastAPI, Header, HTTPException

    app = FastAPI(title="Source Maintenance Evidence")

    @app.get("/v1/incidents/{incident_id}/{kind}")
    def fetch(incident_id: str, kind: str, authorization: str | None = Header(default=None)):
        if not token or not hmac.compare_digest(authorization or "", "Bearer " + token):
            raise HTTPException(401)
        if kind not in {
            "get_poll_health",
            "get_access_events",
            "get_runtime_status",
            "get_deployment_manifest",
            "get_bounded_log",
        }:
            raise HTTPException(404)
        try:
            incident = store.repository.get(incident_id)
        except KeyError as exc:
            raise HTTPException(404) from exc
        if kind == "get_poll_health":
            return redact(
                [
                    s.model_dump(mode="json")
                    for s in store.repository.samples()
                    if s.source_id in incident.source_ids
                ][-100]
            )
        return redact(incident.context.get("evidence_views", {}).get(kind, {}))

    return app
