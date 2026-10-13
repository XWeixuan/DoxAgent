import time,signal,faulthandler
from contextlib import contextmanager
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.calendar import PageCalendar
from doxagent.api_v2.views import Views
started=time.monotonic()
def expired(*args):raise TimeoutError('audit deadline')
signal.signal(signal.SIGALRM,expired);signal.alarm(20);faulthandler.dump_traceback_later(5)
class Trace(ReadStore):
 @contextmanager
 def connect(self,**kwargs):
  print('CONNECT',kwargs,round(time.monotonic()-started,3),flush=True)
  with super().connect(**kwargs) as db:
   db.set_trace_callback(lambda sql:print(round(time.monotonic()-started,3),sql[:100],flush=True) if sql.lstrip().upper().startswith(('SELECT','BEGIN','COMMIT')) else None)
   yield db
  print('CLOSE',round(time.monotonic()-started,3),flush=True)
r=Views(Trace('/data/read/v2.sqlite3'),PageCalendar()).create('deployment-audit','RESEARCH','MU',None)
print('DONE',r['activation']['state'],round(time.monotonic()-started,3),flush=True)
