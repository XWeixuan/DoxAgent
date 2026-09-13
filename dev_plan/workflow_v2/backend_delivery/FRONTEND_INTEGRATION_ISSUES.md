# V2 前后端真实联调问题与覆盖记录

> 2026-09-09 后端更新：BE-01～BE-10 已按报告完成代码/契约修复及针对性复验，详见 [后端修复记录](BACKEND_INTEGRATION_FIXES_20260909.md)。BE-05 有效性和 BE-08 局部固定引用缺失保留明确未知/部分覆盖，未补造业务证据。以下保留前端原始发现及验收边界，不据此宣称完整上线通过。

日期：2026-09-08 至 2026-09-09。状态：本轮真实数据联调完成；发现的前端问题已修复，后端问题待另一任务处理。**尚不能认定已满足完整上线验收。**

环境：前端 5178 → 正式 V2 API 8098 → `.tmp/v2-integration/mu` 原生源库和读库。仅外部输入/模型执行使用录制包；未连接生产或券商。

## 业务与验收依据

已重新阅读两份前端 PRD，并对照 API draft.2、后端开发方案、REAL_DATA_INTEGRATION。主线为研究资产 D1 → 预期 D2 → Policy D3，Canonical Event/Fact 提供已知事实，Bus 标准消息进入 W1/W2 与可选 W3，形成可追溯结果和真实用量。Policy 命中、消费、正式 intent、成交必须分开。

最新用户批注优先于 PRD 中尚残留的旧导航顺序/跨 Unit 聚合描述；不因旧描述回退已确认交互。

## 全页面覆盖清单

| 页面 | 模块与能力 | 状态 |
| --- | --- | --- |
| 全局 | 认证、导航/ticker隔离、缓存/返回/刷新/错误、权限、桌面尺寸 | 内置浏览器导航/返回/手动刷新、1262/1559px 检查；HTTP 无令牌401；真实Supabase、多owner、多ticker不在数据包覆盖内 |
| Overview | 状态、四周期KPI、表单、筛选、暂停/重启/移除/重加、六步初始化/失败重试 | 浏览器四周期、运行/健康未知过滤、恢复全量、MU导航；标的页切换器真实目录仅MU；启动/恢复受BE-01阻断，未破坏唯一激活数据；六步初始化/失败重试无真实样本 |
| 基础投研 | C1/C3/C5、结构化文档/表格/折叠、未来节点分页、引用、历史、ZIP、刷新 | 三报告完整分块读取；未来节点20→26；引用解析/未解析；历史抽屉当前卡；ZIP结构及CRC通过。其他历史/失败版本无样本 |
| 预期研究 | 激活pin、Shell/4个Unit、State/Factor/Gap、引用、历史、局部导航/刷新 | 4个Unit逐一切换，State/Factor/Gap和引用入口，固定run来源跳转/首次滚动定位/sticky通过；失败Shell、多Shell、Unit跨页无真实样本 |
| 策略 | Shell/状态/五周期、KPI、42条分页索引、OR正文/来源、全局时间线、下载 | 默认/ALL、六状态、20→40→42索引、OR正文、来源Gap和时间线分页、JSON下载；ACTIVE/定义变更时间受BE-05/06影响；真实修改/失效/执行历史无样本 |
| 事件 | 四筛选/五周期、Event/Fact分页/详情/关系、索引、Delta日期/内容、刷新 | 默认/当天/ALL、四状态、Event20→40、E143的11 Facts/关系、索引定位、Delta空日期；排序BE-07，实际Delta与更多Fact页无样本 |
| 消息流 | KPI周期与列表独立、类型/来源/路由/搜索、正文分页、续页、来源状态、SSE/恢复/分钟刷新 | 默认/ALL列表保持同一保留范围、TRADE与关键词搜索、5行/展开正文、20→24鼠标及键盘续页、来源未知状态；SSE插入/路由更新通过，跨页续传漏消息BE-09 |
| 配置 | binding读取、表单/JSON、启用/间隔/窗口/参数、保存/校验/CAS、候选API | 60→75→60持久化；停用后回放不接管；恢复启用；双标签65/70冲突拒绝、最终恢复60。录制源无额外参数/候选API，爬虫JSON/多源/时段/缓冲未覆盖 |
| Runtime | 五周期/KPI、图/全部节点/路径、结果/来源筛选、Case/attempt/reasoning/消息/执行、SSE/分钟刷新 | 五周期、九节点、归档筛选、Case20→25、可用W1/W2依据/attempt/正文，图12→13即时而记录12→13按分钟；25条详情HTTP中23条200、2条BE-08；W3/候选/真实执行无样本 |
| 审计 | API/Codex、五周期、六KPI、趋势、节点/模型占比/筛选/分页、收益入口、刷新 | 默认/ALL、API/Codex、节点/模型占比、W1过滤、手动刷新、覆盖标记/收益未开放；98调用/Token/固定价格算术核对。只有1天/2节点/1模型，无多页/未知模型/Codex实数样本 |

