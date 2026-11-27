# By Distribution 绑定级额外正则过滤：开发与历史验证

2026-11-27。本轮完成机制、配置/UI 接入、实际订阅规则和离线复核，**未发布生产、未修改远端配置或历史消息**。代码在独立分支 `codex/binding-extra-regex`，避免干扰原工作区的并行修改。

对四个指定来源实际启用的 **16 个绑定、6 个 ticker** 导出全部留存正式历史，共 **8,707 条**。最终规则额外排除 **184 条（2.11%）**，保留 **8,523 条**。被排除的 **162 份不同标题+正文、184 个消息×ticker 案例**均逐一审阅：96 条明显偏题、88 条低相关/远端传导；在本轮尺度下未发现应恢复的严重漏召。另保留并验证了20条容易误删的正面控制。

这里的“全量历史”是快照时这16个绑定留存的正式消息，不是原网站全量文章，也不包括第一层已拒绝而未发布的候选。时间跨度（原文 published_at）为2026-09-24 15:24:49 UTC至2026-11-26 06:50:25 UTC。未因模型或规则版本不同而丢弃旧记录。快照之后新增消息不在本次分母。

## 实现与边界

流程是 `(统一正则命中 OR 现有 Decision Model 放行) AND 绑定额外规则通过 → 原有去重/发布`。现有阈值、提示词、统一词库、正文获取、轮询与首次相关性判定均保持原逻辑；额外层没有模型调用。

配置是 `TickerSourceBinding.extra_relevance`，通过现有 Binding GET/PATCH、版本、CAS、幂等和审计管理，不新增表或控制端点。未配置/null 直接通过额外层；正常修改其他绑定设置会保留已有规则，显式 null 才移除。仅允许普通 by_distribution，不能给 Search/Ticker/Massive 启用。默认配置和其他未订阅该来源的 ticker 不会被批量改写。

Live 的最终 publish 入口覆盖正则阳性、已缓存阳性、模型回调及既有文章复用途径，过滤结果记录 `IRRELEVANT / EXTRA_REGEX_FILTERED`，保留第一层正则/模型结果。legacy Distribution 投递路径也补同一门禁，沿用其原生终态NOT_RELEVANT并在result中记录额外过滤原因及第一层结果。匹配复用原生 NFC/空白归一化、字段选择、大小写、whole_word 和20ms超时；额外层超时抛给已有恢复流程，不将失败的排除检查当作允许。保存时拒绝非法表达式、空组配置和超过16KiB的规则。

Live 在提交前还检查并发额外配置变化：若准备后绑定版本改变且额外规则确实变化，阻止旧准备结果提交，交由既有恢复流程重读。普通设置变动而额外规则不变不会因此被额外阻挡。关闭过滤不会自动补发过去已被过滤的历史消息。

前端沿用现有 Lucide Filter 图标、表单和折叠区；已配置绑定旁显示小图标，无额外大卡片。该绑定的JSON编辑器可保存、报错、留空关闭。图标在绑定配置列表与消息总线来源卡片一致展示。

## 规则思路与复核中撤回的做法

这层以标题识别明确的非目标主题，再用公司、核心产品和独立正文事实保护。最终每组只有三个OR组：正常未触发排除、业务事实保护、正文缺失保护；不增设语言调度、评分器或新模型。`none`只否决所在组，不是全局否决；保护组是有意保留的通路。消费者外设可仅靠明确标题排除，其他正文缺失时不武断拦截。

HuggingNews 的重点是普通代理/安全实验、非核心应用融资，以及 COHR/BE 下缺少设施用途的模型公司 IPO/估值快讯。NBIS 不套用“所有模型新闻排除”：模型API价格、托管服务、GPU运行时/吞吐、虚拟机和算力供给均保护。TechNews 重点是办公治理、安全演练、医疗应用、纯推广，以及 BE 下无供电/建设事实的存储元件新闻。Tom’s Hardware 仅排明确的外围消费品主题，CPU/内存/SSD本身的市场信息不因评测或优惠体裁删除。Quantum Zeitgeist 仅在无量子工程联系时排同名生物测序、教学或宇宙学主题；量子计算、纠错、通信、传感、时钟及早期技术研究继续保护。

