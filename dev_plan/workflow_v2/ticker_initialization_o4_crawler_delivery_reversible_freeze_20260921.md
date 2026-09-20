# Ticker 初始化 O4 `crawler-delivery` 可恢复冻结：实现排查与调整方案

日期：2026-09-21  
范围：正式 ticker initialization V2；不改 O4 prompt/skill，不改 Crawler Plane 能力本身，不执行生产部署。  
审查基线：当前工作树（`main`，HEAD `d7d1c24c`，工作树已有大量未提交改动）。本文最初为只读排查；现已按第 5 节推荐方案完成本地实现，尚未执行生产部署。

实施状态（2026-09-21）：初始化专属开关默认关闭，正式 DAG 已绕过 DELIVER；CONFIGURE 不再派生 delivery request/checkpoint，REGISTER 直接消费冻结 plan 并保留真实 binding 校验；旧 DAG 可无 agent 兼容收口，已完成的真实 DELIVER 仍按原结果处理。O4 prompt/skill 与通用 Crawler Plane 未修改。Ticker Initialization 相关 122 项回归、Ruff、compileall 与 diff check 已通过，尚未执行生产部署。

## 1. 结论

可以冻结 `crawler-delivery`，并让正式 ticker 初始化在 `monitoring-configuration` 完成后继续走到 Activation、Message Bus 和 Runtime ready。

但不能只从 `default_plan()` 删除 `o4.deliver`。当前实现中有两条硬编码链路：

1. `MonitoringO4Orchestrator._configure()` 发现 `NEW_CRAWLER_REQUIRED` 后仍会创建初始化专属的 DELIVER request；只删 DAG 节点会留下永远无人消费的 `PENDING` 请求和 delivery checkpoint。
2. `o4.register` 只读取 `o4.deliver` 的产物，`activation.prepare` 又只从 `o4.register` 取得 `monitoring_configuration` 激活引用；直接删除 DELIVER 会让正式配置无法进入 Activation。

推荐方案不是伪造“DELIVER 成功”，也不是删除交付能力，而是：

> 用一个初始化专属 feature flag 暂停 DELIVER 派生和执行；正式 DAG 改为 `o4.configure -> o4.register -> activation`。REGISTER 保留为确定性控制面校验与激活引用生成步骤，直接消费 CONFIGURE 的冻结 plan 和候选 Message Bus 配置。

这样能同时满足：

- O4 agent 仍在 Monitoring Configuration Plan 中明确写出 `NEW_CRAWLER_REQUIRED`；
- 不调用 Codex SDK agent 搭建、测试或注册新 crawler；
- 已有默认消息源、已注册 source 和可复用 crawler 仍能被 CONFIGURE 配置并投入运行；
- 新 crawler 缺口被明确保留，不冒充已交付；
- Message Bus / Runtime 仍以真实 binding 和 ACK 为准；
- DELIVER 代码、schema、skill、capability 和历史数据全部保留，后续可重新启用。

## 2. 当前实现链路

### 2.1 正式初始化 DAG

`src/doxagent/ticker_initialization/catalog.py` 当前固定为：

```text
d1 + cdecr
    -> o2 -> d2 -> d3
    -> o4.configure
    -> o4.deliver
    -> o4.register
    -> activation.prepare
    -> activation.commit
    -> bus.ready
    -> runtime.ready
```

具体硬依赖为：

- `o4.deliver.dependencies = ["o4.configure"]`
- `o4.register.dependencies = ["o4.deliver"]`
- `activation.prepare.dependencies` 包含 `o4.register`

父编排只承认节点 `SUCCEEDED` / `FAILED`。所有顶层节点都必须 `SUCCEEDED`，整个 initialization 才能完成；所以保留一个永久 `PENDING`、`HELD` 或失败的 DELIVER 节点并不能实现绕过。

### 2.2 CONFIGURE 实际已经做了什么

正式初始化的 O4 runtime 工作在隔离的 candidate Message Bus SQLite 中，不直接启动轮询。candidate 初始化时会：

