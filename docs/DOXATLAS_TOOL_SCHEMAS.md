# DoxAtlas Tool Server API 与工具 Schema

本文档定义 `doxatlas_tools` 通过 HTTP Tool Server 暴露给 DoxAgent 的远程调用契约。当前版本以 agent-facing evidence graph 为目标：默认返回短代码、scope、compact evidence 与下一跳工具提示，而不是前端 dashboard 的全量 JSON。

## 一、API 配置说明

### 1. 服务挂载位置

Tool Server 合入 DoxAtlas backend FastAPI 服务，统一挂载在：

```text
/api/doxa-tools
```

本地开发示例：

```text
http://localhost:8000/api/doxa-tools
```

生产环境示例：

```text
https://<your-doxatlas-domain>/api/doxa-tools
```

### 2. 必填环境变量

在 backend 运行环境中配置：

```bash
DOXATLAS_TOOL_SERVER_TOKEN=<generate-a-server-side-token>
DOXATLAS_TOOLS_USER_ID=<service-user-uuid>
```

- `DOXATLAS_TOOL_SERVER_TOKEN`：DoxAgent 调用 Tool Server 时使用的静态 Bearer Token。
- `DOXATLAS_TOOLS_USER_ID`：Tool Server 绑定的固定 DoxAtlas 服务用户 UUID。

HTTP 请求体中不要传 `user_id`。Tool Server 会在后端自动注入 `DOXATLAS_TOOLS_USER_ID`。

如果任一环境变量未配置，工具接口返回：

```json
{
  "error": {
    "code": "TOOL_SERVER_NOT_CONFIGURED",
    "message": "DOXATLAS_TOOL_SERVER_TOKEN and DOXATLAS_TOOLS_USER_ID must be configured.",
    "details": null
  }
}
```

### 3. 请求规范

所有工具接口均使用：

```text
POST /api/doxa-tools/<tool-endpoint>
```

请求头：

```http
Authorization: Bearer <DOXATLAS_TOOL_SERVER_TOKEN>
Content-Type: application/json
```

请求体必须是 JSON。除本文档列出的字段外，不允许传入额外字段。

### 4. 鉴权与 CORS

Tool Server 是 DoxAgent 到 DoxAtlas 的服务间 API，不面向浏览器前端开放。

- Bearer Token 使用 constant-time compare 校验。
- 不开启任意浏览器 CORS。
- 对 `/api/doxa-tools/*` 的 `OPTIONS` 预检请求返回 `405 METHOD_NOT_ALLOWED`。

### 5. 统一错误格式