## 后端问题

本轮共10项（9项真实接口/浏览器问题，1项静态确认的契约缺口）。P1：BE-01、05、06、08、09；P2：BE-02、03、04、07、10。下面区分实际复现与静态契约检查；本任务未修改后端实现。

## 覆盖限制与上线前置

录制包明确缺少完整初始化、同pin W3完成、真实成交/佣金/收益、真实Supabase登录，多ticker、失败Shell/丰富历史及外部采集配置分支未必有数据。不能把缺少数据分支标为通过；将区分预期缺失和实现错误。

## 前端修复与验证

接口证据位于 [integration-20260908](../../../frontend/v2/artifacts/integration-20260908/)，不保存认证令牌。文件带 `-final` 为25条回放完成后的复核；原文件保留24条及更早基线。

### BE-01 · P1 · Overview 控制能力与已激活运行状态矛盾

浏览器 5178/overview：启动所有模式禁用，MU 重启禁用。`GET /capabilities` monitoring=false / CONTROL_WORKER_UNAVAILABLE，`GET /tickers/MU` RUNNING、initialization_incomplete=false，却 actions.restart.reason=NO_ACTIVE_REVISION。真实状态 CLI 同时有 activation_id=integration-mu-frozen-20260908，D1 current 可读取。需核对能力构造及重启动作判定是否遗漏本地激活接线；不要由前端绕过 actions。当前不执行暂停/删除，避免唯一数据集无法通过 UI 恢复。证据 overview-start.json；影响启动/暂停恢复/移除重加端到端验收。

### BE-02 · P2 · 非例行维护固定指标返回未知

`GET /overview/metrics` 当前日及其他窗口的 nonroutine_repairs=NOT_RECORDED。PRD Part1 §5.5.5、Contract §4.4 要求本期固定 AVAILABLE 0，无环比。应在后端按正式当前功能定义返回0。证据 overview-research.json。

### BE-03 · P2 · 健康未知未反映在顶层覆盖状态

MU health=UNKNOWN / NOT_RECORDED，Overview正常/阻塞都为0且资源coverage=COMPLETE。Contract §4.4要求 UNKNOWN不归类但相应计数coverage=PARTIAL；否则零正常/零阻塞看似完整健康观测。证据 overview-start.json、overview-research.json。

### BE-04 · P2 · 未开放收益审计能力标记为 true

`GET /capabilities` revenue_audit.available=true；本期PRD/Contract明确该入口仅“暂未开放”且capability=false。前端当前仍按PRD不请求收益数据。证据 overview-start.json。

### FE-01 · 已修复 · 未来节点分页覆盖前页

真实数据26项暴露20→6替换且无法返回；改为usePages追加，浏览器复验20→26项保留。

### FE-02 · 已修复 · 报告引用无读取入口

D1正式正文有【cite:O#】但无来源查询入口；使用现有Citations组件按需读取当前ContentRef，不预取。浏览器已能读取并显示已解析链接及未解析项，不伪造URL。

### BE-05 · P1 · 生效 Policy 未知被当成完整零值

真实 D3 context 固定42条正式Policy和匹配D2；ACTIVE列表 EMPTY/COMPLETE、生效KPI AVAILABLE 0/COMPLETE。ALL+ADDED可读42条，各条consumed/effective却为NOT_RECORDED。既然无法证明消费/有效性，不可把未知过滤成可信0；需补齐激活/消费关联或明确PARTIAL/UNAVAILABLE。浏览器默认策略页完全无正文。证据 strategy.json。

