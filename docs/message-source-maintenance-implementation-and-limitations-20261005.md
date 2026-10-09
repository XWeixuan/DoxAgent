# 消息源维护 Codex Worker：当前实现与续开发边界

日期：2026-10-05。依据：[冻结实施方案](../dev_plan/workflow_v2/message_source_maintenance_codex_worker_feasibility_and_implementation_plan_20261004.md)。

## 1. 当前交付状态

已完成本地实现，**不上线、不部署、默认不启用**。本轮没有 SSH、生产数据库操作、服务重启、真实模型调用、镜像构建或推送；没有接入 O1/O2/O3/O4、初始化 Guardian 的线程或控制表。

默认 `DOXAGENT_SOURCE_MAINTENANCE_ENABLED=false`，模式 `shadow`。即使显式运行 guardian，默认也只输出 disabled 后退出，不创建维护数据库、不启动 Docker/SDK、不执行维护动作。独立 Compose 使用 `source-maintenance` profile，未加入现有生产部署文件；示例也不强制打开 enabled。

这是一套可配置、可离线验证的维护实现，不是已经过真实账号、生产 Docker、原 Chrome 身份和完整交易窗口验收的自治维护服务。密集开发阶段继续保持关闭。

## 2. 模块与实际链路

代码位于 `src/doxagent/source_maintenance/`：

| 文件 | 已实现职责 |
| --- | --- |
| schema/settings | 事故、轮次、动作、报告、精确部署 manifest；独立配置及默认关闭 |
| repository | 四张维护表及有界健康样本；租约/generation、原子轮次预算、动作幂等/资源互斥、重启恢复 |
| signals/runtime_signals | 原采集、共享分发、正文、到期调度、External driver 的可选轻量采样；不存新闻全文 |
| health | 确定性触发、上游资源归并、真正成功与空列表/延期区分、多 binding 恢复观察 |
| evidence/issues | 脱敏冻结证据、内容 hash、受限只读证据 HTTP、统一 Markdown 台账 |
| workspace/policy | 独立 bare repo/worktree、部署 overlay 重建、站点符号级 diff 校验、本地候选提交 |
| agent | 锁定 Python SDK 的独立 AsyncCodex start/resume、同 Thread 反馈、turn/report/usage 收据 |
| containers | 按需非 root Worker、2GB/有限 CPU、隔离网络；独立只读/offline 测试容器 |
| apply/site_api | 可信动作执行器、受限 Site capability、配置 CAS、窄镜像更新、drain、原 owner canary、比较式回滚 |
| controller/cli | 持久状态推进；guardian/status/inspect/adopt/retry/cancel/issues/replay；不创建第二条采集管线 |
| proxy | 明确模型域名的 HTTPS CONNECT 隧道；不做 MITM，不允许任意 HTTP 或私网目的地 |

链路：真实健康事实 → 确定性事故 → 已知 driver 有限恢复 / 人工事项 / 候选修复 → 独立验证 → 受控应用 → 原管线合格轮询 → RECOVERED → 长期观察后的 STABLE。

`shadow` 只留证据，不调用模型和执行动作；`diagnose` 可以生成并验证候选，不能上线；只有另行明确配置 `enabled=true, mode=apply` 才允许应用器工作。

### 信号与窗口

- 采集至少三个不同操作、连续影响至少 15 分钟；Search 全失败/持续 PARTIAL 可触发。
- DEFERRED 不伪装成功；持续排队归容量事故，不换 IP。
- Body 使用不同文章及 80% 失败比例，明确权益/登录/challenge 优先人工事项。
- Driver 恢复失败或 30 分钟三个不同崩溃操作归并到共享 Runtime；同崩溃不按 ticker 累加。
- Scheduler stale 只由现有编排判断为真实到期、无在途任务时采样。周末、暂停、未到期 sweep 不另创 polling。
- 三次真实合格 poll 才接受采集恢复；涉及多个 binding 必须逐一成立。没有新文章可以是真空列表，不能用没有 publish 认定故障。
- 60s、实时 30min、原闭市 sweep、去重和正文原队列均未改动。

### Worker 与宿主权限

Worker 只挂候选目录、独立 Codex home；不挂业务 `/data`、网站 Profile、生产 env、SSH key、维护管理令牌、Docker/Supervisor socket。独立 auth 模板只复制 auth.json，不复制业务 Codex config/MCP/plugin 配置。SDK sandbox 与容器边界同时生效。

模型只能提出固定枚举动作，不能提交任意宿主命令。测试只在无网络、无生产挂载的验证容器运行；既有基线测试另冻结一份，不能通过删掉原断言让独立验证虚假通过。候选 Git marker 校验，宿主 Git 禁用 hooks/fsmonitor/外部 diff，避免 Git 本身成为候选执行入口。

Site maintenance token 与 admin token 分开。默认不开 maintenance 路由；开后也不能管理账号密码、创建 Profile、修改 auth、出口、域名或调用任意 admin API。参数仅限注册 recipe 的站点字段，域名归属和 revision 校验仍成立。

