from __future__ import annotations

import importlib.util
import json
import sqlite3
from argparse import Namespace
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/distribution_relevance_debug.py"
SPEC = importlib.util.spec_from_file_location("distribution_relevance_debug", SCRIPT)
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)


def config(pattern=None):
    return {
        "ticker": "MU",
        "expected_revision": 1,
        "l1_concepts": [{"concept_id": "company", "expressions": {"en": "Micron"}}],
        "l2": {
            "en": {
                "groups": [
                    {
                        "id": "core",
                        "any": [
                            {"regex": pattern}
                            if pattern
                            else {"literal": "server", "whole_word": True}
                        ],
                    }
                ]
            }
        },
        "definition": {"relevant": "memory and compute", "irrelevant": "unrelated"},
    }


def context():
    return {
        "sources": {
            "s": {"enabled": True, "acquisition_mode": "by_distribution", "content_language": "en"}
        },
        "bindings": [{"ticker": "MU", "source_id": "s", "enabled": True, "tombstoned_at": None}],
    }


def case(ident, title, label, ticker="MU"):
    return {
        "pair_id": ident,
        "family_id": ident,
        "evidence_sha256": ident,
        "ticker": ticker,
        "sources": ["s"],
        "evidence": {"title": title, "summary": "", "body": ""},
        "gold": {"label": label, "reason": "must not leak to provider"},
    }


def args_for(tmp_path, track="regex"):
    lab.write(tmp_path / "context.json", context())
    lab.write(tmp_path / "terms.json", config())
    lab.write_rows(
        tmp_path / "data.jsonl",
        [case("a", "observers", "IRRELEVANT"), case("b", "server", "RELATED")],
    )
    return Namespace(
        context=tmp_path / "context.json",
        terms=tmp_path / "terms.json",
        ticker="MU",
        track=track,
        dataset=tmp_path / "data.jsonl",
        output=tmp_path / "out",
        concurrency=2,
        history=None,
    )


def test_metrics_missing_scores_and_insufficient_are_not_negatives():
    result = lab.metrics(
        [
            {"gold": "RELATED", "prediction": True},
            {"gold": "RELATED", "prediction": None, "error": "HTTP_401"},
            {"gold": "INSUFFICIENT", "prediction": True},
        ]
    )
    assert result["TP"] == 1 and result["FN"] == 0
    assert result["errors"] == result["insufficient"] == 1
    assert result["precision"] == result["recall"] == 1
    assert result["complete"] is result["passed"] is False
    assert lab.metrics([{"gold": "IRRELEVANT", "prediction": False}])["precision"] is None
    exact = lab.metrics([{"gold": "RELATED", "prediction": i < 4} for i in range(5)])
    assert exact["recall"] == 0.8 and not exact["passed"]


@pytest.mark.asyncio
async def test_real_regex_replay_traces_and_candidate_identity(tmp_path):
    args = args_for(tmp_path)
    summary = await lab.evaluate(args)
    assert summary["precision"] == summary["recall"] == 1
    assert summary["TP"] == summary["TN"] == 1
    results = lab.rows(args.output / "details.jsonl")
    assert results[1]["replay"]["matches"][0]["terms"][0]["matched"] == "server"
    lab.write(args.terms, config("servers?"))
    with pytest.raises(ValueError, match="another candidate"):
        await lab.evaluate(args)
    args.output = tmp_path / "bad"
    summary = await lab.evaluate(args)
    assert summary["precision"] == 0.5
    assert (
        lab.rows(args.output / "badcases.jsonl")[0]["replay"]["matches"][0]["terms"][0]["matched"]
        == "servers"
    )


def test_languages_follow_bindings_and_english_fallback(tmp_path):
    ctx = context()
    ctx["sources"]["s"]["content_language"] = "he"
    assert lab.required_languages(ctx, "MU") == {"he"}
    cfg = config()
    cfg["l1_concepts"][0]["expressions"]["he"] = "מיקרון"
    cfg["l2"]["he"] = {"groups": [{"id": "he", "any": [{"literal": "זיכרון"}]}]}
    lab.write(tmp_path / "terms.json", cfg)
    terms = lab.load_terms(tmp_path / "terms.json", "MU", ctx)
    message, languages = lab.message_for(case("a", "server", "RELATED"), ctx)
    assert languages == ["he"]
    assert lab.regex_relevant(terms, "he", message)  # English rules still apply.
    assert "ko" not in terms.l2 and "zh-Hant" not in terms.l2
    ctx["bindings"][0]["enabled"] = False
    assert lab.required_languages(ctx, "MU") == set()


