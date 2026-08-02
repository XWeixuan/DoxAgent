from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "cdecr_fragmentation_metrics.py"
    spec = importlib.util.spec_from_file_location("cdecr_fragmentation_metrics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fragmentation_metrics_keep_precision_next_to_fragmentation() -> None:
    result = _module().evaluate(
        [
            {
                "source_occurrence_id": "o1",
                "gold_atomic_id": "ga1",
                "gold_package_id": "gp1",
                "predicted_atomic_id": "a1",
                "predicted_package_id": "p1",
            },
            {
                "source_occurrence_id": "o2",
                "gold_atomic_id": "ga1",
                "gold_package_id": "gp1",
                "predicted_atomic_id": "a2",
                "predicted_package_id": "p1",
            },
            {
                "source_occurrence_id": "o3",
                "gold_atomic_id": "ga2",
                "gold_package_id": "gp1",
                "predicted_atomic_id": "a2",
                "predicted_package_id": "p2",
            },
        ]
    )

    assert result["atomic"]["precision"] == 0.0
    assert result["atomic"]["pair_fragmentation_rate"] == 1.0
    assert result["atomic"]["fragmented_cluster_rate"] == 1.0
    assert result["atomic"]["component_profiles"][0]["component_distribution"] == "1+1"
    assert result["package_end_to_end"]["recall"] == 1 / 3
    assert result["package_conditional"]["recall"] == 0.0
