# DoxAtlas Tools Agent Contract 排查与修改方案

排查日期：2026-06-20  
排查对象：DoxAgent 真实初始化 workflow 中的 DoxAtlas tool 调用失败，以及服务器 `/root/doxatlas` 上 DoxAtlas Tool Server 当前实现。  
本次动作：只读排查与方案设计，未修改远端 DoxAtlas 代码，未触发 `doxa_run_*` 这类会启动新分析任务的接口。

## 1. 核心结论

DoxAtlas 工具在真实 agent workflow 中暴露的问题，不是单纯的“上游没数据”，而是 agent contract 没有被设计成可连续调用的证据图谱：

1. 顶层 `doxa_get_narrative_report` 能返回 narrative、event、proposition 等底层 ID，但返回体缺少面向 agent 的短 id 映射、下一跳工具提示和字段语义说明。agent 很容易只消费叙事摘要，后续 A1 审计再追底层证据时找不到稳定路径。
2. 低层工具直接要求 `narrative_id` / `narrative_event_id` / `proposition_id` UUID，但真实 workflow 里 agent 会传 ticker、自己生成的 expectation `event_id`、或叙事文本附近的业务 ID，DoxAtlas 没有 scoped short id 解析层，导致 Supabase UUID 查询报 `22P02`。
3. Tool Server 把 Supabase `22P02` 这类参数错误包装成 `500 TOOL_EXECUTION_FAILED`，DoxAgent 侧会把它视为可能 retryable 的上游失败，而不是立即可修正的 schema/ID 错误。
4. `doxa_get_media_result` / `doxa_get_social_result` 第一层返回字段不能变厚，否则 payload 会失控；正确方向是第一层只返回短代码 + 底层聚合字段摘要，第二层 detail endpoint 再按短代码数组拉 agent 需要的记录细节。
5. DoxAgent 当前 `ToolDescriptor` 只给模型 `input_fields` 与一句 `business_purpose`，schema 说明确实应进入 ReAct prompt，但必须是极短 contract brief，不能把完整远端 Markdown schema 塞进上下文。
6. 远端 `doxatlas_tools/TOOL_SCHEMAS.md` 当前含真实 Tool Server token。该问题与 agent contract 质量不同，但属于必须一起处理的 P0 安全风险：文档应脱敏，token 应轮换。

## 2. 证据摘要

### 2.1 LangSmith 真实 run 中的失败集中点

上次审计报告显示，DoxAtlas 工具失败集中在 A1/O1 节点：

- `doxa_get_event_source`：3 次调用 3 次失败，典型原因是把 `event_...` 传给 UUID 字段，Postgres 返回 `invalid input syntax for type uuid`。
- `doxa_get_ignored_propositions`：2 次调用 2 次失败，典型原因是 `narrative_id` 传了 `MU`，或传了不存在/不可解析的 UUID。
- `doxa_query_propositions`：出现 `narrative_id not found`。
- `doxa_get_narrative_report`：大多数成功，但有 SSL EOF；更重要的是成功后仍常只形成顶层 narrative evidence，没能把底层 proposition/media/social/event source 可靠带入 expectation patch。

这些失败的直接影响是：A1 的 DoxAtlas provenance audit 无法落到命题级、事件源级和 ignored proposition 级证据，只能给 `pass_with_warnings` / `needs_revision`，最终把文档 2 promotion blocker 后移到 `ReviewExpectationFields` 和 resolver。

### 2.2 DoxAgent 侧封装现状

本地 DoxAgent 的 DoxAtlas provider 位于 `src/doxagent/tools/providers/doxatlas.py`：

- `DOXATLAS_TOOL_SPECS` 仅声明 endpoint、allowed fields、required fields、是否 single scope id。
- 低层工具目前允许字段为 `narrative_event_id` / `narrative_id` / `proposition_id`，但没有给 agent 一个稳定的短代码映射层，也没有把“父级 scope + 子级 code”的下一跳调用方式放进 descriptor。
- provider 成功时统一输出 `{"provider": "doxatlas", "data": raw}`，没有质量状态、字段路径提示或 next tool hints。
- `ToolDescriptor` 只有 `name`、`description`、`input_fields`、`business_purpose` 等浅字段；ReAct prompt 将这些 descriptor 原样塞进 `available_tools`，agent 看不到完整 JSON schema 和例子。

这解释了“agent 构建请求体时不知道正确 schema”的部分根因：schema 存在于远端文档，但不在 agent 当前可见上下文里。

### 2.3 远端 DoxAtlas Tool Server 实现

服务器路径：`/root/doxatlas`，当前分支 `main`，最近提交 `e4c90a3`。Tool Server 已挂载，backend container 正在运行，`8000` 端口公开。

关键实现：

- `backend/main.py` 挂载 `tool_router`。
- `backend/tool_server.py` 定义 `/api/doxa-tools/*` 路由。
- `doxatlas_tools/tools.py` 实现具体 `doxa_` 工具。
- `backend/services/narrative_research/repository.py` 保存 narrative/event/proposition 关系。

当前请求模型的问题：

- `QueryScopeRequest` 只允许 `narrative_event_id`、`narrative_id`、`proposition_id` 三选一。
- `GetIgnoredPropositionsRequest` 只允许 `narrative_id`。
- `GetEventSourceRequest` 只允许 `narrative_event_id`。
- 这些字段都是 `str`，没有先做 UUID 格式校验，也没有 ticker/run/title/text 到 UUID 的解析。