- 从 live 配置复制 source definitions、default profile 和该 ticker 已有 bindings；
- bootstrap 内置 source；
- materialize 默认 bindings；
- 明确不复制 `ticker_monitoring_states`，因此 candidate 不可轮询。

当前默认 profile 包含 Benzinga、Finnhub、Yahoo Finance、IBKR News 和 Reuters Site Search。即使冻结新 crawler 交付，初始化通常仍有一组可启动的基础发现源。

CONFIGURE agent 仍可在 candidate 中完成以下已有能力工作：

- `KEEP_DEFAULT`
- `CONFIGURE_REGISTERED_SOURCE`
- `ENABLE_EXISTING_CRAWLER`
- 把缺失能力记录为 `NEW_CRAWLER_REQUIRED`
- 把无法合理建立独立 source 的需要记录为 `NO_DEDICATED_SOURCE`

`O4PlanFinalizer` 会逐项校验已存在 source/binding 的实际回读结果，并把不合格项记入 omission，而不是把整个计划一并作废。

### 2.3 DELIVER 当前做了什么

当 plan 中存在 `NEW_CRAWLER_REQUIRED` 时，`_configure()` 会自动创建 DELIVER request 和 delivery checkpoint。随后 `o4.deliver`：

- 让 Codex SDK agent 选择 plan 已批准候选；
- 创建 crawler version；
- 执行、live probe、添加 regression、certify、promote；
- 注册 SourceDefinition 和 ticker binding；
- 逐 Source Need 生成 DeliverySettlement。

现有实现已经允许某些 item 结算为 `REPLAN_REQUIRED` 或 `HUMAN_INTERVENTION_REQUIRED`，且不会因为一个 crawler 未交付就否认其他健康 binding；但在正式 ticker 初始化 DAG 中，DELIVER 容器节点本身仍必须跑完并产生 terminal settlement，因而仍会消耗 agent 能力和时间。

### 2.4 REGISTER、Activation、Bus、Runtime 的真实依赖

`o4.register` 当前不是 crawler 构建节点。它做两件必要工作：

1. 若 plan 需要新 crawler，确认 DELIVER 有 terminal settlement；
2. 从 candidate Message Bus 回读至少一个 enabled、未 tombstone、且 source enabled 的 binding，然后生成：

```json
{
  "monitoring_configuration": {
    "initialization_id": "...",
    "database": ".../message_bus.sqlite3",
    "plan_id": "...",
    "plan_version": 1,
    "enabled_binding_count": 5
  }
}
```

`activation.prepare` 必须拿到这个 `monitoring_configuration` 引用并写入 immutable activation revision。之后：

- Bus consumer 将 candidate portfolio 原子安装到 live Message Bus；
- `CandidateConfiguration.install()` 再次要求至少一个可用 binding；
- Bus 启动 ticker 并真实 ACK revision；
- Runtime 只有在 Bus ACK 后才能 admit activation 并 ACK；
- 初始化最后进入 `VERIFY_READY`。

因此，`o4.deliver` 没有不可替代的下游语义依赖；不可绕过的是“至少一个真实可用 binding + 正式 monitoring_configuration 引用 + Bus/Runtime 真实 ACK”。

## 3. 依赖关系判定

