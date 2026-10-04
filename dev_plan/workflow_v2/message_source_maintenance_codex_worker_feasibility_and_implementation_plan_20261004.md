# 消息源维护 Codex SDK Worker：可行性分析与开发方案

日期：2026-10-04。输入：本轮只读生产勘察、此前消息源修复记录、实际初始化 Guardian 实现及 OpenAI 官方 Codex SDK 文档。**这是待开发方案，本轮未部署 Worker、调用真实模型、修复或重启生产服务。**

配套：[消息源反复失败：根因诊断与根本修复方案](message_source_recurring_failure_root_cause_and_repair_plan_20261004.md)。基础控制链路修复与本方案配套，但不能用模型 Worker 代替基础层应有的确定性恢复。

## 1. 可行性结论

**可以做，适合做“确定性守护 + 独立 Codex 窄修复 + 受控应用 + 真实验收”，不适合做“失败就唤醒一个有 root/SSH/Docker 权限的万能 Agent”。**

过去修复中，大量工作可重复为证据收集、错误分层、现有组合验证、站点 parser/selector/等待条件的局部改动、针对性测试、窄镜像更新与验收。足以支持此模式。与此同时，下列问题不应交给模型自动扩大权限解决：

- 登录/MFA/人工 challenge、付费订阅权益，模型不能替用户完成或绕过。
- 已死亡 driver 的重建、有限 RPC 重试、已有 fallback，应首先由确定性程序执行；没必要每次花模型调用重新诊断已知机制。
- Shared Runtime、数据库/调度核心、Chrome 生命周期/沙箱、全局 Clash 变更影响多个来源；Worker 可诊断和提交补丁，但首版不能任意自动部署这些共享核心修改。
- 无法找到与实际部署匹配的代码基线时，自动代码应用不可靠；只读诊断和已批准操作仍可执行，不能拿本地最新 HEAD 猜测线上代码。

**可预期降低人工维护，但不能承诺所有消息源从此无需人工。** 本次四源共同失效主要应通过 driver 自愈消除；Worker 更大的长期价值是对新出现的站点局部变化进行有证据的窄修复，以及自动整理真正需要人工登录的事项。

### 1.1 初始化 Guardian 能复用什么，不能直接照搬什么

已核实：生产独立 `initialization-guardian` 服务在运行，SDK `openai-codex==0.159.3`，Repair Agent 使用 `gpt-6.1-sol / medium`；但生产 `initialization_repair_incidents`、`initialization_repair_rounds` 当前均为空。**有落地框架和测试，不等于已有生产自治修复成功率证据。**

| Guardian 现有机制 | 消息源维护采用方式 |
| --- | --- |
| 持久 Incident、Repair Round、独立 Thread、worktree/branch | 沿用设计，使用独立数据库和命名空间，不写初始化 repair 表 |
| AsyncCodex、结构化报告、receipt、同线程验证反馈 | 复用 SDK 调用模式；独立 client_name/service_name/CODEX_HOME |
| 开发 Agent 不挂业务数据库/Docker socket | 沿用；只得到脱敏 evidence 与可写候选源码 |
| 可信 Guardian 做独立测试、构建与执行 | 沿用职责隔离，但应用器只接受消息源允许的动作 |
| 精确部署 revision/hash 验证 | 扩展为多服务 base digest + overlay 文件 manifest；当前消息源不是一个纯 Git archive 镜像 |
| 正式 FAILED initialization、每持久节点 3 轮 | 不照搬。消息源是持续服务，需处理 PARTIAL、延期、闭市、共享 Identity 和采集/正文两个阶段 |
| 原 initialization ID resume、临时 Executor 接管原 DAG | 不照搬。消息源恢复后由原 scheduler/正文队列继续运行，不启动第二条生产消息管线 |
| 修复分支不改正式服务，成功节点保持 | 保留分支隔离；但消息源局部代码需要受控上线到原常驻服务，否则问题不会持续恢复 |
| Agent 容器内存上限 15GB | 不照搬。首版一个 Worker、2GB 内存初始上限，避免挤压生产 Chrome/业务 Agent |

## 2. 历史案例是否支持自动化

