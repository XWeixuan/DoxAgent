# 远端临时发布目录、修复遗漏与 MAIN 收敛调查

调查日期：2026-10-14，Asia/Shanghai。远端快照起点为 21:38 左右；以证据 JSON 的 UTC 时间为准。

## 结论

用户怀疑成立，但不是所有旧目录中的差异都代表遗漏。已经确认三条不同路径：

1. **分支未合入。** GitHub `origin/main=83d030a5`；当前线上主应用版本 `8a8f60f2` 位于 `origin/codex/v1-retirement-cleanup`，相对 MAIN 有 28 个独有提交。MAIN 另有 1 个独有的双语 README 提交，应保留。`codex/maintenance-recovery` 的 9 个独有提交没有进入当前发布分支；其中存在实际回退。
2. **修复只在生产覆盖层及本地脏树。** 当前 Worker 的 writer 交接与 W3 Data MCP 启动预算两处修复没有进入 `8a8f60f2` 或 MAIN。按原提交重新构建 Worker 会丢失它们。
3. **代码已同步，部署配置未同步。** Yahoo DE/TR 后备路由代码在当前镜像中，但启用它的 `DOXAGENT_MESSAGE_BUS_V2_YAHOO_HTTP_ROUTES` 在当前容器和 `.env.v2` 中均不存在；旧 `source-repair-20261014` overlay 明确设置了该值。

实时状态也修正了历史描述：当前正式 Compose 服务大部分已统一为 `release-8a8f60f2`，不再是 10/12–10/14 窄发布时期的多套旧应用基线。仍存在 Worker 覆盖层、4 个长期运行的隔离研究 Worker、多个已退出测试容器和旧修复模板。Web、Chrome、Site Access、Guardian 使用不同专用镜像是正常装配；目标应是同一 MAIN 提交的应用源码和可追溯构建，而不是强迫所有服务使用同一种镜像。

本次仅进行了远端只读取证及本地隔离验证；未合并、推送、重启、删除远端目录或修改生产配置。下面给出具体整合与退出顺序，不把调查结论写成已完成部署。

## Git 与实际部署

| 对象 | 核实结果 |
| --- | --- |
| 本地主检出 | `codex/v1-retirement-cleanup`，HEAD `8a8f60f2fb03a43fd9a9eb4cabb548736ee09593`；存在 W3/回测等未提交工作，本调查未修改这些文件 |
| GitHub MAIN | `83d030a59c6fb60325cb0144742cd204b8802f8d`；已执行 fetch 核实 |
| 远端 `/home/ubuntu/doxagent` | 分支 main，HEAD `b0f1caf50060b34483f13020a9d173ef57933afa`，9/28 提交；32 个 tracked 修改项，14 个 untracked 顶层状态项；不能直接 pull/reset |
| 当前生产发布检出 | `/home/ubuntu/doxagent-release-8a8f60f2`，detached HEAD；应用源码无 tracked 修改，存在未跟踪 deployment 配置及构建文件 |
| 正式服务 Compose | `/home/ubuntu/doxagent-release-8a8f60f2/deployment/compose.json` |
| Worker Compose | 上述文件加 `/home/ubuntu/doxagent-w3-writer-repair-20261014/compose.writer-fix.json` |
| 当前 Worker 镜像 | `doxagent-v2:w3-writer-mcp-fix-20261014` |
| 其他正式后端 | `doxagent-v2:release-8a8f60f2`，共享同一应用镜像 |
| 正式 Web / Site / Chrome / Guardian | 各自的 `release-8a8f60f2` 专用镜像 |

核对 API、Worker、Site Access、Chrome Supervisor、Guardian 五个运行容器，每个取得 754 个源码/提示词文件 SHA256。与 Git HEAD 比较时统一换行：API/Site/Chrome/Guardian 全部一致；Worker 仅 `codex_worker/sdk_runtime.py`、`codex_worker/thread_lifecycle.py` 不同。此项证明代码来源，不等于全部业务路径已经验收。

## 确定需要整合的修复

