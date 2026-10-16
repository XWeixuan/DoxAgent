import sqlite3,json
p='/data/runtime/runtime.sqlite3'
d=sqlite3.connect('file:'+p+'?mode=ro',uri=True);d.execute('PRAGMA query_only=ON')
print(json.dumps({'recent_cases':d.execute("SELECT COUNT(*) FROM runtime_v2_cases WHERE julianday(created_at)>julianday('now','-30 minutes')").fetchone()[0], 'recent_effects':d.execute("SELECT e.effect_type,e.status,COUNT(*) FROM runtime_v2_effects e JOIN runtime_v2_cases c USING(case_id) WHERE julianday(c.created_at)>julianday('now','-30 minutes') GROUP BY 1,2").fetchall()}))
