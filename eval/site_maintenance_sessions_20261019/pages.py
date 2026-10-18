"""Observe exact retained targets, without navigating or changing browser ownership."""
import asyncio
import json
import sqlite3
from pathlib import Path
import httpx
from websockets.asyncio.client import connect
from doxagent.content_enrichment.quality import inspect_html
from doxagent.site_strategy.governance_repository import safe_display_url


async def main():
    db = sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro', uri=True)
    async with httpx.AsyncClient(trust_env=False) as http:
        for raw, in db.execute("SELECT data_json FROM maintenance_sessions WHERE state<>'CLOSED'"):
            row = json.loads(raw)
            if row['identity_id'] not in ('digitimes-de-1', 'reuters-backup'):
                continue
            manifest = json.loads((Path('/run/doxagent-chrome/instances') / (row['identity_id']+'.json')).read_text())
            port = manifest['port']
            if not port:
                print('manifest_keys', list(manifest))
                continue
            targets = (await http.get(f'http://127.0.0.1:{port}/json/list')).json()
            target = next((t for t in targets if t['id'] == row['target_id']), None)
            result = {'identity': row['identity_id'], 'session_id': row['session_id'], 'target_found': bool(target)}
            if target:
                result.update(title=target.get('title'), url=safe_display_url(target['url']))
                async with connect(target['webSocketDebuggerUrl'], max_size=16*1024*1024) as ws:
                    async def send(n, method, params):
                        await ws.send(json.dumps({'id': n, 'method': method, 'params': params}))
                        while True:
                            msg=json.loads(await asyncio.wait_for(ws.recv(),5))
                            if msg.get('id') == n:
                                return msg['result']
                    doc=await send(1,'DOM.getDocument',{})
                    html=(await send(2,'DOM.getOuterHTML',{'nodeId':doc['root']['nodeId']}))['outerHTML']
                    info=inspect_html(html,target['url'],'')
                    result.update(page_kind=info.page_kind, access_reason=info.access_reason, html_bytes=len(html))
                    # Browser-generated net error names only, never full document/account content.
                    import re
                    result['network_errors']=sorted(set(re.findall(r'(?:net::)?ERR_[A-Z_]+',html)))
                    from lxml import html as parser
                    dom=parser.fromstring(html)
                    result['main_count']=len(dom.xpath('//main'))
                    result['has_search_results_heading']=bool(re.search('Search results for',dom.text_content(),re.I))
                    result['article_link_count']=len(dom.xpath('//main//a[contains(@href,"2026-")]'))
                    from urllib.parse import urlsplit, parse_qs
                    result['query_present']=bool(parse_qs(urlsplit(target['url']).query).get('query'))
            print(json.dumps(result),flush=True)


asyncio.run(main())