所有工具接口的错误响应形态统一为：

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable message.",
    "details": null
  }
}
```

常见错误码：

| HTTP 状态码 | code | 含义 |
| --- | --- | --- |
| `401` | `UNAUTHORIZED` | 缺少 token、token 格式错误或 token 不匹配 |
| `405` | `METHOD_NOT_ALLOWED` | 工具路由不接受该 HTTP 方法，例如 CORS preflight |
| `422` | `INVALID_PARAMS` | 请求 JSON 不符合接口 schema |
| `400` | `INVALID_PARAMS` | 工具逻辑发现普通参数不合法 |
| `400` | `INVALID_SCOPE` | 父级 scope 或短代码组合不合法 |
| `400` | `INVALID_ID_FORMAT` | UUID 格式不合法，或传入了 DoxAgent 内部 id |
| `400` | `SHORT_ID_OVERFLOW` | 单一 scope 下对象数量超过两位 base36 短代码容量 |
| `500` | `TOOL_SERVER_NOT_CONFIGURED` | 服务端缺少必要环境变量 |
| `500` | `TOOL_EXECUTION_FAILED` | 工具执行时出现未预期异常 |

`INVALID_SCOPE` / `INVALID_ID_FORMAT` 都是非 retryable contract error。PostgREST `22P02` 这类 UUID 类型错误会被映射为 `INVALID_ID_FORMAT`，不会再作为 retryable `500 TOOL_EXECUTION_FAILED` 暴露。

## 二、短代码与 Scope 约定

数据库内部仍使用 UUID；agent-facing contract 使用 scoped short id。短代码只在父级 scope 内有效。

| 层级 | 前缀 | 示例 | 父级 scope |
| --- | --- | --- | --- |
| narrative run | `R` | `R01` | `ticker` |
| analysis task | `T` | `T01` | `ticker` |
| narrative | `N` | `N01` | `run_id` |
| narrative event | `E` | `E01` | `narrative_id` 或 `run_id + narrative_code` |
| proposition | `P` | `P01` | event scope |
| media result | `M` | `M01` | 调用 `get-media-result` 时的同一 scope |
| social result | `S` | `S01` | 调用 `get-social-result` 时的同一 scope |
| event source document | `D` | `D01` | event scope |
| ignored proposition | `I` | `I01` | run/narrative/event scope |

通用 event scope 支持三种形态：

```json
{"narrative_event_id": "<full-event-uuid>"}
{"narrative_id": "<full-narrative-uuid>", "event_code": "E01"}
{"run_id": "<full-run-uuid>", "narrative_code": "N01", "event_code": "E01"}
```

不要把 DoxAgent 内部 `event_id` 当作 DoxAtlas `narrative_event_id` 或 `event_code`。

## 三、Endpoint 总览

| 工具名 | HTTP Endpoint | 说明 |
| --- | --- | --- |
| `doxa_run_narrative_research` | `POST /api/doxa-tools/run-narrative-research` | 启动叙事研究 |
| `doxa_run_analysis` | `POST /api/doxa-tools/run-analysis` | 启动单标的消息面分析 |
| `doxa_get_narrative_report` | `POST /api/doxa-tools/get-narrative-report` | 读取叙事研究报告；默认 agent provenance view |
| `doxa_query_analysis` | `POST /api/doxa-tools/query-analysis` | 查询可用 analysis task 的 `Txx` 短代码与时间窗口 |
| `doxa_get_analysis` | `POST /api/doxa-tools/get-analysis` | 读取单标的 dashboard payload，可用 `task_code` 或 `task_id` |
| `doxa_query_propositions` | `POST /api/doxa-tools/query-propositions` | 按 event scope 查询 compact propositions |
| `doxa_get_ignored_propositions` | `POST /api/doxa-tools/get-ignored-propositions` | 按 run/narrative/event scope 查询 ignored propositions |
| `doxa_get_media_result` | `POST /api/doxa-tools/get-media-result` | 查询 compact `Mxx` media result list |
| `doxa_get_media_result_detail` | `POST /api/doxa-tools/get-media-result-detail` | 按 `Mxx` 拉 URL、source quality 与正文，默认 full content |
| `doxa_get_social_result` | `POST /api/doxa-tools/get-social-result` | 查询 compact `Sxx` social result list |
| `doxa_get_social_result_detail` | `POST /api/doxa-tools/get-social-result-detail` | 按 `Sxx` 拉 URL、source 与正文，默认 full content |
| `doxa_get_event_source` | `POST /api/doxa-tools/get-event-source` | 查询 event 直接绑定的 `Dxx` 新闻源材料，默认 full content |

## 四、工具 Schema

### 1. doxa_run_narrative_research

启动一个 Narrative Research run。

Endpoint:

```text
POST /api/doxa-tools/run-narrative-research
```

Input:

```json
{
  "ticker": "MU",
  "language": "en",
  "force": false
}
```

Output:

```json
{
  "run_id": "<run-uuid>",
  "status": "pending|running|completed|failed"
}
```

### 2. doxa_run_analysis

启动单标的消息面分析 task。默认会复用同一服务用户、同一 ticker 在短时间内创建的近期 task。

Endpoint:

```text
POST /api/doxa-tools/run-analysis
```

Input:

```json
{
  "ticker": "MU",
  "language": "en",
  "reuse_recent": true
}
```

Output:

```json
{
  "task_id": "<task-uuid>",
  "status": "pending|processing|completed|failed",
  "reused_existing": false
}
```

### 3. doxa_get_narrative_report

读取叙事研究报告。默认 `view="agent_provenance"`。

Endpoint:

```text
POST /api/doxa-tools/get-narrative-report
```

Input:

```json
{
  "ticker": "MU",
  "run_id": null,
  "view": "agent_provenance",
  "include_reasoning": false,
  "include_source_propositions": false
}
```

`view` 可选：

- `agent_provenance`：默认 agent view，返回短代码、scope、下一跳工具提示。
- `dashboard_summary`：返回 compact meta/kpi/rank summary。
- `full`：返回接近旧版 full payload，但仍默认去掉 reasoning/COT 与完整 `source_propositions`；如需调试，显式传 `include_reasoning=true` 和/或 `include_source_propositions=true`。

`agent_provenance` Output:

```json
{
  "status": "ok",
  "view": "agent_provenance",
  "map_version": "doxatlas-shortid-v1",
  "run_ref": {
    "ticker": "MU",
    "market": "us",
    "run_code": "R01",
    "run_id": "<run-uuid>",
    "status": "completed",
    "window_days": 30,
    "source_timeframe": "7d",
    "effective_source_timeframe": "7d",
    "created_at": "...",
    "completed_at": "..."
  },
  "narrative_flow_summary": [
    {
      "node": 1,
      "offset_days": -30,
      "bucket_date": "2026-05-21",
      "ranks": [
        {
          "rank": 1,
          "narrative_code": "N01",
          "title": "...",
          "direction": "bullish",
          "sov": 0.31
        }
      ]
    },
    {
      "node": 10,
      "offset_days": 0,
      "bucket_date": "2026-06-20",
      "ranks": []
    }
  ],
  "narratives": [
    {
      "narrative_code": "N01",
      "title": "...",
      "direction": "bullish",
      "brief_thesis": "...",
      "metrics": {"combined_sov": 0.31},
      "event_count": 4,
      "source_proposition_count": 18,
      "narrative_explanation_media": "...",
      "narrative_expectations_media": "...",
      "events": [
        {
          "event_code": "E01",
          "event_time": "...",
          "event": "...",
          "overview": "...",
          "display_source": "...",
          "source_proposition_codes": ["P01", "P02"],
          "source_proposition_count": 2,
          "source_viewpoint_count": 7,
          "top_source_proposition_summaries": [
            {
              "proposition_code": "P01",
              "source_type": "media",
              "sentiment_direction": "bullish",
              "proposition_summary": "..."
            }
          ],
          "next": {
            "query_propositions": {
              "tool": "doxa_query_propositions",
              "input": {"run_id": "<run-uuid>", "narrative_code": "N01", "event_code": "E01"}
            }
          }
        }
      ]
    }
  ]
}
```

默认不返回：

- `narrative_id`
- `narrative_event_id`
- `proposition_id`
- reasoning/COT 字段
- 完整 `source_propositions`
- source 正文
- `source_weights`
- `momentum_rank`

`narrative_flow_summary` 固定返回 10 个 30d flow 节点；每个节点按该节点时点附近的 `combined_sov` 排序，最多返回前 10 条 narrative rank。

### 4. doxa_query_analysis

查询某个 ticker 下可用的 completed analysis tasks，并返回 `Txx` 短代码。

Endpoint:

```text
POST /api/doxa-tools/query-analysis
```

Input:

```json
{
  "ticker": "MU",
  "limit": 10
}
```

Output:

```json
{
  "status": "ok",
  "ticker": "MU",
  "market": "us",
  "map_version": "doxatlas-shortid-v1",
  "sort_rule": "last_update_desc_created_at_desc",
  "analysis_tasks": [
    {
      "task_code": "T01",
      "status": "completed",
      "time_window": "7d",
      "window": {"start": "...", "end": "...", "source": "..."},
      "created_at": "...",
      "completed_at": "...",
      "task_source": "ad_hoc",
      "is_narrative_gap_fill": false,
      "next": {
        "get_analysis": {
          "tool": "doxa_get_analysis",
          "input": {"ticker": "MU", "task_code": "T01"}
        }
      }
    }
  ]
}
```

### 5. doxa_get_analysis

读取单标的 dashboard payload。该接口保留旧能力；agent 常规路径应先调用 `query-analysis` 获得 `task_code`。

Endpoint:

```text
POST /api/doxa-tools/get-analysis
```

Input:

```json
{
  "ticker": "MU",
  "task_code": "T01",
  "task_id": null,
  "capsule_limit": 5
}
```

说明：

- `task_code` 与 `task_id` 至少使用一种；都不传时查固定服务用户下最新 completed task。
- `task_id` 是兼容/debug hook，不建议作为 agent-facing 默认输入。

Output:

```json
{
  "ticker": "MU",
  "task_id": "<task-uuid>",
  "timeframe": "7d",
  "social": {"meta": {}, "kpi": {}, "topic_atlas": {}},
  "media": {"meta": {}, "kpi": {}, "topic_atlas": {}}
}
```

### 6. doxa_query_propositions

按 event-level scope 查询 compact propositions。不接受裸 `narrative_id` 拉整条 narrative 的 propositions；裸 `narrative_id` 会返回 `400 INVALID_SCOPE`。

Endpoint:

```text
POST /api/doxa-tools/query-propositions
```

Input:

```json
{
  "run_id": "<run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "proposition_codes": ["P01", "P03"],
  "limit": 20
}
```

也支持：

```json
{"narrative_event_id": "<event-uuid>", "proposition_codes": ["P01"]}
{"narrative_id": "<narrative-uuid>", "event_code": "E01"}
{"proposition_id": "<proposition-uuid>"}
```

Output:

```json
{
  "status": "ok",
  "map_version": "doxatlas-shortid-v1",
  "scope": {"ticker": "MU", "run_id": "<run-uuid>", "narrative_code": "N01", "event_code": "E01"},
  "sort_rule": "event_source_proposition_order",
  "propositions": [
    {
      "proposition_code": "P01",
      "ticker": "MU",
      "source_type": "media",
      "timeframe": "7d",
      "sentiment_direction": "bullish",
      "proposition_summary": "...",
      "combined_judgements": "...",
      "core_event": "...",
      "event_interpretation": "...",
      "forward_judgement": "...",
      "et": "...",
      "event_time_source": "...",
      "coherence": 0.91,
      "coherence_level": "high",
      "source_viewpoint_count": 5,
      "source_pill_count": 5,
      "created_at": "..."
    }
  ]
}
```

默认不返回 `proposition_id`、`topic_id`、`parent_event_cluster_id`、`core_event_id`、`task_id`、`user_id`、`source_viewpoints`、完整 `source_pills`。

### 7. doxa_get_ignored_propositions

按输入 scope 返回同层级 ignored propositions。

Endpoint:

```text
POST /api/doxa-tools/get-ignored-propositions
```

Input:

```json
{"run_id": "<run-uuid>"}
{"run_id": "<run-uuid>", "narrative_code": "N01"}
{"run_id": "<run-uuid>", "narrative_code": "N01", "event_code": "E01"}
{"narrative_id": "<narrative-uuid>"}
{"narrative_event_id": "<event-uuid>"}
```

Output:

```json
{
  "status": "ok",
  "map_version": "doxatlas-shortid-v1",
  "level": "run|narrative|event",
  "scope": {"run_id": "<run-uuid>", "narrative_code": "N01", "event_code": "E01"},
  "items": [
    {
      "ignored_code": "I01",
      "reason": "...",
      "source_type": "media",
      "sentiment_direction": "bearish",
      "summary": "...",
      "core_event": "...",
      "event_interpretation": "...",
      "created_at": "...",
      "relevance_reason": "same_topic_id"
    }
  ]
}
```

当前 narrative/event-level relevance 是工具层计算：优先按 topic、cluster、core event 或 direct proposition match 过滤。更强的中期方案是持久化 `ignored_scope_links`。

### 8. doxa_get_media_result

返回 compact `Mxx` media result list，不暴露 `analysis_result_id`、`task_id`、`original_id`。

Endpoint:

```text
POST /api/doxa-tools/get-media-result
```

Input:

```json
{
  "run_id": "<run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "proposition_codes": null,
  "limit": 20
}
```

Output:

```json
{
  "status": "ok",
  "map_version": "doxatlas-shortid-v1",
  "scope": {"ticker": "MU", "run_id": "<run-uuid>", "narrative_code": "N01", "event_code": "E01"},
  "sort_rule": "source_grade_enrichment_original_created_at_desc_id",
  "items": [
    {
      "media_code": "M01",
      "event": "...",
      "source_hint": "media",
      "detail_available": true
    }
  ]
}
```

### 9. doxa_get_media_result_detail

用同一父级 scope + `Mxx` 数组拉取 media 细节。

Endpoint:

```text
POST /api/doxa-tools/get-media-result-detail
```

Input:

```json
{
  "run_id": "<run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "media_codes": ["M01", "M03"],
  "content_mode": "full",
  "preview_chars": 1200
}
```

`content_mode` 可选 `preview|full|none`，默认 `full`。默认响应返回 `content`；只有显式传 `content_mode="preview"` 时才返回 `content_preview`。

Output:

```json
{
  "status": "ok",
  "map_version": "doxatlas-shortid-v1",
  "scope": {"ticker": "MU", "run_id": "<run-uuid>", "narrative_code": "N01", "event_code": "E01"},
  "content_mode": "full",
  "items": [
    {
      "media_code": "M01",
      "event": "...",
      "title": "...",
      "url": "...",
      "source_name": "...",
      "published_at": "...",
      "content_enrichment_status": "success",
      "source_grade": "B",
      "content_length": 6989,
      "content": "..."
    }
  ]
}
```

### 10. doxa_get_social_result

返回 compact `Sxx` social result list，不暴露 `analysis_result_id`、`task_id`、`original_id`。

Endpoint:

```text
POST /api/doxa-tools/get-social-result
```

Input:

```json
{
  "run_id": "<run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "proposition_codes": null,
  "limit": 20
}
```

Output:

```json
{
  "status": "ok",
  "map_version": "doxatlas-shortid-v1",
  "scope": {"ticker": "MU", "run_id": "<run-uuid>", "narrative_code": "N01", "event_code": "E01"},
  "sort_rule": "conviction_weight_desc_original_created_at_desc_id",
  "items": [
    {
      "social_code": "S01",
      "ai_summary": "...",
      "conviction_weight": 3.0,
      "social_sentiment": "bullish",
      "detail_available": true
    }
  ]
}
```

### 11. doxa_get_social_result_detail

用同一父级 scope + `Sxx` 数组拉取 social 细节。

Endpoint:

```text
POST /api/doxa-tools/get-social-result-detail
```

Input:

```json
{
  "run_id": "<run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "social_codes": ["S01"],
  "content_mode": "full",
  "preview_chars": 1200
}
```

`content_mode` 可选 `full|none`，默认 `full`；兼容旧的 `preview` 入参，但会规范化为 `full`，不返回 `content_preview`。

Output:

```json
{
  "status": "ok",
  "map_version": "doxatlas-shortid-v1",
  "scope": {"ticker": "MU", "run_id": "<run-uuid>", "narrative_code": "N01", "event_code": "E01"},
  "content_mode": "full",
  "items": [
    {
      "social_code": "S01",
      "source_name": "guba|stocktwits|reddit|...",
      "url": "...",
      "published_at": "...",
      "ai_summary": "...",
      "social_sentiment": "bullish",
      "conviction_weight": 3.0,
      "content_length": 820,
      "content": "..."
    }
  ]
}
```

### 12. doxa_get_event_source

查询 narrative event 直接绑定的新闻源材料。默认返回 `Dxx` + full content。

Endpoint:

```text
POST /api/doxa-tools/get-event-source
```

Input:

```json
{
  "run_id": "<run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "source_codes": ["D01"],
  "limit": 5,
  "content_mode": "full",
  "preview_chars": 1200
}
```

也支持 `narrative_event_id` 或 `narrative_id + event_code` scope。

`content_mode` 可选 `full|none`，默认 `full`；兼容旧的 `preview` 入参，但会规范化为 `full`，不返回 `content_preview`。

Output:

```json
{
  "status": "ok",
  "map_version": "doxatlas-shortid-v1",
  "scope": {"ticker": "MU", "run_id": "<run-uuid>", "narrative_code": "N01", "event_code": "E01"},
  "content_mode": "full",
  "narrative_event": "...",
  "items": [
    {
      "source_code": "D01",
      "title": "...",
      "summary": "...",
      "published_at": "...",
      "source_name": "...",
      "url": "...",
      "content_enrichment_status": "success",
      "source_grade": "B",
      "content_length": 6989,
      "content": "..."
    }
  ]
}
```

## 五、只读 Smoke 建议

部署后优先跑只读与 validation-only 检查，不触发 `doxa_run_*`：

```bash
curl -X POST "$BASE/get-narrative-report" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"MU","view":"agent_provenance"}'
```

验收：

- 返回 `run_ref.run_id`、`narratives[].narrative_code`、`narratives[].events[].event_code`。
- 默认不返回 child UUID、reasoning/COT、完整 `source_propositions`。
- 至少一个 event 带 `next.query_propositions`，且 input 使用 `run_id + narrative_code + event_code`。

继续用上一响应的 scope 验证：

```bash
curl -X POST "$BASE/query-propositions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"run_id":"<run_id>","narrative_code":"N01","event_code":"E01"}'
```

验收：

- 返回 `propositions[].proposition_code=Pxx`。
- 不返回整条 narrative 下的所有 propositions。

再验证 media/social 两层查询：

```bash
curl -X POST "$BASE/get-media-result" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"run_id":"<run_id>","narrative_code":"N01","event_code":"E01"}'