| 修复 | 来源与当前缺口 | 应如何整合 |
| --- | --- | --- |
| Event Library 局部错误降级 | `502f83a7`；当前 validator 仍把未知稳定 ID、重复 Fact、跨 Event 移动等局部问题升级为整包 FAIL | 恢复局部 Event 取消、原 Published owner 保留、关联 Delta KEEP_PENDING、健康内容 PARTIAL 发布；带回 importer effective 诊断及幂等重放语义 |
| MAINTAIN 分阶段失败预算 | `83bfefd3`；当前 journal 没有 `MaintenancePhaseFailure`、phase_failures，仍使用全局次数与固定 5 秒重试 | 将原有按阶段有限预算迁入当前 journal，保留当前 SQLite contention 日志和新接口 |
| 已确认超时的有限恢复 | `f14e8926`；ReceiptWorker 缺少 `recover_timeouts` | 仅终态、已清理的 `CODEX_TURN_TIMEOUT` 恢复采用有限扩大预算；保留当前 W3 task_phase/Case 身份规则 |
| 已成功 O3 工件恢复 | `d63612b3`、`0c03c956`；ReceiptWorker 缺少 `recover_artifacts` 和 `_recover_o3_patch` | 按同 run/attempt 的已成功回执恢复 schema-valid patch，避免重跑已完成任务；需适配当前 v2.1 O3 工件合同，不能直接移植旧 v2 文件路径而不核实 |
| 维护错误归因与成功收据保留 | `502f83a7`、`18caaaf1`；当前缺少 execute/validate/publish 分阶段标记 | 发布/验证失败不能拒绝先前成功 Worker 回执；保留 LeaseLost/ControlError 边界 |
| 本轮 resolved 限定 | `502f83a7`；当前维护查询仍按 runtime_scope 聚合历史 RESOLVED | 限定本轮 source_snapshot，避免历史结果被计入当前消费 |
| FAILED/HELD parent 的 dirty 通知 | `502f83a7`；当前 coordinator 只保护 PENDING/RUNNING parent | 恢复 FAILED/HELD 保留，供明确恢复后继续处理 |
| W1 语义日读取 | `502f83a7`；当前 service/repository 仍按 visibility 决定是否读历史未处理 provisional | 按 Case 固定 trading_date 读取，保留当前 Case/W3 新合同和身份计算 |
| Barron's 无包装、无日期卡片 | `codex/barrons-body-intake` 的精确时间修复只部分吸收；当前仍是 `if wrapped and basis != "EXACT"` 与 `if wrapped and candidate_count` | 两处精确时间规则适用于所有页面形态，带入 undated regression；不回退当前 source_name 等增强 |
| W3 writer 交接 | 远端 `doxagent-w3-writer-repair-20261014/thread_lifecycle.py`；在运行 Worker，未提交 | 生命周期 RPC 与关闭共锁，fork 后释放客户端 writer；只提取生产最小修复，避开本地正在进行的回测配置改动 |
| W3 Data MCP 冷启动 | 同目录 `sdk_runtime.py`；在运行 Worker，未提交 | W3 启动预算 90 秒，其余节点原预算保留；连同现有权限/required 回归整合 |
| Yahoo 批准 HTTP 路由启用 | `source-repair-20261014/compose-v2-message-bus.json`；源码有，当前容器及 env file 无 | 将 `["de-standard-1","tr-standard-1"]` 纳入正式受管部署配置；切换前复核实际 listener/egress 映射，不能仅凭旧 route 名判断国家 |

`codex/maintenance-recovery` 尚未合入的提交清单为：`83bfefd3`、`f14e8926`、`d63612b3`、`0c03c956`、`40d83af9`、`502f83a7`、`18caaaf1`、`e1f03f9d`、`3206f63a`。其中包含验收文档提交；不需要把旧 Docker 覆盖部署方式一并恢复。

### 已完成的隔离复现

- 从 maintenance 分支提取 5 个纯本地 Event Library 回归用例。当前源码：**5 failed**；分别出现 FACT_IDENTITY_MOVED、UNKNOWN_STABLE_FACT、UNKNOWN_STABLE_EVENT、DUPLICATE_FACT_IDENTITY 及全局拒绝行为。
- 在 D 盘独立的 HEAD 源码快照中，仅替换 `validator.py`、`bundle_io.py`、`importer.py` 为 maintenance 分支版本，同样用例：**5 passed**。证明该组局部降级修复有价值，未修改主检出/生产。它不是全量分支兼容验收。
- 无包装 Barron's 历史无日期样本：当前解析 **2 条 UNKNOWN_FIRST_SEEN**；隔离快照只修改两处条件后 **0 条**。
- Yahoo 配置：旧 overlay 有两条路由；当前容器 key 缺失，当前 `.env.v2` 也无该 key。当前代码会因此走 legacy 单路路径。未对消息源发起额外轮询，不能从此推断当前每个 ticker 都已发生采集故障。

## 已吸收或不应整份覆盖的旧内容

