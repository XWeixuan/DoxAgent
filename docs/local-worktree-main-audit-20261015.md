# 本地支线、未提交改动与 Codex 对话迁移审计

核验日期：2026-10-15（北京时间）。核验基线：本地 `main` 与已刷新 `origin/main` 均为 `618fd9c5c9a5ec4e0d1242fd3a6f57d2381a7d4e`。

## 结论

1. 用户列出的 9 个支线 worktree，在首次扫描时均无已跟踪文件的未提交修改。真正需要处理的是 **16 个独有历史提交中尚未进入 MAIN 的功能**，以及两个目录中的未跟踪运维资产。
2. `DoxChallenge`、`DoxDriverProd` 确有值得恢复的修复。原分支的离线回归用例通过，当前 MAIN 上对应能力失败；不能因为工作树干净就认为修复已经同步。
3. `DoxBarrons` 的两项业务修复已被 MAIN 以其他提交吸收；其原提交仍显示独有。独有提交数、`git cherry` 结果都不能单独作为缺失功能的判断依据。
4. Luna 是针对固定历史日期的恢复工具，维护分支额外文件是复现资料，不应直接作为当前业务功能整体合入。
5. 本轮仅进行了 Git/源码审计、离线测试及报告落盘，没有合并、提交、推送、部署、删除或迁移对话。

## 逐个 worktree 核验

下表落后数和独有数以首次清点为准。全部支线的已跟踪文件均干净。

| 目录 / 分支 | 落后 MAIN / 独有提交 | 额外未跟踪内容 | MAIN 同步建议 |
|---|---:|---|---|
| `C:/DoxBarrons` / `codex/barrons-body-intake` | 60 / 3 | 无 | 两项业务修复已在 MAIN；不用重复合并，历史验收记录可归档 |
| `C:/DoxChallenge` / `codex/yahoo-revised-headline` | 103 / 9 | 无 | 有实际遗漏；按功能移植挑战接管、正文探测、Yahoo 标题修订及驱动恢复等 |
| `C:/DoxDiag` / `codex/barrons-diagnostic` | 105 / 1 | 无 | 可补运行异常上下文日志；与 Challenge 同类日志合并为一项 |
| `C:/DoxDriverProd` / `codex/yahoo-driver-prod` | 99 / 2 | 无 | 恢复页面使用次数回收；驱动恢复与 Challenge 去重 |
| `C:/DoxLuna` / `codex/luna-maintenance-recovery` | 70 / 1 | `luna_recovery_20260929.py` | 固定日期的一次性恢复工具；保留历史资产，不作为默认业务流程移植 |
| `C:/Users/WEIXUANXIE/.codex/worktrees/bilingual-readme/DoxAgent` / `codex/bilingual-readme` | 40 / 0 | 初扫无 | 全部提交已包含于 MAIN；结束复核时目录与 worktree 登记已被外部操作移除，分支仍在 |
| `C:/Users/WEIXUANXIE/.codex/worktrees/main-convergence/DoxAgent` / `codex/w3-backtest-sync-20261015` | 0 / 0 | 无 | HEAD 与 MAIN 完全相同，无待合代码 |
| `C:/Users/WEIXUANXIE/.codex/worktrees/maint/DoxAgent` / `codex/maintenance-recovery` | 28 / 0 | `eval/event_maintenance_20261006` 下 10 条未跟踪路径 | 已提交修复全在 MAIN；额外复现/部署资料单独处理 |
| `C:/Users/WEIXUANXIE/Desktop/DoxAgent-ib-client-fix` / `codex/ib-gateway-client` | 200 / 0 | 无 | 已提交内容全在 MAIN，无遗留 IB 客户端修复待合 |

主目录 `C:/Users/WEIXUANXIE/Desktop/DoxAgent` 本身也无已跟踪文件修改。初扫有 27 条未跟踪路径，主要是已有审计报告、评估资料及 Document3 研究文件；本轮又新增本报告和审计证据。它们不能当作支线未合的应用补丁，也没有在本轮擅自提交。

## 16 个独有提交的逐项判断

