# V2.1 生产部署与 MU / BE / INTC 研究资产迁入方案

日期：2026-10-12。远端实查主要窗口：北京时间 20:15—20:28。本轮仅进行代码、文件、容器和数据库只读排查，产出部署方案；未部署、未切换生产活动版本、未修改生产配置，也未发起模型或交易调用。

## 1. 结论与实施边界

本地已经具备 D2/D3 v2.1 的主要消费者适配，前端也已支持新结构；远端仍在运行多个日期、多个补丁版本混合的旧镜像。本次不能只更新初始化 Worker，再把三个 ticker 的最新测试库复制成生产库：这样会留下旧运行时消费新版合同的问题，还会覆盖生产 Event Library、监测配置和交易状态。

采用以下路径：**补齐少量生产切换缺口 → 固定并部署兼容新旧合同的发布镜像 → 按白名单迁入已完成的研究链及可维护基线 → 每个 ticker 一次性切换 D1/D2/D3 三个引用 → 验证实际消费者与前端。** 不重跑整套研究，不重做 Event Library，不重做 O4，不建新的发布平台。

“生产环境”沿用现在的 PAPER_TRADING 模式、账户和交易配置。本次不包含切到实盘。RKLB 不参与研究资产替换，但共享服务必须继续兼容它及所有历史在途任务。

必须原样保留：

- 当前生产 Event Library 的活动版本、实际 root 和历史；新 D3 自身记录的研究来源 Event 引用也保留。
- 当前监测配置、人工关键词修改、源绑定、启用状态、间隔、时间窗和删除标记。
- ticker 控制状态、控制 epoch、账户/执行 profile、消费账本、WATCH、候选、Case、消息游标、订单、成交及历史执行包。
- Site Access / Chrome 的独立卷、身份、浏览器 profile、认证与代理配置。

允许变化：发布代码/镜像与新执行包；目标 ticker 的 D1/D2/D3 活动引用；由正式激活自然产生的 revision、ACK、投影、历史和版本预留。保留配置不意味着冻结正常消息入库、已有任务完成等业务活动。

## 2. 当前部署与必要条件

### 2.1 真实部署入口与版本

生产主机为 SSH 别名 `doxagent-sg` 对应的新加坡 `VM-0-15-ubuntu`，仓库 `/home/ubuntu/doxagent`，Compose project 为 `doxagent-v2`。数据卷实际位置为 `/var/lib/docker/volumes/doxagent-v2_v2-data/_data`，容器内 `/data`。

远端 Git HEAD 为 `b0f1caf50060b34483f13020a9d173ef57933afa`，工作树有大量线上补丁和覆盖文件。本地审查起点 HEAD 为 `dfc202b1`，标题虽写“部署v2.1工作流”，但不能据此判断已经上线。本地另有并行进行的消息源/Site Access 修改，最终发布必须重新固定有效文件清单。

`docs/server-ssh-deployment.md` 中旧香港主机和 `/root/doxagent` 不能作为此次实际部署入口。以 SSH 实测、现有容器 Compose labels、有效配置和 `docs/ssh-connection-guide.md` 的新加坡连接信息为准。

| 服务 | 实查运行版本/现象 | 本次动作 |
|---|---|---|
| API / Web | `runtime-title-search-20261009` | 部署新后端合同及同批前端构建，保留已有搜索等补丁 |
| Runtime scheduler | `event-maintenance-20261006` | 升级消费者、维护及执行包冻结逻辑 |
| Codex Worker | `maintenance-worker-20261003` | 升级原生合同、节点、MCP 子进程和 workspace 支持 |
| Initialization / O4 | `codex-gpt6-research-0.159.3` | 部署统一兼容版本；迁入不触发整套研究/O4 |
| Guardian | `codex-gpt6-guardian-0.159.3` | 核对共享合同与控制逻辑后随统一后端版本发布 |
| Projector / delivery / executor | 各自历史 trade 补丁镜像 | 统一升级涉及方向消费、WATCH、显式 Condition 的部分，带入历史交易补丁 |
| Message Bus / enrichment / Site Access | `source-storage-20261009-r1` 等独立补丁镜像 | Bus admission 必须更新；抓取与正文补全补丁必须先纳入发布清单，其他服务按实际差异决定是否更新 |
| Chrome supervisor | 独立服务/数据卷 | 保留；仅在资源事故定位确有需要时调整 |

不能在远端脏仓库直接 `git reset/pull`，不能对整个 Compose project 执行 `down` 后重建。现有容器来自多个 Compose override，不能假设单个基础 YAML 已代表全部线上参数。

### 2.2 生产稳定性先决条件

