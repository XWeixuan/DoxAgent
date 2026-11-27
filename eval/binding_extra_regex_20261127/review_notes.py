"""Human review notes, authored after reading the frozen bodies (not an auto labeler).

Indices refer to immutable review-input snapshots kept in the local evidence archive.
The final ledger is per message x ticker; shared text is read once, business scope per target.
"""

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

R = Path(__file__).resolve().parent
notes = {}


def add(version, indices, label, reason):
    rows = json.loads((R / f"review-{version}.json").read_text(encoding="utf-8"))
    for index in indices:
        title = rows[index]["title"]
        notes[title] = (label, reason)


add(
    "v7",
    [0],
    "LOW_RELEVANCE",
    "模型榜单、上下文和一次训练费用；未陈述芯片/内存资源配置或设施采购。训练金额本身不等于硬件投资。",
)
add("v7", [1], "IRRELEVANT", "LangSmith 调试、红队及工作流修复功能；没有硬件部署或内存架构变化。")
add("v7", [2], "IRRELEVANT", "代理支付路由、稳定币充值及免费额度；并非计算资源供给。")
add(
    "v7",
    [3, 45],
    "LOW_RELEVANCE",
    "美国/英国获得模型评估权限的次序与审计安排；无芯片、算力设施或供电措施。",
)
add("v7", [4], "LOW_RELEVANCE", "匿名模型的一百万上下文和建站功能；无可识别硬件部署条件。")
add(
    "v7",
    [5, 6, 7, 8, 10, 41, 50, 52, 63, 68, 69, 78],
    "IRRELEVANT",
    "代理越权、凭据窃取、网站探测或沙盒实验；正文无目标产品变化或具体产业采购。云/服务器在这里是攻击环境。",
)
add("v7", [12], "IRRELEVANT", "自动转移银行存款与银行挤兑风险，业务对象是银行账户收益。")
add("v7", [14], "IRRELEVANT", "代理身份验证、支付反欺诈融资与商业协议，非目标硬件或算力服务市场。")
add("v7", [15], "IRRELEVANT", "投标欺骗、实验性挖矿和模型安全团队；不能据此推导芯片内存需求。")
add(
    "v7",
    [18, 21],
    "IRRELEVANT",
    "memory 指项目上下文/知识图谱记忆；不是 DRAM/NAND，也没有光互连或设施事实。",
)
add(
    "v7",
    [19, 39, 60, 116],
    "IRRELEVANT",
    "办公助手、个人任务或桌面控制功能；未给出目标硬件资源或云算力供给变化。",
)
add(
    "v7",
    [23, 71, 86, 112, 115],
    "IRRELEVANT",
    "数学/理论题证明、形式验证或单次解题成本；租用服务器是单个实验工具，不是算力服务市场变化。",
)
add(
    "v7",
    [24, 31],
    "IRRELEVANT",
    "键盘的按键、灯效与零售价；页尾 CPU/RAM deals 和作者简介是导航/背景。",
)
add(
    "v7",
    [29, 33, 87],
    "IRRELEVANT",
    "显示面板画质、刷新率、接口和促销/评测；无独立 CPU 或存储竞争事实。",
)
add("v7", [30], "IRRELEVANT", "鼠标按钮、光学传感器与零售价；不是存储市场变化。")
add("v7", [32], "IRRELEVANT", "座椅及记忆棉靠垫；memory foam 不是芯片内存。")
add("v7", [34], "IRRELEVANT", "耳机音质、麦克风及佩戴体验；没有目标产品/产业信息。")
add("v7", [35], "IRRELEVANT", "耳机挂架兼 USB 充电器；作者顺带 SSD 测试不构成存储新闻。")
add("v7", [37], "IRRELEVANT", "个人 VPN 订阅优惠，不是 GPU 云/托管推理供给或芯片存储市场。")
add("v7", [38], "IRRELEVANT", "PC 除尘器与风机电池促销；页尾硬件词不构成独立业务事实。")
add(
    "v7",
    [40],
    "LOW_RELEVANCE",
    "点对点通信与加密消息网络软件；无处理器产品、GPU 租赁或硬件采购变化。",
)
add(
    "v7",
    [42],
    "LOW_RELEVANCE",
    "模型蒸馏的知识产权指控；这篇正文没有具体芯片出口限制或算力资源措施。",
)
add(
    "v7",
    [44],
    "LOW_RELEVANCE",
    "分类/提取模型的 API 价格与任务效率属于软件服务，INTC 下无硬件配置或 CPU 比较。",
)
add(
    "v7",
    [49, 61, 64],
    "IRRELEVANT",
    "模型指纹、安全护栏或评测流程研究；没有目标硬件技术/部署变化。",
)
add(
    "v7",
    [54, 56, 59, 76],
    "LOW_RELEVANCE",
    "一般 AI 安全立法、停机授权或模型阈值；无具体半导体产业或硬件采购措施。"
    "FLOPs 作为监管定义不自动等于芯片政策。",
)
add(
    "v7",
    [65],
    "LOW_RELEVANCE",
    "Copilot 模型接入、任务榜单和 token 定价；没有 CPU 产品或硬件部署条件。",
)
add("v7", [67], "IRRELEVANT", "AI 公司内部 HR/销售/IT 系统入侵，未触及目标产品或硬件供应链。")
add("v7", [70], "LOW_RELEVANCE", "开源权重与模型分数变化，没有资源配置或芯片市场事实。")
add("v7", [75], "IRRELEVANT", "小模型结构改写提升推理分数，未讨论硬件需求或部署。")
add("v7", [77], "IRRELEVANT", "用既有 NASA LiDAR 数据发现遗址，未发布 CPU/硬件或采购变化。")
add(
    "v7",
    [80],
    "LOW_RELEVANCE",
    "模型间付费 token 份额和支出迁移；"
    "无总体硬件容量或采购变化，INTC 传导较弱。",
)
add("v7", [81], "IRRELEVANT", "课堂 AI 教学与摄像头推广，无具体 CPU/服务器投资或半导体措施。")
add("v7", [82], "IRRELEVANT", "蛋白结合率和药物模型有效性；没有目标芯片或硬件部署信息。")
add("v7", [84], "IRRELEVANT", "自动撰写论文与同行评审行为研究，没有硬件市场变化。")
add("v7", [85], "LOW_RELEVANCE", "软件模型地缘风险与联邦软件采用建议；无处理器/制造/算力采购措施。")
add("v7", [88], "IRRELEVANT", "消费 3D 打印机、耗材与工具优惠，未含独立 CPU 产品或制造投资。")
add(
    "v7", [90, 122], "IRRELEVANT", "Adobe/Disney 视觉素材和创作软件合作，无数据中心建设或电力事实。"
)
add(
    "v7",
    [28],
    "LOW_RELEVANCE",
    "OLED 出货和显示器价格；NBIS 下未提供云算力供给变化。"
    "MU 下的 DRAM/NAND 价格事实已通过保护条件保留。",
)
add(
    "v7",
    [91],
    "LOW_RELEVANCE",
    "语音助手融资，Together AI 只是托管背景；COHR 下无光器件或设施项目。",
)
add("v7", [92], "LOW_RELEVANCE", "模型研发加速与安全政策预期；没有光通信或物理基础设施事实。")
add("v7", [93], "IRRELEVANT", "可解释性/安全软件收购洽谈，不是光通信产业交易。")
add("v7", [94], "IRRELEVANT", "企业定制 AI 软件融资与专有数据训练，不涉及光器件/设施。")
add("v7", [95], "IRRELEVANT", "药物发现融资与临床项目；不能由 AI 标签推导光通信需求。")
add("v7", [96], "IRRELEVANT", "PDF/表格结构化和查询软件；可在自有服务器部署不是新增设施。")
add(
    "v7",
    [97],
    "LOW_RELEVANCE",
    "代理应用托管与工作流平台；COHR 下缺少光网络或设施事实，只有软件平台连接。",
)
add(
    "v7",
    [99, 100, 101, 104, 107, 110],
    "IRRELEVANT",
    "企业代理治理、网站可用性、数据上下文或软件营销；半导体/算力仅泛背景，无独立硬件事实。",
)
add("v7", [103], "IRRELEVANT", "医疗检查费用与支付激励，不是 CPU/半导体产业变化。")
add(
    "v7",
    [105, 114],
    "IRRELEVANT",
    "凭据盗用、通用网络攻击和企业防御建议；未改变 GPU 云供给或硬件产品。",
)
add(
    "v7",
    [108],
    "IRRELEVANT",
    "模型模拟战场目标识别安全实验；机载延迟的概述不是 CPU 产品/部署变化。",
)
add("v7", [111], "IRRELEVANT", "AI 与延寿的哲学预测，不涉及目标市场。")
add("v7", [113], "IRRELEVANT", "消费者识别语音诈骗的建议，与目标硬件市场无关。")
add("v7", [118], "LOW_RELEVANCE", "微软代码模型 API 价格和办公软件集成；MU 下无物理内存需求事实。")
add("v7", [123], "IRRELEVANT", "晶圆造型电锅与员工礼品；并非半导体建设或工业电力需求。")
add(
    "v7",
    [124],
    "LOW_RELEVANCE",
    "本科量子光学教学与既有实验训练；"
    "无计算/通信/传感产品或独立研发成果。研究体裁本身未被排除。",
)
add("v7", [125], "IRRELEVANT", "Quantum-Si 的蛋白测序，不是量子计算公司或量子工程产品。")

