"""Atomic prompt/skill publication; old executions read immutable local content."""

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .journal import RuntimeJournal, digest
from .prompts import RuntimeV2PromptSet


class ExecutionBundles:
    def __init__(self, journal: RuntimeJournal) -> None:
        self.journal = journal

    def publish(
        self,
        prompts: RuntimeV2PromptSet,
        *,
        assets: dict[str, str] | None = None,
        orchestration_revision: int | None = None,
    ) -> str:
        value: dict[str, Any] = {
            "compatibility": 1,
            "orchestration_revision": orchestration_revision or (2 if prompts.w2_r3 else 1),
            "prompts": asdict(prompts),
            "assets": assets or {},
        }
        if any(item is not None and not item.strip() for item in value["prompts"].values()):
            raise ValueError("empty runtime prompt")
        if value["orchestration_revision"] >= 3:
            from .schema import W3CaseResultV3
            from doxagent.workflows.codex_document3.schema import strict_json_schema

            schema = strict_json_schema(W3CaseResultV3.model_json_schema())
            value["contracts"] = dict(
                policy_schema="document3.v3",
                projection_schema="document3.runtime_projection.v5",
                maintenance_feed="persistent-runtime.o3-maintenance-feed.v2",
                w3_result="persistent-runtime.w3-result.v3",
                w3_output_schema=schema,
                w3_output_schema_hash=digest(schema),
            )
        identity = self.journal.freeze(value)
        self.journal.set("execution", "active", identity)
        return identity

    def load_manifest(self, path: str | Path) -> str:
        import json

        manifest_path = Path(path).resolve()
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        if manifest.get("compatibility") != 1:
            raise ValueError("incompatible execution manifest")
        assets: dict[str, str] = {}
        root = manifest_path.parent
        for name, expected in manifest["files"].items():
            target = (root / name).resolve()
            if not target.is_relative_to(root):
                raise ValueError("execution asset must remain inside manifest directory")
            import hashlib

            content = target.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f"execution asset hash mismatch: {name}")
            assets[name] = content.decode("utf-8")
        if manifest_path.read_bytes() != raw:
            raise ValueError("execution manifest changed during load")
        revision = manifest.get("orchestration_revision", 1)
        prompts = RuntimeV2PromptSet(
            **{
                name: assets[f"{name}.md"].strip()
                for name in (
                    "core",
                    "w1_r1",
                    "w1_r2",
                    "w1_r3",
                    "w2_r1",
                    "w2_r2",
                    *(["w2_r3"] if revision >= 2 else []),
                )
            }
        )
        active = self.journal.get("execution", "active")
        previous = self.get(active)["assets"] if active else {}
        return self.publish(prompts, assets={**previous, **assets}, orchestration_revision=revision)

    def get(self, identity: str) -> dict[str, Any]:
        value = self.journal.snapshot(identity)
        if value["compatibility"] != 1 or digest(value) != identity:
            raise ValueError("incompatible execution bundle")
        return dict(value)