1. **有服务持续重启。** Control、CDECR executor、trade delivery 实查存在大量重启，退出码 137；日志同时出现 SQLite `database is locked`。这些事实尚不能证明“锁异常导致 137”。需逐服务定位退出前后的容器事件、宿主 OOM/资源治理记录、进程与 writer lease、长事务和锁等待。单次检查未发现长持有 POSIX WRITE lock，不能据此排除间歇性写争用。
2. **已存在真实内存压力。** 20:08 左右内核记录 Chrome 的 memcg OOM；应用 slice 约 High 14 GiB / Max 15 GiB，主机 16 GB RAM，swap 2 GB 已满。不要把它和所有重启事件直接等同，也不要通过增加并行度掩盖。修复应基于进程归属：限制或回收本任务系统管理的异常浏览器会话，校正重复 runner、单服务配额与健康重启策略，沿用已有资源治理，不另造调度器。
3. **磁盘不够照搬全量迁移流程。** 根盘约 177 GB、已用 152 GB、可用 18 GB。research / initialization / runtime / bus / read 五库合计约 53.2 GiB，其中 read 约 29.1 GiB、bus 约 19.0 GiB。现有完整备份加默认 20 GiB 余量远超当前可用空间。还未计入 WAL、workspace、镜像构建和文件备份。

部署前先完成一次有针对性的运行故障排查和资源预算。推荐扩容后实施；容量依据实际备份、构建、WAL 峰值和研究文件计算，可将增加约 100 GB 作为初步预算。也可用经过恢复验证的异机备份降低本机占用，但仍必须留出构建和运行空间。不把删除镜像、清理研究目录或清空读库作为默认准备步骤。

可执行的放行标准：相关服务停止无进展重启，锁等待有可解释的持续时间并能恢复；不存在同一业务 writer 的重复实例；资源余量覆盖实施步骤；旧任务可续跑。允许正常重试和短暂 SQLite 写竞争，无须引入“零重试”门槛。

### 2.3 访问与验证边界

远端 `/healthz` 为 200，认证配置接口可达，未认证 `/auth/me` 为 401，符合认证行为；这些结果不能证明研究页、交易消费者或维护节点可工作。本轮未做登录后的浏览器验收，也未执行真实模型调用。

远端 SDK 目前混用 0.159.3 和 0.144.4；IBKR `ibapi` 实查为 10.49.2。新镜像按最终 lockfile 安装，并验证实际 Worker SDK、CLI、模型枚举和 Data MCP 子进程导入路径。不得因统一镜像丢失 IBKR 和已有源抓取依赖。沿用当前节点模型路由及账户可用模型，不把历史 Pilot 的单次 `gpt-6-sol/high` 要求误套到所有生产节点。

## 3. 要迁入的研究资产

### 3.1 已锁定的最新完成链

三套产物已经在远端独立测试目录，无须从本地重新上传整套数据库。应从这些源做选择性迁入。

| ticker | 源数据库 | D1 / D2 / D3 run | 源 D3 版本 | Shell / Policy |
|---|---|---|---:|---:|
| MU | `/data/mu-v21-test/research.sqlite3` | `mu-v21-20261010-02-d1` / `mu-v21-20261010-02-d2` / **`mu-v21-20261010-02-d3-resume1`** | 22 | 12 / 262 |
| BE | `/data/be-v21-test/research.sqlite3` | `be-v21-20261011-01-d1` / `be-v21-20261011-01-d2` / `be-v21-20261011-01-d3` | 16 | 6 / 124 |
| INTC | `/data/intc-v21-test/research.sqlite3` | `intc-v21-20261011-01-d1` / `intc-v21-20261011-01-d2` / `intc-v21-20261011-01-d3` | 17 | 6 / 173 |

MU 原始 D3 v21 是中断后的旧结果，不能替代已完成的 resume1。源 D3 四个发布文件及 manifest 的哈希已核验；本地使用当前真实解析器验证，三套均为：

- D2：`document2.v2.1`；D3：`document3.v3`；projection：`document3.runtime_projection.v5`。
- D3 的 D2 引用与对应 D2 run / artifact / SHA 匹配；projection 等于 canonical PolicySet 重新构造的结果。
- D1 的 Future Nodes、Entity Relations、Entity Network Report 均有可用节点产物。迁入时保留这些独立 D1 产品及 D2 对它们的引用。

源 `document3.json` SHA-256：

```text
MU    7b55edcda0dd2d354543544845d6ec973f80bd7be591df021fe642c56b38213a
BE    3fd35a30f0a0288525a35635c993e5d8d8fa8de054ca39bd4171297f0452f67a
INTC  8e280dfa1a3fc7cc1d1027b674110a592a5c338d259fab356721e0aac45ddc07
```