add("v9-new", [0], "LOW_RELEVANCE", "BE 下是单款消费 SSD 促销和存储价格，未含设施/供电信息。")
add(
    "v9-new",
    [3],
    "LOW_RELEVANCE",
    "通用应用云平台之间的代理安全事件，无 GPU 租赁或推理产品供给变化。",
)
add(
    "v9-new",
    [7],
    "LOW_RELEVANCE",
    "DRAM 单元架构、样品与验证，没有新厂/供电项目；BE 不把全部内存研发纳入。",
)
add("v9-new", [8], "LOW_RELEVANCE", "消费 Mac/iPad 交期受内存短缺影响；缺少供电或设施传导。")
add("v9-new", [9], "LOW_RELEVANCE", "HBF 的 NAND 产能占用和 SSD 定价，未给出新厂建设或电力项目。")
add(
    "v9-new",
    [10],
    "LOW_RELEVANCE",
    "芯片封装内记忆体频宽和能效，不是设施级用电/供电变化；BE 下传导仍弱。",
)
add("v9-new", [15], "LOW_RELEVANCE", "DRAM 微影/蚀刻实验，不含工厂扩建或用电项目，BE 下较远。")
add(
    "v9-new",
    [17],
    "LOW_RELEVANCE",
    "暗能量和宇宙学常数的理论计算，全文未见计算/通信/传感工程用途。",
)