@pytest.mark.asyncio
async def test_model_uses_real_client_worker_without_gold_and_resumes_only_errors(tmp_path):
    args = args_for(tmp_path, "decision")
    calls = []
    recovered = False

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload["state"]["title"])
        assert set(payload["state"]) == {"title", "summary", "body", "source", "language"}
        assert "must not leak" not in request.content.decode()
        if payload["state"]["title"] == "observers" and not recovered:
            return httpx.Response(401, json={"error": "test"})
        return httpx.Response(
            200,
            json={
                "answers": {
                    "MU": {
                        "type": "noul",
                        "noul": 0.1 if payload["state"]["title"] == "observers" else 0.9,
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        client = lab.JevClient("test-key", client=transport)
        summary = await lab.evaluate(args, client)
        assert summary["errors"] == 1 and not summary["complete"] and summary["FN"] == 0
        recovered = True
        summary = await lab.evaluate(args, client)
        assert summary["complete"] and summary["passed"]
        assert calls.count("server") == 1 and calls.count("observers") == 2


@pytest.mark.asyncio
async def test_unknown_source_language_is_reported_not_assumed(tmp_path):
    args = args_for(tmp_path)
    ctx = context()
    ctx["sources"] = {}
    lab.write(args.context, ctx)
    summary = await lab.evaluate(args)
    assert summary["errors"] == 2 and summary["FP"] == summary["FN"] == 0
    assert (
        "SOURCE_LANGUAGE_UNKNOWN" in lab.rows(args.output / "badcases.jsonl")[0]["replay"]["error"]
    )


@pytest.mark.asyncio
async def test_model_input_limit_keeps_unknown_and_never_calls_provider(tmp_path):
    args = args_for(tmp_path, "decision")
    row = case("a", "server", "RELATED")
    row["evidence"]["body"] = "a" * 64001
    lab.write_rows(args.dataset, [row])

    class NeverCalls:
        async def classify(self, *_):
            raise AssertionError("over-limit input must not be sent")

    result = await lab.evaluate(args, NeverCalls())
    assert result["errors"] == 1 and result["FN"] == 0
    assert lab.rows(args.output / "badcases.jsonl")[0]["replay"]["error"] == "JEV_INPUT_LIMIT"


@pytest.mark.asyncio
async def test_historical_badcases_require_exact_input_and_known_score(tmp_path):
    args = args_for(tmp_path)
    args.history = tmp_path / "history.jsonl"
    old = {
        "family_id": "b",
        "ticker": "MU",
        "evidence_sha256": "b",
        "evidence_basis": "retained_matching_decision_content_version",
        "content_version": "v1",
        "decisions": [
            {"decision_id": "d1", "content_version": "v1", "regex_result": 0, "jev_result": None},
            {"decision_id": "d2", "content_version": "v2", "regex_result": 0, "jev_result": None},
        ],
    }
    lab.write_rows(args.history, [old, old, {**old, "evidence_basis": "immutable_original_input"}])
    await lab.evaluate(args)
    bad = lab.rows(args.output / "historical_badcases.jsonl")
    assert len(bad) == 1 and bad[0]["historical_decision"]["decision_id"] == "d1"
    assert lab.rows(args.output / "badcases.jsonl") == []


def test_subsample_is_reproducible_fifty_per_ticker_and_keeps_insufficient(tmp_path):
    source = tmp_path / "source"
    for split in ("development", "validation"):
        lab.write_rows(
            source / f"{split}-gold.jsonl",
            [
                case(f"{split}-{t}-{i}", "title", "INSUFFICIENT", t)
                for t in ("MU", "CEG")
                for i in range(100)
            ],
        )
    lab.subsample(source, tmp_path / "one", 50, 7)
    lab.subsample(source, tmp_path / "two", 50, 7)
    for split in ("development", "validation"):
        assert (tmp_path / f"one/{split}-gold.jsonl").read_bytes() == (
            tmp_path / f"two/{split}-gold.jsonl"
        ).read_bytes()
        selected = lab.rows(tmp_path / f"one/{split}-gold.jsonl")
        assert lab.Counter(r["ticker"] for r in selected) == {"MU": 50, "CEG": 50}
        assert all(r["gold"]["label"] == "INSUFFICIENT" for r in selected)


def emit_attempt(root, name, train, val=None):
    for split, metric in [("development", train), ("validation", val)]:
        if metric is None:
            continue
        lab.write(
            root / name / split / "metrics.json",
            {
                "precision": metric[0],
                "recall": metric[1],
                "complete": True,
                "passed": metric[0] > 0.8 and metric[1] > 0.8,
            },
        )
        lab.write(
            root / name / split / "metadata.json",
            {
                k: "same"
                for k in (
                    "candidate_sha256",
                    "context_sha256",
                    "engine_sha256",
                    "model",
                    "threshold",
                    "decision_url",
                )
            },
        )


def test_comparison_does_not_read_validation_by_default_or_pick_incomplete_final(tmp_path):
    emit_attempt(tmp_path, "attempt-01", (0.9, 0.9), (0.8, 0.7))
    emit_attempt(tmp_path, "attempt-02", (0.85, 0.85))
    (tmp_path / "attempt-01/validation/metrics.json").write_text("broken", encoding="utf-8")
    assert lab.comparison(tmp_path)["development_best"] == "attempt-01"
    emit_attempt(tmp_path, "attempt-01", (0.9, 0.9), (0.8, 0.7))
    result = lab.comparison(tmp_path, True)
    assert result["final_best"] is None and result["attempts"][0]["overfit_suspected"]
    emit_attempt(tmp_path, "attempt-02", (0.85, 0.85), (0.85, 0.85))
    assert lab.comparison(tmp_path, True)["final_best"] == "attempt-02"
    emit_attempt(tmp_path, "attempt-03", (0.1, 0.0), (0.1, 0.0))
    for split in ("development", "validation"):
        path = tmp_path / "attempt-03" / split / "metrics.json"
        value = lab.read(path)
        value["precision"] = None
        lab.write(path, value)
    result = lab.comparison(tmp_path, True)
    assert result["all_attempts_paired_and_complete"]
    assert result["final_best"] == "attempt-02"  # Undefined metric is unranked, not unrun.


def test_experience_is_one_record_per_ticker_session_preserves_both_tracks(tmp_path):
    shared = tmp_path / "shared.md"
    shared.write_text("# seed\n", encoding="utf-8")
    emit_attempt(tmp_path / "regex", "attempt-01", (0.9, 0.9))
    lab.note(tmp_path, "MU", "regex", "day-MU", "first", shared)
    emit_attempt(tmp_path / "decision", "attempt-01", (0.85, 0.85))
    lab.note(tmp_path, "MU", "decision", "day-MU", "merged general lesson", shared)
    text = shared.read_text(encoding="utf-8")
    assert text.count("<!-- relevance-debug:day-MU -->") == 1
    assert "|regex|attempt-01|" in text and "|decision|attempt-01|" in text
    assert "merged general lesson" in text and "first" not in text


def test_export_context_is_read_only_and_omits_binding_parameters(tmp_path):
    database = tmp_path / "bus.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            "CREATE TABLE source_definitions(data_json TEXT); "
            "CREATE TABLE ticker_source_bindings(data_json TEXT);"
            "CREATE TABLE ticker_monitoring_terms(ticker TEXT,current_revision INTEGER);"
            "CREATE TABLE ticker_monitoring_term_revisions("
            "ticker TEXT,revision INTEGER,config_json TEXT);"
        )
        db.execute(
            "INSERT INTO source_definitions VALUES(?)",
            (json.dumps({"source_id": "s", **context()["sources"]["s"]}),),
        )
        db.execute(
            "INSERT INTO ticker_source_bindings VALUES(?)",
            (json.dumps({**context()["bindings"][0], "parameters": {"secret": "do-not-export"}}),),
        )
        db.execute("INSERT INTO ticker_monitoring_terms VALUES('MU',2)")
        db.execute(
            "INSERT INTO ticker_monitoring_term_revisions VALUES('MU',2,?)", (json.dumps(config()),)
        )
    before = database.read_bytes()
    lab.export_context(database, tmp_path / "context.json")
    result = lab.read(tmp_path / "context.json")
    assert result["terms"]["MU"]["ticker"] == "MU" and result["revisions"]["MU"] == 2
    assert "do-not-export" not in (tmp_path / "context.json").read_text(encoding="utf-8")
    assert database.read_bytes() == before