## 3. 最重要的局限：上线补丁与 main 没有回流闭环

**风险成立，而且这份实现没有把它包装成已解决的问题。**

维护能力存在两个独立步骤：

1. 紧急上线：Worker 在独立 `codex/source-maintenance/<incident-id>` worktree 修改，Controller 独立验证并本地提交，以正式服务实际镜像为 FROM 构建窄 overlay，受控替换原常驻服务。
2. 代码归并：人工把对应修复合入 main，本地同步，再通过常规发布消除 overlay 分叉。

本轮实现步骤 1 的受控能力；步骤 2 **没有自动 Git push、PR 创建、PR 合并或本地同步**。严格遵守原方案不自动推送/合并 main 的边界，没有把你引用的“未来建议”暗自扩展为本轮 Git 自动写入功能。

### 3.1 与 ticker 初始化守护不同

初始化守护可以让额外分支/容器继续完成某一次初始化，留下结果而不修改原常驻服务。消息源每分钟持续采集；若候选只留在旁路容器，正式来源仍会失败。本实现因此确实有替换原消息源服务的步骤，**不是长期运行旁路爬虫来假装修复**。

### 3.2 已做到的防覆盖机制

- 候选起点必须是 exact source commit + 已部署 overlay 文件 hash；缺基线允许诊断，但不能猜 HEAD 上线。
- Apply 比较当前容器的实际 image ID，候选变动需重新验证；不将依赖、Chrome、SDK 或其他服务随候选漂移。
- 记录不可变候选 commit、before/after 镜像、文件 hash、配置 revision、动作及回滚收据。
- 每次代码更新保存独立 deployment manifest，并更新维护目录 `deployment-head.json`。下一事故优先读取它，继承前次已上线补丁，而非回到最初 main 基线。
- manifest 的 `applied_patches` 和 action receipt 的 `code_return=LOCAL_BRANCH_ONLY_NOT_MERGED` 明确标出补丁仍未归并。
- 回滚只接受本次 before/after，遇到人类新镜像/revision 不覆盖；代码回滚同步维护 head 的对应谱系，不回滚业务数据库。

这些是可追溯、避免 Worker 自己盲目覆盖的机制，**不是 main 对齐机制**。

### 3.3 仍会怎样漂移

main 基线 → 维护上线 Reuters A → 维护上线 Barron's B → 本地仍只有原 main。

维护内部能够从 A+B 继续，但普通本地开发、测试和发布不自动知道 A+B。当前普通生产 build/release **没有读取维护 head 并强制包含 A/B 的门禁**。若直接用未归并 main 重建，仍可能抹掉已上线补丁。

配置动作与代码变化必须分开看：driver 重连/选既有组合/Registry 参数调整记录配置与操作，不造成 Git 代码漂移；parser、adapter、正文识别代码变化必须回流 Git。参数调整也可能与仓库 seed 不一致，这是配置治理问题，不能误称 Git 已对齐。

### 3.4 将来启用代码自动维护前的必补项

1. 每个已上线代码候选自动推送修复分支、创建 PR，关联 incident、exact manifest、测试、真实验收和回滚；仍不自动 merge main。
2. PR 回流失败时保留 `APPLIED_NOT_RETURNED`，准确报告生产未归并补丁；上线成功不等于代码生命周期完成。
3. 正式发布读取当前维护 head：必须包含所有有效补丁，或由人类逐个明确回滚/替代；不能悄悄覆盖。
4. 合并后校验 main 中实际文件与已上线补丁，记录 merged/superseded/rolled_back，清理谱系状态而非只删除临时分支。
5. 准备并测试 A → B → main 合并 → 常规发布、PR 冲突/失败、回滚 B 不丢 A、人工另发新镜像这些路径。

GitHub 访问凭据只给可信回流程序，不给 Coding Worker。这不是自动合并 main 的许可，也不应由模型自行决定哪些补丁已经可以丢弃。

## 4. 其他明确局限与续开发入口

### 4.1 本轮尚未做真实上线验收

只有离线/fake SDK/fake Docker 测试和本地 CLI 默认关闭验证。没有真实 start→杀进程→resume 的账号 smoke，没有构建维护镜像，没有验收目标主机的 sandbox、目录 UID、网络、token、Compose topology 和 Chrome 排他，也没有完整实时窗口、24–48h 或实际维护成功率。

后续必须先在隔离环境做账号/镜像/权限 smoke，再 shadow 回放真实事故，最后仅对小范围来源启用 diagnose/apply。不能因为本文代码完成就声称生产自治能力已验收。

### 4.2 站点策略与 canary 仍需部署时配置

首批 ownership 示例覆盖五源的 parser/recipe 局部符号。共享 MarketTickerAdapter、通用 body selector dispatcher、Runtime 核心不整体放行；需要改共享行为时提交 REVIEW_REQUIRED，或后续平移站点 helper 并扩充对应测试。

