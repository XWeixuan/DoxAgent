# W1R3/W2R3 Luna High 生产发布验收

日期：2026-11-11。用户要求两个R3改为OpenAI Platform Luna high，并以OpenRouter Haiku 5.5 high / Google Vertex Global备用，完成后发布。

## 已上线配置

- 主模型：`gpt-6-luna`，OpenAI Platform Responses API，`reasoning.effort=high`、`service_tier=fast`，timeout 300秒，max_output_tokens 65536。
- 固定提示词显式缓存30m，与现有Luna R2一致；父R2输入/输出、本轮数据、capture mode和修复说明位于动态尾部，不写入固定前缀。
- 备用：`anthropic/claude-haiku-5.5`，OpenRouter，`google-vertex/global`，high，原生严格Schema及1h固定前缀缓存。
- 两个R1/R2的配置和备用完全不改；R1仍为12h缓存，R2仍为原配置。业务提示词、Schema、路由、历史冻结Case均不改。

## 必须的R3适配

R3原先通过前轮上下文工作，原OpenAI适配仅支持R1/R2。如果只改模型名，会缺少父上下文。因此将OpenAI的继承上下文请求纳入现有stateless parent replay：读取成功R2的冻结输入与原始输出，显式回放，不跨provider传递response ID。

Responses历史assistant内容使用output_text；native strict wire Schema递归required处理沿用现有实现。W2条件引用在发送副本上要求完整Policy/Condition身份，现有本地严格布尔值及覆盖校验继续执行；未新增业务字段。

业务回执中的previous_response_id仍保留原R2逻辑关联用于追踪；原生HTTP请求不携带该字段。REPLAY_PARENT表示实际上下文由冻结的父输入/输出构成，不依赖服务端response继承。

API Schema与模型参数参考[官方Luna文档](https://developers.openai.com/api/docs/models/gpt-6-luna)、[Fast mode](https://developers.openai.com/api/docs/guides/fast-mode)。

## 回归与真实验证

- 独立origin/main工作树：246项通过；2个不适用的“没有fallback却要求中断fallback绑定”参数组合跳过。
- 原本地脏checkout：21项通过、同2组合跳过，未覆盖其他会话开发。
- 发布前：从生产读取每个R3最近3个已成功Case的冻结输入和父R2，共6次Luna真实调用；加每节点一次Haiku调用，共8次均成功。W2结果通过现有validate_alignment覆盖校验。
- 这3个W2样本均为仅RELATED路径，未将其作为HIT正例业务验收；完整Condition身份约束另有离线覆盖。
- API新镜像健康后，再用实际已部署代码重放同样8次主/备调用，全部成功。原生回执确认为gpt-6-luna / fast，不只检查请求参数。
- 自动主备切换、错误回执、进程在fallback绑定前中断后的恢复，以及父上下文完整保留均有离线测试。未在真实业务Case上故意制造API故障。

## 发布身份与状态

- 实现提交：`ecbdd2bcc89d705c559562e6c9aeb7bec0a1daf5`，已提交origin/main并同步远端。
- 新profile：`openai_luna_all_high`，API/Scheduler overlay启用；其他角色和全局.env.v2保留原配置。
- 切换时间UTC：2026-11-10T21:10:47.527113+00:00。
- 发布前execution：`49601a112a4dc8632a40eecf42eb1212bdb3b0772bbc5476db42f8b69d4a3f5c`。
- 定向发布execution：`6d9fcdc77992b472ec4886f87f127d8c9eb3aca2c243780c298227c4739b32cc`。
- 最终执行配置：`7ba145f69e061c688467b127a1186e177c4d4b2e7fa713e2340160b42206ddab`。
- 最终快照对比：仅W1R3/W2R3改变，其他模型字段、prompts/contracts相同；只有runtime_models.json asset改变。

- doxagent-v2-v2-api-1: `sha256:1fed03bbeacf7b4a7f56810e6a6de2990678c501355b4267f6642ecedd55049c`；health=healthy，restarts=0。
- doxagent-v2-v2-scheduler-1: `sha256:23294f30df9f04e037b2db6ab1f3f482d7378c515edda6d94215f0a1b6597bc1`；health=None，restarts=0。

API、Scheduler均以各自当时实际运行镜像为基底，只应用本提交的4个文件差量并构建新镜像，保留并行上线的W3性能/恢复和其他角色修复。逐文件hash与构建after记录一致。部署使用现有发布锁及Compose链，首次锁被占用时等待后重试，没有强占或覆盖另一个发布。

其他容器镜像及启动时间保持不变数量：22；同时观测到其他变化：[]（若非空需结合独立并行发布解释，本次只重建两个角色）。

## 自然流量

截至UTC 2026-11-10T21:14:31.886591+00:00，切换后观测到R3回执2次，其中Luna 2次、成功2次。旧Case按原冻结配置继续，不因发布改写。

|ticker|lane|Case|status|context_mode|input tokens|cached tokens|
|---|---|---|---|---|---|---|
|BE|W2|case_19f2795c6cd746a6bb94dad4cfac58ba|OK|REPLAY_PARENT|8565|1327|
|BE|W1|case_19f2795c6cd746a6bb94dad4cfac58ba|OK|REPLAY_PARENT|3871|2865|

该采样用于确认发布、上下文回放与可消费，不等同整体业务准确率或高峰稳定性保证。

## 证据与回滚

本地证据：`eval/luna-r3-release-20261111/`。远端：`/var/lib/doxagent/releases/luna-r3-20261111/`，含4文件补丁及before/after hash、基底/目标镜像、build.json、activation.json、smoke.json、verification.json。包含环境参数的manifest和原Compose/state备份只保存在远端受限权限目录，不输出、不提交密钥。

回滚应恢复此次两个角色的旧镜像/Compose配置；若active仍为本次派生版本，再恢复原执行配置。保留所有历史Case与业务记录，不删除volume。

changelog已记录实现与发布；本地其他会话的脏改动完整保留。
