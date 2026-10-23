# Challenge Solver / Recovery 修复实施记录

日期：2026-10-23。依据：`dev_plan/workflow_v2/site_access_challenge_solver_recovery_repair_plan_20261023.md`。

## 实现

- `failover.py`统一候选解释，恢复后的暖证明绑定实例、配置、认证 epoch、出口；坏备用可由主身份接替，健康活动身份保持 sticky。显式候选必须属于 scope；BODY 借用备用不抢走健康 CRAWLER 路由。已有有效暖证明不停止 Chrome。
- `recovery.py`保留90秒 tick/100秒 lease，预留10秒收尾，导航预算按真实剩余时间计算。独立恢复容量先于 Profile writer 获取，容量等待至多5秒；排空和 IPC 使用剩余预算。每个 probe_id 都有唯一技术终态，异常中断由下次 claim 对账；未取得网站回执不更新 last_real_probe_at。
- solver 的 provider poll 在 Profile writer 外；ready 先保存费用再应用 Cookie。APPLYING 中断/应用回执不确定不重复写入、不重新购买。旧 APPLIED 只补为 INDETERMINATE，不补造业务成功。事件结束后仍有 taskId 的作业做有界费用收集，solution 不落盘。
- APPLIED 后原页按5/15/30/60/120秒有限确认，每 tick 一次；缺乏业务证明至多预留一次独立 probe。窗口耗尽输出 STILL_BLOCKED / INDETERMINATE，保留原页、人工及既有后台恢复。费用、应用、原页清除、业务证明、路由、自然请求、复发分别记录。
- `observe_verification_document`统一原页/OOPIF证据；保留 owner→frameId 精确绑定。可解判断需要真实 loaded URL、可见尺寸、可读子页和交互证据，不拿父DOM填充子页。隐藏资源不构成 Challenge；不可读不等于 target 丢失。明确 ACCESS_BLOCK 立即进入人工复核，而非先误入 AUTO。
- Reuters 恢复后持久化5分钟观察期，实际导航最短10秒，已有单并发不变；两次快速复发后延长至15分钟。恢复/canary/人工新业务页与正常业务共用 Identity 节奏时钟。60秒、24小时的消息源调度不变；其他来源不加临时降速。
- 只读 CRAWLER recipe 增加精确语义 single-flight/15秒缓存；请求、query、分页、scope、配置、实例、session、出口及 route generation 隔离。结果复制消费者 ID，物理 ACCESS_RESULT 与 CAPTURE_CONSUMED 分开，复用既有32MiB总缓存预算。
- 每 Identity 10分钟最多2次付费预留；全局24小时预算保留。限额不阻断免费探测、人工或真正备用。
- solver metadata / resolved recovery 保留30天；活跃事件、未结费用及人工引用不会被7天GC删掉，相关 target 受保护。继续分批清理，不删除 Profile、Cookie 或业务数据。
- API/英文桌面 Verify 分列 challenge、采集、正文、认证、路由、原页检查和 solver 费用。原登录与 Challenge 流程保留隔离；人工完成后调用同一候选 reconcile。

## 测试与边界

新增 `tests/test_solver_recovery_repair.py` 的20项确定性复合回归，覆盖暖主接替、旧实例拒绝、Viewer占用、先记费用、原页丢失仍收账、确认窗口、跨事件付费上限、活跃记录保留、取消/中断终态、共享结果、OOPIF/隐藏资源、剩余预算、持久观察期、快速复发及旧APPLIED兼容。没有调用新闻站或 provider。

扩展回归 **318 passed / 1 skipped / 1 deselected，161.11秒**；随后增加的快速复发、旧APPLIED兼容2项独立通过。宽范围测试发现 `tests/test_source_maintenance.py::test_closed_evidence_archived_after_thirty_days_not_active_thread` 失败：该独立 Codex 维护模块及测试均未修改，测试固定 clock=10月5日，但 Incident.updated_at 使用真实当前日期，31天后 cutoff 仍早于该 updated_at。该已复现的既有测试单独排除，不借此启用/修改维护 Worker。运行时包静态检查无F类错误；改动模块Ruff（仅排除既有长SQL行E501）通过。

不能承诺 Reuters 永久免验证；APPLIED、一次 canary、原页背景有结果均不等于持续可用。自然付费滑块未出现时不制造验证、不反复购买或守候。24小时观察是独立验收阶段，不能用离线测试或一次恢复替代。

