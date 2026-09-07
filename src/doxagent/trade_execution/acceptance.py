"""Persistent, explicitly armed Paper acceptance. Never activates a Live profile."""

import asyncio
import json
from datetime import timedelta
from typing import Any

from doxagent.persistent_runtime_v2.journal import encode
from doxagent.semantic_clock import expires_at, semantic_day


def create_suite(
    repo: Any,
    *,
    identity: Any,
    revision: Any,
    ticker: Any = "MU",
    session: Any = "RTH",
    side: Any = "LONG",
    arm: Any = False,
) -> Any:
    profile = repo.profile(revision)
    if profile.environment != "PAPER":
        raise ValueError("PAPER_ONLY_ACCEPTANCE")
    if session not in {"RTH", "EXTENDED", "OVERNIGHT"} or side not in {"LONG", "SHORT"}:
        raise ValueError("invalid suite")
    value = {
        "id": identity,
        "revision": revision,
        "ticker": ticker,
        "session": session,
        "side": side,
        "armed": arm,
        "state": "WAIT_MARKET",
        "execution_id": None,
        "due_at": repo.journal.clock().isoformat(),
        "evidence": {},
        "strategy": profile.strategy.model_dump(mode="json"),
    }
    with repo.journal.transaction() as db:
        old = db.execute(
            "SELECT payload FROM te_acceptance_runs WHERE id=?", (identity,)
        ).fetchone()
        if old:
            prior = json.loads(old[0])
            for field in ("revision", "ticker", "session", "side"):
                if prior[field] != value[field]:
                    raise ValueError("immutable acceptance identity conflict")
            return prior
        db.execute(
            "INSERT INTO te_acceptance_runs VALUES(?,?,?,?)",
            (identity, value["state"], value["due_at"], encode(value)),
        )
    return value


async def tick_suites(executor: Any) -> Any:
    repo = executor.repository
    with repo.journal.transaction() as db:
        rows = db.execute(
            "SELECT payload FROM te_acceptance_runs WHERE state IN "
            "('WAIT_MARKET','WAIT_MARKET_DATA','READY','RUNNING') AND due_at<=?",
            (repo.journal.clock().isoformat(),),
        ).fetchall()

    async def tick(value: Any) -> Any:
        if not value["armed"]:
            return
        try:
            profile = repo.profile(value["revision"])
            if profile.environment != "PAPER":
                raise ValueError("PAPER_ONLY_ACCEPTANCE")
            identity = value["execution_id"]
            if not identity and repo.get("executions", "acceptance:" + value["id"]):
                identity = "acceptance:" + value["id"]
                value.update(execution_id=identity, state="RUNNING")
            if identity:
                execution = repo.get("executions", identity)
                entry = repo.get("jobs", identity + ":entry")
                exit_job = repo.get("jobs", identity + ":exit")
                if entry["state"] == "DONE":
                    if execution["entry_result"] not in {"FILLED", "PARTIAL_FILLED"}:
                        value["state"] = "FAIL"
                    elif exit_job and exit_job["state"] == "FAILED":
                        value["state"] = "PARTIAL"
                    elif exit_job and exit_job["state"] == "DONE":
                        value["state"] = (
                            "PASS"
                            if execution["entry_result"] == "FILLED"
                            and repo.fills(identity + ":exit")
                            else "PARTIAL"
                        )
                    elif not repo.get("lots", identity):
                        value["state"] = (
                            "PARTIAL"  # Entry offset another lot; no independent exit tested.
                        )
                    value["evidence"] = {
                        "entry_result": execution["entry_result"],
                        "exit": exit_job,
                        "lot": repo.get("lots", identity),
                    }
            else:
                broker = executor.broker(value["revision"])
                venue = "OVERNIGHT" if value["session"] == "OVERNIGHT" else "SMART"
                contract = await asyncio.to_thread(broker.contract, value["ticker"], venue)
                actual_session = executor.sessions.classify(repo.journal.clock(), contract)
                if actual_session != value["session"]:
                    value["state"] = "WAIT_MARKET"
                else:
                    value["state"] = "WAIT_MARKET_DATA"
                    quotes = (
                        executor.broker(profile.quote_profile_revision)
                        if profile.quote_profile_revision
                        else broker
                    )
                    quote_contract = await asyncio.to_thread(
                        quotes.contract, value["ticker"], venue
                    )
                    if quote_contract["con_id"] != contract["con_id"]:
                        raise ValueError("QUOTE_CONTRACT_MISMATCH")
                    await asyncio.to_thread(
                        quotes.quote,
                        quote_contract,
                        "BUY" if value["side"] == "LONG" else "SELL",
                        strategy=profile.strategy,
                    )
                    identity = "acceptance:" + value["id"]
                    now = repo.journal.clock()
                    intent = {
                        "intent_id": identity,
                        "ticker": value["ticker"],
                        "status": "READY",
                        "expires_at": expires_at(semantic_day(now)).isoformat(),
                        "release_semantic_day": semantic_day(now).isoformat(),
                        "trade": {"decision": value["side"], "decision_origin": "ACCEPTANCE"},
                        "execution_pin": {
                            "revision": value["revision"],
                            "profile": profile.model_dump(mode="json"),
                        },
                    }
                    repo.admit(intent)
                    value.update(state="RUNNING", execution_id=identity)
        except Exception as exc:
            value["last_error"] = str(exc)
        value["due_at"] = (repo.journal.clock() + timedelta(seconds=30)).isoformat()
        with repo.journal.transaction() as db:
            db.execute(
                "UPDATE te_acceptance_runs SET state=?,due_at=?,payload=? WHERE id=?",
                (value["state"], value["due_at"], encode(value), value["id"]),
            )

    await asyncio.gather(*(tick(json.loads(row[0])) for row in rows))
