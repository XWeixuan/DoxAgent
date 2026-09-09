# DoxAgent V2 CDECR 预构建交付包开发方案

> 状态：可实施方案  
> 日期：2026-09-09  
> 范围：只拆分 ticker 初始化中的 CDECR 高内存阶段；D1、O2、D2、D3、O4、激活、Message Bus、Persistent Runtime 和交易链路继续在远端按现有 V2 拓扑运行。

## 1. 结论

采用“本地生成 CDECR 预构建包，远端正常创建初始化 run，并在 CDECR 节点原子接管该包”的方案。

该方案不改变前端现有两个初始化选项，也不改变 `REUSE_ACTIVE` 的业务含义：

- `REUSE_ACTIVE` 仍只复用某 ticker 已激活的完整 revision。没有 active revision 时仍返回 `NO_ACTIVE_REVISION`。
- `FORCE_INITIALIZE` 仍创建新的完整初始化 run。
- 当远端存在一个已发布、校验通过且属于该 ticker 的 CDECR 预构建包时，新 run 的 CDECR 节点导入该包并跳过远端 CDECR 模型执行。
- 当没有预构建包时，是否允许远端执行 CDECR 由部署配置决定。香港 4 GB 实例使用 `PREBUILT_REQUIRED`，防止意外启动高并发 CDECR 打爆内存。
- CDECR 接管完成后，远端继续生成当前 run 自己的 Runtime Activity、Frozen Snapshot 和 Event Library Delta，再正常运行 O2 及全部下游节点。

这是一项后端和运维能力，不需要前端新增“导入”“恢复”或第三种初始化选项。

## 2. 当前实现与改造边界

### 2.1 当前初始化语义

当前 `src/doxagent/v2_control/service.py` 的启动逻辑为：

1. 查询 ticker 的 active revision。
2. `REUSE_ACTIVE` 且不存在 active revision 时失败。
3. `FORCE_INITIALIZE` 或首次启动时调用 `InitializationRepository.submit(...)` 创建新 run。
4. 其他情况等待 active revision 的 Bus/Runtime ACK，并复用整套已激活版本。

因此，不能把“复用当前文档”解释成从磁盘扫描并复用一个 CDECR 文件，也不应修改其语义。

### 2.2 当前 CDECR/O2 接续边界

`src/doxagent/ticker_initialization/catalog.py` 的拓扑为：

```text
D1 ─────┐
        ├─> O2 -> D2 -> D3 -> O4 -> Activation -> Bus -> Runtime
CDECR ──┘
```

`ResearchInitializationAdapter` 为每个初始化 run 创建独立工作区：

```text
<initialization-root>/workspaces/<initialization_id>/
  registry/US/<TICKER>/runtime.sqlite3
  state/ticker_jobs.sqlite3
  exports/
  o2/
```

`TickerCDECRPipelineCoordinator.prepare_runtime_through_delta(...)` 当前完成以下职责：

1. 采集并写入历史消息源。
2. 运行 CDECR，要求 epoch 达到 `FINALIZED`。
3. 投影 Runtime Activity。
4. 冻结 Runtime Snapshot。
5. 编译 Event Library Delta。
6. 返回 `TickerPipelineResult`，供 O2 按不可变引用继续执行。

本方案只替换前两步的输入来源。步骤 3～6 必须在远端当前 run 中照常执行，避免复制本地 Event Library、绝对路径、O2 状态或其他运行态数据库。

### 2.3 现有恢复入口不能代替本方案

现有 artifact adoption/节点恢复能力要求初始化 run 和目标节点已经存在，服务于故障恢复。它不支持在没有 run 的情况下预放 CDECR，也不适合作为日常初始化入口。

本方案不会在数据库中伪造一个已完成的初始化 run；远端仍拥有完整、真实、可审计的新 run 记录。

## 3. 目标架构

```text
本地机器
  source staging
       ↓
  CDECR execution
       ↓
  finalized runtime.sqlite3
       ↓ validate/export
  <bundle_id>.tar.zst
       ↓ scp/rsync

远端 incoming
       ↓ publish CLI（完整校验）
远端 ready/<bundle_id>
       ↓ FORCE_INITIALIZE 时原子 claim，并固定到 NodeSpec.inputs
正常 ticker initialization run
       ↓ CDECR 节点导入 Registry，跳过 execute_cdecr
Runtime Activity → Frozen Snapshot → Delta
       ↓
O2 → D2 → D3 → O4 → Activation → Bus → Runtime
```

