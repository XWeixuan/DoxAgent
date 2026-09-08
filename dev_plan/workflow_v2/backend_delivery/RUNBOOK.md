# DoxAgent V2 后端运行与恢复

本目录记录本地实现与离线验证。生成配置和执行测试不等于生产部署、模型研究、Paper 成交或 Live 开放。四项外部验收分别记录，不能相互代替。

## 1. 进程及路径

| 进程 | 入口 | 写入责任 |
|---|---|---|
| API | `python -m doxagent.api_v2.cli` | Runtime 控制事务、Bus Binding 事务、读库 view/cursor；不执行模型或 broker 请求 |
| 控制处理器 | `python -m doxagent.v2_control.worker --runtime-db ... --initialization-db ... --bus-db ... --scheduler-db ...` | 控制步骤、镜像和 ACK 对账 |
| 投影处理器 | `python -m doxagent.v2_read.cli project --read-db ... --source KIND=PATH ... --event-root ...` | 读库及初始化用量 spool；其他源只读 |
| V2 scheduler | `python -m doxagent.runtime_scheduler.v2` | 已接纳 V2 ticker 调度；不构造 V1 service |
| intent delivery | `python -m doxagent.persistent_runtime_v2.delivery_worker --runtime-db ...` | 已正式释放 intent 的持久交付；全部 ticker 停止后仍运行 |
| 原领域 worker | 初始化、Message Bus V2、Codex worker、trade executor 原入口 | 实际研究、采集、订单状态机；使用本次 gate-aware 代码 |

API 默认绑定本机。认证必须提供 `DOXAGENT_DASHBOARD_SUPABASE_URL` 与 `DOXAGENT_DASHBOARD_SUPABASE_PUBLISHABLE_KEY`，开发者身份来自可信服务端用户资料。不要将 service role 密钥交给浏览器。

`docker-compose.v2-backend.yml` 提供显式目录挂载。每个 `*_DIR` 应是对应数据库及 WAL/SHM 所在的本地目录；不可使用网络共享 SQLite。保持不可变工件中已有的绝对 locator 可访问：现有部署路径不同应修改挂载目标和环境配置，不能只移动 db 后忽略里面的正式引用。API 不挂载研究/Event 目录，projector 的 Runtime/Bus/研究/Event 卷为只读；初始化卷可写以排空真实用量回执。API 的 Runtime/Bus/读库目录可写是实际权限边界，不宣称表级隔离。

## 2. 初次迁移

在受控维护窗口停相关新工作写者；已有交易管理必须保留兼容 gate 的执行器责任。以下路径用实际已验证路径替换，命令不自动搜索业务库。

```powershell
python -m doxagent.v2_read.cli migrate --dry-run --read-db READ_DB --runtime-db RUNTIME_DB --source runtime=RUNTIME_DB --source bus=BUS_DB --source initialization=INIT_DB --source research=RESEARCH_DB
python -m doxagent.v2_read.cli backup --backup-dir NEW_BACKUP_DIRECTORY --source runtime=RUNTIME_DB --source bus=BUS_DB --source initialization=INIT_DB --source research=RESEARCH_DB --artifact-root IMMUTABLE_PUBLISHED_ROOT
python -m doxagent.v2_read.cli migrate --backup-dir ANOTHER_NEW_BACKUP_DIRECTORY --read-db READ_DB --runtime-db RUNTIME_DB --source runtime=RUNTIME_DB --source bus=BUS_DB --source initialization=INIT_DB --source research=RESEARCH_DB
```

SQLite Backup API 包含 WAL 中已提交内容；每个源有独立一致性水位，跨库不是原子快照。`backup` 生成 SHA-256 manifest，选定工件在复制前后校验。源库缺失应修正路径或用原领域 provisioning 命令建库，不用空库掩盖缺失。migrate 只增加控制/采集/投影结构，保留 Policy 消费、消息 cursor 与 `te_*` 账本。Event birth identity 需对每个正式分支数据库执行 `--source events=EXACT_BRANCH_DB` 的迁移，继承副本保留身份；来源不可证的历史身份明确标记。

## 3. 历史回填与身份分类

对 dry-run 返回的表使用以下有界命令，重复直到 captured=0；进度持久存在源库 `v2_backfill_checkpoint`。

```powershell
python -m doxagent.v2_read.cli backfill --source runtime=RUNTIME_DB --table runtime_v2_cases --limit 100
python -m doxagent.v2_read.cli import-history --dry-run --runtime-db RUNTIME_DB --source runtime=RUNTIME_DB --manifest HISTORY_JSON
python -m doxagent.v2_read.cli import-history --runtime-db RUNTIME_DB --source runtime=RUNTIME_DB --manifest HISTORY_JSON
```

Manifest 格式：`format=doxagent.v2.history.1`、`actor`、`entries[]`；每项包括 `source`、`source_seq`、`ticker`、`entity_id`、`classification`、`evidence:[{path,sha256}]`。分类只接受 BUSINESS_V2/CANDIDATE/ACCEPTANCE_TEST/PROVENANCE_UNVERIFIED；按精确 source receipt 核验对象与 ticker，不能按目录或 model 猜归属。分类不可静默覆盖。初始化 run、activation、Case、StandardMessage 是当前支持的历史入口；其后重新投影/修复 gap，不重跑任何模型。

