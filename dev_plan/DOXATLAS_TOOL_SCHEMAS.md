# DoxAtlas Tool Server API 与工具 Schema

本文档定义 `doxatlas_tools` 中 `doxa_` 工具通过 HTTP Tool Server 暴露给 DoxAgent 的远程调用契约。

这些工具是对 DoxAtlas 既有能力的薄封装：复用现有 Supabase 数据、Narrative Research 流程和单标的消息面分析流程，但不改变现有前后端产品管线。DoxAgent 应通过 DoxAtlas 云服务器上的 HTTP API 调用这些工具，而不是直接访问数据库或产品端接口。

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

如果云服务器已有反向代理将公网 HTTPS 请求转发到 backend，则通常只需要确保 `/api/doxa-tools/*` 路径也被转发到 backend 的 `8000` 端口。

### 2. 必填环境变量

在 DoxAtlas 云服务器的 backend 运行环境中配置：

```bash
DOXATLAS_TOOL_SERVER_TOKEN=L2KxDJ9gxPxu-hCw2QarlH03g5HoUWkwDOOGU-vJO3wlKMDJ6Mr2X5IA9yju7Q0j
DOXATLAS_TOOLS_USER_ID=44049c80-fe71-41e0-ab79-0bcf905eaedc
```

含义：

- `DOXATLAS_TOOL_SERVER_TOKEN`：DoxAgent 调用 Tool Server 时使用的静态 Bearer Token。
- `DOXATLAS_TOOLS_USER_ID`：Tool Server 绑定的固定 DoxAtlas 服务用户 UUID。

HTTP 请求体中不要传 `user_id`。Tool Server 会在后端自动注入 `DOXATLAS_TOOLS_USER_ID`，所有工具调用都绑定这个固定服务用户。

如果任一环境变量未配置，工具接口会返回：

```json
{
  "error": {
    "code": "TOOL_SERVER_NOT_CONFIGURED",
    "message": "DOXATLAS_TOOL_SERVER_TOKEN and DOXATLAS_TOOLS_USER_ID must be configured.",
    "details": null
  }
}
```

### 3. 部署与重启

在云服务器更新 `.env` 后，重新构建并启动 backend：

```bash
docker compose up -d --build backend
```

建议同时确认：

- `backend` 容器可以 import `doxatlas_tools`。
- 现有产品端 API 仍可访问。
- 反向代理已经覆盖 `/api/doxa-tools/*`。

### 4. 请求规范

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

### 5. 鉴权与 CORS

Tool Server 是 DoxAgent 到 DoxAtlas 的服务间 API，不面向浏览器前端开放。

- Bearer Token 使用 constant-time compare 校验。
- 不开启任意浏览器 CORS。
- 对 `/api/doxa-tools/*` 的 `OPTIONS` 预检请求会返回 `405`。

### 6. 统一错误格式

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
| `400` | `INVALID_PARAMS` | 工具逻辑发现参数不合法 |
| `500` | `TOOL_SERVER_NOT_CONFIGURED` | 服务端缺少必要环境变量 |
| `500` | `TOOL_EXECUTION_FAILED` | 工具执行时出现未预期异常 |

### 7. 调用示例

启动单标的消息面分析：

```bash
curl -X POST "https://doxatlas.com/api/doxa-tools/run-analysis" \
  -H "Authorization: Bearer <DOXATLAS_TOOL_SERVER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"TSLA","language":"en","reuse_recent":true}'
```

读取叙事研究报告：

```bash
curl -X POST "https://doxatlas.com/api/doxa-tools/get-narrative-report" \
  -H "Authorization: Bearer <DOXATLAS_TOOL_SERVER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"TSLA"}'
```

按 `narrative_event_id` 查询 proposition：

```bash
curl -X POST "https://doxatlas.com/api/doxa-tools/query-propositions" \
  -H "Authorization: Bearer <DOXATLAS_TOOL_SERVER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"narrative_event_id":"<narrative-event-id>"}'
```

## 二、通用约定

- 所有时间字段按 DoxAtlas 数据库存储格式返回，通常是 UTC ISO 字符串。
- 标的代码字段统一使用 `ticker`。
- ID 作用域查询类工具只允许传入 `narrative_event_id`、`narrative_id`、`proposition_id` 三者之一。
- `doxa_run_*` 类工具只返回当前任务或 run 的 id 与状态，不等待完整 pipeline 执行完毕。
- `doxa_get_analysis` 如不传 `task_id`，只会查找固定服务用户下该 `ticker` 最新 completed task；找不到时返回错误，不 fallback 到其他用户数据。
- `doxa_get_ignored_propositions` 使用已确认的第二方案：输入 `narrative_id` 后先解析其 `run_id`，再返回该 run 下全部 ignored propositions，并标记 `scope: "run_level_not_narrative_level"`。