核心原则：

- 预构建包是 CDECR 的不可变输入，不是一个完整初始化版本。
- 包选择发生在 run 创建时，不能等 CDECR worker 临时扫描目录决定。
- run 的统一 `research_cutoff_at` 取自包 manifest，D1 和所有下游节点使用同一 cutoff。
- NodeSpec 只保存 bundle identity、digest 和 claim owner，不保存可漂移的绝对路径。
- 所有复制和状态迁移都应可重试、可核验、不可并发重复消费。

## 4. CDECR 预构建包契约

### 4.1 文件结构

传输包解压后只允许以下内容：

```text
<bundle_id>/
  manifest.json
  runtime.sqlite3
```

不接受 WAL/SHM、符号链接、任意附加文件或本地 `TickerJobState`。构建器在导出前必须 checkpoint WAL，并通过 SQLite Backup API 生成稳定的单文件副本。

### 4.2 `manifest.json`

建议新增严格 Pydantic 契约 `CDECRPrebuiltManifest`：

```json
{
  "contract_version": "cdecr-prebuilt-bundle-v1",
  "bundle_id": "cdecr-US-MU-20260909T020000Z-<digest12>",
  "market": "US",
  "ticker": "MU",
  "runtime_scope": "cdecr:US:MU",
  "research_cutoff_at": "2026-09-09T02:00:00Z",
  "generated_at": "2026-09-09T03:12:34Z",
  "code_revision": "<git-commit>",
  "compatibility_version": "cdecr-prebuilt-runtime-v1",
  "configuration_fingerprint": "sha256:<hex>",
  "epoch_id": "<finalized-epoch-id>",
  "epoch_status": "FINALIZED",
  "message_ids": ["..."],
  "message_set_sha256": "sha256:<hex>",
  "document_count": 123,
  "eligible_document_count": 87,
  "registry": {
    "file": "runtime.sqlite3",
    "size_bytes": 12345678,
    "sha256": "sha256:<hex>"
  }
}
```

约束：

- market、ticker、runtime_scope 必须与目标初始化完全一致。
- `research_cutoff_at` 必须带时区，且不得晚于远端当前时间。
- `epoch_status` 只允许 `FINALIZED`；`FINALIZED_NOOP` 不需要传输 Registry，后续可单独扩展。
- `message_ids` 必须去重并排序，digest 按稳定 JSON 编码计算。
- `configuration_fingerprint` 覆盖会改变 CDECR 结果的 prompt、model、reasoning、processor、relevance filter、并发和 schema 版本，但不包含 API key 等秘密。
- `code_revision` 用于审计；兼容判断以显式 `compatibility_version` 和配置 fingerprint 为主。首版要求二者精确匹配，暂不维护模糊兼容表。
- manifest 不记录本地绝对路径、密钥、provider token 或原始环境变量。

### 4.3 Registry 强校验

发布到远端 `ready` 之前，以及 CDECR 节点正式接管之前，均执行以下校验：

1. 文件大小与 SHA-256 匹配。
2. `PRAGMA quick_check` 返回 `ok`；发布 CLI 额外执行一次 `PRAGMA integrity_check`。
3. `doxagent_ticker_binding` 中 market、ticker、runtime_scope 和 binding version 精确匹配。
4. manifest 指定的 epoch 存在且状态为 `FINALIZED`。
5. epoch 的 message ID 集合与 manifest 精确一致。
6. manifest 中每个 message ID 在 Registry 中存在对应 source。
7. Registry 不存在仍为运行中、可恢复或未决状态的 epoch。
8. 文件不是符号链接，解析后的路径始终位于配置的 prebuilt root 内。

任何一项失败都不得继续导入，也不得在 `PREBUILT_REQUIRED` 模式下回退到远端 CDECR。

## 5. 远端目录与状态机

配置根目录：

```text
/data/prebuilt/cdecr/
  incoming/
  ready/US/MU/<bundle_id>/
  claimed/<control_operation_id>/<bundle_id>/
  consumed/<initialization_id>/<bundle_id>/
  rejected/<bundle_id>/
```

状态含义：