### BE-06 · P1 · Policy 新增时间使用导入时刻

context.policy_set.published_at=2026-09-03T15:18:40.001142Z，但changes中42条ADD occurred_at=2026-09-08T13:51:19.132Z，界面显示09/08 09:51。Contract/PRD要求正式PolicySet发布时间。需保留imported/recorded与published/occurred区别，否则周期新增、排序和时间线均错。证据 strategy.json。

### FE-03 · 已修复 · 真实 D2 长文挤压 State 表头

实际参数定义和USD_PER_*单位使来源/有效性表头逐字竖排。改为稳定列边界、正常换行与表内有界横向滚动，长horizon独立换行；未改变状态数值语义。

### BE-07 · P2 · Event 发生时间未按精度解析排序

`GET /tickers/MU/events?view_id=...&filter=ACTIVE&limit=20`：2026-Q2 的 E178、E179、E40、E166 等排在 2026-09-22 的 E21 和 2026-08-26 的 E181 之前。Contract §2.4要求 QUARTER 按区间起点、DAY 按日期，统一倒序，不能直接比较展示字符串。浏览器索引也继承此错误顺序。应修复服务端排序锚点和游标一致性，不改变原展示精度。证据 [events.json](../../../frontend/v2/artifacts/integration-20260908/events.json)。

### BE-08 · P1 · 两个已完成 Case 的详情缺失固定产物

浏览器 Runtime → 全部 → 对应记录 → 研判明细，返回 HTTP404 `PINNED_ARTIFACT_MISSING`、retryable=false。25条逐项只读检查中23条200，以下两条失败：

| case_id | 消息 |
| --- | --- |
| case_66b98f01d0f740ccb36a4c11a003f79a | Micron Revises Selected Package Deliveries After Utility Interruption |
| case_7365a02a94054c0a8da01f737c23b2e7 | Orion Cloud Clears Micron 245TB QLC SSD for Its Approved Environment |

期望：固定 pin 的已录制 W1/W2、attempt 与可用正文可追溯；若只有局部产物确实缺失，按契约保留其他已知部分和缺失原因，不能换用最新版本补齐。请核查组装包对这两个 Case 的固定引用/产物登记，修复后用同一 Case URL 复验。证据 [case-availability-final.json](../../../frontend/v2/artifacts/integration-20260908/case-availability-final.json)、[runtime-final.json](../../../frontend/v2/artifacts/integration-20260908/runtime-final.json)。

### BE-09 · P1 · 消息 SSE 将首屏挤出误报为筛选移除，造成分页漏项

复现：回放到24条 → 消息页20条首屏 → 展开更多到24条 → 离页到Overview → 单步新增第25条 → 通过MU导航返回消息流。新消息成功续传，但可见列表仍为24条且没有更多入口；原第20条 `std_70b052b1938d485b8aa11ca88c73ea10`（SK hynix, Samsung and Micron Set the Reference Points for HBM4 Supply）消失。新增消息ID `std_b83cf411b62d4a15a37d0edacba30900`。同条件完整HTTP基线仍包含25条和该旧消息，消息没有删除或失配。

原因已定位到 [streaming.py](../../../src/doxagent/api_v2/streaming.py)：MessageStreaming.next只读`limit=state["limit"]`的头部，将old head不在new head中的身份都标记REMOVE，matches_scope=false。Contract §11要求REMOVE只移出当前筛选；头部移位不是筛选失配。它还意味着后续页中的路由更新可能不进入该头部差量，需要一并按契约核查。

期望：已加载分页集合不能丢失仍符合筛选的消息；新消息和后续路由变化按稳定身份增量更新，真正失配才REMOVE，游标重连幂等。前端继续遵守REMOVE契约，不用忽略REMOVE、补读全列表或拼造页内记录规避问题。证据 [message-bus.json](../../../frontend/v2/artifacts/integration-20260908/message-bus.json)（24条旧基线）、[message-bus-final.json](../../../frontend/v2/artifacts/integration-20260908/message-bus-final.json)（25条新基线）、本轮内置浏览器DOM复现。

