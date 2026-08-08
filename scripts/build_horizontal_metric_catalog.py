"""Generate the checked-in horizontal metric id catalog from its governing plan."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dev_plan" / "workflow_v2" / "d1_horizontal_indicators_collection.md"
TARGET = (
    ROOT
    / "src"
    / "doxagent"
    / "horizontal_collection"
    / "generated_metric_catalog.py"
)


def main() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("# 六、C1 个股基本面固定清单")
    end = text.index("# 十、采集结果 Manifest")
    ids = sorted(
        set(
            re.findall(
                r"\b(?:fin|op|macro|ind|market)_[a-z0-9_]+\b",
                text[start:end],
            )
        )
    )
    if len(ids) < 300:
        raise RuntimeError(f"Expected at least 300 metric ids, found {len(ids)}.")
    body = "\n".join(f'    "{metric_id}",' for metric_id in ids)
    TARGET.write_text(
        '"""Generated from dev_plan/workflow_v2/d1_horizontal_indicators_collection.md.\n\n'
        "Do not edit manually; run scripts/build_horizontal_metric_catalog.py.\n"
        '"""\n\n'
        "GENERATED_METRIC_IDS = (\n"
        f"{body}\n"
        ")\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"generated {len(ids)} metric ids -> {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
