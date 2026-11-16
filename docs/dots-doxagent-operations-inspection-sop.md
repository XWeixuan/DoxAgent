# Dots · DoxAgent 运营巡检 SOP

目标：每轮做轻量巡检，只在发现**尚未通知的新事件**时，通过微信发送一条聚合通知。只使用 DoxAgent MCP。

## 1. 时间与跨轮记录

- 时区统一使用 `America/New_York`，自动处理夏令时。
- 每周从**周一 04:00 到周五 20:00**，连续每30分钟一轮，含周内夜间及周五20:00这一轮；窗口外不巡检。这不是每天04:00–20:00。
- 在 Dots 自己的持久化笔记/状态中，保存：上轮时间与统计日、已通知交易事件、未决交易Case、各告警是否仍存在、消息源上轮失败集合、当日收益是否已报、通知回执。MCP 的短期游标不是巡检状态。
- 首轮检查并通知当前已存在的阈值异常和当日交易；消息源先建立失败基线，第二轮才判断持续失败。

## 2. 每轮只读这两组数据

**概览：** 调用 `doxagent_get_overview`：

```json
{"section":"summary","period":"CURRENT_TRADING_DAY","limit":20}
```

**全部消息源：** 再调用同一工具：

```json
{"section":"sources","limit":20}
```

两组均读完 `data.has_more=true` 的续页：保持原参数并加入 `cursor=data.next_cursor`；不要跨轮复用游标。sources 不传 ticker、period、view_id。不要漏掉概览后续页的 ticker 或目录后续页的源。

读值约定：

- 全局指标：`data.metrics.data`；Live连接：`data.gateway.data.ib_gateway_live_status`；余额：`data.openrouter_balance.data.balance_usd`。
- ticker 行：`data.items[]`，名称在 `overview.state.ticker`，交易计数在 `overview.metrics`，运行指标在 `runtime.data`。
- 每个指标读取 `指标.current.value`，仅在 `current.state="AVAILABLE"` 时转成数字比较；`null/NOT_RECORDED` 不是0。模块不可用时不要当作正常。
- 指标统计日以返回的 `data.period.current.trading_days` 为准，不能在UTC或美东午夜盲目清零。若明确返回 `NON_TRADING_DAY`，可用 `PREVIOUS_TRADING_DAY` 概览检查连接和余额，但不把前日累计值当作今日；目录照常检查。

## 3. 检查清单

| 检查 | MCP 字段与判定 |
| --- | --- |
| 当日交易 | 全局及各ticker的 `trade_executed`（执行数）、`trade_triggered`（意图/触发数）。按第4节核对新增执行和未执行意图；两数不相等只是线索，不直接宣告阻塞。 |
| Live API | `ib_gateway_live_status="DISCONNECTED"` 时告警；`CONNECTED` 为正常。未知/模块失败按“无法核验”处理，不冒称断连。 |
| fallback余额 | `balance_usd < 5` 美元告警。 |
| Case判定失败 | 全局 `failed_cases.current.value > 10` 告警；不是累计失败尝试次数。 |
| 当日API消费 | 全局 `api_token_cost.current.value > 10` 美元告警；不要用 OpenRouter 余额变化推算消费。 |
| 各ticker一轮延时 | `runtime.data.hot_path_mean_seconds.current.value > 30` 秒告警。 |
| 各ticker二轮延时 | `runtime.data.w3_mean_seconds.current.value > 90` 秒告警。 |
| 各ticker长研判 | `runtime.data.long_w3_cases.current.value > 10` 个告警。它统计 W3 模型判定超过180秒的Case，不能把180秒当作本项告警数量阈值。 |
| 消息源持续失败 | 目录卡片 `enabled=true`、`delivery_mode="production"` 且有启用订阅；`poll_counts.failed > 0` 为本轮失败。按 `source_id` 对比连续两轮，首次进入持续失败才告警；原因取卡片 `errors`。`partial`、休眠、停用、测试源和未知不冒充失败。 |
| 可访问性/严重投影延迟 | 工具或关键模块报错导致无法巡检，记新“巡检数据不可访问”事件。检查 `data.projection` / `meta.projection`：`oldest_pending_at` 距今≥2小时且 `head>checkpoint`，或 `checked_at` 已停更≥2小时，记疑似服务器/投影健康问题。不要仅因 `freshness=STALE`、历史gap存在或安静源的 `source_event_at` 很早就告警。 |

