"""Binding-specific business screening. Emits existing native L2 JSON only."""
import json
from pathlib import Path

from doxagent.message_bus_v2.schema import TickerSourceBinding

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / 'binding_extra_regex_20261127' / 'cases.json'

TITLE = {
 'MU': r'\b(?:Micron|Crucial|SanDisk|Kioxia|SK\s*hynix|Solidigm|Phison|Silicon Motion|Western Digital|Seagate|DRAM|NAND|HBM\d*|DDR[3-6]\w*|LPDDR\w*|SOCAMM|DIMMs?|RAM|VRAM|SSDs?|hard drives?|HDDs?|CXL|memory (?:chips?|prices?|shortage|bandwidth|capacity|market)|storage (?:chips?|prices?|drives?))\b|美光|記憶體|儲存晶片|快閃|海力士|메모리(?!\s*폼)|디램|낸드',
 'INTC': r'\b(?:Intel|INTC|Xeon|AMD|Ryzen|EPYC|CPUs?|processors?|GPUs?|Nvidia|GeForce|semiconductors?|chipmakers?|foundr\w*|wafers?|lithograph\w*|TSMC|ASML|chiplets?|advanced packaging|transistors?|x86|RISC.V|Core Ultra|MacBook|Mac mini|Mac Studio|iMac|mini.PCs?|gaming PCs?|prebuilt PCs?|motherboards?|chipsets?|laptops?|notebooks?|supercomput\w*|HPC|PC (?:makers?|market|sales|shipments?|prices?)|RTX|Radeon|Snapdragon|Nova Lake|Wildcat Lake|Zen [3-6]|DLSS|FSR|ASICs?|EDA|Synopsys|Cadence|chip (?:design|manufactur\w*)|Arm (?:chips?|servers?|architecture))\b|英特爾|輝達|處理器|晶圓|半導體|先進封裝|電晶體|台積電|인텔|프로세서',
 'COHR': r'\b(?:Coherent|COHR|Lumentum|AAOI|photonics?|lasers?|transceivers?|CPO|optical (?:interconnects?|networks?|switch\w*|communicat\w*|engines?|chips?|comput\w*)|silicon photonics|SiC substrates?|InP substrates?|Corning.{0,25}(?:AI|fiber|optical|communicat\w*))\b|矽光子|光通訊|光模組|光引擎|光互連|雷射|高意|磷化銦|碳化矽',
 'BE': r'\b(?:Bloom Energy|fuel cells?|SOFC|SOEC|electricity|power (?:plants?|grids?|supply|demand|capacity|generation|contracts?|prices?)|(?:on.site|backup) power|electric grids?|gas turbines?|nuclear|geothermal|hydrogen|PPAs?)\b|燃料電池|供電|發電|電網|電力|核電|綠電|氫能|天然氣|地熱|블룸|연료전지',
 'NBIS': r'\b(?:Nebius|NBIS|CoreWeave|Nscale|GPUaaS|neoclouds?|OpenRouter|Baseten|Together AI|Fireworks AI|SGLang|vLLM|GPU (?:cloud|rent\w*|host\w*|capacity)|hosted (?:models?|inference)|inference (?:API|engine|service|runtime|capacity|pric\w*|provider|throughput)|token (?:prices?|pricing))\b|算力租賃|托管推理|推論服務|推理服務|模型託管|GPU\s*雲|雲端\s*GPU|네비우스',
 'IONQ': r'\b(?:IonQ|ID Quantique|Oxford Ionics|Vector Atomic|qubits?|QPUs?|quantum (?:comput\w*|processors?|algorithms?|circuits?|sensors?|sensing|networks?|communicat\w*|cryptograph\w*|key distribution|error correction|memories|repeaters?|clocks?|advantage|speedup)|QKD|PQC|post.quantum|quantum.safe|trapped.ion\w*|fault.toleran\w*|atomic clocks?)\b',
}
IDENTITY = {
 'MU': r'\b(?:Micron|Crucial|SanDisk|Kioxia|SK\s*hynix)\b|美光|海力士',
 'INTC': r'\b(?:Intel|INTC|Xeon|AMD|Ryzen|EPYC|TSMC|ASML)\b|英特爾|台積電',
 'COHR': r'\b(?:Coherent|COHR|Lumentum|AAOI)\b|高意|科希倫',
 'BE': r'\bBloom Energy\b|布魯姆|블룸\s*에너지',
 'NBIS': r'\b(?:Nebius|NBIS|CoreWeave|Nscale)\b|네비우스',
 'IONQ': r'\b(?:IonQ|ID Quantique|Oxford Ionics|Vector Atomic)\b',
}
FACT = {
 'MU': r'\b(?:DRAM|NAND|HBM\d*|DDR[3-6]\w*|LPDDR\w*|SOCAMM|DIMMs?|CXL|SSDs?|VRAM)\b.{0,105}\b(?:prices?|supply|demand|capacity|bandwidth|shortage|architecture|manufactur\w*|ship\w*|launch\w*|announc\w*|market share|forecast\w*|yields?)\b|\b(?:requires?|needs?|fits?|runs?)\b.{0,70}\b\d+\s*(?:[GTM]i?B)\b.{0,25}\b(?:RAM|VRAM|memory)\b|\b(?:DRAM|RAM|NAND|memory) (?:component )?prices?\b.{0,55}\b(?:increas\w*|ris\w*|sur\w*|fall\w*|sky\w*|doub\w*)\b|(?:記憶體|DRAM|NAND|HBM|儲存晶片).{0,50}(?:價格|供應|需求|產能|頻寬|短缺|架構|製程|市占|出貨|量產)|(?:需要|配備|搭載|消耗).{0,12}\d+\s*(?:GB|TB).{0,10}(?:記憶體|VRAM)',
 'INTC': r'\b(?:CPUs?|processors?|Xeon|Ryzen|EPYC|x86|Core Ultra|chiplets?|foundry|wafer|lithography|advanced packaging)\b.{0,105}\b(?:benchmark\w*|faster|performance|cores?|prices?|launch\w*|announc\w*|manufactur\w*|capacity|yields?|fund\w*|invest\w*|export\w*|supply|shortage)\b|\b(?:GPU|Ascend|Blackwell|GB[23]\d\d|B[23]\d\d)\b.{0,90}\b(?:train\w*|pretrain\w*|deploy\w*|capacity|manufactur\w*|export\w*|controls?|ban\w*|procure\w*|orders?)\b|(?:CPU|處理器|晶圓|晶片|半導體).{0,60}(?:效能|核心|價格|供應|產能|製程|出口|禁令|管制|投資|建廠|量產)|(?:出口|禁令|管制).{0,20}晶片',
 'COHR': r'\b(?:optical (?:interconnects?|networks?|switch\w*|communicat\w*|engines?)|silicon photonics|transceivers?|lasers?|CPO|InP substrates?|SiC substrates?)\b.{0,110}\b(?:demand|supply|capacity|deploy\w*|develop\w*|manufactur\w*|launch\w*|technology|bandwidth|yields?|orders?|invest\w*|prices?|sales|revenue|grow\w*|doubl\w*)\b|(?:光通訊|光模組|光引擎|矽光子|雷射|磷化銦|碳化矽).{0,60}(?:需求|供應|產能|技術|部署|研發|頻寬|訂單|量產|價格)',
 'BE': r'\b(?:fuel cells?|electricity|power grid|power plants?|gas turbines?|geothermal|nuclear power|power (?:demand|supply|generation))\b.{0,110}\b(?:demand|supply|capacity|deploy\w*|construct\w*|build\w*|approv\w*|contracts?|prices?|orders?|invest\w*|risks?|outage|shortage)\b|(?:供電|發電|電網|電力|燃料電池|核電|地熱|氫能).{0,60}(?:需求|供應|產能|裝置|興建|建置|合約|價格|缺口|中斷|投資)',
 'NBIS': r'\b(?:inference (?:API|service|engine|runtime|throughput|provider)|GPU (?:cloud|rental|capacity)|hosted (?:inference|models?)|virtual machines?|GPU kernels?|megakernels?)\b.{0,100}\b(?:launch\w*|announc\w*|releas\w*|offer\w*|prices?|capacity|throughput|tokens?|deploy\w*|boot\w*|scale|available)\b|\b(?:models?|weights?)\b.{0,140}\b(?:API|OpenRouter|Baseten|Together AI|Fireworks|hosted|hosting)\b|\b(?:input|output) tokens?\b|\bper (?:million|1M) tokens?\b|\b(?:OpenRouter|Baseten|Together AI|Fireworks AI|SGLang|vLLM)\b.{0,100}\b(?:launch\w*|releas\w*|inference|tokens?|model|throughput|compute|serverless)\b|(?:推論|推理|GPU\s*雲|雲端\s*GPU|算力租賃|模型託管|託管模型|推論引擎).{0,60}(?:服務|平台|價格|費用|供應|上線|發布|吞吐|每秒|API)|每百萬.{0,10}(?:token|Token|詞元)',
 'IONQ': r'\b(?:qubits?|QPUs?|quantum (?:comput\w*|processors?|algorithms?|circuits?|memories|sensors?|sensing|networks?|communicat\w*|error correction|repeaters?|clocks?|speedup|advantage)|QKD|PQC|post.quantum|atomic clocks?)\b.{0,110}\b(?:fidelity|error|coherence|gate|readout|control|engineer\w*|demonstrat\w*|implement\w*|prototype|develop\w*|algorithm|comput\w*|transmi\w*|dephas\w*|entangle\w*|detect\w*|measure\w*|speedup|benchmark|fund\w*|invest\w*|launch\w*|orders?)\b',
}