当前工具函数的问题：

- `_fetch_narrative_event()` 直接执行 `db.table("narrative_events").eq("narrative_event_id", narrative_event_id)`。
- `_fetch_narrative()` 直接执行 `db.table("narratives").eq("narrative_id", narrative_id)`。
- `_resolve_proposition_ids()` 依赖上述两个函数，无法处理 ticker、run_id、叙事标题、事件文本或 agent 自己生成的 event id。
- `doxa_get_narrative_report()` 返回 full-ish report，但没有额外构造 agent-friendly 的 provenance map。

### 2.4 只读线上 smoke 结果

在服务器上用本地 `127.0.0.1:8000/api/doxa-tools` 做了只读 smoke，未调用 run 类接口。

`doxa_get_narrative_report({"ticker": "MU"})` 可返回：

- `meta.run_id = f4f06b3a-48b1-401e-ab66-750c50e2f4c9`
- `narratives` 共 8 条。
- 第一条 narrative 有真实 `narrative_id = d580ee30-a0c8-401a-8912-4ce18ab105eb`。
- 第一条 narrative 下有 8 个 `narrative_events`，第一条 event 有真实 `narrative_event_id = 0bd57717-0ac8-412c-99df-b1dc45d7b156`。
- event 带有 `source_proposition_ids`、`source_viewpoints`、`display_source` 和 `event_overview`。

用真实 UUID 调低层工具是成功的，这是当前实现证据，不代表目标 agent contract 应继续依赖这些 child UUID：

- `query-propositions` by narrative id：返回 42 个 proposition。该能力会造成过宽证据池，目标方案中应从 agent-facing contract 移除。
- `get-event-source` by narrative event id：返回 media source，第一条有 URL、source name、published time、content enrichment status、grade、约 6989 字正文。
- `query-propositions` by proposition id：返回 1 条 proposition。

用真实 workflow 中常见的错误入参会失败：

- `get-ignored-propositions({"narrative_id": "MU"})` 返回 `TOOL_EXECUTION_FAILED`，message 中含 Supabase `22P02 invalid input syntax for type uuid`。
- `get-event-source({"narrative_event_id": "event_23f"})` 同样返回 `TOOL_EXECUTION_FAILED` + `22P02`。

这说明数据本身存在，底层工具也能查到；失败集中在 contract：agent 没有稳定拿到/复用正确 UUID，DoxAtlas 也没有容错解析和更好的错误分类。

## 3. 失败点逐项归因

### 3.1 `doxa_get_narrative_report` 成功但输出对 agent 不够可操作

当前返回体保留了大量 dashboard/report 字段，适合前端渲染，但不适合 agent 做后续工具调用。

具体问题：

- `narratives[].source_proposition_ids` 可能很长，agent 不知道优先查哪些，也不应直接把这些 raw UUID 当作工作记忆。
- `narratives[].narrative_events[]` 中虽然有 `narrative_event_id`，但字段埋在大 payload 内，后续 context compaction 后容易丢失；目标 contract 应返回 `Nxx/Exx/Pxx` 这类 scoped short code。
- 没有 `next_tool_calls` / `tool_hints` 告诉 agent：“若要查该 event 的 propositions，请调用什么工具、传 `run_id+narrative_code+event_code` 还是 `narrative_id+event_code`”。
- 没有 compact provenance index，导致 O1 常只引用顶层 narrative report evidence，A1 后续要查 proposition/event/source 时重新猜 ID 或猜 UUID。

### 3.2 `doxa_get_event_source` 缺少 ID 解析与友好失败

当前接口只接受真实 `narrative_event_id` UUID。真实 workflow 里 A1/O1 容易传入 expectation patch 内部的 `event_id`，例如 `event_...`。

这类入参本应在工具层被识别为“不是 DoxAtlas scoped event ref”，返回非 retryable 的 `INVALID_PARAMS`，并提示应先从 `doxa_get_narrative_report(view="agent_provenance")` 取得 `run_id+narrative_code+event_code` 或 `narrative_id+event_code`；现在会穿透到 Supabase，变成 500。

### 3.3 `doxa_get_ignored_propositions` 的层级语义需要重做

当前接口文档说 `narrative_id` 只是用于解析 `run_id`，实际返回 run-level ignored propositions。这一层解析思路不正确：输入什么层级的 id，就应该返回什么层级的数据，而不是统一降到 run-level。

正确业务语义应为：

- A1 审 O1 是否正确输出了几个正确的 expectation units，看的是 run-level 数据。此时输入 run scope，返回该 run 下所有 ignored/contradicted proposition 摘要。
- A1 审 O1 的 expectation detail 是否写对、写全面，看的是 narrative-level 数据。此时输入 narrative scope，返回与该 narrative 相关的 ignored/contradicted proposition、遗漏事件或弱证据。
- 如果后续需要审某个 realized fact 对应的具体事件，则可以进一步输入 event scope，返回 event-level 相关 ignored/contradicted proposition。

如果底层表目前只保存 run-level ignored rows，则需要在工具层临时计算 narrative/event relevance：先按 run 取 ignored rows，再用 `topic_id`、`parent_event_cluster_id`、`core_event_id`、source type、方向、proposition text similarity 等字段与目标 narrative/event 的 source propositions 做匹配。中期更好的做法是在 Narrative Research 保存阶段持久化 `ignored_scope_links`，明确 ignored proposition 与候选 narrative/event 的关联。