一轮/二轮平均值已由后端排除超过10分钟的Case；二轮只计 W3 `trade_decision` 模型判定，不含 Fork/Operations。长研判数仍含超过10分钟者。直接使用这些值，不重算或把缺失锚点补成0。未列出的指标无需主动排查。

## 4. 仅对交易线索查看 Case

对当日有交易计数的ticker，以及状态中尚未解决的交易Case：

1. 用 `doxagent_list_cases` 分别查 `result="TRADE_EXECUTION"` 和 `result="TRADE_INTENT"`，参数为该 `ticker`、`period="CURRENT_TRADING_DAY"`、`limit=20`。读必要续页并按 `case_id` 合并；不能只比较总计数，旧Case可能本轮才开始执行。
2. 只对新增交易Case、执行状态发生变化的Case及未决Case调用 `doxagent_get_case_detail`，传 `ticker/case_id`、对应列表的 `meta.view_id`，先选 `part="executions"`。无需读取正文、reasoning或全部尝试。
3. 核对 `direction`、`intake_status`、`execution_state`、`entry_result`、`filled_quantity`、`first_fill_at` 及 `execution_reason_codes/entry_reason`。`NOT_RECEIVED` 不是已执行；有执行记录不等于已成交，只有明确成交字段才能称成交。
4. 新的执行/成交需要通知；意图没有对应执行时，也在本轮通知并记为未决。无明确错误时写“有意图但尚无执行，疑似阻塞/待执行”，不要断言失败；明确拒绝或失败时写清原因。必要时只读该Case的 `part="main"`，补 `failures`；错误码优先取执行记录和列表 `trade.reason_codes`，没有则写“未提供”。未决Case下轮复查，同一待执行状态不重复报，后续实际执行算新事件。
5. 通知写清ticker、方向、执行/成交状态、已知数量、时间、Case标题（`title.value`）和case_id；若有 `execution_group`，注明主仓/对冲实际标的，避免都写成Case的ticker。优先使用 `first_fill_at.value` 作为成交时间；缺少执行时间就写未记录，不能把 `received_at`（Case接纳时间）冒称执行时间。PAPER/LIVE仅在有明确证据时注明，不推断。

交易去重按“ticker + case_id + 方向 + 已报告执行阶段/成交变化”记录：相同状态不重复报，首次成交、从部分成交到完成、后续明确失败可分别视为新事件。无新执行线索且无未决Case时，不钻取交易详情。

## 5. 美东15:30收益报告

每个美东日期，在15:30或其后首次成功巡检时，读取全局 `paper_realized_net_pnl`、`live_realized_net_pnl`。确认统计日为当天，任一项为 AVAILABLE 即生成一次“当日收益”新事件，**0和负数也要报告**；分别列 Paper/Live，缺失项写未记录，标明“截至当前的已实现净收益，非最终收盘收益”。两项均无数据则后续巡检再看，不编造0。每日期最多报告一次，和本轮其他新事件合并，不另发第二条消息。

## 6. 去重与唯一一次通知

- 阈值告警按“检查项 + ticker（如适用）+ 统计日（累计指标）”维护是否处于告警；一直超限只通知首次，不因数值继续升高重复通知。明确恢复后再次异常，算新事件；未知/缺页不能当作恢复。
- 消息源只通知新进入持续失败的 `source_id`。持续失败源未增加则不因它发消息；已通知源恢复后再次连续两轮失败，才是新事件。目录未读全不判恢复，也不能据此确认两轮持续失败。
- 每轮把全部新事件先放进一个集合。**集合为空：不调用通知工具、不发例行微信。集合非空：本轮只调用一次 `send_wechat_notification`**，汇总全部新事件，末尾用几句话概括仍在持续的已知问题/持续失败源。
- 标题示例：`DoxAgent巡检｜3项新事件`；正文注明美东时间。级别取本轮最高：正常交易/收益为 INFO，阈值/源失败为 WARNING，Live断连、交易阻塞或无法巡检为 CRITICAL。

```json
{"title":"DoxAgent巡检｜新事件","content":"美东时间：…\n新增：…\n持续已知问题：…","level":"WARNING"}
```

发送前保存待通知事件；返回 `data.status="accepted"` 后保存 `short_code` 并标记已通知。这仅表示PushPlus受理，不保证微信实收。明确拒绝则保留未通知事件供下一轮合并重试；超时/发送结果不明则记“待确认”，不自动重发同一批，避免重复推送。每轮最多一次发送尝试。

结束时保存巡检状态。只记录简短事实和必要ID，不持久保存分页游标、完整Case正文或凭证；不延伸为复杂故障排查。
