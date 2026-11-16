# Massive 四 ticker 简化重调 · 2026-11-12

本轮按最新要求，以**表达搭配直观合理、准确率优先、不过度设计**为优先级，基于原调试集重写 BE、COHR、NBIS、CEG。共5个候选：四个首版，NBIS一次仅删除分支的修正。没有容量数字、单位推断、任意词距、前后向断言、文章特判或负例黑名单。未追求把每个漏判“补到过线”。

## 本轮结果

每ticker仍为50条调试＋50条旧验证。P为准确率、R为召回率，过线仍是两项严格>80%。**调试BE、CEG过线；若同时看旧验证，仅CEG两集过线。**

|Ticker|本轮版本|调试 P / R|旧验证诊断 P / R|两集四项均>80%|
|---|---|---:|---:|---|
|BE|attempt-01|100.00% / 88.89%|44.44% / 33.33%|否|
|COHR|attempt-01|100.00% / 73.68%|80.00% / 40.00%|否|
|NBIS|attempt-02|100.00% / 69.57%|100.00% / 16.67%|否|
|CEG|attempt-01|100.00% / 95.00%|86.67% / 86.67%|是|

**旧验证已经在上一轮曝光，本轮不可能恢复成未见数据。** 候选依据调试集及业务范围设计，最终版本在此次复测前冻结；旧验证只作诊断，不据其坏例追加修改，不把本轮分数称为新的独立泛化验收。NBIS第二版在复测前按业务合理性确定，未按验证均分改选首版。

## 简化方式

- 标题/摘要中的公司主体、具体产品市场优先；正文仅保留少量有明确业务对象的词组。多组OR，主语可以出现在标题或摘要，正文规则独立补充。
- 不把普通AI、云、能源、data center或“建设/扩张”动词全篇共现当成业务联系，不要求容量达到特定数字。
- 不为每个行业对象穷举订单、收入、发布、采购等事件谓词；不按体裁排除通知、诉讼或市场报告。
- 保留正文独立事实的业务相关GOLD；简单规则匹配不到就记FN，不改变业务定义或标签替表达式辩护。
- 英文fallback包含实际语料词组；没有强制补齐三语。L1、definition等原字段不变。

|Ticker|上一轮入选组数 → 本轮组数|去重后表达式字符数：旧 → 新|
|---|---:|---:|
|BE|12 → 6|2075 → 626|
|COHR|8 → 5|1192 → 585|
|NBIS|14 → 5|1719 → 501|
|CEG|7 → 4|937 → 476|

字符数只描述可维护性，不是质量分。尤其NBIS的结果表明，表达短并不自动代表召回合理。

## 与上一轮入选版本对照

|Ticker|上一轮调试 P / R|本轮调试 P / R|上一轮旧验证 P / R|本轮旧验证 P / R|
|---|---:|---:|---:|---:|
|BE|92.59% / 92.59%|100.00% / 88.89%|68.75% / 91.67%|44.44% / 33.33%|
|COHR|100.00% / 100.00%|100.00% / 73.68%|61.54% / 80.00%|80.00% / 40.00%|
|NBIS|90.91% / 86.96%|100.00% / 69.57%|100.00% / 72.22%|100.00% / 16.67%|
|CEG|100.00% / 90.00%|100.00% / 95.00%|100.00% / 73.33%|86.67% / 86.67%|

这是冻结输入相同的版本对照，不是线上实绩。没有因为旧版均分更高，就在本轮按验证成绩反向拼装表达式；上一轮的九套入选文件保持原样。

## 逐ticker取舍与调试偏差

### BE

首版6组，没有样本ID、数值门槛、词距或排除清单。调试漏掉3条：数据中心供电储能综合报告、澳洲输电建设的物流报告、学校捐赠稿中的独立电网建设事实。未为这3篇补泛电网/建设词。旧验证诊断明显较差；业务范围和正文表达覆盖仍不足，保留简化方案但不建议替换旧版。