## 三、Endpoint 总览

| 工具名 | HTTP Endpoint | 说明 |
| --- | --- | --- |
| `doxa_run_narrative_research` | `POST /api/doxa-tools/run-narrative-research` | 启动叙事研究 |
| `doxa_run_analysis` | `POST /api/doxa-tools/run-analysis` | 启动单标的消息面分析 |
| `doxa_get_narrative_report` | `POST /api/doxa-tools/get-narrative-report` | 读取叙事研究报告层明细 |
| `doxa_get_analysis` | `POST /api/doxa-tools/get-analysis` | 读取单标的消息面分析明细 |
| `doxa_query_propositions` | `POST /api/doxa-tools/query-propositions` | 查询 proposition 与 micro-merge 字段 |
| `doxa_get_ignored_propositions` | `POST /api/doxa-tools/get-ignored-propositions` | 查询 run-level ignored propositions |
| `doxa_get_social_result` | `POST /api/doxa-tools/get-social-result` | 查询高 conviction social summary |
| `doxa_get_media_result` | `POST /api/doxa-tools/get-media-result` | 查询 media event 字段 |
| `doxa_get_event_source` | `POST /api/doxa-tools/get-event-source` | 查询 narrative event 直接绑定的新闻源材料 |

## 四、工具 Schema

### 1. doxa_run_narrative_research

启动一个 Narrative Research run。

Endpoint：

```text
POST /api/doxa-tools/run-narrative-research
```

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "ticker": {
      "type": "string",
      "description": "标的代码。"
    },
    "language": {
      "type": "string",
      "default": "en",
      "description": "输出语言。"
    },
    "force": {
      "type": "boolean",
      "default": false,
      "description": "是否强制新建 run，而不是复用已有状态。"
    }
  },
  "required": ["ticker"],
  "additionalProperties": false
}
```

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string",
      "description": "Narrative Research run id。"
    },
    "status": {
      "type": "string",
      "description": "当前 run 状态。"
    }
  },
  "required": ["run_id", "status"]
}
```

### 2. doxa_run_analysis

启动单标的消息面分析 task。默认会复用同一服务用户、同一 ticker 在短时间内创建的近期 task。

Endpoint：

```text
POST /api/doxa-tools/run-analysis
```

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "ticker": {
      "type": "string",
      "description": "标的代码。"
    },
    "language": {
      "type": "string",
      "default": "en",
      "description": "输出语言。"
    },
    "reuse_recent": {
      "type": "boolean",
      "default": true,
      "description": "是否复用近期同标的 task。"
    }
  },
  "required": ["ticker"],
  "additionalProperties": false
}
```

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string",
      "description": "消息面分析 task id。"
    },
    "status": {
      "type": "string",
      "description": "当前 task 状态。"
    },
    "reused_existing": {
      "type": "boolean",
      "description": "是否复用了已有 task。"
    }
  },
  "required": ["task_id", "status", "reused_existing"]
}
```

### 3. doxa_get_narrative_report

读取 DoxAtlas 叙事研究报告层明细。输出应覆盖前端网页会展示的除图表之外的报告内容。

Endpoint：

```text
POST /api/doxa-tools/get-narrative-report
```

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "ticker": {
      "type": "string",
      "description": "标的代码。"
    },
    "run_id": {
      "type": ["string", "null"],
      "default": null,
      "description": "指定 Narrative Research run id；不传时由工具解析最新可用 run。"
    }
  },
  "required": ["ticker"],
  "additionalProperties": false
}
```

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "meta": {
      "type": "object",
      "description": "run、ticker、时间范围、状态等元信息。"
    },
    "kpi": {
      "type": "object",
      "description": "报告层 KPI。"
    },
    "source_weights": {
      "type": "object",
      "description": "信源权重或来源结构信息。"
    },
    "dominant_rank": {
      "type": "array",
      "items": {"type": "object"},
      "description": "主导性排名。"
    },
    "momentum_rank": {
      "type": "array",
      "items": {"type": "object"},
      "description": "动量排名。"
    },
    "narratives": {
      "type": "array",
      "items": {"type": "object"},
      "description": "叙事明细、叙事事件、解释、来源追踪等报告层内容。"
    }
  },
  "required": ["meta", "kpi", "source_weights", "dominant_rank", "momentum_rank", "narratives"]
}
```

### 4. doxa_get_analysis

