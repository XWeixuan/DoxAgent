import sqlite3,json
c=sqlite3.connect('file:/tmp/ui-impl/isolated-final.sqlite3?mode=ro',uri=True)
s=int(c.execute("SELECT value FROM read_meta WHERE key='highwater'").fetchone()[0])
print(json.dumps({t:c.execute('SELECT count(*) FROM '+t+' WHERE valid_to>?',(s,)).fetchone()[0] for t in ['objects','contributions']}))
