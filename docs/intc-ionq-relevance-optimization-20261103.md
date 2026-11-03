# INTC 提准与 IONQ 首轮相关性调试、生产应用

日期：2026-11-03。结果目录：[完整证据](../eval/relevance_intc_ionq_20261103/verification.json)。已在北京时间 **19:40:20** 完成两套监测词应用：**INTC rev3→4；IONQ rev1→2**，独立只读回读与实际运行模块哈希一致。其他八个 ticker 的词表未改。

## 结果及解释

INTC 本轮以合理提准为目标，没有套用通常的每轨准召 >80% 验收。固定旧集之外，又冻结并逐条标注 **156 条当前真实输入**，包括共享源120条和Google36条。在同一份复核后 GOLD 上：

|INTC 当前样本|原配置/接纳路径 P/R|新配置 P/R|误召 FP 变化|
|---|---|---|---|
|五个共享源120条，两轨并集|71.25% / 96.61%|76.32% / 98.31%|23→18|
|Google36条，原直接接纳→新两轨判定|82.86% / 100.00%|96.67% / 100.00%|6→1|

共享源误召减少 **21.74%**，召回没有为降量牺牲；正则自身由 73.61% / 89.83% 变为 93.18% / 69.49%，更多有歧义的文章转交语义轨道。以上是分层样本的离线回放，**不是全量生产降量承诺**。Google数字只衡量正文后接纳，不包含新增搜索上下文对上游召回的改变。

IONQ 保持原来的 Recall-first 业务尺度。新建100/100 GOLD，模型子集50/50。正则第一候选、模型第二候选在调试集均达标后停止常规开发，冻结两轨后才验证；验证也均达标，无需额外修正。

|IONQ选定版本|调试 P/R|验证 P/R|
|---|---|---|
|正则 attempt-01|100.00% / 97.44%|93.59% / 93.59%|
|Decision attempt-02|94.87% / 94.87%|97.78% / 100.00%|
|50条共有子集上的两轨并集|94.87% / 94.87%|95.65% / 100.00%|

## INTC：改了什么

1. **搜索词消歧。** 保留公司名 `Intel`，避免改成狭窄的固定公司全称短语；Google为INTC单独配置10个上下文literal，构成AND条件：

   `Intel ("INTC" OR "Intel Corporation" OR "semiconductor" OR "chip" OR "CPU" OR "processor" OR "foundry" OR "Xeon" OR "Core Ultra" OR "Lip-Bu Tan") when:1d`

   未设置域名白名单。公司可在一般新闻站被报道，体育站也可能有真实公司消息，域名不作为语义真值。统一L1中裸 `foundry` 改为 `Intel Foundry`，中韩文同样限定公司，避免其他搜索源独立召回整个代工行业；`Xeon` 保留。

2. **搜索命中不再等于相关。** 原 BY_SEARCH 绕过相关性判定。Google新增可选binding参数 `context_terms` / `require_relevance`，仅INTC开启；取得正文后复用既有 LiveDeliveryWorker 的正则→模型补充放行路径。任一轨相关可以发布，模型不能否决正则误放；两者均否不进入Bus。没有新建服务或另一套分类器。其他Google绑定参数、启停和轮询设置不变，仅随源schema升级更新版本元数据。

3. **正则收回宽主题的直接通行权。** 删除泛半导体、泛AI投资、混合专家模型、量子实验室、全篇跨字段“投资+数据中心”凑词等组。保留明确公司/产品、CPU/GPU和制造市场，并限定通用产品与制造信号的直接放行字段；具体算力合同可在正文识别。复杂项目动词表达式表现不佳时，改用简单的数据中心标题主题，并对直接配套产品作局部消歧。实际建设、取消、项目风险、计算资本支出仍可以由模型放行，不要求披露Intel订单。

4. **模型定义区分业务市场和相邻器件。** 保留CPU/竞争计算芯片、逻辑制造、先进封装及实际算力项目；明确独立内存、PCB、MLCC、光通信、家电、供电冷却、泛AI应用等不因含热门词自动相关。正式活动、研究和早期计划仍可进入。最终采用 attempt-05；旧验证子集上的并集 P/R为 75.86% / 88.00%，没有将其包装成每项过线。

本次发现正文中推荐阅读会混入CPU标题，正则和模型都可能误认作实质事实。没有修改冻结输入、按文章加黑名单或批量删除既有消息。剩余误召包括Google ESPN选秀长文（模型0.53，正则未中）；搜索上下文有望减少这类入口，但不能保证消失。共享源仍漏一条讨论量化、KV cache及GPU显存资源的LLM技术入门文章。详细文章、命中片段与分数在 `INTC/*/attempt-05/risk-*/`。