读取 DoxAtlas 单标的消息面分析明细，包含 social 与 media 两条分析轨道。图表数据只返回 7d 时间桶。

信源胶囊按类似 LLM4 的筛选逻辑输出前若干篇，默认前 5 篇；social 侧按 `conviction_weight` 排序。

Endpoint：

```text
POST /api/doxa-tools/get-analysis
```

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "ticker": {
      "type": "string",
      "description": "标的代码。"
    },
    "task_id": {
      "type": ["string", "null"],
      "default": null,
      "description": "指定消息面分析 task id；不传时查找固定服务用户下该 ticker 最新 completed task。"
    },
    "capsule_limit": {
      "type": "integer",
      "default": 5,
      "minimum": 1,
      "maximum": 20,
      "description": "每个 proposition 附带的 source capsule 数量上限。"
    }
  },
  "required": ["ticker"],
  "additionalProperties": false
}
```

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "ticker": {
      "type": "string"
    },
    "task_id": {
      "type": ["string", "null"]
    },
    "timeframe": {
      "const": "7d"
    },
    "social": {
      "type": "object",
      "description": "social 轨道分析结果。"
    },
    "media": {
      "type": "object",
      "description": "media 轨道分析结果。"
    }
  },
  "required": ["ticker", "timeframe", "social", "media"]
}
```

`social` 与 `media` 轨道通常包含：

- `meta`
- `kpi`
- `sentiment_matrix`
- `chart_timeseries`
- `topic_atlas`
- `noise_summary`

其中 `topic_atlas.topics[].propositions[]` 会附带 `source_capsules`。

### 5. doxa_query_propositions

输入 `narrative_event_id`、`narrative_id` 或 `proposition_id` 之一，返回情绪及 micro-merge 生成的 proposition 字段。

Endpoint：

```text
POST /api/doxa-tools/query-propositions
```

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "narrative_event_id": {
      "type": ["string", "null"],
      "default": null
    },
    "narrative_id": {
      "type": ["string", "null"],
      "default": null
    },
    "proposition_id": {
      "type": ["string", "null"],
      "default": null
    }
  },
  "oneOf": [
    {"required": ["narrative_event_id"]},
    {"required": ["narrative_id"]},
    {"required": ["proposition_id"]}
  ],
  "additionalProperties": false
}
```

运行时校验要求三种 id 有且仅有一个为非空值。

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "scope": {
      "type": "string",
      "description": "本次查询的作用域类型。"
    },
    "scope_id": {
      "type": "string",
      "description": "本次查询的作用域 id。"
    },
    "proposition_ids": {
      "type": "array",
      "items": {"type": "string"}
    },
    "items": {
      "type": "array",
      "items": {"type": "object"}
    }
  },
  "required": ["scope", "scope_id", "proposition_ids", "items"]
}
```

`items` 通常包含：

- `proposition_id`
- `sentiment_direction`
- `proposition_summary`
- `combined_judgements`
- `core_event`
- `event_interpretation`
- `forward_judgement`
- `source_viewpoints`
- `source_pills`
- coherence 相关字段
- event time 相关字段

### 6. doxa_get_ignored_propositions

输入 `narrative_id`，返回该 narrative 所属 run 下全部 ignored propositions。

该接口采用已确认的第二方案：`narrative_id` 只用于解析 `run_id`，返回结果是 run-level，不是 narrative-level。

Endpoint：

```text
POST /api/doxa-tools/get-ignored-propositions
```

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "narrative_id": {
      "type": "string",
      "description": "用于解析 run_id 的 narrative id。"
    }
  },
  "required": ["narrative_id"],
  "additionalProperties": false
}
```

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "narrative_id": {
      "type": "string"
    },
    "run_id": {
      "type": "string"
    },
    "scope": {
      "const": "run_level_not_narrative_level"
    },
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "proposition_id": {"type": ["string", "null"]},
          "reason": {"type": ["string", "null"]},
          "core_event": {"type": ["string", "null"]},
          "event_interpretation": {"type": ["string", "null"]},
          "task_id": {"type": ["string", "null"]},
          "created_at": {"type": ["string", "null"]}
        }
      }
    }
  },
  "required": ["narrative_id", "run_id", "scope", "items"]
}
```

### 7. doxa_get_social_result

输入 `narrative_event_id`、`narrative_id` 或 `proposition_id` 之一，返回对应所有 task 的 `analysis_results.social` 中 `conviction_weight = 3` 的记录的 `ai_summary` 字段。

Endpoint：

