# ruff: noqa: E501
# Audit prose is kept as whole string literals so generated rationales remain exact.

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "eval" / "cdecr_relevance_filter" / "mu_relevance_30_v1"
SOURCE_GOLD = BASE / "cdecr_mu_relevance_30_candidates_gold_final.jsonl"
PREDICTIONS = BASE / "runs" / "frozen_gate_20260811_retry1" / "predictions.jsonl"
MAPPINGS = BASE / "runs" / "frozen_gate_20260811_retry1" / "candidate_mention_mapping.jsonl"
SOURCE_REPORT = BASE / "runs" / "frozen_gate_20260811_retry1" / "report.json"
OUTPUT_GOLD = BASE / "cdecr_mu_relevance_30_candidates_gold_readjudicated_v2.jsonl"
OUTPUT_AUDIT = BASE / "gold_dispute_adjudication_v2.json"
OUTPUT_SUMMARY = BASE / "gold_readjudication_v2_summary.json"

CONTEXT_SCOPE = (
    "candidate statement + exact evidence span + containing paragraph; stable public "
    "knowledge may identify an established competitive or complementary-product "
    "exposure, but no other article paragraph may be inherited"
)


def decision(
    relevance: str,
    relevance_type: str,
    path: str,
    rationale: str,
) -> dict[str, str]:
    return {
        "relevance": relevance,
        "relevance_type": relevance_type,
        "transmission_path": path,
        "rationale": rationale,
    }