### FE-04 · 已修复 · Policy 来源定位遗漏 Gap 与异步滚动

来源URL补齐content=GAPS与item=gap_id；D2在实际Unit到达后才定位，重载也有效。浏览器验证 NAND Unit 的“控制器固件故障导致资格撤回”：首次进入scrollY=380，目标位于视口内，索引top=20。另为尚未出现在Unit列表页中的指定Unit使用契约单条端点读取，不静默显示第一页其他Unit；跨Unit分页分支仅类型/代码检查，当前4个Unit无法制造第二页。

### FE-05 · 已修复 · 无发布 Delta 日期仍请求不存在的日对象

日期目录已完整且为空时直接显示未发布；未知/错误目录仍显示相应状态，不误判没有变化。浏览器DELTA原404已变为明确的未发布空态；不编造Delta。

### FE-06 · 已修复 · Runtime 最终结果为空时单元格完全空白

results=[]时按result_settled显示“结果未记录”或“尚未形成结果”，状态仍独立一列。当前录制环境没有正式Delta/交易effect回执属于已知范围限制，不能把消息的TRADE或ADD_TO_DELTA推断成真实成交/事件发现。浏览器完整25行已复验。

### FE-07 · 已修复 · 成本与消息 KPI 遗漏覆盖信息

复用CoverageNote/MetricNote，在成本六KPI、趋势、占比、节点与消息周期指标展示真实PARTIAL/UNKNOWN及暂估；未计价请求非零或未知时显式显示。浏览器当前0.822M/$0.093与图表、节点均标“部分覆盖”，Codex成本仍“不适用”。

### FE-08 · 已修复 · 正文懒加载导致续页按钮位移

原未加载预览仅40px，真实长正文到达后扩至五行，使滚动到尾部的按钮在点击前移动。为收起正文预留一致的五行高度，不扩大独立流框。浏览器重新加载后鼠标一次点击20→24成功，键盘Enter也通过；展开仍读取同一正式正文。

### FE-09 · 已修复 · 真实长 Shell 问题文本覆盖相邻按钮

1262px截图发现真实Shell问题超过420px后绘制到“全部Shell”上。改为边界内省略号，title保留完整问题；Shell横向滚动仍独立，状态下拉位置不变。浏览器复验长标题420px、hidden/ellipsis，无页面横向溢出。

### BE-10 · P2 · 消息实时排序所需的 stream_offset 未出现在 DTO

静态契约问题，当前录制源没有相同入流时刻的不同StreamItem来完整浏览器复现。Contract §2.4要求按stream_published_at、stream_offset、member_index、standard_message_id倒序；实际MessageSummary只有stream_published_at/stream_item_id/member_index，没有stream_offset或等价排序键/插入位置。opaque的stream_item_id不能替代顺序。请后端/契约提供可证明的完整排序信息；不能要求前端猜测相同时刻不同StreamItem的先后。

### FE-10 · 已修复已知部分 · SSE 使用采集时间而不是入流时间排序

改用stream_published_at；同StreamItem按member_index/身份倒序。新增两个聚焦单元测试验证“早采集但晚发布的buffer”和“同buffer成员次序”。不同StreamItem的同时间排序受BE-10限制，当前暂保留接收顺序，没有把该分支宣称正确。此项为契约/代码检查发现，录制数据仅IMMEDIATE，真实buffer仍待补验。

## 最终证据与验收边界