## 发布证据与现场污染

发布前已在线备份 Registry（root私有目录）；脱敏清单位于 `eval/challenge_solver_repair_20261023/before.json`，源码改动之外未读取凭据/Cookie。

11:58 UTC原 Supervisor=`36804dfd...`，Reuters备用精确原页 `C8480BB8...` / instance `3c11550b...` 原生 CDP 确认存在。随后生产发生本轮之外的整批容器重建；12:03 UTC Supervisor变为`5042aeaf...`，旧页已丢失，随后出现新 target `F01CB50E...`。`external-restart.json`保留前后证据。该重建不是本轮修复发布，不能把其后新页当作原页连续保留。

本次发布只允许 Site Access 和英文桌面脚本，禁止重建 Supervisor、Message Bus、Enrichment、Clash 或 captcha-proxy；以新 Supervisor 基线做发布前后 instance/target 比较。生产真实结果、最终版本和遗留操作在完成发布后补记。

## 自然验收驱动的收尾

第一版 `a2c9854e` 已于12:17:55 UTC发布，52个文件哈希一致，Site Access健康；Supervisor `5042aeaf...`、备用原页`F01CB50E...` / instance `e73d9680...` 保持不变，Bus/Enrichment没有重建，英文GUI原位安装。发布前另有在线Registry备份，文件路径及摘要见`prepublish.json`。

自然发生的主身份新事件 `recovery_c4f2a6f7...` 加载了真实滑块；作业`dd_f7b62925...`、供应商任务`84215160824`在12:19:14 UTC进入PENDING，12:22:53返回`ERROR_CAPTCHA_UNSOLVABLE`。没有ready/Cookie应用/业务恢复证明，费用未知，不当0。该事件没有第二次购买，原页`7FDE4CC6...` / instance `83135655...`保留并转入人工Task。不能以此宣称新版本打码端到端成功。

本次真实观察还补上三项闭环：

1. FAILED作业不得仍显示verification_state=PENDING；供应商明确无解为STILL_BLOCKED，场景/出口变化为SUPERSEDED，技术结果不确定为INDETERMINATE。启动兼容补齐旧FAILED元数据，不重购、不伪造费用。
2. 只靠布局尺寸不够，增加原生computed style观察；CSS隐藏资源不参与判定，CSS/子DOM不可读保持UNKNOWN。HTML仅在内存中去除已确认隐藏的vendor iframe用于验证，不修改浏览器页面、不保存Cookie。
3. Reuters过去只接受recipe_result，纯原页DOM没有对应的可信listing parser，导致正常原页仍要重新导航。增加与现有recipe对应的main文章链接/日期、明确零结果以及精确query/offset契约；可见遮罩/UNKNOWN仍拒绝，主备/查询不能互相证明。人工Verify使用原Task的查询而不是固定Micron，并接纳offset=0的canonical差异，避免为证明已恢复再触发验证。

以上补充均在既有Site Access包内，无新增服务。新增回归累计27项，后续最小版本及验收结果在最终收尾补记。24小时只读验收已复用自动化24；新观察窗口为10-23 12:17:55至10-24 12:17:55 UTC，保留原10-21窗口，不因收尾发布延长截止。OpenAI Docs用于安排只读计划任务，不改变修复或购买授权；原页、费用与业务回执分别统计。

### Reuters正常零结果被误作失败（实证）

原生只读复核备用`F01CB50E...`：URL为`/site-search/?query=Ondas&offset=0`，有main但无日期文章链接，实际可见文案为`No search results match the term “Ondas”`，assessment=NONE。这不是Challenge。原recipe仅接受旧式`Search results for ... 0 results`，因此每次等待12秒后抛TimeoutError，恢复则一直拒绝零结果原页，旧记录误导为Runtime不可用。

采集recipe及纯DOM验证现在同时接受两种明确零结果；仍要求真实Reuters搜索URL与query/offset一致、页面无阻断。增加3项正反链路回归，新增用例累计30项。该recipe文件位于message_bus_v2包，但在生产Site Access浏览器Worker中执行，不修改Bus调度/消费逻辑；新增窄Dockerfile明确复制该文件，源码验收扩大为53文件，不为此重建Message Bus。正常零条是有效采集，不是伪造有消息。