- `incoming`：上传未完成或尚未校验，初始化永远不可读取。
- `ready`：publish CLI 校验成功并通过原子 rename 发布。
- `claimed`：某次 START/RESTART control operation 已独占该包。
- `consumed`：CDECR 节点已输出可用 `TickerPipelineResult`，保留审计和重建证据。
- `rejected`：人工 publish 或接管校验失败；保存无秘密的错误 receipt。

同一 ticker 首版只允许一个 ready bundle。新包发布前应先处理旧 ready 包，避免“自动选择最新”造成隐式业务决策。

## 6. 本地构建流程

### 6.1 新增命令

```powershell
python -m doxagent.cdecr_integration.prebuilt_cli build `
  --market US `
  --ticker MU `
  --research-cutoff-at 2026-09-09T02:00:00Z `
  --output-root C:\DoxAgentData\cdecr-prebuilt
```

构建器职责：

1. 使用现有 `HistoricalNewsLoader` 获取并筛选历史消息。
2. 使用 `PerTickerRegistryResolver` 创建临时 ticker Registry。
3. 保存 source，并调用现有 `build_cdecr_workflow_runner`/CDECR executor 完成 epoch。
4. 只接受真实 `FINALIZED` epoch。
5. checkpoint、关闭所有连接，并通过 SQLite Backup API 导出 `runtime.sqlite3`。
6. 生成 manifest、digest 和人类可读的无秘密构建摘要。
7. 在临时目录执行与远端相同的 bundle validator。
8. 生成 `<bundle_id>.tar.zst` 或未压缩 bundle 目录。

为避免本地构建和远端执行出现两套 source staging 逻辑，应从 Coordinator 中提取一个小型共享函数，例如 `stage_historical_sources(...)`。Coordinator 和 builder 都调用它；不要复制筛选、fingerprint 和 message ID 选择代码。

### 6.2 本地构建不做的事情

- 不运行 D1、O2、D2、D3 或 O4。
- 不生成可直接发布的 Event Library。
- 不写远端 initialization control DB。
- 不生成 active revision、Bus ACK 或 Runtime ACK。
- 不携带本地 job DB 和路径。

## 7. 远端发布流程

上传必须先进入 `incoming`，不能直接复制到 `ready`：

```bash
scp cdecr-US-MU-....tar.zst hk:/srv/doxagent/prebuilt/incoming/

docker compose -f docker-compose.v2-production.yml \
  -f deploy/docker-compose.hk.yml run --rm ticker-initialization \
  python -m doxagent.cdecr_integration.prebuilt_cli publish \
  --archive /data/prebuilt/cdecr/incoming/cdecr-US-MU-....tar.zst
```

`publish` 在同一文件系统内完成解压、全量校验和 `staging -> ready` 原子 rename。成功后输出 bundle_id、ticker、cutoff、epoch_id、digest；失败时保留在 incoming/rejected，初始化不可见。

可补充只读命令：

```bash
python -m doxagent.cdecr_integration.prebuilt_cli list --ticker MU
python -m doxagent.cdecr_integration.prebuilt_cli inspect --bundle-id <id>
```

## 8. 初始化 run 的接管逻辑

### 8.1 在 control service 中固定包

修改 `V2ControlService.step(...)` 的新 run 分支：

1. 只在 `FORCE_INITIALIZE` 或首次初始化即将 `submit(...)` 时查询 prebuilt store。
2. 若 ticker 存在 ready bundle，执行轻量 manifest/版本检查并原子移动到 `claimed/<operation_id>/...`。
3. 使用 manifest 的 `research_cutoff_at` 创建 run，保证 D1、CDECR 和下游共享统一 cutoff。
4. 调用 `default_plan(cdecr_prebuilt_ref=...)`，把以下不可变引用写入 CDECR NodeSpec inputs：

```json
{
  "_prebuilt_cdecr": {
    "contract_version": "cdecr-prebuilt-ref-v1",
    "bundle_id": "...",
    "claim_owner": "<control_operation_id>",
    "registry_sha256": "sha256:<hex>",
    "epoch_id": "..."
  }
}
```

5. `submit` 失败且数据库中确认没有该 operation 对应的初始化 run 时，安全释放 claim 回 ready。
6. 进程在 claim 后崩溃时，由 reconcile 命令检查 operation/run 关系：有关联 run 则保持 claim；无关联且超过宽限期才释放。

