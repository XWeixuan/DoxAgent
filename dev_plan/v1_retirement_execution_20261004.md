# V1 退役与清理执行记录

执行日期：2026-10-04（Asia/Shanghai）。依据：[批准方案](v1_retirement_cleanup_final_20261004.md)。用户本轮已授权本地执行；本轮没有推送、部署、停服务、数据库维护或真实模型／交易验收。

## 结果

本地自建 ReAct／Blackboard V1 的源码、旧 Dashboard、旧 API、专用命令和默认容器装配已退出。V2 入口、current/staged v2.1、CDECR 和共享工程资源保留。源码目标清单均已确认不存在；旧 UI 的安装产物也已物理移除。

88 个历史归档目标集合中 83 个已完成项目外 ZIP 归档并验证 CRC、逐文件 SHA-256；5 个旧实验／测试目标因 Windows ACL 无法完整读取，保留且明确登记。已移出主树的历史材料包括旧设计原稿、V1 eval/Brief State、旧恢复和服务器 WIP 快照、外部框架参考副本、已结束的验收资料与九月正文诊断导出。另有 90 个 `.tmp` 根级旧 Blackboard/Dashboard 日志、截图和 tracing 分析文件按精确清单归档后移除。档案保存约 1.57 GB 的原始可读资料；这不是磁盘节省数字。

已记录的物理删除文件逻辑长度合计 **4,885,120,162 字节（约 4.55 GiB）**，包括重建缓存、旧安装和归档后的资料，不代表实际分配磁盘块。部分 ACL 限制的空目录／旧 pytest 文件尚未清理；没有改 ACL、跟随外部连接或删除公共 runtime 依赖。额外清理本轮生成的隔离基线环境不计入该历史清理数字。

## 恢复基线和提交

- 开始执行时分支：`codex/trade-execution-repair`；HEAD：`7befd54b3eb739af62e2e3c2a53e5465aeaf6665`，比方案审查基线更新。
- 当前清理分支：`codex/v1-retirement-cleanup`。
- `3574bf2a`：误跟踪的依赖／测试产物退出 Git，忽略规则和无密钥 V2 配置模板。
- `8651002d`：V1 源码退役，V2 共享依赖、测试夹具、设置／锁文件／文档入口收口。
- 历史证据、冻结输入和本报告形成独立后续提交，提交号以当前分支记录为准。
- 项目外恢复目录：`C:\Users\WEIXUANXIE\Desktop\DoxAgentArchive\v1-retirement-20261004`。
- 恢复目录中保留 `baseline.bundle`、`HEAD.txt`、原工作树和索引补丁、批准方案、详细物理操作记录、验证日志和归档文件。`git bundle verify` 已通过，包含完整历史；可用于新目录恢复，补丁在相同基线上应用。

开始时未提交的 Message Bus／Site Strategy 修改已先保全，并从清理提交排除。执行期间出现的消息源修复源码、测试、部署资料和报告也未纳入清理提交；changelog 中只暂存本次退役追加记录，保留其余本地记录。

源码解耦批可用 Git revert 单独回退。若要恢复完整 V1 工程，还需先回退第三批历史资料／旧 prompt 的删除，再回退 `8651002d`；只回退源码批不足以恢复旧 prompt 和专用资料。普通缓存重建即可，不必重新跟踪 node_modules／pytest 垃圾。历史原稿、唯一 WIP 和 ignored 资料从项目外档案恢复。不要在当前有其他修改的工作树执行 `reset --hard` 或 `git clean`。不改历史数据库、消费游标、WAL/SHM、激活 pin 或迁移记录来回退源码。

## 具体实现

1. Data MCP 的 C1/C2/C3/O4/C5 原工具上限从旧 registry 提取到 `data_runtime/tool_ceiling.py`。原 5 组集合逐项一致，数量分别为 24、19、35、12、37；节点映射、排除列表、O3 Planning 零预算及当前权限验证保留。
2. `models` 保留实际共享身份、历史 DocumentType 值和 AgentPermissions；退出旧 AgentTask、Prompt/Skill、Blackboard 与 V1 文档 DTO。monitoring 只保留共享 schema、normalizer 和 media_enrichment；保留 CDECR 历史加载和当前正文消费者。
3. 调度器保留持久化 state/audit/refresh 契约，移除旧文档 provider、自动 Blackboard 初始化、旧轮询／Runtime 分支及 DTO。V2 激活检查、初始游标、控制 epoch、Coordinator、pending Effect、流提交、故障隔离、心跳、WriterLock 和关闭逻辑保持。
4. Model Usage 保留 schema/repository/pricing/service 和当前直接记账，退出 Gateway recorder；Horizontal Collection 保留注册表、采集器和编译器，退出 Blackboard artifact adapter。独立 StockTwits 爬虫退役，V2 StockTwits adapter 和共享连接配置保留。
5. 旧 Dashboard HTTP 测试退出；混合套件保留 Codex／消息／爬虫／计费断言。通用 helper 移到 `tests/fixtures`，当前 Codex 生命周期服务只保留为测试 fixture。Phase 11 当前工具测试与受显式开关约束的真实 API smoke 保留。
6. 退役 V1 的 direct dependencies `agent-framework-core`、`anthropic`、`langsmith`，正常生成 uv.lock，只退出对应依赖树；SDK `openai-codex==0.159.3` 未升级。
7. 旧 Dockerfile／Compose 退出；完整 V2 拓扑、Site Access、Chrome Supervisor、IBKR、guardian／repair 和当前升级／修复 overlays 保留。README、运行入口、配置模板和 V2 前端固定参照约束更新。
8. CDECR 历史语料构建脚本改用现行 job-scoped HistoricalNewsLoader；未实际抓取。PGlite 0.5.8 固定测试工具移到 `tests/tools/ticker_initialization_sql`，原临时目录在离线验证通过后清理。

