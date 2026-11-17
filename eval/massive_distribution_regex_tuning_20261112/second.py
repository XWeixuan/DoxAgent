"""Second regular recipes derived only from development errors."""
from design import *
import copy

# Punctuation and spelled-out units must not change the identity of a facility fact.
DC2 = r"\bdata[ -]cent(?:er|re)s?\b"
CAP2 = r"\b(?:\d[\d,.]*[ -]?(?:MW|GW|megawatts?|gigawatts?|petaflops?)|gigawatt-scale)\b"
FACILITY2 = rf"(?:{DC2}[^.\n]{{0,180}}{CAP2}|{CAP2}[^.\n]{{0,180}}{DC2})"
COMPUTE2 = rf"{CAP2} (?:of )?(?:AI )?compute capacity|\b[\d,]{{3,}}(?: [\w-]+){{0,3}} GPUs?\b"
OPEN_DC = rf"\b(?:opens?|opened|opening|construct\w*|commission\w*)\b[^.\n]{{0,100}}{DC2}"

for ticker in ["MU", "COHR", "NBIS"]:
    groups = copy.deepcopy(FIRST[ticker])
    for g in groups:
        if g['id'] == 'capacity_project': g['any'] = [term(FACILITY2)]
    groups += [group('compute_scale_fact', COMPUTE2), group('facility_opening', OPEN_DC)]
    if ticker == 'MU':
        for g in groups:
            if g['id'] == 'memory_business_fact':
                # Generic DRAM supply risk in an unrelated firm's disclaimer is not a memory-market report.
                g['any'] = [term(r"\b(?:HBM\d*\w*|NAND|SanDisk|SSD|solid-state drives|high-bandwidth memory)\b[^.\n]{0,100}\b(?:demand|revenue|growth|supply|packaging|inspection|retire\w*|refresh\w*|recover\w*)\b")]
        groups += [group('memory_materials_demand', r"\b(?:demand|sales|growth)\b[^.\n]{0,120}\b(?:sputtering targets|memory applications|memory and AI-related applications)\b"),
                   group('ai_server_market', r"\bAI servers? market\b", 'title')]
    elif ticker == 'COHR':
        groups += [group('optical_component_fact', r"\b(?:(?:fiber|fibre) connectivity|specialist lasers|photonics/lasers)\b", 'body')]
    else:
        # Short supplier identities are only strong if the product market is identifiable.
        groups += [group('compute_cloud_market', r"\b(?:accelerated computing|Cerebras Systems)\b", 'title'),
                   group('compute_integration_results', r"\bsystems integration revenue\b", 'body', require=[(r"\b(?:AI|high-performance computing) infrastructure\b", 'body')]),
                   group('cooling_capacity_dependency', r"\bdata center operators expand capacity\b", 'body', require=[(r"\b(?:dry cooler|cooling)\b", 'title')])]
        for g in groups:
            if g['id'] == 'cloud_supply_fact':
                g['any'] = [term(r"\b(?:GPU (?:cloud|deployment)|petaflop|AI GridWorks|hyperscale computing infrastructure|data cent(?:er|re) joint venture)\b", 'body')]
    create(ticker, 'attempt-02', groups, 'Repair observed punctuation/unit variants and genuinely substantive facility/product facts. Preserve report and activity coverage. Remove generic disclaimer memory risk; do not encode article IDs or publisher-specific lists.')
