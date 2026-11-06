# Reuters 内部可靠性修复与验收

## 实施边界

依据 `reuters-recurring-retry-root-cause-audit-20261107.md`，在现有 main 工作树执行。保留 Profile、Chrome、原 target、页面所有权、现有请求去重、业务验收和人工维护。仅窄发布 Site Access，不重建 Supervisor。

本轮修复控制、调度、诊断和终态一致性。不增强自动验证码绕过，不通过并行身份规避站点限制，不新增付费测试。用户提出的双 Chrome 并行规避受限请求不实施；原有业务轮询目标配置不修改，容量不足时如实报告延期，不能承诺第三方阻断下保持原有时效。

## 可执行变更

1. External Chrome 的 CDP 初始化失败时，回收 Python/Node 控制 Worker，保留 Supervisor/Chrome/Profile/原页；同一业务请求不重放。恢复协调器持久化连续控制故障次数，采用 60/120/300/600 秒退避，并同时约束 next_due_at/next_probe_at。成功完成附着后清零。维护与来源状态显式显示控制层不可用，区别于网页 Challenge。
2. 保护期内仍保持原有节奏；自然业务成功后，在一个保护期长度内线性恢复至配置节奏，避免 300 秒处突然放量。不改普通轮询目标、不补发积压请求。复发列表保留有界的全部复发，并标记是否处于保护期、恢复升载期或其后 120 秒，原有短期熔断只使用该窗口内事件。
3. 已终止、提交不确定或应用不确定的付费任务，先处理终态，再判断浏览器准备动作；不得再点击 RETRY 或购买新任务。APPLIED 后现有被动观察和业务证明仍可继续，人工待办及原页保留。
4. 利用已有原页观察和自然 CRAWLER/BODY 回执，将页面验证、HTTP状态、登录状态、搜索契约、正文质量分开记录。只保留每层最新摘要及最多 8 条状态变化，不存 Cookie/完整URL/正文/响应头，不新增网站请求、抓包、截图或高频观察。
5. 冷附着只增加阶段/耗时/错误类型日志，不输出异常字符串或 CDP URL。

## 验证与发布

- 离线回归：控制进程回收且不重放、退避持久化与清零、升载边界连续、6–7 分钟复发入账、终态零输入/零购买、诊断脱敏及有界幂等写入、人工维护兼容。
- 窄发布：精确提交、Registry 私有在线备份、仅 Site Access，核对源码摘要与 Supervisor/Chrome instance/原页仍在。
- 真实验收：仅自然请求与持久回执，区分 Provider、原页解除、搜索、正文、控制故障和延期。若现有网页仍被阻断，应明确业务验收未通过，不能以测试通过或 Provider APPLIED 交付为恢复。

## 执行结果

离线专项 28 passed；扩展回归 111 passed、3 failed，三项在未修改的 main（d225ff4d）源码上均同样失败，证据见 baseline.xml/log，不宣称全绿。Ruff F/E9 与 git diff --check 通过。

三项旧失败为 finished_capture_does_not_replace_same_or_next_poll_network、legitimate_zero_results 的旧占位页样本、live_recipe_accepts_same_zero_result_contract 缺少 page.url 的测试替身；本轮没有放宽搜索契约来消除这些失败。

发布前只读快照：备用仍为 browser_cdp_attach_failed，累计 attempt_count=1067；主身份 verification_pending。磁盘约79GiB余量，具备窄发布空间。远端 main 留有另一维护的 changelog 修改，发布使用不可变 Git 提交，不覆盖该工作区改动。

发布与真实运行结果将在窄发布后补充；未证实搜索/正文恢复前，不标为生产业务验收通过。

## 真实运行中进一步定位的控制层根因

对已有标签页各做一次有界本地 CDP 读取（无导航、输入、reload、自动附着/恢复 renderer），主身份3个标签页均正常；备用原页6AA6…及另一页正常，唯独8552A991AC8C5CFD7E330A08E06D85C5的 Page.getFrameTree/DOM.getDocument 均2秒超时。所有权核实：它是11月5日00:41 UTC创建、1.5秒后就记为CLOSED的 BUSINESS 页，没有任何人工会话或 Challenge 引用。它仍然活在原生 Chrome 中。

旧代码把关闭ACK当成实际销毁，并且只查询非CLOSED/LOST记录，因此该遗留页永久逃过清理，又卡住 Playwright 对现有页面的初始化。这解释了为什么重建控制Worker仍不够。

追加精确修复：冷附着先取得原生target清单，仅按这些ID索引查找同Chrome实例的CLOSED业务遗留页；Owner原子校验身份/实例/target及人工原页引用后重新声明CLOSE_PENDING。实际关闭需观察原生target消失才记CLOSED。External业务页正常收尾也采用同一确认，失败则保留CLOSE_PENDING并在finally释放控制lease，避免新增槽位泄漏。未知页面、当前Challenge及人工维护页保持原有保护。没有按年龄或URL扫页，也没有重建Chrome。

历史关闭回包本轮无法还原，因此不声称已证明当时具体返回了true还是false；能确认的是旧实现不核验实际销毁、登记CLOSED却原生存活，以及该页阻塞初始化。修复后的自然恢复已形成对照：遗留页消失，同一Chrome实例重新完成搜索请求，控制故障计数清零。

## 发布与真实验收结果

