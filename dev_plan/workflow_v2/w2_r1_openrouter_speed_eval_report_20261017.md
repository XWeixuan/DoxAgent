# W2R1 OpenRouter 输出速度评测：Nemotron low 与 Haiku low/high（2026-10-17）

## 结论

Haiku high 在这5条样本中表现最好：首次5/5有效、候选集合与既有Qwen基线5/5一致，平均5.38秒、最慢8.54秒，总费用$0.02511828。相比Haiku low的平均3.92秒，多花约1.46秒，但修正了本批low中的非法Policy ID、错误Policy及额外召回问题。该结论只针对5条观察，不是大样本精度验收。

Nemotron解码/流式输出很快，但推理输出量大。首轮平均15.44秒、4/5有效；LS Electric Case首轮16K token全部花在推理而无答案。单独放宽到64K后重试16.27秒结束，实际仅生成7215 token，最终5条集合均与基线一致；这说明首轮不稳定推理延长不能简单归为“稳定需要超过16K”。

未修改生产模型或编排，未重跑Qwen基线。Nemotron补测及Haiku low纠错均独立保留，不替换首轮统计。收到改为high的指令后检查时，medium批次已全部返回，因此只作为附录和费用留痕，不再补跑medium。

## 样本、版本与调用口径

从原20Case固定选择原序号1、3、6、7、19：Qwen FULL基线中召回3条、不召回2条，覆盖BE、INTC、MU、RKLB。全部一次注入原Case完整Policy集合，不分片、不用decision model；每组5Case并发，模型/effort组之间串行。

同一Case在各模型中使用同一冻结输入、顺序、指令和R1 Schema，并复用既有 `qwen_baseline` 成功回执。4条冻结指令与当前R1完全相同；UNT Case 6来自更早的Candidate Recall版本（依赖criterion的潜在触发关系），为保持配对公平未换成当前Scope Recall指令，未将其包装为当前prompt的完整验收。样本Policy集合规模和消息长度不同，统计的是这5条整体任务。

测试在新加坡生产主机的独立容器中运行，复用已有镜像、不构建新镜像、不挂载生产数据库/工作区，不创建业务Case或Effect。只继承OpenRouter凭据，未使用百炼或StepFun专用key。

请求明确固定：Nemotron `nvidia/nemotron-3.5-super-vl` + `provider.only=["coreweave/nvfp4"]`；Haiku `anthropic/claude-haiku-5.5` + `provider.only=["google-vertex/europe"]`。全部 `allow_fallbacks=false`、`require_parameters=true`，原始响应provider分别为CoreWeave/Google。请求体和全部SSE原chunk均留痕；不把模型默认最低价当作指定端点价格。

Nemotron和Haiku首轮均发送 `reasoning.effort=low`；用户所说mid映射为medium；最终新增组发送high。全部使用JSON Schema strict及原Pydantic/候选身份校验，流式输出并请求usage。首轮 `max_tokens=16384`、read timeout 300秒、SDK/编排不自动重试，不添加“再想一想”的第二轮对话。只有补测Nemotron Case 1提高总输出上限至65536；其余prompt与effort不变。Haiku low Case 7仅按已有语义校验纠错上下文补测一次。

