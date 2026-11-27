"""Human-selected business controls; IDs are evaluation evidence, never live rules."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent / "binding_extra_regex_20261127"


def main():
    data = json.loads((BASE / "cases.json").read_text(encoding="utf-8"))
    result = {(r["ticker"], r["id"]): r["extra_allowed"] == "True" for r in
              csv.DictReader((ROOT / "all-history-results.csv").open(encoding="utf-8-sig"))}
    controls = {(x["ticker"],x["id"]):
        (x["expected_allowed"] == "True",x["reason"]) for x in
        json.loads((ROOT / "expected-controls.json").read_text(encoding="utf-8"))}
    rows = []
    for item in data["cases"]:
        key = item["ticker"], item["id"]
        if key not in controls:
            continue
        expected, reason = controls[key]
        rows.append({"ticker":key[0], "id":key[1], "title":item["title"],
            "source_id":item["source_id"],"url":item["url"],"expected_allowed":expected,
            "actual_allowed":result[key],"passed":result[key]==expected,"reason":reason,
            "body_sha256":hashlib.sha256(item["body"].encode()).hexdigest()})
    with (ROOT / "business-controls.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer=csv.DictWriter(file,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    failed=[r for r in rows if not r["passed"]]
    print(json.dumps({"controls":len(rows),"passed":len(rows)-len(failed),"failed":
        [{k:r[k] for k in ("ticker","id","title")} for r in failed]},ensure_ascii=False))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
