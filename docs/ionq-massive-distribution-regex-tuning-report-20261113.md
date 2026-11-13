# IONQ Massive Distribution 正则调试与上线 · 2026-11-13

已构造并逐篇标注调试50、验证50，按SOP完成1个常规候选及1个额外修正版。最终采用attempt-02-overfit，生产IONQ词库revision=5，六个Massive来源均已绑定启用。本轮只调Massive正则，未调用Decision Model、未修改普通L1/L2/definition/X账号，也未修改其余九ticker词库或全局源配置。

## 数据与GOLD

- 用户明确本轮沿用Massive专项50/50。扫描六源留存FULL/SHORT_FULL记录16,119条，按冻结的IONQ第4版专用规则回放召回136条；该专用规则当时与普通L2相同，是Bloomberg上线时预置的基线，未有Massive专项调试验收。
- 文本/URL初步去重117家族，补齐平台公告主键后116家族。最终100条各自公告主键、URL别名、规范化正文及文章家族无跨集或集内重复。采集留存来自2026-11-03至12日，不声称两个自然日全量档案。
- 验证样本避开上一轮九ticker已经使用的文章；调试集允许22个先前暴露家族。只读取旧集合身份字段，不导入旧GOLD。原有IONQ标注此前未用于九ticker调词，本轮仍重新逐篇标注。
- 只由gpt-6.1-terra、high标注：两个开发包各25、验证包50；身份修复另盲标1条，实际标注101条，最终有效100。每条有RELATED/IRRELEVANT/INSUFFICIENT、理由、类别、混题标记及置信度。128K字符的长正文另有完整分页阅读审计。
- 最终GOLD：调试39相关/11无关，验证37相关/13无关。标题分层只作抽样提示，实际相关比例78%/74%，未达到最初希望的30%-50%；没有按GOLD再抽样或改标签凑比例。
- 100条来自五家Newswire。六源均扫描，但当时没有完整Bloomberg输入命中冻结IONQ基线，因此没有虚构Bloomberg配额；Bloomberg的专门内容分布尚无离线样本验收。

### 身份修复留痕

初次分组漏掉GlobeNewswire公告号3396017的英/芬语变体，分别进入两集。最终校验发现后保留原始集合、GOLD及旧成绩，验证中移除这一变体，从尚未选中且未暴露的家族按固定seed哈希顺序补1条：优先同标题层，再同来源；同标题层无剩余时如实退到同来源。补样在看GOLD前确定，由原指定标注员盲标。两个候选的字节哈希保持不变，只完整重评验证；新旧成绩相同，没有追加候选或根据补样再调词。详见dataset-identity-repair.json及validation-before-identity-fix目录。

这项修复解决文章身份重复，不等于独立事件隔离：同一诉讼事件的不同律师通稿仍可能分处两集。验证已用于一次修正及最终选择，不能称为未见、无偏的泛化验收，更不能用样本成绩证明自然全市场准召。

## 调试结果

|版本|调试 TP/FP/FN/TN|调试P/R|验证 TP/FP/FN/TN|验证P/R|四项均分|双集均严格>80%|
|---|---|---|---|---|---:|---|
|第4版基线回放|39/11/0/0|78% / 100%|37/13/0/0|74% / 100%|88.00%|否|
|attempt-01|32/1/7/10|96.97% / 82.05%|27/2/10/11|93.10% / 72.97%|86.27%|否|
|attempt-02-overfit|39/1/0/10|97.5% / 100%|37/0/0/13|100% / 100%|99.38%|是|

- 首版把宽量子词主要限制在标题/摘要，正文保留较明确技术信号；调试过线即停止常规编辑并冻结，再开放验证。首版验证暴露同类企业经营、硬件供应及软件/安全事实漏判。
- 额外修正补回目标公司正文、少量核心竞企主体和明确量子产品/架构/硬件供应信息；精密时钟的独立弱信号收回到主体区，减少原料用途罗列误召。不排除市场报告、诉讼、活动、公司介绍等体裁，不穷举事件动词，不写文章级黑名单。
- 最终配置5组：目标公司正文、公司/竞企主体、量子业务主体、明确量子技术正文、量子硬件供应。全部仅在massive_l2/en；按真实来源语言与英文fallback运行，没有强制补三语。
- 只用一次额外修正，修正版评分前再次冻结；数据身份修复后两个候选都保持冻结。所有候选均有两集完整结果，按四项均分选出attempt-02-overfit。没有在最后验证后继续调词。
- 调试剩余1个FP是IT资产退役研报目录中的后量子密码迁移/HSM-TPM退役条目。保留明确技术短语的正文通路，未为该例加目录黑名单或改GOLD；该业务边界可另行人工校准。