这样可以避免并发 run 消费同一个包，也可以避免 worker 启动时选到另一个更新包。

### 8.2 执行模式

新增配置：

```dotenv
DOXAGENT_CDECR_EXECUTION_MODE=LOCAL_OR_PREBUILT
DOXAGENT_CDECR_PREBUILT_ROOT=/data/prebuilt/cdecr
DOXAGENT_CDECR_PREBUILT_MAX_AGE_HOURS=24
DOXAGENT_CDECR_PREBUILT_COMPATIBILITY_VERSION=cdecr-prebuilt-runtime-v1
```

枚举语义：

| 模式 | 有有效包 | 无包 | 包存在但无效 |
|---|---|---|---|
| `LOCAL_ONLY` | 不读取 | 远端执行 CDECR | 不读取 |
| `LOCAL_OR_PREBUILT` | 使用包 | 远端执行 CDECR | 明确失败，不静默回退 |
| `PREBUILT_REQUIRED` | 使用包 | `CDECR_PREBUILT_REQUIRED` | 明确失败 |

香港 4 GB 服务器固定使用 `PREBUILT_REQUIRED`。这样缺包或坏包不会触发已知会 OOM 的服务器 CDECR。

### 8.3 CDECR 节点导入

`ResearchInitializationAdapter` 在 `context.node.key == "cdecr"` 时检查 `_prebuilt_cdecr`：

1. 根据 claim owner 和 bundle_id 定位包，再做一次强校验。
2. 解析 Coordinator 为当前 run 生成的目标 binding。
3. 目标 Registry 不存在时，将包内 SQLite 复制到同目录临时文件，fsync、校验 digest，然后 `os.replace` 为 `runtime.sqlite3`。
4. 目标已存在且 digest 相同则视为前次导入已完成，直接继续；不同则报 `CDECR_PREBUILT_TARGET_CONFLICT`，绝不覆盖。
5. 在当前 run 的 `ticker_jobs.sqlite3` 中构造远端自己的 `TickerJobState`：
   - job_id 使用 Coordinator 的既有确定性算法；
   - 路径全部使用远端当前 run 的 canonical path；
   - `message_ids`、`epoch_id` 来自已校验 manifest；
   - stage 设为 `CDECR_RUNNING`，让现有 Coordinator 检出 Registry 中已 FINALIZED 的 epoch。
6. 调用现有 `prepare_runtime_through_delta(...)`。Coordinator 会跳过 historical load 和 `execute_cdecr`，随后照常计算 Activity、Snapshot 和 Delta。
7. NodeResult 正常返回 `artifacts.cdecr`，并增加 `quality_annotations=["CDECR_PREBUILT_ADOPTED"]`。
8. NodeResult 已持久化后，把 bundle 原子移动到 `consumed/<initialization_id>/...`，写入消费 receipt。

消费 receipt 至少记录：initialization_id、control operation id、bundle_id、registry digest、epoch_id、cutoff、adopted_at 和目标 Registry 路径的相对标识。

不要伪造本地 CDECR 子任务在远端执行过。前端仍能看到 CDECR 节点成功及 `CDECR_PREBUILT_ADOPTED` 注释；细粒度的本地任务摘要只保留在 manifest/构建摘要中。

## 9. 失败与恢复语义

| 故障点 | 恢复行为 |
|---|---|
| 上传中断 | 文件留在 incoming；初始化不可见，重新上传 |
| publish 校验失败 | 移入 rejected 或留 incoming；不产生 ready 包 |
| claim 后、run 创建前崩溃 | reconcile 根据 control operation 检查；无 run 的过期 claim 可释放 |
| run 已创建、导入前崩溃 | NodeSpec 已固定 bundle ref；worker 重试同一个 claim |
| 复制 SQLite 中断 | 临时文件不替换目标；下次删除同名临时文件后重试 |
| 已替换目标、状态未保存 | digest 相同则幂等复用，并重建当前 run 的 job state |
| 目标 Registry digest 不同 | 失败并人工处理，不覆盖现有证据 |
| Snapshot/Delta 生成失败 | 保留已导入 Registry；节点按现有恢复机制重试，不再运行 CDECR |
| 下游 O2/D2/D3 失败 | 使用当前 run 的正常节点恢复；bundle 已作为当前 run 的不可变上游 |
| 初始化最终失败 | bundle 保留在 consumed/claimed 供审计；默认不自动回到 ready，避免无意重复使用 |