| 支线 | 提交 | 内容与当前 MAIN 状态 | 建议 |
|---|---|---|---|
| Barrons | `b83edcd1` | Barrons 包装/未包装新闻卡片均要求精确发布时间；MAIN 已具备对应逻辑与回归 | 不重复合并 |
| Barrons | `d7b9026b` | `site_budget_deferred` 在既有期限内重试；MAIN 已有该分支，原队列回归通过 | 不重复合并 |
| Barrons | `bb894e63` | 仅 changelog，记录历史生产正文验收 | 按需保存记录 |
| Challenge | `7cb1cc5f` | 保留触发挑战的原文章标签页，进入人工维护并阻止身份继续业务访问；MAIN 缺少核心挑战协调流程 | 值得移植，适配当前身份/会话模型 |
| Challenge | `d23680dd` | 另一身份已有维护会话时，仍暂停当前遭遇挑战的身份；此提交仅补测试和记录 | 随挑战流程带回回归用例 |
| Challenge | `bd61de0b` | 精确 Profile 探测同样交接原挑战页；当前 MAIN 无对应完整流程 | 随挑战流程移植 |
| Challenge | `ea315ae7` | 管理员探测返回正文质量、长度等诊断，不回传正文；同时增强运行异常日志 | 诊断值得恢复；日志与 Diag 去重 |
| Challenge | `19cf3cda` | 管理员显式要求时，通过同站点、同身份、标题与 FULL 正文证据记录认证观测 | 建议与探测诊断一起恢复，保留显式认证开关和当前 session revision 绑定 |
| Challenge | `ef5661ae` | Yahoo 标题更新后，在 canonical 与摘要同文证据满足时接纳正文；MAIN 缺失 | 优先移植，保留防串文负例 |
| Challenge | `61e3c00b` | `scripts/repair_published_yahoo_body.py` 修复已发布 Yahoo 正文；含 INTC、SUMMARY 状态与文章身份限制 | 有历史运维价值，适合作为明确用途的手工工具保存；不是自动任务，不在本轮执行 |
| Challenge | `ff6ea604` | TR 固定出口示例与 Yahoo 故障诊断文档；MAIN 示例未列 TR，但当前 server Compose 已引用 DE/TR 路由 | 补齐示例与文档的一致性；无需把旧生产配置整套覆盖回来 |
| Challenge | `2d2f9de0` | Yahoo recipe 不序列化整页 HTML；Managed Playwright 驱动退出检测与服务恢复 | 优先恢复功能，与 DriverProd 的同类提交只实现一次 |
| Diag | `cf8b3ca3` | Runtime 尝试异常记录 operation/site/identity/组合/重试及堆栈；MAIN 对应异常捕获目前缺少该日志 | 低成本、有价值，建议合入一次 |
| DriverProd | `f9b8dfc0` | 驱动恢复及 Yahoo 精简载荷，与 Challenge 上述提交功能重叠 | 去重移植，不合两遍 |
| DriverProd | `fea138e8` | 按 `max_context_pages` 回收空闲 Managed context，忙碌 context 不关闭 | 优先恢复；MAIN 仅剩配置字段，缺少执行接线 |
| Luna | `e780f3e8` | 四 ticker 并行、单 ticker 日期顺序恢复；新增脚本、两条测试、记录 | 历史恢复资产，暂不引入当前业务路径 |

上述 Yahoo 标题、挑战接管、正文诊断及页面次数回收的关键符号，在当前 MAIN 的对应文件 `git log main -S ...` 中也未检出引入记录。现有证据支持“支线功能未进入当前 MAIN 历史”，不能据此断言“昨天某个提交删掉了它们”。

## 缺失功能的代码证据与回归结果

当前 MAIN 的直接证据：