| 真实案例 | 自动化适合度 | 推荐处理 |
| --- | --- | --- |
| 内部 ReadError/RemoteProtocolError，原操作服务端已完成 | 高，确定性 | 按原 operation ID 一次有限重试，记录，不唤醒模型 |
| 本次 External Node driver OOM，Chrome/CDP 存活 | 高，确定性 | 分轨 driver recovery；恢复失败或出现未知新模式再进入 Worker |
| 代理出口 IP 轮换误增配置 generation | 有明确回归的代码修复，但属于共享治理 | 诊断 + 小补丁 + 测试；核心修复经独立批准后部署，不自动轮换 Profile |
| Barron's 页脚假卡片、卡片/日期延迟加载 | 高，站点局部 | 候选 parser/等待条件修复，真卡片和日期回归后受控上线 |
| Investing 普通/Pro 模板不同、正文选择器不准 | 高，站点局部 | 修复站点 DOM scope 与分类；无权益则保留 entitlement 结果 |
| DIGITIMES 列表可用，订阅正文受限 | 不适合代码自动解决 | 分类为 HUMAN_REQUIRED，提供准确维护身份；不改权限判据 |
| 多源 JointBudget 队头、取消漏 permit、无界 cleanup | 部分适合 | 统一 Runtime 事故；确定性恢复/定位，共享核心修复需要独立验证批准 |
| Message Bus 同步 SQLite/日历热路径 | 不适合首版自动应用 | 提交根因与局部候选，不扩大到生产调度核心重构 |
| IBKR 缺 ibapi 的错误构建镜像 | 可诊断，可执行已登记的基线纠正 | 验证依赖清单；只能回到已批准已知正确镜像，不临时 pip install 污染线上 |
| 持续 Reuters 403，实际拒绝页丢失 | 先补证据才能自动判 | 修正基础错误契约后，验证已注册组合；仍需 challenge 则转人工 |
| 偶发出口网络/网站 5xx | 高，有限恢复 | 原重试/fallback，不无限换 IP，不每次修改源码 |

这说明它不是“每 ticker 失败都建一个 Coding Agent”的功能；应先定位故障所属资源再决定是否需要 Codex。

## 3. 首版范围与边界

覆盖现有 Message Bus 的 API/RSS/爬虫三类采集来源，以及其正文阶段；首批真实验收使用本轮五个来源。按消息源可配置启用，不默认给每个来源一套定制 Worker。

首版交付：

1. 独立维护控制器及持久事故/轮次/操作账本。
2. 有界健康采样、确定性归因、共享事故归并。
3. 若仍失败，按需启动独立 Codex Worker 诊断并做站点局部候选修复。
4. 候选独立验证、可信应用器执行允许的窄更新、回滚和真实观察。
5. 一份统一 `message-source-issues.md` 运维台账与简单 status/inspect/retry/cancel CLI；不扩张为管理后台。

明确不做：O1/O2/O3/O4 重构、修改业务 Agent 线程/队列、自动新增订阅/ticker、自动改 L1/L2/Jev、改变 60s/30min/sweep、伪造成功或补发旧新闻、自动买会员、处理 MFA/CAPTCHA、复制/重建 Cookie/Profile、升级全局 Chrome/Playwright/SDK、修改 Chrome sandbox、自动推送/合并 main、让模型拥有宿主 Docker/SSH/Clash 管理权。

## 4. 最小架构

```text
原 Bus / Enrichment / Site Access / Supervisor
       │ 脱敏健康与错误、只读证据
       ▼
Source Maintenance Controller（独立常驻、可信程序）
       ├─ 已知故障 → 有限确定性动作 → 真实验收 → 关闭事故
       └─ 未恢复 → Incident / Evidence Pack
                    ▼
             按需 Codex Repair Worker
             独立 SDK Thread + 候选源码 + 测试 + 结构化报告
                    ▼
             Controller 独立验证
                    ▼
             受限 Apply / Rollback → 原服务继续采集
```

一个控制器、一个按需 Worker，不加新任务中台或多 Agent 委员会。原生产消息路径不迁移到维护容器。