## 冻结输入与工程资源

W1/W2 数据集新增 `frozen_inputs`、哈希映射和 resolver，原 manifest 完全不改。5 个 O2/O3 原始输入复制后逐一匹配原哈希；5 个 prompt 当前内容已有基线漂移，从 Git blob 恢复为原哈希副本。冻结目录设置 `-text`，10 个文件的 Git 暂存字节已逐一匹配原 manifest 哈希，避免自动行尾转换破坏跨平台复现。新副本独立于当前运行 prompt，25 条数据集校验通过。相关旧临时工作区未按名称盲删。

与执行 HEAD 比对确认以下工程资产没有源码／资源变化：`src/cdecr`、当前 Codex/Runtime/repair prompts、全部 Supabase migrations、V2 API contract、`workflow_v2.1`、D2/D3 Codex 实现、V2 页面源码、现行生产 Compose/server overlay/Dockerfile.v2。保留 `.uv-python`、现有 `.venv`、V2 node_modules/dist、当前数据库和 `.control`、浏览器 Profile/凭据、待交付 spool/payload、正式 CDECR 预构建、近期部署与回退资料。构建仅重生成 V2 当前 dist；没有视觉改版。

## 验证与实际限制

| 检查 | 结果 |
| --- | --- |
| 最终离线收集 | 1,645 选中，23 个真实 API/DB/model 用例排除，零收集错误 |
| 首次完整离线回归 | 1,618 passed、20 failed、4 skipped、23 deselected，1 teardown error，688 秒 |
| 失败项退役前隔离比对 | 18 个失败和同一 offline teardown error 在原源码重现；2 个并发时序用例单独运行通过 |
| 当前失败项 + 迁移后 Codex/Runtime/Message Bus 受影响集合 | 158 passed、同样 18 failed、同一 teardown error；两项时序用例也通过，无新增失败 |
| 当前共享资源／计费／C3／爬虫／研究 lane／初始化集合 | 40 passed、20 deselected |
| V2 frontend | schema/typecheck/build/lint 通过；12 文件、57 tests 通过 |
| 导入与轻量工程检查 | 10 个 V2 入口导入通过；当前源码无退役绝对模块导入，console scripts 和保护资源存在 |
| SDK／依赖 | 锁文件正常重生成；仅旧依赖树退出，现有 .venv 未重建 |
| wheel/sdist | 均构建成功；wheel 586 项，无退役模块，全部 CDECR 非缓存源码／资源齐全 |
| Compose | 现行 production + server overlay 的 `config --quiet` 通过，没有启动容器 |
| SQL 工具 | 本地 PostgreSQL/WASM 迁移回放、单调 RPC、权限、RLS 与 invoker 检查通过；迁移文件未改 |
| 冻结语料 | 25 条输入、原哈希和业务分布验证通过 |

完整集合**没有全部通过**。18 个重现的基线失败涉及 CDECR prompt 断言、Benzinga 时间窗口、Pilot/Windows 路径、O4 配置、Reuters DOM、Event Library 校验、当前 D2 注入契约、工具 descriptor、Runtime/Broker 快照、Site Access 浏览器与 V2 wire/reference 断言。同一个 Site Strategy 用例还在 teardown 记录了被 offline fixture 阻止的 HTTP 尝试。没有通过修改 V2 业务断言、跳过测试或放宽离线边界掩盖这些问题；具体 node IDs 和原始日志随恢复档案保留。

现有 .venv 的原生扩展／权限问题仍保留。验证使用独立 `.tmp/v1-retirement-verification`，按当前锁文件安装，源码是 editable 当前工作树。复跑时可设置 `UV_PROJECT_ENVIRONMENT=.tmp/v1-retirement-verification` 后使用 `uv run --no-sync pytest --offline ...`。

Vite/Vitest 首次被父目录读取权限阻断，正常用户进程权限重试后通过。没有受影响的页面关联行为，故没有为本轮重复开展真实网页或视觉验收。真实模型、线上服务、正式数据库和真实交易未验收；本地代码退役不等于线上已切换。

## 物理未处理项

5 个归档未完成目标：`.tmp-cdecr-relevance-v2-full`、`.tmp-cdecr-relevance-v21-full`、`.tmp-cdecr-v5`、`.tmp-parent-v2`、`.tmp-verify-o3`，均为 ACL 读取拒绝。其他缓存／已归档 recovery 的受限子路径以及旧 `.pytest-codex-live-*` 下少量目录／文件保留，精确列表见下列 manifest 的 `issues`。本轮另移除约 1.62 GB 的隔离基线／伪数据测试文件；其中受权限限制的测试残留也单独登记。当前验证环境和执行审计资料保留，完整日志另有项目外副本。这些残留没有进入 current Git 和分发包，也不构成 V1 启动入口。

操作清单：

- [缓存结果](v1_retirement_cache_manifest_20261004.json)
- [源码目标](v1_retirement_source_manifest_20261004.json)
- [部署入口](v1_retirement_deployment_manifest_20261004.json)
- [归档与 SHA-256](v1_retirement_archive_manifest_20261004.json)
- [归档后及历史测试清理](v1_retirement_archive_cleanup_results_20261004.json)
- [诊断与迁移工具清理](v1_retirement_diagnostic_cleanup_20261004.json)
- [正常权限物理重试](v1_retirement_physical_retry_20261004.json)
- [旧根级文件清理](v1_retirement_root_files_cleanup_20261004.json)
- [本轮验证临时副本清理](v1_retirement_verification_cleanup_20261004.json)

详细逐文件日志项目外保存，仓库只保留目标、计数、可读字节数和未处理项，避免把大量生成物重新提交。