OpenRouter模型元数据列明Haiku可选low/medium/high等档位；Nemotron端点声明支持reasoning，但catalog未给supported_efforts。NVIDIA上游文档将low/medium定义为较短推理模式，且警告thinking_token_budget可能产生空答案，因此没有另加硬截断reasoning budget。本轮能确认发出的wire effort，不能凭请求参数证明CoreWeave内部每个推理参数如何实现。[OpenRouter推理参数](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)，[NVIDIA推理控制说明](https://docs.nvidia.com/nim/large-language-models/latest/get-started/advanced/get-started-nemotron-3.5-super-vl.html#control-reasoning)。

## 首次调用比较

平均/P50/P95均含首次失败耗时，集合一致分母始终5，不删除失败样本。P95为线性插值的小样本描述。

| 模型/端点/effort | 首次有效 | 集合一致 | 有无召回一致且有效 | 平均秒 | P50秒 | P95秒 | 最大秒 | 5条费用 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Qwen3.8 Flash medium旧基线 | 5/5 | 作为比较参照 | 作为比较参照 | 78.20 | 53.36 | 156.13 | 167.86 | ¥0.09096820 |
| Nemotron / CoreWeave low | 4/5 | 4/5 | 4/5 | 15.44 | 12.40 | 32.19 | 36.63 | $0.04368560 |
| Haiku / Vertex Europe low | 4/5 | 3/5 | 4/5 | 3.92 | 4.02 | 4.61 | 4.74 | $0.02388518 |
| Haiku / Vertex Europe high | 5/5 | 5/5 | 5/5 | 5.38 | 5.13 | 8.16 | 8.54 | $0.02511828 |

Qwen是历史生产/受控FULL回执，本轮OpenRouter是5并发流式请求；时间、上游负载和缓存条件不同。因此平均耗时差异是实测观察，不代表可归因的纯模型加速倍数。Qwen旧回执未记录流式首token时间，无法补算其真实decode TPS。集合一致不是gold准确率，基线也可能有过召。

## Token与按token计算的费用

不同模型的tokenizer不同；输入token数不同不代表少传Policy。已按冻结业务输入逐条校验完整Policy集合及序列化内容一致。

| 组别 | 输入 | 输出（已含推理） | 其中推理 | 缓存输入 | 首轮费用 |
|---|---:|---:|---:|---:|---:|
| Qwen旧基线 | 129,424 | 17,910 | 17,322 | 87,040 | ¥0.09096820 |
| Nemotron / CoreWeave low | 190,624 | 30,184 | 29,640 | 0 | $0.04368560 |
| Haiku / Vertex Europe low | 213,553 | 717 | 0 | 0 | $0.02388518 |
| Haiku / Vertex Europe high | 213,553 | 2,959 | 2,162 | 0 | $0.02511828 |

计价均由端点价格快照×实际usage复算，并与OpenRouter返回cost逐条校验：

- CoreWeave：输入$0.15/M、缓存输入$0.02/M、输出$0.50/M。
- Vertex Europe：输入$0.11/M、缓存输入$0.011/M、输出$0.55/M。本批每请求输入均小于其100K加价门槛，无缓存写入或web费用。
- 百炼Qwen旧基线：输入¥0.8/M、缓存输入¥0.1/M、输出¥2.7/M。[百炼价格](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)。

费用公式：`(input-cached)*input_price + cached*cache_price + output*output_price`。推理token已计入输出，不能再次加价。币种保持原币种，不编造汇率或直接跨币种算费用倍数。OpenRouter充值手续费、账户优惠、税费不在token计价中。[Nemotron端点价格](https://openrouter.ai/api/v1/models/nvidia/nemotron-3.5-super-vl/endpoints)，[Haiku端点价格](https://openrouter.ai/api/v1/models/anthropic/claude-haiku-5.5/endpoints)。

## TPS：测到了什么、没有测到什么

全部请求使用perf_counter记录首个非空正文/推理delta、首个正文delta、最后输出delta与完成时刻。流式全输出观测TPS定义为 `usage.completion_tokens / (last_output_delta - first_nonempty_output_delta)`；包括推理和正文。

| 组别 | 首非空delta P50秒 | 首正文 P50秒 | 流式全输出观测TPS P50 | 观测TPS范围 |
|---|---:|---:|---:|---:|
| Nemotron / CoreWeave low | 3.80 | 9.48 | 516.18 | 395.2–575.7 |
| Haiku / Vertex Europe low | 3.16 | 3.16 | 201.57 | 157.8–286.4 |
| Haiku / Vertex Europe high | 2.83 | 4.79 | 196.42 | 173.7–386.1 |

**这些是客户端接收窗口估算，不能称为精确原生TPS。** OpenRouter generation接口本应提供native token和generation_time；本轮对22个实际generation ID使用原调用凭据查询、并对首次结果作延后核查，仍全部404。因此服务端生成时间缺失，未伪造native TPS。[generation接口](https://openrouter.ai/docs/api/api-reference/generations/get-generation)。

流式buffering会让多个token集中到一个chunk，首chunk前已生成的token又无法扣除。正文通常很短，单独正文TPS甚至出现数千/上万的明显分包伪影，故未把它作为性能结论。medium也有突发chunk导致单条全输出估算约4214 TPS，不将其视为模型真实吞吐。API reported reasoning可为完整文本、摘要或opaque details，返回时间不等于内部生成时间；不能把高估值拿来做容量规划。

可直接采用的结论是：Nemotron本批流式输出很快，但首轮30184输出token中29640为推理，导致任务耗时；Haiku low reported推理token为0，总输出仅717，high也只2959输出token（2162推理），因此即使观测TPS低于Nemotron，整体完成仍更快。

## 逐条结果与差异复核

Policy简称：电气交付=pol_caf37545d669448ea06097ecabb90784；Intel设施连续性=pol_cece5821b48b4d38b666945b2569e63d；Neutron资格/首飞时点=pol_0ae9b330f38549419ba27efb632ac461。完整ID集合在analysis.json和原响应。

| 原20样本序号 / 标题 | Qwen基线 | Nemotron low | Haiku low | Haiku high |
|---|---|---|---|---|
| 1 / LS Electric变压器订单 | 电气交付 | 首轮length、无答案；放宽上限后电气交付 | 电气交付+永久供电窗口，多召后者 | 电气交付 |
| 3 / Intel以色列工厂爆炸 | Intel设施连续性 | 同基线 | 同基线 | 同基线 |
| 6 / UNT注资/12英寸Fab | 空 | 空 | 空 | 空 |
| 7 / Neutron首飞延后 | Neutron资格/首飞时点 | 同基线 | 首轮不存在ID；纠错后错选火箭重复制造/生产节拍Policy | 同基线 |
| 19 / Lennar住宅The Foundry | 空 | 空 | 空 | 空 |

LS Electric的永久供电窗口Policy仅在同项目同阶段消息直接改变供电就绪状态时适用；设备订单/交期并未给出此状态，Haiku low新增该Policy缺乏支持。原基线的电气交付召回本身也有范围争议：match_scope要求LS订单/交期明确映射具名Bloom站址且影响关键路径，本消息只称宾州某大型科技公司数据中心。high与基线一致不等于已证明此条为gold正确。

Neutron标题明确是首飞时点更新，资格/首飞Policy直接覆盖；纠错后Haiku low选的制造节拍Policy明确排除由Neutron里程碑本身推定合格产能变化，因此不是只换了同义ID，而是实际错召/漏召。JSON Schema合法与Policy ID存在，都不能代替业务语义正确。high本条改为正确Policy，并给出事实范围原因。

## 两条补测单独记账

- Nemotron Case1：仅总输出上限16K→64K，额外16.27秒、输入41633（缓存41616）、输出7215（推理7027）、$0.00444237；候选恢复为基线。缓存命中变化及随机生成均有影响，不能把更快全归因于上限变动。首次+补测累计请求耗时52.90秒，不含人工等待。
- Haiku low Case7：已有语义纠错一次，额外2.41秒、输入42095、输出49、$0.00465740；ID存在、Schema有效，但语义仍错。
- Nemotron包括补测总费用$0.04812797、最终集合5/5一致；Haiku low包括纠错总费用$0.02854258、最终集合仍3/5一致。

## Medium附录与全轮费用

medium批次在改为high后的检查时已完成：5/5有效、4/5集合一致，平均4.84秒、P50 4.65秒、费用$0.02449073；输入213553、输出1818（推理934）。Neutron一条虽然找对主Policy，却额外召回两个Policy，其中Flatellite在正文未出现，属于越出已提供事实的相关性扩张。medium不作为本轮最终选择的测试档位。

本轮实际生成调用总数22：Nemotron low 5+1补测、Haiku low 5+1纠错、medium 5、high 5。合计token费用$0.12627956；既有Qwen费用只是旧回执估费，不是本轮再次支付。metadata GET不是模型生成调用。

## 留痕与限制

原数据、冻结输入、端点/模型能力快照、完整请求/SSE/usage、generation 404、每条CSV、分析、验证与隔离容器清理回执位于 `eval/w2_r1_openrouter_speed_20261017/`。首轮原脚本保留于初始bundle；后续脚本增补不会改写首次请求体。未提交或部署生产模型切换。

本轮最有价值的后续候选是Haiku high：本批仍保持约5秒完成且5/5基线一致。但样本仅5条，并非认证结果；provider稳定性、不同长度/复杂度与完整20条的语义边界还未覆盖。无需为了本轮速度测试重跑原Qwen基线。
