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
