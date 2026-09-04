"""Static validation for the frozen MU W1/W2 future corpus."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
MESSAGES = ROOT / "messages.jsonl"
GOLD = ROOT / "gold.jsonl"
MANIFEST = ROOT / "manifest.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise AssertionError(f"{path.name}:{line_no} must be a JSON object")
        rows.append(value)
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    messages = _load_jsonl(MESSAGES)
    gold = _load_jsonl(GOLD)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert len(messages) == 25
    assert len(gold) == 25
    expected_ids = {f"MU-W12-{number:03d}" for number in range(1, 26)}
    assert {row["case_id"] for row in messages} == expected_ids
    assert {row["case_id"] for row in gold} == expected_ids
    assert [row["case_id"] for row in messages] == [row["case_id"] for row in gold]

    cutoff = datetime.fromisoformat("2026-09-03T15:18:39.758709+00:00")
    word_counts: dict[str, int] = {}
    kinds: Counter[str] = Counter()
    prior_order_key: tuple[datetime, str] | None = None
    forbidden_financial_story_terms = (
        "earnings release",
        "earnings call",
        "quarterly results",
        "earnings preview",
        "revenue guidance",
        "eps guidance",
        "gross-margin guidance",
        "gross margin guidance",
    )
    audit_payload_fields = {
        "version_pin",
        "schema_version",
        "consumer_contract",
        "policy_set_published_at",
        "activation_revision",
    }
    for row in messages:
        case_id = row["case_id"]
        assert row["synthetic"] is True
        assert set(row["source_message"]) == {"ticker", "title", "body"}
        assert not audit_payload_fields.intersection(row["source_message"])
        assert row["source_message"]["ticker"] == "MU"
        assert row["source_message"]["title"].strip()
        body = row["source_message"]["body"].strip()
        assert body
        word_counts[case_id] = len(body.split())
        assert word_counts[case_id] >= 500, (case_id, word_counts[case_id])
        assert "hypothetical" not in body.lower()
        assert "synthetic test" not in body.lower()
        title_and_body = f'{row["source_message"]["title"]} {body}'.lower()
        assert not any(term in title_and_body for term in forbidden_financial_story_terms), case_id
        published_at = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
        assert published_at > cutoff, case_id
        order_key = (published_at, case_id)
        assert prior_order_key is None or prior_order_key <= order_key, case_id
        prior_order_key = order_key
        kinds[row["source_kind"]] += 1

    assert kinds == Counter({"NEWS": 20, "OFFICIAL": 4, "GOVERNMENT": 1}), kinds
    messages_by_id = {row["case_id"]: row for row in messages}
    assert messages_by_id["MU-W12-019"]["depends_on_case_ids"] == ["MU-W12-018"]
    assert messages_by_id["MU-W12-024"]["depends_on_case_ids"] == ["MU-W12-013"]

    novelty = Counter(
        row["w1"]["final"]["expected_output"]["result"] for row in gold
    )
    assert novelty == Counter({"NEW": 13, "OLD": 12}), novelty
    policy_cases = sorted(
        row["case_id"]
        for row in gold
        if row["w2"]["final"]["expected_output"]["policy_ids"]
    )
    assert policy_cases == ["MU-W12-018", "MU-W12-019", "MU-W12-021", "MU-W12-022"]

    r2_shapes = {
        row["case_id"]: (
            row["w2"]["r1"]["expected_output"]["confidence"],
            None
            if row["w2"]["r2"] is None
            else row["w2"]["r2"]["expected_output"]["confidence"],
            None
            if row["w2"]["r2"] is None
            else bool(row["w2"]["r2"]["expected_output"]["policy_ids"]),
        )
        for row in gold
        if row["w2"]["r2"] is not None
    }
    assert r2_shapes == {
        "MU-W12-017": ("low", "normal", False),
        "MU-W12-021": ("low", "normal", True),
        "MU-W12-022": ("low", "low", True),
    }, r2_shapes

    r3_cases = sorted(
        row["case_id"] for row in gold if row["w1"]["r3"]["expected_execution"]
    )
    assert r3_cases == ["MU-W12-018", "MU-W12-021"]

    gold_by_id = {row["case_id"]: row for row in gold}
    for consumer, producer in {
        "MU-W12-019": "MU-W12-018",
        "MU-W12-024": "MU-W12-013",
    }.items():
        dependency = gold_by_id[consumer]["w1"]["r1"]["provisional_dependency"]
        reference = gold_by_id[consumer]["w1"]["final"]["reference_expectation"]
        assert dependency["producer_case_id"] == producer
        assert dependency["must_include_allocated_provisional_event_id"] is True
        assert reference["mode"] == "provisional_from_case"
        assert reference["producer_case_id"] == producer
        assert reference["must_include_allocated_provisional_event_id"] is True
        producer_at = datetime.fromisoformat(
            messages_by_id[producer]["published_at"].replace("Z", "+00:00")
        )
        consumer_at = datetime.fromisoformat(
            messages_by_id[consumer]["published_at"].replace("Z", "+00:00")
        )
        assert producer_at < consumer_at

    for row in gold:
        reference = row["w1"]["final"]["reference_expectation"]
        if reference["mode"] == "static":
            assert len(reference["must_include"]) <= reference["max_items"]
            assert set(reference["must_include"]).isdisjoint(reference["may_include"])
            if row["w1"]["final"]["expected_output"]["result"] == "OLD":
                assert reference["must_include"]
        assert len(row["w1"]["r1"]["must_include_event_ids"]) <= 5
        assert len(row["w2"]["final"]["expected_output"]["policy_ids"]) <= 3

    hbm_policy = "pol_4fbb78816d7863893f09"
    qlc_policy = "pol_df1332e7ce712ae0dc0c"
    case_21 = gold_by_id["MU-W12-021"]
    expected_multi_order = [hbm_policy, qlc_policy]
    assert case_21["w2"]["r1"]["expected_output"] == {
        "policy_ids": expected_multi_order,
        "confidence": "low",
    }
    assert case_21["w2"]["r2"]["expected_output"]["policy_ids"] == expected_multi_order
    assert case_21["w2"]["r2"]["expected_output"]["confidence"] == "normal"
    assert case_21["w2"]["final"]["expected_output"]["policy_ids"] == expected_multi_order
    assert case_21["w2"]["final"]["expected_output"]["matched_condition_ids"] == [
        {"policy_id": hbm_policy, "condition_ids": ["C1"]},
        {"policy_id": qlc_policy, "condition_ids": ["C1"]},
    ]

    interruption_policy = "pol_a2fb4594422ae0c078a2"
    for case_id in ("MU-W12-018", "MU-W12-019"):
        expected = gold_by_id[case_id]["w2"]["final"]["expected_output"]
        assert expected == {
            "policy_ids": [interruption_policy],
            "confidence": "normal",
            "matched_condition_ids": [
                {"policy_id": interruption_policy, "condition_ids": ["C1"]}
            ],
        }

    restriction_policy = "pol_e28a1b1e8eedc9bf53f5"
    case_22 = gold_by_id["MU-W12-022"]
    assert case_22["w2"]["r1"]["expected_output"] == {
        "policy_ids": [restriction_policy],
        "confidence": "low",
    }
    assert case_22["w2"]["r2"]["expected_output"] == {
        "policy_ids": [restriction_policy],
        "confidence": "low",
        "matched_condition_ids": [],
    }
    assert case_22["w2"]["final"]["expected_output"] == {
        "policy_ids": [restriction_policy],
        "confidence": "low",
        "matched_condition_ids": [],
    }

    for item in manifest["frozen_inputs"]:
        path = Path(item["path"])
        assert path.is_file(), path
        assert _sha256(path) == item["sha256"], path

    inputs_by_purpose = {
        item["purpose"]: Path(item["path"]) for item in manifest["frozen_inputs"]
    }
    expected_event_ids: set[str] = set()
    expected_policy_ids: set[str] = set()
    for row in gold:
        expected_event_ids.update(row["w1"]["r1"]["must_include_event_ids"])
        expected_event_ids.update(row["w1"]["r1"]["may_include_event_ids"])
        reference = row["w1"]["final"]["reference_expectation"]
        if reference["mode"] == "static":
            expected_event_ids.update(reference["must_include"])
            expected_event_ids.update(reference["may_include"])
        expected_policy_ids.update(row["w2"]["r1"]["expected_output"]["policy_ids"])
        expected_policy_ids.update(row["w2"]["final"]["expected_output"]["policy_ids"])
        expected_policy_ids.update(row["w2"]["near_miss_policy_ids"])
        if row["w2"]["r2"] is not None:
            expected_policy_ids.update(
                row["w2"]["r2"]["expected_output"]["policy_ids"]
            )

    with sqlite3.connect(inputs_by_purpose["W1 canonical Event Detail store"]) as connection:
        available_event_ids = {
            f"E{event_no}"
            for (event_no,) in connection.execute(
                "SELECT event_no FROM canonical_events WHERE ticker='MU'"
            )
        }
        active_event_count = connection.execute(
            "SELECT count(1) FROM canonical_event_states "
            "WHERE ticker='MU' AND status='ACTIVE'"
        ).fetchone()[0]
    assert expected_event_ids.issubset(available_event_ids), (
        expected_event_ids - available_event_ids
    )

    policy_set = json.loads(inputs_by_purpose["PolicySet source"].read_text(encoding="utf-8"))
    available_policy_ids = {item["policy_id"] for item in policy_set["policies"]}
    assert expected_policy_ids.issubset(available_policy_ids), (
        expected_policy_ids - available_policy_ids
    )
    reference_view_count = sum(
        1
        for line in inputs_by_purpose["latest MU Event Library Reference View"]
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("E") and " | " in line and line.split(" | ", 1)[0][1:].isdigit()
    )
    assert manifest["baseline_summary"] == {
        "policy_set_version": policy_set["policy_set_version"],
        "policy_set_publication_state": policy_set["publication_state"],
        "policy_count": len(policy_set["policies"]),
        "activation_condition_count": sum(
            len(item["activation_conditions"]) for item in policy_set["policies"]
        ),
        "reference_view_version": 1,
        "reference_view_event_count": reference_view_count,
        "full_event_library_active_event_count": active_event_count,
        "policy_selection_note": "The corpus intentionally uses only Policies judged plausible as future observed events; it is not a 42-Policy coverage suite.",
    }
    assert manifest["model_visible_source_message_fields"] == ["ticker", "title", "body"]

    assert manifest["artifacts"]["messages.jsonl"]["sha256"] == _sha256(MESSAGES)
    assert manifest["artifacts"]["gold.jsonl"]["sha256"] == _sha256(GOLD)

    assert manifest["expected_distribution"] == {
        "messages": 25,
        "new": 13,
        "old": 12,
        "policy_non_empty": 4,
        "news": 20,
        "official": 4,
        "government": 1,
        "minimum_body_words": 500,
    }

    print(
        json.dumps(
            {
                "status": "PASS",
                "messages": len(messages),
                "word_count_min": min(word_counts.values()),
                "word_count_median": sorted(word_counts.values())[12],
                "word_count_max": max(word_counts.values()),
                "novelty": dict(novelty),
                "source_kinds": dict(kinds),
                "policy_cases": policy_cases,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
