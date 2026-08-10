from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

DATASET_VERSION = "cdecr-mu-relevance-30-v1-20260810"
TARGET = {
    "market": "US",
    "ticker": "MU",
    "company": "Micron Technology",
}

CATEGORY_LOW = "very_low_or_none"
CATEGORY_HIGH = "mostly_high_or_very_high"
CATEGORY_MIXED = "mixed_relevant_and_irrelevant"
CATEGORY_INDIRECT = "hard_indirect_relevance"


# The selected documents were reviewed against the complete frozen article text and the
# production EventMention payloads. This specification is intentionally separate from the
# source payloads so the exported mentions remain byte-for-byte equivalent JSON objects.
SELECTION: dict[int, dict[str, Any]] = {
    17: {
        "category": CATEGORY_LOW,
        "review_summary": "印度 Kamala 水电项目；MU 表示 million units，不是 Micron ticker。",
        "why_selected": "单位缩写误召回，且包含政策、基建、融资、产能等多种强事件。",
    },
    19: {
        "category": CATEGORY_LOW,
        "review_summary": "Masters' Union 的 MU Ventures 基金；MU 是机构/基金名称缩写。",
        "why_selected": "公司简称碰撞，事件覆盖基金设立、投资条款与创业支持。",
    },
    50: {
        "category": CATEGORY_LOW,
        "review_summary": "Alex Murdaugh 刑事重审与民事诉讼，与 Micron 无经济暴露关系。",
        "why_selected": "人物姓名字符串噪声，提供纯法律事件负例。",
    },
    67: {
        "category": CATEGORY_LOW,
        "review_summary": "University of Missouri/Mizzou 棒垒球报道，且正文存在字符编码污染。",
        "why_selected": "MU 校名缩写碰撞，并覆盖脏文本下的体育、人事与合同事件。",
    },
    89: {
        "category": CATEGORY_LOW,
        "review_summary": "Mumbai University 本科录取名单；MU 是大学缩写。",
        "why_selected": "教育机构缩写碰撞，提供日程/录取流程类纯负例。",
    },
    10: {
        "category": CATEGORY_HIGH,
        "review_summary": "Erste 下调 Micron 评级，并披露 Micron CapEx 计划和股价表现。",
        "why_selected": "分析师行动、资本开支、证券表现均直接涉及目标公司。",
    },
    62: {
        "category": CATEGORY_HIGH,
        "review_summary": "Micron 在 Virginia 启动 1-alpha DRAM 制造并投资扩建 Manassas fab。",
        "why_selected": "生产启动与制造投资的高纯度公司特异性事件。",
    },
    232: {
        "category": CATEGORY_HIGH,
        "review_summary": "Micron Q3 财务、业务分部、Q4 指引、供需、分红与盘后股价。",
        "why_selected": "高密度、多字段、几乎全为直接相关的财报型文本。",
    },
    292: {
        "category": CATEGORY_HIGH,
        "review_summary": "Micron 等被控价格合谋，并包含 Micron HBM 合同、产能与客户满足率。",
        "why_selected": "诉讼、历史监管、合同与产能直接相关，夹有少量行业传导事实。",
        "indirect_path": (
            "Hyperscaler 资本开支和竞争者 HBM 供给约束会改变 Micron 的需求、定价与市场份额。"
        ),
    },
    298: {
        "category": CATEGORY_HIGH,
        "review_summary": "Micron 与 General Motors 签长期供货及技术协作协议。",
        "why_selected": "明确客户关系、产品供给、技术验证与本地制造投资。",
    },
    21: {
        "category": CATEGORY_MIXED,
        "review_summary": "Micron 估值与盈利预期混合 SkyWater 并购、Photronics 财务等独立事件。",
        "why_selected": "同一推荐文中同时存在目标公司事件、间接技术信号和无关公司事件。",
        "indirect_path": "AI 模型效率变化可能改变推理成本与用量，进而影响内存需求。",
    },
    27: {
        "category": CATEGORY_MIXED,
        "review_summary": "Micron SSD 出货和股价事件混合 Iran、Disney、Spirit、Intel 等新闻。",
        "why_selected": "典型 Morning Squawk 杂糅文本，相关与无关事件边界清晰。",
    },
    72: {
        "category": CATEGORY_MIXED,
        "review_summary": "Micron 指引/评级/订单簿与 AMD 财务、估值、OpenAI GPU 协议并列。",
        "why_selected": "目标公司直接事件与另一芯片公司的独立及互补需求事件混合。",
        "indirect_path": (
            "AMD 数据中心 GPU 部署会形成互补的 HBM/DRAM 需求，从而影响 Micron 需求预期。"
        ),
    },
    95: {
        "category": CATEGORY_MIXED,
        "review_summary": "Micron 财报预期与股价混合 Ciena 财务、订单和光网络供需。",
        "why_selected": "目标公司事件、AI 数据中心间接信号和 Ciena 独立证券事件共存。",
        "indirect_path": "光互连订单与数据中心资本开支反映 AI 基建扩张，可能传导至服务器内存需求。",
    },
    111: {
        "category": CATEGORY_MIXED,
        "review_summary": "Netflix 广告、订阅、财务与估值长文中仅夹有 Micron 股价涨幅。",
        "why_selected": "一个直接目标证券事件被大量跨行业事件包围。",
    },
    147: {
        "category": CATEGORY_MIXED,
        "review_summary": "Micron 盘前下跌混合餐饮、服饰、宠物零售、黄金与融资事件。",
        "why_selected": "短篇行情清单包含单一直接相关事件和多行业无关事件。",
    },
    165: {
        "category": CATEGORY_MIXED,
        "review_summary": "Micron 目标价与股价混合 SpaceX、油价、航空、媒体并购等事件。",
        "why_selected": "多资产盘前清单，直接相关 mention 占少数。",
    },
    167: {
        "category": CATEGORY_MIXED,
        "review_summary": "Micron 毛利率、CapEx 与股价混合 Oracle 云业务、融资及分红。",
        "why_selected": "两家公司基本面事件交错，适合测试比较型文章的事件级过滤。",
    },
    220: {
        "category": CATEGORY_MIXED,
        "review_summary": "Micron 财报日信息混合芯片抛售、Iran、Fed、Cerebras 与 PGA。",
        "why_selected": "宏观、政治、体育和公司事件同时出现的高异质 newsletter。",
    },
    223: {
        "category": CATEGORY_MIXED,
        "review_summary": "Micron 财报预期与证券表现混合住房立法、销售数据和房企财报。",
        "why_selected": "目标公司财报事件与完全不同的房地产主题并置。",
    },
    1: {
        "category": CATEGORY_INDIRECT,
        "review_summary": (
            "Google TurboQuant 降低内存用量，但也可能通过 Jevons 效应扩大 AI/内存需求。"
        ),
        "why_selected": "技术替代与需求弹性的方向相反，属于高难间接相关。",
        "indirect_path": (
            "内存压缩改变单位推理内存需求、内存价格与 AI 用量，进而改变 Micron 产品需求。"
        ),
    },
    25: {
        "category": CATEGORY_INDIRECT,
        "review_summary": "AMD 数据中心增长、Helios 出货及 OpenAI/Meta 部署映射到互补内存需求。",
        "why_selected": "事件主体是 AMD，但 GPU/机架部署对 HBM/DRAM 有具体需求传导。",
        "indirect_path": "AI 加速器和机架部署量增加会提高配套 HBM/DRAM 消耗，影响 Micron 需求。",
    },
    45: {
        "category": CATEGORY_INDIRECT,
        "review_summary": "HPE/Dell 评级上调被归因于内存成本压力缓解，反向映射内存价格周期。",
        "why_selected": "下游客户利润与上游 Micron 定价/需求之间存在方向可能相反的传导。",
        "indirect_path": (
            "服务器厂商内存成本和毛利变化反映内存定价与需求承受力，"
            "影响 Micron 价格/销量判断。"
        ),
    },
    61: {
        "category": CATEGORY_INDIRECT,
        "review_summary": "Kioxia 的上市准备、产品、产能、财务与供货安排构成 NAND 竞争信号。",
        "why_selected": "竞争者事件是否足以更新 Micron 份额、价格或投资判断，需要逐事件判断。",
        "indirect_path": (
            "Kioxia/SK Hynix 的技术、产能、成本与融资能力会改变 NAND/HBM 竞争、"
            "供给和 Micron 市占率。"
        ),
    },
    97: {
        "category": CATEGORY_INDIRECT,
        "review_summary": "Goldman 对 DRAM/NAND/HBM 供需、价格与市场规模的预测未直接点名 Micron。",
        "why_selected": "行业事件必须通过 Micron 的产品暴露映射到收入、价格和利润率。",
        "indirect_path": "DRAM/NAND/HBM 的短缺、价格和容量直接作用于 Micron 的销量、ASP 与毛利率。",
    },
    102: {
        "category": CATEGORY_INDIRECT,
        "review_summary": "Nvidia/Microsoft RTX Spark 推动本地 AI PC，同时可能争夺紧缺内存产能。",
        "why_selected": "终端新品既可能创造客户需求，也可能因价格抑制采用，方向并不简单。",
        "indirect_path": "高内存配置 AI PC 的采用量会改变客户端内存需求和共享制造资源的紧张程度。",
    },
    138: {
        "category": CATEGORY_INDIRECT,
        "review_summary": "Hyperscaler CapEx、Oracle RPO 与 AI 芯片利润率被用作 AI 基建需求信号。",
        "why_selected": "需区分可传导的客户预算信号与仅为 ETF/宏观背景的信息。",
        "indirect_path": (
            "Hyperscaler 预算和 AI 基建订单决定服务器部署，"
            "进而影响 Micron HBM/数据中心内存订单。"
        ),
    },
    180: {
        "category": CATEGORY_INDIRECT,
        "review_summary": "SK Hynix 向客户送样 HBM4E，并披露 Nvidia 主供关系和财务表现。",
        "why_selected": "直接竞争者的产品资格和核心客户份额会影响 Micron 竞争地位。",
        "indirect_path": (
            "SK Hynix 的 HBM 产品、Nvidia 供应关系和盈利能力会改变 "
            "Micron 的客户份额与定价空间。"
        ),
    },
    190: {
        "category": CATEGORY_INDIRECT,
        "review_summary": "SK Hynix 扩产、市场份额及终端销量预测同时影响 Micron 的供需与竞争。",
        "why_selected": "竞争供给增加与行业需求增长方向相反，具有真实判定难度。",
        "indirect_path": (
            "竞争者扩产/份额与 PC、手机、HBM 需求共同改变 Micron 的供需平衡、"
            "ASP 和市场份额。"
        ),
    },
    293: {
        "category": CATEGORY_INDIRECT,
        "review_summary": "Apple 因内存短缺提价，可能带来供应商定价收益，也可能造成终端需求破坏。",
        "why_selected": "明确客户成本和需求弹性路径，影响方向具有不确定性。",
        "indirect_path": "Apple 的内存成本、提价和终端需求会反馈到其采购量及 Micron 的价格/销量。",
    },
}