| 依赖/契约 | 当前用途 | 是否可绕过 DELIVER | 调整后的处理 |
|---|---|---:|---|
| Monitoring Configuration Plan | 表达完整监测意图和 Source Need | 是 | 原样保留，继续允许 `NEW_CRAWLER_REQUIRED` |
| DELIVER request/checkpoint/settlement | crawler 实现、测试、认证、注册 | 是 | 冻结时不创建、不执行，不伪造 settlement |
| 已注册 source / 已有 crawler | CONFIGURE 可直接复用 | 不应绕过 | 继续由 CONFIGURE 配置并回读确认 |
| 至少一个 usable binding | Candidate install 和 Bus 启动底线 | 否 | REGISTER 继续硬校验；没有可用 binding 就失败 |
| `monitoring_configuration` activation ref | Activation 和消费者定位配置快照 | 否 | REGISTER 直接从 CONFIGURE plan/candidate 生成 |
| Crawler Plane ACTIVE release | 仅新 crawler source 所需 | 对被冻结 item 可绕过 | 未交付 item 不注册、不绑定、不进入 active revision |
| Message Bus ACK | 证明配置已安装且 ticker 已启动 | 否 | 保留现有真实 ACK 门槛 |
| Runtime/W3 readiness + Runtime ACK | 证明运行面可用 | 否 | 保留现有门槛 |
| O4 REPAIR | 修复已存在、已运行能力 | 与冻结本身无直接依赖 | 保留；未交付 source 不会产生 repair 对象 |
| Pilot `O4_CONFIGURE -> O4_DELIVER` | 独立能力测试 | 是，且不应受正式冻结影响 | Pilot 与手工实验路径继续可用 |

## 4. 影响评估

### 4.1 正向影响

- 每次正式初始化最多只运行一次 O4 agent turn，不再把不可靠的 crawler 开发能力放在启动关键路径上。
- 初始化耗时、token 成本、Crawler Plane mutation 风险和环境依赖显著下降。
- 不再因为 Chromium、站点反爬、HTTP 403、certification 或 agent 实现质量导致 ticker 无法启动。
- 默认源、已注册源和已有 crawler 的配置仍然生效，Bus/Runtime 的真实 readiness 标准不降低。
- plan 仍保留需要建设哪些 crawler 的业务判断，后续可以按 plan_id/version 恢复交付。

### 4.2 必然损失

- `NEW_CRAWLER_REQUIRED` 对应的覆盖缺口不会在本次初始化中转化为运行能力。
- 只依赖这些缺失 source 的 policy 可能长期没有直接 source-of-record 事件，只能依赖默认发现源或其他冗余源。
- 新 crawler 不会有 probe、regression、certification、ACTIVE release、SourceDefinition 或 binding。
- 因为 source 根本没有注册，运行期 O4 REPAIR 不会替这些缺口自动工作。

这些不是实现异常，而是本次冻结的明确业务代价。产品与运维展示必须把它表达为“计划存在、交付冻结”，不能显示为“crawler 已交付”或“完整覆盖”。

### 4.3 对上游的影响

- D1、CDECR、O2、D2、D3 的执行、产物和依赖不变。
- D3 仍提供 PolicySet；CONFIGURE 仍必须覆盖或明确 omission。
- O4 prompt/skill/schema 不需要调整。`monitoring-configuration.md` 继续要求形成具体 `NEW_CRAWLER_REQUIRED` item 是正确的，因为冻结的是执行，不是业务判断。

### 4.4 对下游的影响

- Activation 仍拿到相同形状的 `monitoring_configuration` 引用；可新增非破坏性元数据，但不改消费者必需字段。
- Message Bus 只消费实际安装的 source/binding，不读取 DELIVER settlement；因此没有直接依赖。
- Runtime 只依赖 active revision、Bus ACK 和文档/Policy/Event Library 引用，不依赖 crawler delivery 记录。
- 前端公开进度聚合把 O4、REGISTER、ACTIVATION、BUS_START 都归入 `SOURCES_ACTIVATION`。只要 REGISTER/Activation/Bus 继续存在，六阶段 wire contract 不必变化。
- 成本审计会自然少一个 O4 Codex 请求；这是预期变化。

### 4.5 对历史运行和恢复的影响

初始化 node plan 在 submit 时冻结。仅修改 `default_plan()` 只会影响新提交的初始化，已经存在的 run 仍可能包含 `o4.deliver`。

必须区分：