- 回放25/25，auto=false、stage=idle；MU仍RUNNING/MESSAGE_MONITORING，new_intent_allowed=false，Binding恢复enabled=true/60s。未启动外部模型、爬虫或券商，没有生产操作。最终源Bus位置91/91、Runtime179/179、Initialization1/1，projection_gaps=[]。最后手动刷新后消息分页25条/25个唯一身份；刷新恢复完整基线不代表BE-09已修复。
- [verification-summary.json](../../../frontend/v2/artifacts/integration-20260908/verification-summary.json)：98次录制真实调用、822108 Token、$0.09284291176470588235294117646。校验input+output=total、cached属于input、固定产品定价/6.8汇率算术一致。coverage=PARTIAL，不代表完整历史费用。
- 下载：D1 ZIP含manifest/C1/C3/C5/future_nodes，CRC通过；Policy JSON保留42条定义和D2/EventLibrary固定引用。浏览器下载入口与实际HTTP文件均检查，未把点击成功单独当作文件正确性证明。
- 真实配置写入：间隔保存及重载；停用后step不推进；恢复后继续；双标签页旧ETag提交被REVISION_CONFLICT拒绝且草稿保留。源摘要的快照可能短暂保留旧值，完整重载后与effective一致，本轮不将其直接判成后端持久化失败。
- 前端最终检查：TypeScript、ESLint、22个Vitest（含2个新增消息排序测试）通过；生产构建通过，仍有既存512.83kB主chunk提示。本轮未跑后端广泛回归或外部Playwright；UI均通过内置浏览器操作。
- Runtime重试浏览器补验：Samsung Electronics Provides HBM4 Delivery Status Update 展示 W1-R2 attempt1 FAILED/14.68s 与 attempt2 SUCCEEDED/24.50s，未合并或覆盖失败尝试；最终OLD与W2未命中单独显示。
- 默认/ALL覆盖所有有统计窗口的业务页；Overview四周期、Runtime五周期实际点击。其他页面没有把每一种周期×筛选笛卡尔积都声称通过。服务断网/401续期/403其他owner、游标过期RESET、真正浏览器后台休眠恢复尚未做真实故障注入；已覆盖实际离页后重新连接，并由此发现BE-09。

### 上线真实测试前需补齐

1. 修复9个已复现后端问题并解决1个排序契约缺口，优先控制能力、策略有效性/时间、固定Case产物和消息SSE漏项；按上述同样URL/数据复验。
2. 独立数据目录补齐完整初始化/失败恢复/移除重加、多ticker、多Shell/失败Shell/多Unit分页、不同历史版本、Delta ADD/MODIFY/REMOVE、可配置API/爬虫与多源/缓冲样本。
3. 真实Supabase登录/失效/角色隔离，以及目标部署的代理、重连和限流验证；当前本机身份仅DEV integration可用。
4. 当前包没有同pin完成W3、真实Candidate释放/消费、Entry/Exit/Fill/PnL，也没有正数Codex成本样本。这些能力不能记为通过；Paper/Live真实交易测试须沿正式执行验收条件单独进行。

本轮完成的是“所有页面均进入真实API环境检查，并处理已发现前端问题”；不是“所有生产能力已通过”。后端修复和上述缺口补验前，不建议宣布可以完整上线。


## 2026-09-12 Runtime 判定归因缺失（本轮前端排查）

### P1 W2 无候选时没有可展示的 R1 分析
- `persistent_runtime_v2/service.py` W2 R1 的 candidate_policy_ids 为空时，直接构造 reason=`no_policy_candidate_recalled` 的 W2PolicyResult；并非前端折叠组件吞掉 reasoning。
- `schema.py` W2Round1RecallResult 只定义 candidate_policy_ids，没有分析字段。`v2_read/projectors.py` case_reasoning 只保存 w2_final.reason，Case API 也只暴露此 ContentRef。
- 需要后端在业务输出中记录可展示的召回判断依据（不是模型内部思维链），投影并按 Case 固定版本提供。历史未记录的分析不能由前端补写，也不能把无候选当成发生过 R2 判定。
- 验收：无候选显示真实召回解释；有候选未命中显示最终判断解释；命中展示真实 Policy ID 与 matched_condition_ids。前端已展示可用的策略/条件，并增加 unresolved_policy_ids 提示，展开完整续取正文。

### P1 W1 Event / Fact 归因
- `api_v2/case_detail.py` references() 仅遍历 reference_ids；成功解析 Event 时仍将 fact_ids 固定写成 []。解析失败时身份进入 unresolved_reference_ids，旧前端未显示这些 ID。
- 前端已补齐未解析 ID，并明确“Fact 归因未提供”及 OLD 完全无归因情形；不得把整个 Event 下的所有 Fact 冒充判旧依据。
- 需要后端确认原始判定记录是否含精确 Fact 归因，按被该 Case 固定的库快照返回；若模型输出未记录，补充业务输出与持久化链路。历史缺失须保留明确未知。
- 验收：OLD 的 Event 和实际参与判定的 Fact 可追溯；缺失快照保留原始 ID；不链接到当前新版本冒充原版本。

