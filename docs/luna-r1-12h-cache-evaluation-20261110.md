# W1R1/W2R1 Luna High Fast 与12小时显式缓存：实现及真实评估

日期：2026-11-10。

## 最终决定

**本地适配已完成；技术可用，但本轮没有启用生产切换。** 用户授权是“性能、准确率等符合预期后切换”。当前已复现W2R1的明确漏召回，尚未满足该条件。生产R1继续保持Haiku，不能将Schema可消费、缓存命中成功当成业务准确性通过。

## 已实现的范围

- 复用现有 `openai_platform.py` Responses客户端，将合法路由从W1/W2 R2扩展到R1/R2。
- 新增显式启用profile `openai_luna_r1_r2_high`：两个R1为 `gpt-6-luna`、`reasoning.effort=high`、`service_tier=fast`、显式12h；原R2配置及R3原样保留。
- W1沿用当前Haiku布局：固定提示词、冻结Known Event Index、已完成provisional分块设断点；追加的新分块不重组旧文本，活动尾部与当前消息不写缓存。W2沿用固定提示词、完整Policy projection断点。
- OpenAI请求使用 `prompt_cache_options={"mode":"explicit","ttl":"12h"}`，内容块使用 `prompt_cache_breakpoint`。固定说明置developer消息，动态修复说明置尾部；R1缓存键使用固定base hash，不随provisional增长改变。
- 原生strict JSON Schema仅在发送副本递归补全required，本地业务Schema、字段、路由规则不变。输出限额65536，不靠过低上限截断推理。
- 为新OpenAI R1冻结单层Haiku备用：OpenRouter、Google Vertex Global、high、原1h显式缓存及相同provisional batch size。延用持久化切换、独立回执与中断恢复；不新增递归Qwen备用，不吞控制错误。旧Case及旧profile继续原逻辑。
- 已记录原生model、实际service tier、cached/write及12h写入明细；既有价格归一化已支持12h，无需新增业务表。
- 修改已同步到原本地checkout；另保留基于origin/main的独立工作树。没有提交/推送，也没有更改远端生产模型profile、active execution或重启应用。

## 官方依据与计费边界