正式导入前重新核验同一源 run 和四文件哈希，避免把后续维护产生的新内容悄悄混入此次冻结输入。操作 manifest 同时记录 D1/D2 的 artifact 清单、正文 SHA、引用闭包和源数据库备份标识。

### 3.2 “已完成”不等于无业务缺口

三套均存在 PARTIAL 状态，D3 当前为 STAGED；这不是简单的未完成进程，但也不能改成 COMPLETE：

- MU：剩余研究缺口及 2 个 late Lead coverage 项，需核对覆盖账本和最终收录情况。
- BE：剩余研究缺口及 2 个 late Lead coverage 项。
- INTC：剩余研究缺口、3 个 late Lead 项，另有 4 个 `final_edit_incomplete`：chiplet procurement、advanced manufacturing credit cash basis、equity financing proceeds per share、secure enclave public capital terms。

实施前对这四个 INTC 编辑项读取最终 Policy 全文、edit target、最终校验和审查记录，判断是“修订已生效但收尾回执不完整”，还是“错误仍在被接纳的交易条件中”。前者保留真实状态并登记；后者在独立派生 release 做最小修复，重构 projection 和四文件哈希后再迁入。不能因结构校验通过直接认定内容安全，也不必为了 PARTIAL 把整套 D1/D2/D3 重跑。

其余缺口逐项标明是否影响已接纳 Policy 的独立充分条件。未收录线索或明确隔离的研究缺口可保留并进入后续 MAINTAIN；已知错误实际参与触发则先修复。该项是内容验收，不与文件可读性混为一个检查。

### 3.3 正式版本号与 Event 来源

实查生产 D3 版本分别为 MU 21、BE 15、INTC 18。源 INTC 17 **小于**生产 18，不能强行覆盖同名版本，也不能简单取源版本作为“最新”。

目标版本使用生产 `StateV21.reserve()`，以正式、staged 和 reservation 的最大值为底分配。若期间无新版本，预计为 MU 22、BE 16、INTC 19；**实施时动态分配，不能硬编码预计值。**

三套源 D3 研究来源是 Event Library v1，而生产活动 Event 实查为 MU v39、BE v21、INTC v31，各自有不同的 runtime-maintain root。两者语义不同：

- `PolicySet.event_library_ref`：当时研究使用的来源，保留 v1 及原始引用。
- 新 activation 的 `event_library`：沿用切换时最新生产 root/version，供运行时当前消费。

不得为了“看起来对齐”把来源 v1 改成生产版本，也不得把生产 Event 回滚到 v1。正式切换前读取当前 revision 后整体保留该引用；生产维护在审查期间已继续推进，早先采样值不能当作切换时值。

## 4. 前后端现有适配与剩余改动

| 链路 | 本地实现现状 | 上线前工作 |
|---|---|---|
| 初始化 | 新 submit 显式选择 D2 v2.1 / D3 v2.1；老记录默认值兼容旧版 | 沿用，不全局篡改 RunRecord 历史默认值 |
| W2 / W3 | 原生 v3 Policy / v5 projection、Condition 归因、按方向 READY 消费、WATCH 已有实现 | 部署到全部实际消费者；真实冻结包与行为验收 |
| MAINTAIN | 可按 base schema 选择新版，保持单节点；依赖 staged release 和实际 workspace | 迁入完整维护基线，补齐执行包 atlas 冻结与本地大文件发布 |
| O4 / 监测 | 新合同读取已适配 | 本次不重新运行 O4，修复保留配置路径的副作用 |
| 读模型 / API | 原生 D2/D3、方向使用状态、WATCH、Condition 等已有实现 | 显式更新 CDC、升级镜像、处理历史投影缺口 |
| 前端 / 导出 | Shell/Unit、四块内容、新 Policy/Condition、calibration/transmission/ref、状态与导出已有适配 | 同合同重新构建，使用正式迁入数据验收；无需页面重构 |

此前本地回归通过是复用基础，不代表线上已完成。远端抽查镜像甚至没有 `policy_consumption.py`、`policy_watch.py`。当前活动执行包只有旧的 assets/compatibility/prompts，缺少新版 orchestration revision 和 contracts；不能让 native v5 使用它。

### 4.1 修复正式激活提交时的并发覆盖

位置：`src/doxagent/ticker_initialization/repository.py::activate()`。

提交阶段对 ACTIVATE / REPLACE_ARTIFACT / ROLLBACK 豁免 base revision 检查。虽然 submit 已检查 expected base，prepare 到 commit 之间仍可被 MAINTAIN 推进，从而用旧快照覆盖新 Event 或其他引用。

改为：同 revision 重入仍幂等返回；其他操作在 commit 均要求 `current_id == staged.base_revision`。冲突返回明确的 active revision changed，重新读取当前完整引用后重建激活，不强行覆盖。回滚同样必须基于当前 head，不能恢复一个过期全量快照。