# No isolated GPU/compute/API/hardware protection. A physical business object and fact must co-occur.
INFRA = (
 r'\b(?:data[ -]?cent(?:er|re)s?|server (?:farms?|clusters?)|AI infrastructure|artificial intelligence infrastructure)\b.{0,150}\b(?:construct\w*|build\w*|develop\w*|expan\w*|invest\w*|fund\w*|financ\w*|capital|capex|loans?|leases?|permits?|moratori\w*|power|electricity|capacity|land|campus|projects?)\b'
 r'|\b(?:construct\w*|build\w*|expan\w*|invest\w*|fund\w*|financ\w*|loans?|leases?|capital expenditur\w*|capex)\b.{0,150}\b(?:data[ -]?cent(?:er|re)s?|server (?:farms?|clusters?)|AI infrastructure|artificial intelligence infrastructure)\b'
 r'|\b(?:compute|computing) (?:capacity|resources?|clusters?|contracts?|agreements?)\b.{0,160}\b(?:purchase\w*|lease\w*|rent\w*|contract\w*|agreements?|deals?|cancel\w*|expir\w*|financ\w*|fund\w*|invest\w*|expand\w*)\b'
 r'|\b(?:purchase\w*|lease\w*|rent\w*|contracts?|agreements?|deals?)\b.{0,100}\b(?:GPUs?|compute|computing capacity)\b'
 r'|\b(?:multi.gigawatt|\d+(?:\.\d+)?\s*(?:MW|GW))\b.{0,80}\b(?:capacity|campus|data|power|facility|site|project|supercomput\w*)\b'
 r'|(?:資料中心|數據中心|AI\s*基礎設施|AI\s*基礎建設|伺服器叢集).{0,65}(?:興建|建置|擴建|投資|融資|貸款|供電|建設|容量|產能|禁令|限制|採購|部署|園區)'
 r'|(?:興建|建置|擴建|投資|融資|貸款|採購|部署).{0,65}(?:資料中心|數據中心|AI\s*基礎設施|AI\s*基礎建設|伺服器叢集)'
)
FACTORY = r'\b(?:construct\w*|build\w*|expand\w*|open\w*|launch\w*)\b.{0,100}\b(?:factories|factory|fabs?|manufacturing (?:facilit\w*|plants?))\b|(?:興建|新建|建置|擴建|擴廠|改建).{0,45}(?:晶圓廠|產線|生產設施|工廠)|(?:晶圓廠|工廠).{0,40}(?:興建|擴建|擴廠|建置)'
NOISE = r'\b(?:gaming chairs?|keyboards?|mice|mouse|headphones?|headsets?|monitors?|VPN deals|cordless dusters?|gift ideas|desk upgrades?|3D printers?|Wi.Fi routers?|wrist rests?|phone cases?|earbuds?)\b|記憶棉|메모리\s*폼|晶圓電鍋|耳機|鍵盤|滑鼠|詐騙|黎曼猜想|\b(?:Riemann|chatbots?|AI agents?|AI safety|AI models?|LLMs?|IPO|valuation|stock market|S&P 500|Nasdaq|Dow Jones)\b'

# Skip existing publisher navigation/related links, without changing message bodies or first-layer inputs.
FOOTER = r'(?:Earlier version from|You.re reading an older version|Continues from|Polymarket odds|延伸閱讀\s*[:：]?|文章看完覺得有幫助|If you.re looking for more[^.]{0,40}savings|Download the Tom.s Hardware app|Follow Tom.s Hardware on Google News).*(*SKIP)(*F)|(?:%s)'


def term(pattern, field='title'):
    return {'regex': pattern, 'field': field}