示例 `canaries=[]` 有意不冻结可能已失效的付费文章和 ticker URL。启用 apply 前应填入当时真实的列表/搜索与最终 publisher 正文样本，覆盖受影响 ticker，以及 normal/failure fixture；不是拿首页 200 当恢复。当前 Site canary 内置解析首批五源；新增 API/RSS 的健康、事故和候选契约通用，但它们各自的生产 provider/Feed canary 需登记，未登记不能自动代码上线。

健康样本目录必须以显式共享卷接入 Bus/Enrichment/Site/Scheduler；source IDs 与 Site IDs 的配置字段分别是 sources/sites，不能混用。原服务默认未开启这些写入，也未向其生产 Compose 自动加卷。

### 4.3 安全和测试不是形式上的绝对证明

符号级 diff 与离线回归验证业务范围，并不能静态证明任何 Python 修改都没有隐蔽副作用；正常与失败 fixture 的质量仍需人维护。脱敏是结构字段加模式规则，不是任意日志零泄密的数学保证，不应喂全库/full env/full Cookie。

模型网络只允许配置的精确公开 HTTPS 域名，provider 的实际附加域名/认证刷新必须经 smoke 核实再调整；不应为联网失败开放整个互联网。证据 HTTP 只提供冻结关联信息；当前更完整的日志/DOM/截图仍需可信收集程序产出脱敏 fixture，Worker 不直接控制原 Profile。

### 4.4 运维与费用

一个 Coding Worker、每事故最多三轮、每轮 20min、每日六个新轮次、每日 7200s 预留预算、资源每日三次代码上线。预留 wall budget 不等于精确供应商账单；已记录 SDK usage，但没有可靠美元成本上限。认证目录隔离不隔离同一账号的总额度。

样本按每源最多 1000 条/近七日裁剪。已关闭（STABLE/CANCELLED/ROLLED_BACK）事故的重要证据至少保留 30 日，随后按事故压缩归档；只在归档内容逐字节确认后移除对应的独立 evidence JSON。每 tick 最多处理一个事故，不触及候选 Git、部署收据、未结人工事项或 Codex session。本轮只在测试临时目录验证此行为，没有删除工作区或生产材料。压缩归档、候选分支和会话仍需长期磁盘配额/离线迁移策略，当前不自动删除这些历史。

模型只能报告登录/challenge/订阅问题。自动 config/镜像回滚不覆盖人类新版本；发现缺旧 canary、协议迁移或共享核心问题时进入审阅，不能重新造 auth_state、Chrome 或第二条消息管线。候选生成后生产基线变化会要求明确 rebase，不盲加旧补丁；首版不会自动解决分支冲突。多个发布域名的正文需为相应 publisher 登记 ownership/canary，不能拿采集源的首页代表第三方正文。

## 5. 本地入口与验证

入口：`uv run doxagent-source-maintenance --help`。默认关闭验收：`uv run doxagent-source-maintenance guardian --once`。status/inspect/issues 读取独立账本；adopt/replay/retry/cancel 均为显式运维入口，不是给模型的任意 SQL/命令通道。

本轮测试含默认无副作用、15min/不同操作、Body 比例与权益、共享归并、off-window、三轮观察、SQLite 预算/lease/CAS、Worker 容器权限、SDK Thread 续跑、验证反馈、孤立 round 恢复、取消后释放槽位、diff 越界、证据脱敏/跨平台 hash、只读证据数据库、30 日归档、A→B 补丁继承/只回滚 B、人工镜像漂移与候选期间基线变化。冻结的基线测试及其 conftest 在独立验证批次执行，避免候选测试替换原断言或 fixture。

验证结果（本地、offline）：

- 最后一次维护模块定向测试：**33 passed**，包括新增的“历史 driver 失败不得重开稳定事故”。
- 扩展至采集、分发、Site Strategy、生命周期和正文回归的最近一轮：**123 passed / 1 deselected**。最后上述历史 driver 用例及其修改随后又通过全部 33 项维护定向测试，未重复整轮扩展集合。
- 排除的是既有 `test_api_separates_worker_and_admin_tokens`：令牌断言完成后，其服务后台 egress probe 未 mock，offline teardown 捕获真实 HTTP transport 尝试。请求被边界拦截；没有为跑绿测试开放真实网络，也不把这项排除写成通过。
- 新模块/测试和涉及的业务 hook 文件 Ruff 全部通过；`uv lock --check`、`git diff --check` 通过。独立 Compose YAML 已解析，所有服务均受 opt-in profile 控制。
- 默认 guardian 实测输出 disabled，`data/source-maintenance` 没有创建；未启动维护容器。

这些结果不把 fake Docker/SDK 通过等同生产能力。没有真实维护账号、生产发布、付费正文、完整市场窗口和长期资源验收。

实现复用项目 `openai-codex==0.159.3`，未改变锁定依赖或业务模型。接口依据：[OpenAI Codex SDK 官方文档](https://learn.chatgpt.com/docs/codex-sdk)，并核对了本地锁定 SDK 的实际类型；本轮未做账号可用性推断。