# Keys are document ID plus the final eight hexadecimal characters of candidate_id.
# Every one of the 35 valid Gate/Gold disagreements is explicitly adjudicated.
DECISIONS: dict[tuple[str, str], dict[str, str]] = {
    ("D021", "63c332eb"): decision(
        "IRRELEVANT",
        "NONE",
        "No target-specific path in the allowed context.",
        "Cheaper DeepSeek model performance is a general AI event; the paragraph does not connect it to memory demand, pricing, or Micron.",
    ),
    ("D021", "6ce61ead"): decision(
        "RELEVANT",
        "DIRECT",
        "Micron is explicitly one of the stocks trading below 20x earnings -> direct update to MU valuation.",
        "List membership is not disqualifying because the list itself states a valuation fact about Micron.",
    ),
    ("D072", "f80ced78"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "AMD hyperscaler accelerator share gains -> accelerator deployment -> HBM demand -> MU demand and market expectations.",
        "AMD accelerators and HBM are established complementary products, and the paragraph explicitly frames hyperscaler deployment and AI-capex exposure.",
    ),
    ("D072", "e54c17f7"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "AMD data-center growth -> expanding accelerator/server deployments -> HBM/DRAM demand -> MU revenue expectations.",
        "The same paragraph quantifies data-center growth and a 6 GW OpenAI deployment, establishing a material AI-infrastructure demand signal.",
    ),
    ("D072", "02826b7f"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "Six-gigawatt GPU deployment -> large HBM/server-memory requirement -> MU demand expectations.",
        "The deployment is concrete and large enough to create a non-trivial complementary-memory demand path.",
    ),
    ("D095", "7404fba9"): decision(
        "IRRELEVANT",
        "NONE",
        "Ciena backlog alone -> no locally established MU exposure.",
        "The paragraph reports orders, supply, and backlog but does not identify AI data centers or another Micron-specific transmission path.",
    ),
    ("D095", "3dc3332d"): decision(
        "IRRELEVANT",
        "NONE",
        "Ciena consolidated revenue guidance alone -> no locally established MU exposure.",
        "The paragraph contains general company growth; the referent of 'This explains' lies outside the allowed context and cannot be inherited.",
    ),
    ("D095", "8d754ba8"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "AI-data-center-driven Ciena profitability -> active data-center interconnect buildout -> server-memory demand -> MU expectations.",
        "The containing paragraph explicitly attributes profitability to AI data-center demand, creating a concrete complementary-infrastructure signal.",
    ),
    ("D095", "5405ad60"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "Optical component demand for AI data centers -> data-center capacity buildout -> server-memory demand -> MU expectations.",
        "The evidence expressly states that the projected component growth supports AI data centers.",
    ),
    ("D095", "1feb2648"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "Data-center interconnect market growth -> data-center capacity buildout -> server-memory demand -> MU expectations.",
        "The same paragraph identifies AI data centers as the use case, so this is more specific than generic networking growth.",
    ),
    ("D095", "10c8bd12"): decision(
        "IRRELEVANT",
        "NONE",
        "Ciena consolidated revenue growth alone -> no locally established MU exposure.",
        "The paragraph gives company-wide revenue figures but does not locally attribute them to AI-data-center demand.",
    ),
    ("D095", "faebd847"): decision(
        "IRRELEVANT",
        "NONE",
        "Ciena orders alone -> no locally established MU exposure.",
        "The paragraph does not state that these orders arise from AI data centers or another target-specific demand channel.",
    ),
    ("D147", "c212e525"): decision(
        "IRRELEVANT",
        "NONE",
        "Generic hardware-component financing -> no named memory or Micron procurement path.",
        "'Hardware components' is too broad to infer DRAM, NAND, HBM, or a Micron supplier relationship.",
    ),
    ("D167", "be4118ec"): decision(
        "IRRELEVANT",
        "NONE",
        "Oracle consolidated guidance and generic cloud growth -> no locally established memory-demand path.",
        "The paragraph does not identify AI infrastructure, memory-intensive capacity, capex, or a Micron-specific customer relationship.",
    ),
    ("D167", "42e3a36c"): decision(
        "IRRELEVANT",
        "NONE",
        "Oracle stock decline -> no target-specific economic path.",
        "A one-week move in Oracle shares is a security event for Oracle and does not by itself update Micron.",
    ),
    ("D001", "fcf0a3c6"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "SanDisk valuation -> relative valuation/expectations for an established NAND competitor -> MU market expectations.",
        "SanDisk is a close NAND competitor; its valuation is a concrete competitive read-through, not mere sector membership.",
    ),
    ("D001", "d6f1d897"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "SanDisk restructuring and stock re-rating -> NAND-competitor market expectations -> MU relative expectations.",
        "The event concerns a close NAND competitor and can non-trivially update investor views of comparable memory businesses.",
    ),
    ("D025", "b848600a"): decision(
        "IRRELEVANT",
        "NONE",
        "AMD consolidated revenue beat alone -> no locally established MU exposure.",
        "The isolated bullet contains no product mix or data-center attribution.",
    ),
    ("D025", "4c2a0730"): decision(
        "IRRELEVANT",
        "NONE",
        "AMD net-income growth -> no explicit attribution to the locally mentioned data-center business.",
        "The data-center sales figure is adjacent, but the paragraph does not attribute net-income growth to that exposure.",
    ),
    ("D025", "c9b7fc1c"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "AMD revenue growth materially composed of 57% data-center growth -> accelerator/server deployment -> HBM/DRAM demand -> MU expectations.",
        "The same paragraph shows data-center sales at $5.8B, more than half of reported revenue, establishing a material local driver rather than article-wide inheritance.",
    ),
    ("D025", "747ae2af"): decision(
        "IRRELEVANT",
        "NONE",
        "AMD EPS beat alone -> no locally established MU exposure.",
        "The isolated bullet contains no attribution to data-center or memory-complementary demand.",
    ),
    ("D045", "a4e30f12"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "HPE re-rating tied to reversal of memory concerns -> server-OEM memory cost/demand exposure -> MU expectations.",
        "The containing paragraph explicitly says the analyst call was tied to memory-related concerns.",
    ),
    ("D045", "9d8d16da"): decision(
        "IRRELEVANT",
        "NONE",
        "Dell price-target increase -> no memory driver inside the allowed paragraph.",
        "The memory rationale appears in another paragraph and cannot be inherited under the frozen local-context rule.",
    ),
    ("D045", "c09b2bac"): decision(
        "IRRELEVANT",
        "NONE",
        "General HPE/Dell earnings optimism -> no locally stated memory exposure.",
        "The paragraph gives earnings and valuation expectations without attributing them to memory prices, demand, or supply.",
    ),
    ("D097", "efc97082"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "Samsung memory-business re-rating -> close DRAM/NAND competitor expectations -> MU relative expectations.",
        "Samsung is an established memory competitor, making the large target change a specific competitive read-through.",
    ),
    ("D097", "39c49356"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "SanDisk re-rating benchmarked to Micron's upcycle multiples -> direct relative-valuation read-through to MU.",
        "The paragraph explicitly uses Micron as the valuation benchmark, so the path is locally stated.",
    ),
    ("D097", "9e5e9999"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "Kioxia upgrade -> close NAND competitor expectations -> MU competitive and relative-valuation expectations.",
        "Kioxia is an established NAND competitor; the analyst action is a specific memory-market read-through.",
    ),
    ("D097", "e2939aee"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "SK Hynix re-rating -> close DRAM/HBM competitor expectations -> MU competitive and relative-valuation expectations.",
        "SK Hynix is an established DRAM/HBM competitor, making the target move specifically informative for MU.",
    ),
    ("D102", "1c15821d"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "RTX Spark performance with up to 128GB unified memory -> high-capacity AI-PC memory demand -> MU demand expectations.",
        "The containing paragraph explicitly specifies the unusually large unified-memory configuration.",
    ),
    ("D102", "a5253cf8"): decision(
        "IRRELEVANT",
        "NONE",
        "Historical digital-assistant adoption -> no locally stated hardware-memory consequence.",
        "The paragraph concerns software adoption and capability, without a specific memory-demand or Micron path.",
    ),
    ("D102", "13297705"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "Dedicated local-AI PC platform -> high-capacity AI-PC memory category demand -> MU DRAM expectations.",
        "Local AI PCs and high-capacity memory are established complementary products; this is a product-category demand path, not generic AI sentiment.",
    ),
    ("D138", "085d6e51"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "Broadcom AI-sales target -> hyperscaler AI-capex demand -> common AI-supplier demand and pricing -> MU order expectations.",
        "The paragraph explicitly links hyperscaler capex, Broadcom AI sales, and Micron order books.",
    ),
    ("D138", "f1125e8d"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "Oracle hyperscaler/AI-lab-backed RPO -> sustained AI infrastructure buildout -> server-memory demand -> MU orders.",
        "The paragraph explicitly joins the RPO, hyperscaler capex signal, and Micron order-book horizon.",
    ),
    ("D138", "8b7ff3d2"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "NVIDIA margin direction -> common AI-chip pricing-power signal -> MU Cloud Memory margin expectations.",
        "The paragraph directly compares NVIDIA and Micron margins and defines a common pricing-power signal.",
    ),
    ("D293", "a99dba3f"): decision(
        "RELEVANT",
        "INDIRECT_ECONOMIC",
        "Soaring memory prices pressure Apple margins while benefiting Micron -> MU pricing and margin expectations.",
        "The containing paragraph explicitly states the memory-price cause and names Micron as a beneficiary.",
    ),
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def containing_paragraph(text: str, start: int) -> str:
    left = text.rfind("\n", 0, start)
    right = text.find("\n", start)
    return text[left + 1 : len(text) if right < 0 else right].strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    relevant = [item for item in records if item["gold"] == "RELEVANT"]
    dropped = [item for item in records if item["effective_action"] == "DROP"]
    valid = [item for item in records if item["prediction"] is not None]
    correct = [item for item in valid if item["prediction"] == item["gold"]]
    relevant_kept = [item for item in relevant if item["effective_action"] != "DROP"]
    correct_drops = [item for item in dropped if item["gold"] == "IRRELEVANT"]
    direct_fns = [
        item for item in records if item["direct_gold"] and item["effective_action"] == "DROP"
    ]
    return {
        "candidates": len(records),
        "gold_relevant": len(relevant),
        "gold_irrelevant": len(records) - len(relevant),
        "valid_predictions": len(valid),
        "fail_open_predictions": len(records) - len(valid),
        "dropped_candidates": len(dropped),
        "relevant_recall": len(relevant_kept) / len(relevant) if relevant else None,
        "dropped_candidate_precision": len(correct_drops) / len(dropped) if dropped else None,
        "valid_prediction_accuracy": len(correct) / len(valid) if valid else None,
        "direct_relevant_candidates": sum(1 for item in records if item["direct_gold"]),
        "direct_relevant_false_negatives": len(direct_fns),
        "direct_relevant_false_negative_ids": [item["candidate_id"] for item in direct_fns],
    }


def main() -> None:
    documents = load_jsonl(SOURCE_GOLD)
    predictions = load_jsonl(PREDICTIONS)
    mappings = {row["candidate_id"]: row for row in load_jsonl(MAPPINGS)}
    source_report = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    bad_ids = {row["candidate_id"] for row in source_report["bad_cases"]}

    candidates: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    for document in documents:
        candidate_by_id = {row["candidate_id"]: row for row in document["dream_candidates"]}
        for gold in document["candidate_gold"]:
            candidate_id = gold["candidate_id"]
            key = (document["document_id"], candidate_id[-8:])
            if candidate_id in bad_ids:
                candidates[key] = (document, candidate_by_id[candidate_id], gold)

    if set(candidates) != set(DECISIONS):
        missing = sorted(set(candidates) - set(DECISIONS))
        extra = sorted(set(DECISIONS) - set(candidates))
        raise RuntimeError(f"adjudication coverage mismatch: missing={missing}, extra={extra}")

    audit_rows: list[dict[str, Any]] = []
    revised_by_id: dict[str, str] = {}
    changed = Counter()
    for key in sorted(DECISIONS):
        document, candidate, gold = candidates[key]
        ruling = DECISIONS[key]
        candidate_id = candidate["candidate_id"]
        previous = gold["relevance"]
        action = "SECOND_ADJUDICATION_RETAINED"
        if previous != ruling["relevance"]:
            action = "SECOND_ADJUDICATION_CHANGED"
            changed[f"{previous}_TO_{ruling['relevance']}"] += 1
        starts = [int(row["start_char"]) for row in candidate["evidence_locations"]]
        audit_rows.append(
            {
                "document_id": document["document_id"],
                "candidate_id": candidate_id,
                "candidate_statement": candidate["statement"],
                "exact_evidence": [row["text"] for row in candidate["evidence_locations"]],
                "containing_paragraph": containing_paragraph(
                    document["source_message"]["text"], min(starts)
                ),
                "context_scope": CONTEXT_SCOPE,
                "terra_relevance": gold["terra_relevance"],
                "previous_final_relevance": previous,
                "readjudicated_relevance": ruling["relevance"],
                "relevance_type": ruling["relevance_type"],
                "transmission_path": ruling["transmission_path"],
                "rationale": ruling["rationale"],
                "action": action,
                "gate_prediction": next(
                    row["prediction"]
                    for prediction_doc in predictions
                    for row in prediction_doc["predictions"]
                    if row["candidate_id"] == candidate_id
                ),
            }
        )
        revised_by_id[candidate_id] = ruling["relevance"]

    output_documents: list[dict[str, Any]] = []
    for document in documents:
        revised_document = dict(document)
        revised_gold: list[dict[str, Any]] = []
        for gold in document["candidate_gold"]:
            candidate_id = gold["candidate_id"]
            revised = dict(gold)
            if candidate_id in revised_by_id:
                ruling = DECISIONS[(document["document_id"], candidate_id[-8:])]
                revised.update(
                    {
                        "prior_final_relevance": gold["relevance"],
                        "relevance": ruling["relevance"],
                        "second_adjudication_action": next(
                            row["action"]
                            for row in audit_rows
                            if row["candidate_id"] == candidate_id
                        ),
                        "second_adjudication_context_scope": CONTEXT_SCOPE,
                        "second_adjudication_relevance_type": ruling["relevance_type"],
                        "second_adjudication_transmission_path": ruling["transmission_path"],
                        "second_adjudication_rationale": ruling["rationale"],
                    }
                )
            revised_gold.append(revised)
        revised_document["dataset_version"] = "cdecr-mu-relevance-candidates-gold-readjudicated-v2"
        revised_document["candidate_gold"] = revised_gold
        output_documents.append(revised_document)

    OUTPUT_GOLD.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in output_documents
        ),
        encoding="utf-8",
    )

    gold_by_id = {
        gold["candidate_id"]: gold["relevance"]
        for document in output_documents
        for gold in document["candidate_gold"]
    }
    direct_ids = {
        candidate_id
        for candidate_id, row in mappings.items()
        if "DIRECT" in row["mention_relevance_types"]
    }
    direct_ids.update(
        row["candidate_id"] for row in audit_rows if row["relevance_type"] == "DIRECT"
    )
    records: list[dict[str, Any]] = []
    for document in predictions:
        for prediction in document["predictions"]:
            candidate_id = prediction["candidate_id"]
            records.append(
                {
                    **prediction,
                    "document_id": document["document_id"],
                    "selection_category": document["selection_category"],
                    "gold": gold_by_id[candidate_id],
                    "direct_gold": candidate_id in direct_ids,
                }
            )
    overall = metrics(records)
    slices = {
        category: metrics([row for row in records if row["selection_category"] == category])
        for category in sorted({row["selection_category"] for row in records})
    }

    OUTPUT_AUDIT.write_text(
        json.dumps(
            {
                "adjudication_version": "candidate-local-context-v2",
                "source_gold": str(SOURCE_GOLD.relative_to(ROOT)).replace("\\", "/"),
                "source_predictions": str(PREDICTIONS.relative_to(ROOT)).replace("\\", "/"),
                "context_contract": CONTEXT_SCOPE,
                "rules": [
                    "Direct target facts remain Direct even when stated in a comparison or list.",
                    "Stable knowledge may establish a close competitor or complementary-product exposure.",
                    "General company financial or security events require the containing paragraph to establish a material target-exposure driver.",
                    "No other paragraph or article-wide theme may be inherited.",
                    "Gate predictions were not used as adjudication evidence.",
                ],
                "items": audit_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    label_counts = Counter(gold_by_id.values())
    summary = {
        "dataset_version": "cdecr-mu-relevance-candidates-gold-readjudicated-v2",
        "documents": len(output_documents),
        "candidates": len(gold_by_id),
        "disputes_reviewed": len(audit_rows),
        "changed_labels": sum(changed.values()),
        "change_directions": dict(sorted(changed.items())),
        "labels": dict(sorted(label_counts.items())),
        "context_contract": CONTEXT_SCOPE,
        "metrics": {"overall": overall, "slices": slices},
        "source_gold_sha256": sha256(SOURCE_GOLD),
        "output_gold_sha256": sha256(OUTPUT_GOLD),
        "audit_sha256": sha256(OUTPUT_AUDIT),
        "candidate_ids_preserved_in_order": [
            gold["candidate_id"] for document in documents for gold in document["candidate_gold"]
        ]
        == [
            gold["candidate_id"]
            for document in output_documents
            for gold in document["candidate_gold"]
        ],
    }
    OUTPUT_SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
