# Sail FP4 DeepSeek V4.1 Flash 1017 Low + Strict Schema 复测

日期：2026-11-04。证据目录：`C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/r2_deepseek_sail_low_strict_20261104/`。

## 结论

本轮30/30首次可消费，全部HTTP200，无Schema 400、429、超时或输出修复重试。发送Schema副本递归补全required后，Sail可接受原生strict json_schema，原本漏身份字段的问题此次未复现；不能再把上轮400概括为端点不支持strict。

但业务准确率仍未达到上一轮Haiku high结果：W1明确银标14/15，W2为8/9。W1 #2把背景内容提升为核心新增，W2 #14仍忽略Condition必需的技术因果前提而判HIT。因此结论是结构问题得到改善、低成本和速度有优势，业务误判仍在，不据此直接替换生产。

本轮总费用 **$0.01487658**。同一历史集上，相比Sail high费用约低24.3%，相比Haiku high约低70.5%；W1/W2平均耗时相对Sail high分别减少36.4%/27.6%，相对Haiku high分别减少17.4%/20.3%。这是单轮观测，不能推断生产长期改善比例。

## 请求与冻结边界

- 原20例：W1R2 20条、W2R2 10条，使用上一轮冻结消息、已加载事实/Policy、Schema、静态跨字段一致性指令。其余10条没有原W2R2输入，未补造。
- 模型 `deepseek/deepseek-v4.1-flash-1017`，`reasoning={effort:low,exclude:false}`，仅 `sail-research/fp4`，禁用fallback、require_parameters=true。30条SSE及generation回执均为Sail Research。
- 将上一轮原生Schema的副本逐对象处理：对有properties的对象设置required=list(properties)，递归处理$defs/anyOf/数组等嵌套Schema。字段名、类型、默认值、正则、枚举、引用等均不变；不修改本地Pydantic业务Schema。
- 原提示词中的业务Schema文本不重写；实际response_format发送strict json_schema的补全required副本。原可省略字段需显式输出；非空数组类型不能用null替代，允许null的字段仍可null。
- 逐条验证消息及本地Schema与上轮相同；wire Schema相对原wire副本仅required变化；30条输出另经离线JSON Schema验证和原本地模型/引用范围校验。
- 远端隔离容器读取当前生产OpenRouter key，无生产DB挂载；5并发、无请求/输出修复重试，max_tokens=65536、上限300秒。首个请求纳入30条而非额外付费探测。临时密钥文件已清理；未修改生产。
- 复用Sail high、Haiku high、历史百炼DS medium回执，不重跑基线。Sail high用json_object，本轮同时改变effort和wire约束，因此不能把稳定性/准确率变化纯粹归因于low。百炼DS历史prompt尚未包含后加的不变量和W2规则澄清，仅作历史参考。

## 本轮指标

|指标|W1R2|W2R2|
|---|---:|---:|
|首次可消费|20/20|10/10|
|明确银标正确且可消费|14/15|8/9|
|输入token|80,192|73,656|
|其中缓存读取|47,104（58.7%）|27,392（37.2%）|
|输出token（含推理）|16,967|25,435|
|费用 USD|$0.00620418|$0.00867240|
|平均耗时|6.41s|12.67s|
|中位耗时|6.35s|11.02s|
|P95|11.75s|31.22s|
|最长耗时|13.65s|31.22s|
|平均TTFT（含推理流）|2.73s|2.11s|
|流式实测加权TPS（含推理）|232.6|241.7|
|服务端加权TPS（含推理）|144.4|209.9|

费用包含全部30次（含判断错误者），采用OpenRouter usage原生USD账单。generation账单合计$0.014876566，与usage总额相差约$0.000000017，属于精度差。

TPS覆盖30/30。流式口径=原生输出token总和/各请求首个推理或正文到最后输出片段时长之和；服务端口径=原生输出token总和/generation API generation_time总和（毫秒转秒）。二者计时边界与缓冲不同，并列披露；均含reasoning，不是仅正文TPS或硬件解码基准。本轮较快主要表现为输出量减少，而非TPS提高。

## 模型历史对照

|节点/配置|平均耗时|流式TPS|费用USD|首次可消费|明确银标正确|
|---|---:|---:|---:|---:|---:|
|W1R2 / 百炼 DS medium 历史|13.99s|161.9|$0.05720188|20/20|15/15|
|W1R2 / Haiku high|7.76s|218.8|$0.02272772|20/20|15/15|
|W1R2 / Sail DS high / json_object|10.07s|261.7|$0.00887428|20/20|15/15|
|W1R2 / Sail DS low / strict|6.41s|232.6|$0.00620418|20/20|14/15|
|W2R2 / 百炼 DS medium 历史|27.38s|164.5|$0.06439225|10/10|8/9|
|W2R2 / Haiku high|15.88s|237.5|$0.02768636|10/10|9/9|
|W2R2 / Sail DS high / json_object|17.48s|259.5|$0.01077290|9/10|8/9|
|W2R2 / Sail DS low / strict|12.67s|241.7|$0.00867240|10/10|8/9|

