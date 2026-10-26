import time
from datetime import datetime,timedelta
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.calendar import PageCalendar
from doxagent.api_v2.views import Views
from doxagent.api_v2.bus import source_status
from doxagent.api_v2.news_timeliness import poll_cadence

store=ReadStore('/data/read/active.json');views=Views(store,PageCalendar())
start=time.monotonic()
wire=views.create('recovery-sources-profile','MESSAGE_BUS','INTC','CURRENT_TRADING_DAY')
view=views.get('recovery-sources-profile',wire['view_id'],'INTC')
print('view',time.monotonic()-start,flush=True)
start=time.monotonic()
page=views.page('recovery-sources-profile','native:ticker_source_bindings','INTC',view=view,
                view_id=wire['view_id'],live_bindings=True,limit=20)
print('page',time.monotonic()-start,flush=True)
start=time.monotonic();until=datetime.fromisoformat(view['as_of'])
cadences=poll_cadence(store,'INTC',view['seq'],until-timedelta(minutes=30),until,by_binding=True)
print('cadence',time.monotonic()-start,flush=True)
for binding in page['items']:
    start=time.monotonic()
    value=source_status(store,binding,view['seq'],view,cadences)
    print(binding['source_id'],round(time.monotonic()-start,4),flush=True)