- `src/doxagent/site_strategy/runtime.py:395`：Managed pool 的 `driver_ready` 未检测底层驱动进程退出码。
- `src/doxagent/site_strategy/runtime.py:468`：`page()` 不接收 `max_context_pages`；`managed_runtime.py:54` 调用时也未传入该配置。虽然 `schema.py:302` 仍有该字段，它当前不能实现按次数回收。
- `src/doxagent/site_strategy/runtime.py:1056`：Yahoo recipe 成功后仍调用 `page.content()` 读取完整 HTML。
- `RuntimeResponse` 缺少旧挑战流程的 `challenge_token` / `challenge_target_id`；`ProfileUseCoordinator` 缺少 `begin_challenge` 等协调方法。Profile 中残留挑战字段不代表功能仍完整。
- `api.py:82` 的 Profile 探测请求没有旧版正文预期标题、显式认证字段；探测响应缺少 `content_diagnostic`。
- 当前内容补全 `quality.py` / `pipeline.py` 缺少 Yahoo 已验证标题修订的判断与诊断。
- `service.py:1380` 的 Runtime 异常捕获缺少上述上下文日志。

为避免把旧测试接口变化误称为生产故障，已将原分支测试复制到独立审计目录，分别以原分支源码和 MAIN 源码运行。全程使用临时 SQLite 和浏览器桩；未启动真实浏览器、未调用模型、未触碰远端生产。

| 验证 | 原分支结果 | MAIN 结果 | 解释 |
|---|---|---|---|
| Driver：驱动退出检测、页面次数回收 | 2 passed | 两项失败 | 分别为退出后仍 ready、`page()` 不接受配置参数 |
| Challenge：另一身份维护时暂停挑战身份、精确探测交接、保留原文章页 | 均通过 | 三项失败 | 两项缺少 `begin_challenge`；精确探测桩返回旧挑战字段时被当前接口异常分类为运输失败，结合源码确认整条挑战链未保留 |
| Yahoo recipe 不读取完整 HTML | 通过 | 失败 | MAIN 实际调用了测试禁止的 `page.content()` |
| Profile 正文诊断与显式认证 | 通过 | 失败 | 响应缺少 `content_diagnostic`；该失败尚未走到认证正例，认证缺失同时由源码确认 |
| Yahoo 标题修订：同文正例、canonical 错误、摘要不符 | 3 passed | 1 failed、2 passed | 真正同文但标题变化仍被拒绝；两个防串文负例仍被拒绝 |
| Barrons 卡片时间与旧队列重试 | 未重复跑原分支 | 9 passed | MAIN 已覆盖这两项修复 |

原分支基线合计 **10 passed**。MAIN 上选中的同类历史测试合计 **9 failed、2 passed**，其中驱动测试在两个测试文件中重复了一次；去重后为 **8 个失败用例、2 个通过用例**。这不是 8 个独立生产事故，而是上述几个缺失功能的可复现证据。Barrons 独立验证另有 **9 passed**。

建议按功能移植到当前 MAIN，再恢复这些回归。不要整目录复制旧源码，也不要直接将所有独有提交无差别 cherry-pick：旧分支落后较多，当前已有新的身份共享、并发、维护及 SQLite 处理，必须保留。

## 未跟踪资产如何处理

### Luna

`C:/DoxLuna/luna_recovery_20260929.py` 是串行恢复脚本，已提交版本 `scripts/luna_recovery_parallel_20260930.py` 改为按 ticker 并行。两者固定 BE/INTC/MU/RKLB 和 2026-09-11 至 2026-09-28，操作旧版 runtime task/control 状态。

未跟踪串行脚本在模块顶层创建 `RuntimeJournal`，导入并不等同于只读检查。本轮仅阅读文本，没有导入或运行。后续撤销 worktree 前，把这个唯一未提交脚本与已提交并行版本保存为一份历史恢复资料即可，无需为此设计新的通用恢复系统。

### Maintenance

`C:/Users/WEIXUANXIE/.codex/worktrees/maint/DoxAgent/eval/event_maintenance_20261006` 包含 badcase/replay SQLite、before/baseline/bundle、ZIP、部署 tar 和 replay JSON 等。该目录整体 920 个文件，约 **226.9 MiB**；其中同时含已跟踪和未跟踪文件，该大小不是“未跟踪文件总大小”。

这些资料用于旧故障复现和部署留痕，不是漏合的源代码。可保留必要的最终复现记录，其他可重建中间产物在后续清理时处理；不建议把数据库和打包产物批量提交到 MAIN。当前审计没有证明每份 SQLite 都是唯一业务原始数据，也没有将它们认定为可直接删除。

