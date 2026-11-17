import sqlite3,json,time
p='/data/read/serving-v4-20261018/v2.sqlite3';db=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=.2)
db.execute("ATTACH DATABASE 'file:/data/runtime/runtime.sqlite3?mode=ro' AS runtime")
deadline=time.monotonic()+5;db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
rows=db.execute("SELECT g.reason,coalesce(o.table_name,json_extract(e.packet,'$.table_name')),coalesce(json_extract(r.payload,'$.kind'),json_extract(e.packet,'$.row.kind')),count(*),sum(o.seq IS NOT NULL),sum(e.seq IS NOT NULL),min(CAST(g.event AS INTEGER)),max(CAST(g.event AS INTEGER)) FROM gaps g LEFT JOIN runtime.v2_source_outbox o ON o.seq=CAST(g.event AS INTEGER) LEFT JOIN runtime.v2_source_receipts r ON r.id=o.receipt_id LEFT JOIN runtime.v2_source_exception_receipts e ON e.seq=CAST(g.event AS INTEGER) WHERE g.source='runtime' GROUP BY 1,2,3").fetchall()
print(json.dumps({'headers':rows,'seconds':5+time.monotonic()-deadline}))
