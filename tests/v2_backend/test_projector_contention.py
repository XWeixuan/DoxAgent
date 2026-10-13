import sqlite3
import sys
import pytest
from doxagent.v2_read import cli

@pytest.mark.parametrize('code,retry',[(sqlite3.SQLITE_BUSY,True),(sqlite3.SQLITE_LOCKED,True),(sqlite3.SQLITE_BUSY_SNAPSHOT,True),(sqlite3.SQLITE_CORRUPT,False)])
def test_projection_loop_retries_only_contention(tmp_path,monkeypatch,code,retry):
 calls=[]
 def tick(self,**kwargs):
  calls.append(1)
  if len(calls)>1:raise SystemExit(0)
  exc=sqlite3.OperationalError('fixture');exc.sqlite_errorcode=code;raise exc
 monkeypatch.setattr(cli.ProjectionWorker,'tick',tick)
 monkeypatch.setattr(cli.time,'sleep',lambda _:None)
 monkeypatch.setattr(sys,'argv',['projector','project','--read-db',str(tmp_path/'read.db')])
 with pytest.raises(SystemExit if retry else sqlite3.OperationalError):cli.main()
 assert len(calls)==(2 if retry else 1)
