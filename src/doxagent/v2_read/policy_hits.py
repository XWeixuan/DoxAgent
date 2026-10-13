"""Final analysis hits are independent of consumption, release, and execution."""

from datetime import datetime

from doxagent.semantic_clock import semantic_day


def project(store, case, summary, event):
    ticker, case_id = case["source"]["snapshot"]["ticker"], case["case_id"]
    final = summary["final_policy_hit"]["value"]
    result = (case.get("w3_result") or {}).get("policy") or case.get("w2_final") or {}
    hit_ids = set(result.get("policy_ids", [])) if final is True else set()
    policies = (case.get("frozen_inputs", {}).get("projection") or {}).get("policies", [])
    records, contributions = [], []
    revisions = None
    for policy in policies:
        policy_id = policy["policy_id"]
        ar = policy.get("activation_revision")
        if ar is None:
            if revisions is None:
                details = store.get(
                    "activation_revision", ticker,
                    case["version_pin"].get("activation_revision_id") or "",
                )
                artifact_id = (details or {}).get("policy_set", {}).get("artifact_id")
                from .lifecycle import rows

                revisions = {}
                if artifact_id:
                    for detail in rows(store, "policy_detail", ticker, artifact_id).values():
                        item = detail["summary"]
                        # Preserve the prior first-match behavior for duplicate IDs.
                        revisions.setdefault(item["policy_id"], item["policy_revision_id"])
            if policy_id not in revisions:
                continue
            ar = revisions[policy_id]
        identity = case_id + ":" + policy_id
        old = store.get("policy_hit_observation", ticker, identity)
        hit = policy_id in hit_ids
        at = old.get("occurred_at") if old and old["hit"] == hit else None
        if at is None and final is not None and event["operation"] != "BACKFILL":
            at = event["recorded_at"]
        if at is None and old and not hit:
            at = old.get("occurred_at")
        records.append(
            {
                "kind": "policy_hit_observation",
                "ticker": ticker,
                "id": identity,
                "parent": case_id,
                "data": {"policy_id": policy_id, "ar": ar, "hit": hit, "occurred_at": at},
            }
        )
        # Historical hits without their settlement time remain unknown, not relocated to today.
        if at is None:
            continue
        day = semantic_day(datetime.fromisoformat(at)).isoformat()
        for metric, business_key in (
            ("policy_hits", policy_id),
            ("policy_ar_hits", policy_id + ":" + ar),
        ):
            contributions.append(
                {
                    "metric": metric,
                    "ticker": ticker,
                    "entity": identity,
                    "day": day,
                    "value": "1" if hit else "0",
                    "dimensions": {"business_key": business_key, "policy_id": policy_id, "ar": ar},
                }
            )
    return records, contributions
