from time import perf_counter
from doxagent.v2_read.repository import ReadStore
store=ReadStore('/data/read/v2.sqlite3')
with store.connect() as db: seq=store.highwater(db)
for ticker in ['BE', 'MU']:
    for q in ['hydrogen', 'OpenAI', 'no_such_runtime_title_20261009']:
        start=perf_counter()
        rows=store.page('case',ticker,seq,limit=20,q=q)
        print(ticker, q, len(rows), round((perf_counter()-start)*1000,2), 'ms')