- 新 run：直接采用无 DELIVER 的新拓扑。
- 已完成 DELIVER 的旧 run：按原结果继续，不回滚已经完成的 crawler 交付。
- 尚未开始 DELIVER 的旧 run：通过兼容分支做“冻结绕过”，不调用 agent，并带明确 `CRAWLER_DELIVERY_FROZEN_NOT_EXECUTED` 注解；不能生成虚假 DeliverySettlement。
- 正在执行 DELIVER 的 run：不能无条件中途切换。其 capability 可能仍在修改 candidate。部署前应确认没有 RUNNING DELIVER；否则显式选择“让它完成”或“取消并丢弃该 candidate 后重建”。不要一边让旧 agent 写 candidate，一边把同一 candidate 激活。
- 已在 DELIVER 失败的旧 run：恢复时走冻结兼容分支，再继续 REGISTER/Activation。

## 5. 推荐调整方案

### 5.1 增加一个初始化专属开关

新增设置，建议命名：

```text
DOXAGENT_TICKER_INITIALIZATION_O4_DELIVERY_ENABLED=false
```

语义必须严格限定为“正式 ticker initialization 是否执行 O4 DELIVER”。它不关闭：

- O4 CONFIGURE；
- 已有 crawler 的运行；
- O4 REPAIR；
- O4 Pilot 或独立人工 DELIVER；
- Crawler Plane 服务本身。

默认值设为 `false`，生产 compose/env 显式写出 `false`，避免部署环境靠隐式默认。

### 5.2 让默认 DAG 按开关生成

关闭时：

```text
d3 -> o4.configure -> o4.register -> activation.prepare
```

开启时恢复原链路：

```text
d3 -> o4.configure -> o4.deliver -> o4.register -> activation.prepare
```

`default_plan()` 接收显式 `o4_delivery_enabled` 参数；ControlService 和 CLI 都从同一个 settings 字段传入，避免 API 启动与 CLI 启动生成不同拓扑。

不建议长期保留一个每次都“成功 NOOP”的 DELIVER 节点。那会继续显示 O4_DELIVER phase，并把“未执行”混同于“成功执行”，不利于审计。

### 5.3 CONFIGURE 冻结时不派生 DELIVER

`MonitoringO4Orchestrator._configure()` 在以下条件同时成立时：

- request 属于 ticker initialization；
- plan 包含 `NEW_CRAWLER_REQUIRED`；
- 初始化 DELIVER 开关关闭；

应直接把 CONFIGURE request 结算为 `SUCCEEDED`，保存 plan，但不创建：

- DELIVER request；
- delivery checkpoint；
- delivery settlement。

plan 中的 `NEW_CRAWLER_REQUIRED` 必须原样保留。不能把它改写成 `KEEP_DEFAULT`、`NO_DEDICATED_SOURCE` 或 `INFEASIBLE`，因为冻结不是能力判断。

建议在 CONFIGURE 的 `NodeResult.quality_annotations` 加一个固定标记：

```text
CRAWLER_DELIVERY_FROZEN
```

该标记是运行策略事实，不需要进入 agent prompt 或业务 schema。

### 5.4 REGISTER 改为可直接消费 CONFIGURE

REGISTER 根据实际依赖选择输入：

- delivery enabled：读取 `o4.deliver`，保留现有 terminal settlement 校验；
- delivery frozen：读取 `o4.configure`，跳过 settlement 校验，但仍执行真实 binding 回读。

无论哪条路径，都必须保留以下硬门槛：

- candidate Message Bus 存在；
- 至少一个 enabled、未 tombstone 且 source enabled 的 ticker binding；
- plan_id/version 可回读；
- candidate 仍不可轮询；
- 不在 REGISTER 内做 crawler probe 或启动 Message Bus。

建议在 `monitoring_configuration` 引用中增加非破坏性元数据：

```json
{
  "crawler_delivery_state": "FROZEN",
  "pending_crawler_need_count": 3
}
```

消费者继续只依赖既有字段；这两个字段用于审计和未来恢复，不进入 public wire contract 也可以。

### 5.5 旧 run 的兼容分支

旧 run 的冻结 plan 仍包含 `o4.deliver`。为避免修改历史 node plan：