历史 ticker 接入需要核对已有 active、Bus/scheduler 事实及原模式。为交易模式显式配置 ticker profile binding：

```powershell
python -m doxagent.v2_control.admin --runtime-db RUNTIME_DB --ticker MU --mode PAPER_TRADING --profile-revision IMMUTABLE_PROFILE_REVISION --actor OPERATOR
```

更新已有 binding 增加 `--expected-profile-revision OLD_REVISION`。profile 必须由既有执行器仓库验证环境；历史记录和 CAS 修订留在 Runtime 库。没有证明的 Live 模式不会从全局 profile 导入。随后使用同一公开 POST /tickers、REUSE_ACTIVE 和明确模式完成接纳及消费者 ACK。不要直接 SQL 打开 analysis/new-intent gate；不要把历史 monitoring Case 改写成交易输入。

## 4. 建立、验证及切换读库

```powershell
python -m doxagent.v2_read.cli rebuild --read-db NEW_GENERATION_DB --source runtime=RUNTIME_DB --source bus=BUS_DB --source initialization=INIT_DB --source research=RESEARCH_DB --event-root EVENT_ROOT
python -m doxagent.v2_read.cli diagnose --read-db NEW_GENERATION_DB
python -m doxagent.v2_read.cli verify --read-db NEW_GENERATION_DB --source runtime=RUNTIME_DB --source bus=BUS_DB --source initialization=INIT_DB --source research=RESEARCH_DB
python -m doxagent.v2_read.cli switch --read-db NEW_GENERATION_DB --alias ACTIVE_ALIAS_JSON --source runtime=RUNTIME_DB --source bus=BUS_DB --source initialization=INIT_DB --source research=RESEARCH_DB
```

rebuild 从源 outbox 建影子 generation，追赶高水位并验证数据库完整性与 gap=0，生成 `.rebuild.json`。持续写入导致未追平时，使用 project 在该影子库继续追赶，修复缺口后运行 verify 重新生成验证报告；不能跳过 gap 强行切换。切换前暂停旧 projector，追平影子水位；原子替换 alias 后重启 API/projector 以打开新 generation。新进程对其他 generation 的 view/cursor 返回 410，由客户端重新建立基线。旧数据库保留至少 24 小时及运维回退期；不要覆盖原文件或复用 sequence。

读库只影响页面，故障不撤销已接管交易。普通诊断应检查 source_health、checkpoint、gaps 与消费者 ACK；数据库存在不代表 worker 活跃，更不代表 broker 可交易。

## 5. 服务接入

构建校验过的镜像后，以明确服务列表启动 API/control/projector/delivery/scheduler。原初始化、Bus/Codex/执行器必须使用同一份新代码、同一组数据库和正式工件 locator。不能同时运行旧 scheduler 或第二个相同账户下单 writer。

```text
docker compose -f docker-compose.v2-backend.yml --profile v2-backend up -d v2-api v2-control v2-projector v2-delivery v2-scheduler
```

运行该命令是部署操作；本次离线开发没有执行它。首次接入先验证 /auth/me、/capabilities、控制 worker heartbeat 和 source coverage。Paper/Live binding 仅代表后端模式映射，broker 连接、账户与报价准入仍由执行器验证。

## 6. 故障恢复与保留

- 控制失败：保持 gate 关闭，按 operation_id 重试未完成步骤；网络重试复用原 Idempotency-Key。不要新建重复初始化。
- pause/remove：继续运行 delivery/executor。旧 intent 的期限及原重试规则仍有效；不撤单、不自动平仓。
- 投影失败：gap 按来源/事件退避修复，健康来源继续推进。历史缺失用 coverage 表达，新采集失败需修复，不能永久伪装成历史缺失。
- 用量：先落本地 spool，再发布初始化 ledger；spool 可重放，同 invocation id 不重复收费。`V2_USAGE_CAPTURE_FAILED` 需排查磁盘/权限，并记录对应 coverage 缺口；不得重跑已收费的模型来补统计。
- `python -m doxagent.v2_read.cli gc --read-db READ_DB --limit 500` 每次有界清理过期 tokens 和无有效 view/cursor 依赖的历史。保留 commit 去重身份及不可变内容；不删除业务 source outbox。
- 回退 API/读投影可使用上一已验证 generation。启用 gate 后禁止退回绕过 gate 的旧写者；先停止新分析/新 intent，保留旧交易管理，走兼容迁移恢复。

## 7. 验证命令

```text
python scripts/audit_v2_backend.py --output dev_plan/workflow_v2/backend_delivery
python -m pytest tests/v2_backend -q
python scripts/check_v2_baseline.py
python scripts/benchmark_v2_backend.py --output dev_plan/workflow_v2/backend_delivery/performance.json
```

路由/OpenAPI 79/79 只证明结构覆盖，不能代替业务验收。报告中的本机基准不是生产 p95；本地回归、部署探针、真实研究与真实成交分别标识。
