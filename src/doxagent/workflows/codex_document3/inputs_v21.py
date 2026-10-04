"""Freeze original published inputs without invoking research providers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .schema import Document2Ref, EventLibraryRef
from .state_v21 import canonical, digest


def utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("V21 as_of requires an explicit timezone")
    return value.astimezone(UTC)


class InputPreparerV21:
    def __init__(self, legacy, workspace):
        self.legacy = legacy
        self.workspace = workspace

    async def prepare(
        self,
        *,
        ticker,
        as_of,
        document2_run_id=None,
        source_global_run_id=None,
        event_library_version=None,
        additional_materials=(),
    ):
        as_of = utc(as_of)
        files, manifest, shells, warnings = {}, [], [], []
        d2ref = eventref = None

        def add(frozen_path, content, **metadata):
            files[frozen_path] = content
            manifest.append(
                {
                    **metadata,
                    "path": frozen_path,
                    "sha256": digest(content),
                    "size_bytes": len(content.encode()),
                    "availability": "available",
                }
            )

        repo = self.legacy._runtime_repository
        if document2_run_id:
            bundle = repo.get_bundle(document2_run_id)
            if bundle is None or bundle.status != "published" or bundle.handoff is None:
                raise ValueError("D2 must be a published snapshot")
            if bundle.ticker.upper() != ticker:
                raise ValueError("D2 ticker mismatch")
            artifact_id = bundle.handoff.document2_artifact_id
            published = repo.get_published_document(document2_run_id, artifact_id)
            if published is None:
                raise ValueError("D2 published metadata missing")
            content = await self.legacy._read_document(published)
            text = content.decode("utf8")
            if len(content) != published.size_bytes or digest(text) != published.sha256:
                raise ValueError("D2 original bytes checksum mismatch")
            raw = json.loads(text)
            if raw.get("ticker", "").upper() != ticker:
                raise ValueError("D2 content ticker mismatch")
            add(
                "context/document3/v21/shared/document2.json",
                text,
                source_run_id=document2_run_id,
                published_at=bundle.handoff.published_at.isoformat(),
                economic_as_of=raw.get("as_of"),
            )
            d2ref = Document2Ref(
                run_id=document2_run_id,
                artifact_id=artifact_id,
                sha256=published.sha256,
                published_at=bundle.handoff.published_at,
                publication_state=bundle.handoff.publication_state,
            ).model_dump(mode="json")
            source_global_run_id = source_global_run_id or raw.get("source_global_run_id")
            economic = raw.get("as_of")
            future = economic and utc(datetime.fromisoformat(economic)) > as_of
            if raw.get("schema_version") == "document2.v2.1" and not future:
                from doxagent.workflows.codex_document2.schema import ExpectationShellV21

                for i, shell in enumerate(raw.get("shells", []), 1):
                    # Salvage Units independently; original full source remains available.
                    try:
                        from doxagent.workflows.codex_document2.schema import ExpectationUnitV21

                        units = []
                        for j, unit in enumerate(shell.get("units", []), 1):
                            try:
                                units.append(
                                    ExpectationUnitV21.model_validate(unit).model_dump(mode="json")
                                )
                            except ValueError:
                                warnings.append(f"D2 malformed Unit:{i}:{j}")
                        normalized = ExpectationShellV21.model_validate({**shell, "units": units})
                        path = f"context/document3/v21/d2/shell-{i:04d}.json"
                        add(path, canonical(normalized.model_dump(mode="json")))
                        shells.append(
                            {
                                "slot": f"S{i:04d}",
                                "name": normalized.name,
                                "path": path,
                                "scope": normalized.scope,
                                "boundary": normalized.boundary,
                                "units": [
                                    {"name": u.name, "scope": u.scope, "horizon": u.horizon}
                                    for u in normalized.units
                                ],
                            }
                        )
                    except (ValueError, TypeError, AttributeError):
                        warnings.append(f"D2 malformed Shell:{i}")
            else:
                warnings.append("D2 is historical/shared only or beyond cutoff")
                if future:
                    manifest[-1]["availability"] = "unavailable_after_cutoff"
        if source_global_run_id:
            bundle = repo.get_bundle(source_global_run_id)
            if bundle is None or bundle.status != "published" or bundle.ticker.upper() != ticker:
                raise ValueError("Global source must be a published matching snapshot")
            references = list(getattr(bundle, "reports", {}).values())
            if hasattr(repo, "list_artifacts"):
                references.extend(repo.list_artifacts(source_global_run_id, limit=500))
            seen = set()
            for reference in references:
                if reference.relative_path in seen:
                    continue
                seen.add(reference.relative_path)
                try:
                    file = await self.workspace.read_text(
                        source_global_run_id, reference.relative_path
                    )
                    if file.content is None or digest(file.content) != reference.sha256:
                        raise ValueError("Global original checksum mismatch")
                    path = f"context/document3/v21/shared/global/{len(seen):04d}.txt"
                    add(
                        path,
                        file.content,
                        source_run_id=source_global_run_id,
                        original_path=reference.relative_path,
                        published_at=bundle.published_at.isoformat()
                        if bundle.published_at
                        else None,
                    )
                except (ValueError, FileNotFoundError):
                    warnings.append(f"unavailable Global artifact:{reference.relative_path}")
            structure = {
                key: [
                    x.model_dump(mode="json") if hasattr(x, "model_dump") else x
                    for x in getattr(bundle, key, [])
                ]
                for key in ("future_nodes", "entity_relations")
            }
            add(
                "context/document3/v21/shared/global/structure.json",
                canonical(structure),
                source_run_id=source_global_run_id,
            )
        reader = self.legacy._event_library_reader
        if reader:
            snapshot = reader.reference_view(ticker, version=event_library_version)
            if snapshot and snapshot.published_at:
                # An unpinned latest snapshot cannot leak post-cutoff material.
                economic = getattr(snapshot, "as_of", None)
                allowed = (economic is None or utc(economic) <= as_of) and (
                    event_library_version is not None or utc(snapshot.published_at) <= as_of
                )
                if allowed:
                    eventref = EventLibraryRef(
                        contract_version=snapshot.contract_version,
                        ticker=snapshot.ticker,
                        version=snapshot.version,
                        sha256=snapshot.sha256,
                        published_at=snapshot.published_at,
                    ).model_dump(mode="json")
                    add(
                        "context/document3/v21/shared/event_library.md",
                        snapshot.reference_view,
                        published_at=snapshot.published_at.isoformat(),
                        economic_as_of=economic.isoformat() if economic else None,
                    )
                else:
                    warnings.append("Event snapshot unavailable after cutoff")
        for i, item in enumerate(additional_materials):
            descriptor = {"path": str(item)} if isinstance(item, (str, Path)) else dict(item)
            if descriptor.get("run_id"):
                file = await self.workspace.read_text(descriptor["run_id"], descriptor["path"])
                text = file.content
                if text is None or (
                    descriptor.get("sha256") and digest(text) != descriptor["sha256"]
                ):
                    raise ValueError("additional artifact checksum mismatch")
            else:
                text = Path(descriptor["path"]).read_bytes().decode("utf8")
                if descriptor.get("sha256") and digest(text) != descriptor["sha256"]:
                    raise ValueError("additional local material checksum mismatch")
            economic = descriptor.get("as_of")
            availability = (
                "unavailable_after_cutoff"
                if economic and utc(datetime.fromisoformat(economic)) > as_of
                else "available"
            )
            add(
                f"context/document3/v21/shared/additional/{i:04d}.txt",
                text,
                **{**descriptor, "original_path": descriptor["path"]},
            )
            manifest[-1]["availability"] = availability
        topology = {
            "shells": shells,
            "route_aliases": {
                f"{s['name']}::shell-{i:04d}": s["slot"] for i, s in enumerate(shells, 1)
            },
            "owners": {
                **{s["slot"]: s["name"] for s in shells},
                "OPEN": "OPEN",
                "GLOBAL": "GLOBAL",
            },
        }
        previous = self.legacy._policy_repository.get_current(ticker)
        previous_ids = []
        if previous:
            previous_ids = [p.policy_id for p in previous.policies]
            add(
                "context/document3/v21/shared/previous_policy_set.json",
                previous.model_dump_json(indent=2),
                policy_set_version=previous.policy_set_version,
                published_at=previous.published_at.isoformat(),
            )
        for kind, available, configured in [
            ("document2", bool(d2ref), bool(document2_run_id)),
            ("global", bool(source_global_run_id), bool(source_global_run_id)),
            ("event_library", bool(eventref), reader is not None),
            ("additional", bool(additional_materials), bool(additional_materials)),
        ]:
            if not available:
                manifest.append(
                    {
                        "path": f"context/document3/v21/shared/{kind}",
                        "availability": "ABSENT" if configured else "NOT_CONFIGURED",
                    }
                )
        add("context/document3/v21/topology.json", canonical(topology))
        return {
            "files": files,
            "manifest": manifest,
            "topology": topology,
            "document2_ref": d2ref,
            "event_library_ref": eventref,
            "warnings": warnings,
            "as_of": as_of.isoformat(),
            "previous_policy_ids": previous_ids,
        }