- SDK 0.159.3 与模型兼容映射、市场新闻源、消息源登录维护/HTTP 后备核心代码、Service Busy 生命周期、存储归档/备份保留、v2.1 流程、Runtime 标题搜索等已进入当前提交的相应实现。它们多数仍未进入 MAIN，因为整个发布分支未合入。
- `site_budget_deferred` 的有限正文重试当前已存在，不能把 Barron's 整个分支都标成“全部丢失”。缺的是另一条无包装精确时间路径。
- SWEEP 优先于实时调度的顺序当前已存在，maintenance 分支与当前 bus 文件的其他差异还包括新的异步 SQLite 访问；整份覆盖会回退这些优化。
- disk-repair 快照比当前代码少了 policy-query-indexes、部分 W3 outbox 排除和 receipt-packs 备份覆盖，属于较早阶段，不能用旧文件覆盖新代码。
- source-repair-20261014 的部分差异只是字段顺序/格式，其 Web 源码还早于当前可读性/指标重构；不能将旧 dist 直接搬回。
- 远端 `doxagent` 的 settings、pyproject、uv.lock、API views、部分正文服务变更，经三方比对已被当前 HEAD 吸收。其余大文件冲突需按上表的具体语义审查，不能把“不同”直接解释为“缺失”。
- 大量旧 worktree 的数千个 deleted 状态来自历史追踪的 `exports/.../node_modules`；这不是数千项未提交 bug 修复。

## 部署构建中另有需要保留的内容

当前提交的 `Dockerfile.v2` 固定下载 IBKR SDK 1049.02；最近正式构建的依赖回执记录该官方归档已 404。远端 `deployment/Dockerfile.backend` 实际从已部署的 approved SDK 镜像提取同版 ibapi，另安装 `protobuf==5.29.5`。当前 Chrome 构建同样复用了 approved Chrome OS 层，因为固定 153.0.8010.52-1 包当时已不在 apt 源中，再用新应用目录替换旧 `/app`。

这些是发布的可重建性缺口，不能把整个 deployment 当临时垃圾删掉后才发现 MAIN 无法重建。应将依赖来源、固定版本/校验和与可用构建路径纳入正式配置；保留凭据在受限外部配置中，不把生成的含环境值 Compose 整份提交。

## 目录清单与清理条件

下面列出 `/home/ubuntu` 下本次发现的 35 个非隐藏目录。容量为逐目录 `du -sx`，合计约 5,826 MiB；其中主检出约 808 MiB、当前发布检出约 900 MiB。共享 Git 元数据等可能跨目录重复计算，不能直接当作实际回收承诺。归类是退出条件，不是已获准删除的最终清单。