add("v15-new", [0], "LOW_RELEVANCE", "低成本 AI 模型融资与估值，资金未明确用于基础设施或电力项目。")
add(
    "v15-new",
    [1],
    "LOW_RELEVANCE",
    "AI 助手下载量、订阅变现与分析师目标价；BE 下没有电力传导事实。",
)
add("v15-new", [2], "IRRELEVANT", "生物安全与抗体药物融资，非电力或能源市场。")
add("v15-new", [3], "LOW_RELEVANCE", "模型公司创始人投票权结构；BE 下没有设施/供电关联。")
add("v15-new", [4], "LOW_RELEVANCE", "模型公司过桥融资满足一般运营需要；未披露设施/算力采购用途。")
add(
    "v15-new",
    [7],
    "LOW_RELEVANCE",
    "多家普通 VPS 上的攻击实例和身份认证绕过，无 GPU 云/托管推理产品变化。",
)
add(
    "v15-new",
    [8],
    "LOW_RELEVANCE",
    "普通应用云主机之间的安全事件及 IP 封锁，没有 GPU 云服务市场变化。",
)
add("v15-new", [9], "LOW_RELEVANCE", "Google 的医疗工作流应用，无算力租赁或托管模型服务供给事实。")
add("v15-new", [11], "LOW_RELEVANCE", "移动应用代码测试和云 iOS 模拟器；并非 GPU 云/推理服务供给。")
add(
    "v15-new",
    [13],
    "LOW_RELEVANCE",
    "应用层 token 预算、缓存和上下文路由管理，没有实际算力部署或推理产品价格变化。",
)
add("v15-new", [15], "IRRELEVANT", "代理共享消息板及权限功能，与 GPU 云/推理供给没有明确联系。")
add(
    "v15-new",
    [17],
    "IRRELEVANT",
    "Workspace 办公产品 REST/MCP 接口迁移，不是模型推理或云算力服务。",
)
add("v15-new", [18], "IRRELEVANT", "电商图片生成与购物信息流应用融资，无光通信/基础设施事实。")
add("v15-new", [19], "LOW_RELEVANCE", "代码助手估值和新融资洽谈；未披露设施或物理算力用途。")
add("v15-new", [21], "LOW_RELEVANCE", "模型公司的融资和主权独立目标，没有光通信或基础设施项目。")
add(
    "v15-new",
    [25, 36, 52],
    "LOW_RELEVANCE",
    "模型软件收入、客户支出或留存增长；全文未披露物理基础设施采购，COHR 下是较远的需求代理。",
)
add("v15-new", [27], "IRRELEVANT", "软件 SRE 故障定位平台融资，客户名含云厂商不等于光通信业务。")
add("v15-new", [29], "IRRELEVANT", "药物设计计算平台融资与蛋白发现，无光网络/设施用途。")
add("v15-new", [30], "LOW_RELEVANCE", "AI 公司融资估值和人员流失，缺少物理基础设施计划。")
add("v15-new", [39], "IRRELEVANT", "专家劳务、训练数据和招聘访谈融资，非光器件或算力设施投资。")
add(
    "v15-new",
    [47, 50],
    "LOW_RELEVANCE",
    "计算芯片融资与堆叠 DRAM 路线，不含光互连/光器件或设施项目，COHR 下较远。",
)
add("v15-new", [48], "LOW_RELEVANCE", "模型安全阈值与研发提速声明，没有光互连/设施产品事实。")
add("v15-new", [51], "LOW_RELEVANCE", "模型公司一般融资金额和估值；未披露基础设施投入用途。")
add("v15-new", [53], "LOW_RELEVANCE", "模型公司估值与蒸馏限制，COHR 下无物理产业联系。")
add(
    "v15-new",
    [57, 58, 65, 67],
    "LOW_RELEVANCE",
    "IPO 前后指数/股价行情，未披露光通信、基础设施或硬件经营事实。",
)
add("v15-new", [66], "IRRELEVANT", "FTX 旧持股的假设市值和破产清偿，非光通信市场。")
add(
    "v15-new",
    [68],
    "LOW_RELEVANCE",
    "尚未发布产品的 AI 软件公司融资估值，未披露硬件或基础设施用途。",
)
add(
    "v15-new",
    [71, 72],
    "LOW_RELEVANCE",
    "模型公司的股价与软件使用条款争议，全文无光互连或设施变动。",
)
add("v15-new", [73], "IRRELEVANT", "客服代理软件的收入、估值及融资洽谈，非光器件市场。")
add("v15-new", [75], "IRRELEVANT", "通用软件漏洞武器化和网络攻击，不是 GPU 云或托管推理产品变化。")
add(
    "v15-new",
    [
        20,
        23,
        24,
        31,
        32,
        34,
        35,
        37,
        38,
        40,
        41,
        42,
        43,
        44,
        45,
        46,
        49,
        54,
        55,
        56,
        59,
        60,
        61,
        62,
        63,
        64,
        69,
        70,
    ],
    "LOW_RELEVANCE",
    "IPO 定价、认购/配售、股权/投票权或估值快讯。"
    "正文可能列出芯片/云供应商作为股权投资者，"
    "但没有光通信、物理资源采购或设施用途；不把融资金额直接等同于新增光互连订单。",
)

new = json.loads((R / "review-v13-new.json").read_text(encoding="utf-8"))
notes[new["title"]] = (
    "LOW_RELEVANCE",
    "黑洞奇点、广义第二定律与时空理论；无量子计算/通信/传感工程联系。已用生产相同清理移除来源模板。",
)

rows = [
    json.loads(line)
    for line in (R / "blocked-selected.jsonl").read_text(encoding="utf-8").splitlines()
]
ledger = []
for row in rows:
    assert row["title"] in notes, "UNREVIEWED: " + row["title"]
    label, reason = notes[row["title"]]
    ledger.append(
        {
            **{k: row[k] for k in ("id", "ticker", "source_id", "title", "url")},
            "review": label,
            "serious_miss": False,
            "reason": reason,
            "body_sha256": hashlib.sha256(row["body"].encode()).hexdigest(),
        }
    )
(R / "blocked-review.jsonl").write_text(
    "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in ledger), encoding="utf-8"
)
with (R / "blocked-review.csv").open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(ledger[0]))
    writer.writeheader()
    writer.writerows(ledger)
print(json.dumps(dict(Counter(x["review"] for x in ledger))))