建议新增 `src/doxagent/source_maintenance/`：

| 文件 | 职责 |
| --- | --- |
| `schema.py` | Incident、Round、Evidence、Action、RepairReport 契约 |
| `repository.py` | 独立 SQLite、lease/CAS、恢复、预算与操作去重 |
| `health.py` | 采样、窗口资格判断、失败分层和归并 |
| `evidence.py` | 只读证据、脱敏、版本/配置 manifest |
| `controller.py` | 持久阶段推进、确定性动作、Worker 调度、验证闭环 |
| `agent.py` | 独立 AsyncCodex、Thread/turn receipt 与反馈 |
| `apply.py` | allowlist 动作、配置 CAS、窄镜像更新与 rollback receipt |
| `cli.py` | guardian/status/inspect/adopt/retry/cancel/issues |

独立目录 `/var/lib/doxagent/source-maintenance/`，包括 `maintenance.sqlite3`、`repository.git/`、`incidents/<id>/worktree/`、`codex-home/`、`rounds/<round>/`、`message-source-issues.md`。可以复用已有 Git workspace/日志脱敏的小工具，不能 import Guardian 来操作初始化控制表。

## 5. 健康信号与触发

### 5.1 先修正健康事实

必须落实配套根因方案：QueryOutcome、全部失败不刷成功、DEFERRED 独立表达、reason_code/operation ID、driver 分轨健康。否则只看 `consecutive_failures>=N`，Reuters 这样的全失败会永远漏触发。

不要把没有新文章、没有 publish、PARTIAL coverage、周末长时间没 poll、旧 auth_state 过期本身当故障。

为每次真实采集补一个小型 `source_health_samples` 记录，保存 sample_id、source/binding、stage、真实开始/完成时间、schedule/sweep eligibility、query 成败/延期数、category/reason、Site/Identity/runtime epoch、operation ID、纯 queue/service 延迟与是否有有效列表/正文。不存新闻全文或秘密。采样收敛为一个批次结果，避免每网络事件重复写入；保留近 7 日或每源最近 1,000 条，事故引用的证据另留副本。

正文健康由现有正文 outcome 和有权限验证样本形成，不能以队列里没有任务认为健康，也不能以某篇 Pro/订阅失败认定整个公开源不可用。

### 5.2 默认触发规则

以下是首版初始值，配置化但不要求操作者维护大量参数：

- 采集：在真实 eligible 工作窗口中，至少 3 个不同 poll 操作失败、连续受影响达到 15 分钟；或者 Search 连续 degraded 达 15 分钟且关键 query 持续失败。曾经的真实成功不能被纯延期或空失败冒充。
- 调度：仅在按原规则到期应执行时，超过 `max(3×目标间隔,300秒)` 无新 attempt/无有效在途任务，记 `SCHEDULER_STALE`；闭市没有 due sweep 时不触发。
- 正文：15 分钟内至少 3 个不同可访问候选失败且占有资格样本至少 80%；同一文章重试不算多个样本。无新文章不触发。明确登录/订阅失败优先记录人工事项。
- Runtime driver dead：立即由 Runtime 自愈；一次恢复失败，或 30 分钟内 3 次 driver 崩溃，开启 Runtime 级事故。30 分钟重连次数不应被不同 ticker 重复累加。
- Queue/resource deferred：有资格时持续超过 15 分钟开启容量事故，只诊断负载/排队，不当网站风控；不靠换 IP 修容量。

闭市时可以继续处理此前已确认的 driver/代码事故和只读诊断；不另创强制实时 polling。验收要么使用明确标记的只读 canary、不发布历史样本，要么等待下一原定 sweep/实时轮询。

### 5.3 事故归并层级

按有证据的最上游共同资源归并：

1. `RUNTIME:external_chrome:<driver_epoch>`：本次四源合成一个事故，涉及多个 Identity/ticker。
2. `IDENTITY:<identity_id>:<failure_class>`：同一 Chrome/Profile/容量问题归并；DIGITIMES/IHub/Investing 可能共同受影响。
3. `SOURCE:<source_id>:<stage>`：共享 Feed 一次失败，不按三个订阅 ticker 各修一次。
4. `BINDING:<binding_id>:<query_or_entry>`：只有已证明某 ticker 的搜索词/入口映射异常才独立事故。

