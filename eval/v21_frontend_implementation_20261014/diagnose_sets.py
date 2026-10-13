import sys,importlib.util,sqlite3,time,json
p='/tmp/ui-impl/policy_queries.py'; spec=importlib.util.spec_from_file_location('q',p);q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
db=sqlite3.connect('/tmp/ui-impl/isolated-final.sqlite3');db.row_factory=sqlite3.Row
seq=int(db.execute("SELECT value FROM read_meta WHERE key='highwater'").fetchone()[0])
result={'seq':seq,'parts':[]}
for scope,days in [('prev',['2026-10-12']),('ALL',None)]:
 sql,args=q.selection('INTC',seq,'ALL',days,db=db)
 result[scope+'_plan']=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql,args)]
 for name,query in [('catalog',q._catalog('INTC',seq,'ALL',db)),('changes',q._objects(db,'INTC',seq,'policy_change',"route,json_extract(payload,'$.type') AS type",days)),('positive',q._positive(db,'INTC',seq,days)),('combined',(sql,args))]:
  s,a=query;t=time.monotonic();db.set_progress_handler(lambda:int(time.monotonic()-t>2),1000)
  try:row=db.execute('SELECT count(*) FROM ('+s+')',a).fetchone()[0];err=None
  except Exception as e:row=None;err=str(e)
  result['parts'].append(dict(scope=scope,part=name,ms=round((time.monotonic()-t)*1000,2),count=row,error=err))
  db.set_progress_handler(None,0)
print(json.dumps(result))
