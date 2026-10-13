"""Disposable lifecycle probe: synthetic initialization only, no business data."""
import asyncio
import json
import tempfile
from pathlib import Path
from doxagent.codex_worker.thread_lifecycle import AppServerThreads


async def main():
    transport = AppServerThreads()
    result = {}
    try:
        cwd = str(Path('eval/w3_refactor_20261013/sdk_workspace').resolve())
        Path(cwd).mkdir(exist_ok=True)
        if cwd:
            started = await transport.request('thread/start', {'cwd': cwd, 'approvalPolicy': 'never', 'sandbox': 'read-only', 'config': {'web_search': 'disabled'}})
            parent = started['thread']['id']
            result['start'] = parent
            result['model'] = started.get('model')
            await transport.request('turn/start', {'threadId': parent, 'input': [{'type': 'text', 'text': 'Synthetic SDK lifecycle check. Do not use tools. Return only {"ready":true}.'}], 'effort': 'low'})
            for _ in range(60):
                try:
                    read = await transport.request('thread/read', {'threadId': parent, 'includeTurns': True})
                except Exception as exc:
                    if 'is empty' not in str(exc) and 'no rollout found' not in str(exc):
                        raise
                    await asyncio.sleep(1)
                    continue
                turns = read['thread'].get('turns', [])
                if turns and turns[-1]['status'] in ('completed', 'failed', 'interrupted'):
                    result['initialization'] = turns[-1]['status']
                    break
                await asyncio.sleep(1)
            else:
                raise TimeoutError('synthetic initialization did not finish in 60 seconds')
            forked = await transport.request('thread/fork', {'threadId': parent, 'cwd': cwd, 'approvalPolicy': 'never', 'sandbox': 'danger-full-access'})
            child = forked['thread']['id']
            result['fork'] = child
            result['read'] = (await transport.request('thread/read', {'threadId': child, 'includeTurns': False}))['thread']['id']
            result['resume'] = (await transport.request('thread/resume', {'threadId': child, 'cwd': cwd}))['thread']['id']
            result['list'] = len((await transport.request('thread/list', {'cwd': cwd, 'limit': 100}))['data'])
            result['delete_child'] = await transport.request('thread/delete', {'threadId': child})
            result['delete_parent'] = await transport.request('thread/delete', {'threadId': parent})
    except Exception as exc:
        result['error'] = repr(exc)
    finally:
        await transport.close()
    Path('eval/w3_refactor_20261013/sdk_lifecycle_receipt.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))

asyncio.run(main())
