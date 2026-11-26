"""Bounded read-only production unit and article evidence, without payload bodies."""
import json
import sqlite3
from datetime import UTC,datetime,timedelta
from pathlib import Path

from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.collection_plane.store import path_for

TARGETS={'unchained_crypto','thedefiant_fintech_regulation','cftc_trading_rules','cftc_press_releases','corporate_ir_press_releases'}


def main():
    settings=DoxAgentSettings();out={'at':datetime.now(UTC).isoformat(),'units':[],'articles':[]}
    with sqlite3.connect(path_for(settings.message_bus_v2_sqlite_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
        db.execute('PRAGMA query_only=ON')
        for binding,unit,sid,state in db.execute('SELECT binding_id,unit,source_id,state FROM units WHERE ticker=?',('COIN',)):
            if sid not in TARGETS:continue
            value=json.loads(state)
            latest=db.execute("SELECT status,reason,dispatch_at,finished_at,data_json FROM cycles WHERE binding_id=? AND unit=? AND status!='SCHEDULE' ORDER BY dispatch_at DESC LIMIT 1",(binding,unit)).fetchone()
            out['units'].append(dict(source=sid,unit=unit,state={k:v for k,v in value.items() if k in {'status','last_error','last_error_code','last_error_message','last_success_at','last_attempt_at','consecutive_failures'}},
                latest=dict(status=latest[0],reason=latest[1],dispatch_at=latest[2],finished_at=latest[3],detail=json.loads(latest[4])) if latest else None))
    with sqlite3.connect(Path(settings.message_bus_v2_sqlite_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
        db.execute('PRAGMA query_only=ON')
        since=(datetime.now(UTC)-timedelta(hours=2)).isoformat()
        for key,sid,title,url,seen,outcome,error in db.execute('SELECT article_key,source_id,title,url,first_seen_at,outcome,last_error FROM live_articles WHERE first_seen_at>=? ORDER BY first_seen_at DESC LIMIT 300',(since,)):
            if sid not in TARGETS:continue
            delivery=db.execute('SELECT state,last_error,consumable_at,terms_revision,bootstrap FROM live_deliveries WHERE article_key=? AND ticker=?',(key,'COIN')).fetchone()
            origins=db.execute('SELECT unit,first_seen_at FROM live_origins WHERE article_key=? AND source_id=?',(key,sid)).fetchall()
            out['articles'].append(dict(source=sid,title=title,url=url,first_seen_at=seen,outcome=outcome,error=error,delivery=list(delivery) if delivery else None,source_origins=origins))
    print(json.dumps(out))


if __name__=='__main__':main()
