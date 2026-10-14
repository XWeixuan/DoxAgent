"""One foreground job for the authorized first dataset and baseline."""
from pathlib import Path
import json
import time

root = Path(__file__).resolve().parent
dataset = root / "datasets/w3_history_20261014"
marker = dataset / "preparation/web_collection_complete.json"
last = None
while not marker.exists():
    count = len(list((dataset / "inputs").glob("*/evidence/web_search.jsonl")))
    if count != last:
        print(json.dumps({"stage": "waiting_for_collector_handoff", "web_files": count, "cases": 73}), flush=True)
        last = count
    time.sleep(15)

from doxagent.w3_backtest.cli import build_dataset, execute_run
manifest = build_dataset(dataset)
print(json.dumps({"stage": "dataset_built", "summary": manifest["preparation_summary"]}, ensure_ascii=False), flush=True)
result = execute_run(dataset, root / "runs/baseline-20261014", concurrency=8)
print(json.dumps({"stage": "baseline_finished", "summary": result}, ensure_ascii=False), flush=True)