以上召回率是冻结基线召回池内的相关消息保留率，未覆盖基线从未召回的新闻；抽样富集比例、同事件通稿和有限负例都限制外推。P/R不是生产历史实绩。

## 工具、SOP与经验

在原distribution_relevance_debug.py增加--rule-scope massive_l2：调用专用massive_relevant与实际冻结language，命中解释及缓存身份与普通L2隔离；默认普通回放行为保持不变，Massive拒绝模型track。同步SOP参数说明，没有新增自动调词器或验证权限框架。

共享经验仍是docs/by-distribution-relevance-debug-experience.md，session为IONQ-MASSIVE-20261113-01，每次attempt后更新并最终合成一条，只记录通用机制。具体文章和验证答案留在本轮材料。原工作区已有其他未提交经验内容未被覆盖。

检查：离线工具及Massive回归26通过、1可选历史fixture跳过；Ruff致命错误检查通过。各候选未改普通配置，GOLD值未在评分后改判；冻结哈希、固定集合及成对结果核对通过。生产Bus匹配器源码在仅归一化CRLF/LF后与离线引擎一致。

## 生产生效

北京时间2026-11-13 07:28:26，通过既有监测词服务读取当前revision并CAS合并massive_l2到第5版，再用现有绑定服务补齐五个Newswire订阅；原Bloomberg绑定保持原样。普通字段和其他九ticker词库在操作前后逐字段/哈希核对不变。无角色重启或镜像构建，无全局源开关及正文策略改动，无历史测试消息提升或初始化重跑。

|来源|IONQ绑定|处理|
|---|---|---|
|Business Wire|IONQ:businesswire_massive|新增并启用|
|PR Newswire|IONQ:prnewswire_massive|新增并启用|
|GlobeNewswire|IONQ:globenewswire_massive|新增并启用|
|ACCESS Newswire|IONQ:access_newswire_massive|新增并启用|
|TMX Newsfile|IONQ:tmx_newsfile_massive|新增并启用|
|Bloomberg|IONQ:bloomberg_massive|既有绑定保留|

六源均production、模型关闭、完整正文门槛开启；API schema回读通过，统一监测词返回6个Massive消费者，实际IONQ采集单位覆盖六源。普通词库及其他九ticker核对未变化。配置与发布回读见activation.json、production-verification.json、engine-parity.json。

已观察到PR Newswire的一条D-Wave投资者通告按massive_regex/revision=5进入正式总线，Runtime inbox=SUCCEEDED。该稿原始发布时间23:00 UTC、正文采集23:00:26 UTC，新订阅按正常缓存复用后以第5版判定分发；它也在冻结评测集中，只证明配置与正式链路生效，不当作新的独立泛化样本。其他来源的新候选已出现阴性或正文不可用状态；当前未逐源观察到相关正例，不宣称六源各自完成正例业务验收。

私有备份：服务器/data/bus/ops/ionq-massive-20261113/before.private.json，目录0700、文件0600；同目录留存best.json及activation.json，重建容器后仍在数据卷。若需回滚，先停用本次新增五绑定，保留原Bloomberg；从备份取旧massive_l2，基于当时最新revision合并回写，不能整份覆盖旧terms或改其他ticker。正式投递保留审计。

## 交付位置

- eval/ionq_massive_regex_20261113/下：最终development-gold.jsonl、validation-gold.jsonl，各50条；development-review.md与validation-review.md为逐案标签索引。
- best.json为入选全量候选快照；生产只能合并其massive_l2，不能用旧候选覆盖当前其他字段。
- IONQ/regex/保留两版候选、设计说明、完整预测、坏例、指标和旧错误划分的验证记录；selection.json为最终比较。
- 本地压缩包ionq-massive-gold-and-regex-20261113.zip包含上述完整100条冻结输入、GOLD、上下文及调试记录。Git仅留自有标注、候选、指标、哈希及公开回执，完整文章与原始导出不上传。

源码分支：codex/ionq-massive-regex；本报告及调试工具随本次交付提交，生产词库已独立通过现有配置服务生效。
