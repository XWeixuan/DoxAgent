"""Manually authored candidate recipes. Running this only writes a NEW candidate."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from ops import DATA, HERE, read, write


def term(pattern, field="all"):
    return {"field": field, "regex": pattern}


def group(name, pattern, field="all", require=None, veto=None):
    return {"id": name, "any": [term(pattern, field)],
            "all": [term(p, f) for p, f in (require or [])],
            "none": [term(p, f) for p, f in (veto or [])]}


DC = r"\bdata cent(?:er|re)s?\b"
CAPACITY = r"\b(?:\d[\d,.]*[ -]?(?:MW|GW|petaflops?)|[\d,]{3,} (?:NVIDIA )?GPUs?)\b"
DC_PROJECT = rf"(?:{DC}[^.\n]{{0,180}}{CAPACITY}|{CAPACITY}[^.\n]{{0,180}}{DC})"
DC_MARKET = r"\bdata cent(?:er|re) (?:market|facility management|and AI infrastructure market)\b"
COMPUTE_DEPLOY = r"\b(?:purchas\w*|acquir\w*|deploy\w*|order\w*|expand\w*|lease\w*|contract\w*)\b[^.\n]{0,100}\b(?:[BH][123]\d{2}|GPU (?:clusters?|systems?|capacity)|AI (?:compute|supercomput\w*))\b"


def dc_groups():
    return [group("compute_facilities_market", DC_MARKET, "title"),
            group("capacity_project", DC_PROJECT), group("compute_deployment", COMPUTE_DEPLOY)]


FIRST = {
"INTC": [
    group("processor_subject", r"\b(?:Intel|INTC|Xeon|AMD|EPYC|Ryzen|CPUs?|ASIC|industrial edge computing|semiconductor metrology|semiconductor assembly|FOUP|Lam Research|ASML)\b", "title"),
    group("processor_fact", r"\b(?:Xeon|Ryzen|Snapdragon|Intel Foundry|CoWoS|advanced (?:chip )?packaging|logic (?:base dies|foundr\w*))\b"),
    group("edge_compute_deployment", r"\b(?:Raspberry Pi|AI processors?)\b", "all", require=[(r"\b(?:integrat\w*|computing|deploy\w*)\b", "title")]),
] + dc_groups(),
"MU": [
    group("memory_subject", r"\b(?:Micron|Crucial|DRAM|HBM\w*|NAND|SSD|memory-centric|in-memory comput\w*|FOUP)\b", "title"),
    group("memory_business_fact", r"\b(?:HBM\d*\w*|NAND|DRAM|SanDisk|memory applications|SSD)\b[^.\n]{0,100}\b(?:demand|revenue|growth|shortage|supply|packaging|inspection|retire\w*|refresh\w*)\b"),
    group("memory_resource_risk", r"\b(?:hardware and memory|memory and hardware)\b", "body"),
] + dc_groups(),
"BE": [
    group("power_subject", r"\b(?:Bloom Energy|FuelCell Energy|FCEL|fuel cells?|distributed energy|DERMS|grid (?:balancing|modernization|edge)|battery storage|energy storage system|renewable power|nuclear (?:power|decommissioning|fusion)|NANO Nuclear|power applications|SMR|solar|Shoals|ARRAY Technologies)\b", "title"),
    group("stationary_supply_fact", r"\b(?:onsite|on-site|distributed|temporary) (?:power|generation)|\b(?:fuel.cell|electrolys\w*|electrolyz\w*|power plants?|electricity generation|transmission projects?)\b", "body"),
    group("grid_investment", r"\b(?:grid resilience|grid investment|transmission and distribution|renewable energy)\b[^.\n]{0,100}\b(?:billion|million|invest\w*|fund\w*|project\w*)\b", "body"),
] + dc_groups(),
"COHR": [
    group("optical_subject", r"\b(?:Coherent(?: Corp\w*)?|MACOM|photonic\w*|optical (?:fiber|fibre|cable|interconnect)|data center interconnect|industrial lasers?|silicon carbide substrate)\b", "title", veto=[(r"Coherent Market Insights", "title")]),
    group("optical_fact", r"\b(?:silicon photonics|photonic (?:quantum|integrated|comput\w*)|CPO (?:optical|modules?)|optical (?:transceivers?|interconnects?|connectivity)|wavelength services|speciali[sz]ed lasers?)\b", "body"),
] + dc_groups(),
"NBIS": [
    group("cloud_subject", r"\b(?:Nebius|NBIS|CoreWeave|HIVE|Hut 8|Bitdeer|Runpod|Rackspace|Together AI|SUSE)\b", "title"),
    group("ai_facility_subject", r"\b(?:data cent(?:er|re)|GPU cloud|AI infrastructure|GPU optimization|KV cache)\b", "title", veto=[(r"\b(?:access control|power IC|transformer|inverter|power challenge|connectivity|copper|names new|congratulat\w*)\b", "title")]),
    group("cloud_supply_fact", r"\b(?:GPU (?:cloud|deployment)|petaflop|AI GridWorks|hyperscale computing infrastructure|data center joint venture|datacenter joint venture|datacenter-joint venture|datacenter joint-venture|datacenter-joint-venture|datacenteractiviteiten)\b", "body"),
    group("cloud_refresh", r"\bhyperscal\w*\b[^.\n]{0,100}\b(?:GPU|refresh|replac\w*)\b", "body"),
] + dc_groups(),
"CEG": [
    group("electricity_subject", r"\b(?:Constellation Energy|Calpine|nuclear (?:power|phase|spent fuel|grade|waste|fusion)|uranium|SMR|power quality|energy storage|battery energy storage|net-zero energy buildings|renewable power|HVDC|Chesapeake Utilities)\b", "title"),
    group("grid_business", r"\b(?:grid moderni[sz]ation|grid resilience|electricity prices?|electricity generation|electricity distribution|power prices?|power plants?)\b", "body"),
],
"RKLB": [
    group("space_subject", r"\b(?:Rocket Lab|RKLB|Firefly Aerospace|AST SpaceMobile|ASTS|Viasat|Gilat|Globalstar|GHGSat|space (?:militarization|logistics|technology)|satellite intelligence|satellite-based|Telespazio|Rivada|Maritime Launch|NASA)\b", "title"),
    group("space_contract_fact", r"\b(?:satellite constellations?|satellites? (?:launch\w*|manufactur\w*)|space (?:data layer|force)|(?:launched|launching) into orbit|Zhuque-3)\b", "body"),
],
"SNOW": [
    group("data_platform_subject", r"\b(?:Snowflake|SNOW|Databricks|MongoDB|Teradata|Percona|Denodo|CData|Qlik|Talend|data integration|data warehouse|data lakehouse|enterprise database)\b", "title"),
    group("platform_integration", r"\b(?:Snowflake|Databricks)\b[^.\n]{0,100}\b(?:platforms?|integrat\w*|Cortex|partner\w*|deploy\w*)\b", "body"),
    group("enterprise_data_architecture", r"\b(?:unified data layer|data silos|fragmented data|master data|data interoperability|cross-system data)\b", "body"),
],
"ONDS": [
    group("unmanned_subject", r"\b(?:Ondas|ONDS|Airobotics|American Robotics|Sentrycs|Optimus|DJI|Matrice|ZenaTech|ParaZero|ScoutDI|SPARC AI|drone\w*|UAV|UAS|unmanned aerial|counter-UAS|counter-drone|defence robotics)\b", "title", veto=[(r"\b(?:helicopter tours|maritime systems|water drone)\b", "title")]),
    group("unmanned_business_fact", r"\b(?:drone|UAV|unmanned aerial)\w*\b[^.\n]{0,90}\b(?:manufactur\w*|procure\w*|contract\w*|swarm\w*|airframes?|propulsion|training|qualified supplier|program\w*)\b", "body"),
    group("private_industrial_network", r"\b(?:FullMAX|IEEE 802\.16t|railway private network|railroad wireless network|private LTE|private 5G)\b", "title"),
],
}


def create(ticker, attempt, groups, rationale):
    destination = HERE / ticker / "regex" / attempt / "candidate.json"
    assert not destination.exists(), "Never overwrite a frozen/tested candidate"
    candidate = read(DATA / "baseline-terms" / f"{ticker}.json")
    candidate["l2"] = {"en": {"groups": groups}}
    write(destination, candidate)
    write(destination.parent / "design.json", {"rationale": rationale, "usage": "offline Massive Distribution only; English fallback includes observed wire vocabulary; not common production L2"})


if __name__ == "__main__":
    for ticker, groups in FIRST.items():
        create(ticker, "attempt-01", groups, "Start with a few identifiable product markets, subject-level entities, and concrete compute-capacity facts. Remove broad all-industry matches and name-list-only recall. No genre-wide veto.")