错误码建议：

- `CDECR_PREBUILT_REQUIRED`
- `CDECR_PREBUILT_INVALID`
- `CDECR_PREBUILT_INCOMPATIBLE`
- `CDECR_PREBUILT_STALE`
- `CDECR_PREBUILT_ALREADY_CLAIMED`
- `CDECR_PREBUILT_TARGET_CONFLICT`

这些是具体可诊断错误，不应归并成普通 `CDECR_FAILED`。

## 10. API 与前端影响

首版不修改 V2 API Contract 和 `StartTickerRequest`：

```json
{
  "ticker": "MU",
  "monitor_mode": "MESSAGE_MONITORING",
  "initialization": "FORCE_INITIALIZE"
}
```

选择 `ready` 包本身就是运维侧的显式授权；生产服务器不需要前端再传 bundle_id。为了避免选择歧义，首版对同一 ticker 限制一个 ready 包。

现有初始化进度读取只需透传 `CDECR_PREBUILT_ADOPTED` quality annotation。若当前投影没有显示 annotation，可在既有 node detail/receipt 中增加该字段映射，不新增批量接口，也不把 manifest 或 Registry 暴露给浏览器。

## 11. 代码落点

### 11.1 新增文件

| 文件 | 职责 |
|---|---|
| `src/doxagent/cdecr_integration/prebuilt.py` | Manifest/ref/receipt 契约、validator、store、原子 claim/import/consume |
| `src/doxagent/cdecr_integration/prebuilt_cli.py` | build、publish、list、inspect、reconcile 命令 |
| `tests/test_cdecr_prebuilt.py` | 契约、校验、目录状态机、导入幂等性 |
| `tests/test_ticker_initialization_cdecr_prebuilt.py` | control → run → CDECR → delta 的聚焦集成测试 |

### 11.2 修改文件

| 文件 | 最小修改 |
|---|---|
| `src/doxagent/settings.py` | 增加 execution mode、prebuilt root、max age、compatibility version |
| `src/doxagent/ticker_initialization/catalog.py` | `default_plan(...)` 可选写入 CDECR prebuilt ref；默认行为不变 |
| `src/doxagent/v2_control/service.py` | 新 run 前 claim 包、使用包 cutoff、失败释放/恢复 |
| `src/doxagent/ticker_initialization/research_adapter.py` | CDECR 节点导入并 seed 当前 run job state |
| `src/doxagent/cdecr_integration/coordinator.py` | 提取共享 source staging；提供确定性 job identity/seed 所需的小型公开方法 |
| `docker-compose.v2-production.yml` | 为 control worker 和 ticker initialization worker 挂载同一 prebuilt root |
| `deploy/docker-compose.hk.yml` | 香港环境固定 `PREBUILT_REQUIRED`，持久卷路径一致 |
| 生产环境模板和 Runbook | 增加本地构建、上传、publish、启动、回滚命令 |

不需要数据库 migration。包引用写入现有 `NodeSpec.inputs`，接管结果写入现有 `NodeResult`/receipt。

## 12. 实施顺序

### Phase A：不可变包与本地构建

1. 定义 manifest/ref/receipt 严格模型。
2. 实现共享 validator、digest、SQLite export。
3. 提取共享 historical source staging。
4. 实现本地 build 和 inspect。

完成标准：本地真实 ticker 能产生一个离线可重复校验的单文件 Registry 包。

### Phase B：远端发布和目录状态机

1. 实现 publish/list/reconcile。
2. 实现 incoming → ready → claimed → consumed 原子迁移。
3. 加入 path/symlink/并发保护和无秘密 receipt。

完成标准：并发 claim 只有一个成功，坏包不能进入 ready。

### Phase C：初始化接管

1. control service 在 submit 前固定包和 cutoff。
2. catalog 把 ref 固化到 CDECR node inputs。
3. adapter 导入 Registry、seed job state、调用现有 Coordinator。
4. 保留原有 CDECR 本机执行分支，按 execution mode 门禁。