| 目录（均在 `/home/ubuntu/`） | MiB | 用途 | 退出条件/处理 |
| --- | ---: | --- | --- |
| `barrons_ticker_fix_20260930` | 0.19 | Barron’s ticker/正文与行业源早期覆盖发布 | 先提取精确时间遗漏，其余按当前源实现复核 |
| `disk-repair-20261013` | 0.65 | Bus/Read 冷归档、GC、备份留存、Guardian CLI | 多数代码已吸收；保存回执后可退出旧覆盖层 |
| `doxagent` | 807.85 | 9/28 main 脏检出，环境/secret/systemd 入口 | 保留根与运行配置，整合后更新 MAIN；不整目录删除 |
| `doxagent-acceptance` | 0.04 | 9 月 Site Access 只读验收回执 | 验收资料；不属于待合并代码 |
| `doxagent-backups` | 14.31 | 历史备份/恢复资料 | 核实恢复集归属；本次不按代码垃圾删除 |
| `doxagent-barrons-egress-build` | 362.09 | Barron’s/消息源出口构建 worktree | 先确认源补丁吸收，再按 worktree 退出 |
| `doxagent-be-v21-20261011` | 296.73 | BE v2.1 正式隔离研究检出与启动器 | 对应 Worker 仍 Up，先查队列与退出条件 |
| `doxagent-case-release-d11f7fa1` | 363.65 | Case 明细、导出、前端历史发布 worktree | 当前正式服务已迁出；复核派生 UI 补丁后退出 |
| `doxagent-case-template-20261001` | 2.12 | Case 模板、Policy 可见性/KPI 发布产物 | 提取必要源改动；不回灌旧 dist |
| `doxagent-challenge` | 357.52 | Yahoo/challenge/正文策略 worktree | 后续源码已扩展；按补丁语义核实后退出 |
| `doxagent-d2-download-20261002` | 2.08 | Document2 JSON 下载发布产物 | 功能已有 MAIN 提交 47f6515f；退出旧产物 |
| `doxagent-driver-prod` | 357.78 | Managed browser context/driver 恢复 worktree | 复核当前生命周期实现后退出 |
| `doxagent-gateway-viewfix-20261003` | 2.07 | Gateway 状态 UI 构建包第一版 | 只有 dist/发布配置，不能整份当新修复合并 |
| `doxagent-gateway-viewfix2-20261003` | 2.09 | Gateway 状态 UI 构建包第二版 | 与当前 UI 合同对照后退役旧产物 |
| `doxagent-intc-v21-20261011` | 504.54 | INTC v2.1 正式隔离研究检出 | 对应 Worker 仍 Up，先查队列与退出条件 |
| `doxagent-luna-recovery` | 361.52 | Luna 维护恢复 worktree | 维护修复依赖遗漏需整合；secret 引用另核 |
| `doxagent-maintenance-20261003` | 0.23 | 3 文件 MAINTAIN 恢复覆盖补丁 | 价值明确，按当前 v2.1 合同移植后退役 |
| `doxagent-mu-v21-20261010` | 296.26 | MU v2.1 隔离研究及 D3/C4 恢复 | Worker Up，退出容器仍 bind mount；先完成退役 |
| `doxagent-policy-recalled-20261001` | 2.13 | Policy recalled 展示/旧 Web 发布包 | 与当前 Policy UI 对照，保留当前新合同 |
| `doxagent-release-8a8f60f2` | 899.81 | 当前正式应用检出、Compose、Guardian-source | 当前活跃依赖，MAIN 完整切换前禁止删除 |
| `doxagent-release-f3da669b` | 361.91 | 交易结果/投影/消息发布 worktree，实际 HEAD 已 56094655 | 当前生产已迁出；确认历史修复吸收后退出 |
| `doxagent-rklb-v21-20261012` | 504.21 | RKLB v2.1 隔离研究检出 | 对应 Worker 仍 Up，先查队列与退出条件 |
| `doxagent-runtime-search-20261009` | 3.64 | Runtime 标题搜索 API/Read 覆盖发布 | bdbd7eb3 已入当前发布分支，MAIN 尚未纳入 |
| `doxagent-stage` | 283.15 | 早期实验、部署包、SDK 状态及 auth 等混合暂存 | 含敏感/状态材料；需分项核实，不能整目录盲删 |
| `doxagent-store-recovery-20261002` | 3.29 | STORE_UNAVAILABLE/API/Read 并发恢复覆盖发布 | 多数代码已吸收，复核后退出旧构建目录 |
| `doxagent-ui-validation-20261014` | 0.70 | v2.1 UI 查询/采样/验收临时脚本及源快照 | 验收目录，不是服务发布入口；保留需要的小回执 |
| `doxagent-w3-writer-repair-20261014` | 0.24 | 正在运行的 writer/MCP 两模块覆盖修复 | 未提交，必须整合且解除 Worker Compose 引用后退出 |
| `event-maintenance-20261006` | 0.45 | Event Library 局部降级及维护覆盖发布 | 存在确定遗漏；先移植后退出 |
| `service-busy-repair-20261013` | 0.21 | Site/API SERVICE_BUSY 生命周期修复 | 本次映射的修复文件与当前 HEAD 一致；旧覆盖可退出 |
| `source-repair-20261004` | 0.58 | 消息源重复性恢复/登录与采集早期覆盖 | 较早快照，按后续版本吸收而非整份覆盖 |
| `source-repair-20261006` | 0.55 | 消息源运行时后续修复 | 较早 runtime/service；按后续版本吸收 |
| `source-repair-20261009` | 0.65 | 消息源存储/正文/客户端窄发布 | 多数有后续实现；复核后退出 |
| `source-repair-20261012` | 3.32 | 多源恢复及逐服务 baseline/staging | 基线快照不是补丁；保留后续新实现 |
| `source-repair-20261014` | 6.89 | 登录维护、HTTP 后备、延期状态与四服务发布 | 代码多数已吸收；Yahoo route 配置确定遗漏 |
| `v21-production-20261012` | 22.98 | v2.1 生产采用、源快照、smoke/发布收据 | 退出 smoke 仍挂载；不要误删已激活业务原件 |

### 当前明确依赖