错误文案变化、Registry revision 变化不重置轮次预算；driver 恢复后真实健康期成立，后续新的故障可形成新 incident，但受资源级滚动预算约束。记录 affected_bindings，所有受影响页面都看到同一事故进度。

## 6. 独立 SDK Worker

直接复用项目已落地的 Python `openai-codex==0.159.3` 与锁定 runtime，不新引入 OpenAI Agents SDK/TypeScript 框架；独立 `AsyncCodex(CodexConfig(...))`。模型初始 `gpt-6.1-sol / medium`，独立配置和上线前账号可用性 smoke，不修改业务 O 系列配置，也不默默换模型。

`client_name/service_name=doxagent-source-maintenance`；独立 CODEX_HOME、认证模板、Thread、receipt、模型预算/日志，关闭生产 MCP 与用户插件继承。认证账号可以经明确配置复用凭据，但其会话与权限必须隔离；同账号的供应商额度仍可能共享，不能声称目录隔离就隔离了账户额度。

Worker 运行于单次容器，非 root，workspace-write + deny_all（SDK 中的非交互拒绝审批模式），仅候选 worktree/报告目录可写。网络只允许模型所需目的地与受控证据服务；依赖预装。不挂生产 `/data`、Profile、`.env.v2`、SSH key、Site 管理令牌、Supervisor socket 或 Docker socket。Codex sandbox 不是生产授权层，必须同时用容器挂载/网络/可信应用器约束。

持续 Thread 用于同 incident 的历史与反馈，但每轮提供新冻结证据；代码基线变动时在显式新 worktree/rebase receipt 后继续，不能把旧补丁盲加到新版本。

结构化 RepairReport 至少包括：

```text
root_cause / confidence / evidence_refs / failure_scope
proposed_actions[]（固定枚举，不是 shell）
changed_files / tests / affected_sites
expected_recovery / remaining_items / human_action
```

模型不能凭报告声明“已恢复”。Controller 另做独立验证。网页、新闻正文、历史错误和日志属于不可信证据，不能成为指令；不得从网页接受“运行命令/修改密钥/忽略规则”。

## 7. 证据工具与可执行动作

### 7.1 Worker 所需证据

Evidence Pack 冻结发生窗口、UTC/ET/北京时间、失败样本、真实 last_success、eligible/sweep receipt、Site/Identity/出口映射、当前组合健康、Chrome/CDP/driver 状态、队列指标、脱敏日志、已部署 manifest 和相应源码、相关历史修复/测试。

模型可调用一个小型只读证据服务补充：`get_poll_health`、`get_access_events`、`get_runtime_status`、`get_deployment_manifest`、`get_bounded_log`。参数只接受事故关联 source/identity/time 范围，不能接受 SQL、任意路径/API/命令。

DOM/截图确有必要时由可信 Controller 通过现有 Site Access 建立诊断任务、保存脱敏输出；执行原身份的治理/维护排他/预算，模型不直接 attach 第二个 controller。无需完整新 MCP 平台，首版可用同网络受限 HTTP 证据接口。

### 7.2 自动动作 allowlist

| 动作 | 可以自动执行的范围 |
| --- | --- |
| `RECONNECT_EXTERNAL_DRIVER` | 配套生命周期已实现；只控制层、不关 Chrome；Runtime 单飞并保留维护锁 |
| `SELECT_EXISTING_COMBINATION` | Site/用途允许、启用、真实 canary 成功的既有组合；Registry/runtime CAS；需要认证时该备用自身已验证 |
| `PATCH_SOURCE_PARAMETERS` | 同源正式入口映射、已注册 recipe 参数、已证明的站点正文 selector 等字段；域名归属校验和 revision CAS |
| `APPLY_SOURCE_PATCH` | 站点局部采集/正文适配补丁，测试/独立验证通过，以当前服务精确 baseline 派生不可变 overlay |
| `ROLLBACK_SOURCE_UPDATE` | 本轮对应的旧配置/image receipt，无条件回滚未知用户更改不允许 |
| `REQUEST_HUMAN_MAINTENANCE` | 汇总站点、Identity、出口、challenge/login/entitlement 原因和现有桌面入口 |

