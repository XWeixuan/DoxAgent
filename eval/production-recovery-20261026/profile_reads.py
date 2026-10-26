import json
import time
import traceback
from datetime import UTC,datetime,timedelta
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.query_budget import deadline
from doxagent.api_v2.bus import aggregates
from doxagent.api_v2.news_timeliness import poll_cadence,poll_window,body_cohort

store=ReadStore('/data/read/active.json')
with store.connect() as db:
    seq=store.highwater(db)-100
now=datetime.now(UTC)
for name,call in [
 ('bindings',lambda: store.page('native:ticker_source_bindings','INTC',seq,live_bindings=True,limit=21)),
 ('aggregate',lambda: aggregates(store,'INTC',seq)),
 ('cadence',lambda: poll_cadence(store,'INTC',seq,now-timedelta(minutes=30),now)),
 ('window',lambda: poll_window(store,'INTC',seq,now.isoformat())),
 ('cohort',lambda: body_cohort(store,'INTC',seq,{},['2026-10-26'])),
 ('messages',lambda: store.page('message','INTC',seq,limit=21)),
]:
    start=time.monotonic();deadline.set(start+3)
    try:
        value=call()
        print(name,round(time.monotonic()-start,3),'OK',len(value))
    except Exception as e: print(name,round(time.monotonic()-start,3),str(e));traceback.print_exc(limit=3)
    finally:deadline.set(None)
