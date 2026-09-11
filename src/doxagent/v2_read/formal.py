"""Register D1/D2 only from an admitted V2 initialization's exact artifact refs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from doxagent.api_v2.dto import available, missing, validate
from doxagent.workflows.codex_document3.runtime_projection import policy_activation_revision
from doxagent.workflows.codex_document3.schema import PolicySet

from .artifacts import ArtifactIndexer, PublishedArtifacts
from .libraries import LibraryIndexer
from .projector import native
from .projectors import DomainProjectors
from .repository import ReadStore, encode


class FormalProjectors(DomainProjectors):
    def coverage(self, source, capture):
        from .lifecycle import consumption_projection

        return consumption_projection(self.store, capture) if source == "runtime" else []

    def __init__(
        self,
        store: ReadStore,
        research: PublishedArtifacts | None = None,
        event_root: str | Path | None = None,
    ) -> None:
        super().__init__(store)
        self.artifacts = ArtifactIndexer(store, research) if research else None
        self.event_root = event_root

    def __call__(self, event: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records, metrics = super().__call__(event)
        if event["table_name"] == "v2_business_imports":
            value = native(event)
            records.append(
                {
                    "kind": "history_classification",
                    "ticker": value["ticker"],
                    "id": value["entity_id"],
                    "data": value,
                }
            )
            if value["classification"] == "BUSINESS_V2":
                records.append(
                    {
                        "kind": "business_provenance",
                        "ticker": value["ticker"],
                        "id": value["entity_id"],
                        "data": value,
                    }
                )
        if event["table_name"] == "v2_model_invocations":
            from .api_usage import project

            calls, counts = project(native(event))
            records.extend(calls)
            metrics.extend(counts)
        if (
            event["table_name"] == "runtime_values"
            and event["row"]["namespace"] == "reference_deliveries"
        ):
            from .reference import project

            value = native(event)
            if value.get("control_epoch") is None and not self.store.get(
                "business_provenance", value["ticker"], value["delta_id"]
            ):
                raise ValueError("PROVENANCE_UNVERIFIED")
            records.extend(project(self.store, value))
        if event["table_name"] == "activation_revisions":
            revision = native(event)
            control = self.store.get("ticker", revision["ticker"], revision["ticker"])
            if control or self.store.get(
                "business_provenance", revision["ticker"], revision["revision_id"]
            ):
                records.extend(self.activation(revision))
        if event["table_name"] == "ticker_active_revision":
            pointer = native(event)
            state = self.store.get("native:v2_ticker_control", pointer["ticker"], pointer["ticker"])
            if state and state.get("initialization_incomplete"):
                return records, metrics
            active = self.store.get(
                "activation_revision", pointer["ticker"], pointer["revision_id"]
            )
            if not active:
                raise ValueError("active revision must be indexed before pointer")
            if event["operation"] != "BACKFILL":
                active["activated_at"] = available(event["recorded_at"])
            records.append(
                {"kind": "activation", "ticker": pointer["ticker"], "id": "active", "data": active}
            )
        if event["table_name"] == "v2_ticker_control":
            state = native(event)
            previous = self.store.get("native:v2_ticker_control", state["ticker"], state["ticker"])
            if state.get("activation_id") and (
                not previous or previous.get("activation_id") != state["activation_id"]
            ):
                active = self.store.get(
                    "activation_revision", state["ticker"], state["activation_id"]
                )
                if not active:
                    raise ValueError("admitted activation must be indexed before effective pointer")
                if event["operation"] != "BACKFILL":
                    active["activated_at"] = available(event["recorded_at"])
                records.append(
                    {
                        "kind": "activation",
                        "ticker": state["ticker"],
                        "id": "active",
                        "data": active,
                    }
                )
        if event["table_name"] == "initialization_nodes":
            node = native(event)
            ticker = records[0]["ticker"]
            run = self.store.get("native:initialization_runs", ticker, event["row"]["run_id"])
            if run and run.get("control_operation_id"):
                from .runtime import worker_usage

                for observation in node["receipt"].get("worker_invocations", {}).values():
                    calls, counts = worker_usage(observation)
                    records.extend(calls)
                    metrics.extend(counts)
            if (
                node["status"] == "SUCCEEDED"
                and node["block"] in {"D1", "D2"}
                and event["row"]["node_key"] == node["block"].lower()
            ):
                role = "document1" if node["block"] == "D1" else "document2"
                ticker = records[0]["ticker"]
                run = self.store.get("native:initialization_runs", ticker, event["row"]["run_id"])
                # A historical test run in the same database is not proof of business provenance.
                if not run.get("control_operation_id") and not self.store.get(
                    "business_provenance", ticker, run["initialization_id"]
                ):
                    return records, metrics
                if self.artifacts is None:
                    raise ValueError("formal research source is not configured")
                ref = node["result"]["artifacts"][role]
                records.extend(
                    self.artifacts.index(ref["run_id"], ticker, lineage=run["initialization_id"])
                )
        for record in list(records):
            if record["kind"] == "activation" and record["data"]:
                from .lifecycle import activate

                transitions, counts = activate(
                    self.store,
                    record["ticker"],
                    record["data"],
                    event["recorded_at"] if event["operation"] != "BACKFILL" else None,
                )
                records.extend(transitions)
                metrics.extend(counts)
                from .event_lifecycle import activate as activate_events

                transitions, counts = activate_events(
                    self.store,
                    record["ticker"],
                    record["data"],
                    event["recorded_at"] if event["operation"] != "BACKFILL" else None,
                )
                records.extend(transitions)
                metrics.extend(counts)
        active_records = {
            r["ticker"]: r["data"] for r in records if r["kind"] == "activation" and r["data"]
        }
        for ticker, active in active_records.items():
            prior = self.store.get("activation", ticker, "active")
            for kind, role in (("research_run", "document1"), ("expectations_run", "document2")):
                for identity in {active[role]["run_id"], (prior or active)[role]["run_id"]}:
                    if not any(
                        r["kind"] == kind and r["ticker"] == ticker and r["id"] == identity
                        for r in records
                    ):
                        value = self.store.get(kind, ticker, identity)
                        if value:
                            records.append(
                                {
                                    "kind": kind,
                                    "ticker": ticker,
                                    "id": identity,
                                    "sort": value["created_at"],
                                    "data": value,
                                }
                            )
        for record in records:
            if (
                record["kind"]
                not in {"research_run", "expectations_run", "research", "expectations_index"}
                or not record["data"]
            ):
                continue
            active = active_records.get(record["ticker"]) or self.store.get(
                "activation", record["ticker"], "active"
            )
            role = "document1" if record["kind"].startswith("research") else "document2"
            run = record["data"].get("run", record["data"])
            run["is_active"] = bool(active and active[role]["run_id"] == run["run_id"])
        return records, metrics

    def activation(self, revision: dict[str, Any]) -> list[dict[str, Any]]:
        if self.artifacts is None:
            raise ValueError("formal research source is not configured")
        refs, ticker = revision["artifacts"], revision["ticker"]
        identity = revision["revision_id"]
        records = []
        documents = {}
        for role in ("document1", "document2"):
            run_id = refs[role]["run_id"]
            indexed = self.artifacts.index(run_id, ticker, lineage=identity)
            documents[role] = next(r["data"] for r in indexed if r["kind"] == "document_ref")
            records.extend(indexed)
        root = refs["event_library"].get("root") or self.event_root
        if not root:
            raise ValueError("formal event source is not configured")
        library, indexed = LibraryIndexer(root).index(ticker, refs["event_library"]["version"])
        records.extend(indexed)
        run_id = self._policy_run_id(revision)
        bundle = self.artifacts.source.record("bundles", run_id, run_id)
        if bundle["ticker"] != ticker or bundle["status"] != "published":
            raise ValueError("PINNED_ARTIFACT_MISSING")
        artifact_id = bundle["handoff"]["published_artifact"]["artifact_id"]
        document, body = self.artifacts.source.body(run_id, artifact_id)
        policies = PolicySet.model_validate_json(body)
        if policies.ticker != ticker or policies.policy_set_version != refs["document3"]["version"]:
            raise ValueError("POLICY_VERSION_MISMATCH")
        ref = {
            **self.artifacts.document(document, schema=policies.schema_version),
            "policy_set_version": policies.policy_set_version,
        }
        source_run = policies.document2_ref.run_id
        source_records = self.artifacts.index(source_run, ticker, lineage=identity)
        source_document = next(r["data"] for r in source_records if r["kind"] == "document_ref")
        if source_document["artifact_id"] != policies.document2_ref.artifact_id or (
            source_document["content_sha256"] != policies.document2_ref.sha256
        ):
            raise ValueError("POLICY_SOURCE_DOCUMENT_MISMATCH")
        records.extend(
            r
            for r in source_records
            if r["id"] not in {x["id"] for x in records if x["kind"] == r["kind"]}
        )
        shells = {
            r["data"]["shell_id"]
            for r in records
            if r["kind"] == "shell" and r.get("parent") == documents["document2"]["run_id"]
        }
        for policy in policies.policies:
            ar = policy_activation_revision(policy)
            policy_revision = hashlib.sha256(
                encode([artifact_id, policy.policy_id, policy.model_dump(mode="json")]).encode()
            ).hexdigest()
            shell_ids = list(dict.fromkeys(item.shell_id for item in policy.source_refs))
            summary = validate(
                "PolicySummary",
                {
                    "policy_id": policy.policy_id,
                    "policy_revision_id": policy_revision,
                    "policy_activation_revision": ar,
                    "policy_set_version": policies.policy_set_version,
                    "title": policy.title,
                    "decision": policy.decision.value,
                    "shell_ids": shell_ids,
                    "unresolved_shell_ids": [shell for shell in shell_ids if shell not in shells],
                    "lifecycle": "ACTIVE",
                    "consumed": missing(),
                    "consumed_at": missing(),
                    "effective": missing(),
                    "matched_filters": ["ACTIVE"],
                },
            )
            detail = validate(
                "PolicyDetail",
                {
                    "summary": summary,
                    "source_document2": source_document,
                    "activation_semantics": "OR",
                    "policy": policy.model_dump(mode="json"),
                },
            )
            records.append(
                {
                    "kind": "policy_detail",
                    "ticker": ticker,
                    "id": policy_revision,
                    "parent": artifact_id,
                    "data": detail,
                }
            )
            for shell in shell_ids:
                records.append(
                    {
                        "kind": "policy",
                        "ticker": ticker,
                        "id": policy_revision + ":" + shell,
                        "parent": artifact_id + ":" + shell,
                        "data": summary,
                    }
                )
        original = self.store.put_content(ticker, body, "application/json")
        records.append(
            {
                "kind": "policy_set",
                "ticker": ticker,
                "id": artifact_id,
                "data": {
                    "reference": ref,
                    "source_document2": source_document,
                    "content": original,
                },
            }
        )
        active = validate(
            "Activation",
            {
                "runtime_activation_id": identity,
                "activated_at": missing(),
                **documents,
                "policy_set": ref,
                "event_library": library,
                "monitoring_configuration_id": refs["monitoring_configuration"][
                    "initialization_id"
                ],
            },
        )
        records.append(
            {"kind": "activation_revision", "ticker": ticker, "id": identity, "data": active}
        )
        return records

    def _policy_run_id(self, revision: dict[str, Any]) -> str:
        policy = revision["artifacts"]["document3"]
        if policy.get("run_id"):
            return str(policy["run_id"])
        candidate = revision["revision_id"].removesuffix("-activation") + "-o3"
        if self.artifacts is not None:
            try:
                bundle = self.artifacts.source.record("bundles", candidate, candidate)
            except KeyError:
                bundle = None
            handoff = (bundle or {}).get("handoff") or {}
            if (
                bundle
                and bundle.get("ticker") == revision["ticker"]
                and bundle.get("status") == "published"
                and handoff.get("policy_set_version") == policy["version"]
            ):
                return candidate
        base_revision = revision.get("base_revision")
        base = (
            self.store.get("activation_revision", revision["ticker"], base_revision)
            if base_revision
            else None
        )
        base_policy = (base or {}).get("policy_set") or {}
        if base_policy.get("policy_set_version") == policy["version"] and base_policy.get("run_id"):
            return str(base_policy["run_id"])
        raise ValueError("POLICY_RUN_ID_MISSING")