Provider API/Feed 的重试/fallback先调用既有机制。禁止自动切 global Clash 节点，改变 Profile 的固定 egress、伪造 auth_state、解除未知人工维护锁、重建 Chrome Identity。注册的备用组合若实际同属已死亡 External driver，则换组合无意义，应归并 Runtime 事故。

`site_access.maintenance_token` 等新权限只给可信 Controller，不能给 Coding Worker；token capability 仅开放上述维护动作，不复用完整管理员令牌给模型。对现有 API 的权限扩展限定消息源维护，不改 O 系列业务权限。

### 7.3 自动代码范围

允许站点专用 adapter/parser、正文识别/recipe 参数及其测试。首批涉及 `market_sources.py`、`industry_sources.py`、`reuters_sources.py` 中相应站点函数/类，以及 Registry 中对应站点的 body/crawler 参数。对尚位于大共享文件的站点逻辑，验证 diff 只触及该站点符号；必要时将这部分平移到站点专用 helper，不借维护大规模重构。

共享 budget、Runtime 生命周期、通用 health classifier、调度/分发/admission、数据库 schema、依赖锁、Docker 基础环境、维护控制器本体不在首版自动应用范围。模型可给这些问题出候选补丁和测试，归类 `REVIEW_REQUIRED`，不能伪装站点改动上线。

不设置“最多改 N 行/两个文件”的形式门禁。真正约束是业务范围、权限、基线、回归与受影响资源，避免小到无法修复真实站点缺陷。

## 8. 候选验证、应用与回滚

### 8.1 部署基线必须反映现实

本次生产 Bus/Enrichment/Access 是不同依赖基线上的窄 overlay；Bus SDK 0.144.4，Guardian SDK 0.159.3。不能用 Guardian 镜像当生产 Bus 基线重建全项目。

Controller 获取 base image digest、overlay commit/文件 SHA256、相关配置 revision，准备干净候选；保留正文 retry 等现行补丁。缺失 manifest 时先补可追溯记录，不用仓库 HEAD 冒充生产状态；缺失记录仅阻止代码应用，不阻止无代码恢复/只读诊断。

默认分支 `codex/source-maintenance/<incident-id>`，由可信控制器记录候选 commit，模型不推 Git。实际生效补丁集中列入发布 manifest，并提供后续人工合并入口，避免下次普通 build 又丢修复。

### 8.2 独立验证与真实上线

1. Controller 比对实际 diff 与报告、业务 allowlist；独立运行预定义 focused pytest/Ruff，不执行模型给出的任意宿主命令。测试容器仅挂候选源码，无生产秘密。
2. 使用真实脱敏 DOM/Feed fixture验证目标问题，至少包含一个正常样本和一个失败/付费/空结果样本；检查正文不是推荐栏、日期不是更新时间、错误不变成假成功。
3. 配置更新通过既有 API CAS 实施，记录 before/after revision。代码生成 scoped image，仅修改必要服务文件，依赖与环境不随候选仓库其他变动漂移。
4. 不启动“candidate Site Access”与生产同时控制同一 Profile；不热复制人工 Profile 来测试。offline 验证后受控替换必要的原服务，再在唯一 runtime owner 下做真实 canary。首版接受短暂服务切换与可回滚风险，不装作能零成本并行灰度同一身份。
5. Site Access 更新先停止接新任务、有限 drain、detach 控制链路，Supervisor/Chrome/Profile 保留。只在动作明确需要时替换 Bus/Enrichment；无需时不重启它们，更不重启 API/交易/O 系列服务。
6. 同时涉及 Bus 与 Access 协议的补丁必须兼容滚动顺序；无法保持兼容或要求数据库迁移的候选不自动部署。
7. 真实 canary 检查对应 ticker 列表/搜索或 Feed解析、最终域名正文、类型与日期；保持原窗口。普通历史样本只诊断，不向消息总线 publish。
8. 在真实 eligible 窗口中观察至少 3 次连续合格 poll，若有新可访问正文则同时验收；没有新闻时允许真实空列表。多个受影响 ticker均要检查，不以某个主 ticker一次成功关闭所有问题。闭市则等待原定轮询，状态为 `WAITING_WINDOW`，不是“已修复”。
9. 发现候选新增回归即按本轮 receipt 回滚；不会 rollback 数据库、删消息/正文任务、重置 checkpoint、篡改成功状态或覆盖人类新 revision。回滚后保留旧事故和诊断。

