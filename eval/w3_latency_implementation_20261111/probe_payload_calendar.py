"""Replay only the pure calendar/profile portion of one completed production Case.

Production SQLite is read-only; calendar writes go to a disposable Python dict.
No model, broker, price or network calls are made.
"""
from contextlib import closing,contextmanager
from datetime import UTC,datetime
import json
import os
import signal
import sqlite3
import sys
import time

os.nice(19);signal.alarm(30)
from doxagent.persistent_runtime_v2.schema import RuntimeCase
from doxagent.persistent_runtime_v2.calendar import MarketCalendar
from doxagent.persistent_runtime_v2.w3_context import trading_context
from doxagent.v2_read.native_content import NativeContent

path='/data/runtime/runtime.sqlite3';codec=NativeContent(path)
cid='case_5ec9df499bb64ecba48496e14f6386d5'


def connect():
    db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=.1)
    db.row_factory=codec.row;db.execute('PRAGMA query_only=ON');return db


class Readonly:
    def __init__(self):self.calendar={};self.would_write=[]
    @contextmanager
    def transaction(self,**kwargs):
        assert kwargs.get('write',True) is False
        with closing(connect()) as db:yield db
    def get(self,ns,key,default=None):
        if ns in ['calendar','calendar_bounds']:return self.calendar.get((ns,key),default)
        with closing(connect()) as db:
            row=db.execute('SELECT payload FROM runtime_values WHERE namespace=? AND key=?',(ns,key)).fetchone()
        return json.loads(row[0]) if row else default
    def set(self,ns,key,value):
        assert ns in ['calendar','calendar_bounds'];self.calendar[ns,key]=value;self.would_write.append([ns,key])


with closing(connect()) as db:
    case=RuntimeCase.model_validate_json(db.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?',(cid,)).fetchone()[0])
loads=[];original=MarketCalendar._load_calendar
if '--narrow' in sys.argv:
    def original(self,*args,**kwargs):
        if self._calendar is None:
            import exchange_calendars
            year=case.trading_date.year
            self._calendar=exchange_calendars.get_calendar(self.name,start=f'{year-1}-01-01',end=f'{year+1}-12-31')
        return self._calendar
def measured(self,*args,**kwargs):
    a=time.monotonic();p=time.process_time();value=original(self,*args,**kwargs)
    loads.append(dict(wall_ms=(time.monotonic()-a)*1000,cpu_ms=(time.process_time()-p)*1000,
                      window=[d.isoformat() for d in self._calendar_window] if getattr(self,'_calendar_window',None) else None))
    return value
MarketCalendar._load_calendar=measured
journal=Readonly();rows=[]
for label in ['calendar_cache_miss','calendar_cache_hit']:
    a=time.monotonic();p=time.process_time();value=trading_context(case,journal)
    rows.append(dict(label=label,wall_ms=(time.monotonic()-a)*1000,cpu_ms=(time.process_time()-p)*1000,
                     holding_exit_at=value['holding_exit_at']))
print(json.dumps(dict(at=datetime.now(UTC).isoformat(),case_id=cid,rows=rows,calendar_loads=loads,
                      narrow='--narrow' in sys.argv,simulated_cache_writes=journal.would_write,production_writes=0,model_calls=0)))