def main():
    global FOOTER
    data = json.loads(DATA.read_text(encoding='utf-8'))
    rules = {}
    for binding in data['bindings']:
        ticker = binding['ticker']
        source = binding['source_id']
        # The optional gate trims publisher tails once before native matching.
        FOOTER = '%s'
        groups = [{'id': 'business_subject', 'any': [term(TITLE[ticker])]},
                  {'id': 'material_business_facts', 'any': [term(FOOTER % FACT[ticker], 'body')]}]
        if ticker != 'IONQ':
            groups[0]['any'].append(term(r'\bdata[ -]?cent(?:er|re)s?\b|資料中心|數據中心'))
            groups.append({'id':'capital_and_capacity_facts','any':[term(r'\b(?:hyperscalers?.{0,80}(?:spend\w*|invest\w*|capex|debt)|(?:spend\w*|invest\w*|capex).{0,80}hyperscalers?|(?:compute|computing) (?:spend\w*|plans?|costs?|bills?|budgets?))\b'),term(FOOTER % r'\b(?:capital expenditur\w*|capex|purchase and lease commitments)\b.{0,180}\b(?:cloud|AI|AWS|Azure|GPU|server)\b|\b(?:cloud|AI|AWS|Azure|GPU|server)\b.{0,180}\b(?:capital expenditur\w*|capex|purchase and lease commitments)\b','body')]})
        if ticker in ['INTC','MU']:
            groups[0]['any'].append(term(r'\b(?:AI chips?|HBM|Blackwell|Trainium|TPUs?|Ascend|(?:on.device|local) (?:AI|inference)|(?:memory|VRAM|RAM) (?:requirement|footprint))\b|AI\s*晶片'))
        if ticker=='MU':
            groups.append({'id':'physical_memory_deployment','all':[term(r'\b\d+[ -]?(?:[GTM]i?B)\b.{0,25}\b(?:RAM|VRAM|memory)\b|\b(?:RAM|VRAM|memory)\b.{0,25}\b\d+[ -]?(?:[GTM]i?B)\b','body'),term(FOOTER % r'\b(?:RAM|VRAM|memory)\b','body') ], 'none':[term(r'\b(?:keyboards?|mice|mouse|headsets?|headphones?|monitors?|gaming chairs?|VPN|dust\w*|gift\w*|3D printers?|USB chargers?)\b|耳機|滑鼠|鍵盤')]})
        if ticker=='INTC':
            groups[0]['any'].append(term(r'\b(?:DRAM|RAM|HBM|memory).{0,50}(?:shortage|supply|prices?|bandwidth|architecture)|(?:記憶體|DRAM|HBM).{0,30}(?:短缺|缺貨|供應|漲價|架構)'))
            groups.append({'id':'semiconductor_facility','all':[term(FOOTER % FACTORY,'body'),term(r'\b(?:Micron|hynix|Solidigm|TSMC|semiconductor|chipmaking|chip)\b|晶圓|半導體','body')]})
        if ticker=='NBIS':
            groups[0]['any'].append(term(r'\b(?:Trainium|TPUs?|Ascend|H[12]00|B[23]00|GB[23]00|MI[34]00|GPU (?:prices?|supply)|AI (?:chips?|factory)|production inference)\b|\b(?:GPUs?).{0,55}(?:orders?|supply|prices?|cloud)\b|\b(?:order\w*|rent\w*).{0,50}GPUs?\b|AI\s*工廠|GPU.{0,20}(?:價格|租金|供應|部署)'))
            groups.append({'id':'hosted_model_market','all':[term(r'\b(?:OpenAI|Anthropic|Google Cloud|Azure|AWS|DeepSeek|Qwen|Kimi|Moonshot|Tencent|MiniMax|Cohere|Mistral)\b','body'),term(FOOTER % r'\b(?:API|inference|hosting|hosted|provider|cloud platform|cloud service)\b','body')]})
            groups[0]['any'].append(term(r'\b(?:EPYC|Xeon|Vera|server CPUs?|HPC|supercomput\w*|AI servers?)\b|伺服器CPU|伺服器處理器'))
            groups.append({'id':'inference_hardware_deployment','all':[term(FOOTER % r'\b(?:GPUs?|TPUs?|Trainium|B[23]00|V100|RTX|CUDA|VRAM)\b','body'),term(FOOTER % r'\b(?:inference|tokens? (?:per second|/s)|tok/s|throughput|prefill|model (?:deployment|weights)|memory requirements)\b|推論|每秒.{0,12}(?:token|詞元)','body')]})
        if ticker=='IONQ':
            peer=r'\b(?:IBM Quantum|D.Wave|Rigetti|Quantinuum|QuEra|PsiQuantum|IQM|Pasqal|Xanadu|Q.CTRL|Qblox|Riverlane|Atom Computing|Iceberg Quantum|SuperQubit|Unitary Foundation|quantum (?:memory|state discrimination|channels?|noise|control|simulation|metrolog\w*|information|teleport\w*|resistance)|spin (?:readout|control)|superconducting (?:devices?|circuits?|qubits?)|quantum kit)\b'
            groups[0]['any'].append(term(peer))
            groups.append({'id':'quantum_information_or_device','any':[term(FOOTER % (peer+r'|\b(?:entanglement|spin)\b.{0,90}\b(?:witness|certif\w*|detect\w*|channels?|fidelity|readout|gates?|control|communicat\w*)\b'),'body')]})
        if ticker != 'IONQ':
            extra_infra=r'\b(?:commit\w*|purchase\w*|lease\w*|rent\w*|contract\w*)\b.{0,100}\b(?:compute|computing|GPU)\b|\bcapacity\b.{0,70}\b(?:GPU|Nvidia|Huawei|compute) clusters?\b|\b(?:GPU|Nvidia|Huawei|compute) clusters?\b.{0,80}\b(?:capacity|deploy\w*|expand\w*|construct\w*|build\w*)\b'
            groups.append({'id':'contracted_compute_or_cluster_capacity','any':[term(extra_infra),term(FOOTER % extra_infra,'body')]})
        if ticker=='COHR':
            groups.append({'id':'network_hardware_market','any':[term(FOOTER % r'(?:高速網通|高速網路|網路設備|光纖).{0,60}(?:需求|採購|訂單|出貨|成長|升級)|(?:需求|帶動).{0,40}高速網通|\b(?:optical communications|fiber optic\w*)\b.{0,100}\b(?:sales|revenue|demand|growth|invest\w*|doubl\w*)\b','body')]})
        if ticker=='BE':
            groups.append({'id':'industrial_construction_fact','any':[term(FOOTER % r'\b(?:launch\w*|open\w*|construct\w*|build\w*)\b.{0,90}(?:\d+.{0,25}(?:square foot|sq.?ft|square feet)|industrial|manufacturing|production|automated).{0,30}\b(?:facilit\w*|plants?|factor\w*)\b','body')]})
        if ticker=='NBIS':
            groups=[g for g in groups if g['id']!='hosted_model_market']
            groups[1]['any']=[term(FOOTER % r'\b(?:input|output) tokens?\b|\bper (?:million|1M) tokens?\b|(?:推論服務|推理服務|GPU\s*雲|雲端\s*GPU|算力租賃|模型託管|託管模型).{0,50}(?:價格|費用|供應|上線|發布|吞吐|API)|每百萬.{0,10}(?:token|Token|詞元)','body')]
            groups.append({'id':'model_service_availability','any':[term(FOOTER % r'\b(?:model|weights?)\b.{0,100}\b(?:available|accessible|served|hosted|offered|released)\b.{0,100}\b(?:API|OpenRouter|Baseten|Together AI|Fireworks|inference)\b|\b(?:available|accessible|hosted|offers?)\b.{0,70}\b(?:model|inference|API access)\b|\b(?:inference|token)\b.{0,40}\b(?:prices?|pricing|costs?)\b.{0,45}\b(?:fall\w*|drop\w*|declin\w*|collapse|crash|cheaper|reduc\w*)\b','body')]})
        if ticker=='MU':
            physical_arch=r'\b(?:GPU|CPU|RAM|VRAM|NUMA|CXL)\b.{0,90}\b(?:shared memory|unified memory|memory (?:bandwidth|capacity|architecture|pool|hierarchy))\b|\b(?:shared memory|unified memory|memory (?:bandwidth|capacity|architecture|pool|hierarchy))\b.{0,90}\b(?:GPU|CPU|RAM|VRAM|NUMA|CXL)\b'
            groups.append({'id':'physical_memory_architecture','any':[term(FOOTER % physical_arch,'body')]})
        if ticker=='NBIS':
            groups[0]['any'].append(term(r'\bserver (?:performance|memory|chips?|processors?|hardware)\b|伺服器效能|伺服器記憶體|(?:AWS|Azure|Google Cloud).{0,60}(?:revenue|backlog|capacity|growth|earnings)|cloud (?:revenue|backlog|capacity|sales)'))
        if ticker=='MU':
            mem_fact=r'\b(?:DDR[3-6]\w*|DRAM|NAND|HBM\d*|GDDR\w*|LPDDR\w*) (?:shortages?|prices?|supply|demand|output|production|capacity|market|chips?|orders?)\b|\b(?:prioritiz\w*|manufactur\w*|supply|procur\w*|allocat\w*|share|pool|shard\w*)\b.{0,65}\b(?:DRAM|NAND|HBM\d*|GDDR\w*)\b|\b(?:CXL|memory bandwidth|memory latency|memory controller|memory bus|memory hierarchy)\b'
            groups.append({'id':'memory_market_or_architecture_fact','any':[term(FOOTER % mem_fact,'body')]})
        if ticker=='NBIS':
            runtime=r'\b(?:inference|API|model.serving)\b.{0,100}\b(?:CPU|memory|latency|throughput|compute)\b.{0,80}\b(?:reduc\w*|less|lower|faster|improv\w*|cut\w*)\b|\b(?:CPU|memory|latency|throughput|compute)\b.{0,80}\b(?:reduc\w*|less|lower|faster|improv\w*|cut\w*)\b.{0,100}\b(?:inference|API|model.serving)\b'
            groups.append({'id':'model_runtime_resource_change','any':[term(runtime),term(FOOTER % runtime,'body')]})
            groups[0]['any'].append(term(r'\b(?:model.serving|availability zones?)\b|\b(?:models?|inference)\b.{0,65}\b(?:servers?|hosting|hosts?)\b'))
        # Actual operating facts, rather than generic AI company background.
        if ticker != 'IONQ':
            operators=r'\b(?:DayOne|Equinix|Digital Realty|AirTrunk|CyrusOne|Crusoe|IREN|Vantage Data Centers)\b'
            groups[0]['any'].append(term(operators))
            infra_more=r'\b(?:comput(?:e|ing)|AI|superintelligence|SI) infrastructure\b.{0,100}\b(?:spend\w*|debt|borrow\w*|invest\w*|financ\w*|expan\w*|agreements?|deals?)\b|\b(?:spend\w*|debt|borrow\w*|invest\w*|financ\w*|build\w*|deals?)\b.{0,100}\b(?:comput(?:e|ing)|AI|superintelligence|SI) infrastructure\b|\b(?:sovereign|AI) clouds?\b.{0,100}\b(?:agreements?|deals?|expan\w*|launch\w*|buil\w*)\b|\b(?:agreements?|deals?|expan\w*)\b.{0,100}\bsovereign clouds?\b|(?:算力|運算|AI晶片).{0,25}(?:合約|長約|租賃|支出|資本支出|容量|採購)|(?:資料中心|數據中心).{0,50}(?:用電|電力|電網|限制|許可|出口|租用|租賃|銷售|營收)|(?:供電|電力|電網).{0,45}(?:資料中心|數據中心)|\b(?:supercomput\w*|supercompute site)\b.{0,100}\b(?:invest\w*|buil\w*|construct\w*|capacity|power|grid)\b'
            groups.append({'id':'physical_infrastructure_operating_fact','any':[term(infra_more),term(FOOTER % infra_more,'body')]})
        if ticker in ['MU','COHR','NBIS','INTC']:
            operating=r'\b(?:data[ -]?cent(?:er|re)|networking|AI.optimized servers?|AI servers?)\b.{0,65}\b(?:revenue|sales|shipments?|orders?|backlog|demand|growth)\b|\b(?:revenue|sales|shipments?|orders?|backlog)\b.{0,65}\b(?:data[ -]?cent(?:er|re)|networking|AI.optimized servers?|AI servers?)\b|(?:AI.{0,4}伺服器|資料中心|數據中心|高速網路|網通).{0,35}(?:營收|出貨|訂單|需求|成長|量產)|(?:營收|出貨|訂單).{0,35}(?:AI.{0,4}伺服器|資料中心|數據中心|高速網路|網通)'
            groups.append({'id':'datacenter_hardware_operating_market','any':[term(FOOTER % operating,'body')]})
        if ticker in ['INTC','NBIS']:
            chip_policy=r'\b(?:chips?|GPUs?|processors?|Nvidia|Blackwell|Hopper|H200|Megaspeed)\b.{0,100}\b(?:export\w*|sanctions?|blacklist\w*|trade restrictions?|smuggl\w*|licensed|licenses?|shipments?|remote access)\b|\b(?:export\w*|sanctions?|blacklist\w*|smuggl\w*|licensed|licenses?|remote access)\b.{0,100}\b(?:chips?|GPUs?|processors?|Nvidia|Blackwell|Hopper|H200|Megaspeed)\b|(?:晶片|輝達|伺服器|算力|GPU).{0,45}(?:出口|管制|限制|黑名單|許可|走私)|(?:出口|管制|限制|黑名單|許可|走私).{0,45}(?:晶片|輝達|伺服器|算力|GPU)'
            groups.append({'id':'compute_supply_and_export_policy','any':[term(chip_policy),term(FOOTER % chip_policy,'body')]})
        if ticker=='MU':
            mem_more=r'\bmemory (?:procurement|purchas\w*|supply|shortage|pricing)\b|\b(?:cards?|GPUs?|modules?)\b.{0,35}\b\d+\s*[GTM]i?B\b|\b\d+\s*[GTM]i?B\b.{0,25}\b(?:cards?|GPUs?|modules?)\b|(?:HBM\w*|DDR\w*|記憶體).{0,45}(?:容量|頻寬|架構|標準|規格|製程|量產|價格|供給|訂單|記憶體)|(?:容量|頻寬|架構|標準|規格).{0,35}(?:HBM\w*|DDR\w*|記憶體)'
            groups.append({'id':'physical_memory_market_detail','any':[term(FOOTER % mem_more,'body')]})
        if ticker=='INTC':
            groups[0]['any'].append(term(r'先進封裝|混合鍵合|封裝製程|CoP.{0,12}封裝|RDMA|chipmaking|chip.making'))
            groups.append({'id':'main_subject_processor','any':[term(r'\A.{0,800}\b(?:with|powered by|equipped with|packs?|uses?|using|sports?)\b.{0,50}\b(?:Intel|AMD|Ryzen|Core Ultra)\b.{0,35}\b(?:CPU|processor|chip)\b','body')]})
        if ticker=='NBIS':
            service=r'\b(?:virtual machines?|VMs|cloud instances?)\b.{0,110}\b(?:performance|memory|cores?|pricing|throughput|capacity|faster)\b|\b(?:AI services?|AI cloud|cloud infrastructure)\b.{0,80}\b(?:revenue|spend\w*|expan\w*|shortage|contracts?|outage)\b|\b(?:spend\w*|expan\w*|contracts?)\b.{0,80}\b(?:AI services?|AI cloud|cloud infrastructure)\b|\b(?:computing power|compute capacity|GPUs?|training bandwidth)\b.{0,70}\b(?:shortage|dedicat\w*|cut\w*|reduc\w*|less|invest\w*|allocat\w*)\b|\b(?:shortage|dedicat\w*|cut\w*|reduc\w*|less|invest\w*|allocat\w*)\b.{0,70}\b(?:computing power|compute capacity|GPUs?|training bandwidth)\b|(?:雲端服務|雲端業者|雲端算力|伺服器|推論|推理).{0,40}(?:價格|費率|量產|出貨|訂單|記憶體|吞吐|效能|最佳化|減少|效率)|(?:推論|推理).{0,80}(?:HBM|SRAM|頻寬)|模型.{0,30}(?:價格|費率)|(?:訓練|運行|執行).{0,15}客製化.{0,6}模型'
            groups.append({'id':'cloud_resource_service_fact','any':[term(service),term(FOOTER % service,'body')]})
        if ticker=='COHR':
            groups[0]['any'].append(term(r'光子晶片|量子光學|光纖通訊|光纖傳輸'))
            groups.append({'id':'optical_link_fact','any':[term(FOOTER % r'\b(?:optical laser links?|NVLink|InfiniBand|RDMA|Ethernet)\b.{0,100}\b(?:bandwidth|interconnect\w*|deploy\w*|network\w*|speed|switch\w*|communicat\w*)\b|(?:高速|光).{0,8}(?:傳輸|互連|連結).{0,35}(?:需求|量產|頻寬|技術|網路)','body')]})
        if ticker=='BE':
            groups.append({'id':'factory_project_fact','any':[term(FOOTER % r'\b(?:build|construct\w*|expan\w*|proposal|proposed)\b.{0,110}\b(?:semiconductor manufacturing campus|chip manufacturing|chipmaking campus|fab)\b|(?:晶片製造設施|擴廠計畫|建廠計畫|工廠.{0,10}建設|新廠動土|德州建廠)','body')]})
        if ticker=='IONQ':
            groups[0]['any'].append(term(r'\bquantum (?:annealing|walks?|encryption|secure|security|business|cluster|start.ups?|technolog\w*|private capital|enhanced|research|expert)\b|\b(?:Rydberg|Ising models?|logical error rates|chip.scale clock|QuIC|QuintessenceLabs|Qtonic)\b'))
            groups.append({'id':'quantum_engineering_fact','any':[term(FOOTER % r'\b(?:qubits?|QPUs?|quantum comput\w*|post.quantum cryptograph\w*|quantum key|atomic clocks?|quantum information|quantum data|quantum random|Rydberg arrays?)\b','body')]})
        reverse_fact=rf'\b(?:buy\w*|bought|invest\w*|acquir\w*|sue\w*|sued|strike\w*|dividends?|shares?|stocks?|earnings|revenue|gain\w*|stakes?|holdings|rose|fell|down|dropped|upgraded)\b.{{0,160}}(?:{IDENTITY[ticker]})'
        groups.append({'id':'company_fact_reverse_order','any':[term(FOOTER % reverse_fact,'body')]})
        identity_fact = rf'(?:{IDENTITY[ticker]}).{{0,100}}\b(?:buy\w*|bought|placement|equity|announc\w*|report\w*|forecast\w*|manufactur\w*|launch\w*|invest\w*|fund\w*|orders?|contracts?|shares?|earnings|revenue|rose|fell|down|dropped|upgraded)\b'
        groups.append({'id': 'company_business_facts', 'any': [term(FOOTER % identity_fact, 'body')]})
        if ticker != 'IONQ':
            groups.append({'id': 'infrastructure_market', 'any': [term(INFRA), term(FOOTER % INFRA, 'body')]})
        if ticker == 'BE':
            groups.append({'id': 'factory_power_demand', 'any': [term(FACTORY), term(FOOTER % FACTORY, 'body')]})
        groups.append({'id': 'insufficient_body', 'all': [term('^$', 'body')], 'none': [term(NOISE)]})
        if ticker!='IONQ':
            material_capacity=r'\b(?:data[ -]?cent(?:er|re)s?|cloud infrastructure)\b.{0,100}\b(?:deploy\w*|licens\w*|restrict\w*|export\w*)\b|\b(?:deploy\w*|licens\w*|restrict\w*|export\w*)\b.{0,100}\b(?:data[ -]?cent(?:er|re)s?|cloud infrastructure)\b|\b(?:\d[\d,]* GPUs?|GPU.backed financing|multi.gigawatt|AI manufacturing hub)\b|\b(?:supply and capacity obligations|multiyear memory procurement)\b|(?:資料中心|數據中心).{0,25}(?:規模|供電|用電|許可)|(?:AIDC|算力).{0,25}(?:落地|供電|GW|容量)'
            groups.append({'id':'capacity_and_infrastructure_constraints','any':[term(FOOTER % material_capacity,'body')]})
        if ticker=='NBIS':
            groups[0]['any'].append(term(r'\b(?:Blackwell|Hopper|AI chip\w*)\b.{0,70}\b(?:sold|sales|supply|output|export\w*)\b|\b(?:neocloud|GPU cloud|AI cloud)\b.{0,70}\b(?:bubble|demand|supply|revenue|debt|risk)\b|晶片替代供應商'))
            groups.append({'id':'cloud_provider_risk','any':[term(FOOTER % r'\b(?:Nebius|CoreWeave|Fireworks AI|Prime Intellect|neoclouds?)\b.{0,150}\b(?:breach|stopped using|risk|credit|default|options|stocks|demand|output|supply)\b|\b(?:stopped using|risk|credit|default|options)\b.{0,150}\b(?:Nebius|CoreWeave|Fireworks AI|neoclouds?)\b','body')]})
        if ticker=='INTC':
            groups[0]['any'].append(term(r'高通|Qualcomm|AI200'))
        if ticker=='COHR':
            groups.append({'id':'network_platform_architecture','any':[term(FOOTER % r'高速網路.{0,50}(?:傳輸|伺服器)|乙太網路.{0,30}(?:擴展|擴充)|\b(?:Vera Rubin|NVL72)\b.{0,100}\b(?:deploy\w*|expan\w*|rollout|rack)\b','body')]})
        if ticker=='NBIS':
            groups=[g for g in groups if g['id']!='capital_and_capacity_facts']
            for group in groups:
                if group['id'] in ['physical_infrastructure_operating_fact','model_runtime_resource_change']:
                    group['any']=[v for v in group['any'] if v['field']=='body']
        if ticker=='MU':
            groups[0]['any'].append(term(r'\b(?:CXMT|YMTC|Nanya|Winbond|G.Skill|ADATA|DDR\w*|HBM\w*)\b|長鑫|長江存儲|南亞科|華邦|群聯|慧榮|威剛'))
        if ticker=='INTC':
            groups[0]['any'].append(term(r'\b(?:Cerebras|Astera Labs|Marvell|Broadcom|MediaTek|Qualcomm|Applied Materials|Lam Research|GlobalFoundries|Micron|CXMT|YMTC|DRAM|DDR\w*|HBM\w*|RAM|Raspberry Pi|Googlebook\w*)\b|晶片|GPGPU|伺服器|記憶體|海力士|群聯|華碩|超級電腦'))
        if ticker!='IONQ':
            sector=r'\b(?:CoreWeave|Nscale|Nebius)\b.{0,80}\b(?:capacity|demand|backlog|revenue|fund\w*|financ\w*|debt|bonds?|yields?|loan|delays?)\b|\b(?:GPUs?|AI chips?)\b.{0,80}\b(?:rent\w*|insur\w*|financ\w*|prices?|orders?|supply|demand|lease\w*|lead times?)\b|\b(?:rent\w*|insur\w*|financ\w*|orders?|lease\w*)\b.{0,70}\b(?:GPUs?|AI chips?)\b|\b(?:Stargate|GPU scheduling|multi.GW)\b|(?:算力|AI|GPU).{0,20}(?:基建融資|超級電腦園區)|(?:租借|租賃).{0,25}(?:顯卡|GPU)|(?:GPU|算力).{0,20}(?:部署|進駐|長約|轉售|價格|租金)'
            groups[0]['any'].append(term(sector))
            # Only title subjects here: do not let an unrelated article's sidebar
            # or speculative background GPU-price sentence rescue it.
        if ticker=='NBIS':
            groups[0]['any'].append(term(r'\b(?:capex|capital expenditur\w*|compute (?:spend\w*|bill|plans?|expenses?|funding)|cloud commitments?|inference market\w*|model.serving|AI inference (?:market|engine))\b|\b(?:Daytona|Inkbox|Mt. Thor|OVH|DeepInfer|FP8|tensor parallelism|Diamond Rapids|llama.cpp)\b|\b(?:cloud (?:hosting|computer|simulator)|hosted access|production engine|GPU cloud|serverless (?:engine|platform))\b|GPU.{0,20}量子電腦|AI.{0,12}(?:服務|推論新創).{0,20}(?:洽售|價格|費率|財務長)'))
            # Shared INFRA already covers these body facts; remove repeated terms.
            groups=[g for g in groups if g['id'] not in ['model_runtime_resource_change','physical_infrastructure_operating_fact']]
        if ticker=='IONQ':
            groups[0]['any'].append(term(r'\b(?:Horizon Quantum|Classiq|Entropica|Q.Block|Quantum Cyber|SEALSQ|SEALQS|WISeQey|quantum (?:device|tech|supply chain|priors|smoothing|electro.optic|money)|Bloch Quantum|quantum strain|spin lifetime|quantum speckle|post.selection|entanglement (?:asymmetry|negativity)|quantum state.{0,15}electric current)\b'))
            groups.append({'id':'quantum_control_and_information','any':[term(FOOTER % r'\b(?:trapped ions?|quantum (?:communication|cryptography|software|hardware|technology|technologies)|quantum.state (?:control|tomography)|measurement.{0,25}dephasing|entanglement (?:measures?|detection|quantification))\b','body')]})
        if ticker!='IONQ':
            physical_commitment=r'\b(?:compute|computing|cloud|infrastructure)\b.{0,60}\b(?:commitments?|contracted|leases?|leasing|expenditure|financing)\b|\b(?:commitments?|contracted|leases?|leasing|expenditure|spend\w*)\b.{0,60}\b(?:compute|computing|cloud|AI infrastructure|artificial intelligence infrastructure)\b|\b(?:GPU cloud|cloud providers?|neoclouds?|CoreWeave|Nebius|Nscale)\b.{0,100}\b(?:production|capacity|demand|financing|bonds?|debt|backlog|revenue|capital)\b|\b(?:deploy\w*|rollout|ramp\w*|expan\w*)\b.{0,80}\b(?:NVL\d+|AI capacity|AI chips?)\b|\b(?:NVL\d+|AI capacity|AI chips?)\b.{0,80}\b(?:deploy\w*|rollout|ramp\w*|expan\w*)\b|(?:興建|建置|擴建|建設).{0,35}(?:超級電腦|算力園區)|(?:預付款|採購計畫).{0,40}(?:電力設備|資料中心)|(?:電力設備|資料中心).{0,40}(?:預付款|採購計畫)'
            groups.append({'id':'physical_capacity_commitments','any':[term(FOOTER % physical_commitment,'body')]})
        if ticker=='MU':
            groups.append({'id':'physical_memory_resource_detail','any':[term(FOOTER % r'\b(?:high.bandwidth memory|memory (?:architecture|bottlenecks?|subsystem|channels?|dies)|HBM\w*.{0,20}stacks?)\b|\bmemory chips?\b.{0,65}\b(?:lack|shortage|capacity|production|wafers?)\b|\b(?:lack|shortage|capacity|production|wafers?)\b.{0,65}\bmemory chips?\b|\b\d+\s*(?:[GTP]i?B)\b.{0,15}\b(?:DDR\w*|LPDDR\w*|SSDs?|storage|disk)\b|\b(?:disk|storage) (?:requirement|capacity)\b.{0,35}\b\d+\s*[GTP]i?B\b','body')]})
        if ticker=='INTC':
            groups[0]['any'].append(term(r'\b(?:TorchTPU|M3 Ultra|graphics performance|optical chips?)\b|光子晶片'))
        if ticker=='NBIS':
            groups[0]['any'].append(term(r'\b(?:inference (?:compiler|framework)|GPU (?:optimizations?|inference)|PyTorch.{0,50}CPUs?|cloud instances?)\b|量子電腦.{0,25}GPU'))
            groups.append({'id':'serving_runtime_resource_fact','any':[term(FOOTER % r'\b(?:inference|API)\b.{0,130}\b(?:less|lower|reduc\w*|cut\w*)\b.{0,30}\b(?:CPU|memory|latency)\b','body')]})
        for group in groups:
            if group['id'] in ['infrastructure_market','physical_infrastructure_operating_fact']:
                group['any']=[v for v in group['any'] if v['field']=='body']
        # This is an exclusion layer, not a replacement relevance classifier:
        # only known noise topics require a material business rescue. Unknown
        # topics keep the first layer's decision instead of failing a whitelist.
        risk_title=(r'\b(?:AI|OpenAI|Anthropic|Claude|GPT|Gemini|Qwen|MiMo|MiniMax|DeepSeek|LLMs?|agents?|chatbots?|Muse|Cognition|Kling|IPO|valuation|stocks?|shares?|markets?|Nasdaq|Kospi|Nikkei|S&P|Dow Jones|gaming|games?|GTA|Doom|Pok.mon|PlayStation|PS[235]|Xbox|Nintendo|Steam|headphones?|headsets?|keyboards?|mice|mouse|monitors?|routers?|3D.print\w*|gifts?|VPN|breach\w*|hack\w*|vulnerabilit\w*|passwords?|malware|researchers?|scientists?|universit\w*|mathemati\w*|physics|quantum|cryptograph\w*)\b'
                    r'|AI|人工智慧|模型|代理|開源|研究|科學|機器人|股價|股市|台股|估值|上市|掛牌|募資|融資|詐騙|資安|網攻|資安|駭|耳機|鍵盤|滑鼠|螢幕|博物館|藝術館')
        cross_market={
            'MU':r'\b(?:Intel|AMD|Ryzen|CPUs?|processors?|Nvidia|TSMC|foundr\w*|EPYC|chip design|semiconductor\w*)\b|英特爾|輝達|台積電|晶片|半導體|光子|光學|封裝|雷射|處理器',
            'BE':r'\b(?:Intel|AMD|Nvidia|CPUs?|GPUs?|DRAM|NAND|chips?|memory|semiconductor\w*|TSMC|foundr\w*)\b|晶片|記憶體|半導體|晶圓|封裝|光電|伺服器|英特爾|台積電|輝達',
            'COHR':r'\b(?:Intel|AMD|Nvidia|CPUs?|GPUs?|DRAM|NAND|HBM\w*|chips?|memory|semiconductor\w*|TSMC|foundr\w*)\b|晶片|記憶體|半導體|晶圓|封裝|伺服器|台積電|輝達|英特爾',
            'NBIS':r'\b(?:Intel|AMD|Micron|TSMC|foundr\w*|memory|RAM|DRAM|DDR\w*|SSD\w*|laptops?|notebooks?|RTX|GeForce|PCs?)\b|晶片|記憶體|半導體|晶圓|封裝|手機|筆電|台積電|輝達|英特爾',
            'INTC':r'\b(?:Windows|Linux|software|crypto\w*|robots?|robotics|lasers?|3D|PoE|Ethernet)\b|軟體|機器人|雷射|量子|電力|電網|電池|記憶|雲端資安',
            'IONQ':r'\b(?:spin|entangle\w*|superconduct\w*|atoms?|materials?|entropy|symmetr\w*|fermion\w*|photons?|photonics?|neutrino\w*|electrons?|Bonn|Nvidia|AMD|SEAL\w*|Cloudflare)\b'}
        groups.append({'id':'outside_known_noise','all':[term(r'.','title')],
                       'none':[term(risk_title+'|'+cross_market[ticker])]})
        if ticker!='IONQ':
            commitments=r'\b(?:compute|computing|cloud|infrastructure) (?:commitments?|funding|financing|buildout|spend\w*|capacity planning)\b|\b(?:AI|artificial intelligence) (?:compute|computing) infrastructure\b|\b(?:AI|data[ -]?cent(?:er|re)s?)\b.{0,80}\b(?:guarantees?|procurement|non.refundable deposits|infrastructure partnership)\b|\b(?:guarantees?|procurement|non.refundable deposits)\b.{0,80}\b(?:AI|data[ -]?cent(?:er|re)s?)\b|(?:建|興建|建設).{0,30}(?:超級電腦園區|先進封裝廠)|(?:萬|億).{0,8}(?:顆|張).{0,15}(?:GPU|RTX PRO)|(?:GPU|H200|B300|GB300).{0,35}(?:億美元|億.{0,5}訂單|租賃)'
            groups.append({'id':'committed_physical_market','any':[term(FOOTER % commitments,'body')]})
        if ticker=='BE':
            groups[0]['any'].append(term(r'\b(?:fab\w*|foundry|plant)\b.{0,60}\b(?:construction|capacity|booked)\b|\b(?:Terafab|AI center)\b|先進封裝.{0,12}量能|園區.{0,20}環評'))
            groups.append({'id':'semiconductor_plant_construction','any':[term(FOOTER % r'\bconstruction\b.{0,50}\b(?:memory|chip|semiconductor) plants?\b|\b(?:newest|new|planned|forthcoming)\b.{0,45}\b(?:fab|foundry|semiconductor plant)\b','body')]})
        if ticker=='MU':
            groups.append({'id':'memory_resource_architecture','any':[term(FOOTER % r'\b(?:coherence protocols|shared memory|VRAM.{0,65}(?:capacity|exceed|offload|system memory))\b','body')]})
        if ticker=='NBIS':
            groups[0]['any'].append(term(r'\b(?:Applied Compute|Brev|AI accelerators?|Arm Axion|DDR6|AI chip (?:supply|lineup)|GPU locks|Claude.{0,35}AMD GPUs?|AI models?\b.{0,65}\b(?:API|price|cost|preview)|API\b.{0,65}\b(?:AI models?|price)|cloud simulators?)\b'))
            for group in groups:
                if group['id']=='cloud_resource_service_fact':
                    group['any']=[v for v in group['any'] if v['field']=='body']
            groups[0]['any'].append(term(r'\b(?:cuts?|reduc\w*|shortage)\b.{0,60}\b(?:computing power|training bandwidth)\b|\b(?:computing power|training bandwidth)\b.{0,60}\b(?:cuts?|reduc\w*|shortage)\b'))
            groups.append({'id':'inference_service_access','any':[term(FOOTER % r'\b(?:API (?:price|pricing|access)|infrastructure unit|infrastructure costs)\b|\b(?:gas generators?|space.based AI computers?|AI data centers?|AI accelerator hosts?)\b|(?:AI\s*伺服器|AI\s*推論產品|HBM).{0,30}(?:需求|供應|產能|頻寬|規格|量產)|(?:需求|出貨|營收).{0,35}AI\s*伺服器','body')]})
        if ticker=='INTC':
            groups[0]['any'].append(term(r'\b(?:SSDs?|HBM\w*|TorchTPU|GB300|B300)\b|崑崙芯|伺服器|工業級.{0,20}儲存'))
        if ticker=='IONQ':
            groups[0]['any'].append(term(r'\b(?:entanglement.{0,40}(?:negativity|tool)|spin calibrations|quantum states? from data|quantum (?:time|system) evolution|probabilities to quantum states|quantum symposium)\b'))
            groups.append({'id':'quantum_computational_advantage','any':[term(FOOTER % r'\bquantum[ -]advantage\b','body')]})
        if ticker!='IONQ':
            capex=r'\b(?:capital expenditur\w*|capex|capital spending|cloud (?:industry backlog|infrastructure unit)|AI computing centers?|cloud compute capabilities)\b|\b(?:buy|lease|finance|financing)\b.{0,60}\bAI accelerators?\b|(?:雲端網路產品).{0,50}(?:營收|需求|成長)'
            groups.append({'id':'physical_capital_or_cloud_market','any':[term(FOOTER % capex,'body')]})
            groups[0]['any'].append(term(r'\bNvidia\b.{0,40}\b(?:commitments?|contract|AI financing|chip finance)\b|\b(?:sale|sell)\b.{0,55}\bNvidia chips?\b|\b(?:AI|artificial intelligence) buildout\b'))
        if ticker=='MU':
            groups.append({'id':'physical_memory_price_movement','any':[term(FOOTER % r'\b(?:price rises?|price increases?|price surges?)\b.{0,30}\b(?:RAM|DRAM|NAND|SSDs?)\b|\bmemory shipments?\b','body')]})
        if ticker=='NBIS':
            for group in groups:
                if group['id']=='cloud_resource_service_fact':
                    group['any']=[term(FOOTER % r'\b(?:virtual machines?|VMs|cloud instances?)\b.{0,110}\b(?:performance|memory|cores?|pricing|throughput|capacity|faster)\b|(?:雲端服務|雲端算力|伺服器|推論|推理).{0,40}(?:價格|費率|吞吐|效能|最佳化|減少|效率)|(?:推論|推理).{0,80}(?:HBM|SRAM|頻寬)','body')]
            groups[0]['any'].append(term(r'\b(?:Cerebras|fal|compute infrastructure|AI compute|DeepSeek.{0,45}API|GPU AI limiter|cloud contract)\b'))
        if ticker=='INTC':
            groups.append({'id':'gpu_runtime_performance','any':[term(FOOTER % r'\b(?:GPUs?|GB300|B300|Blackwell)\b.{0,100}\b(?:performance|throughput|kernels?|benchmarks?)\b','body')]})
        if ticker=='COHR':
            groups[0]['any'].append(term(r'\b(?:H200|B300|Blackwell|Hopper|AI chips?)\b.{0,65}\b(?:export\w*|imports?|sales|sold|orders?|supply|deliver\w*|servers?)\b|\b(?:VR200|NVL\d+)\b.{0,40}\b(?:ramp|production|delay)\b|\bcloud (?:demand|backlog)\b'))
            groups.append({'id':'optical_components_or_ai_facility','any':[term(FOOTER % r'(?:光纖陣列|CPO).{0,60}(?:產能|量產|產線|需求|組裝|技術)|\bAI computing centers?\b','body')]})
        if ticker!='IONQ':
            groups[0]['any'].append(term(r'(?:萬|億)(?:顆|張).{0,16}(?:輝達|Nvidia|GPU|RTX)'))
            groups.append({'id':'physical_infrastructure_chinese_fact','any':[term(FOOTER % r'資本支出|(?:租用|租賃|採購).{0,30}(?:晶片|伺服器)|(?:興建|新建|planned|first commercial).{0,60}(?:semiconductor fabrication facilit\w*|半導體工廠)|\bartificial intelligence manufacturing hub\b','body')]})
        if ticker=='INTC':
            groups.append({'id':'independent_pc_product_fact','any':[term(FOOTER % r'\b(?:mini.PCs?|desktop PCs?|gaming PCs?)\b.{0,180}\b(?:Ryzen|Intel Core|CPU)\b','body')]})
        if ticker=='NBIS':
            groups[0]['any'].append(term(r'\b(?:AI models? with|models? across API|Foundry public preview|DeepSeek.{0,65}API)\b'))
            groups.append({'id':'hosted_model_market_chinese_fact','any':[term(FOOTER % r'模型.{0,40}(?:價格|費率)|(?:價格|費率).{0,30}模型|開放\s*API\s*服務|(?:訓練|運行).{0,20}客製化.{0,8}模型|(?:CSP|雲端服務供應商).{0,45}(?:下修|減少).{0,20}(?:訂單|採購)|\bNPU\b.{0,120}\b(?:AI frameworks|inference)\b','body')]})
        # Guard meanings, rather than letting a new rescue recreate the old
        # generic-word bypass. These edits apply to topic families, never IDs.
        for group in groups:
            for bucket in ('any','all','none'):
                for item in group.get(bucket,[]):
                    pattern=item['regex']
                    if group['id']=='outside_known_noise':
                        pattern+=r'|\b(?:dusters?|SSDs?|ARR|revenue|earnings|reviews?|awards?|accessories|modders?|developers?|enthusiasts?|hobby\w*|homelab|bootable|operating system|ZIP file|Wikipedia|chess|drones?|weapons?|torpedo|Microsoft 365|Bambu|Elegoo|Anycubic|Creality|subscribe|ad.free|sign up)\b|黎曼猜想|數學家|作業系統'
                    if ticker=='MU' and group['id']=='memory_resource_architecture':
                        pattern=pattern.replace('coherence protocols|shared memory|','coherence protocols|')
                    if ticker=='INTC' and group['id']=='business_subject':
                        pattern=pattern.replace('|伺服器|','|伺服器.{0,18}(?:需求|供應|CPU|處理器|出貨|效能|營收|產品|平台|架構|生產)|')
                    if ticker=='INTC' and group['id']=='material_business_facts':
                        pattern=pattern.replace('效能|核心|價格','效能|核心數|價格')
                    if ticker=='NBIS' and group['id']=='inference_service_access':
                        pattern=pattern.replace('AI data centers?','orbital compute modules?')
                    if ticker=='NBIS' and group['id']=='cloud_resource_service_fact':
                        pattern=pattern.replace('|伺服器|','|伺服器(?:效能|CPU|處理器|記憶體)|')
                    if group['id']=='physical_capital_or_cloud_market':
                        pattern=pattern.replace('capital expenditur\\w*|capex|capital spending|',r'(?:capital expenditur\w*|capex|capital spending).{0,90}(?:\$\s*\d|\d.{0,10}(?:billion|million|B\b|M\b))|')
                    if group['id']=='physical_infrastructure_chinese_fact':
                        pattern=pattern.replace('資本支出|',r'資本支出.{0,70}(?:億|兆)|')
                    pattern=pattern.replace(r'\d[\d,]* GPUs?',r'(?<!RTX )(?<!GTX )(?<!RX )(?<!PRO )(?<!Quadro )(?<!Radeon )(?<!GeForce )\d[\d,]* GPUs?')
                    if ticker=='MU':
                        pattern=pattern.replace('Crucial',r'Crucial(?=.{0,45}\b(?:SSD|RAM|DRAM|memory|storage|Micron|brand|drives?|exits|shuts|launches|announces)\b)')
                    if ticker=='INTC':
                        pattern=pattern.replace('Intel|',r'(?<!Senate )(?<!House )Intel(?!\s+(?:Committee|Panel|Agency))|')
                    item['regex']=pattern
        groups=[g for g in groups if g['id']!='insufficient_body']
        for group in groups:
            if group['id']=='capital_and_capacity_facts':
                group['any']=[t for t in group['any'] if t['field']=='body']
        if ticker=='MU':
            physical=r'\b(?:private placement|falling|rising|gain|equity)\b.{0,160}\b(?:Micron|SanDisk|Kioxia|SK\s*hynix)\b|\b(?:Micron|SanDisk|Kioxia|SK\s*hynix)\b.{0,160}\b(?:production|shipments?|supply|demand|orders?|booked|spoken for|falling|rising)\b|(?:美光|海力士).{0,140}(?:漲|跌|基石投資人|私募|配售)|\b(?:less|reduc\w*|lower)\b.{0,30}\bVRAM\b|\b(?:memory speed|buffers.{0,40}VRAM|Silicon Motion SM\d+\w*)\b'
            groups.append({'id':'own_company_or_physical_memory_detail','any':[term(FOOTER % physical,'body')]})
        if ticker in ['INTC','NBIS']:
            resource=r'\b(?:CPUs?|NPUs?)\b.{0,220}(?:\d+(?:\.\d+)?\s*(?:GB|GiB)|quantization)|\b(?:tokens? per second|tok/s)\b.{0,25}\b(?:GPU|B300|GB300)\b|\b(?:processor cache timing|CPU cache.{0,20}(?:timing|contention)|cache contention.{0,70}CPUs?|GPU.{0,15}shader array)\b'
            groups.append({'id':'hardware_execution_resource_fact','any':[term(FOOTER % resource,'body')]})
            groups.append({'id':'model_hardware_requirements','all':[
                term(r'\b(?:models?|LLMs?|inference|weights)\b'),
                term(r'\b(?:requires?|recommended|suggested hardware|hardware configuration)\b.{0,150}\b(?:GPUs?|CPUs?|processors?)\b','body')]})
        if ticker=='NBIS':
            service=r'\b(?:GPU[ -]as[ -]a[ -]service|GPU cloud rental|cloud inference (?:platform|service)|(?:AI|enterprise) API (?:market|spending)|API.{0,35}\$\s*\d.{0,25}per (?:image|token|request)|inference cluster.{0,50}(?:expand\w*|tenfold)|insufficient computing power|offboard\w*.{0,60}inference providers?)\b|\bHBM\b.{0,80}\binference products?\b|\bTriton kernel\b|CoreWeave.{0,60}(?:營收|成長)'
            groups[0]['any'].append(term(r'\b(?:self.hosted sandboxes|GPU financing|GPU rental prices)\b'))
            groups[0]['any'].append(term(r'\bCoWoS\b.{0,65}\b(?:prices?|capacity|shortages?)\b'))
            groups.append({'id':'actual_model_hosting_market_fact','any':[term(FOOTER % service,'body')]})
            groups.append({'id':'inference_runtime_deployment','any':[term(r'\binference (?:servers?|runtimes?)\b|\bGPU cloud\b.{0,50}\b(?:rental|hosting|services?)\b','body')]})
        if ticker=='INTC':
            groups.append({'id':'named_processor_execution_requirements','any':[term(r'\b(?:hardware optimizations.{0,70}Intel|processor cache timing|CPU cache.{0,20}(?:timing|contention)|AVX.512|VBMI)\b','body')]})
            groups.append({'id':'self_hosted_hardware_configuration','all':[term(r'\bself.hosted\b'),term(r'\b\d+\s+Nvidia\s+[BH][23]00\s+GPUs?\b','body')]})
            groups.append({'id':'system_software_processor_support','all':[
                term(r'\b(?:Linux|Windows|kernel|drivers?)\b|作業系統'),
                term(r'(?:支援|支持|最佳化|相容).{0,60}(?:AMD|Intel|Ryzen|Nvidia|處理器)|\b(?:AMD|Intel|Ryzen|GPUs?|CPUs?)\b.{0,70}(?:support|optimi|driver|支援|相容)','body')]})
        if ticker=='COHR':
            groups[0]['any'].append(term(r'\b(?:InnoLight|Eoptolink|Accelink)\b|中際旭創|新易盛|天孚通信|光迅'))
            groups.append({'id':'named_optical_communications_device','any':[term(FOOTER % r'(?:\d+G|Tbps).{0,30}(?:Optical Transceivers?|光學收發器|光收發器)','body')]})
            groups.append({'id':'optical_peer_operating_facts','any':[term(r'\b(?:InnoLight|Eoptolink|Accelink)\b.{0,70}\b(?:down|up|earnings|revenue|sales|capacity|orders)\b','body')]})
        if ticker=='IONQ':
            groups.append({'id':'quantum_circuit_engineering','any':[term(r'\bquantum circuit design\b','body')]})
        # Some RSS records retain only a headline. Explicit physical transactions
        # are facts in their own right; missing bodies must not erase them.
        if ticker!='IONQ':
            physical_title = r'\b(?:AI (?:hardware|servers?|chips?)|Nvidia)\b.{0,95}\b(?:financ\w*|loans?|pledged|leases?|deploy\w*|clampdown)\b|\b(?:financ\w*|loans?|pledged|lease\w*|deploy\w*|clampdown)\b.{0,95}\b(?:AI (?:hardware|servers?|chips?)|Nvidia|chip lease)\b|\b(?:Oracle|hyperscaler\w*)\b.{0,90}\b(?:CDS|debt|credit spreads?)\b'
            physical_title+=r'|\bchip\b.{0,55}\b(?:clampdown|curbs)\b|\bAI chips?\b.{0,25}\borbit\b|\b(?:Nscale|CoreWeave|Nebius)\b.{0,80}\b(?:spending|campus|deal|partnership)\b'
            groups.append({'id':'explicit_physical_transaction_title','any':[term(physical_title)]})
        if ticker=='BE':
            groups[0]['any'].append(term(r'\bpower (?:deals?|agreements?)\b|\bgroundbreaking\b.{0,95}\b(?:chip|semiconductor|manufacturing|power) plants?\b'))
        if ticker=='NBIS':
            groups[0]['any'].append(term(r'\b(?:MI\d{3}[A-Za-z]*|Inception AI|private inference|AI memory expansion)\b|\bHugging Face\b.{0,70}\b(?:CDN|data traffic)\b'))
            groups[0]['any'].append(term(r'\b(?:Inception|Cerebras)\b.{0,60}\b(?:acqui\w*|cloud|revenue)\b|(?:收購|洽購).{0,35}Inception|\b(?:Anthropic|OpenAI)\b.{0,65}\b(?:outage|API CPU|enterprise spending)\b'))
            groups.append({'id':'infrastructure_supply_and_service_fact','any':[term(r'\bdata center optics\b|\benterprise customers\b.{0,130}\bAI services\b|(?:AI\s*運算|AI\s*晶片|伺服器).{0,50}(?:散熱|熱傳導|冷卻)|伺服器出貨量.{0,30}(?:成長|需求|增加|增長)','body')]})
        if ticker=='COHR':
            groups[0]['any'].append(term(r'\bNvidia\b.{0,45}\b(?:chips?|H200|B300)\b.{0,45}\b(?:demand|restricted|training)\b|\bdemand for Nvidia\b|\bCerebras\b.{0,65}\bcloud growth\b'))
        # OR-only groups are equivalent to one OR group. Share the source-tail
        # guard once per field, keeping native configuration compact.
        merged, rest = {}, []
        for group in groups:
            if 'all' in group or 'none' in group:
                rest.append(group)
                continue
            for item in group['any']:
                field, pattern = item['field'], item['regex']
                if field == 'body' and '(*SKIP)' in FOOTER and pattern.startswith(FOOTER.split('%s')[0]):
                    pattern = pattern[len(FOOTER.split('%s')[0]):-1]
                merged.setdefault(field, []).append(pattern)
        compact = {'id':'business_scope','any':[]}
        for field,patterns in merged.items():
            if field=='body':
                compact['any'].extend(term(p,field) for p in patterns)
            else:
                compact['any'].append(term('|'.join('(?:'+p+')' for p in patterns),field))
        groups=[compact,*rest]
        model = TickerSourceBinding(**{k: binding[k] for k in ('binding_id','ticker','source_id')}, extra_relevance={'groups': groups})
        rules[binding['binding_id']] = model.extra_relevance
    (ROOT / 'rules-selected.json').write_text(json.dumps(rules,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
