import sqlite3
c=sqlite3.connect('/tmp/ui-impl/isolated-bulk.sqlite3')
c.execute("ATTACH DATABASE 'file:/data/read/v2.sqlite3?mode=ro' AS origin")
for db in ['main','origin']:
 for t in ['objects','contributions']:
  print(db,t,c.execute('SELECT sql FROM '+db+'.sqlite_master WHERE name=?',(t,)).fetchone())
  print(c.execute('PRAGMA '+db+'.table_info('+t+')').fetchall())
