# DeepSeek V4.1 Flash 1017 / Sail FP4 High：W1R2、W2R2 测试

证据目录：`C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/r2_deepseek_sail_high_20261104/`。下文数据文件路径均相对该目录。

日期：2026-11-04。用户将最初的 mid 明确改为 **high**，本轮没有发送 mid。

## 结论

成本明显降低，输出吞吐较高；但不建议据此直接替代 Haiku high。W1R2 20/20 首次可消费、明确银标 15/15；W2R2 9/10 首次可消费、明确银标 8/9。W2 #14 同时漏字段与过度判 HIT。相对上一轮 Haiku high，总费用下降约61%，两轮平均耗时反而分别增加约30%和10%。生产环境未改动。

## 测试口径

- 原20例历史集：W1R2 20例、W2R2 10例，其余10例没有原始W2R2输入，不补造。
- 使用 `eval/haiku_high_stability_20261103/dataset.json` 的冻结提示词、业务事实、原生Schema文本及本地校验。逐条核对与上一轮 Haiku 输入文本及Schema完全相同；仅删除Anthropic专用cache_control，修改模型/端点/推理档位/输出协议，未修改业务字段。
- `deepseek/deepseek-v4.1-flash-1017`，`reasoning.effort=high`，`provider.only=[sail-research/fp4]`、`allow_fallbacks=false`、`require_parameters=true`。所有30个生成回执provider均为Sail Research。
- 远端隔离容器，读取当前生产OpenRouter凭据，无生产DB挂载；5并发、单次尝试、无输出修复重试，max_tokens=65536、等待上限300秒。
- 先发送1次 strict原生json_schema 请求，端点HTTP400，提示worker rejected request，无进一步原因。保持同一输入改为json_object后成功，因此全矩阵用json_object完成。共提交31次模型请求（1次被拒探测+30次正式测试）；不将协议差异隐藏为纯模型对照。
- 复用历史百炼DS medium以及上一轮Haiku high回执，不重跑。百炼DS基线未包含后来追加的静态不变量/W2规则澄清，属于历史参考，不能把差异全部归因于模型或端点；Haiku文本相同，但原生输出协议不同。

## 新测试结果

|指标|W1R2（20）|W2R2（10）|
|---|---:|---:|
|首次可消费|20/20|9/10|
|明确银标正确且可消费|15/15|8/9|
|输入token|74752|71048|
|缓存读取token|44032|26112|
|缓存占总输入|58.9%|36.8%|
|输出token（含推理）|36500|40390|
|其中推理token|34483|38252|
|总费用 USD|$0.00887428|$0.01077290|
|平均耗时|10.07s|17.48s|
|中位耗时|7.57s|12.38s|
|P95|27.01s|40.85s|
|平均TTFT（含推理流）|3.07s|1.89s|
|流式加权TPS（含推理）|261.7|259.5|
|服务端加权TPS（含推理）|189.4|237.5|

30次合计 **$0.01964718**；包含那条不可消费输出的费用。OpenRouter generation账单逐例核对合计$0.019647162，与流式usage差约$0.000000014，属于精度差。最初400请求无generation ID和usage，不虚构其费用为0，未纳入以上已返回账单的合计。

TPS口径：流式加权值为总原生输出token / 总“首个推理或正文片段到最后片段”时长，覆盖30/30；服务端值为generation API的native_tokens_completion / generation_time（毫秒转秒），同样覆盖30/30。两者包含推理token，均不是纯正文速度，也不是物理硬件吞吐；流式批量缓冲与服务端计时边界会造成差异，故同时披露。

## 历史对照

|节点/配置|平均耗时|流式加权TPS|费用USD|首次可消费|明确银标|
|---|---:|---:|---:|---:|---:|
|W1R2 / 百炼 DS medium（历史）|13.99s|161.9|$0.05720188|20/20|15/15|
|W1R2 / Haiku high（上一轮）|7.76s|218.8|$0.02272772|20/20|15/15|
|W1R2 / Sail FP4 DS high（本轮）|10.07s|261.7|$0.00887428|20/20|15/15|
|W2R2 / 百炼 DS medium（历史）|27.38s|164.5|$0.06439225|10/10|8/9|
|W2R2 / Haiku high（上一轮）|15.88s|237.5|$0.02768636|10/10|9/9|
|W2R2 / Sail FP4 DS high（本轮）|17.48s|259.5|$0.01077290|9/10|8/9|