最终运行源码：`1b3a744fa8e803a62858abaa1eede0ea9fbc0fe8`，已推送origin/main。仅Site Access重建，最终容器d9e8d0aed550，healthy，10个模块摘要一致。Supervisor未重建，主/备用Chrome实例558479…/5cfab0…与原PID保留。Collector、Bus、Enrichment没有重启。首次发布命令曾传错提交参数，构建前退出、未替换服务，已纠正；中间有效发布224ed/19f6/1706单列，不混作最终版本运行证据。

最终版本窗口：北京时间2026-11-07 **02:54:10—03:07:31**（UTC11-06 18:54:10—19:07:31）。备用于02:47:57经自然fresh_probe验证恢复；至窗口结束约19.6分钟，覆盖5分钟保护、5分钟升载及随后约9.6分钟正常期。旧备用原页6AA6…是在业务恢复提交后由既有收尾流程关闭，任务为RESOLVED，不是发布丢页或TARGET_LOST。主身份EDCA…仍留存、仍阻断。

| 指标 | 实际结果 |
| --- | --- |
| 正式CRAWLER ACCESS_RESULT SUCCESS | 223；不是223条新新闻，也不等于全部HTTP请求总数 |
| 爬虫排队延期 | 218，site_queue_timeout，不计成功或新Challenge |
| BODY排队延期 | 3；正式FULL/SHORT_FULL回执为0，正文未验收通过 |
| 采集Owner额外失败/在途 | 2次search_query_failed，dispatch分别为18:54:11/18:54:13 UTC，均处于发布启动切换期；9个DISPATCHED在途，不计成功或失败 |
| 最终版本控制故障 | 无新增CDP附着失活回执；备用control计数已清零 |
| 主身份自然恢复探测 | 2次STILL_BLOCKED，当前仍需人工处理 |
| 备用复发 | 该观察窗未新增Challenge；不能外推长期稳定 |
| Provider | 无本窗口新增付费提交；更新的是11-05旧作业的晚恢复/诊断，不计即时解码成功 |

查询覆盖基于**当前采集Owner acquisition.sqlite3 的units/cycles**，按现行QUERY单元在该窗口逐项完成一次计一轮，不能使用已退役bus.sqlite3内的poll_states/poll_runs投影。当前为10个ticker；不是旧快照的9个。

| ticker | QUERY数 | 窗口完整查询组数 |
| --- | ---: | ---: |
| INTC | 3 | 1 |
| BE | 3 | 3 |
| CEG | 2 | 2 |
| COHR | 3 | 3 |
| IONQ | 2 | 2 |
| MU | 3 | 3 |
| NBIS | 3 | 1 |
| ONDS | 3 | 3 |
| RKLB | 3 | 4 |
| SNOW | 1 | 6 |

这些覆盖不能证明每60秒完成一轮。单位当前仍可能显示queue失败，不能把整批ticker标绿。

恢复后实际观察到的非辅助搜索启动数（UTC18:54部分分钟起）：5、8、10、14、19、20、20、20，显示平滑升载而非原来300秒处的阶跃。启动计数来自已有回执中的transport时间，缺少时间字段的失败不在其中，不当作完整网络抓包统计。

## 尚未达到的目标与后续优先级

1. **总业务时效未通过。** 当前主身份max_concurrency=2/min_interval=1500ms，备用为1/3000ms；主身份仍被阻断时，备用理论导航启动上限约20次/分钟。采集Owner有26个QUERY和10个TAIL单元，均配置60秒，尚未计BODY，需求超过单备用供给。这是剩余排队和覆盖缺口的明确容量约束，不应再笼统归因为Challenge。业务目标频率配置未下调，但实际时效尚未满足。
2. **双Chrome并行未实施。** 本轮保留既有主备，不新增用多身份分流规避站点限制的能力。后续应在正常授权访问前提下解决容量与业务入口问题；不能以本轮内部修复已上线来宣称这项新增需求完成。
3. **正文能力未完成真实验收。** 只有排队延期，没有正式FULL/SHORT_FULL。缓存auth_state=VALID不是当前正文权限证明。下一步先恢复主身份正常授权访问，再检查BODY的实际排队、正文契约和权限；本轮不强制制造额外网站请求或付费测试。
4. 现有APPLIED后诊断已区分原页阻断、fresh_probe与自然业务，保留HTTP状态、分类原因、查询/操作关联摘要及有界时间线。网站HTTP错误取证期间page.content/all_headers再失败时，保留原HTTP错误，避免误当网络故障重放请求。网络细节继续复用原ACCESS_RESULT stage_timings，不添加抓包或截图。

最终专项29 passed；遗留页/双Runtime/维护扩展108 passed、1 skipped、2个复现的基线失败；较早扩展111 passed、3个复现的基线失败。选择集重叠，不相加。产品Ruff F/E9通过。未把旧搜索占位页放宽为成功以消除旧测试失败。

资源：诊断每作业最多3层最新摘要、8条变化，正常相同状态不写入，不增加截图或网站探测。最终单点可用内存约7.6GiB、磁盘余量约74.7GiB、Registry WAL约14.2MiB；全机CPU压力样本不是本功能的性能分位或归因证据。本轮清理3份自身冗余中间备份，保留首份发布前和最终回滚快照，释放3,881,451,520字节；未删除活跃业务库、Profile或回滚镜像。

最终证据为 `eval/reuters_internal_reliability_20261107/acceptance-final.json`、`final-release.json`、`passive-targets.json`、`passive-targets-after.json`、`ownership.json`。没有安排后续自动监测。