完成标准：远端 `execute_cdecr` 未被调用，但 CDECR 节点得到真实 Snapshot/Delta，并可继续 O2。

### Phase D：部署和运行手册

1. Compose 挂载持久目录。
2. 香港环境启用 `PREBUILT_REQUIRED`。
3. 补充上传、publish、初始化、审计、清理和回滚步骤。
4. 用一个真实 ticker 完成全链路验收；不得触发 Live 订单。

## 13. 必要测试

遵循只测必要路径的要求，测试集中在以下 6 项：

1. `REUSE_ACTIVE` 行为完全不变；无 active revision 仍失败。
2. 有效 ready 包被原子 claim，run 使用 manifest cutoff，CDECR NodeSpec 固定 bundle ref。
3. CDECR 节点导入 Registry 后不调用 `execute_cdecr`，并真实生成 Snapshot/Delta。
4. ticker、digest、binding、epoch、message IDs 或 compatibility 任一不匹配时明确失败；`PREBUILT_REQUIRED` 不回退。
5. 在“已 claim”“已复制”“已 seed state”三个中断点重试均幂等，不覆盖不同 digest 的目标。
6. 一次小型集成链路证明 O2 能读取导入后的 epoch/snapshot/delta，并保持现有 D1+CDECR 依赖关系。

无需为纯 getter、枚举映射或 CLI 输出格式增加镜像实现型测试，也不扩大到 V1 或全仓回归。

## 14. 真实验收步骤

1. 本地对目标 ticker 运行 builder，记录峰值内存、bundle_id、cutoff、epoch_id 和 digest。
2. 本地执行 inspect，确认 epoch FINALIZED 和 Registry 完整。
3. 上传远端 incoming，通过容器内 publish 发布到 ready。
4. 远端执行 `list --ticker <TICKER>`，确认只有一个 ready 包。
5. 通过正式 V2 API 选择 `FORCE_INITIALIZE` 启动 ticker。
6. 确认初始化 run 的 cutoff 等于 manifest cutoff，CDECR 节点带固定 bundle ref。
7. 监控进程和内存，确认未启动 CDECR 子进程，4 GB 实例无 OOM/restart。
8. 确认 CDECR 节点输出 `CDECR_PREBUILT_ADOPTED`、epoch、snapshot 和 delta。
9. 确认 O2、D2、D3、O4、Activation、Bus、Runtime 按当前正式链路完成。
10. 确认包进入 consumed，active revision 指向本次完整初始化结果。
11. 对 `MESSAGE_MONITORING` 检查 Policy ACTIVE 投影仍满足零消费证明；不发送任何 Live 订单。

## 15. 回滚

代码回滚不需要迁移数据库：

1. 暂停新的 ticker 初始化。
2. 将 execution mode 改回 `LOCAL_ONLY`，或回滚相关提交。
3. 保留 consumed/claimed 包和已有 run 证据，不删除当前 run Registry。
4. 对尚未创建 run 的 orphan claim 使用 reconcile 释放。
5. 已创建 run 的失败节点按现有恢复/重新初始化规则处理，不手工改 active revision。

在香港 4 GB 实例上，回滚到 `LOCAL_ONLY` 只表示代码能力回退，不代表可以安全执行 CDECR；实际恢复远端 CDECR 前仍需升级内存或降低并发并重新验收。

## 16. 工作量与风险

预计工作量为 1～2 个开发日，主要改动集中在一个新 prebuilt 模块、两个现有接线点和部署配置。前端、API wire schema 和业务数据库不需要变更。

主要风险及控制：

- **版本漂移**：以 compatibility version + configuration fingerprint 强校验，首版拒绝模糊兼容。
- **cutoff 不一致**：在创建 run 前 claim 包，并直接采用 manifest cutoff。
- **重复消费**：ready → claimed 使用同文件系统原子 rename，NodeSpec 固定 identity。
- **半复制/覆盖**：临时文件 + fsync + digest + `os.replace`，不同 digest 冲突即失败。
- **路径污染**：manifest 无绝对路径，远端重新构造全部 canonical paths。
- **隐式 OOM 回退**：香港固定 `PREBUILT_REQUIRED`，坏包和缺包都不运行服务器 CDECR。

该范围已经足够解决“只把 CDECR 放在本地跑”的资源问题，同时保留远端完整初始化、版本激活和运行时生命周期。