- `F060d873c22a0fd-BE`：Global Data Center Power, Cooling and Energy Storage Market 2027-2037 | AI Demand and High-Density Racks Drive Grid-to-Chip Infrastructure Investment。GOLD：报告分析数据中心电力约束、现场燃料电池与备用发电，涉及Bloom固定式供电市场。
- `F30abefa2b5611d-BE`：Australia Project Logistics Market - Size, Share, Industry Trends, and Growth Forecasts (2026-2031) | Mining and Grid Investment Propel Revenue to USD 10.27 Billion。GOLD：澳大利亚输电项目五年管线达360亿澳元并带动变压器和发电设备运输，反映电网建设供电条件变化。
- `F8e0e659f597556-BE`：Compass Datacenters Makes One-Time $5 Million Gift to Meridian's Public Schools。GOLD：Compass披露Meridian数据中心建设、首栋调试及所需电网投资，与大型负荷供电相邻。

### COHR

5组，围绕光通信/光子产品与少量明确的算力供给主题。调试漏掉5条：综合数据中心市场、Bitzero设备到货、设施运维、配电设备和印尼建筑报告中的独立算力设施事实。它们按原GOLD仍相关；本轮未把其遗漏解释为业务上应当无关。撤掉数字与宽动作推断后，这些间接消息覆盖变弱；不为凑召回扩充项目谓词。

- `F1fdc34387a1bca-COHR`：Data Center Market to Reach USD 1,103.70 Billion by 2035 as AI Capacity, Hyperscale Capex and Power Access Reshape Global Infrastructure。GOLD：文章描述超大规模数据中心和计算设施扩建，形成数据中心网络互连的具体需求近邻。
- `F29fee94e01c102-COHR`：RETRANSMISSION: Bitzero Receives All Long-Lead High-Voltage Equipment for Its Flagship Norway Data Center and Provides Recap in Shareholder Letter from Founder & CEO。GOLD：挪威数据中心已运营40MW并推进至110MW扩容，电力设备到货和施工支持算力设施及互连需求。
- `Faf409ced410796-COHR`：Data Center Facility Management & O&M Services Market Size, Share, and Industry Analysis - Global Forecast to 2036 | AI and Liquid Cooling Drive 12.9% CAGR to $33.32 Billion。GOLD：报告讨论数据中心容量扩建并预测全球容量增长，涉及数据中心网络和光互连需求近邻。
- `Fba9c7c247e2da6-COHR`：Global Data Center Electrical Equipment Market Size, Share, Trends, and Forecast, 2026-2036 | AI and Hyperscale Demand to Drive 11.8% CAGR, Reaching USD 98.6 Billion by 2036。GOLD：报告描述吉瓦级数据中心园区部署及超大规模设施扩建，形成光互连设施需求近邻。
- `Fc470fe05afe644-COHR`：Indonesia Construction Market Size, Share, Industry Trends, and Forecast, 2026-2031 | Megaprojects and Data Centers Drive 5.81% CAGR to USD 436.92 Billion。GOLD：报告具体披露雅加达120MW数据中心建设和500MW AI设施计划，形成光互连设施需求近邻。

### NBIS

首版的AI数据中心标题词误收固态变压器融资稿，不能区分算力设施主体和器件终端用途。第二版只删此分支，其他表达式保持原样，未加负例黑名单。调试由18TP/1FP变为16TP/0FP；同时失去散热与IT资产退役报告，累计7个FN，还包括实际计算部署、云市场和供给合同。旧验证3TP/0FP/15FN，说明保守简化代价很大，不能称为效果优于旧版，也不建议直接用于生产替换。

