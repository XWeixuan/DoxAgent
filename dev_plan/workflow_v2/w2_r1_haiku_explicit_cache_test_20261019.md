# W2R1：Haiku 5.5 / OpenRouter 显式 1 小时缓存配置与实测

日期：2026-10-19（北京时间）。本轮配置、真实调用和统计只针对 W2R1；未切换生产模型、未修改生产调度或部署。测试配置位于 `eval/w2_r1_haiku_cache_20261019/`。

## 1. 结论

显式缓存创建和读取均已成功。通过把完整 Policy 库放在稳定前缀、当前消息放在断点之后，热缓存调用覆盖约 **99% 输入 token**，无需删减 Policy、改变召回业务提示词或减少推理强度。

1 小时 TTL 已通过请求参数和逐 token 写入账单核对。超过 5 分钟闲置的读取验证及最终总计见后面的最终实测节。本轮不把该测试解释为“已经等待 1 小时验证过期行为”。

## 2. 官方规则与配置选择

依据 [OpenRouter Prompt Caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching)、[Haiku 提供商列表](https://openrouter.ai/anthropic/claude-haiku-5.5#providers) 与 [Claude Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)。认证 API 的当次端点信息另保存为 `endpoints.json`。

- 使用 Chat Completions 的文本块级 `cache_control`，显式指定 `{"type":"ephemeral","ttl":"1h"}`。不是创建可引用的独立缓存对象，也不需要额外“建缓存”请求：首次真实研判负责写入，后续真实研判携带相同前缀负责读取。
- Haiku 5.5 的最低缓存前缀长度为 512 tokens；当前 W2R1 固定指令和完整 Policy 库均满足要求，无需凑长度。
- Google Vertex Europe 支持显式 1 小时缓存。本轮锁定 `google-vertex/europe`，关闭 fallback，避免换供应商造成缓存丢失。
- 同一 API key、模型、端点、指令、输出 Schema 和 Policy 前缀保持一致。稳定序列化字典，保持 Policy 数组的业务顺序；不为了命中而隐藏真实 Policy 更新。
- 1 小时写入按普通输入的 2 倍计费，读取按 0.1 倍计费。读取会刷新存活时间；一次请求的缓存生命周期从请求开始计时。默认 5 分钟缓存与本轮显式 1 小时缓存不同。

### 请求形态

```json
{
  "model": "anthropic/claude-haiku-5.5",
  "provider": {
    "only": ["google-vertex/europe"],
    "allow_fallbacks": false,
    "require_parameters": true
  },
  "reasoning": {"effort": "high"},
  "session_id": "w2r1-<固定指令与Policy前缀的SHA256>",
  "messages": [
    {
      "role": "system",
      "content": [{
        "type": "text",
        "text": "core.md + w2_r1.md + 原有严格输出Schema说明",
        "cache_control": {"type": "ephemeral", "ttl": "1h"}
      }]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "原有只读业务数据说明 + 完整runtime_policy_projection",
          "cache_control": {"type": "ephemeral", "ttl": "1h"}
        },
        {"type": "text", "text": "原有Current Case Input + source_message"}
      ]
    }
  ],
  "response_format": {"type": "json_schema", "json_schema": "原有strict schema"},
  "max_tokens": 16384,
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

上例中的占位符仅用于解释；真实、可直接执行的请求由 `cache_request.py` 构造，完整请求保存在每条回执中。

## 3. 为什么这样组装

现行 `_cache_optimized_input()` 已经把 `runtime_policy_projection` 放在前面，把 `source_message` 放在后面，但原 Bailian 路径不具备 OpenRouter Claude 的块级缓存标记。本轮沿用同一业务序列化文本，仅拆分为两个文本块，不改业务数据或提示词内容。

两个断点各有用途：

1. **固定指令断点**：不同 ticker、Policy 版本之间也能复用这部分。
2. **完整 Policy 库断点**：同一 Policy 版本下的不同消息共用整个库。消息 ID、正文、标题等动态字段只在后面出现。

`session_id` 帮助保持路由身份，但它本身不会创建缓存或使不相同的前缀命中。实际读写以 usage 为准。这里也没有维护永久的“已热”布尔状态，避免在过期、端点变化后错误认为缓存仍存在。

固定指令由当前 `RuntimeV2PromptSet` 的实际组合方式核对：`core.md + w2_r1.md` 与本轮冻结样本完全一致，SHA256 为 `286e1850f21438a110df3eccec094455d1caf5fea09e9f240cce6c9196d5f1b5`。保留此前选定的 high 推理强度和严格输出，不同时混入推理强度实验。

## 4. 测试设计与证据边界

沿用之前样本中的 INTC、BE、RKLB，包括召回与不召回消息，不重跑 Qwen 基线。使用新加坡远端现有调度器镜像，在隔离评估容器中串行调用；不挂载生产数据目录，只传 OpenRouter 凭据，凭据不保存到评估文件。

- INTC A：冻结样本 ordinal 3 的 173 条 Policy。
- INTC B：冻结样本 ordinal 19 的 173 条 Policy。与 A 有一个 Policy 定义不同，用于测试真实版本变动。
- “A + 不同消息”：仅把消息替换为 ordinal 19，明确固定 A 的 Policy 投影，属于缓存边界对照，不冒充该历史消息当时的原始投影。
- BE / RKLB：分别使用冻结 ordinal 1 / 7，完整保留 124 / 138 条 Policy。
- 先完成 10 次连续调用，再让 INTC A 前缀闲置超过 5 分钟，追加一次读取。

统计口径：`cached_tokens / prompt_tokens` 是输入 token 读取占比；`cache_write_tokens` 是写入量。单看“请求里存在一点缓存”会把只读到固定指令的冷启动也计为命中，因此另外统计“无写入的完整热缓存请求”。

### 连续 10 次结果

| 场景 | 输入 tokens | 缓存读取 | 缓存写入 | 输入读取占比 | 总耗时 s | 总费用 USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| INTC A 首次 | 56,582 | 0 | 55,822 | 0.00% | 6.340 | 0.01270654 |
| INTC A 重复 | 56,582 | 55,822 | 0 | 98.66% | 5.891 | 0.00096549 |
| INTC A 更换消息 | 55,982 | 55,822 | 0 | 99.71% | 1.956 | 0.00069709 |
| INTC B 更新一个 Policy | 56,082 | 1,920 | 54,002 | 3.42% | 2.064 | 0.01197966 |
| INTC B 重复 | 56,082 | 55,922 | 0 | 99.71% | 2.229 | 0.00069489 |
| BE 首次 | 46,081 | 1,920 | 43,709 | 4.17% | 9.055 | 0.01021537 |
| BE 重复 | 46,081 | 45,629 | 0 | 99.02% | 7.454 | 0.00111319 |
| RKLB 首次 | 41,930 | 1,920 | 39,750 | 4.58% | 6.740 | 0.00916652 |
| RKLB 重复 | 41,930 | 41,670 | 0 | 99.38% | 4.638 | 0.00083292 |
| 切回 INTC A | 56,582 | 55,822 | 0 | 98.66% | 4.026 | 0.00094514 |

10/10 均通过 Schema、Policy 身份和正常结束校验，候选集合均与已有对应消息的 Qwen 基线一致。这是有限样本的缓存回归检查，不是重新评估召回质量。

### 1 小时写入账单核验

本次 Europe 端点单价：普通输入 $0.11/M、1h 写入 $0.22/M、读取 $0.011/M、输出 $0.55/M（本轮所有输入均低于 100k tokens）。

首条调用：

```text
55,822 × $0.22/M + 760 × $0.11/M = $0.01236444 输入费用
622 × $0.55/M                       = $0.00034210 输出费用
合计                                 $0.01270654
```

与 OpenRouter usage 的输入费用和总费用完全吻合。每条调用均按同样公式重算，检查不是以 5 分钟的 1.25 倍写入价结算。

## 5. 成本、时效与后续接入边界

前 10 次中，6 次完整热缓存调用的加权输入读取占比为 **99.19%**；包括输出 token 后，费用较“同样 token 量全部按普通输入计费”下降 **85.42%**。包含四个 Policy 前缀的冷写入后，全部 10 次实际费用为 **$0.049316817**，相同 token 无缓存计价为 **$0.05938394**，节省 **16.95%**。

后者是账单重算，未额外调用无缓存模型，不能冒充重新跑出的无缓存对照实验。首次 1h 写入更贵；同一固定前缀只使用一两次时未必省钱，通常第三次使用后才体现净节省。稳定库上的连续消息越多，越接近热缓存收益。

缓存减少输入预填充和费用，不消除 high 推理的生成耗时。示例冷/热耗时 INTC 为 6.340/5.891 秒，BE 为 9.055/7.454 秒，RKLB 为 6.740/4.638 秒；输出和推理 token 每次不同，样本也少，不能据此承诺固定时延降幅。

生产接入时应复用本轮请求配置和拆块方式，保留当前超时、取消、租约、重试和历史 Case 冻结机制。不能仅把 Bailian 的 model 字符串换成 Haiku：还需要 W2R1 专属 OpenRouter transport/凭据路由、模型配置冻结和缓存写入 usage 留痕。本轮完成的是可执行缓存实验配置，未擅自完成这些生产切换。

不增加定时空调用预热、不逐 Policy 创建缓存对象、不为了命中而屏蔽版本更新。首次同前缀的并发请求可能重复支付写入成本；本轮按要求串行，未把所有生产 Case 改成串行，也不声称测过冷并发下的命中率。

## 6. 复现与文件

- `cache_request.py`：模型、端点、high、严格 Schema、默认 1h、两个显式断点及稳定路由身份。
- `dataset.json` / `delayed_dataset.json`：冻结输入、原始消息 ordinal 和 Policy 投影来源。
- `run_eval.py` / `launch_remote.py`：串行真实调用、原始 SSE、隔离容器执行与凭据临时文件清理。
- `initial/calls.jsonl` / `delayed/calls.jsonl`：完整请求、原始输出、usage、计时、响应 provider/model。
- `summarize.py` / `summary.json`：读写 token、成本重算、逐条断言与最终汇总。
- `test_cache_request.py`：输入文本完整保留、动态消息不破坏前缀、Policy/指令更新正确失效、字典序列化稳定。

本地复算：

```powershell
.venv/Scripts/python.exe -m pytest eval/w2_r1_haiku_cache_20261019/test_cache_request.py -q
.venv/Scripts/python.exe -X utf8 eval/w2_r1_haiku_cache_20261019/summarize.py
```

## 7. 最终实测

最后一次使用 INTC A 的响应结束后，闲置 **319.359 秒**再发起不同消息的研判：

| 场景 | 输入 tokens | 缓存读取 | 缓存写入 | 输入读取占比 | 总耗时 s | 总费用 USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| INTC A 闲置超过 5 分钟后 | 55,982 | 55,822 | 0 | 99.71% | 2.150 | 0.00069709 |

这证明相同 Policy 前缀在本轮超过默认 5 分钟的闲置窗口后仍可读，结合所有写入均按 1h 价格结算，支持默认 1 小时配置确实生效。未等待整整 1 小时测试过期边界，也不将远端缓存留存当作可永久依赖的业务状态。

最终 **11/11 调用成功**，候选集合均与已有相应消息基线一致。统计为：

| 口径 | 全部 11 次，含冷写入/版本更新 | 7 次完整热缓存请求 |
| --- | ---: | ---: |
| 输入 tokens | 569,896 | 369,221 |
| 缓存读取 tokens | 372,269 | 366,509 |
| 缓存写入 tokens | 193,283 | 0 |
| 加权输入读取占比 | **65.32%** | **99.27%** |
| 实际总费用 | **$0.050013909** | **$0.005945819** |
| 相同 token 无缓存计价 | $0.06560741 | $0.04223021 |
| 含输出费用的节省 | **23.77%** | **85.92%** |

热缓存单次输入读取占比区间为 **98.66%～99.71%**；所有 7 次应当完整复用前缀的请求均成功读取且无新写入，不能把这个小样本的 7/7 当作生产长期可用率保证。

本地 4 项缓存边界测试通过，全部脚本通过编译检查。评估容器均正常退出，临时凭据文件已清理；生产配置、容器和历史 Case 未更改。