### 3.4 `doxa_query_propositions` 不应再支持 narrative-level 查询

当前 `doxa_query_propositions` 支持 `narrative_id`，这会鼓励 agent 一次拉取整条 narrative 下几十个 propositions。真实 MU 样本中第一条 narrative 下有 42 个 propositions，单次返回约 94 KB；如果多个 narrative 反复查，既浪费上下文，也容易让 A1 在过宽证据池里漂移。

新的查询粒度应至少收敛到 event-level：

- 可接受：`narrative_event_id`，或 `narrative_id + event_code`，或 `run_id + narrative_code + event_code`。
- 可选：在 event scope 下再传 `proposition_codes`，只取 `P01/P03` 这类短代码对应的明细。
- 不再暴露给 agent：直接用 `narrative_id` 查询全部 propositions。

narrative-level 的 proposition 概览应由 `doxa_get_narrative_report(view="agent_provenance")` 提供轻量计数、top summaries 和 event 分组；真正查 proposition 明细时必须落到 event。

### 3.5 `doxa_get_media_result` / `doxa_get_social_result` 返回字段过薄

当前 `doxa_get_media_result` 主要返回 `analysis_result_id`、`task_id`、`original_id`、`event`、`original_created_at`。其中 `event` 是非常底层的舆情聚合字段，保留它是有价值的；但 `analysis_result_id`、`task_id`、`original_id` 对 agent 来说是数据库实现细节，不应作为 agent-facing identity 继续暴露。

`doxa_get_social_result` 同理：`ai_summary` 是底层聚合摘要，能帮助 A1 避免遗漏有价值信息；但身份字段应改为短代码，而不是原始 UUID。

因此问题不是“第一层字段必须补厚”，而是需要两层 endpoint：

- 第一层 `doxa_get_media_result` / `doxa_get_social_result`：返回短代码 `M01/S01` + `event/ai_summary` + 极少量排序/方向字段，确保不漏低层信息。
- 第二层 `doxa_get_media_result_detail` / `doxa_get_social_result_detail`：agent 用它第一次查询时的父级 scope + `{短代码数组}` 拉取 URL、source quality、正文 preview、raw source metadata 等细节。

这样第一层可以保持小 payload，第二层又能在需要时给足审计证据。

### 3.6 错误包装使 agent 重试错误方向

DoxAtlas 当前 `_dispatch()` 只特殊处理 `ValueError`，其他异常一律 `500 TOOL_EXECUTION_FAILED`。

Supabase/PostgREST 的 `22P02` 是输入类型错误，应被映射为：

```json
{
  "error": {
    "code": "INVALID_ID_FORMAT",
    "message": "event scope must be a DoxAtlas scoped event ref, such as run_id+narrative_code+event_code.",
    "details": {
      "field": "event_scope",
      "received": "event_23f",
      "expected_source": "doxa_get_narrative_report(view=agent_provenance).narratives[].events[].next",
      "retryable": false
    }
  }
}
```

这样 DoxAgent 不会把参数错误当成网络波动反复 retry。

### 3.7 文档与 agent runtime 脱节

远端 `doxatlas_tools/TOOL_SCHEMAS.md` 写了相对完整的 schema，但 DoxAgent 的 runtime 不读取该文档。agent 只看到 descriptor 的字段名数组。

修改方向不是继续扩写 Markdown，而是让极短 machine-readable contract 随工具 descriptor 进入 ReAct prompt。这里不能把完整 schema、长 usage examples 和大量 common mistakes 塞进去，否则又会制造上下文压力。

每个 DoxAtlas tool descriptor 只需要说明：

- 功能：这个 endpoint 解决什么任务。
- 必填入参：最小字段集合。
- ID 规则：接受哪个父级 UUID/短代码组合。
- 返回：最关键的 2-4 个字段。
- 禁用：最容易传错的一句话，例如“不要把 DoxAgent 内部 `event_id` 当 DoxAtlas event code”。

### 3.8 安全风险：schema 文档硬编码真实 token

`/root/doxatlas/doxatlas_tools/TOOL_SCHEMAS.md` 中的环境变量示例含真实 token。该 token 还存在 `.env` 是正常部署需要，但文档中不应出现真实值。

处理要求：

- 立即把文档改成占位符。
- 轮换 Tool Server token。
- 检查 git 历史和远程仓库是否已提交该 token；如果已提交，按泄露处理。

## 4. 修改方案

### 方案边界：哪些是已核实现状，哪些是目标契约草案

本轮二次复核只确认了本地 DoxAgent provider 的当前封装现状：`doxa_get_narrative_report` 只允许 `ticker/run_id`，`doxa_get_analysis` 只允许 `ticker/task_id/capsule_limit`，`doxa_query_propositions` / media / social 仍使用 `narrative_event_id`、`narrative_id`、`proposition_id` 这类 raw UUID scope。远端 DoxAtlas 新 endpoint 和新 view 尚未在本轮重新 SSH 实测，因此下文的新增字段不应被理解为“当前真实 schema 已支持”，而是 agent-facing contract 的目标草案。

为避免误导，后续 DoxAtlas 代码实现时必须以服务端实际模型和测试为准：本文只规定必要语义、ID 规则和 payload 形态，不强行规定完整 JSON schema。凡是示例 JSON，只代表最小字段方向，不代表穷尽字段列表。

### P0-1：建立 scoped short id contract

