import sqlite3,json,os
for label,p in [('bus',os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']),('acquisition','/data/bus/acquisition.sqlite3'),('read',os.environ.get('DOXAGENT_V2_READ_SQLITE_PATH','/data/read/read.sqlite3'))]:
 try:
  c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=2); c.execute('pragma query_only=on')
  rows=c.execute("select name,sql from sqlite_master where type='table'").fetchall()
  print(json.dumps({'db':label,'path':p,'schema':rows},ensure_ascii=False));c.close()
 except Exception as e: print(json.dumps({'db':label,'error':str(e)}))