- `F0c6f63104e9803-NBIS`：North America Dry Cooler Market Size, Share, and Trends Report, 2026-2033 | AI Data Centers Drive 8.2% CAGR and a USD 1.76 Billion Opportunity by 2033。GOLD：报告指出AI及高密度数据中心扩容提高散热负荷并推动冷却设备需求，与AI云设施冷却条件相邻。
- `F3324658eb5434f-NBIS`：Global IT Asset Disposition Market Share, Trends, and Growth Forecasts, 2026-2031 | AI Data-Center Refreshes and ESG Mandates Propel Market to $39.66 Billion。GOLD：报告称超大规模运营商以GPU密集基础设施替换传统服务器，反映云计算硬件更新与容量路线。
- `F73c7bff906c03b-NBIS`：NVIDIA, Rebellions and SK Telecom Announce Collaboration to Advance Sovereign AI Infrastructure With NVIDIA NVLink Fusion。GOLD：合作计划将R200和NVIDIA互连机架部署在SK Telecom AI数据中心，涉及AI云基础设施供给。
- `Fafa888b0027c1f-NBIS`：AIB Advances CLT1 Delivery With Funded Prepayment and Executed Equipment Orders。GOLD：CLT1 的 50 MW AI/HPC 数据中心项目已签客户协议并推进采购，涉及云算力设施供给。
- `Fb75f7ae118d0da-NBIS`：UAE Data Center and AI Infrastructure Market Study 2026-2035 | Analysis of Competitive Positioning Among Developers, Operators and Infrastructure Participants。GOLD：阿联酋数据中心研究讨论现有和计划供给、超大规模需求及电力接入，涉及云算力设施市场。
- `Fcb1d3d54999b5d-NBIS`：Global Accelerated Computing Market Size, Share, Industry Trends, and Growth Forecasts, 2026-2031 | AI Training and Edge Inference Fuel 20.19% CAGR and Custom Silicon Opportunities。GOLD：报告披露大型GPU集群和云实例部署，以及加速计算与云基础设施需求变化。
- `Fd05cda0560166c-NBIS`：Lost Money on Cerebras Systems Inc. Shares? The Law Offices of Frank R. Cruz Continues Investigating Potential Securities Fraud After 19.6% Stock Decline。GOLD：调查披露 Cerebras 为满足云算力积压订单租回系统并建设自有数据中心，涉及竞争算力供给。

### CEG

4组，保留核能/燃料/废物、发电与电网储能、少量正文经营事实，不给每个词绑定订单或建设动作。调试仅漏Chesapeake公用事业财报中的配电项目；没有单独加公司名救这一条。两集均过线，但验证是已曝光旧样本，不能把结果当作新的独立验收。

- `F3976354a44cf80-CEG`：CHESAPEAKE UTILITIES CORPORATION REPORTS THIRD QUARTER 2026 RESULTS。GOLD：Chesapeake披露电力配电防风暴投资和电价举措，属于电网输配电经营市场。

## 全部候选

|Ticker / Attempt|调试 TP/FP/FN/TN|调试 P / R|旧验证 TP/FP/FN/TN|旧验证 P / R|
|---|---:|---:|---:|---:|
|BE / attempt-01|24/0/3/23|100.00% / 88.89%|4/5/8/33|44.44% / 33.33%|
|COHR / attempt-01|14/0/5/31|100.00% / 73.68%|4/1/6/39|80.00% / 40.00%|
|NBIS / attempt-01|18/1/5/26|94.74% / 78.26%|7/1/11/31|87.50% / 38.89%|
|NBIS / attempt-02|16/0/7/27|100.00% / 69.57%|3/0/15/32|100.00% / 16.67%|
|CEG / attempt-01|19/0/1/30|100.00% / 95.00%|13/2/2/33|86.67% / 86.67%|

## 文件与复现

- [四套本轮候选](../eval/massive_distribution_regex_round2_20261112/proposed/README.md)：BE/COHR/CEG首版、NBIS删减版。
- [完整表达式及字段](../eval/massive_distribution_regex_round2_20261112/rules.md)，方便直接检查搭配是否合理。
- [冻结回执](../eval/massive_distribution_regex_round2_20261112/final-freeze.json)；[全部指标](../eval/massive_distribution_regex_round2_20261112/summary.json)；[核验](../eval/massive_distribution_regex_round2_20261112/verification.json)。
- 每个候选目录保留完整输入、GOLD理由、逐条预测、命中片段和FP/FN；全部5候选均有两集结果。
- [唯一共享经验](by-distribution-relevance-debug-experience.md)新增4个round2 session，NBIS两版合并为同一条；上一轮记录保留。

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe eval/massive_distribution_regex_round2_20261112/finish.py
```

核验实际重放500条候选预测，结果与记录一致，正则错误/超时0；输入与GOLD哈希、候选哈希、两集配置及冻结时序一致。配置只改L2，未调用Decision Model、未改远端生产。

本轮仅CEG具备两集样本门槛结果；其他三个保留为未通过的简化实验。特别是BE和NBIS，不能把更好读的规则当成更好的筛选结果。原池是旧正则阳性且标题富集的样本，召回仍只是原召回池内保留率，精度不是自然生产精度。