补测：submit 后 Event 维护推进导致 commit 拒绝；重基后仅 D1/D2/D3 改变；重复 commit 幂等；旧正常初始化仍可完成。

### 4.2 保留监测配置必须有真正不写入的路径

位置：`ticker_initialization/activation_adapter.py`、`configuration.py`、operations/节点 inputs 及 Bus admission。

当前 research-only activation 即使保留原 monitoring ref，仍复制 CandidateConfiguration 并 `prepare()`；其中 bootstrap / materialize default bindings 可能补默认绑定、调整旧轮询间隔，最终 install 还会改配置版本。这不满足此次“保留监测配置”。

增加明确的 `preserve_monitoring_configuration` 激活参数，贯通 submit 和 activation.prepare：

1. 本次迁入启用该参数，且只能保留当前 base 中同一 monitoring ref；不能同时指定新配置。
2. 跳过 CandidateConfiguration 的 prepare、默认绑定生成和重新 install，直接沿用正式配置引用。Bus 对已生效同一配置执行确认，不另行重装。
3. 新初始化、显式重配置继续沿用原行为；不修改所有 bootstrap 的全局语义。
4. 切换前后校验有效配置语义摘要：绑定及参数、关键词、enabled、interval、time window、tombstone、相关 profile。排除正常运行计数等动态字段，但不能排除实际业务配置。

不把“没有重新运行 O4”误当作配置一定没有变化的证明。

### 4.3 正文和 MAINTAIN 基线迁入

位置：`codex_runtime/repository.py` 对应 SQLite repository、`codex_runtime/published_storage.py`、`ticker_initialization/activation_adapter.py::_documents_available()`、W3 context preparer、`workflows/codex_document3/{state_v21,publication_v21,orchestrator_v21}.py`、`v2_read/cli.py`。

实际不一致：

- 源测试 D2 有外部正文，生产未配置这些测试目录的 storage provider。MU 对应 16 份发布正文仅在源测试 published 路径，缺少旁边的 published-contents 缓存。
- activation 可用性检查需要 `content_text`；W3 读正文需要 `content_text` 或已配置 storage。读投影却可从 `published-contents/<sha>` 读取。因此“前端能展示”不足以证明 W3 可用。
- 新版 MAINTAIN 还会按 staged receipt 经正式 Worker 读取 base 的 `document3.json` / `coverage_map.json`。只迁 PolicySet 行无法维护。

采用最小统一落地方式：

1. 按源 PublishedDocument / artifact 逐个解析真实外部文件，核验字节长度、SHA 和 UTF-8；迁入生产时为这些选定 D1/D2 发布记录物化本地 `content_text`，保留 artifact_id / run_id / 正文 SHA / 大小。外部 location 改为本地可用表示，并在迁入 manifest 保留源 location；不改正文、不改来源身份。
2. 同时保存不可变 `published-contents/<sha>`，覆盖读投影和离线恢复；碰到同 ID 同 hash 幂等复用，同 ID 不同 hash 拒绝覆盖。
3. 把派生 D3 release 四文件、manifest、staged receipt、最小 state 和 candidate handoff 置于正式 `/data/workspaces/<adoption_run>/...`。通过正式 Worker 读取并核验，不能让生产 Worker 改挂整个测试 workspace root。
4. 导入 D1/D2 本 run 的必要 runtime records、bundle、artifact、published document、来源/引用记录；对引用到的 run 做闭包补齐。保留 Future Nodes 和两类 Entity Map 独立产物。D3 引用文件与研究证据保留可访问归档，manifest 映射源与目标路径，不搬入源 Worker JobStore。
5. 补充备份文件范围：现有 `v2_read.cli.backup` 未包含 published-contents，部署备份应显式纳入它、实际 Event root 和此次依赖的 workspace/manifest；数据库备份不能替代文件备份。

另补正常生产后续发布：`finalize_candidate_v21()` 目前 >2 MiB 必须 external storage，但生产 MAINTAIN 清除了外部 storage 配置，新 Policy 集合变大后会失败。SQLite 本地正式发布采用与 managed D1/D2 一致的本地正文保存方式，不因 2 MiB 的外部传输阈值强制引入外部服务；真正的远程 storage 分支继续保留其限制和哈希校验。

注意：managed D2 已有本地大正文路径，不能错误地再给所有 D2 增加外部存储依赖。测试必须经过真实 initialization adapter / MAINTAIN publication 调用路径，包含 >2 MiB、Worker 读取和 W3 读取，不能只测试缓存 helper。

### 4.4 增量数据库升级，明确执行 CDC 迁移