## 数据、标注与可比性

### INTC

- 原正则100/100：`distribution_relevance_gold_v2_20261028/sample1800`；原模型50/50：`distribution_relevance_debug_20261028/decision50`，未重抽或改旧GOLD。旧验证集此前已参与调试，**本轮不声称未见或独立验收**。
- 新风险样本按 retained live delivery 的首次 `created_at` 取 10月29日06:00UTC至11月3日07:00UTC，再按固定seed分层抽取。不是审计报告15日正式发布量的同一分母，也不是只挑已发布正例。来源：Hugging35、DCD30、Tom’s20、DIGITIMES20、TechNews15、Google36。Google分层含六条风险域名输入及其余随机30条，不能当流量加权精度。
- 120条共享源中历史实际PUBLISHED82条，另有IRRELEVANT及CONFIG_INCOMPLETE。接口未执行/403不能作负预测。原配置回放使用恢复权限后的相同模型，不能与历史当时的发布收据混称。
- GOLD由用户指定的 `gpt-6.1-terra` high 独立逐条阅读，判定结果/分数隐藏。初版69 RELATED、86 IRRELEVANT、1 INSUFFICIENT。之后发现“实际机房建设但无设备采购细节”被加了原规则不存在的门槛，要求标注员不看候选/预测做一致性复核，形成19项改判、3项仍存疑，最终 **88 RELATED / 67 IRRELEVANT / 1 INSUFFICIENT**。这保留了原有召回尺度，而非用严格标签证明严格规则。
- 原标注、完整复核理由及双口径重评分均保留：[初版](../eval/relevance_intc_ionq_20261103/intc-retained-gold-labels.jsonl)、[复核](../eval/relevance_intc_ionq_20261103/intc-gold-review.jsonl)、[两口径指标](../eval/relevance_intc_ionq_20261103/INTC/initial-vs-reviewed-gold-metrics.json)。未重抽成功模型回答。最终第五候选是在复核尺度下开发；当前样本已用于开发，不能宣称独立泛化成绩。
- 原审计Hugging的263/280是历史已发布消息；本轮35条含拒绝和故障输入，历史实际仅13条已发布、其中12条正则命中，因此不能直接将263/280与本轮12/35对比成降量成果。

### IONQ

IONQ已经有rev1种子词表。旧 `distribution_*` 表为空，但新实时链路的 `live_*` 表实际留存62条；不是“共享源没有运行”。新绑定11月1日才创建，无法从生产历史直接凑出200条。用户明确同意从已绑定来源补采公开历史文章。

构建过程保存完整454条去重来源池。抽样只按来源和固定seed，不看标签或系统判定；精确URL/规范化标题跨集重叠为0。开发/验证各100条，来源分层为：

|来源|开发|验证|
|---|---:|---:|
|Quantum Zeitgeist|38|39|
|Quantum Computing Report|35|35|
|Quantum Insider|6|6|
|NIST|20|20|
|NSF|1|0|

两名 `gpt-6.1-terra` high 标注员分别读完整冻结输入，互不读取另一集合；开发者未在两轨冻结前查看验证标签。GOLD分布开发{'RELATED': 78, 'IRRELEVANT': 22}，验证{'IRRELEVANT': 22, 'RELATED': 78}。模型50/50由各自父集均匀无放回抽取，seed2026110308，不按标签挑选。

