"""Small, operator-driven replay utilities; validation timing belongs to the SOP.

Run with the project's Python: python scripts/distribution_relevance_debug.py --help
No production writes, tuning agent, session state machine, or validation access gate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import regex
import yaml

from doxagent.message_bus_v2.distribution import DistributionWorker
from doxagent.message_bus_v2.jev import JevClient
from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms, validate_terms
from doxagent.message_bus_v2.relevance import _match, _normalize, regex_relevant
from doxagent.message_bus_v2.schema import RawMessageInput, canonical_json

ROOT = Path.cwd() if __file__ == "<stdin>" else Path(__file__).resolve().parents[1]
EXPERIENCE = ROOT / "docs/by-distribution-relevance-debug-experience.md"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def rows(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_rows(path, values):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in values), encoding="utf-8"
    )


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_context(database, output):
    """Read-only scalar/JSON export, without constructing a repository/owner."""
    from doxagent.v2_read.native_content import NativeContent

    database = Path(database).resolve()
    codec = NativeContent(database)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        codec.definition_connection = db

        def decode(raw):
            return codec.decode(json.loads(raw))

        sources = [decode(r[0]) for r in db.execute("SELECT data_json FROM source_definitions")]
        bindings = [
            decode(r[0]) for r in db.execute("SELECT data_json FROM ticker_source_bindings")
        ]
        saved = db.execute(
            "SELECT t.ticker,t.current_revision,r.config_json FROM ticker_monitoring_terms t "
            "JOIN ticker_monitoring_term_revisions r ON r.ticker=t.ticker "
            "AND r.revision=t.current_revision"
        ).fetchall()
        saved = [(ticker, revision, decode(config)) for ticker, revision, config in saved]
    result = {
        "snapshot_at": datetime.now(UTC).isoformat(),
        "sources": {
            s["source_id"]: {
                k: s.get(k) for k in ("enabled", "acquisition_mode", "content_language", "version")
            }
            for s in sources
        },
        "bindings": [
            {k: b.get(k) for k in ("ticker", "source_id", "enabled", "tombstoned_at")}
            for b in bindings
        ],
        "terms": {ticker: config for ticker, _, config in saved},
        "revisions": {ticker: revision for ticker, revision, _ in saved},
    }
    if output == "-":
        print(json.dumps(result, ensure_ascii=False))
    else:
        write(output, result)


def subsample(dataset, output, count=50, seed=2026102804):
    """Select by ticker and ID, never by GOLD, score, or body availability."""
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("sample output is not empty; reuse its manifest or choose a new folder")
    rng = random.Random(seed)
    manifest = {"seed": seed, "per_ticker": count, "inputs": {}, "outputs": {}}
    for split in ("development", "validation"):
        source = Path(dataset) / f"{split}-gold.jsonl"
        groups = defaultdict(list)
        for row in rows(source):
            groups[row["ticker"]].append(row)
        selected = []
        for ticker, pool in sorted(groups.items()):
            if len(pool) < count:
                raise ValueError(f"{split}/{ticker}: only {len(pool)} rows, need {count}")
            selected.extend(rng.sample(sorted(pool, key=lambda r: r["pair_id"]), count))
        target = output / f"{split}-gold.jsonl"
        write_rows(target, selected)
        manifest["inputs"][split] = {"path": str(source.resolve()), "sha256": sha(source)}
        manifest["outputs"][split] = {
            "sha256": sha(target),
            "count": len(selected),
            "ticker_counts": dict(Counter(r["ticker"] for r in selected)),
        }
    write(output / "manifest.json", manifest)
    return manifest


def required_languages(context, ticker):
    return {
        context["sources"][b["source_id"]]["content_language"]
        for b in context["bindings"]
        if b["ticker"] == ticker
        and b["enabled"]
        and not b.get("tombstoned_at")
        and b["source_id"] in context["sources"]
        and context["sources"][b["source_id"]].get("enabled")
        and context["sources"][b["source_id"]].get("acquisition_mode")
        in {"by_search", "by_distribution"}
        and context["sources"][b["source_id"]].get("content_language")
    }


def load_terms(path, ticker, context):
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig"))
    if "terms" in value:
        value = value["terms"][ticker]
    elif "ticker" not in value:
        value = value[ticker]
    terms = TickerMonitoringTerms.model_validate(value)
    if terms.ticker != ticker:
        raise ValueError("candidate ticker does not match requested ticker")
    validate_terms(terms, required_languages(context, ticker))
    if len(canonical_json(terms.model_dump(mode="json")).encode()) > 65536:
        raise ValueError("candidate exceeds production 64 KiB limit")
    return terms


def message_for(row, context):
    sources = row.get("sources", [row.get("source_id")])
    languages = {context["sources"].get(s, {}).get("content_language") for s in sources}
    languages.discard(None)
    if not languages:
        raise ValueError("SOURCE_LANGUAGE_UNKNOWN: refresh context; do not assume English")
    language = sorted(languages)[0]
    # The 1800-case pack lacks original publisher/language fields. Reconstruction is
    # explicit and fixed across candidates; it is not a byte-exact historical request.
    evidence = row["evidence"]
    message = RawMessageInput(
        **evidence,
        url=row.get("canonical_url", "https://replay.invalid/article"),
        source=sources[0],
        raw_payload={},
        metadata={"content_language": language},
    )
    return message, sorted(languages)


def regex_trace(terms, languages, message):
    hits = []
    for language in dict.fromkeys([*languages, "en"]):
        rules = terms.l2.get(language)
        for group in rules.groups if rules else []:
            if group.any and not any(_match(t, message) for t in group.any):
                continue
            if not all(_match(t, message) for t in group.all):
                continue
            if any(_match(t, message) for t in group.none):
                continue
            terms_hit = []
            for term in [*group.any, *group.all]:
                text = (
                    _normalize(getattr(message, term.field))
                    if term.field != "all"
                    else "\n".join(
                        _normalize(getattr(message, f)) for f in ("title", "summary", "body")
                    )
                )
                pattern = term.regex or regex.escape(_normalize(term.literal))
                if term.literal is not None and term.whole_word:
                    pattern = rf"(?<!\w){pattern}(?!\w)"
                try:
                    match = regex.search(
                        pattern, text, flags=0 if term.case_sensitive else regex.I, timeout=0.02
                    )
                except (regex.error, TimeoutError):
                    continue
                if match:
                    terms_hit.append(
                        {
                            "pattern": term.literal or term.regex,
                            "field": term.field,
                            "matched": match.group(),
                            "context": text[max(0, match.start() - 50) : match.end() + 50],
                        }
                    )
            hits.append({"language": language, "group": group.id, "terms": terms_hit})
    return hits


def metrics(results):
    counts = Counter()
    for r in results:
        if r.get("error") or r["prediction"] is None:
            counts["errors"] += 1
        elif r["gold"] == "INSUFFICIENT":
            counts["insufficient"] += 1
        else:
            truth = r["gold"] == "RELATED"
            counts[("TP" if truth else "FP") if r["prediction"] else ("FN" if truth else "TN")] += 1
    tp, fp, fn = counts["TP"], counts["FP"], counts["FN"]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    complete = not counts["errors"]
    return {
        **{k: counts[k] for k in ("TP", "FP", "FN", "TN", "errors", "insufficient")},
        "total": len(results),
        "precision": precision,
        "recall": recall,
        "complete": complete,
        "passed": complete
        and precision is not None
        and recall is not None
        and precision > 0.8
        and recall > 0.8,
    }


def history_for(path, selected):
    wanted = {(r["family_id"], r["ticker"], r["evidence_sha256"]) for r in selected}
    result = defaultdict(list)
    if path:
        for r in rows(path):
            key = (r["family_id"], r["ticker"], r["evidence_sha256"])
            if (
                key in wanted
                and r["evidence_basis"] == "retained_matching_decision_content_version"
            ):
                result[key].extend(
                    d for d in r["decisions"] if d["content_version"] == r["content_version"]
                )
    for key, values in result.items():
        result[key] = list({d["decision_id"]: d for d in values}.values())
    return result


async def evaluate(args, client=None):
    if getattr(args, "env_file", None):
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=False)
    context = read(args.context)
    terms = load_terms(args.terms, args.ticker, context)
    selected = [r for r in rows(args.dataset) if r["ticker"] == args.ticker]
    if not selected or len(selected) != len({r["pair_id"] for r in selected}):
        raise ValueError("empty dataset for ticker or duplicate pair_id")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(terms.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    metadata = {
        "track": args.track,
        "ticker": args.ticker,
        "dataset_sha256": sha(args.dataset),
        "context_sha256": sha(args.context),
        "candidate_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "engine_sha256": {
            n: sha(ROOT / f"src/doxagent/message_bus_v2/{n}.py")
            for n in (
                ("relevance", "monitoring_terms")
                if args.track == "regex"
                else ("jev", "distribution", "monitoring_terms")
            )
        },
        "model": JevClient.MODEL if args.track == "decision" else None,
        "decision_url": (os.getenv("DOXAGENT_MESSAGE_BUS_DECISION_URL") or JevClient.URL)
        if args.track == "decision"
        else None,
        "threshold": 0.5,
        "source_context": "reconstructed_from_frozen_source_ids",
    }
    if (output / "metadata.json").exists() and read(output / "metadata.json") != metadata:
        raise ValueError(
            "output contains another candidate/dataset/context/engine; use a new output folder"
        )
    write(output / "metadata.json", metadata)
    write(output / "candidate.json", terms.model_dump(mode="json"))
    cached = (
        {r["pair_id"]: r for r in rows(output / "predictions.jsonl")}
        if (output / "predictions.jsonl").exists()
        else {}
    )
    pending = [
        r for r in selected if r["pair_id"] not in cached or cached[r["pair_id"]].get("error")
    ]
    owned = False
    if args.track == "decision" and pending and client is None:
        key = os.getenv("DASHSCOPE_API_KEY")
        if not key:
            raise ValueError("DASHSCOPE_API_KEY is not set (not printed or saved)")
        client = JevClient(key, url=metadata["decision_url"])
        owned = True
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(row):
        async with semaphore:
            result = {
                "pair_id": row["pair_id"],
                "gold": row["gold"]["label"],
                "prediction": None,
                "error": None,
            }
            try:
                message, languages = message_for(row, context)
                result["languages"] = languages
                if args.track == "regex":
                    result["prediction"] = any(
                        regex_relevant(terms, lang, message) for lang in languages
                    )
                    result["matches"] = regex_trace(terms, languages, message)
                else:
                    # Actual production segmentation, limits, retry and score aggregation;
                    # no regex prerequisite and no bus/repository/delivery side effects.
                    worker = DistributionWorker(None, None, None, jev=client)
                    scores, attempts, errors = await worker._jev(message, {args.ticker: terms})
                    result.update(
                        score=scores.get(args.ticker),
                        provider_attempts=attempts.get(args.ticker, 0),
                    )
                    result["error"] = errors.get(args.ticker)
                    if args.ticker in scores:
                        result["prediction"] = scores[args.ticker] >= 0.5
                    elif not result["error"]:
                        result["error"] = "MISSING_MODEL_SCORE"
            except Exception as exc:
                result["error"] = type(exc).__name__  # No provider body, URL query or credentials.
                if isinstance(exc, ValueError) and str(exc).startswith("SOURCE_LANGUAGE_UNKNOWN"):
                    result["error"] = str(exc)
            cached[row["pair_id"]] = result
            with (output / "predictions.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            if len([r for r in selected if r["pair_id"] in cached]) % 10 == 0:
                print(f"{args.ticker}/{args.track}: {len(cached)}/{len(selected)}", flush=True)

    try:
        await asyncio.gather(*(one(r) for r in pending))
    finally:
        if owned:
            await client.close()
    results = [cached[r["pair_id"]] for r in selected]
    summary = {
        **metrics(results),
        "track": args.track,
        "ticker": args.ticker,
        "candidate_sha256": metadata["candidate_sha256"],
    }
    write(output / "metrics.json", summary)
    history = history_for(args.history, selected)
    details = []
    for row, result in zip(selected, results, strict=True):
        label = result["gold"]
        kind = (
            "ERROR"
            if result.get("error")
            else "INSUFFICIENT"
            if label == "INSUFFICIENT"
            else ("TP" if label == "RELATED" else "FP")
            if result["prediction"]
            else ("FN" if label == "RELATED" else "TN")
        )
        details.append(
            {
                **row,
                "replay": result,
                "outcome": kind,
                "historical_same_input_decisions": history[
                    (row["family_id"], row["ticker"], row["evidence_sha256"])
                ],
            }
        )
    write_rows(output / "details.jsonl", details)
    write_rows(
        output / "badcases.jsonl", [r for r in details if r["outcome"] in {"FP", "FN", "ERROR"}]
    )
    historical_badcases = []
    for row in details:
        if row["gold"]["label"] == "INSUFFICIENT":
            continue
        for decision in row["historical_same_input_decisions"]:
            score = decision["regex_result"] if args.track == "regex" else decision["jev_result"]
            if score is not None and (score >= 0.5) != (row["gold"]["label"] == "RELATED"):
                historical_badcases.append(
                    {
                        "pair_id": row["pair_id"],
                        "evidence": row["evidence"],
                        "gold": row["gold"],
                        "historical_decision": decision,
                    }
                )
    write_rows(output / "historical_badcases.jsonl", historical_badcases)
    return summary


def comparison(root, with_validation=False):
    table = []
    for directory in sorted(Path(root).glob("attempt-*")):
        if not (directory / "development/metrics.json").exists():
            continue
        train = read(directory / "development/metrics.json")
        item = {"attempt": directory.name, "development": train, "eligible": False}
        p, r = train["precision"], train["recall"]
        item["development_mean"] = (
            (p + r) / 2 if p is not None and r is not None and train["complete"] else None
        )
        if with_validation and (directory / "validation/metrics.json").exists():
            val = read(directory / "validation/metrics.json")
            tm, vm = (
                read(directory / "development/metadata.json"),
                read(directory / "validation/metadata.json"),
            )
            if any(
                tm[k] != vm[k]
                for k in (
                    "candidate_sha256",
                    "context_sha256",
                    "engine_sha256",
                    "model",
                    "threshold",
                    "decision_url",
                )
            ):
                raise ValueError(f"{directory.name}: development/validation configuration differs")
            item["validation"] = val
            vp, vr = val["precision"], val["recall"]
            item["eligible"] = (
                item["development_mean"] is not None
                and val["complete"]
                and vp is not None
                and vr is not None
            )
            item["mean"] = (p + r + vp + vr) / 4 if item["eligible"] else None
            item["overfit_suspected"] = bool(
                item["eligible"] and train["passed"] and not val["passed"]
            )
            item["precision_drop"] = p - vp if item["eligible"] else None
            item["recall_drop"] = r - vr if item["eligible"] else None
        table.append(item)
    train_rank = sorted(
        (r for r in table if r["development_mean"] is not None),
        key=lambda r: (
            -r["development_mean"],
            -min(r["development"]["precision"], r["development"]["recall"]),
            r["attempt"],
        ),
    )
    paired = [r for r in table if r["eligible"]]
    paired.sort(
        key=lambda r: (
            -r["mean"],
            -min(r[s][m] for s in ("development", "validation") for m in ("precision", "recall")),
            r["attempt"],
        )
    )
    complete = (
        bool(table)
        and with_validation
        and all(
            row["development"]["complete"] and row.get("validation", {}).get("complete", False)
            for row in table
        )
    )
    return {
        "attempts": table,
        "development_best": train_rank[0]["attempt"] if train_rank else None,
        "all_attempts_paired_and_complete": complete,
        "final_best": paired[0]["attempt"] if complete and paired else None,
        "ranking": [r["attempt"] for r in paired],
        "warning": (
            "Final validation is used for selection, not an unbiased final holdout estimate."
        ),
    }


def note(root, ticker, track, session, text, shared=EXPERIENCE, with_validation=False):
    if not session or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in session
    ):
        raise ValueError("session must use letters, digits, underscore or hyphen")
    root = Path(root)
    tracks = [(name, root / name) for name in ("regex", "decision") if (root / name).is_dir()]
    reports = (
        [(name, comparison(path, with_validation)) for name, path in tracks]
        if tracks
        else [(track, comparison(root, with_validation))]
    )
    start, end = f"<!-- relevance-debug:{session} -->", f"<!-- /relevance-debug:{session} -->"
    lines = [
        start,
        f"## {session} · {ticker}",
        "",
        "|板块|Attempt|调试 P/R|验证 P/R|",
        "|---|---|---|---|",
    ]

    def fmt(v):
        return "—" if v is None else f"{v:.1%}"

    for name, report in reports:
        for r in report["attempts"]:
            cells = []
            for split in ("development", "validation"):
                m = r.get(split, {})
                cells.append(f"{fmt(m.get('precision'))} / {fmt(m.get('recall'))}")
            lines.append(f"|{name}|{r['attempt']}|{cells[0]}|{cells[1]}|")
    lines += ["", text.strip(), "", f"原始指标与具体坏例：`{Path(root).as_posix()}`。", end]
    shared = Path(shared)
    old = shared.read_text(encoding="utf-8")
    block = "\n".join(lines)
    if start in old:
        a, b = old.index(start), old.index(end) + len(end)
        updated = old[:a] + block + old[b:]
    else:
        updated = old.rstrip() + "\n\n" + block + "\n"
    shared.write_text(updated, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sample")
    p.add_argument("--dataset", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--count", type=int, default=50)
    p.add_argument("--seed", type=int, default=2026102804)
    p = sub.add_parser("export-context")
    p.add_argument("--db", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("candidate")
    p.add_argument("--context", required=True)
    p.add_argument("--ticker", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("evaluate")
    for k in ("dataset", "terms", "context", "ticker", "output"):
        p.add_argument(f"--{k}", required=True)
    p.add_argument("--track", choices=["regex", "decision"], required=True)
    p.add_argument("--history")
    p.add_argument("--concurrency", type=int, choices=range(1, 9), default=4)
    p.add_argument("--env-file", help="optional dotenv file; existing environment wins")
    p = sub.add_parser("compare")
    p.add_argument("--root", required=True)
    p.add_argument("--with-validation", action="store_true")
    p = sub.add_parser("note")
    for k in ("root", "ticker", "track", "session", "text-file"):
        p.add_argument(f"--{k}", required=True)
    p.add_argument("--shared", default=str(EXPERIENCE))
    p.add_argument("--with-validation", action="store_true")
    args = parser.parse_args()
    if args.command == "sample":
        print(
            json.dumps(
                subsample(args.dataset, args.output, args.count, args.seed), ensure_ascii=False
            )
        )
    elif args.command == "export-context":
        export_context(args.db, args.output)
    elif args.command == "candidate":
        write(args.output, read(args.context)["terms"][args.ticker.upper()])
    elif args.command == "evaluate":
        args.ticker = args.ticker.upper()
        print(json.dumps(asyncio.run(evaluate(args)), ensure_ascii=False))
    elif args.command == "compare":
        print(
            json.dumps(
                comparison(args.root, args.with_validation),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        note(
            args.root,
            args.ticker,
            args.track,
            args.session,
            Path(args.text_file).read_text(encoding="utf-8"),
            args.shared,
            args.with_validation,
        )


if __name__ == "__main__":
    main()
