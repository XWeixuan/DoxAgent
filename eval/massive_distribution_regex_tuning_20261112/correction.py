"""ONE post-validation correction per ticker. Authored before any corrected scores."""
from design import *
import copy

DC3 = r"\bdata[ -]?cent(?:er|re)s?\b"
UNITS = r"\d[\d,.]*\s*(?:MW(?:dc)?|GW(?:dc)?|megawatts?|gigawatts?)\b"
COMPONENTS = r"\b(?:SCADA|router|power IC|power challenge|switchgear|transformer|copper|access control|cable|connectors?|primer|power device|SiC module)\b"
FACILITY_TITLE = rf"{DC3}|\bAI factor(?:y|ies)|AI datacenter\b"
FACILITY_ACTION = r"\b(?:acqui\w*|construct\w*|develop\w*|complet\w*|plans?|planned|financ\w*|fund\w*|expan\w*|opens?|opened|campus|sites?|deploy\w*)\b"
LOCAL_ACTION = r"\b(?:opens?|opened|opening|built|builds?|building|commissioned|commissioning|under construction)\b"


def facility_groups():
    return [
        group('facility_market', DC_MARKET, 'title', veto=[(COMPONENTS, 'title')]),
        group('facility_subject_event', FACILITY_TITLE, 'title', require=[(FACILITY_ACTION, 'title')], veto=[(COMPONENTS, 'title')]),
        group('facility_capacity_progress', rf"\b(?:commission\w*|energiz\w*|contracted|operational|campus|construct\w*|deploy\w*|planning|planned|build\w*)\b[^.\n]{{0,90}}{UNITS}", 'body', require=[(DC3, 'body')], veto=[(COMPONENTS, 'title')]),
        group('facility_opening', rf"{LOCAL_ACTION}[^.\n]{{0,90}}{DC3}", 'body', veto=[(COMPONENTS, 'title')]),
        group('compute_buildout', rf"\b(?:purchas\w*|acquir\w*|deploy\w*|order\w*|lease\w*)\b[^.\n]{{0,85}}\b(?:NVIDIA|Blackwell|Rubin|[BGH][123]\d{{2}}|rack-scale systems?|GPU clusters?)\b", 'body'),
        group('compute_finance_capacity', r"\bfinancing\b[^.\n]{0,100}\bAI compute capacity\b|\b(?:\d[\d,.]*\s*(?:MW|GW) (?:of )?(?:AI )?compute capacity|[\d,]{3,}(?: [\w-]+){0,3} GPU(?:s| servers))\b", 'body'),
    ]


def previous(ticker):
    frozen = read(HERE / ticker / 'regular-freeze.json')
    return copy.deepcopy(read(HERE / ticker / 'regex' / frozen['development_best'] / 'candidate.json')['l2']['en']['groups'])


FINAL = {}
FINAL['INTC'] = [
    group('processor_subject', r"\b(?:INTC|Xeon|AMD|EPYC|Ryzen|CPUs?|ASIC|industrial edge computing|semiconductor metrology|semiconductor assembly|FOUP|Lam Research|ASML|silicon wafers|data center accelerators)\b", 'title'),
    group('intel_subject', r"\bIntel\b", 'title', veto=[(r"\b(?:FinTech Intel|market intel|sports intel)\b", 'title')]),
    group('processor_technology_fact', r"\b(?:Xeon|Ryzen|Snapdragon|Thunderbolt [45]|CoWoS|silicon[ -]spin quantum|Blackwell[ -]class processors|x86 HPC)\b", 'body'),
    group('intel_manufacturing_fact', r"\b(?:partnership with Intel|Intel[^.\n]{0,75}(?:semiconductor|wafer|fab expansion)|capacity expansion[^.\n]{0,70}Intel)\b", 'body'),
    group('logic_packaging_fact', r"\b(?:advanced (?:chip )?packaging|logic (?:base dies|foundr\w*))\b", 'body', require=[(r"\b(?:logic|chiplet|wafer|AI processor)\b", 'body')], veto=[(r"\b(?:IGBT|power device|power module)\b", 'title')]),
    group('edge_compute_deployment', r"\b(?:Raspberry Pi|AI processors?)\b", 'body', require=[(r"\b(?:integrat\w*|computing|deploy\w*)\b", 'title')]),
    group('facility_construction_policy', rf"(?:\b(?:freeze|moratorium|ban|restrictions?)\b[^.\n]{{0,80}}{DC3}|{DC3}[^.\n]{{0,80}}\b(?:freeze|moratorium|ban|restrictions?)\b)", 'body'),
] + facility_groups()