百炼费用是历史token、当时峰谷价及缓存价计算估值；OpenRouter费用使用原生账单。以旧固定USD/CNY=6.6529换算百炼历史费用，仅为沿用原比较口径，不声称是今日汇率。

## 一致率与准确率

|比较对象/口径|W1R2|W2R2|
|---|---:|---:|
|与历史百炼DS核心判断一致（原始结果）|19/20|10/10|
|与历史百炼DS核心判断一致且可消费|19/20|9/10|
|与历史百炼DS完整结构签名一致|14/20|8/10|
|与上一轮Haiku核心判断一致（原始结果）|20/20|9/10|
|与上一轮Haiku完整结构签名一致|11/20|8/10|

核心判断为W1 NEW/OLD、W2 HIT布尔。完整结构签名额外包含confidence、引用与归因、HIT/RELATED身份和Condition分组，忽略reason措辞及列表顺序。引用多个重复已知事实的不同选取会降低完整一致率，并不自动等于错误。原始结果一致不要求通过Schema，因此W2与旧DS原始HIT一致10/10，真正可消费且一致只有9/10。

准确率沿用测试前已有分析银标，不是用户确认的独立Gold。W1只评15个明确样本；W2只评9个明确样本，且均为预期NO HIT，无法代表正向HIT召回率。其余5个W1和1个W2歧义样本不计分。RELATED及细粒度归因无完整Gold，不将核心准确率冒充全部字段准确率。

## 逐例重点复核

- **W2 #14（Neutron正式延期）：失败且误判。** 实际返回只有policy_hit、policy_related、confidence、reason，漏related_policy_ids，且HIT的Policy/Condition身份也未输出；严格接收失败。reason以“宣布2027年”直接推导C6，但冻结criterion和trigger_boundary都要求技术资格/地面集成或操作准备原因，输入没有给出。Haiku此前NO HIT更符合明确银标；旧DS同样判HIT，因此与旧DS一致不能证明正确。
- **W2 #1（LS Electric）：核心均NO HIT，但RELATED对象不同。** Sail选供应商付款Policy，Haiku选电气集成Policy；原match_scope本身分别有付款变化、具名站址/关键路径限定，不能仅凭分歧给任一方追加Gold。另确认Sail reason把原文1853亿韩元/1.387亿美元写成1.853亿美元，属于金额事实失真，未计入只评分HIT的8/9分数。这说明结构可消费与核心正确仍不能替代全部解释内容审查。
- **W2 #16（Google/AEP）：** 与Haiku的HIT、RELATED、C1身份完全一致且可消费；仍保持原先商业绑定边界歧义标签，不临时纳入Gold。
- **W1：** NEW/OLD与Haiku全部一致，与旧DS仅原歧义#8不同。9条完整签名差异主要为参照/Fact归因选取，已逐例保存在comparison.json，不视为9条核心错误。例如#3/#7选择不同已加载provisional、#14归因F47与F45+F47不同。

## 适用判断

作为成本候选值得保留，当前证据不足以直接上线替换。W1表现较好，但W2首次可消费率90%，且存在严格业务前提越界；原生Schema请求还存在端点兼容障碍。此次不修改生产配置，不弱化现有本地校验，不替失败输出补ID，不重测挑选更好结果。

## 证据

- 本目录：`dataset.json` / `dataset.native.json` / `config.json` / `endpoints.json`。
- `calls/`：30条候选原始输出、请求、流式时序与30条复用旧DS回执。
- `native-rejected-calls/`：最初400探测；`generation/`：30条原生账单与服务端耗时。
- `analysis.json` / `comparison.json` / `paired_outputs.json` / `metrics.csv`；可通过analyze.py、compare.py离线复算。
- 远端临时密钥文件已清除；运行容器自动删除，无生产应用修改。
- 官方端点目录：https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash-1017/endpoints 。目录标价为每百万输入$0.101、输出$0.15、缓存读$0.006735；以实际账单为准。
