"""Read two completed stored threads, never resume/start a Turn or expose content."""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time

os.nice(19)
from doxagent.codex_worker.thread_lifecycle import AppServerThreads


async def main():
    transport=AppServerThreads();rows=[]
    try:
        await transport.ensure_initialized()
        for thread in ['01a1b01a-f471-73a3-8d55-910956f8fd74','01a1c0b6-9d39-7e92-a063-f62ad86d168d']:
            with closing(sqlite3.connect('file:/data/codex-home/state_5.sqlite?mode=ro',uri=True,timeout=.1)) as db:
                row=db.execute('SELECT rollout_path FROM threads WHERE id=?',(thread,)).fetchone()
            path=Path(row[0]) if row else None
            if not path or not path.is_file():continue
            size=path.stat().st_size
            if size>4*1024**2:
                rows.append(dict(thread_id=thread,rollout_bytes=size,full_read_skipped=True));continue
            for include in [False,True,False]:
                started=time.monotonic()
                value=await asyncio.wait_for(transport.request('thread/read',dict(threadId=thread,includeTurns=include)),10)
                elapsed=time.monotonic()-started
                rows.append(dict(thread_id=thread,rollout_bytes=size,include_turns=include,
                                 wall_ms=round(elapsed*1000,3),response_bytes=len(json.dumps(value).encode()),
                                 turns=len(value.get('thread',{}).get('turns',[]))))
    finally:await transport.close()
    print(json.dumps(dict(rows=rows,model_calls=0,resume_calls=0)))


asyncio.run(main())
