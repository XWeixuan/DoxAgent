"""Curated decision-only datasets; preparation records never enter Agent scope."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from .dataset import input_fingerprint, read_json, write_json

SCHEMA = "w3-backtest.dataset.v2"
CONSTRUCTION_MODEL = "gpt-6.1-terra"
CONSTRUCTION_EFFORT = "medium"
CATALOG = Path.home() / "Downloads/W3_测试集59案例_最终GOLD与Tag汇总.md"
MANUAL = Path("dev_plan/workflow_v2/W3测试集构造规范手册.md")


def parse_catalog(path=CATALOG, previous=Path("eval/w3_backtest/datasets/w3_history_20261014")):
    text = Path(path).read_text(encoding="utf-8-sig")
    old = {p.parent.name: read_json(p) for p in (Path(previous) / "inputs").glob("*/case.json")}
    ids = dict(re.findall(r"\|\s*(ADD-[A-Z0-9-]+)\s*\|[^\n]*?`(case_[a-f0-9]+)`", text))
    rows = []
    for line in text.splitlines():
        cells = [x.strip() for x in line.split("|")[1:-1]]
        if (
            len(cells) != 5
            or not cells[0].isdigit()
            or cells[3] not in {"LONG", "SHORT", "NO_TRADE", "移除"}
        ):
            continue
        ordinal, key, title, target, tag = cells
        prior = old.get(key, {})
        ticker = key.split("-")[1] if key.startswith(("ADD-", "EXTRA-")) else key.split("-")[0]
        rows.append(
            dict(
                ordinal=int(ordinal),
                case_key=key,
                ticker=ticker,
                title=title,
                included=target != "移除",
                target=target,
                tags=[] if tag == "—" else [x.strip() for x in tag.split("、")],
                original_case_id=ids.get(key, prior.get("original_case_id")),
            )
        )
    if len(rows) != 59 or len({r["case_key"] for r in rows}) != 59:
        raise ValueError(
            "catalog must contain the 59 unique case rows, excluding the ID mapping table"
        )
    return rows


def remote_read(body, host="doxagent-sg"):
    prefix = (
        Path(__file__)
        .with_name("remote_export.py")
        .read_text(encoding="utf-8")
        .split('if __name__ == "__main__":')[0]
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, "sudo -n python3 -"],
        input=prefix + "\n" + body,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=240,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def snapshot_production_prompts(destination, host="doxagent-sg"):
    rows = remote_read(
        """
rt=ro(ROOT/'runtime/runtime.sqlite3')
a=load(rt.execute("select payload from runtime_values "
 "where namespace='execution' and key='active'").fetchone()[0])
b=load(rt.execute('select payload from runtime_snapshots where id=?',(a,)).fetchone()[0])
assets={k:b['assets'][k] for k in ['w3/AGENTS.md','w3/skills/skill.md']}
emit('prompts',bundle_id=a,assets=assets,contracts=b['contracts'])
""",
        host,
    )
    value = rows[0]
    destination = Path(destination)
    for key, text in value["assets"].items():
        target = destination / key.removeprefix("w3/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    write_json(
        destination / "contract.json",
        dict(w3_contract_version=value["contracts"]["w3_contract_version"]),
    )
    shutil.copyfile("prompts/w3_backtest/backtest.md", destination / "backtest.md")
    write_json(
        destination / "provenance.json",
        dict(
            source="production_active",
            bundle_id=value["bundle_id"],
            hashes={
                n: hashlib.sha256((destination / n).read_bytes()).hexdigest()
                for n in ("AGENTS.md", "skills/skill.md", "contract.json", "backtest.md")
            },
        ),
    )
    return value["bundle_id"]


def acquire_cases(identities, host="doxagent-sg"):
    body = (
        "IDS="
        + repr(identities)
        + "\n"
        + """
rt=ro(ROOT/'runtime/runtime.sqlite3');research=ro(ROOT/'research/research.sqlite3')
query='select case_id,ticker,payload_json from runtime_v2_cases where case_id in ('
for row in rt.execute(query+','.join('?'*len(IDS))+')',IDS):
 c=load(row['payload_json']);c['ticker']=row['ticker'];texts={}
 pins=c['version_pin']
 for name,run in [('document1',pins.get('document1_run_id')),
                  ('document2',pins.get('document2_run_id'))]:
  row2=research.execute("select payload_json from codex_runtime_records "
   "where record_type='bundles' and record_key=?",(run,)).fetchone()
  bundle=load(row2[0]);h=bundle['handoff']
  aid=h.get('document_artifact_id') or h.get('document2_artifact_id')
  row2=research.execute("select payload_json from codex_runtime_records "
   "where record_type='published_documents' and run_id=? and record_key=?",(run,aid)).fetchone()
  doc=load(row2[0]);content=doc.get('content_text');path=doc.get('storage_path')
  if not content and path:content=(ROOT/path.removeprefix('/data/')).read_text()
  if not content:raise ValueError('published document unavailable: '+str(run))
  texts[name]=content
 emit('case',case=c,research=texts)