curl -X POST "$BASE/get-media-result-detail" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"run_id":"<run_id>","narrative_code":"N01","event_code":"E01","media_codes":["M01"],"content_mode":"full"}'
```

验收：

- 第一层只返回 `media_code` 和 compact `event`。
- detail 层默认返回 URL、source_name、content_length、content。

## 六、给 DoxAgent 的集成建议

1. DoxAgent 侧保存 `DOXATLAS_TOOL_SERVER_TOKEN`，不要把 token 暴露给浏览器端。
2. DoxAgent 调用前统一配置 `base_url = https://<your-doxatlas-domain>/api/doxa-tools`。
3. 首轮 narrative evidence 使用 `doxa_get_narrative_report({"ticker":"MU","view":"agent_provenance"})`。
4. 后续 proposition/media/social/source 查询使用 event-level scope，不要猜 UUID，不要把内部 `event_id` 当 DoxAtlas event ref。
5. 对 `INVALID_SCOPE` / `INVALID_ID_FORMAT` 视为非 retryable 参数错误，让 agent 修正调用，而不是网络重试。
6. detail/source 工具默认使用 `content_mode="full"` 返回正文；只做列表筛选时先停留在 compact list 工具，避免不必要地拉取全文。

## 七、已知未完成项

- 真实线上 `DOXATLAS_TOOL_SERVER_TOKEN` 轮换不是文档修改能完成的工作，需要在部署环境执行。
- DoxAgent 侧 tool descriptor / prompt 仍需同步本 contract brief，否则 agent 可能继续按旧字段构造请求。
- 当前 narrative/event-level ignored proposition relevance 是工具层即时计算；中期更稳妥的方案是持久化 `ignored_scope_links`。
