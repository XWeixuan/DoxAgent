"""Bounded real feed tests; does not update source configuration or publish articles."""
import asyncio
import json
from xml.etree import ElementTree as ET

import httpx


async def main():
    rows = []
    for name,url in [('yahoo','https://finance.yahoo.com/rss/headline?s=MU'),
                     ('huggingnews','https://huggingnews.com/feed.xml'),
                     ('be_ir','https://investor.bloomenergy.com/rss/pressrelease.aspx')]:
        for port in (18083,18085):
            row = {'source':name,'port':port}
            try:
                async with httpx.AsyncClient(proxy=f'http://doxagent-egress-clash:{port}',timeout=12,follow_redirects=True) as client:
                    r = await client.get(url)
                row['status'] = r.status_code
                if r.status_code == 200:
                    root = ET.fromstring(r.text)
                    row['items'] = len(root.findall('.//item')+root.findall('.//{*}entry'))
                    row['xml_valid'] = root.tag.rsplit('}',1)[-1] in ('rss','feed','RDF')
            except Exception as exc:
                row['error'] = type(exc).__name__
            rows.append(row)
    print(json.dumps(rows))


asyncio.run(main())