"""
    )
    return {r["case"]["case_id"]: r for r in remote_read(body, host)}


def neutral_upstream(original):
    return {
        name: {
            k: v
            for k, v in (original.get(name + "_final") or {}).items()
            if k in {"result", "confidence", "reason"}
        }
        for name in ("w1", "w2")
    }


def prepare_curated(root, *, catalog=CATALOG, previous=None, host="doxagent-sg"):
    root = Path(root)
    marker = root / "manifest.json"
    if marker.exists() and read_json(marker).get("schema_version") != SCHEMA:
        raise ValueError("use a new curated dataset directory; legacy datasets remain immutable")
    catalog = Path(catalog)
    if not catalog.exists() and (root / "preparation/catalog_source.md").exists():
        catalog = root / "preparation/catalog_source.md"
    previous = Path(previous or "eval/w3_backtest/datasets/w3_history_20261014")
    rows = parse_catalog(catalog, previous)
    overrides_path = root / "preparation/selection_overrides.json"
    overrides = read_json(overrides_path) if overrides_path.exists() else {}
    for row in rows:
        override = overrides.get(row["case_key"])
        if override and override.get("included") is False:
            row.update(included=False, target="移除", exclusion_reason=override["reason"])
    active = [r for r in rows if r["included"]]
    old = {p.parent.name: read_json(p) for p in (previous / "inputs").glob("*/case.json")}
    needed = [r["original_case_id"] for r in active if r["case_key"] not in old]
    fetched = acquire_cases(needed, host) if needed else {}
    write_json(
        root / "preparation/catalog_source.json",
        dict(path=str(catalog), sha256=hashlib.sha256(Path(catalog).read_bytes()).hexdigest()),
    )
    if catalog.resolve() != (root / "preparation/catalog_source.md").resolve():
        shutil.copyfile(catalog, root / "preparation/catalog_source.md")
    (root / "catalog.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    cases = []
    for row in active:
        key = row["case_key"]
        if key in old:
            prior = old[key]
            original = prior["original_case"]
            source_research = previous / "research" / prior["research_key"]
            research = dict(
                document1=(source_research / "document1.md").read_text(encoding="utf-8-sig"),
                document2=(source_research / "document2.json").read_text(encoding="utf-8-sig"),
            )
        else:
            found = fetched[row["original_case_id"]]
            original = found["case"]
            research = found["research"]
        source = original["source"]
        snapshot = source["snapshot"]
        pin = original["version_pin"]
        research_key = hashlib.sha256(
            json.dumps(
                {k: pin.get(k) for k in ["document1_run_id", "document2_run_id"]}, sort_keys=True
            ).encode()
        ).hexdigest()[:20]
        folder = root / "research" / research_key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "document1.md").write_text(research["document1"], encoding="utf-8")
        (folder / "document2.json").write_text(research["document2"], encoding="utf-8")
        write_json(
            folder / "manifest.json",
            {k: pin.get(k) for k in ["document1_run_id", "document2_run_id"]},
        )
        date_only = (
            source.get("publication_time_basis") == "DATE"
            or len(str(source.get("published_at", ""))) == 10
        )
        case = dict(
            schema_version="w3-backtest.case.v2",
            case_key=key,
            ticker=row["ticker"],
            original_case_id=row["original_case_id"],
            research_key=research_key,
            as_of=None if date_only else source["published_at"],
            time_basis="DATE_REQUIRES_SOURCE_TIME" if date_only else "MESSAGE_PUBLICATION",
            message=dict(
                title=snapshot["title"],
                body=snapshot.get("body"),
                url=source.get("url"),
                publisher=source.get("source_id"),
                published_at=source.get("published_at"),
                publication_time_basis=source.get("publication_time_basis"),
            ),
            **neutral_upstream(original),
        )
        target = root / "inputs" / key / "case.json"
        if target.exists():
            # Case agents may have resolved DATE precision; rerunning export must retain it.
            existing = read_json(target)
            if existing.get("as_of") and date_only:
                case.update(as_of=existing["as_of"], time_basis=existing["time_basis"])
        write_json(target, case)
        write_json(root / "preparation/original_cases" / (key + ".json"), original)
        cases.append(dict(case_key=key, path=f"inputs/{key}/case.json", ticker=row["ticker"]))
    gold = [
        dict(
            case_key=r["case_key"],
            original_case_id=r["original_case_id"],
            gold_source="user-final-20261030",
            tags=r["tags"],
            expected_action="NO_TRADE" if r["target"] == "NO_TRADE" else "TRADE",
            expected_direction=None if r["target"] == "NO_TRADE" else r["target"],
        )
        for r in active
    ]
    (root / "gold.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in gold), encoding="utf-8"
    )
    (root / "cases.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in cases), encoding="utf-8"
    )
    manifest = dict(
        schema_version=SCHEMA,
        dataset_id=root.name,
        evaluation_scope="w3_decision_only",
        case_count=len(cases),
        cases=cases,
        construction_model=CONSTRUCTION_MODEL,
        construction_effort=CONSTRUCTION_EFFORT,
        prompt_root="prompt_source",
        preparation_status="PREPARING",
    )
    write_json(root / "manifest.json", manifest)
    snapshot_production_prompts(root / "prompt_source", host)
    return manifest


def build_curated(root):
    root = Path(root)
    manifest = read_json(root / "manifest.json")
    ready = []
    incomplete = []
    for entry in manifest["cases"]:
        key = entry["case_key"]
        case = read_json(root / entry["path"])
        folder = root / "inputs" / key / "evidence"
        sources = []
        search = folder / "web_search.jsonl"
        metadata = (
            {
                r.get("path", r.get("id")): r
                for r in [
                    json.loads(line)
                    for line in search.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            }
            if search.exists()
            else {}
        )
        for p in sorted((folder / "sources").glob("*.md")):
            name = "sources/" + p.name
            sources.append({**metadata.get(name, {}), "id": name, "path": name})
        if search.exists():
            search.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in sources),
                encoding="utf-8",
            )
        market_path = folder / "market_view.json"
        market = read_json(market_path) if market_path.exists() else {}
        has_market = bool(market) and market.get("observed_at") == case.get("as_of")
        has_market = has_market and all(
            market.get("series", {}).get(n, {}).get("rows")
            or market.get("series", {}).get(n, {}).get("session_state") == "MARKET_CLOSED"
            for n in ("daily", "hourly", "fine")
        )
        if not case.get("as_of") or not sources or not has_market:
            incomplete.append(
                dict(
                    case_key=key,
                    time=bool(case.get("as_of")),
                    sources=len(sources),
                    market=has_market,
                )
            )
        else:
            ready.append(key)
        write_json(
            folder / "index.json",
            dict(
                as_of=case.get("as_of"),
                sources=sources,
                market=dict(access="w3_research.market_view", observed_at=case.get("as_of")),
            ),
        )
    manifest.update(
        preparation_status="READY" if not incomplete else "PREPARING",
        preparation_summary=dict(
            cases=len(manifest["cases"]), ready=len(ready), incomplete=incomplete
        ),
        input_fingerprint=input_fingerprint(root),
    )
    write_json(root / "manifest.json", manifest)
    return manifest


def construction_tasks(root, keys=None, batch_size=4):
    root = Path(root).resolve()
    manifest = read_json(root / "manifest.json")
    selected = [e["case_key"] for e in manifest["cases"] if not keys or e["case_key"] in keys]
    tasks = []
    for n in range(0, len(selected), batch_size):
        batch = selected[n : n + batch_size]
        task = dict(
            model=CONSTRUCTION_MODEL,
            reasoning_effort=CONSTRUCTION_EFFORT,
            case_keys=batch,
            manual=str(MANUAL.resolve()),
            dataset=str(root),
            instructions=(
                "先读构造规范手册；只读本批 inputs/*/case.js"
                "on、research D1/D2、prompt_sourc"
                "e/AGENTS.md、skills/skill.md、ba"
                "cktest.md。禁止读 Gold、catalog、原始C"
                "ase、preparation或历史报告，禁止读取实施方案。"
                "基于身处 as_of 的 W3 研究需要执行真实 Web S"
                "earch，准备原发正文、细节、Prior、传导和必要对照，"
                "避免只收转载。真实收集，禁止虚构来源或正文。只写本批 evi"
                "dence/sources/*.md 和 evidence/"
                "web_search.jsonl；source正文用原文或忠"
                "实摘录，索引仅含 id/path/title/url/pub"
                "lisher/published_at/role，不写 we"
                "b_gaps 或采集器可信度判断。研究问题与采集困难仅写独立"
                " preparation/web_review/<case_"
                "key>.md；不要判断交易方向或改 Gold/Tag。DA"
                "TE精度案例仅补确认的原发精确时刻加5分钟并写回该Case "
                "as_of/time_basis，其他原消息字段不改。每个来"
                "源文件写 URL、原发时间、角色和实际读到的正文；12-20"
                "份来源仅作参考，按信息覆盖选择，不设硬上限或凑数。完成后逐C"
                "ase报告来源数量、覆盖面、DATE补时证据及实际困难。"
            ),
        )
        path = root / "preparation/construction_tasks" / f"batch_{n // batch_size + 1:02}.json"
        write_json(path, task)
        tasks.append(str(path))
    return dict(
        tasks=tasks, cases=len(selected), model=CONSTRUCTION_MODEL, effort=CONSTRUCTION_EFFORT
    )
