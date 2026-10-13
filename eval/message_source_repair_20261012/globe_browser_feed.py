"""One owner-runtime check of the exact official feed, not a replacement feed."""
import asyncio
import json
from xml.etree import ElementTree as ET

import httpx
from lxml import html

from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token


async def main():
    settings = DoxAgentSettings()
    url = 'https://www.globenewswire.com/RssFeed/industry/9576-Semiconductors/feedTitle/GlobeNewswire%20-%20Industry%20News%20on%20Semiconductors'
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8011', timeout=65, headers={'Authorization': 'Bearer ' + read_token(settings.site_access_worker_token, settings.site_access_worker_token_file)}) as client:
        response = await client.post('/v1/access/execute', json={'operation_id': 'globe-feed-canary-20261012', 'purpose': 'CRAWLER', 'mode': 'BROWSER', 'url': url, 'remaining_budget_ms': 45000})
        result = response.json()
        output = {k: result.get(k) for k in ('disposition', 'reason_code', 'status_code', 'failure_category', 'combination_id', 'profile_id', 'retry_not_before')}
        output['xml_valid'] = False
        try:
            root = ET.fromstring(result.get('body', ''))
            output['items'] = len(root.findall('.//item') + root.findall('.//{*}entry'))
            output['xml_valid'] = root.tag.rsplit('}', 1)[-1] in ('rss', 'feed', 'RDF') and output['items'] > 0
        except ET.ParseError:
            tree = html.fromstring(result.get('body') or '<html/>')
            output['document_shape'] = tree.xpath('//*[@id]/@id')[:15]
            output['document_text_prefix'] = tree.text_content()[:500]
            output['embedded_items'] = len(tree.xpath('//*[local-name()="item"]'))
            try:
                root = ET.fromstring(tree.text_content().strip())
                output['items'] = len(root.findall('.//item') + root.findall('.//{*}entry'))
                output['xml_valid'] = root.tag.rsplit('}', 1)[-1] in ('rss', 'feed', 'RDF') and output['items'] > 0
                output['xml_transport'] = 'browser_text_wrapper'
            except ET.ParseError:
                pass
        print(json.dumps(output))


asyncio.run(main())