核心原则：数据库和 DoxAtlas 内部仍保留 UUID，但 agent-facing contract 不应把 run/narrative/event/proposition/task/record 都暴露成裸 UUID。对 agent 来说，子对象用短代码表示；短代码只在父级 scope 内有意义。

#### 短代码格式

短代码采用“类型前缀 + 2 位 base36 序号”，兼顾短、可读、可区分类型：

| 层级 | 前缀 | 示例 | 父级 scope |
|---|---|---|---|
| narrative run | `R` | `R01` | `ticker` |
| analysis task | `T` | `T01` | `ticker` |
| narrative | `N` | `N01` | `run_id` |
| narrative event | `E` | `E01` | `narrative_id` 或 `run_id + narrative_code` |
| proposition | `P` | `P01` | `narrative_event_id` 或 event ref |
| analysis topic | `Q` | `Q01` | `task_code` |
| media result | `M` | `M01` | 调用 `doxa_get_media_result` 时的同一个 scope |
| social result | `S` | `S01` | 调用 `doxa_get_social_result` 时的同一个 scope |
| event source document | `D` | `D01` | `narrative_event_id` 或 event ref |
| ignored proposition | `I` | `I01` | run/narrative/event scope |

2 位 base36 可覆盖 `01..0Z..10..ZZ`，单类型单父级最多 1295 个对象；对当前 narrative/event/proposition 规模足够。若超过上限，工具应返回 `SHORT_ID_OVERFLOW`，要求 agent 缩小 scope，而不是生成更长、不可控的 code。

#### 生成规则

短代码必须由服务端确定性生成，不依赖当前 response 的 `limit`：

- run `Rxx`：按 `created_at desc, run_id` 在同一 ticker 下排序。注意 `R01` 会随新 run 出现而变化，因此只适合作为“最新/候选 run 列表”里的短引用；进入后续证据查询后仍应以选中的 `run_id` 作为父级锚点。
- task `Txx`：按 `completed_at desc, task_id` 在同一 ticker 下排序；response 同时给出该 task 的时间周期，供 agent 选择。
- analysis topic `Qxx`：按 topic relevance/volume desc，再按 topic id 排序。
- narrative `Nxx`：按 `combined_sov desc, narrative_id` 排序。
- event `Exx`：按 `event_time asc, created_at asc, narrative_event_id` 排序。
- proposition `Pxx`：按 event 的 `source_proposition_ids` 原始顺序排序；如果不是 event scope，则按 `created_at asc, proposition_id`。
- media/source `Mxx/Dxx`：按 source grade、content enrichment status、服务端内部时间戳 desc、raw id 排序；第一层 list 不暴露时间字段。
- social `Sxx`：按 conviction_weight desc、服务端内部时间戳 desc、raw id 排序；第一层 list 不暴露时间字段。
- ignored `Ixx`：按 relevance score desc、created_at desc、proposition_id 排序。

#### 输出规则

短代码格式不是要求 data API 把每个原始 UUID 都替换成 `{父级 scope + 短代码}` 的长对象。那样会比 raw UUID 更耗 token。正确形态是：返回体在顶层或当前分组中明确一次父级 scope，例如 `ticker`、`run_ref`、`task_ref`、`narrative_code`、`event_code`；其后子列表只使用短代码。

例如 narrative report 的 `run_ref` 已说明当前 run，下面的 narratives 只需要 `N01/N02`；某个 narrative/event 分组已说明 `N01/E01` 后，子级 proposition/media/social 只需要 `Pxx/Mxx/Sxx`。所有列表响应可返回 `map_version` 和 `sort_rule`，用于说明短代码生成版本和排序规则：

```json
{
  "map_version": "doxatlas-shortid-v1",
  "sort_rule": "event_time_asc_created_at_asc_id",
  "items": [
    {"event_code": "E01", "event": "存储主题ETF推出及板块资金涌入"}
  ]
}
```

#### 输入规则

agent 不再把子级 UUID 当作主要输入。新 contract 中所有多层对象都用“父级锚点 + 短代码”：

```json
{"run_id": "<full-run-uuid>", "narrative_code": "N01"}
{"narrative_id": "<full-narrative-uuid>", "event_code": "E02"}
{"run_id": "<full-run-uuid>", "narrative_code": "N01", "event_code": "E02"}
{"narrative_event_id": "<full-event-uuid>", "proposition_codes": ["P01", "P03"]}
{"narrative_event_id": "<full-event-uuid>", "media_codes": ["M01", "M04"]}
```

为了减少 agent 需要保存的 UUID 数量，所有 detail endpoint 都应同时支持“完整父级 UUID + 短代码”和“祖先 UUID + 多级短代码链”。例如 `doxa_query_propositions` 可接受：

- `{"narrative_event_id": "..."}`
- `{"narrative_id": "...", "event_code": "E01"}`
- `{"run_id": "...", "narrative_code": "N01", "event_code": "E01"}`

但不接受裸 `narrative_id` 查询 proposition。

### P0-2：重写 `doxa_get_narrative_report` 的 agent view

`doxa_get_narrative_report` 保留 full view 兼容旧调用，但 DoxAgent 默认使用 `view="agent_provenance"`。该 view 的目标不是展示 dashboard，而是给 agent 一个短 id 地图和下一跳工具入口。

输入方向：

```json
{
  "ticker": "MU",
  "run_id": null,
  "run_code": null,
  "view": "agent_provenance"
}
```

输出方向不是完整 schema，而是必须保留这些语义块：