`RECOVERED` 表示真实访问恢复；`STABLE` 另需跨 24 小时或下一个完整实时窗口，含正文结果。控制层 OOM 的长期稳定性必须按配套方案 24–48 小时单独验收。

## 9. 持久化、并发与有限预算

四张核心表足够，不再建第二套业务 Runtime Journal：

- `maintenance_incidents`：scope/resource、failure class、影响源/ticker、first/last observation、阶段、状态、owner lease/generation、基线与人工事项。
- `maintenance_rounds`：round_id、Thread/turn、候选 patch/commit/image、证据/测试/usage、应用/验收结果。
- `maintenance_actions`：操作 idempotency key、kind/target、expected revision/image、before/after receipt、开始/完成与回滚结果。
- `maintenance_evidence`：脱敏文件索引、时间/摘要/hash；大 fixture 放事故文件夹，不把 HTML 反复塞进 SQL/模型上下文。

状态链：`OBSERVED → TRIAGE → AUTO_RECOVERY → CODING → VERIFY → APPLY → OBSERVE → RECOVERED → STABLE`，旁路 `WAITING_WINDOW / HUMAN_REQUIRED / REVIEW_REQUIRED / ROLLED_BACK / CANCELLED`。Controller 每 tick推进有限步骤，掉电后从 action receipt核实实际状态再继续，不凭进程退出码重复部署。

默认一个 Coding Worker同时运行；按 Runtime/Identity/服务更新分别加租约，避免两个事故对同一生产服务交叉 apply/rollback。普通消息采集不受维护全局锁阻塞，只影响动作所需资源。generation防迟到结果，action幂等防重复切换。

建议预算：每 incident最多 3 个候选轮次、每轮 20 分钟 wall limit（含验证反馈但不含等待市场窗口）；同资源 24 小时最多 3 次自动代码部署。真实配置/镜像改变不凭错误文本洗掉预算；外部状态明确改变后可由人工 retry。driver确定性自愈单独计数，不消耗 Coding轮数。Codex返回 token usage有记录；初始同时限制每天 6 次新 Coding轮与总运行时间，成本上限待实际账号计费建立可靠 usage 后启用，不能编造美元费用。

耗尽预算、明确需要认证/MFA/权益、越过权限边界、缺精确基线、候选造成回归而无法安全继续，才转人工/审阅；不能因为一轮测试失败、问题看起来困难而过早放弃。失败验证可在同 Round/Thread内提供反馈改进。

台账记录 attempted/recovered/stable/human/review、原因证据、变更、测试与回滚，不只统计唤醒次数。日志/fixture滚动保留、事故重要证据留 30 天；Codex session按独立策略保留，不删除活动线程。无需首版改前端 KPI，可通过 CLI和统一 Markdown读取。

## 10. 开发顺序与测试

### 阶段 A：信号与确定性自愈

落实配套 P0–P3。新增健康结果与 driver状态；恢复动作有独立测试；现有来源原频率/窗口/Profile不变。没有这些基础能力，不开启 Coding自动部署。

### 阶段 B：只读 Controller 与持久事故

新增模块、SQLite、脱敏证据、资源归并、CLI和 `message-source-issues.md`。默认只读 shadow模式，用近两日失败回放验证本次四源只开一个 Runtime事故、Reuters开独立 Site事故、周末不误报调度故障。

### 阶段 C：独立 SDK与候选验证

新增独立 prompt、`deploy/Dockerfile.source-maintenance-agent`、`deploy/docker-compose.source-maintenance.yml`。controller为小常驻服务；Worker按需容器、预装SDK/pytest/ruff、初始2GB内存/有限CPU，不复用15GB Guardian候选资源配置。模型调用在实施阶段做真实 start/process-exit/resume smoke；本轮未调用。

