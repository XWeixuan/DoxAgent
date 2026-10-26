"""Install the committed CDC trigger revision, retaining prior definitions; no backfill."""
import json
from pathlib import Path
from datetime import datetime,UTC
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.outbox import SourceOutbox
import argparse

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    path=Path(DoxAgentSettings().message_bus_v2_sqlite_path)
    source=SourceOutbox(path,'bus')
    if not args.apply:
     print(json.dumps({'apply':False,'tables':source.migrate(dry_run=True)}));raise SystemExit(0)
    receipt=path.parent/'migrations'/'business-delta-capture-50d6b021.json'
    receipt.parent.mkdir(exist_ok=True)
    if not receipt.exists():
     with source.connection() as db:
      definitions=[dict(r) for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'v2_capture_%'")]
     receipt.write_text(json.dumps({'at':datetime.now(UTC).isoformat(),'definitions':definitions},indent=2))
    tables=source.migrate()
    with source.connection() as db:
     value=db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='v2_capture_poll_states_INSERT'").fetchone()[0]
     assert "'$.units'" in value
    print(json.dumps({'at':datetime.now(UTC).isoformat(),'tables':tables,'receipt':str(receipt),'backfill':False}))


if __name__ == "__main__":
    main()