- adapter 在 DELIVER 节点开始前先读取开关；
- 关闭时不创建/处理 agent request；
- 若存在未开始的初始化 DELIVER request，将其标记为 `HELD`，错误原因固定为策略冻结；
- DELIVER 顶层节点返回 plan refs 和 `CRAWLER_DELIVERY_FROZEN_NOT_EXECUTED`，让旧 DAG 可以到 REGISTER；
- REGISTER 识别该注解，禁止要求 terminal settlement，但继续做 binding 硬校验。

这里顶层节点仍会以 `SUCCEEDED` 结算，是受当前父状态模型只有 `SUCCEEDED` / `FAILED` 的约束；必须依靠明确 quality annotation 和不存在的 settlement 来避免把它解释为交付成功。

不建议为本次冻结新增第三种父状态 `SKIPPED`。这会扩散到 repository、read projection、前端 wire schema、恢复和 fault matrix，明显超出本需求。

### 5.6 恢复策略

“可恢复冻结”应定义为两层：

1. **向前恢复**：把开关设为 `true` 并重新部署，之后的新初始化恢复原 `CONFIGURE -> DELIVER -> REGISTER` 链路。
2. **历史缺口补交付**：保留 plan、candidate identity、DELIVER schema/skill/capability 和 Crawler Plane 代码；恢复时由显式 operator workflow 按 plan_id/version 执行 DELIVER，再通过新的 monitoring configuration activation 安装结果。

本次不需要同时实现历史 backlog 自动补跑。自动扫描并补跑所有冻结 plan 会带来并发、版本过期、source viability 变化和意外生产 mutation，不符合“不过度设计”。恢复时再做一个有范围、有 operator reason 的补交付入口即可。

## 6. 不应做的调整

- 不改 `prompts/codex_v2/monitoring_o4/skills/monitoring-configuration.md`。
- 不改 `crawler-delivery.md`，也不删除它。
- 不删除 `CodexMonitoringO4Node.DELIVER`、DeliveryCheckpoint、DeliverySettlement 或 DELIVER capability tools。
- 不把 `NEW_CRAWLER_REQUIRED` 自动降级成其他 resolution。
- 不降低 REGISTER 的 usable-binding 门槛。
- 不绕过 Candidate install、Bus ACK、Runtime ACK 或 W3 readiness。
- 不让 frozen delivery 在 UI、日志或 artifact 中显示为真实完成。
- 不让通用 O4 worker 自动消费 initialization-scoped DELIVER request；现有 repository 已按 `initialization_id IS NULL` 隔离通用队列，这个边界应继续保留。

## 7. 预计改动面

### 必改

- `src/doxagent/settings.py`：新增初始化 DELIVER 开关。
- `.env.example` 及生产 compose/env：显式配置冻结值。
- `src/doxagent/ticker_initialization/catalog.py`：按开关生成两种拓扑。
- `src/doxagent/v2_control/service.py`：提交初始化时传入开关。
- `src/doxagent/ticker_initialization/cli.py`：默认 plan 使用相同开关。
- `src/doxagent/workflows/codex_monitoring_o4/orchestrator.py`：冻结时不派生 DELIVER request/checkpoint。
- `src/doxagent/workflows/codex_monitoring_o4/service.py`：把初始化专属开关传给 orchestrator。
- `src/doxagent/ticker_initialization/o4_adapter.py`：REGISTER 直接消费 CONFIGURE；兼容旧 run 的冻结 DELIVER。
- 初始化/O4 定向测试与运维文档。
- `changelog`：实际代码修改完成后追加记录。

### 无需改

- O4 prompts / skills。
- O4 plan 与 delivery schema。
- Crawler Plane 实现与 release store。
- Message Bus polling、normalization、stream、cursor 逻辑。
- Runtime scheduler / W1 / W2 / W3 核心逻辑。
- V2 public API wire schema和前端六阶段结构。

## 8. 验收矩阵

实施后至少验证以下定向用例：