百炼费用按旧token/峰谷/缓存价估算，使用沿用的历史USD/CNY=6.6529换算，不代表今日汇率；OpenRouter三组使用各自真实账单。缓存命中、负载、结构化约束和采样随机性不同，延迟及费用不是严格隔离单因素的因果对照。

## 一致率

|对象/核心判断一致|W1R2|W2R2|
|---|---:|---:|
|历史百炼DS medium|19/20|10/10|
|上一轮Sail DS high|18/20|10/10|
|Haiku high|18/20|9/10|
|与百炼完整结构签名一致|13/20|9/10|
|与Haiku完整结构签名一致|9/20|9/10|

核心判断是W1 NEW/OLD、W2 HIT；完整结构签名还含confidence、引用与归因、HIT/RELATED身份及Condition分组，忽略reason措辞及数组顺序。不同重复事实参照的选择未必错误，完整一致率不等于准确率。与Sail high的W2核心10/10一致包含上轮#14原始但不可消费的HIT；本轮均可消费，不掩盖此前结构失败。

## 逐例判断与准确率边界

- **W1 #2：** 冻结核心是Roth将目标价225改为260并维持Neutral，E115/F243已覆盖；low仍把Oracle/Jupiter背景重申当新增核心。与既定银标OLD不一致，Sail high及Haiku high判OLD。没有为本轮调分或改标签。
- **W1 #8：** 已知Neutron延期之外，把盘前跌9%视为核心新增；与Haiku/Sail high不同，与历史百炼DS相同。该例早已标为整条复合消息与目标ticker核心粒度歧义，继续不计入准确率，不以多数投票指定Gold。
- **W2 #14：** strict本次补齐全部Policy和Condition字段，结构通过；但reason仍以“开发项目更新中调整至2027年”代替C6要求的技术资格/地面集成/操作准备原因，因此仍误HIT。前一次人工提供的单例NO HIT没有在本次稳定复现。没有削弱本地校验或事后改写结果。
- **W2 #1：** 这次RELATED身份与Haiku一致，未复现上轮将1853亿韩元错写1.853亿美元的解释错误；不能由一个样本推断完全消失。
- **W2 #16：** HIT C1+两个RELATED与上一轮一致；保持原商业绑定事实边界歧义标签，不纳入Gold。

**准确率范围：**沿用原分析银标，非用户确认的独立Gold。W1仅15条明确样本（本次14/15=93.3%），W2仅9条明确样本（8/9=88.9%），其中W2全部预期NO HIT，不能估计正向HIT召回。其余5条W1和1条W2不计分；RELATED及每个引用/解释没有完整Gold，不能把核心准确率宣称为全字段准确率。

## 补充：Reasoning token对比

|节点/配置|样本|推理token总计|单例均值|占输出比例|非推理输出token|
|---|---:|---:|---:|---:|---:|
|W1R2 / Sail DS low strict|20|14,891|744.5|87.8%|2,076|
|W1R2 / Sail DS high json_object|20|34,483|1,724.2|94.5%|2,017|
|W1R2 / Haiku high|20|18,407|920.4|83.7%|3,593|
|W1R2 / Bailian DS medium historical|20|36,377|1,818.8|94.8%|2,000|
|W2R2 / Sail DS low strict|10|23,157|2,315.7|91.0%|2,278|
|W2R2 / Sail DS high json_object|10|38,252|3,825.2|94.7%|2,138|
|W2R2 / Haiku high|10|28,284|2,828.4|89.9%|3,176|
|W2R2 / Bailian DS medium historical|10|39,433|3,943.3|94.4%|2,323|

同样30轮合计：Sail DS low **38,048**推理token，Sail DS high **72,735**，Haiku high **46,691**，历史百炼DS medium **75,810**。low相对Sail high减少47.7%，相对Haiku high减少18.5%；本轮推理token仍占总输出约89.7%。

分节点：low比Sail high分别少56.8%/39.5%，比Haiku high少19.1%/18.1%。Sail high比Haiku high反而多87.3%/35.2%。跨模型high并非同一推理预算；不同tokenizer及供应商推理计量会影响数量，不将这些数字解释为等量思考或相同质量。原始completion_tokens_details.reasoning_tokens保留，30条均有值；没有把缺失值当0。

在本轮低价下，单纯减少token并不足以抵消业务误判；结构化约束负责结构，本地跨字段和规则校验仍需保留，业务因果与核心事实识别需要独立评估。

## 证据与复算

- dataset.json、config.json、verification.json：冻结输入、wire Schema适配和30条校验。
- calls/：原始请求、输出、usage和流式时序；generation/：30条服务端账单与时间；未覆盖旧评估文件。
- analysis.json、comparison.json、extended_comparison.json、paired_outputs.json、metrics.csv、reasoning_comparison.csv。
- analyze.py、compare.py、extend_comparison.py：离线复算，无需再调用模型。
- 官方约束说明：https://openrouter.ai/docs/guides/features/structured-outputs 。端点支持和strict执行方式因provider而异；本次30/30验证仅证明这批实际请求可用，不证明所有JSON Schema特性都被强制执行。
