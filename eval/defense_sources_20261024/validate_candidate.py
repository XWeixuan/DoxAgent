"""Read-only bounded validation against real endpoints using the candidate image."""
import asyncio,json,time
from contextlib import asynccontextmanager
from datetime import datetime,UTC
from types import SimpleNamespace
from pathlib import Path
from doxagent.message_bus_v2.defense_sources import CATALOG,DefenseAdapter,initial_sources
from doxagent.message_bus_v2.defense_body import DefenseBodyReader
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
@asynccontextmanager
async def permit():
 yield
async def main():
 adapter=DefenseAdapter(); reader=DefenseBodyReader(); slots=asyncio.Semaphore(3);sources={s.source_id:s for s in initial_sources()}
 async def one(e):
  async with slots:
   t=time.monotonic(); row={"source_id":e.source_id,"route":e.route,"body_route":e.body_route or e.route}
   try:
    ctx=SimpleNamespace(source=sources[e.source_id],checkpoint={},requested_at=datetime.now(UTC),window_start=None,request_permit=permit,query_plan={"queries":[{"query":"אונדס"}]})
    result=await adapter.poll_shared(ctx); row.update(items=len(result.messages),failures=[f.error_message for f in result.failures],poll_ms=int((time.monotonic()-t)*1000),endpoints=result.acquisition_metadata)
    if result.messages:
     message=result.messages[0]
     record=MediaEnrichmentRecord(standard_message_id="validation",raw_message_id="validation",source_id=e.source_id,ticker=e.tickers[0],title=message.title,body="",url=message.url)
     outcome=await reader.extract(SimpleNamespace(source=sources[e.source_id],message=message),record)
     row["body"]={"succeeded":outcome.succeeded,"outcome":outcome.diagnostics.get("outcome"),"reason":outcome.reason,"http_status":outcome.http_status,"chars":len(outcome.content or ""),"published_at":outcome.diagnostics.get("publisher_published_at"),"method":outcome.extraction_method}
    again=await adapter.poll_shared(SimpleNamespace(**{**vars(ctx),"checkpoint":result.next_checkpoint}))
    row["repeat"]={"items":len(again.messages),"endpoints":again.acquisition_metadata,"failures":[f.error_message for f in again.failures]}
   except Exception as exc:row["error"]=type(exc).__name__+":"+str(exc)[:250]
   return row
 try:rows=await asyncio.gather(*(one(e) for e in CATALOG))
 finally:await adapter.close();await reader.close()
 out=Path("/data/ops/defense-sources-20261024");out.mkdir(parents=True,exist_ok=True);(out/"candidate-validation.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf8")
 print(json.dumps(rows,ensure_ascii=False))
asyncio.run(main())