- 顶层 `run_ref`：至少说明 `ticker`、`run_code`、`run_id`、生成时间或完成时间。这个父级 scope 只出现一次，后续 narrative 只用 `Nxx`。
- `narratives[]`：每条 narrative 至少有 `narrative_code`、标题、方向/情绪、核心 thesis，并保留当前 dashboard 中对 agent 有判断价值的 Narrative Explanation、Narrative Expectations、Upcoming Events 语义块。字段名以 DoxAtlas 当前实现为准，不在本文强行编造完整字段名。
- `narratives[].events[]`：保留 event code、事件文本/时间、overview、source/proposition 计数、display source 的短代码，以及下一跳工具所需的 `run_id + narrative_code + event_code`。
- `narrative_flow_summary`：替代 `dominant_rank` / `momentum_rank` 的 full narrative 复制。建议把 30d 分成 10 个均等节点，每个节点只返回该时间点的 narrative rank 摘要，例如 `{"node": 1, "offset_days": -30, "ranks": [{"narrative_code": "N01", "rank": 1, "sov": 0.31}]}`。

该 view 默认不返回：

- child UUID，例如 `narrative_id`、`narrative_event_id`、`proposition_id`
- `dominant_rank` / `momentum_rank` 的 full narrative 复制
- `source_propositions` 完整 dump
- `llm*_reasoning_content`、`*_cot`
- source 全文正文

如果 DoxAgent 后续确实需要完整 child UUID，可通过 detail endpoint 在 `debug_ids=true` 下返回，但生产 agent prompt 不应暴露这类字段。

### P0-3：新增或重构 `doxa_query_analysis`

现有 `doxa_get_analysis` 偏“按 task_id 或最新 task 取 dashboard payload”。agent 更常见的问题其实不需要复杂时间匹配：它先需要知道某个 ticker 当前有哪些可用 analysis task、各自对应什么时间周期，然后再用 `task_code` 继续查具体内容。

建议新增 endpoint：

```text
POST /api/doxa-tools/query-analysis
```

输入方向：

```json
{
  "ticker": "MU"
}
```

返回方向：

- 顶层明确 `ticker`。
- `analysis_tasks[]` 只返回 `task_code=Txx`、时间周期/窗口、完成时间、可用数据类型等 compact metadata。
- 具体 task 的 topic 可在后续 endpoint 中以 `task_code` 为父级 scope 返回 `Qxx`；`task_id` 继续作为 server 内部锚点，默认不进入 agent-facing 列表。

### P0-4：`doxa_query_propositions` 限制到 event-level

`doxa_query_propositions` 不再支持裸 `narrative_id`。它至少需要 event-level scope。

输入方向：

```json
{
  "run_id": "<full-run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "proposition_codes": null,
  "limit": 20
}
```

或：

```json
{
  "narrative_event_id": "<full-event-uuid>",
  "proposition_codes": ["P01", "P03"],
  "limit": 20
}
```

输出字段方向，非完整 schema：

```json
{
  "status": "ok",
  "scope": {"narrative_code": "N01", "event_code": "E01"},
  "propositions": [
    {
      "proposition_code": "P01",
      "source_type": "media",
      "sentiment_direction": "bullish",
      "summary": "...",
      "core_event": "...",
      "forward_judgement": "...",
      "source_pill_count": 7,
      "media_result_count": 5,
      "social_result_count": 0
    }
  ]
}
```

这样 A1 不会一次把整条 narrative 的几十个 proposition 都拉进上下文；如果需要 narrative-level 覆盖，它应先看 `agent_provenance` 中每个 event 的计数，再按 event 分批查。

### P0-5：重写 `doxa_get_ignored_propositions` 的层级语义

`doxa_get_ignored_propositions` 应按输入 scope 返回同层级结果。

输入方向可为三类：

```json
{"run_id": "<full-run-uuid>"}
{"run_id": "<full-run-uuid>", "narrative_code": "N01"}
{"run_id": "<full-run-uuid>", "narrative_code": "N01", "event_code": "E01"}
```

也可接受对应完整父级 UUID：

```json
{"narrative_id": "<full-narrative-uuid>"}
{"narrative_event_id": "<full-event-uuid>"}
```

输出字段方向，非完整 schema：

```json
{
  "status": "ok",
  "level": "run|narrative|event",
  "scope": {"run_id": "...", "narrative_code": "N01", "event_code": null},
  "items": [
    {
      "ignored_code": "I01",
      "reason": "...",
      "source_type": "media",
      "sentiment_direction": "bearish",
      "summary": "...",
      "relevance_reason": "same_topic_and_opposite_direction"
    }
  ]
}
```

实现注意：

- run-level：返回该 run 下全部 ignored propositions 的 compact list。
- narrative-level：返回与该 narrative 相关的 ignored propositions。若当前表没有 narrative linkage，可先用 topic/core_event/cluster/source_type/text similarity 做计算；更好的中期方案是在保存 narrative outputs 时持久化 `ignored_scope_links`。
- event-level：返回与该 event 相关的 ignored propositions，优先按 event 的 source propositions、core event、topic/cluster 计算。

这个逻辑比“传 narrative_id 但统一返回 run-level”更符合 A1 审查任务：construction 审查看 run-level，detail 审查看 narrative/event-level。

### P1-1：`doxa_get_media_result` / `doxa_get_social_result` 改为两层查询

第一层 endpoint 保持轻量，只返回短代码和底层聚合字段，不返回数据库内部 id。