```text
POST /api/doxa-tools/get-social-result
```

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "narrative_event_id": {
      "type": ["string", "null"],
      "default": null
    },
    "narrative_id": {
      "type": ["string", "null"],
      "default": null
    },
    "proposition_id": {
      "type": ["string", "null"],
      "default": null
    }
  },
  "oneOf": [
    {"required": ["narrative_event_id"]},
    {"required": ["narrative_id"]},
    {"required": ["proposition_id"]}
  ],
  "additionalProperties": false
}
```

运行时校验要求三种 id 有且仅有一个为非空值。

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "scope": {
      "type": "string"
    },
    "scope_id": {
      "type": "string"
    },
    "proposition_ids": {
      "type": "array",
      "items": {"type": "string"}
    },
    "items": {
      "type": "array",
      "items": {"type": "object"}
    }
  },
  "required": ["scope", "scope_id", "proposition_ids", "items"]
}
```

`items` 通常包含：

- `analysis_result_id`
- `task_id`
- `original_id`
- `ai_summary`
- `conviction_weight`
- `original_created_at`

### 8. doxa_get_media_result

输入 `narrative_event_id`、`narrative_id` 或 `proposition_id` 之一，返回对应所有 task 的 `analysis_results.media` 的 `event` 字段。

Endpoint：

```text
POST /api/doxa-tools/get-media-result
```

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "narrative_event_id": {
      "type": ["string", "null"],
      "default": null
    },
    "narrative_id": {
      "type": ["string", "null"],
      "default": null
    },
    "proposition_id": {
      "type": ["string", "null"],
      "default": null
    }
  },
  "oneOf": [
    {"required": ["narrative_event_id"]},
    {"required": ["narrative_id"]},
    {"required": ["proposition_id"]}
  ],
  "additionalProperties": false
}
```

运行时校验要求三种 id 有且仅有一个为非空值。

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "scope": {
      "type": "string"
    },
    "scope_id": {
      "type": "string"
    },
    "proposition_ids": {
      "type": "array",
      "items": {"type": "string"}
    },
    "items": {
      "type": "array",
      "items": {"type": "object"}
    }
  },
  "required": ["scope", "scope_id", "proposition_ids", "items"]
}
```

`items` 通常包含：

- `analysis_result_id`
- `task_id`
- `original_id`
- `event`
- `original_created_at`

### 9. doxa_get_event_source

输入 `narrative_event_id`，返回该 narrative event 直接绑定的新闻报道正文、链接等字段。

排序逻辑接近 LLM4 的 direct-media material path。若该 `narrative_event_id` 没有绑定 media 记录，则直接返回空 `items`，不会 fallback 到 social，也不会 fuzzy-match 无关 media。

Endpoint：

```text
POST /api/doxa-tools/get-event-source
```

#### Input Schema

```json
{
  "type": "object",
  "properties": {
    "narrative_event_id": {
      "type": "string",
      "description": "Narrative event id。"
    },
    "limit": {
      "type": "integer",
      "default": 5,
      "minimum": 1,
      "maximum": 20,
      "description": "返回新闻报道数量上限。"
    }
  },
  "required": ["narrative_event_id"],
  "additionalProperties": false
}
```

#### Output Schema

```json
{
  "type": "object",
  "properties": {
    "narrative_event_id": {
      "type": "string"
    },
    "narrative_id": {
      "type": ["string", "null"]
    },
    "run_id": {
      "type": ["string", "null"]
    },
    "ticker": {
      "type": ["string", "null"]
    },
    "narrative_event": {
      "type": ["string", "null"]
    },
    "items": {
      "type": "array",
      "items": {"type": "object"}
    }
  },
  "required": ["narrative_event_id", "items"]
}
```

`items` 通常包含：

- `id`
- `title`
- `summary`
- `content`
- `published_at`
- `source_name`
- `url`
- `content_enrichment_status`
- `grade`

## 五、给 DoxAgent 的集成建议

1. DoxAgent 侧保存 `DOXATLAS_TOOL_SERVER_TOKEN`，不要把 token 暴露给浏览器端。
2. DoxAgent 调用前统一配置 `base_url = https://<your-doxatlas-domain>/api/doxa-tools`。
3. 对 `doxa_run_*` 接口采用异步轮询策略：先拿到 `run_id` 或 `task_id`，后续再调用读取类工具获取结果。
4. 对 id 作用域工具强制做本地参数校验，确保 `narrative_event_id`、`narrative_id`、`proposition_id` 三者只传一个。
5. 对 `500 TOOL_EXECUTION_FAILED` 做重试前判断：若是暂时性数据库或网络错误可重试；若是数据缺失、run 未完成或 task 未完成，应延迟后再查。

