import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

ROOT = Path(__file__).parent
x = json.loads((ROOT / 'inventory.json').read_text())
groups = {
    'GLOBAL': 'access_newswire_massive barrons_ticker_news benzinga_news bloomberg_massive businesswire_massive calcalist_search corporate_ir_press_releases finnhub_company_news globenewswire_massive globenewswire_search google_news_search_rss ibkr_news investing_ticker_news investorshub_ticker_news newswire_rss prnewswire_massive reuters_site_search sec_edgar stocktwits_messages thefly_ticker_news tmx_newsfile_massive x_official_posts yahoo_finance_news',
    'L1-01': 'ctee_semiconductor digitimes_semiconductors digitimes_tw_rss etnews_rss huggingnews_rss nbis_official_actor_rss optical_systems_official_rss siliconangle_ai technews_ai technews_hardware technews_semiconductor thelec_semiconductors_rss tomshardware_rss trendforce_news trendforce_press_releases venturebeat_ai wccftech_ai wccftech_hardware wccftech_industry',
    'L1-02': 'aws_platform_marketplace_rss crn_cloud crn_cloud_security_datacenter crn_data_center crn_security enterprise_apps_official_rss ionq_nautilus_rss ionq_quantum_actors_rss lakehouse_platform_official_rss national_quantum_initiative observability_vendors_official_rss quantum_computing_report quantum_insider quantum_insider_business quantum_insider_capital_markets quantum_insider_national quantum_zeitgeist register_cloud register_security register_software register_software_cloud_security siliconangle_big_data siliconangle_cloud siliconangle_security siliconangle_software venturebeat_data venturebeat_orchestration venturebeat_security venturebeat_software',
    'L1-03': 'ans_nuclear_newswire canary_media dcd_energy_ticker doe_energy_news nrc_event_notifications nrc_news_releases power_engineering_energy power_magazine_energy utility_dive world_nuclear_news',
    'L1-05': 'airspaceforces_space army_news_releases army_news_rss aviationweek_rocketlab aviationweek_rss breakingdefense_space defensescoop_drones defensescoop_space dod_contracts dod_releases iridium_press_releases nasa_2026_releases nro_press_releases shephard_uncrewed spacecom_launches spacenews_rss ssc_newsroom unmannedairspace_counteruas',
}
mapping = {}
for sector, names in groups.items():
    for sid in names.split():
        assert sid not in mapping
        mapping[sid] = [] if sector == 'GLOBAL' else [sector]
mapping.update({
    'darpa_news': ['L1-05', 'L1-01', 'L1-02'],
    'nist_news': ['L1-02', 'L1-01', 'L1-08'],
    'nsf_news': ['L1-02', 'L1-01', 'L1-06', 'L1-08'],
    'data_center_frontier': ['L1-01', 'L1-03'],
    'dcd_infrastructure_rss': ['L1-01', 'L1-03'],
    'sdxcentral_news': ['L1-02', 'L1-01'],
})
for sid in ['siliconangle_ai', 'venturebeat_ai', 'huggingnews_rss', 'nbis_official_actor_rss', 'aws_platform_marketplace_rss']:
    mapping[sid] = ['L1-01', 'L1-02']
assert {s['id'] for s in x['sources']} == mapping.keys()
p = Path('C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/all_message_sources_catalog_execution_plan_20261112.md')
s = p.read_text(encoding='utf-8')
s = s.replace('不引入 L2、Ticker→行业推断或 L2 维护系统。', '不引入 L2、Ticker→行业推断或 L2 维护系统。L1-13是产业类别，不能当所有政府/政策消息源的兜底分类。')
s += '\n| source_id | 建议适用性 / L1（首项为展示区） | 实际订阅数 | 当前状态 |\n| --- | --- | ---: | --- |\n'
for source in sorted(x['sources'], key=lambda v: v['id']):
    sid = source['id']
    n = sum(b['source_id'] == sid for b in x['bindings'])
    status = '停用' if not source['enabled'] else '测试' if source['delivery_mode'] == 'test' else '正式'
    s += f"| {sid} | {', '.join(mapping[sid]) or 'GLOBAL'} | {n} | {status} |\n"
p.write_text(s, encoding='utf-8')
book = Path('C:/Users/WEIXUANXIE/Desktop/DoxAgent_板块地图_L1-L2_V5.xlsx')
ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
with zipfile.ZipFile(book) as z:
    sheet = E.fromstring(z.read('xl/workbook.xml')).find('x:sheets/x:sheet', ns).attrib['name']
    strings = [''.join(si.itertext()) for si in E.fromstring(z.read('xl/sharedStrings.xml')).findall('x:si', ns)]
    rows = []
    for row in E.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('x:sheetData/x:row', ns):
        if 16 <= int(row.attrib['r']) <= 28:
            cells = {}
            for c in row.findall('x:c', ns):
                v = c.find('x:v', ns)
                if v is not None and c.attrib['r'][0] in 'ABC':
                    cells[c.attrib['r']] = strings[int(v.text)] if c.attrib.get('t') == 's' else v.text
            rows.append(cells)
(ROOT / 'l1_workbook.json').write_text(json.dumps({
    'file': str(book), 'sha256': hashlib.sha256(book.read_bytes()).hexdigest(),
    'sheet': sheet, 'range': 'A16:C28', 'rows': rows,
}, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'L1': len(rows), 'sources': len(mapping), 'plan_lines': len(s.splitlines())}))
