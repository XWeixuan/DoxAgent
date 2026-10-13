"""Bounded owner-controlled article checks; never publishes diagnostic articles."""
import asyncio
import json

from doxagent.content_enrichment.managed import SiteAccessBrowserReader, SiteAccessTransport
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token

ARTICLES = {
    'barrons': [
        'https://www.barrons.com/articles/memory-chip-stocks-drop-cxmt-threat-51baf6e0',
        'https://www.barrons.com/articles/stock-market-things-to-know-today-earnings-oil-b765556b',
    ],
    'digitimes_tw': [
        'https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000771083_K5G1O9RQ7T2SFK4PE9G7V',
        'https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000770437_ONE3KFVK4VG0QU672XFCB',
    ],
    'investorshub': [
        'https://investorshub.advfn.com/stock-market/NYSE/bloom-energy-BE/stock-news/99474508/bloom-energy-to-announce-third-quarter-2026-financ',
        'https://investorshub.advfn.com/stock-market/NASDAQ/intel-INTC/stock-news/99471515/ai-revenue-questions-pull-semiconductor-stocks-off',
    ],
}
TITLES = {
    ARTICLES['barrons'][0]: 'Micron Stock Is Falling. A Chinese Rival Is Muscling In on Its Turf.',
    ARTICLES['barrons'][1]: 'Put the Bull Market’s 4th Birthday Celebrations on Hold',
    ARTICLES['digitimes_tw'][0]: 'DIGITIMES Today：Vicor 再告台廠 鴻海、廣達、台達電入列 | 華為供應東協資料中心設備 |友達轉型邁入新階段',
    ARTICLES['digitimes_tw'][1]: 'DIGITIMES Today：Musk證實與台積電洽談德州廠 | 南韓面板雙雄迎戰紅色供應鏈 | 中國十一手機消費觀察',
    ARTICLES['investorshub'][0]: 'Bloom Energy to Announce Third Quarter 2026 Financial Results on October 22, 2026',
    ARTICLES['investorshub'][1]: 'AI Revenue Questions Pull Semiconductor Stocks Off October Highs',
}


async def main():
    settings = DoxAgentSettings()
    client = SiteAccessClient('http://127.0.0.1:8011', token=read_token(settings.site_access_worker_token, settings.site_access_worker_token_file))
    transport = SiteAccessTransport(client)
    pipeline = ArticlePipeline(transport, browser=SiteAccessBrowserReader(client, transport), site_client=client, pipeline_version='body_v2.2')
    try:
        for source, urls in ARTICLES.items():
            for url in urls:
                try:
                    async with asyncio.timeout(90):
                        result = await pipeline.extract(MediaEnrichmentRecord('acceptance', 'acceptance', source, 'MU', TITLES[url], None, url))
                    payload = result.to_payload()
                    print(json.dumps({'source': source, 'url': url, 'outcome': result.diagnostics.get('outcome'), 'payload': payload}), flush=True)
                except Exception as exc:
                    print(json.dumps({'source': source, 'url': url, 'error': type(exc).__name__, 'reason': str(exc)[:200]}), flush=True)
    finally:
        await client.close()


asyncio.run(main())