位置：`src/doxagent/production_v2.py::migrate()`、`v2_read/outbox.py::SourceOutbox.migrate()`、`workflows/codex_document3/state_v21.py`。

生产当前 migration marker 与 read VERSION 已匹配，通用 migrate 会走快捷分支，跳过 SourceOutbox 升级。实查 runtime_values 的 capture trigger 尚未包含 `policy_direction_usage` 和 `policy_watch`，新状态即使写进源库也可能不进入 API/前端。

新增一个明确的 v2.1 增量升级入口，复用现有迁移函数：

- 在已验证备份和 writer fence 下建立必要 StateV21 表，按实际来源执行 SourceOutbox.migrate。
- 核验 runtime_values 的 INSERT / UPDATE / DELETE 三类触发器均覆盖两个 namespace。
- 复用已有升级逻辑，仅对需要的 runtime_values 补捕获；让 projector 正常追赶，不重置所有业务 outbox/checkpoint，不全库重新灌历史事件。
- 完成源触发器、补捕获和读侧验证后记录单独 upgrade receipt。无需无故提高 ReadStore.VERSION 或重建 29 GiB 读库，也不通过强制通用全量 migrate 去触发 Bus bootstrap。

补测必须从“旧 marker 已存在且旧 trigger 已安装”的数据库开始，证明默认快捷分支不会遗漏升级；验证真实方向消费和 WATCH 状态可以通过 CDC 到 API。

### 4.5 冻结正确执行包与必要行为指引

位置：`persistent_runtime_v2/factory.py`、`execution_bundle.py`、`maintenance.py`，以及现有下游 prompt/skill 同步说明。

新活动包须为 orchestration revision 3，包含原生合同、W2/W3、D3、Event Library、模型路由和 `open_event_atlas`。当前 factory 只冻结 event_library / document3，maintenance 对 atlas 会从当前 prompts 补读；把 atlas 纳入新包，对新包缺少必需资产直接给出明确错误，不读取部署后漂移的 live 文件。老包保留原兼容读取规则，已冻结任务不重绑定。

多个服务启动时都可能 publish active bundle，故参与发布的 runtime 服务必须来自同一冻结代码/资产集，验收活动包 ID 和 hash 一致，不能让旧 scheduler 在新包发布后又覆盖 head。

本轮还发现当前 W3 的 `agent.md` / `revalidate_then_evaluate.md` 仍写旧的顶层 Policy 固定方向和统一四块结果，而新版 NEW + HIT 需要明确 `policy_execution.selected_condition` 或明确不交易，以及 WATCH 行为。schema 注入不能自动消除这类行为冲突。

上线前需要按已有 `d2_d3_v21_downstream_prompt_skill_handoff_20261010.md` 做最小一致性同步：方向来自所选 Condition，多个命中排序后显式选择，Policy 内独立充分 OR，按方向消费，WATCH 有效期及撤销规则。无需重写整套研究 prompt。该同步是生产行为验收的必要工作，本轮仅指出和规划，未改动提示词。

### 4.6 历史投影缺口单独诊断

实查 read model 有 6 个反复重试的历史 gap：

| outbox seq | 来源 | 当前判断 |
|---|---|---|
| 1240145 / 1240191 | 2026-09-29 MU worker_invocations | 无 Case，旧维护上下文来源未被当前 provenance 规则承认 |
| 1240148 / 1240193 | 同次 reference_deliveries PREPARED / SUCCEEDED | 同一旧维护 run 的来源证据问题 |
| 1257748 / 1257924 | 2026-09-30 MU trade_intents | `DUPLICATE_EVENT_TRADE`，远端仅记录通用 ValueError，未获完整堆栈 |

最后两项不能直接归因为“新公共 schema 不支持该状态”：当前本地 `intent_status` 是 string，trade_outcomes 也已经认识该终止状态。要在数据库副本用待发布版本重放原始 outbox payload，捕获字段级异常，验证是旧镜像缺口、依赖记录缺失，还是现行投影问题，再做精确修复。

前四项沿已有历史证据寻找 initialization/control epoch/维护调用归属；能证明则补建可追溯投影关联，不能证明则保留 PROVENANCE_UNVERIFIED 并明确隔离历史展示。不能凭 run 名伪造来源，不能删除 gap、改成成功或把未发送意图改成 READY。

新激活及新业务必须无新增同类 gap。已有历史缺口如果保持独立、不会阻断新投影，可作为明确遗留项而不是强制阻塞全部上线；若同一依赖问题影响新 activation，则必须先修。避免把无条件全量 shadow rebuild 当作发布前提。

## 5. 一次性迁入工具的具体做法

