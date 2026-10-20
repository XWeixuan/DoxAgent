import json
from datetime import datetime

from doxagent.w3_backtest.gold import SOURCE, import_gold
from doxagent.w3_backtest.prices import clip_prices
from doxagent.w3_backtest.report import compare_runs, normalize_decision, score


def test_unified_direction_scores_without_policy_or_host_execution(tmp_path):
    for action in ("LONG", "SHORT", "NO_TRADE"):
        result = dict(decision=dict(decision=action, reason="Offline"), output_disposition="ANALYSIS_ONLY")
        assert normalize_decision(result, {}, tmp_path) == action
        assert score(result, {}, action)["score"] is None


def test_cutoff_excludes_the_ohlc_of_a_bar_that_finishes_in_the_future():
    raw = dict(
        ticker="MU",
        bars=[
            dict(date="2026-10-06T11:50:00Z", open=100, close=101, high=102, low=99, volume=10),
            dict(date="2026-10-06T12:00:00Z", open=101, close=900, high=999, low=1, volume=20),
        ],
    )
    ten, daily = clip_prices(raw, "2026-10-06T12:05:00Z")
    assert len(ten["bars"]) == 1
    assert ten["last_bar_end_at"] == "2026-10-06T12:00:00+00:00"
    assert daily["bars"][0]["high"] == 102
    assert daily["bars"][0]["close"] == 101
    assert daily["bars"][0]["session_finished_by_cutoff"] is False


def test_missing_prices_are_unavailable_instead_of_zero_or_future_estimates():
    ten, daily = clip_prices(
        dict(ticker="MU", bars=[], error="API offline"), "2026-10-06T12:05:00Z"
    )
    assert ten["availability"] == daily["availability"] == "UNAVAILABLE"
    assert ten["bars"] == daily["bars"] == []
    assert ten["error"] == "API offline"


def test_gold_import_keeps_29_unreviewed_and_conditional_reviews_unscored(tmp_path):
    for file in SOURCE.glob("*_[0-9][0-9].json"):
        ticker, part = file.stem.split("_")
        records = json.loads(file.read_text(encoding="utf-8"))["records"]
        for index, record in enumerate(records, 1):
            key = f"{ticker}-{part}-{index:02}"
            folder = tmp_path / "inputs" / key
            folder.mkdir(parents=True)
            (folder / "case.json").write_text(
                json.dumps(
                    dict(
                        case_key=key,
                        original_case_id=record["case_id"],
                    )
                )
            )
    for index in range(29):
        key = f"UNREVIEWED-{index}"
        folder = tmp_path / "inputs" / key
        folder.mkdir()
        (folder / "case.json").write_text(json.dumps(dict(case_key=key, original_case_id=None)))
    summary = import_gold(tmp_path)
    assert summary["cases"] == 73
    assert summary["reviewed"] == 44
    assert summary["unreviewed"] == 29
    gold = {
        r["case_key"]: r
        for r in map(json.loads, (tmp_path / "gold.jsonl").read_text(encoding="utf-8").splitlines())
    }
    assert gold["MU-02-02"]["expected_direction"] == "LONG"
    assert gold["MU-02-01"]["expected_action"] is None
    assert gold["MU-02-01"]["assessment_text"]
    assert gold["RKLB-01-01"]["expected_action"] == "NO_TRADE"
    assert gold["UNREVIEWED-0"]["assessment_text"] is None


def test_policy_direction_is_normalized_from_the_selected_condition(tmp_path):
    folder = tmp_path / "research" / "old"
    folder.mkdir(parents=True)
    (folder / "policy_set.original.json").write_text(
        json.dumps(
            dict(
                policies=[
                    dict(
                        policy_id="P",
                        decision="LONG",
                        activation_conditions=[
                            dict(condition_id="C1"),
                            dict(condition_id="C2", decision="SHORT"),
                        ],
                    )
                ]
            )
        )
    )
    result = dict(
        decision=dict(decision="POLICY_TRADE", policy_trade=dict(policy_id="P", condition_id="C2"))
    )
    assert normalize_decision(result, dict(research_key="old"), tmp_path) == "SHORT"


def test_host_rejection_does_not_turn_wrong_model_trade_into_no_trade():
    result = dict(
        decision=dict(decision="EXPERT_TRADE"), host_receipt=dict(disposition="ANALYSIS_ONLY")
    )
    assert score(result, dict(expected_action="NO_TRADE"), "LONG") == dict(
        score=False,
        category="WRONG_TRADE",
    )
    assert score(result, dict(expected_action=None), "LONG")["category"] == "UNLABELLED"
    assert score(dict(decision=None), dict(expected_action="NO_TRADE"), None)["score"] is None


def test_changed_dataset_inputs_cannot_be_reported_as_improvement(tmp_path):
    roots = [tmp_path / name for name in ("before", "after")]
    for index, root in enumerate(roots):
        root.mkdir()
        (root / "run_manifest.json").write_text(json.dumps(dict(dataset_fingerprint=str(index))))
        (root / "results.jsonl").write_text(
            json.dumps(
                dict(
                    case_key="C",
                    score=bool(index),
                    normalized_decision="NO_TRADE",
                )
            )
            + "\n"
        )
    result = compare_runs(*roots)
    assert result["improved"] == result["regressed"] == 0
    assert result["pairs"][0]["change"] == "NOT_COMPARABLE"


def test_build_preserves_manual_gold_and_keeps_it_out_of_input_identity(tmp_path):
    from doxagent.w3_backtest.cli import build_dataset
    from doxagent.w3_backtest.dataset import input_fingerprint, write_json

    write_json(
        tmp_path / "manifest.json", dict(cases=[dict(case_key="C1", path="inputs/C1/case.json")])
    )
    write_json(
        tmp_path / "inputs/C1/case.json",
        dict(
            case_key="C1",
            evidence_cutoff_at="2026-10-01T12:00:00Z",
            original_case={"case_id": "old"},
        ),
    )
    write_json(tmp_path / "inputs/C1/historical_state.json", {})
    gold = (
        json.dumps(dict(case_key="C1", expected_action="NO_TRADE", gold_source="manual-review"))
        + "\n"
    )
    (tmp_path / "gold.jsonl").write_text(gold, encoding="utf-8")
    build_dataset(tmp_path)
    before = input_fingerprint(tmp_path)
    assert (tmp_path / "gold.jsonl").read_text(encoding="utf-8") == gold
    (tmp_path / "gold.jsonl").write_text(gold.replace("NO_TRADE", "TRADE"), encoding="utf-8")
    build_dataset(tmp_path)
    assert "TRADE" in (tmp_path / "gold.jsonl").read_text(encoding="utf-8")
    assert input_fingerprint(tmp_path) == before


def test_day_precision_never_becomes_an_invented_intraday_timestamp():
    from doxagent.w3_backtest.collect import _cutoff_status

    cutoff = datetime.fromisoformat("2026-09-29T08:08:53+00:00")
    assert _cutoff_status("2026-09-29", cutoff) == "UNKNOWN"
    assert _cutoff_status("2026-09-29T00:00:00Z", cutoff, "DAY") == "UNKNOWN"
    assert _cutoff_status("2026-09-28", cutoff, "DAY") == "BEFORE_CUTOFF"
    assert _cutoff_status("2026-09-30", cutoff, "DAY") == "AFTER_CUTOFF"
    assert _cutoff_status("2026-09-29T07:00:00Z", cutoff, "EXACT") == "BEFORE_CUTOFF"
    assert _cutoff_status("2026-09-29T09:00:00Z", cutoff, "EXACT") == "AFTER_CUTOFF"