`doxa_get_media_result` 输入方向：

```json
{
  "run_id": "<full-run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "proposition_codes": null,
  "limit": 20
}
```

输出字段方向，非完整 schema：

```json
{
  "status": "ok",
  "scope": {"narrative_code": "N01", "event_code": "E01"},
  "items": [
    {
      "media_code": "M01",
      "event": "Micron stock hit a new all-time high after earnings optimism.",
      "source_hint": "media",
      "detail_available": true
    }
  ]
}
```

`doxa_get_social_result` 输出字段方向同理：

```json
{
  "items": [
    {
      "social_code": "S01",
      "ai_summary": "Retail discussion links MU's HBM exposure to AI server demand.",
      "conviction_weight": 3.0,
      "detail_available": true
    }
  ]
}
```

第一层明确去掉：

- `analysis_result_id`
- `task_id`
- `original_id`
- raw UUID

这些字段只作为服务端解析短代码的内部数据。

### P1-2：新增 media/social detail endpoint

用户备注中第二个 endpoint 名称写成了 `doxa_get_social_result_detail` 两次；这里按语义理解为分别新增：

- `doxa_get_media_result_detail`
- `doxa_get_social_result_detail`

detail endpoint 必须支持“agent 第一次输入的同一个 scope + 短代码数组”：

```json
{
  "run_id": "<full-run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "media_codes": ["M01", "M03"],
  "content_mode": "preview",
  "preview_chars": 1200
}
```

输出字段方向，非完整 schema：

```json
{
  "status": "ok",
  "items": [
    {
      "media_code": "M01",
      "title": "...",
      "url": "...",
      "source_name": "...",
      "published_at": "...",
      "content_preview": "...",
      "content_length": 6989,
      "content_enrichment_status": "success",
      "source_grade": "B"
    }
  ]
}
```

social detail 字段方向：

```json
{
  "social_code": "S01",
  "source_name": "guba|stocktwits|reddit|...",
  "url": "...",
  "published_at": "...",
  "content_preview": "...",
  "content_length": 820,
  "social_sentiment": "bullish",
  "conviction_weight": 3.0
}
```

默认 `content_mode="preview"`；`full` 只在 agent 明确要求核验原文时使用。

### P1-3：payload 体积控制

#### 现象

只读测量线上 MU 的 `doxa_get_narrative_report({"ticker": "MU"})`，单次响应约 `2,371,214 bytes`，也就是约 2.37 MB。这个体积进入 ReAct observation 后，会直接推高 compaction 压力，并让后续 agent 更容易丢失关键 ID。

顶层字段体积如下：

| 字段 | 约字节数 | 说明 |
|---|---:|---|
| `dominant_rank` | 777 KB | 与 `narratives` 几乎同一批完整 narrative，只是排序用途 |
| `momentum_rank` | 777 KB | 与 `narratives` 同一批完整 narrative，只是动量排序 |
| `narratives` | 777 KB | 主体 narrative 明细 |
| `meta` | 39 KB | 主要来自 `kpi_json`、`llm1_reasoning_content`、`metadata` |
| `kpi` / `source_weights` | < 1 KB | 不是问题来源 |

因此第一层爆量不是 narrative 数量过多，而是同一批 full narrative 被 `dominant_rank`、`momentum_rank`、`narratives` 重复返回了三份。

#### 具体爆点

在 `narratives` 这一份 777 KB 内，主要大字段是：

| 层级/字段 | MU 样本约字节数 | 说明 |
|---|---:|---|
| `narratives[].narrative_events` | 545 KB | event 明细整体最大 |
| `narrative_events[].source_propositions` | 350 KB | 每个 event 内嵌完整 proposition dump，是 event 层最大字段 |
| `narratives[].llm2_reasoning_content` | 127 KB | LLM reasoning，对 agent 审计低价值且不应默认暴露 |
| `narratives[].llm3_reasoning_content` | 68 KB | 同上 |
| `meta.llm1_reasoning_content` | 14 KB | 同上 |
| `meta.kpi_json` | 17 KB | 与 `kpi` 存在语义重复，应按 view 控制 |

单个 event 的字段排序也显示，最大项通常是 `source_propositions`，例如第一条 event 的 `source_propositions` 约 5.8 KB，而 `event_overview` 只有约 551 bytes。也就是说 agent 需要的是 proposition 短代码、计数和少量摘要；当前接口却把 proposition 完整对象嵌进每个 event。

其他接口体积：

- `doxa_query_propositions` by first MU narrative：约 94 KB，主要来自 42 条 proposition 的 `items`，其中 `source_pills` 是主要字段之一。这个测量说明 narrative-level 查询会一次拉入过宽证据池；目标方案中应移除裸 `narrative_id` 查询，改为 event-level 分批，并支持 `limit` 与固定 view projection。
- `doxa_get_event_source(limit=5)`：约 23 KB，几乎全部来自 `items[].content` 正文。作为按需 source lookup 可接受，但不应被默认嵌入 narrative report。
- `doxa_get_media_result` / `doxa_get_social_result`：分别约 2.4 KB / 11 KB，不是主要爆点。
- `doxa_get_analysis` 在该 MU 服务用户样本下约 3.9 KB，不是当前问题来源；但若 topic atlas/source capsules 丰富，仍需要同样的 projection 策略。

#### 优化空间预估

基于 MU 线上样本做本地重组估算：