新增一个小型运维入口，例如 `scripts/ops/adopt_v21_research.py`，只负责本次已有研究链的 plan / import / activate / verify。名称为拟新增，不是现有可直接运行命令。复用现有 Repository、StateV21、finalize_candidate 和正式 activation，不建立第二套发布状态机。

### 5.1 Manifest 与幂等

每 ticker 记录：源库只读路径、D1/D2/D3 run、原始 artifact/hash、质量缺口、引用闭包、目标 run/version、派生四文件 hash、保留的 production base/Event/config refs、目标 release image/bundle，以及各阶段 receipt。

adoption run 采用稳定可识别 ID，例如 `adopt-v21-intc-20261012-<sourcehash12>-d3`，满足现有 ID 长度约束。plan 只读生成；import 才分配并持久化版本。版本与目标 run 的首次映射固定后重跑复用，不重新消耗版本号。同标识异内容报冲突，不强行覆盖。

### 5.2 Import：只增加候选，不抢活动指针

1. 验证源三段链、完整正文、引用闭包、当前 schema 和质量分类；保存源只读快照/清单。
2. 迁入选定 D1/D2 的记录和正文，测试 Worker/W3 读取。不得复制源库全部 tables，更不能复制 isolated test 的控制、Event、监测、消费或运行时状态。
3. 生产 reserve 目标 D3 version；源 release 作为只读证据保留。
4. 从源 PolicySet 派生正式 release，**业务 Policy、Policy ID、Condition ID、条件 revision、as_of、来源 D2/Event 引用和 PARTIAL 状态保持不变**；修改正式 policy_set_version 及必要发布路径身份元数据。新发布记录自身记录迁入时间，不能把研究截止时间伪造为今天。
5. 用 canonical builder 重建 projection；coverage 和 Markdown 只同步必要版本/发布元数据。可从现有发布逻辑抽出纯渲染 helper，不复制一套编排；重新生成四文件 manifest/hash/receipt。
6. `save_staged_v3()` 保存一致的 staged body/receipt，再走 `finalize_candidate_v21()` 形成正式 candidate / handoff。为空的 accepted_rearm 明确保存为空，不把迁入当作重新授权。
7. 校验源与派生 release 的 Policy 语义摘要完全一致，候选可读、MAINTAIN base 可读，确认活动引用未变。

实查三套新 Policy ID 与当前各自活动 Policy ID、历史消费 claim 的交集均为 0。保持新身份，不按标题猜测旧新策略映射，也不把旧消费记录套到无关新 ID；原账本仍完整保留。同方向一次性消费约束对新活动策略正常生效。

### 5.3 Activate：每 ticker 一次完整替换

读取切换时最新 active revision，构造完整 artifacts：

```text
document1                 = 迁入 D1 引用
document2                 = 迁入 D2 引用
document3                 = 派生正式 D3 候选引用
event_library             = 当前生产 base 的原引用
monitoring_configuration  = 当前生产 base 的原引用
```

通过现有 `submit_activation` / activation DAG 提交，携带 expected_base_revision 和 preserve_monitoring_configuration；提交和 commit 都执行严格 CAS。一次切换三个研究引用，不能连续做三个 replace_artifact 产生 D1/D2/D3 混搭窗口。

以 Bus / Runtime 的正式 ACK、有效引用镜像和 readiness 完成为接纳凭证，不手工写 policy `is_current=1`。目前正式当前版本来自 activation revision，Policy candidate 与旧 current 标记不等价。

若 base 因维护推进，重新读取并保留新的 Event/config，不强行恢复旧 base。目标研究 release 可以复用，无须重新研究。

## 6. 发布镜像与配置整理

建立一个此次 release manifest，列出有效源码 hash、prompts/contracts hash、SDK/依赖版本、镜像 digest、服务到镜像映射和 Compose 参数摘要。

本地与远端都有并行补丁，先比对当前服务的真实文件，保留消息源、正文补全、策略 supervisor、IBKR、交易投影/执行以及搜索等已上线改动。完成后冻结可审查源码快照构建；不打包整个含 eval/研究归档的目录。构建上下文采用明确 allowlist 或有效 .dockerignore，排除大体积测试材料和凭据。

后端必须把源码安装到真实 `/app/src`，不能仅临时改 PYTHONPATH；Codex MCP 子进程也须加载相同版本。拟新增的运维 scripts 当前不会被基础 Dockerfile 自动 COPY，要么明确打包该运维入口，要么使用同镜像的受控只读 bind mount，不依赖宿主碰巧有相同依赖。

前端基于相同 wire schema 生成类型并构建。核对正式 API 前缀、认证、反向代理与资产缓存；保留原域名/端口和登录配置。不增加一个“v2.1 前端”并行入口，不换一套监测 UI。

