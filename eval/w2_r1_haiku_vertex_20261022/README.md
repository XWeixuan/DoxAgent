# Haiku 5.5：OpenRouter → Google Cloud Vertex

2026-10-22。本地协议接入与离线验证已完成，用户已提供项目 `gen-lang-client-0328762493`，真实调用在 Google 鉴权阶段被 API key 不受支持阻断，仍待 OAuth/ADC。没有修改当前 provider 开关，没有 push 或部署。用户提供的 key 仅用于进程内探测，未写入配置、数据集或代码。

## 保留的调用行为

- 模型映射：OpenRouter `anthropic/claude-haiku-5.5` → Vertex `claude-haiku-5-5`，端点 global。
- high → 原生 `thinking:{type:adaptive}`、`output_config.effort:high`；max_tokens=16384，流式输出；Schema 映射到原生 `output_config.format`。
- system 与完整 policy projection 继续分别使用原来的 `cache_control:{type:ephemeral,ttl:1h}`。业务文本、Schema、缓存断点、顺序和 prefix fingerprint 一致。
- W1R1 同步支持迁移，reference + 每 5 条 provisional 的累计缓存布局不改。原有 Vertex profile 把 W1R1 的 120s 改成 180s，本次修正为保留有效配置；W2R1 沿用有效的 180s。
- 原生 usage 的 `input_tokens` 不含缓存部分：规范化总输入=uncached+cache_read+cache_creation；保留原生 usage 和 5m/1h 写入分项，SSE 累计快照不重复相加。
- 历史 Case 根据冻结 provider 调度，OpenRouter 老 Case 不因新 profile 被改成 Vertex。显式凭据失败不回退其他凭据、其他 provider，也不产生隐式重试。

## 配置与鉴权

现有 `vertex_recall` profile 只切 W1R1/W2R1，不夹带 GLM 切换：

```text
DOXAGENT_PERSISTENT_RUNTIME_V2_API_PROFILE=vertex_recall
DOXAGENT_VERTEX_PROJECT=<Google Cloud Project ID>
```

Google/Anthropic 官方标准 Claude 路径为：

```text
https://aiplatform.googleapis.com/v1/projects/<PROJECT_ID>/locations/global/publishers/anthropic/models/claude-haiku-5-5:streamRawPredict
```

标准鉴权为 OAuth/ADC（google-auth 刷新令牌）。eval runner 可从进程环境 `DOXAGENT_VERTEX_API_KEY` 读取 key，以 x-goog-api-key 做明确的诊断请求。现在标准接口已实测拒绝 API key，**不再把该环境变量接入生产 factory/dispatcher/settings**；生产迁移只使用 OAuth/ADC。若收到 401 则记录 `provider_auth_failed` 和脱敏错误，不静默回退其他凭据/provider。key 不进入模型设置、Case、execution bundle 或回执。

最初不带项目的探测返回 HTML 404（`api_key_probe.json`）。取得项目后，标准地址明确返回 `401 UNAUTHENTICATED`：`API keys are not supported by this API. Expected OAuth2 access token ...`（`project_api_key_probe.json`）。这已确认凭据类型不适用，尚不能判断模型是否已开通或缓存是否可用。本地尚无 gcloud/ADC。

首个 runner 调用还暴露出 Google 错误 JSON 是数组而不是对象，导致错误解析异常；已修复对象/数组兼容并补回归，原异常记录不覆盖。修复后用 `api-key-confirmed` 标签重跑首例，正常记录 `provider_auth_failed`、usage=null、其余9例未尝试，不发9次相同鉴权失败。加上直接确认请求，项目地址共3次请求均在鉴权前失败；没有模型结果，没有缓存命中率，也不把未知usage记成0。

## 10例数据及执行

`dataset.json` 从原 `eval/w2_r1_model_low_20261016/dataset.json` 固定选择 10 个不同 Case：原序号 1/16/17/9/2/10/5/13/14/15，召回与未召回各5。使用它们各自原始完整 policy projection，不给某条消息替换另一时刻的 Policy。五组自然共享前缀，按前缀聚类串行发送，兼顾首次写缓存和不同消息的后续读取；没有以同 Case 重复调用充当10例。

准备（不调用模型）：

```powershell
.venv/Scripts/python.exe -X utf8 eval/w2_r1_haiku_vertex_20261022/run_eval.py --prepare
```

API key 诊断入口（已实测401，不能当成功迁移路径）：

```powershell
.venv/Scripts/python.exe -X utf8 eval/w2_r1_haiku_vertex_20261022/run_eval.py --project <PROJECT_ID> --auth api-key --label api-key
```

配置 ADC 后使用 `--project gen-lang-client-0328762493 --auth adc --label adc` 继续。runner 不写生产库，不更新旧回执，同标签已有记录不覆盖。每次保留输入hash、实际调用结果、原生usage、写入/读取token、耗时与候选Policy范围校验，并按总读取/总输入计算加权命中率。首次写缓存与同prefix后续请求应分开看，不能期待跨账户/跨provider复用OpenRouter的旧缓存。

目前模型推理成功数和缓存命中率**未测得**，不是0%命中。取得项目和有效鉴权后继续该runner即可。

## 验证

迁移/native/OpenRouter/W1缓存的25项检查通过；再合并 W1R2 与持久化运行回归，新增数组错误回执回归后合计85项通过。验证请求块等价、1h TTL、native usage、凭据失败不回退/不泄漏、历史provider不变、有效超时/批大小保留。相关新增/修改模块及评估脚本 Ruff 通过。

官方依据：[Claude 原生调用](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/partner-models/claude/use-claude)、[Haiku 5.5](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/partner-models/claude/haiku-5-5)、[1h Prompt caching](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/partner-models/claude/prompt-caching)、[Anthropic Google Cloud 接入](https://platform.claude.com/docs/en/build-with-claude/claude-on-vertex-ai)。

## 远端续报

服务账户凭据已安全落盘远端，OAuth验证成功；远端10例全部被global Haiku请求配额429阻断，缓存命中率未测得。详见 [远端凭据保管与实测](REMOTE_RESULTS.md)。本地旧API-key失败回执保留。