| 方案 | 估算字节数 | 相对当前减少 |
|---|---:|---:|
| 当前原始响应 | 2,371 KB | 0% |
| 只保留一份 full `narratives`，rank 列表不再复制全文 | 817 KB | 65.5% |
| rank 列表改 summary，同时保留 full `narratives` | 783 KB | 67.0% |
| 去重后再移除 reasoning/COT 字段 | 598 KB | 74.8% |
| 再移除 event 内嵌完整 `source_propositions`，保留 count/codes | 249 KB | 89.5% |
| 新增 `agent_provenance` view，去掉 rank 全量复制、reasoning/COT 和 event 内嵌完整 `source_propositions`，但保留完整 narrative 语义块 | 待重新实测 | 预计为主要优化路径 |

这说明最有效的优化不是压缩 HTTP，而是改变返回结构：默认 read 接口不要把 dashboard 视图、rank 视图、完整 event/proposition dump 和 reasoning 全部同时给 agent。

#### DoxAtlas 侧修改建议

1. `doxa_get_narrative_report` 增加 agent view 参数：

```json
{
  "ticker": "MU",
  "run_id": null,
  "run_code": null,
  "view": "agent_provenance|full|dashboard_summary",
  "include_reasoning": false,
  "include_source_propositions": false
}
```

2. 默认给 DoxAgent 使用 `view="agent_provenance"`，只返回：

- `run_ref` 中的 `ticker/run_code/run_id/status/window/source_timeframe/created_at/completed_at`
- narrative 的 `narrative_code/title/direction/brief_thesis/metrics/event_count/source_proposition_count`
- Narrative Explanation、Narrative Expectations、Upcoming Events 等 narrative-level 语义块
- event 的 `event_code/event_time/event/overview/display_source/source_proposition_count/source_viewpoint_count`
- 每个 event 的 `next`，给出下一跳工具应传的 `run_id + narrative_code + event_code`

3. `dominant_rank` / `momentum_rank` 不再复制 full narrative，也不再用 narrative UUID 作为默认身份。改成 Narrative Flow 的 summary 形式：30d 切成 10 个均等节点，每个节点只列当时的 narrative rank。

```json
{
  "narrative_flow_summary": [
    {"node": 1, "offset_days": -30, "ranks": [{"narrative_code": "N01", "rank": 1, "sov": 0.31}]},
    {"node": 10, "offset_days": 0, "ranks": [{"narrative_code": "N03", "rank": 1, "sov": 0.28}]}
  ]
}
```

4. `source_propositions` 不默认嵌入 event。event 只保留短代码、计数和 top summaries：

```json
{
  "source_proposition_codes": ["P01", "P02", "P03"],
  "source_proposition_count": 3,
  "top_source_proposition_summaries": [
    {
      "proposition_code": "P01",
      "source_type": "media",
      "sentiment_direction": "bullish",
      "proposition_summary": "..."
    }
  ]
}
```

完整 proposition 由 `doxa_query_propositions` 按 event scope 和 `proposition_codes` 按需查。

5. reasoning/COT 字段默认不返回：

- `llm1_reasoning_content`
- `llm2_reasoning_content`
- `llm3_reasoning_content`
- `grouping_cot`
- `explanation_cot`
- `narrative_event_cot`

如果内部 debug 需要，必须显式传 `include_reasoning=true`，并且 DoxAgent production profile 不允许开启。

6. `get-event-source` 增加短 id scope 和 `content_mode`：

```json
{
  "run_id": "<full-run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "source_codes": ["D01"],
  "limit": 5,
  "content_mode": "preview|full|none",
  "preview_chars": 1200
}
```

默认 `preview`，返回 `source_code`、`title`、`url`、`content_preview`、`content_length`，不直接返回全文。只有 agent 明确需要核验原文时，再用 `full` 拉正文。

7. 给所有 list 型工具加 `limit`，但不要暴露任意 `fields` 参数给 agent。字段投影应由 endpoint 的固定 `view` 控制，避免 agent 构造过长或不稳定 schema。必要时只支持小枚举：

```json
{
  "run_id": "<full-run-uuid>",
  "narrative_code": "N01",
  "event_code": "E01",
  "limit": 20,
  "view": "compact|detail"
}
```

字段白名单由服务端固定，不能允许任意 SQL select。

#### DoxAgent 侧修改建议

1. DoxAgent 的 `doxa_get_narrative_report` descriptor 默认示例改为短 id agent view：

```json
{
  "ticker": "MU",
  "view": "agent_provenance"
}
```

2. DoxAgent provider 对 DoxAtlas 大 payload 做二次保护：

- 暂不增加固定 payload 大小硬门槛，避免在 DoxAtlas view 尚未稳定前引入二次截断误伤。
- 先把默认调用路径改为 `view="agent_provenance"`，并在 provider metadata 中记录 `payload_bytes`，用于后续观察是否仍需要 DoxAgent 侧压缩。
- 对 `get-event-source(content_mode="full")` 或 media/social detail 的全文结果，可保留 preview 化策略；全文只在下游明确要求核验原文时进入 observation。

3. A1/O1 prompt 中明确：

- 首轮 DoxAtlas 调用使用 `agent_provenance`。
- 不要要求 full narrative report。
- 查 proposition 必须使用 event-level scope。
- 查 media/social 先调用列表 endpoint 拿 `Mxx/Sxx`，再按短代码数组调用 detail endpoint。
- 如需全文原文，只对单个 event/source 调 `get-event-source(content_mode="full")` 或 media/social detail 的 `content_mode="full"`。