最终 Compose override 只覆盖本次确需更新的服务/image/env，继承已经核对的 volumes、资源限制、网络、能力密钥与运行模式。不打印完整 env 或导出凭据。数据卷、浏览器卷和旧镜像均保留供回退。

## 7. 执行顺序

### 阶段 A：准备与代码补齐

1. 处理第 2 节资源/重启问题并记录根因与修复；完成磁盘和可恢复备份预算。
2. 落地第 4 节修复及迁入工具；重要代码修改追加 changelog。
3. 固定源资产和 INTC 四项编辑的质量结论；冻结 release 与必要 W3 指引同步。
4. 在源库/生产数据库副本进行迁入、激活冲突、CDC、旧 Case 与新消费者回归。副本使用独立数据和网络出口控制，禁止接真实消息投递和执行账户。
5. 预构建镜像和前端，确认磁盘/SDK/导入 hash。离线校验先完成，减少生产暂停时间。

### 阶段 B：短维护窗口与兼容服务更新

1. 记录最新服务、容器、活动 heads、配置语义摘要、源 outbox/checkpoint 和在途任务。
2. 使用现有服务/lease 机制停止相关调度器产生新研究、维护与分析任务；已在模型执行中的任务先 drain 或等其持久化可恢复点，再停对应进程，不无条件杀掉。RKLB 若仍有研究在跑，也按此处理，不删除其状态。
3. 不用 ticker 的业务 PAUSE 作为部署锁：它会影响 control epoch / 任务资格，且可能禁止 initialization activation。维护窗口仅暂时停相关进程或入口，保留业务控制配置。
4. 在受影响数据库 writer 确实静止、已获得现有 WriterLock/fence 后创建一致备份。若需备份 Bus，短暂暂停其写入；外部抓取可继续的范围以实际写库关系决定，不宣称全程无停顿。记录采集间隙及续采检查，不清空游标。
5. 执行 v2.1 增量 schema / CDC 升级，再部署统一兼容后端与前端。恢复必要的 activation 消费服务；发布并核验同一 revision-3 执行包。按 image digest 与实际 `/app/src` 检查，而非只看容器 started。

### 阶段 C：迁入与逐 ticker 切换

1. import 三套候选，校验正文、引用闭包和维护基线；此时旧业务活动引用仍有效。
2. 按 MU → BE → INTC 顺序 activate。每次重新取 base，保留其最新 Event/config，等待正式 Bus/Runtime ACK 与投影可读。
3. 每 ticker 验证通过再切下一个。失败优先修复对应适配或文件问题后复用候选重试，不重跑四阶段研究。
4. 记录目标版本、activation revision、Event root/version、配置摘要、bundle、ACK 和 D1/D2/D3 最终 refs。确认 PAPER_TRADING 和账户未变。

### 阶段 D：恢复与观察

恢复原服务调度，核验积压和游标有进展；检查第一轮新维护实际读取迁入 D1/D2、当前 Event 和新 D3 基线，能生成并接纳后续 candidate。监控新 revision 的来源链、方向消费、WATCH、W3 显式选择和 projector lag；观察以事件/任务进展为准，不仅看容器绿色。

旧 Case、旧已提交 Worker 请求和旧执行包照原合同续跑；新 Case 使用新活动 refs / 新 bundle。不得清空老冻结任务后“全部重新生成”以制造版本一致。

## 8. 验收内容

### 8.1 实现回归

- 三套真实 D2/D3 fixture 解析及 canonical projection、双向 Condition、calibration/transmission/ref 无损。
- 激活提交 CAS、整体三引用切换、配置保留路径；跨库正式 ACK 失败与重试幂等。
- 旧 trigger + 旧 marker 升级，方向消费 / WATCH 的源写入到 API 展示闭环。
- 外部 D1/D2 物化、SHA/大小检查、>2 MiB 正式发布，Worker / W3 / MAINTAIN 从正式路径读取。
- MAINTAIN 同 ID 完整 upsert + rearm；迁入不触发 rearm；LONG 消费不冻结 SHORT；WATCH D/D+1 与 D+2 到期、CANCEL 优先、READY 同事务撤销。
- revision-3 冻结 atlas 和合同，旧 bundle / RKLB v2 / 旧在途任务兼容。
- 迁入重跑、版本冲突、源异 hash、激活竞争和回滚，不能造成重复候选、丢账本或配置重装。

### 8.2 远端真实链路

