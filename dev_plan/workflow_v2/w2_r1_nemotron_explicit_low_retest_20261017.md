# Nemotron 显式 low / exclude=false / 64K 输出上限复测（2026-10-17）

按用户给定配置，先通过认证请求查模型/端点，再在相同5条W2R1冻结Case上真实调用。5条全部有效、候选集合与既有Qwen基线5/5一致；但总输出仍为29,632 token，其中推理29,027，未解决推理输出偏多的现象。不能据此声称low已经有效映射到CoreWeave的内部短推理模式，也不能直接断言它被忽略。

## 认证查询得到的支持范围

使用本次调用同一OpenRouter凭据GET `/api/v1/models/nvidia/nemotron-3.5-super-vl/endpoints`，CoreWeave仅有一个返回端点，tag=`coreweave/nvfp4`。其supported_parameters包含：

```json
[
  "reasoning",
  "include_reasoning",
  "max_tokens",
  "temperature",
  "top_p",
  "top_k",
  "repetition_penalty",
  "frequency_penalty",
  "presence_penalty",
  "stop",
  "seed",
  "response_format",
  "structured_outputs",
  "tools",
  "tool_choice",
  "logprobs",
  "top_logprobs"
]
```

端点声明支持reasoning/include_reasoning，但没有提供low/medium/high枚举。进一步认证GET `/api/v1/models` 中本模型的reasoning对象为：

```json
{
  "mandatory": false
}
```

这里没有 `supported_efforts`，也没有 `default_effort` 或 `supports_max_tokens`。不能把 `supported_parameters` 中存在reasoning，直接解释成“low已被端点确认支持”。OpenRouter文档规定，supported_efforts省略表示模型没有公开effort选择；该字段若为null则是另一种含义，不能把缺字段当null。[OpenRouter推理参数发现说明](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens#discovering-per-model-reasoning-options)。

## 本次实际请求

```json
{
  "model": "nvidia/nemotron-3.5-super-vl",
  "provider": {"only": ["coreweave"], "allow_fallbacks": false, "require_parameters": true},
  "reasoning": {"effort": "low", "exclude": false},
  "max_tokens": 65536,
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

保留原任务messages、完整Policy、顺序及JSON Schema strict；仅以上述三个请求差异重跑，无输出修复重试、不重跑Qwen。5Case并发，在新加坡主机的独立容器执行，不挂载生产数据或写业务Case/Effect。实际响应provider均为CoreWeave。metadata只返回该提供商的nvfp4端点，因此当前 `only=coreweave` 与原先 `only=coreweave/nvfp4` 没有查到不同后端；不能把速度差异归因于换了另一个量化后端。

## 同5条的首轮比较

| 指标 | 上轮low，16K上限 | 本轮显式low/exclude=false，64K上限 |
|---|---:|---:|
| 有效输出 | 4/5 | 5/5 |
| 与Qwen候选集合一致 | 4/5（失败计入分母） | 5/5 |
| 平均耗时 | 15.44s | 13.78s |
| P50 | 12.40s | 9.33s |
| 最大耗时 | 36.63s | 28.96s |
| 输入token | 190,624 | 190,624 |
| 输出token（已含推理） | 30,184 | 29,632 |
| 其中推理token | 29,640 | 29,027 |
| 缓存输入 | 0 | 0 |
| 总费用 | $0.04368560 | $0.04340960 |
| 流式接收窗口估算TPS P50 | 516.2 | 485.8 |

输出只减少552 token（1.8%）。平均耗时下降约10.8%，但5条单次调用、网络/负载和采样不同，且同时改了参数与上限，不能把变化因果归于exclude或effort映射。TPS仍是客户端窗口估算，不是服务端原生吞吐。

费用按认证端点快照的输入$0.15/M、缓存输入$0.02/M、输出$0.50/M逐条复算，均与返回usage.cost一致。推理已在输出计费中，不重复相加。

## 每条明细

| 原20条序号 / Case | 上轮输出/推理token | 本轮输出/推理token | 本轮耗时 | 本轮结果 |
|---|---:|---:|---:|---|
| 1 / LS Electric订单 | 16,384/16,384 | 15,407/15,208 | 28.96s | 与基线候选集合一致 |
| 3 / Intel工厂爆炸 | 4,410/4,245 | 2,847/2,695 | 9.33s | 与基线候选集合一致 |
| 6 / UNT注资/扩产 | 6,299/6,153 | 8,081/7,985 | 18.36s | 与基线候选集合一致 |
| 7 / Neutron首飞延期 | 1,965/1,828 | 2,387/2,243 | 7.35s | 与基线候选集合一致 |
| 19 / Lennar住宅The Foundry | 1,126/1,030 | 910/896 | 4.89s | 与基线候选集合一致 |

上轮30,184是5条合计，并非单条生成了30K；其中LS Electric一条16,384全是推理，返回length且无答案。本轮该条生成15,407输出、15,208推理后正常结束；它在本次实际仍未超过原16K上限，说明“放宽上限后恢复”不能简单解释成该任务必需16K以上，而有生成过程波动。UNT一条反而由6,299输出增至8,081。

## 如何解释low是否生效

1. 已证实原请求和本轮都实际发送了low，不是评测代码漏传。认证接口和HTTP200只证实网关接受了该配置，未提供内部effort映射回执。
2. exclude=false与默认值相同，只要求返回推理内容，不是推理节流开关。官方说明即使exclude=true，内部推理仍会执行并计费。[exclude说明](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens#excluding-reasoning-tokens)。
3. max_tokens=65536是总输出空间，不是限制推理到较短长度；模型仍可以在low下产生很多推理token。
4. 本轮没有公开档位证据，也没有发现显式exclude=false让推理量明显下降。因此应标记为“low已发送、内部映射未证实”，不能把这个端点当作已经验证的可控低推理端点。要进一步判断是否忽略effort，需要上游确认或另外设计effort开关对照；本轮不擅自增加其它模型档位/无推理调用。

同组Haiku high旧回执平均5.38秒、5条合计2,959输出（2,162推理），此处仅复用之前结果，不新增其调用。

原认证响应、完整请求/SSE/usage、逐条身份与价格校验、summary.json和临时凭据/测试容器清理回执在 `eval/w2_r1_nemotron_low_explicit_20261017/`。本轮仅5次真实模型调用，没有生产配置改动或基线重跑。