### 当天新增计数边界
- 前端已通过 CURRENT_TRADING_DAY + source_id 的 BusMetrics 单独读取当天新增，未将 last_published_count 重命名冒充日累计。
- `api_v2/bus_metrics.py` 对消息数为 0 的 current 返回 missing()，所以当天暂无消息时目前仍可能显示“未记录”；前端保留接口语义，没有擅自补 0。若来源覆盖完整，应由后端返回明确的 0。

### 2026-09-12 后端补充处理（Runtime 判定归因）
- W2 R1 增加业务 reason；无候选保留 R1 解释且不执行 R2。Case reasoning 沿用 ContentRef，新增 reasoning_stage；历史机器标记不冒充召回分析。
- W1 增加精确 fact_attributions，写入时约束到已加载的 Event/Fact；Case 保留原始 E#/F# 并按固定快照提供 EventLink.fact_ids。历史 null 表示未记录，局部引用缺失保留原始归因并 PARTIAL。
- API contract、共享 TypeScript、后端 wire schema 和前端生成验证器同步更新。此次不改 Bus 当天零计数；不回填历史判断，不自动重放线上 Case。

## 2026-09-13 Closed Cycle 真实线上排查

线上权威记录证明 Sunday 02:00 ET 已执行三个 Source Sweep，并产生 12 条 Stream/12 个 Runtime Case；以下问题不是“任务未启动”，而是 Closed Cycle 编排与读取契约尚未闭合。

### BE-11 · P0 · Closed Sweep 未实现 per-source cursor/cutoff 完整增量
- `persistent_runtime_v2/bus_orchestration.py::_source()` 仍调用普通 `scheduler._poll(source, binding, now)`，没有把 `[last_successful_cursor, sweep_cutoff)` 传入 adapter，也没有为 Finnhub/Benzinga 持久化 pagination cursor。
- 2026-09-13 三个 Source Sweep 虽均为 `SUCCEEDED`，receipt 均为 `coverage=UNKNOWN`；这只能证明到点调用过 provider，不能证明完整覆盖上次成功位置至 cutoff 的增量。
- 修复边界：每个 Source 独立保存成功 cursor/pagination state；请求和落库必须受固定 cutoff 约束；只有覆盖闭合才记 `COMPLETE`，不支持或无法证明时继续显式 `UNKNOWN/PARTIAL`。
- 验收：连续休市两次 Sweep 无漏项、无跨 cutoff 项、重启可从成功 cursor 恢复；重复 provider payload 不产生 revision，单源失败不伪装为全 Sweep 完整。

### BE-12 · P0 · 异步 enrichment 与 stream highwater 之间缺少 Sweep 结算栅栏
- Source Sweep 在本轮 enrichment jobs 尚未终态时读取 `latest_stream_offset` 并完成；2026-09-13 主 Sweep 只冻结 4 条，稍后完成的 8 条被拆到 supplemental Sweep。
- 同一个计划内 02:00 Sweep 因此执行成 `4 + 8` 两批，并触发两次 O2/O3 maintenance、两次 activation；这不符合“本 Sweep 全部 dedupe/确定性排序后统一进入 Runtime，再 Maintain 一次”的冻结方案。
- 修复边界：poll receipt 必须带本轮稳定 intake/job 集合；等这些 job 全部进入 terminal（成功、明确 fallback 或隔离失败）后再取得 stream highwater、冻结 roster。正常异步晚到不得自动开启第二次 maintenance；补充 Sweep 只用于有明确身份和恢复语义的异常补偿。
- 验收：一次计划 Sweep 只有一个 frozen roster、一个 pinned Runtime bundle、一个 maintenance/activation；进程中断恢复不重复 Case，也不把同批消息拆到新版本。