- Guardian 活跃 bind mount：`doxagent-release-8a8f60f2/deployment/guardian-source -> /repair-source`。旧发布根不能现在删除。
- 正式服务依赖上述 deployment Compose；Worker 还依赖 writer-fix Compose。已构建镜像虽不从源码目录实时加载，也需要迁移运维/重建入口。
- 正式 Site/Bus/Enrichment/O4 的 token bind mount 在 `/home/ubuntu/doxagent/deploy/secrets`；不属于代码清理目标。
- systemd `doxagent-resource-safety.service` 从 `/home/ubuntu/doxagent/scripts/resource_safety_controller.py` 启动。
- 4 个隔离研究 Worker（MU/INTC/BE/RKLB）仍 Up。需确认其真实队列/任务终态，再退役 Worker 与对应测试资源，不能据“测试完成文档”代替实时检查。
- 已退出的 MU 研究容器仍 bind mount `doxagent-mu-v21-20261010`；3 个已退出 smoke 容器仍 mount `v21-production-20261012`。清理时同步退役精确旧容器，保留业务 named volumes。
- 两个旧 initialization-repair-template 容器处于 Created，可能被按名字动态复用；切换正式 repair-agent/模板配置之后再退役。
- `.env.v2`、secrets、浏览器 Profiles、生产数据库、Codex sessions、已激活研究原件及正式恢复集不因本次“目录统一”而删除。`doxagent-stage` 包含 auth/SDK 状态及归档；仅凭名称不能整目录当低价值副本处理。

当前根盘约 177 GiB、153 GiB 已用、17 GiB 可用（91%）。容量只作为发布余量信息；不扩大为另一轮数据库或 Docker 全局清理。

## 推荐实施顺序与验收

1. 在独立整合分支以最新 `origin/main` 为目标，纳入当前已上线的 `codex/v1-retirement-cleanup` 提交，保留 MAIN 独有的双语 README。不要从远端 9/28 脏 main 直接 reset/pull。
2. 逐项移植 maintenance 遗漏、Barron's 两处条件及已上线 Worker 两模块补丁。维护修复按当前 v2.1/W3/Policy 消费合同适配；本地回测未完成改动不夹带。重要代码修改追加 changelog。
3. 将 Yahoo route 与发布依赖构建修订纳入正式部署入口。从 MAIN 根读取唯一受管 Compose，发布明确 commit 标签的应用镜像和专用镜像；不继续累加临时 overlay。
4. 定向回归覆盖 Event 局部降级/幂等、阶段恢复/O3 工件、同语义日、dirty parent、SWEEP、Barron's 时间、W3 writer/MCP、v2.1 原生 Policy；通过后进行有限生产自然任务验收。不得把本次 5 项隔离测试当作整合已通过。
5. 将远端 `/home/ubuntu/doxagent` 收敛到该 MAIN 提交，迁移 Guardian `/repair-source` 与正式重建配置；保留 `.env.v2`、secrets 和 systemd 入口的正确路径。核对所有正式容器源码 revision/image ID、配置、挂载及自然业务。
6. 对照目录表逐项解除引用。已注册 worktree 用 Git worktree 流程退出；独立 overlay/构建目录在源码吸收、必要小回执保留、容器/进程/挂载解除后按精确路径清理。先处理旧代码工作树，后处理仍在使用的 release 根。镜像退役单独核对动态 agent/approved 依赖，不执行全局 prune。

最终验收应为：MAIN 包含保留修复；远端主检出无未提交应用修复；正式服务来自同一 MAIN revision；Compose/Guardian/systemd 无临时目录引用；隔离 Worker 与旧模板按实际状态退出；保留业务数据与身份。不能仅以“远端 git branch 显示 main”验收。

## 证据与限制

本地证据：`eval/release_reconciliation_20261014/`。`inventory.json` 是目录/Git/容器/挂载清单；`remote-code.json` 是筛选后的代码快照；`comparison.json` 是代码映射/规范换行/三方比对；`running-code.json`、`running-comparison.json` 为线上源码比对；`dependencies.json` 为路径依赖与容量；`yahoo-route-loss.json`、`regression-current.txt`、`regression-recovered.txt`、`barrons-current.json`、`barrons-recovered.json` 为确定缺口证据。candidate patch 只供整合参考，未应用到主检出。

本次未对所有旧 dist 做逆向还原，未核实 4 个隔离 Worker 的业务队列终态，未扫描业务库中任意历史路径字符串，也未对每一个历史源文件差异宣称语义等价。用户提到的具体 UI/业务 badcase 未逐条提供，因此已证明修复遗漏机制及可复现实例，尚不能把所有生产症状都归因于这些缺口。最终删除清单应在整合与切换后再以实时引用复核锁定。
