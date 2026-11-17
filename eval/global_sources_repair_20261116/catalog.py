import json,os,time
from doxagent.api_v2.source_catalog import catalog_data
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.calendar import PageCalendar
start=time.monotonic();store=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH'])
r=catalog_data(store,PageCalendar());r['probe_seconds']=round(time.monotonic()-start,3)
print(json.dumps(r,ensure_ascii=False))