首批用历史 DOM案例回放：Barron's footer/异步日期、Investing Pro模板、一个 Reuters拒绝页。测试报告与实际diff核对，不因模型自报通过而放行。

### 阶段 D：有限动作与窄自动应用

先启用已有driver重连/组合选择，随后启用站点专用参数/代码变更自动应用；共享核心保持提交审阅。所有成功/回滚保存immutable receipt；不自动Git push/main合并。

### 阶段 E：真实验收

1. Runtime故障四源共性归并，其他Managed/RSS/API继续运行。
2. 搜索全部失败、部分query失败、真空结果、coverage不完整、DEFERRED、周末与暂停/tombstone均正确分类。
3. Worker退出/Controller重启可恢复Thread与receipt，不重复apply。
4. stale revision与human配置并发更新被CAS隔离；旧结果不改新epoch状态。
5. 同一Identity维护排他；不关闭人工窗口、不复制Cookie、不重建Chrome。
6. 本文allowlist之外的Docker/SQL/API/任意路径/共享core补丁不可自动执行；秘密与网页指令不进入授权判断。
7. 恶意fixture/测试仅在测试容器运行，不能访问宿主socket/生产文件。
8. 独立测试失败给反馈；候选上线新增回归可回滚；没有新闻也能以真空结果验收，过期样本不publish。
9. 存量身份有权限正文验收与Pro/付费失败分别报告；人工列表可直接定位现有登录维护工具。
10. 运行至少一个完整实时窗口，分别统计降低维护次数、自动恢复准确性、误触发、正文成功与回滚情况，不用Worker活跃/容器healthy作为业务可用证明。

实现重要代码改动时追加 `changelog`，部署/回滚记录追加统一运维台账。两者职责不同，运行中台账不由模型直接向 main 提交。

## 11. 最终建议

建议实施，但把目的定为**减少重复维护、自动解决有证据的站点局部缺陷**，不是允许Agent不断改整个生产架构。

本次最有价值的第一步不是Coding Worker上线，而是 External控制驱动自愈、Reuters故障语义与有效fallback、共享Identity公平性/容量。做到这些后，Worker收到的是准确且有边界的事故，才有可能用最小改动真正恢复来源，而不是不断重演“重启恢复几小时、下次再失败”。

## 12. 核查与官方依据

- 代码：`initialization_repair/guardian.py`、`agent.py`、`containers.py`、`git_workspace.py`、`context.py`、`repository.py`、`schema.py`；`docs/ticker-initialization-repair-operations.md`；原 Guardian实施方案与现行部署 overlay。
- 当前生产核查：Guardian SDK 0.159.3 / `gpt-6.1-sol / medium`；两张 repair表无记录。普通 Bus SDK 0.144.4 与其隔离，不视为本轮故障原因。
- 历史源修复：`changelog`、10/01与10/02验收文档；当前Root Cause配套文件包含原始故障时间、访问事件与健康证据。
- [OpenAI 官方 Codex SDK文档](https://learn.chatgpt.com/docs/codex-sdk)：Python SDK通过本地app-server/JSON-RPC控制线程，发布包带锁定runtime；支持AsyncCodex与线程继续/恢复。本方案复用当前Python栈，不新增另一套模型框架。
- [OpenAI 官方 sandbox说明](https://learn.chatgpt.com/docs/sandboxing)：sandbox与approval是不同控制；非交互不请求审批不意味着生产全权限。本方案叠加容器挂载、网络与可信动作执行器。
- [OpenAI 官方审批与安全说明](https://learn.chatgpt.com/docs/agent-approvals-security)：凭据、外部内容和执行边界需要明确控制。维护自动化的生产动作由应用自己的确定性授权层约束，不假定继承桌面人工授权。

官方文档查阅于2026-10-04；上线前继续按项目锁定0.159.3实际接口验证。本文件没有宣称模型账号已实测可用，也没有把空生产repair记录包装成已证明自治成功。