- MU/BE/INTC 分别可以通过生产 API 读取选定 D1 全部可用产品、D2 和新 D3；不只验证 summary。
- 正式 Worker 可读 base release；W3 可读大 D2 正文。至少在隔离副本完成一次真实模型的 W2/W3 Condition 选择和一次新 MAINTAIN smoke，确认当前账户/模型可用与指引有效；测试没有生产交易副作用。
- 前端登录后查看三 ticker 的研究和 Policy 列表/详情、双方向 Conditions、使用/WATCH 状态、Case 的所选 Condition 归因、JSON 导出。选择长正文及跨来源 refs，检查 1262/1559 px；与正式返回逐字段抽核，而非只用 mock 页面截图。
- 切换前后 Event/config 摘要匹配，原监测关键词和源绑定可见；新 Policy 数量为 262 / 124 / 173（若存在经确认的必要内容修复，以派生 receipt 记录为准）。
- 无新合同解析、文件不存在、版本冲突、冻结包不支持、方向默认 LONG、配置被重装或新 provenance gap；旧 gap 有精确处理/遗留结论。

健康检查、离线测试、真实模型、生产激活与业务接纳分别记录，不以任何一种代替其他几种。先做必要离线与隔离验证，不为上线再强制重跑全部研究 Pilot。

## 9. 回退

首选保留兼容新旧合同的新代码，通过正式 activation 将研究引用切回之前快照；保留切回时最新 Event/config，严格基于当前 revision CAS。需要更换代码时，先确认旧镜像能读已写入的新合同/namespace，否则采用带兼容补丁的上一稳定镜像，而非盲目换回历史镜像。

已发生的 consumption、WATCH、候选、Case、交易意图和成交不回滚、不清空；旧新版本均留历史。切换指针不是恢复整库，任何版本回退也不能借机撤销已发生的交易。

整库恢复仅用于明确的数据损坏灾难恢复，必须停写并对备份后发生的业务账本做恢复对账；不能作为普通发布失败后的快捷操作。

## 10. 工作包与完成标志

| 工作包 | 交付物 | 完成标志 |
|---|---|---|
| P0 运行前提 | 重启/锁/资源诊断与容量、备份清单 | 服务可恢复推进，实施空间够，备份可验证 |
| P1 切换安全 | activation CAS + preserve config 修复 | 并发 Event 不被覆盖，配置零业务变更 |
| P2 本地资产可消费 | 正文物化、D3 大文件发布、维护基线与备份文件范围 | 正式 Worker/W3/MAINTAIN 均能读，副本验证通过 |
| P3 运行时一致性 | CDC 增量升级、完整 frozen bundle、必要 W3 指引同步 | 新状态入读模型，新旧任务分别按其合同工作 |
| P4 选择性迁入 | 小型运维入口、三 ticker manifest/derived release | 幂等导入，只增加候选，来源和语义可追溯 |
| P5 生产发布 | 有效补丁发布清单、镜像 digest、逐服务 rollout receipt | 实际进程加载一致代码，无源抓取/交易补丁回退 |
| P6 接纳与维护 | 三次整体 activation receipt、UI/API/模型/维护验收 | 研究成为最新，原 Event/config/交易状态保留 |

暂不需要业务方额外选择架构方案。唯一不能由部署工具代为“默认安全”的是 INTC 等已知内容缺口：应按最终正文判断并处理实际触发错误；本轮没有完成该语义审查，不将其标记为已通过。

## 11. 证据与后续复核

本轮证据在 `eval/v21_deployment_preflight_20261012/`：

- `remote_inventory.json` / `remote_details.json`：容器、Git、数据规模、研究和生产活动引用、release 与文件可读性。
- `remote_images.json`：实际容器模块 hash、SDK 与依赖版本、必要凭据是否存在（不含凭据值）。
- `remote_projection.json`：CDC / marker / 读模型状态。
- `remote_health.json` / `remote_projection_gap_sources.json`：HTTP 状态及六个历史 gap 的来源分类。
- `local_contract_validation.json`：三套真实 D2/D3/projection 校验；`intc_document2.json` 为从远端源只读取得的核验正文。

相关设计与源测试记录：

- `dev_plan/workflow_v2.1/d2_d3_v21_downstream_consumer_implementation_plan_20261010.md`
- `dev_plan/workflow_v2.1/d2_d3_v21_downstream_consumer_implementation_acceptance_20261010.md`
- `dev_plan/workflow_v2.1/d2_d3_v21_downstream_prompt_skill_handoff_20261010.md`
- `dev_plan/workflow_v2.1/mu_v21_formal_remote_test_20261010.md`
- `dev_plan/workflow_v2.1/be_v21_formal_remote_test_20261011.md`
- `dev_plan/workflow_v2.1/intc_v21_formal_remote_test_20261011.md`

生产仍在运行，不同库的顺序只读并非跨库原子快照；采样中 revision 继续变化不能直接判为不一致事故。正式实施前重新采样，并以最新 base CAS 和最终 ACK 为准。此方案交付代表排查与实施路径完成，不代表修复、部署、激活或内容验收已经完成。