FINAL['MU'] = [g for g in previous('MU') if g['id'] not in {'compute_facilities_market','capacity_project','compute_deployment','compute_scale_fact','facility_opening'}] + facility_groups() + [
    group('memory_economics', r"\b(?:memory (?:products|components|costs|prices|supply|demand)|external memory)\b", 'all', require=[(r"\b(?:short supply|up in price|higher memory costs|memory costs|without external memory|memory footprint)\b", 'body')]),
    group('memory_bandwidth_requirement', r"\bhigh[ -]bandwidth memory\b", 'body'),
    group('packaging_process_equipment', r"\b(?:advanced packaging|memory manufacturing)\b[^.\n]{0,100}\b(?:inspection|process control|revenue)\b|\b(?:inspection|process control)\b[^.\n]{0,100}\b(?:advanced packaging|memory manufacturing)\b", 'body'),
    group('cloud_capacity_economics', r"\b(?:private|public) cloud revenue\b", 'body', require=[(r"\b(?:capital expend\w*|AI infrastructure)\b", 'body')]),
    group('compute_project_inventory', rf"\b[\d,]+(?: U\.S\.)? {DC3[2:-2]} projects (?:tracked|in development)\b", 'body'),
]

MOBILE = r"\b(?:hydrogen bus|transit bus|yachts?|portable power|camping|marine propulsion|nuclear diamond batteries|spent (?:nuclear )?fuel|uranium enrichment)\b"
FINAL['BE'] = [
    group('stationary_supplier_subject', r"\b(?:Bloom Energy|FuelCell Energy|FCEL|NANO Nuclear)\b", 'title'),
    group('power_market_subject', r"\b(?:distributed energy|DERMS|grid (?:balancing|edge)|battery storage|energy storage system|renewable power|nuclear (?:power market|power plant equipment|decommissioning|fusion)|power applications|SMR|Shoals|ARRAY Technologies|NatGas To Power|flywheel|power generation assets|PJM)\b", 'title', veto=[(MOBILE, 'title')]),
    group('stationary_supply_fact', r"\b(?:(?:onsite|on-site|on site|distributed|behind-the-meter|baseload|temporary) (?:power|generation)|fuel[ -]?cells?|electrolys\w*|electrolyz\w*)\b", 'body', veto=[(MOBILE, 'title')]),
    group('generation_project_fact', r"\b(?:construct\w*|acquir\w*|permit\w*|operat\w*)\b[^.\n]{0,80}\b(?:power plants?|power stations?|solar farms?)\b", 'body', veto=[(MOBILE, 'title')]),
    group('grid_commitment_fact', r"\b(?:grid resilience|transmission projects?|transmission and distribution)\b[^.\n]{0,80}\b(?:billion|million|invest\w*|fund\w*)\b", 'body'),
    group('large_load_outlook', rf"{DC3} electricity consumption|\b(?:power supplies available|behind-the-meter generation|on-site generation)\b", 'body'),
] + facility_groups()

FINAL['COHR'] = [g for g in previous('COHR') if g['id'] not in {'compute_facilities_market','capacity_project','compute_deployment','compute_scale_fact','facility_opening','optical_fact'}] + [
    group('optical_platform_fact', r"\b(?:silicon photonics|photonic (?:quantum|integrated|comput\w*)|CPO (?:optical|modules?)|wavelength services)\b", 'body'),
    group('optical_product_event', r"\b(?:launch\w*|introduc\w*|showcas\w*|demonstrat\w*|demand for|growth in)\b[^.\n]{0,100}\b(?:optical (?:transceivers?|interconnects?)|fiber connectivity)\b", 'body'),
] + facility_groups()

FINAL['NBIS'] = [g for g in previous('NBIS') if g['id'] not in {'ai_facility_subject','compute_facilities_market','capacity_project','compute_deployment','compute_scale_fact','facility_opening'}] + [
    group('facility_stack_subject', r"\b(?:AI datacenter|GPU cloud|KV cache|Ethernet switch)\b", 'title'),
    group('cloud_hardware_dependency', r"\b(?:high-density inference infrastructure|interconnection studies|validate full-scale[^.\n]{0,35}racks)\b", 'body', require=[(r"\b(?:AI|data cent(?:er|re))\b", 'body')]),
] + facility_groups()