## 5. 测试计划

### DoxAtlas 单元测试

新增：

- `backend/tests/test_tool_server_contract.py`
- `backend/tests/test_doxatlas_tools_agent_contract.py`

覆盖：

1. `get-narrative-report(view="agent_provenance")` 输出 `run_ref.run_code`、`narrative_code=Nxx`、`event_code=Exx`，且默认不返回 child UUID、reasoning/COT、完整 `source_propositions`。
2. 短代码生成稳定：相同 run/narrative/event 在不同 `limit` 下得到相同 `Nxx/Exx/Pxx/Mxx/Sxx`。
3. `query-analysis` 只需输入 `ticker`，即可返回可用 analysis task 的 `task_code=Txx`、时间周期/窗口和 compact metadata。
4. `query-propositions` 拒绝裸 `narrative_id`，返回 400 `INVALID_SCOPE`；使用 `run_id+narrative_code+event_code` 能返回 event-level propositions。
5. `get-ignored-propositions` 对 run/narrative/event 三种 scope 分别返回同层级 ignored items，不再把 narrative scope 统一降级为 run-level。
6. `get-media-result` / `get-social-result` 第一层只返回 `Mxx/Sxx`、`event/ai_summary` 和少量排序字段，不返回 `analysis_result_id/task_id/original_id/published_at`。
7. `get-media-result-detail` / `get-social-result-detail` 能用同一父级 scope + `Mxx/Sxx` 数组取回 URL、source quality、content preview 等细节。
8. Tool Server error envelope 不泄露 PostgREST 原始 dict 到 message；底层细节放到 `details.provider_error`。

### DoxAgent 回归测试

扩展：

- `tests/test_phase11_real_tools.py`
- `tests/test_phase16_react_harness.py`
- A1 workflow 相关 phase tests

覆盖：

1. DoxAtlas descriptors 包含极短 `contract_brief` 或等价 schema 摘要，说明必填字段、短 id 规则和禁止项，不引入长 Markdown schema。
2. DoxAgent provider 将 DoxAtlas `INVALID_ID_FORMAT` 映射为非 retryable failed ToolResult。
3. DoxAgent provider 将 DoxAtlas `status=partial/empty/not_found` 映射到可见 warnings/gaps。
4. A1 不再把 expectation 内部 `event_id` 直接传给 DoxAtlas；查 proposition 时必须使用 event-level scope。
5. A1 先用 `agent_provenance` 拿 `Nxx/Exx`，再按需调用 `query-propositions`、`get-media-result` / `get-social-result`、detail endpoints。
6. ReAct prompt 中的 DoxAtlas tool schema 摘要长度受控，避免因为 descriptor 变长造成上下文压力。

### 只读线上 smoke

部署后在服务器执行：

```bash
curl -X POST "$BASE/get-narrative-report" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"MU","view":"agent_provenance"}'
```

验收：

- 返回 `run_ref.run_code`、`narratives[].narrative_code`、`narratives[].events[].event_code`。
- 不默认返回 `narrative_id` / `narrative_event_id` / `source_propositions` / reasoning 字段。
- 至少一个 narrative event 带 `next.query_propositions`，其 input 使用 `run_id+narrative_code+event_code`。

再执行：

```bash
curl -X POST "$BASE/query-propositions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"run_id":"<run_id_from_previous_response>","narrative_code":"N01","event_code":"E01"}'
```

验收：

- 返回 `propositions[].proposition_code=Pxx`。
- 不返回整条 narrative 下所有 propositions。

再执行 media/social 两层查询：

```bash
curl -X POST "$BASE/get-media-result" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"run_id":"<run_id>","narrative_code":"N01","event_code":"E01"}'

curl -X POST "$BASE/get-media-result-detail" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"run_id":"<run_id>","narrative_code":"N01","event_code":"E01","media_codes":["M01"],"content_mode":"preview"}'
```

验收：

- 第一层只返回 `media_code` 和 compact `event`，不返回 raw id 或 `published_at`。
- detail 层返回 URL、source_name、content_preview、content_length。

## 6. 验收指标

1. DoxAtlas ID contract 类失败归零：不再出现 `event_...` 或 `MU` 导致的 Supabase 22P02。
2. DoxAgent agent-facing DoxAtlas payload 默认只暴露 scoped short ids；child UUID 不再作为普通列表项身份字段进入 prompt。
3. `doxa_query_propositions` 不再支持 narrative-level 查询；A1 按 event-level scope 获取 propositions。
4. `doxa_get_ignored_propositions` 按输入 scope 返回同层级数据，run-level 用于 expectation construction 审查，narrative/event-level 用于 expectation detail 审查。
5. media/social read 工具形成两层调用：第一层 compact code list，第二层按 `Mxx/Sxx` 数组拉细节。
6. `doxa_query_analysis` 能按 `ticker` 返回可用 analysis task 的短代码和时间周期，agent 不需要猜 `task_id`。
7. `doxa_get_narrative_report(view="agent_provenance")` 的 MU 样本响应应显著低于当前 2.37 MB；具体目标值在保留 Narrative Explanation / Expectations / Upcoming Events 后重新实测确定，full view 保留但不作为 DoxAgent 默认路径。
8. DoxAgent loop 中 DoxAtlas 工具失败不再被误判为 retryable 上游失败。
9. `TOOL_SCHEMAS.md` 不再含真实 token；新 token 已轮换。