按官方当前业务覆盖计算、网络、感知、安全及量子空间基础设施，不限于离子阱计算。对技术研究、精密时钟、后量子安全、竞品和正式活动保留正常召回；普通物理/材料/数学须有可识别业务或技术联系。业务范围参考 [IonQ官方介绍](https://investors.ionq.com/overview/)，Intel范围参考 [Intel公司介绍](https://www.intel.com/content/www/us/en/company-overview/company-overview.html)。

补采来自同源公开WordPress文章及官方RSS/正文，明确标记 `offline_public_*`，没有伪装成四个完整生产交易日。NRO、NSF正文与Quantum Insider公开历史API访问失败留有回执；NRO没有样本，NSF只纳入一条真实留存。NIST跨数月、量子源较近期，来源权重不代表生产；不同文章的同一事件仍可能跨集合，不能保证语义事件完全隔离。详见[抽样manifest](../eval/relevance_intc_ionq_20261103/IONQ/sample200/manifest.json)及[抓取回执](../eval/relevance_intc_ionq_20261103/public-fetch-receipts.json)。

## 全部候选及验证

INTC按本轮特别要求放宽通常轮数与验收，实际评估5个正则、5个模型候选；IONQ严格按SOP，在第1个正则和第2个模型达标后停止，没有使用额外验证修正。所有13个候选均有完整的一次调试和一次验证，共26份候选评分结果；最终错误0。模型仍是正式 `decision-model-preview`、正式切段/上限/重试/聚合实现，阈值固定0.5；不向模型提供GOLD或理由。

|Ticker|模块|候选|调试 P/R|验证 P/R|最终选用|
|---|---|---|---|---|---|
|INTC|regex|attempt-01|100.00% / 39.47%|82.35% / 29.17%||
|INTC|regex|attempt-02|100.00% / 47.37%|82.61% / 39.58%||
|INTC|regex|attempt-03|100.00% / 44.74%|81.82% / 37.50%||
|INTC|regex|attempt-04|100.00% / 55.26%|82.61% / 39.58%||
|INTC|regex|attempt-05|100.00% / 57.89%|81.48% / 45.83%|是|
|INTC|decision|attempt-01|61.54% / 84.21%|65.22% / 60.00%||
|INTC|decision|attempt-02|76.19% / 84.21%|81.82% / 72.00%||
|INTC|decision|attempt-03|80.00% / 63.16%|78.57% / 44.00%||
|INTC|decision|attempt-04|41.86% / 94.74%|56.76% / 84.00%||
|INTC|decision|attempt-05|75.00% / 94.74%|76.00% / 76.00%|是|
|IONQ|regex|attempt-01|100.00% / 97.44%|93.59% / 93.59%|是|
|IONQ|decision|attempt-01|100.00% / 10.26%|100.00% / 6.82%||
|IONQ|decision|attempt-02|94.87% / 94.87%|97.78% / 100.00%|是|


新API key前，本地现有主/备用及远端生产凭据均返回403 `AccessDenied.Unpurchased`。用户提供的新key验证200后才继续。失败日志保留，未计作不相关；429经正式重试恢复，成功预测复用。正式模型 API 参考[百炼文档](https://help.aliyun.com/zh/model-studio/decision-model-api)。356条新增文章×ticker均已标注，未将全部未抽中来源池假称为已标注。

## 生产与代码证据

- [词表应用回执](../eval/relevance_intc_ionq_20261103/deployment/apply-terms-receipt.json)：通过 `MonitoringTermsService.apply` 发布INTC4、IONQ2，Google参数通过现有 `MessageBusV2Service.update_source` 原子更新；无原始SQL写配置。其他八个ticker词表逐项确认未改。
- [独立运行探针](../eval/relevance_intc_ionq_20261103/deployment/independent-runtime-probe.json)：新进程只读回读完整词表，验证实际查询、Google gate及四个运行模块hash；远端用实际环境key调用模型成功，合成CPU消息0.95、体育情报消息0.19。这两条是连通/路径探针，不是GOLD样本。
- 窄补丁提交 [460fda18](https://github.com/XWeixuan/DoxAgent/commit/460fda18)，已快进推送到origin/main。隔离分支只包含四个产品文件、针对性测试及changelog，未夹带工作区其他修改。已有真实运行镜像为基底，只覆盖四模块；未重建或发布其他角色。镜像 `sha256:a1846aaeaefed5b0bda2829bcf64031b832bb04b9565aaf92947c3ac1dd39eb1`，运行且restart0。覆盖层在完整镜像之上，不能把基础镜像继承的全树commit环境变量当本补丁源码身份；补丁commit与实际文件hash以回执为准。
- [发布回执](../eval/relevance_intc_ionq_20261103/deployment/runtime-release-receipt.json)：复用host `storage_release` 锁、部署、回滚及资源回收。发现host记录落后于前一紧急发布时，先以实际运行角色建立回滚基线，未回退到旧镜像。新key仅覆盖Message Bus角色的私有环境，没有写入镜像、仓库或报告。一次部署元信息原始快照意外含凭据字段，发现后已从本地审计文件剔除；最终交付只保留允许字段。
- 53项Google路径及既有规则测试，49项实时发布/去重回归通过。新增helper与测试Ruff通过；整文件lint存在原有30项问题，未扩大本轮修改范围。
- 本轮没有历史消息/Case删除、重放或单ticker配额截断。发布后截至 `2026-11-03T11:45:12.621354+00:00` 的短观察尚不足以形成自然流量准召或降量结论；发现的新rev记录若仅SUPPRESSED不能证明已走完相关性链路。配置生效与长期效果分开验收。

共享经验已合并到唯一账本：[INTC/IONQ复盘](by-distribution-relevance-debug-experience.md)，每次ticker级会话一条；具体文章及验证答案不写入共享经验。