NUKE = r"\b(?:advanced nuclear|nuclear (?:power|energy|phase|spent fuel|grade|waste|fusion|reactor)|uranium|SMR|Xe-100)\b"
OFF_EARTH = r"\b(?:lunar|space-based|aerospace)\b"
FINAL['CEG'] = [
    group('generator_subject', r"\b(?:Constellation Energy|Calpine|Duke Energy|Vistra|NRG Energy|NextEra|Chesapeake Utilities)\b", 'title'),
    group('electricity_market_subject', r"\b(?:power quality|energy storage|battery energy storage|net-zero energy buildings|renewable power|HVDC|solar energy sites)\b", 'title'),
    group('nuclear_market_subject', NUKE, 'title', veto=[(OFF_EARTH, 'title')]),
    group('nuclear_project_fact', r"\b(?:advanced nuclear reactors?|small modular (?:nuclear )?reactors?|Xe-100|nuclear island)\b", 'body', veto=[(OFF_EARTH, 'title')]),
    group('generation_construction', r"\b(?:solar PV project|solar farms?|power station|power plant construction|electricity prices?)\b", 'body', veto=[(OFF_EARTH, 'title')]),
    group('grid_committed_funding', r"\b(?:grid resilience|electricity distribution|grid moderni[sz]ation)\b[^.\n]{0,85}\b(?:billion|million|invest\w*|fund\w*)\b", 'body'),
    group('large_demand_project', rf"{UNITS}[^.\n]{{0,100}}{DC3}|{DC3}[^.\n]{{0,100}}{UNITS}|centre de donn[eé]es modulaire de plusieurs m[eé]gawatts", 'body'),
]

FINAL['RKLB'] = previous('RKLB') + [
    group('space_product_subject', r"\b(?:lunar economy|space-based solar power|satellite manufacturing|Starlink|SpaceX|Space42|ICEYE|space solutions)\b", 'title'),
    group('orbital_service_fact', r"\b(?:hardware in orbit|data center satellites|satellite (?:network|systems?))\b", 'body', require=[(r"\b(?:inspect|repair|manufactur\w*|launch\w*|test\w*|demand|growth|growing)\b", 'body')]),
    group('space_component_market', r"\b(?:space and satellite systems|defense rocket systems)\b", 'body', require=[(r"\b(?:growth|growing|demand|revenue)\b", 'body')]),
]

FINAL['SNOW'] = previous('SNOW') + [
    group('data_platform_extension', r"\b(?:Redis|IIoT data platform|industrial data platform|product information management|data readiness)\b", 'title'),
    group('platform_deployment_fact', r"\b(?:enterprise data and AI platforms|context lives across|meter data management)\b", 'body', require=[(r"\b(?:Snowflake|Databricks|Oracle)\b", 'body')]),
    group('real_time_data_integration', r"\b(?:real-time|centralized|cross-system|governed)\b[^.\n]{0,45}\bdata integration\b|\bproduct information management \(PIM\)\b", 'body'),
]

FINAL['ONDS'] = [g for g in previous('ONDS') if g['id'] != 'unmanned_business_fact'] + [
    group('counter_unmanned_systems', r"\b(?:counter[ -]?(?:drone|UAS|UAV)|Dedrone|drone as first responder)\b", 'body'),
    group('ground_unmanned_subject', r"\b(?:unmanned ground vehicles|loitering munitions|robot(?:ic)?[^.\n]{0,25}(?:delivery|logistics)|autonomous mobile robot|private wireless|Anterix)\b", 'title'),
    group('unmanned_technical_fact', r"\b(?:quadrotors?|quadcopters?|pilotless[^.\n]{0,45}aircraft|uncrewed aircraft|drone swarm)\b", 'body'),
    group('unmanned_business_fact', r"\b(?:drone|UAV)\w*\b[^.\n]{0,85}\b(?:manufacturing capacity|selected|funding|procurement|DARPA|test\w*|flight|program)\b|\b(?:qualified|selected|funded|developed|tested)\b[^.\n]{0,85}\b(?:drone|UAV)\w*\b", 'body'),
]


if __name__ == '__main__':
    for ticker, groups in FINAL.items():
        count = len(read(HERE / ticker / 'regular-freeze.json')['candidates'])
        attempt = f'attempt-{count + 1:02d}-overfit'
        create(ticker, attempt, groups, 'One permitted correction: use complete product-market scope, substantive local predicates, physical units and field boundaries. Retain reasonable adjacent markets despite disputed GOLD; no article or source blacklist. All nine corrected recipes frozen together before corrected scoring.')
    write(HERE / 'correction-freeze-all.json', {'frozen_at': __import__('ops').now(), 'candidates': {t: __import__('ops').sha(HERE / t / 'regex' / f"attempt-{len(read(HERE / t / 'regular-freeze.json')['candidates']) + 1:02d}-overfit" / 'candidate.json') for t in FINAL}})