1. **冻结 + 无新 crawler need**：只执行 CONFIGURE；REGISTER 成功；Bus/Runtime 真实 ACK；初始化 `SUCCEEDED`。
2. **冻结 + 有新 crawler need**：plan 保留完整 `NEW_CRAWLER_REQUIRED`；不存在 DELIVER request/checkpoint/settlement；REGISTER 使用默认/已有 bindings；初始化 `SUCCEEDED`。
3. **冻结 + 没有 usable binding**：REGISTER 明确失败，不能为了绕过 DELIVER 而启动空 Message Bus。
4. **冻结 + 已有 registered source / crawler**：CONFIGURE 的实际 binding mutation 被回读并安装；不调用 DELIVER agent。
5. **旧 run 尚未开始 DELIVER**：不产生 Codex worker request；节点带 `CRAWLER_DELIVERY_FROZEN_NOT_EXECUTED`；后续正常激活。
6. **旧 run DELIVER 已完成**：保留真实 settlement 和已交付 source，不回退、不覆盖。
7. **开关重新开启**：原 `CONFIGURE -> DELIVER -> REGISTER` 链路与现有 delivery 测试恢复通过。
8. **隔离性**：O4 Pilot 和非初始化 O4 DELIVER 仍可执行。
9. **进度投影**：公开六阶段仍完整；`SOURCES_ACTIVATION` 最终成功；不出现永久 `O4_DELIVER` pending。
10. **成本/调用断言**：冻结场景 O4 runner 只收到 CONFIGURE，一次初始化没有 DELIVER Codex 请求。
11. **candidate 安全**：Activation 前 live ticker state 不存在；Activation 后才安装 candidate 并启动 polling。
12. **故障矩阵**：`default_plan()` 两种拓扑都满足 dependency validation、crash reconcile 和精确 resume。

建议定向测试集合：

```text
tests/test_ticker_initialization_o4_adapter.py
tests/test_codex_monitoring_o4.py
tests/test_ticker_initialization_fault_matrix.py
tests/test_ticker_initialization_startup_integration.py
tests/test_initialization_resilience.py
tests/v2_backend/test_initialization_projection.py
```

本次审查在未改代码的当前工作树上运行了 O4 adapter、初始化 O4 不启动 Bus、失败 delivery 不阻断独立 O4 Bus 启动、以及 startup integration 的定向基线，共 `10 passed`。这证明了当前链路描述，但不构成冻结方案的实施验收。

## 9. 上线顺序

1. 只读盘点生产 ledger 中 `o4.deliver` 的 `PENDING` / `RUNNING` / `FAILED` / `SUCCEEDED` 数量。
2. 确认没有仍在修改 candidate 的 RUNNING DELIVER；若有，逐项选择完成或取消并重建 candidate。
3. 部署代码，但先保持开关为 `true`，验证服务、schema migration 和当前初始化不受影响。
4. 把初始化专属开关切为 `false`，重启提交初始化的 Control/CLI 入口和 initializer worker。
5. 发起一个非关键 ticker 的正式初始化验收，确认只有 CONFIGURE agent call，plan 保留 crawler needs，Bus/Runtime 真实 ready。
6. 复读 active revision、installed configuration head、ticker bindings、Bus state、Runtime state 和初始化节点账本；不能只看 initialization `SUCCEEDED`。
7. 稳定后再扩大范围。若需回退，把开关恢复 `true`；无需回滚 prompt、schema 或 Crawler Plane 数据。

## 10. 最终建议

批准该冻结，但把它定义为“初始化编排暂停新 crawler 交付”，而不是“监测完整”或“crawler 不再需要”。

最小且正确的落地边界是：

- 一个初始化专属 feature flag；
- 新 DAG 绕过 DELIVER；
- CONFIGURE 不创建孤儿 delivery work；
- REGISTER 直接消费 CONFIGURE 并保留真实 binding 门槛；
- 旧 run 有清晰兼容语义；
- plan、skill、schema、交付代码全部保留。

这条方案不保守退避：ticker 仍会在有真实基础消息源时完成初始化并启动 Runtime；同时也不虚构能力：未交付 crawler 继续以明确 gap 留在冻结 plan 中。