探索时发现并撤回过宽方案：不能把所有宏观/IPO标题或缺少公司名的 HuggingNews 都删掉；有些正文包含目标公司财务、资本开支、数据中心限制或大额算力合同。也不能整体封禁量子基础研究、普通模型发布或CPU消费评测。中间扩大排除的版本不是交付配置，选择以完整正文复核后的最终版本为准。

具体保护了：Reflection 的70亿美元算力承诺和 Nebius 合同；DeepSeek/Huawei 集群部署；Anthropic 的 SpaceX 算力合同变化；Oracle 多GW扩张；GMI 5亿美元数据中心和3456张GB300；新DRAM产线/十座新厂；云端资本开支与高速网络需求；Baseten状态快照、SGLang/Together推理内核；宏观或显示器标题下独立的内存价格事实。普通“语义记忆”、网站攻击、零售外设导航里的RAM/CPU字样，则不等同于这些事实。

对 COHR，单纯股价/IPO配售、普通企业软件融资、AI训练数据劳务与实际光通信、数据中心投入分开判断。对 BE，单颗芯片的能效/内存频宽提升没有直接设施用电信息时仍属较远；但正式的新厂、改建、供电与设施投资必须保护。这也是本轮88条低相关拦截采用的边界，并不宣称这些事件绝无任何经济联系。

## 每个实际绑定的全历史结果

|Ticker × Source|历史数|额外排除|保留|排除占比|
|---|---:|---:|---:|---:|
|MU:huggingnews_rss|764|17|747|2.23%|
|MU:tomshardware_rss|545|10|535|1.83%|
|INTC:huggingnews_rss|1488|40|1448|2.69%|
|INTC:tomshardware_rss|619|4|615|0.65%|
|BE:huggingnews_rss|369|10|359|2.71%|
|BE:tomshardware_rss|166|1|165|0.60%|
|NBIS:huggingnews_rss|1026|10|1016|0.97%|
|NBIS:tomshardware_rss|271|3|268|1.11%|
|COHR:huggingnews_rss|746|59|687|7.91%|
|COHR:tomshardware_rss|128|0|128|0.00%|
|INTC:technews_hardware|536|14|522|2.61%|
|MU:technews_hardware|359|4|355|1.11%|
|COHR:technews_hardware|201|0|201|0.00%|
|NBIS:technews_hardware|395|2|393|0.51%|
|BE:technews_hardware|165|6|159|3.64%|
|IONQ:quantum_zeitgeist|929|4|925|0.43%|

16组规则均已编写并回放。**建议启用其中有实际排除效果的14组**；COHR×Tom’s Hardware、COHR×TechNews 的候选没有在正文保护后形成可靠新增排除，保留候选供复核，当前不建议为了覆盖率强行配置。没有为未订阅来源的 ticker 创建绑定。

本轮整体减量有限是有意控制边界的结果，不能据此宣称解决所有噪声。与此前标题巡检按消息ID对齐的2,823条中，旧“明显偏题”候选405条，本规则拦51条；旧“低相关”候选745条，拦18条；另拦3条当时未标记的消息。旧标签没有阅读全文，不能把未拦下的所有候选认定为漏滤，也不能把其余消息称为真相关。三个新增发现也已进入逐条正文复核。未加入缺乏稳定正文依据的宽泛排除，剩余候选仍有后续进一步诊断空间。

不能由本样本计算整个管线的准确率/召回率：没有第一层拒绝池的完整GOLD，也没有为全部保留消息重做GOLD。这是全历史确定性回放加完整拦截集合审阅，**不是独立盲测，也不是“准确率100%”的结论**。20条正面控制用于捕捉已知漏召风险，不能替代未来分布验证。

## 配置与复现

