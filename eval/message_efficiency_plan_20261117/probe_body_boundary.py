"""Two ordinary public HTTP reads to inspect actual extraction boundaries."""
import asyncio, json
from datetime import UTC, datetime
from lxml import html
from doxagent.content_enrichment.quality import inspect_html, choose_candidate
from doxagent.message_bus_v2.technology_sources import TechnologyHttp, SOURCES as TECHNOLOGY
from doxagent.message_bus_v2.energy_sources import EnergyHttp
from doxagent.message_bus_v2.energy_catalog import SOURCES as ENERGY

URLS = [
    'https://quantumzeitgeist.com/quantum-x-labs-99-93-data-recovery/',
    'https://www.datacenterdynamics.com/en/news/airsys-invests-in-digital-boiler-startup-thermai/',
]
async def main():
    tech = TechnologyHttp(purpose='BODY')
    energy = EnergyHttp()
    out = {'at': datetime.now(UTC).isoformat(), 'pages': []}
    try:
        for url in URLS:
            row = {'url': url}
            try:
                client, spec = (tech, TECHNOLOGY['quantum_zeitgeist']) if 'quantumzeitgeist' in url else (energy, ENERGY['dcd_infrastructure_rss'])
                r = await client.fetch(url, spec)
                row.update(status=r.status, bytes=len(r.text.encode()), route=spec.route,
                           transport=getattr(spec, 'transport', 'energy'))
                if r.status == 200:
                    root = html.fromstring(r.text)
                    title = ' '.join(root.xpath('//h1//text()')).strip()
                    row['title'] = title
                    info = inspect_html(r.text, url, title, strategy_parameters={'body_xpath':[
                        '//*[@itemprop="articleBody"]',
                        '//*[contains(concat(" ",normalize-space(@class)," ")," entry-content ")]',
                        '//*[contains(concat(" ",normalize-space(@class)," ")," single-post-content ")]']})
                    chosen, outcome, reason = choose_candidate(info, title)
                    row['chosen'] = {'outcome':outcome, 'reason':reason,
                        'chars':len(chosen.text) if chosen else 0}
                    row['candidates'] = [c.summary() for c in info.candidates]
                    nodes = root.xpath('//*[not(*) and (starts-with(normalize-space(.),"More in ") or starts-with(normalize-space(.),"See today"))]')
                    row['boundaries'] = []
                    for node in nodes[:8]:
                        ancestors = []
                        for a in [node, *list(node.iterancestors())[:4]]:
                            ancestors.append({'tag':a.tag,'class':a.get('class'),'id':a.get('id')})
                        row['boundaries'].append({'text':' '.join(node.itertext())[:320], 'ancestors':ancestors})
                    row['tail'] = chosen.text[-450:] if chosen else None
            except Exception as e:
                row['error'] = type(e).__name__ + ':' + str(e)[:180]
            out['pages'].append(row)
    finally:
        await tech.close()
        await energy.close()
    print(json.dumps(out, ensure_ascii=False))
asyncio.run(main())