本轮 Git 变更审计不构成 ignored 数据盘点。未来真正删除目录时，需要额外核对其中的 `.env`、认证状态、浏览器 Profile 等未被 Git 管理的资产；对话迁移本身不需要先删除这些 worktree。

## 能否把其他 Codex 对话切回 Local MAIN

**可以操作其他受管理的本地 Codex 对话的 Handoff，但不能承诺一次调用把所有对话任意指定到 MAIN。**

工具可将另一个对话及其 Git 状态在 Local checkout 与 Codex worktree 之间交接；正在运行的对话会被中断。工具没有目标分支参数，且不能迁移调用它的当前对话，也不支持云端对话迁移。本对话当前已在本地 MAIN。

官方说明指出，Handoff 会交接关联的分支/代码状态，因此回到 Local 可能让本地主目录切到原 feature branch；它不天然保证最终是 MAIN。同一 Git 分支也不能同时正常检出到多个 worktree。正确目标是让这些对话使用同一个 `C:/Users/WEIXUANXIE/Desktop/DoxAgent` 的 MAIN checkout，而不是让九个目录都 checkout MAIN。[官方 Git worktree / Handoff 说明](https://learn.chatgpt.com/docs/environments/git-worktrees)

当前取得的最近 50 条对话清单中，多个 DoxAgent 对话的 cwd 已是本地主目录，例如“W3编排与回测”“勘察正文补全与消息源模块 (2)”“编写并同步中英双语 README”。旧 worktree 仍存在，不代表原对话仍以它作为当前 cwd。清单中尚未完整匹配 `C:/DoxBarrons` 等五个外部路径的归属；不能凭目录名认定对话，或批量切换未核实的对象。

另外，少数 C4 Pilot 对话 cwd 是 `eval/.../workspaces/...` 的任务目录，它们不是用户列表中的 Git worktree。其相对输入输出受任务约定约束，不应顺带改到根目录。其他项目及云端对话也不在这次 DoxAgent 收敛范围内。

## 建议的执行顺序

1. 先在 MAIN 上逐项恢复 Yahoo 标题修订、Managed 驱动检测/恢复、recipe 精简载荷、页面次数回收、挑战接管及探测诊断/显式认证，并去重补日志；补必要的出口示例和操作文档。完成当前接口适配与对应回归后再提交。重要代码修改按仓库规则追加 changelog。
2. 保存 Luna 唯一未提交脚本、必要历史运维工具和维护复现资料；无需把中间数据库和部署包都进 Git。
3. 根据真实对话关联，逐个将仍使用 managed worktree 的本地 DoxAgent 对话交接回 Local，每次核对 cwd、HEAD、分支及未提交差异，最后确保根目录仍为 MAIN。外部手工 worktree 是否能交接，要以对应对话的实际绑定为准。
4. 再撤销没有唯一代码/数据且不再被对话使用的 worktree。已吸收的分支无需继续占用 checkout；目录退休与是否保留分支引用可分开处理。

多个对话共用 Local MAIN 后，也共用文件、暂存区及工作树状态。代码写入和提交应协调执行，避免另一个对话把尚未完成的修改一起提交；不需要因此保留所有旧 worktree。

## 审计证据

证据目录：`C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/local_worktree_audit_20261015`。

- `inventory.json` 与各 worktree JSON：分支、HEAD、祖先、独有提交文件、状态与 `git cherry`。
- 16 份提交 patch 和摘要，以及各 worktree 的未提交源码 diff（均为空）。
- `regression-results.txt`：MAIN 上历史回归失败/通过详情。
- `driver-baseline.txt`、`challenge-baseline.txt`：原分支基线通过记录。
- `barrons-main-results.txt`：MAIN 已吸收 Barrons 修复的通过记录。
- `test_historical_*.py`、`verify_historical.py`：复制的历史用例和离线基线运行方式。

结束复核时，本地 MAIN 与 origin/main 仍为 `618fd9c5`，已跟踪文件仍干净；首次扫描与结束复核之间唯一观察到的 worktree 清单变化是 bilingual-readme 被外部操作移除，本轮没有实施该操作。