- 最终配置：[rules-selected.json](../eval/binding_extra_regex_20261127/rules-selected.json)，以binding_id为键；不自动加载/生效。
- 全部逐条结果：[all-history-results.csv](../eval/binding_extra_regex_20261127/all-history-results.csv)。
- 全部拦截及审阅：[blocked-review.csv](../eval/binding_extra_regex_20261127/blocked-review.csv)、[JSONL](../eval/binding_extra_regex_20261127/blocked-review.jsonl)。每行包括ticker/source、消息ID、原文链接、正文哈希、标签、理由及严重漏召检查。
- 正面控制：[protected-controls.csv](../eval/binding_extra_regex_20261127/protected-controls.csv)。
- 分母、哈希与清理语义：[manifest.json](../eval/binding_extra_regex_20261127/manifest.json)；逐绑定统计：[summary-selected.json](../eval/binding_extra_regex_20261127/summary-selected.json)。
- 本地完整原文、审阅输入与中间候选：[完整证据ZIP](../eval/binding_extra_regex_20261127/binding-extra-relevance-evidence-20261127.zip)，完整性见[evidence-sha256.txt](../eval/binding_extra_regex_20261127/evidence-sha256.txt)；未将原文全量提交到Git。

在仓库根运行（Python须能导入src）：

```powershell
$env:PYTHONPATH = 'C:/DoxExtraRegex/src'
& C:/Users/WEIXUANXIE/Desktop/DoxAgent/.venv/Scripts/python.exe eval/binding_extra_regex_20261127/replay.py
```

脚本先使用生产同一 `publisher_cleanup.clean_text`，再用生产同一额外 matcher；632条旧正文会被现有清理逻辑修正。没有只对离线数据另造清理/匹配语义，也没有新HTTP或模型调用。`cases.json`与规则SHA-256写入manifest，全部8707条输出一次。

## 保存和发布时的操作约束

复用 `/tickers/{ticker}/bindings/{binding_id}`。GET取得当前 `control_etag` 与旧 `effective.extra_relevance`；PATCH只提交 `{"extra_relevance": <该binding的规则对象>}`，带现有If-Match及幂等键。不把历史快照version当作当前版本，不修改共享SourceDefinition、统一词库或订阅。关闭/回滚为旧对象或 `{"extra_relevance": null}`。对象格式与现有L2完全一致，例如：

```json
{"groups":[{"id":"exclude_off_topic","any":[],"all":[{"literal":null,"regex":".","field":"all","case_sensitive":false,"whole_word":false}],"none":[{"literal":null,"regex":"\\bgaming chairs?\\b","field":"title","case_sensitive":false,"whole_word":false}]}]}
```

新增规则字段属于原生Binding结构，实际上线应协调升级读取该结构的相关角色及API/UI，再保存非空配置；不能只把新JSON塞给旧worker。本轮没有进行角色切换、远端PATCH、历史删除或补发，线上表现仍需发布后以自然消息回执验收。

## 验证记录与已知限制

新增用例验证未配置/关闭恢复、排除与保护、非法正则、超时、配置保留、CAS与幂等、其他消息类型隔离。真实Live数据库链路覆盖正则/模型两种第一层放行，分别有配置/无配置：过滤时没有标准消息发布，模型分数仍保留；第一层模型调用次数不增加。另有准备后并发修改配置的提交围栏测试。

前端TypeScript检查、14项相关单元测试通过；Playwright在1262/1559宽度均通过编辑、非法JSON、保存图标与清空移除图标验证，截图无横向溢出。Vite正式产物构建通过（既有大chunk提示仍在）。这是本地真实组件+模拟API的UI验收，不是远端在线验收。

两个扩大回归中的旧失败已在原工作区复现：旧newswire_rss测试仍依赖已退役来源；既有buffered绑定删除测试的TransactionRepository缺advanced_dedup。没有把它们算作通过或混入本轮修复。本轮Binding过滤的新增配置往返与原子发布用例通过。新增过滤测试19项通过；最终跨源Live去重与Massive隔离回归36项通过、1项因外部旧语料缺失跳过；此前分发/统一词库相关合并回归50项通过（与前两组有重叠，不累加为独立测试数）。
