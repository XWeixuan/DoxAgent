# Site Access 支线修复收敛回执

日期：2026-10-15（北京时间）。基线：`618fd9c5`。本轮按用户授权提交到 MAIN 并推送 GitHub，同步本地代码；不拉取、构建、重启或部署远端生产。

## 已整合的功能

- Yahoo 发布后标题修订：只在同 canonical 文章和 provider 摘要正文交集证据成立时接受新标题，继续拒绝不同文章和摘要不符的正文。
- Yahoo 页面 recipe：返回已有结构化结果，不额外读取和回传整页 HTML。
- Managed Playwright：真实驱动退出时 readiness 失败，维护循环终止 Site Access owner，交由既有容器重启策略恢复；不放大 Node 堆限额。
- Managed context：接通已有 `max_context_pages` 配置，统计打开页数，仅在无活动页面时回收；默认不启用，不更换 Profile/Cookie/出口。
- External Chrome 挑战：保留触发的原标签页与 target，立即暂停共享身份业务，停止本次 fallback；人工处理期间卸载自动导航拦截，验证原文章时不再次导航。
- 挑战维护适配：保留 MAIN 的异步普通登录窗口；原挑战页报告 READY；重连失败仍保留暂停和原 target，错误的站点绑定在修改维护状态前拒绝。
- 管理员 Profile 探测：返回正文质量诊断，不返回正文；只有显式请求、预期标题与 FULL 正文证据满足时才写入该站点/Identity 的认证观测。
- Runtime 异常日志补上下文；补齐 TR 固定出口示例与 Yahoo 页面爬虫诊断文档。

移植来源：DoxChallenge 的 `7cb1cc5f`、`bd61de0b`、`ea315ae7`、`19cf3cda`、`ef5661ae`、`2d2f9de0`、`ff6ea604`，以及 `d23680dd` 的回归；DoxDriverProd 的 `fea138e8`。`f9b8dfc0` 与 Challenge 驱动/载荷修复重叠，未重复移植。

没有将旧源码目录整体覆盖 MAIN。保留当前 SQLite 存储异常传播、浏览器取消/超时清理、异步维护导航以及其他站点的现有行为。一次性 Yahoo 已发布正文修复工具和 Luna 恢复脚本不纳入本次应用功能同步，也未执行。

## 验证

- 恢复原分支的标题修订正反例、驱动退出、空闲回收、挑战原页交接与探测认证回归。
- 新增挑战不继续 fallback、重连失败保留暂停/target 两项当前接口集成回归。
- 最终相关 Site Strategy、Site Login、Source Maintenance、Content Enrichment 套件：**227 passed、1 skipped、1 deselected**，52.50 秒。明确排除的唯一用例是下述已在未修改 MAIN 上复现的既有归档时钟失败；首次扩大运行未排除它，结果为 225 passed、1 skipped、1 failed。
- Ruff 静态检查及 Git diff 空白检查通过。
- 所有验证离线运行，使用浏览器桩和临时 SQLite；未操作真实登录状态或远端生产。

扩大验证发现既有失败：`tests/test_source_maintenance.py::test_closed_evidence_archived_after_thirty_days_not_active_thread`。已用 `git archive HEAD` 提取未修改基线源码，独立运行同一用例，仍失败。用例使用固定 2026-10-05 时钟，而 Incident 默认更新时间使用真实时钟，造成模拟 31 天后的归档截止日期与实际更新时间不一致。本轮不改这项无关测试/归档逻辑；失败证据保留在本地审计目录的 `unmodified-head-baseline-result.txt`。

## 对话 Local MAIN 核验

同时检查 Codex 对话工具清单和本机 SQLite 对话元数据，后者以 `mode=ro` / `query_only` 打开，未修改应用数据库。

- 本机元数据中有 492 条 Dox 相关历史对话记录；没有任何记录的 cwd 指向本次列出的旧支线 worktree 或 `.codex/worktrees` 路径。
- 本对话 cwd 已是 `C:/Users/WEIXUANXIE/Desktop/DoxAgent`，当前实际分支为 MAIN。附件 `main-convergence/DoxAgent` 并不改变对话当前执行目录。
- 多个其他 DoxAgent 交互对话同样已经使用主目录。对已处于 Local 的对话不执行 Handoff，因为该工具会在 Local/worktree 之间切换，可能反向创建 worktree。
- Pilot 的专用输入输出目录、其他项目、云端 PDF 对话不是本次旧支线 checkout，未迁移到 DoxAgent 主目录。这里的“全部”指本次 DoxAgent Git worktree 收敛范围，不修改其他项目的工作目录。
- 当前工具不能对调用它的本对话执行 Handoff；本对话已在 Local MAIN，所以无需该动作。旧目录和附件均未因本轮核验而删除。

复核依据：实际执行 cwd + 主目录 Git 分支/HEAD，而不是仅依据附件名或历史 `git_branch` 元数据。对话之间以后共享同一个 Local 工作树，应协调代码写入与提交。

## 本地审计证据

`eval/local_worktree_audit_20261015/`：初始 inventory/patch、原分支基线、首次/适配后测试输出、Ruff 结果、未修改基线失败复现，以及只含 ID/cwd/时间/归档标记的本机对话目录清单。该目录保留本地，不批量提交历史数据库、私有状态或无关评估资料。