参考[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)、[Fast mode](https://developers.openai.com/api/docs/guides/fast-mode)、[显式缓存](https://developers.openai.com/api/docs/guides/prompt-caching)、[价格](https://developers.openai.com/api/docs/pricing)。实际请求与回执均验证为 `gpt-6-luna / fast`，没有拿请求参数代替实际执行档位。

12h缓存在首次实际推理请求中写入，不另发伪造“建缓存”业务请求；后续相同断点复用。写入总价为普通输入2.5倍，不是额外叠加2.5倍。Fast采用相应档位价，12h本身不保证每次KV命中或更低延迟。这里验证了12h写入明细及随后的缓存读，没有等待12小时后再测，也不宣称已经实测完整12h寿命。

## 测试集与运行口径

- W2R1：原 `eval/w2_r1_model_low_20261016/dataset.json` 的20例，历史召回/空召回各10，保留原完整Policy输入，不做分片。
- W1R1：从既有 `eval/w1_r1_haiku_cache_20261020/production_export.json` 确定性选20例，BE/INTC/MU/RKLB各5；历史召回/空召回各10，同ticker尽量共用同一index。
- W1旧导出prompt还描述旧JSON provisional结构，先做的一轮仅作探索；正式统计将那一句替换为**当前部署prompt的Markdown事实视图说明**，业务上下文不变，并重新对两模型各跑20例。W2冻结prompt与当前prompt一致，无需重复整组。
- 主表是80次当前口径调用：两个节点×20例×两个模型，逐例串行，同输入/提示词/本地Schema，无自动重试，不经生产业务调度，不写Case/交易记录。
- 另保留旧W1探索40次、6次缓存布局探针及6次问题样本稳定性复测，共132次回执。探索和复测不混入正式均值或首次可消费率。
- 无独立人工Gold；历史Qwen回执和本轮Haiku都只是比较对象。报告一致率与逐项业务复核，不伪造整体“准确率”。

## 正式性能结果

| 节点/模型 | 首次可消费 | 平均秒 | P95秒 | 有效TPS | 20例费用USD | 输入/读取/写入token | 输出/推理token |
|---|---|---|---|---|---|---|---|
| W1R1/luna | 20/20 | 3.650 | 6.755 | 98.3 | $0.059001 | 388878/290396/87749 | 7172/6752 |
| W1R1/haiku | 20/20 | 4.289 | 5.900 | 97.0 | $0.032480 | 600128/473008/108794 | 8317/7957 |
| W2R1/luna | 20/20 | 4.743 | 9.545 | 122.3 | $0.227728 | 703733/274842/416188 | 11597/10162 |
| W2R1/haiku | 20/20 | 7.403 | 13.971 | 143.0 | $0.135389 | 1033525/419952/592466 | 21172/17995 |

当前口径W1平均耗时改善14.9%，但P95为6.76秒，高于Haiku的5.90秒；W2平均改善35.9%，P95也有改善。样本量小、串行测试，不宣称生产高并发尾延迟保证。

“有效TPS”=原生总output token（已含隐藏reasoning）/完整请求wall time；不是纯解码器TPS，隐藏推理阶段没有逐token时序，不能把可见输出时间窗口当成全部推理生成时间。

## 成本与缓存

Native费用为原生usage×官方实际档位价的**估算**，不是已结算账单；Haiku采用OpenRouter回执费用。Fast每百万普通输入/读取/12h写入/输出分别为$0.20/$0.02/$0.50/$1.00。测试输入均未越过Luna长上下文价格边界。

两节点20例正式合计：Luna **$0.286729**，Haiku **$0.167869**。本轮Luna并没有更省钱：既有Fast溢价，又有12h冷写成本。W2测试跨多个ticker/Policy版本，39.1%的整体读取命中率不能代表固定版本持续生产的暖缓存水平。

把本轮写入token统一反事实改为读取、保持其他输入和输出完全不变，Luna约$0.044840、Haiku约$0.034630。该归一化仍不支持“必然节费”，也不是另一轮真实账单；长期跨小时复用的收益需要实际流量验证。

两节点正式请求均观察到 `ttl_12h_tokens>0`，且总原生写入统计与12h分项一致。当前prompt暖缓存实测：

| 节点 | 输入token | 读取token | 命中率 | 写入token |
|---|---|---|---|---|
| W1R1 | 6907 | 6706 | 97.09% | 0 |
| W2R1 | 28431 | 28349 | 99.71% | 0 |

另有动态消息尾部变化探针：W1约97.00%、W2约99.69%，两者均无需再次写入原固定前缀。写入/命中证明参数与断点路径有效，不能证明模型业务判断正确。

## 业务结果与为何不切换

| 节点 | 候选集合一致 | 候选顺序一致 | 空/非空一致 |
|---|---|---|---|
| W1R1 | 10/20 | 10/20 | 18/20 |
| W2R1 | 15/20 | 15/20 | 18/20 |

候选集合不一致不是自动判错：W1同一occurrence与旧主题背景的边界、W2三候选排序，都可能产生合理差异；R1也不是最后NEW/OLD或HIT决策。

但W2 DDR5样本是明确问题：正文就是内存零售价上涨，scope直接写明此类事实可召回。Luna只召回PC出货Policy，漏掉DRAM备货/采购Policy；首次及3次复测均遗漏。该Policy因此不能进入W2R2详情研判，不能靠Schema成功或Haiku fallback解决——它是结构合法的业务遗漏，不会触发provider fallback。

LS Electric样本首次和一次复测为空，其他两次只选供应商付款观察。这里另有scope内具名站址限定的解释问题；它不是简单的接口失败，也不能只用历史模型一致替代业务裁判。

逐条差异复核（仅作本轮审查，不反写Gold）：

| 样本序号 | 节点 | Luna / Haiku | 复核结论 |
|---|---|---|---|
| 1 | W1R1 | E6,E21,E109 / E6,E109,E21,E47 | 核心历史事实一致，Haiku额外召回Fremont购厂；消息明确有扩产，但购厂身份未明确，额外候选不一定必要。 |
| 6 | W1R1 |  / E620,E587 | Optical Computing报告与Quantum Photonic/OSAT报告并非同一报告；Luna空召回更严格，不直接认定漏召回。 |
| 7 | W1R1 |  / E423,E493,E337,E55,E281 | 当前是J.P. Morgan评级升级，索引候选为其他券商/更早事件；Luna空召回可解释。 |
| 8 | W1R1 | E493,E55 / E55,E493,E337,E423,E306 | 均选部分分析师事件；具体券商与日期不同，候选宽窄分歧，不作为Gold。 |
| 15 | W1R1 | E423,E436,E381 / E423,E381,E436,E397,E461 | 双方均保留CXMT业绩及本轮内存卖压核心事件；扩展到券商回应等背景的数量不同。 |
| 16 | W1R1 | E114 / E114,E19,E5,E26,E21 | 双方均保留最近Neutron延期E114；Haiku多选更早延期/发射链背景。 |
| 17 | W1R1 | E114 / E114,E19,E5 | 双方均保留E114；Haiku多选旧延期。 |
| 18 | W1R1 | E114,E110,E97,E19 / E114,E5,E19,E110,E3 | Luna保留E114、TD Cowen E110、内幕交易E97；Haiku多选旧技术故障/延期且未选E97，不能把集合不一致视为Luna错误。 |
| 19 | W1R1 | E56,E57 / E56 | 正文仅给Mars硬件纪录；两者均选太阳能传承E56，Luna额外选新电池E57存在主题扩展嫌疑。不能仅凭标题强行要求选所有延期/股价事件。 |
| 20 | W1R1 | E115 / E115,E112 | 两者均选AST卫星发射背景E115；Haiku额外选TELUS网络测试E112，后者与正文核心问题连接较弱。 |
| 21 | W2R1 |  / pol_caf37545d669448ea06097ecabb90784,pol_166db6d355584fed81b09eaed51650cf | LS Electric签约属于供应商/电气交付观察，但scope另有限定具名站址/关键路径的表述；这是范围限定的解释分歧，且Luna重复调用不稳定。不能只因Haiku/Qwen一致就把它宣布为Gold。 |
| 22 | W2R1 | pol_f4debb2147f8461aa8255b14f15ac5f9 / pol_f4debb2147f8461aa8255b14f15ac5f9,pol_1eb439a162e345ff8fe08d8bd7273829,pol_da9be2ba2880430da3710cad6a59fdc2 | Luna只选Oracle采购Policy，未选正文明确出现的Brookfield融资框架对应观察；scope将单纯扩框列为不等于HIT，而不是不召回，存在收窄召回风险。Haiku其他额外Policy也需逐项核对。 |
| 23 | W2R1 |  / pol_41c0aae673874321b7058ede3bbcc375,pol_e54cffa9612b44b39fde4b9fcf27bb5b | 公用事业负荷/接入协议是否达到相关scope的观察范围存在边界差异；不能以未提Bloom为唯一排除标准，也不能以同属AI电力主题直接召回。保留争议。 |
| 25 | W2R1 | pol_867298f602d0493fb045089fedb0e708,pol_74234c60645d4de6bc7751ae02bed032,pol_f4debb2147f8461aa8255b14f15ac5f9 / pol_867298f602d0493fb045089fedb0e708,pol_94c9f3974ce54a38993a257c7a66ff8e,pol_74234c60645d4de6bc7751ae02bed032 | 超过3个合理相关Policy，三候选上限导致采购链与服务期限的优先级不同；核心AEP项目Policy均保留，不作为明确漏召回。 |
| 32 | W2R1 | pol_0b3c39c285d84b5bb5d322da1cd93de7 / pol_0b3c39c285d84b5bb5d322da1cd93de7,pol_f95d4ea67ebf4da684f4c577a9d4cbaf | 明确漏召回：DDR5涨价正文直接匹配pol_f95d4ea67ebf4da684f4c577a9d4cbaf内“内存涨价导致的零售价调整……可召回”。Luna仅选PC出货Policy；不足3个候选，不能用上限解释。首次加3次复测均漏掉该Policy。 |

问题样本重复测试：

| 序号 | 轮次 | 候选IDs |
|---|---|---|
| 21 | stability_1 | [] |
| 21 | stability_2 | pol_166db6d355584fed81b09eaed51650cf |
| 21 | stability_3 | pol_166db6d355584fed81b09eaed51650cf |
| 32 | stability_1 | pol_0b3c39c285d84b5bb5d322da1cd93de7 |
| 32 | stability_2 | pol_0b3c39c285d84b5bb5d322da1cd93de7,pol_174e1e71876b4c60bd0367d852d1c9a9 |
| 32 | stability_3 | pol_0b3c39c285d84b5bb5d322da1cd93de7 |

因此本轮判断是：**API、显式12h缓存、Schema和备用实现通过；两个R1一起切换的业务验收未通过。** 没有为让测试通过而在编排硬补Policy ID，也没有修改生产prompt或把合法空召回强制改成错误触发Haiku。

后续应针对“scope明确纳入的辅助观察、背景事实仍需召回”与“scope限定语何时构成排除”做小范围提示词/任务边界对齐，再冻结同一输入重测；不建议单纯加大推理档位或硬缩输出。W1尚无独立Gold，本轮也不以50%集合一致率直接换算为50%准确率。

## 验证、清理与发布状态

- 独立origin/main工作树离线250项通过：新增R1断点/追加/修复前缀/路由及错误拒绝，12h账单分项，主备持久化及中断恢复；兼容已有R1、R2、W2身份对齐与两阶段运行回归。原本地脏checkout的新增32项也全部通过。
- 原checkout包含本轮全部实现hunks，已用反向 `git apply --check` 验证；其他会话的脏改动保留。changelog已追加。
- 按用户追加授权，执行远端现有 `storage_release.py reconcile-resources`。没有可安全退休的旧镜像（retired=0），构建缓存回收约7MB，快照余量约5.7GiB；保留运行、回滚、候选及其他明确依赖。没有删业务库/volume或强拆保护镜像。
- 发布前还发现API service.py、Scheduler settings.py相对当前main有既有差异，已留源hash证据；若后续发布，应保留/核对这些差异，不能把本轮整文件覆盖当作仅改R1。本轮未build、未发布，因此没有覆盖现有生产能力。
- 最终生产仍使用原R1 Haiku；已只读核对active execution `c00bf6cc8ee31a63e4bb264bf9f85106a70210f9b48d9b4c85d42f54715b07dd` 的两个R1均为OpenRouter/Haiku high/1h。新profile未激活，Haiku备用代码在启用新profile时生效。R2/R3、历史冻结Case、prompt和业务Schema均未切换。

证据目录：`eval/luna-r1-cache-20261110/`。`dataset.json`、`calls/*.json`、`analysis.json`、`metrics.txt`、`current-w1-instructions.txt`、`implementation.patch`、`cleanup.json`、`preflight.json`均可复核，不包含API密钥。独立工作树：`C:/Users/WEIXUANXIE/Desktop/DoxAgent-luna-r1-20261110`。

## 后续发布授权（2026-11-10）

用户在阅读上述评估后明确要求“切换并发布，haiku作为fallback”。后续按该决定发布两个R1；本报告的业务遗漏证据保留，不将用户上线决定表述为模型准确率验收通过。发布验收另见 docs/luna-r1-release-20261110.md。
