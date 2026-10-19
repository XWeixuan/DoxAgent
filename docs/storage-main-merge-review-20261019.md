# 存储治理分支与最新 main 的合并审查

日期：2026-10-19。范围为本地差异审查、冲突适配和完整 Git 合并；不包含推送、重新部署或存量清理。

## 比较基线

- 合并前本地 main、origin/main 和本次 fetch 的远端 main：`35d1057b8c5ae97d928e123273c5a25ef485ada8`。
- 存储分支：`codex/storage-lifecycle-v4`，审查起点 `8c20f37d07b56a13253442a6b16d5344dbe8405e`。
- 共同祖先：`d8a7fee9e77ca76a828ec788d185f8e1688ce327`。
- main 一侧修改 144 个文件，存储分支修改 106 个文件，重叠 20 个；真实预合并出现 6 个文本冲突。
- 主工作区有一条未提交的 SNOW O4 changelog 补记及未跟踪运行材料。它们不是本轮产品合并内容，独立保留，不进行全目录 stash/reset 或证据目录清理。

## 文本冲突与解决

| 文件 | 冲突原因 | 采用的组合 |
| --- | --- | --- |
| `Dockerfile.v2` | main 新增 tiktoken/o200k_base 预热；存储分支拆分稳定依赖和应用层 | 保留 tokenizer 缓存目录及预热；置于 backend-deps，提交身份仍只进入应用层；保留主分支最新 pyproject/uv.lock |
| `initialization_repair/git_workspace.py` | 两边均修复 bare mirror fetch，注释/格式不同；存储分支另加 retire | 使用 main 明确的 remote-tracking refspec/说明，保留 clean-only worktree 退出及可恢复 repair branch |
| `initialization_repair/guardian.py` | 退出调度字段与 main 新版基础设施重试历史相交 | 保留 main external auth、retry-agent 历史、分次恢复日志及唯一 Issue ID；加入原存储 owner 的终态退出补偿 |
| `tools/providers/base.py` | 同一惰性 HTTP client 修复的不同注释 | 采用 main 完整实现和注释；不回退到 eager TLS client |
| `test_initialization_repair_git_workspace.py` | 两边在同一测试尾部追加不同保护场景 | 保留 main 新部署分支抓取和远端删除后的活跃 worktree 保护；随后验证 clean checkout 退出、branch 保留，以及 dirty incident 不退出 |
| `changelog` | 两侧都追加操作与变更记录 | 完整保留 main 内容，追加存储分支独有记录；主工作区原未提交 SNOW 补记单独恢复 |

没有使用整文件 `ours/theirs` 批量覆盖业务实现。

## 自动合并与业务能力检查

初次冲突解决后，合并索引与两侧 Git blob 的比对确认：124 个 main 独有修改文件、86 个存储独有修改文件均完整保留。随后回归发现需要适配一项 main 新增测试：其 fixture 仍要求 V4 保存已不必要的 Worker receipt。最终只有该测试增加“V4 薄格式/旧完整格式”双分支验证，其余 123 个 main 独有文件保持原样；存储侧仅执行报告追加本次集成说明，其余 85 个独有文件保持原样。没有修改 main 独有产品逻辑。重叠文件另逐项审查，不能仅凭“Git 无冲突”判定兼容。

保留并验证的关键边界：

1. main 的 W3 V5、Research MCP、market preparation、ready slots、Fork/Resume、资源 owner、离线 replay 的无上游调用约束、W2 R1 provider/显式缓存及客户端关闭语义保留；未移回旧 W3 工作流。
2. Worker 的最新后台准备和 lifespan 关闭顺序保留；存储 owner 只增加既有生命周期挂接。Session 退出继续保护整个引用树、未删除的 W3 registry、模板、在途及 W3 Case；不替代 main 自身的 ready-slot 生命周期。
3. Workspace 仅对 `init-`、`d2ws-`、`rerun-` 的初始化快照共享不可变内容；main 的 W3 独立目录/研究资源机制保留，fork 可写副本不与快照共享 inode。
4. main 的 Initialization 单节点查询、top-level 调度、冻结计划校验、投影字段读取及 Document3 的并发兄弟收尾语义，合并后与 main 相应文件内容一致。
5. Guardian/Agent 外部登录鉴权、worker-secret 挂载、基础设施失败后按原轮次恢复、预算与日志审计不回退；只有已成功/取消、超过期限、无活动容器且 checkout clean 的维修工作区参与退出。
6. main 的 Site Access/Reuters 完整正文、维护会话、Challenge/Viewer、IBKR、模型与依赖配置保留。Read V4、四源运输退出、发布器自动资源退出、选择性备份等存储改动整体保留。

Docker 静态检查确认提交号不进入 backend-deps，main 的 tokenizer 预热保留。由于 main 新增依赖及该预热改变了真实依赖输入，未来首次依赖构建产生新依赖键是预期行为；后续普通提交仍复用它。

## 验证结果

扩大回归最终为 **599 passed、1 skipped、1 deselected**（294.84 秒），其中 deselected 为下面在原 main 独立复现的既有时钟测试。前端 15 个测试文件、66 项通过，TypeScript 类型检查通过；针对进度读取、Guardian/worktree 的 14 项回归通过。78 个 Python 文件语法、3 份 Compose 解析、冲突文件 Ruff F、Git 空白检查及离线 uv lock 检查（110 packages）通过。

初轮扩大回归为 597 passed、1 skipped、2 failed：Read fixture 与薄存储的形状冲突已修正，保留 legacy 完整 native 行的按需读取/所需内容完整性检查，并增加 V4 无需读取外部 receipt 的断言；适配及 Guardian/worktree 组合 14 passed。另一项 `test_closed_evidence_archived_after_thirty_days_not_active_thread` 已在原 main `35d1057b` 独立复现，系固定测试 clock 与真实 `updated_at` 不一致；相关实现和该测试在本次合并均保持 main 原样，不伪造全绿，也不在合并任务中改写其业务策略。

本轮不把本地回归当成新版本生产验收；此前生产部署回执仍对应其记录的精确提交。

## 合并与本地内容保留

先在原存储 worktree 中完成 main 的适配和验证，再由本地 main 以完整 merge commit 接收整个分支，保留两条历史以及 main 的第一父提交路径，不做 squash/cherry-pick 子集。

主工作区只临时移开会阻挡合并的既有执行报告，并对唯一的未提交 changelog 做逐字节快照和三方内容核对。两边末尾追加造成的日志文本冲突，按唯一相邻上下文插回原 SNOW 段落；合并后该补记仍为未提交修改。其他未跟踪材料不移动、不纳入提交。

原始差异、保留验证、测试命令/XML/日志及未提交内容快照在 `D:/DoxAgentStorageExecution/main-merge-20261019/`。最终提交和本地/远端关系以最终 Git 回执为准。
