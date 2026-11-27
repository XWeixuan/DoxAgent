"""Preview by default. Apply through native versioned services and binding CAS."""
import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import AcquisitionMode, TickerSourceBinding, UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.v2_control.bindings import Bindings, TransactionRepository

PRIVATE = "_binding_extra_relevance_v1"
SOURCES = {"huggingnews_rss", "technews_hardware", "tomshardware_rss", "quantum_zeitgeist"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rules = json.loads(args.rules.read_text(encoding="utf-8-sig"))
    path = Path(os.environ["DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH"]).resolve(strict=True)
    repo, control = MessageBusV2Repository(path, initialize=False), Bindings(path)
    roster = {b.binding_id:b for b in repo.list_bindings(active_only=True) if b.source_id in SOURCES}
    assert set(roster) == set(rules), "subscription roster changed; review before applying"
    normalized = {k:TickerSourceBinding(binding_id=k, ticker=roster[k].ticker,
        source_id=roster[k].source_id, extra_relevance=v).extra_relevance for k,v in rules.items()}
    for sid in SOURCES:
        assert repo.get_source(sid).acquisition_mode == AcquisitionMode.BY_DISTRIBUTION
    stable = lambda b: b.model_dump(include={"ticker","source_id","enabled","polling","streaming","source_parameters"})
    before = {k:stable(v) for k,v in roster.items()}
    tickers = sorted({b.ticker for b in roster.values()})
    terms_sql = ("SELECT t.ticker,t.current_revision,r.config_hash FROM ticker_monitoring_terms t "
        "JOIN ticker_monitoring_term_revisions r ON r.ticker=t.ticker AND r.revision=t.current_revision "
        "WHERE t.ticker IN ("+",".join("?" for _ in tickers)+")")
    with repo._connect() as db:
        terms_before = {r[0]:tuple(r[1:]) for r in db.execute(terms_sql, tickers)}
    receipt = {"rules_sha256":hashlib.sha256(args.rules.read_bytes()).hexdigest(),
        "bindings":len(roster), "tickers":sorted({b.ticker for b in roster.values()}),
        "pending":sum(roster[k].extra_relevance!=v for k,v in normalized.items()),"applied":False}
    if not args.apply:
        print(json.dumps(receipt)); return
    backup = path.parent / "binding-extra-20261127.before.private.json"
    if not backup.exists():
        raw = {"sources":{s:repo.get_source(s).model_dump(mode="json") for s in SOURCES},
               "bindings":{k:v.model_dump(mode="json") for k,v in roster.items()}}
        with backup.open("x", encoding="utf-8") as file:
            os.fchmod(file.fileno(), 0o600)
            json.dump(raw, file, ensure_ascii=False)
    # Older strict readers still see an ordinary binding plus an allowed opaque
    # parameter. New readers unpack it before adapters or public parameter editors.
    for sid in sorted(SOURCES):
        with control.connect(write=True) as db:
            native = TransactionRepository(path, db)
            source = native.get_source(sid)
            schema = json.loads(json.dumps(source.parameter_schema))
            spec = {"type":"object", "readOnly":True, "writeOnly":True}
            existing = schema.setdefault("properties", {}).get(PRIVATE)
            assert existing is None or existing == spec, "reserved parameter collision"
            if existing is None:
                schema["properties"][PRIVATE] = spec
                MessageBusV2Service(native).update_source(sid, {"parameter_schema":schema},
                    actor=UpdateActor.SYSTEM, reason="Backward-compatible optional binding relevance metadata")
    receipts = []
    for key in sorted(rules):
        current = repo.get_binding(key)
        if current.extra_relevance != normalized[key]:
            old = control.get(current.ticker, key)
            saved = control.mutate(current.ticker, key, "PATCH", {"extra_relevance":rules[key]},
                "codex-extra-regex", "extra-20261127-"+receipt["rules_sha256"][:12]+"-"+key,
                old["control_etag"])
        else:
            saved = control.get(current.ticker, key)
        assert saved["effective"]["extra_relevance"] == normalized[key]
        receipts.append({"binding_id":key,"binding_version":saved["binding_version"],
                         "source_version":saved["source_version"],"rules_digest":digest(normalized[key])})
    after = {k:repo.get_binding(k) for k in roster}
    assert {k:stable(v) for k,v in after.items()} == before
    with repo._connect() as db:
        assert terms_before == {r[0]:tuple(r[1:]) for r in db.execute(terms_sql, tickers)}
    receipt.update(applied=True, applied_at=datetime.now(UTC).isoformat(), effective=receipts,
        unified_terms_unchanged=True, polling_and_subscriptions_unchanged=True, backup=str(backup))
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