### BE-13 · P1 · Message Bus/Runtime 页面仍用交易日窗口排除休市 semantic day
- `v2_read/calendar.py` 在非交易日拒绝 `CURRENT_TRADING_DAY`，`TRADING_DAYS_7/30` 也只枚举交易所 session；`api_v2/views.py` 因此在周末把“当天”设为不可选。
- 9 月 13 日的 12 条 Message/Case 均正确标记 `semantic_day=2026-09-13`，但不属于任何返回的 trading-day membership，所以当天、近 7 天页面都不可见；Event/maintenance lifecycle 使用另一套可见日期，造成同一页面体系互相矛盾。
- 修复边界：Message Bus、Runtime、Event/Policy lifecycle 使用包含休市日的 semantic-day window，“当前 semantic day”每天可选；真正依赖交易所 session 的行情、PnL 等指标继续保留 trading-day window，不做全局粗暴替换。
- 验收：周六/周日当天能够查看 Closed Sweep 的 Message、Case 和失败状态；7/30 日窗口包含对应 semantic days，且交易日比较指标的 membership 仍保持明确。

### BE-14 · P2 · O3 NOOP/继承版本被展示为内容变更
- 2026-09-13 两次 maintenance 均完成并产生新 activation revision，但 O3 receipt 的 policy version 始终为 v2；前端仍显示 O3 变更，混淆“维护任务/激活发生”与“Policy 内容版本改变”。
- 修复边界：后端 lifecycle DTO 明确区分 `MAINTAIN_COMPLETED`、`NO_CHANGE/INHERITED`、`CONTENT_CHANGED`，前端只按该权威 disposition 展示，不由新 activation revision 推断 Policy 内容修改。
- 验收：O2 Event Library 真实 v2→v3→v4 可显示两次变更；O3 版本保持 v2 时显示维护完成但无内容变化。

### BE-11–14 修复记录（2026-09-13）
- BE-11：固定半开窗口、Benzinga update-time 分页/Finnhub 日期分页；每源版本独立持久化扫描位置、页进度和 coverage。没有 provider 完整性证书继续 UNKNOWN，达到页上限/项目错误为 PARTIAL，不能宣称历史全覆盖。
- BE-12：receipt 保存稳定 intake job 集合；分页完成后跨进程恢复等待 job 终态、flush buffered publication，再取 highwater。等待不消耗失败预算、不阻塞其他来源；未结束的 Sweep 期间禁止同 ticker 提前恢复普通 polling。
- BE-13：四个业务页面采用 LISTED_SEMANTIC_DAYS，周末当天可选，7/30 日包含休市日；交易指标保持原 session window，SSE 与列表采用同一成员列表。
- BE-14：Activation/PolicyContext maintenance 明确区分维护完成、继承/无变化、内容变化及未知；策略页按权威 disposition 展示，Reference Delta 文案明确是输入变化。
- 必要验证：休市扫描栅栏及恢复、固定 cutoff/pagination、周末窗口与交易窗口隔离、固定版本归因/激活 DTO；前端类型检查通过。真实连续两个休市 Sweep 的结果须在下一计划周期自然运行后检查，本次不人为触发维护/订单。

### BE-11–14 新加坡部署验收（2026-09-13）
- 代码提交 `b2539116` 已推送 main，新加坡已 `git pull --ff-only`；生产前后端重新构建并切换，补入服务器已有官方 ibapi 10.49.2。
- 8 个数据库完成备份及完整性校验，备份目录 `/data/backups/20260913T151451127807Z`；12 个服务运行，API/Web/Codex Worker 健康，公网 `/healthz` 返回 ok。
- 已登录生产 UI 验收：消息总线周日“当天”可选，周期新增 12；近 7 天为 26，包含 9 月 13 日记录。Runtime 当天处理 12，原有 1 条失败可见。策略页显示“最新维护已完成 · Policy 无内容变化（v2）”。
- 两轮运行检查无服务重启；四个 source head/checkpoint 均追平且 error 为空。未重跑历史 Sweep/维护，trade jobs/executions 均保持 0。
- 保留历史 coverage=UNKNOWN；连续两个自然休市周期的完整端到端运行验收仍待后续计划任务，不能用本次页面验收替代。