DIRECT: dict[int, set[int]] = {
    10: {0, 1, 2, 3},
    62: {0, 1},
    232: set(range(17)),
    292: {0, 1, 3, 4, 5, 6, 8},
    298: set(range(7)),
    21: {1, 2, 3},
    27: {5, 6},
    72: {0, 1, 4, 5, 6, 7, 10},
    95: {4, 7, 13},
    111: {4},
    147: {0},
    165: {15, 19},
    167: {1, 6, 7, 12},
    220: {9},
    223: {1, 2, 3, 12},
    1: {0, 1, 2, 5, 8, 9, 13},
    25: {10},
    45: {5},
    61: {15},
    102: {1},
    138: {0, 6},
    190: {5, 8, 14},
}


INDIRECT: dict[int, set[int]] = {
    292: {2, 7},
    21: {9},
    72: {8, 11},
    95: {3, 5, 6, 8, 9, 11, 12, 15},
    1: {3, 4, 6, 7, 14},
    25: {3, 4, 5, 6, 7, 11, 12, 15},
    45: {0, 1},
    61: {0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 18, 19},
    97: set(range(4, 14)),
    102: {0, 2, 3, 4, 5, 6},
    138: {1, 2, 3},
    180: {0, 1, 2, 3},
    190: {0, 1, 2, 3, 4, 6, 7, 9, 10, 11, 12, 13},
    293: {0, 1, 2},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the fixed MU relevance-filter dataset.")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(r"D:\DoxAgent_CDECR_Stress_20260804_MU300_R1"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("eval/cdecr_relevance_filter/mu_relevance_30_v1"),
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_dump(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=indent)


def load_snapshot(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_mentions(database_path: Path) -> dict[str, list[dict[str, Any]]]:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    query = """
        SELECT dpr.message_id, drm.ordinal, em.mention_id, em.payload_json
        FROM document_processing_runs AS dpr
        JOIN document_run_mentions AS drm ON drm.run_id = dpr.run_id
        JOIN event_mentions AS em ON em.mention_id = drm.mention_id
        WHERE dpr.status = 'SUCCEEDED'
        ORDER BY dpr.message_id, drm.ordinal
    """
    by_message: dict[str, list[dict[str, Any]]] = {}
    try:
        for message_id, ordinal, mention_id, payload_json in connection.execute(query):
            payload = json.loads(payload_json)
            if payload.get("mention_id") != mention_id:
                raise ValueError(f"Mention ID mismatch for {mention_id}")
            by_message.setdefault(message_id, []).append(
                {"ordinal": ordinal, "mention_id": mention_id, "payload": payload}
            )
    finally:
        connection.close()
    return by_message


def annotation(document_index: int, ordinal: int, spec: dict[str, Any]) -> dict[str, Any]:
    if ordinal in DIRECT.get(document_index, set()):
        return {
            "is_relevant": True,
            "relevance_type": "DIRECT",
            "confidence": "HIGH",
            "rationale": "事件主体、行为、结果或证券表现直接涉及 Micron/MU。",
        }
    if ordinal in INDIRECT.get(document_index, set()):
        return {
            "is_relevant": True,
            "relevance_type": "INDIRECT_ECONOMIC",
            "confidence": "MEDIUM",
            "rationale": spec["indirect_path"],
        }
    return {
        "is_relevant": False,
        "relevance_type": "NONE",
        "confidence": "HIGH",
        "rationale": "未找到对 Micron/MU 特异、合理且非微不足道的经济传导路径。",
    }


def validate_evidence(source_message: dict[str, Any], mention: dict[str, Any]) -> None:
    payload = mention["payload"]
    for span in payload.get("evidence_spans", []):
        field = span["field"]
        source_value = source_message.get(field)
        if not isinstance(source_value, str):
            raise ValueError(f"Missing source field {field} for {mention['mention_id']}")
        start = span["start_char"]
        end = span["end_char"]
        if source_value[start:end] != span["text"]:
            raise ValueError(
                f"Evidence mismatch for {mention['mention_id']} at {field}[{start}:{end}]"
            )


def build_dataset(
    source_root: Path, output_dir: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    snapshot_path = source_root / "mu_300_snapshot.jsonl"
    manifest_path = source_root / "mu_300_manifest.json"
    database_path = source_root / "pre_n13_frozen.sqlite3"
    snapshot = load_snapshot(snapshot_path)
    if len(snapshot) != 300:
        raise ValueError(f"Expected 300 snapshot rows, found {len(snapshot)}")
    mentions_by_message = load_mentions(database_path)

    category_counts = Counter(spec["category"] for spec in SELECTION.values())
    expected_categories = {
        CATEGORY_LOW: 5,
        CATEGORY_HIGH: 5,
        CATEGORY_MIXED: 10,
        CATEGORY_INDIRECT: 10,
    }
    if dict(category_counts) != expected_categories:
        raise ValueError(f"Category split mismatch: {dict(category_counts)}")
    if len(SELECTION) != 30:
        raise ValueError(f"Expected 30 selected documents, found {len(SELECTION)}")

    records: list[dict[str, Any]] = []
    document_summaries: list[dict[str, Any]] = []
    selected_fingerprints: set[str] = set()
    selected_source_rows: set[str] = set()
    selected_titles: set[str] = set()
    total_label_counts: Counter[str] = Counter()
    all_event_families: set[str] = set()

    for document_index, spec in SELECTION.items():
        row = snapshot[document_index - 1]
        source_message = row["message"]
        if row["source_row_id"] in selected_source_rows:
            raise ValueError(f"Duplicate source row {row['source_row_id']}")
        if row["document_fingerprint"] in selected_fingerprints:
            raise ValueError(f"Duplicate fingerprint {row['document_fingerprint']}")
        if source_message["title"] in selected_titles:
            raise ValueError(f"Duplicate title {source_message['title']}")
        selected_source_rows.add(row["source_row_id"])
        selected_fingerprints.add(row["document_fingerprint"])
        selected_titles.add(source_message["title"])

        raw_mentions = mentions_by_message.get(source_message["message_id"], [])
        annotated_mentions: list[dict[str, Any]] = []
        label_counts: Counter[str] = Counter()
        ordinals = {mention["ordinal"] for mention in raw_mentions}
        configured = DIRECT.get(document_index, set()) | INDIRECT.get(document_index, set())
        if not configured.issubset(ordinals):
            missing = sorted(configured - ordinals)
            raise ValueError(f"D{document_index:03d} configured ordinals missing: {missing}")

        for mention in raw_mentions:
            validate_evidence(source_message, mention)
            gold = annotation(document_index, mention["ordinal"], spec)
            label_counts[gold["relevance_type"]] += 1
            total_label_counts[gold["relevance_type"]] += 1
            annotated_mentions.append({**mention, "relevance_gold": gold})

        event_families = sorted({m["payload"]["event_family"] for m in raw_mentions})
        all_event_families.update(event_families)
        relevant_count = label_counts["DIRECT"] + label_counts["INDIRECT_ECONOMIC"]
        irrelevant_count = label_counts["NONE"]
        category = spec["category"]
        if category == CATEGORY_LOW and relevant_count != 0:
            raise ValueError(f"D{document_index:03d} low-relevance sample has relevant mentions")
        if category == CATEGORY_HIGH and relevant_count / len(raw_mentions) < 0.8:
            raise ValueError(f"D{document_index:03d} high-relevance sample is below 80%")
        if category == CATEGORY_MIXED and not (relevant_count > 0 and irrelevant_count > 0):
            raise ValueError(f"D{document_index:03d} mixed sample lacks both sides")
        if category == CATEGORY_INDIRECT and label_counts["INDIRECT_ECONOMIC"] == 0:
            raise ValueError(f"D{document_index:03d} indirect sample lacks indirect mentions")
        document_id = f"D{document_index:03d}"
        records.append(
            {
                "dataset_version": DATASET_VERSION,
                "document_id": document_id,
                "document_index_in_mu300": document_index,
                "source_row_id": row["source_row_id"],
                "document_fingerprint": row["document_fingerprint"],
                "target": TARGET,
                "selection_category": spec["category"],
                "article_review": {
                    "review_status": "MANUALLY_REVIEWED",
                    "review_summary": spec["review_summary"],
                    "why_selected": spec["why_selected"],
                    "indirect_path": spec.get("indirect_path"),
                    "event_families": event_families,
                },
                "source_message": source_message,
                "mentions": annotated_mentions,
            }
        )
        document_summaries.append(
            {
                "document_id": document_id,
                "document_index_in_mu300": document_index,
                "source_row_id": row["source_row_id"],
                "document_fingerprint": row["document_fingerprint"],
                "selection_category": spec["category"],
                "title": source_message["title"],
                "source_name": source_message["source_name"],
                "published_at": source_message["published_at"],
                "text_chars": len(source_message["text"]),
                "mention_count": len(raw_mentions),
                "mention_label_counts": dict(sorted(label_counts.items())),
                "event_families": event_families,
                "review_summary": spec["review_summary"],
                "why_selected": spec["why_selected"],
                "indirect_path": spec.get("indirect_path"),
            }
        )

    manifest = {
        "dataset_version": DATASET_VERSION,
        "created_at": "2026-08-10",
        "target": TARGET,
        "scope": (
            "Frozen source articles plus production EventMention results only; "
            "no Atomic/EventPackage normalized results."
        ),
        "relevance_rule": {
            "relevant_if": ["DIRECT", "INDIRECT_ECONOMIC"],
            "unit": "event mention and its evidence sentence/paragraph",
            "article_level_label_is_not_gold": True,
        },
        "provenance": {
            "source_root": str(source_root.resolve()),
            "snapshot": {
                "path": str(snapshot_path.resolve()),
                "sha256": sha256_file(snapshot_path),
                "row_count": len(snapshot),
            },
            "source_manifest": {
                "path": str(manifest_path.resolve()),
                "sha256": sha256_file(manifest_path),
            },
            "mentions_database": {
                "path": str(database_path.resolve()),
                "sha256": sha256_file(database_path),
                "read_mode": "SQLite read-only",
                "tables": [
                    "document_processing_runs",
                    "document_run_mentions",
                    "event_mentions",
                ],
            },
        },
        "counts": {
            "documents": len(records),
            "categories": expected_categories,
            "mentions": sum(item["mention_count"] for item in document_summaries),
            "mention_labels": dict(sorted(total_label_counts.items())),
            "unique_source_rows": len(selected_source_rows),
            "unique_document_fingerprints": len(selected_fingerprints),
            "unique_titles": len(selected_titles),
            "event_family_count": len(all_event_families),
            "event_families": sorted(all_event_families),
        },
        "quality_gates": {
            "category_split_5_5_10_10": "PASS",
            "low_group_zero_relevant_per_document": "PASS",
            "high_group_at_least_80pct_relevant_per_document": "PASS",
            "mixed_group_has_relevant_and_irrelevant_per_document": "PASS",
            "indirect_group_has_indirect_relevance_per_document": "PASS",
            "unique_source_row_fingerprint_and_title": "PASS",
            "mention_evidence_exact_match": "PASS",
        },
        "documents": document_summaries,
    }
    return records, manifest


def write_outputs(
    records: list[dict[str, Any]], manifest: dict[str, Any], output_dir: Path
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = output_dir / "cdecr_mu_relevance_30.jsonl"
    manifest_path = output_dir / "cdecr_mu_relevance_30_manifest.json"
    report_path = output_dir / "CDECR_MU_RELEVANCE_30_REVIEW.md"

    with dataset_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json_dump(record) + "\n")

    manifest_path.write_text(json_dump(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")

    category_titles = {
        CATEGORY_LOW: "极低或无相关度（5）",
        CATEGORY_HIGH: "大部分高或极高相关度（5）",
        CATEGORY_MIXED: "相关与不相关事件杂糅（10）",
        CATEGORY_INDIRECT: "难判定的间接相关（10）",
    }
    lines = [
        "# CDECR MU 相关性前置滤除 30 篇测试集人工审核报告",
        "",
        f"> 数据集版本：`{DATASET_VERSION}`  ",
        "> 目标：`US / MU / Micron Technology`  ",
        (
            "> 口径：以 event mention 及其 evidence 语句/段落为判断单位；"
            "文章类别仅描述样本构成，不代替 mention 级 Gold。  "
        ),
        "> 数据边界：只包含冻结新闻和生产 EventMention；不包含 Atomic、Package 或其他归一化结果。",
        "",
        "## 验收汇总",
        "",
        (
            f"- 文档：{manifest['counts']['documents']}；唯一 source_row/fingerprint："
            f"{manifest['counts']['unique_source_rows']}/"
            f"{manifest['counts']['unique_document_fingerprints']}"
        ),
        (
            f"- Mentions：{manifest['counts']['mentions']}；标签分布："
            f"`{json_dump(manifest['counts']['mention_labels'])}`"
        ),
        "- 类别：`5 / 5 / 10 / 10`，均逐篇阅读完整正文并核对生产 mention 与 evidence。",
        "- 原始 mention payload 保持独立，人工标签仅位于同级 `relevance_gold` 字段。",
        "",
    ]
    for category in (CATEGORY_LOW, CATEGORY_HIGH, CATEGORY_MIXED, CATEGORY_INDIRECT):
        lines.extend(
            [
                f"## {category_titles[category]}",
                "",
                "| 文档 | 标题 | Mention（Direct / Indirect / None） | 选择理由 |",
                "| --- | --- | ---: | --- |",
            ]
        )
        for item in manifest["documents"]:
            if item["selection_category"] != category:
                continue
            counts = item["mention_label_counts"]
            distribution = (
                f"{counts.get('DIRECT', 0)} / {counts.get('INDIRECT_ECONOMIC', 0)} / "
                f"{counts.get('NONE', 0)}"
            )
            title = item["title"].replace("|", "\\|")
            reason = item["why_selected"].replace("|", "\\|")
            lines.append(f"| {item['document_id']} | {title} | {distribution} | {reason} |")
        lines.append("")

    lines.extend(
        [
            "## 使用说明",
            "",
            (
                "- `cdecr_mu_relevance_30.jsonl` 每行一篇文章，含原始 `source_message`、"
                "按生产 ordinal 排序的 `mentions` 及 mention 级 `relevance_gold`。"
            ),
            "- `is_relevant=true` 当且仅当 `relevance_type` 为 `DIRECT` 或 `INDIRECT_ECONOMIC`。",
            (
                "- `INDIRECT_ECONOMIC` 的 `rationale` 给出目标公司特异性的传导路径；"
                "不能仅凭同属行业或出现在列表中判为相关。"
            ),
            (
                "- 若未来节点在 Dreamer 前运行，应把这里的 mention/evidence Gold 映射回承载语句；"
                "若在 Judge 后运行，可直接按 mention_id 评估。"
            ),
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    output_hashes = {
        "dataset": sha256_file(dataset_path),
        "manifest": sha256_file(manifest_path),
        "review_report": sha256_file(report_path),
    }
    (output_dir / "checksums.json").write_text(
        json_dump(output_hashes, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def main() -> None:
    args = parse_args()
    records, manifest = build_dataset(args.source_root, args.output_dir)
    write_outputs(records, manifest, args.output_dir)
    print(
        json_dump(
            {
                "dataset_version": DATASET_VERSION,
                "output_dir": str(args.output_dir.resolve()),
                "counts": manifest["counts"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
