# Persistent Runtime V2 有界 Wave、超时与 Realtime 解耦修复方案

日期：2026-09-14
状态：已实施（本地，未部署）
优先级：P0/P1

## 1. 目标

本方案只处理以下三项已经确认的 Runtime V2 编排问题：

1. CLOSED Sweep 改为有界 wave：冻结成员仍按既有顺序处理，每 20 条先顺序完成 W1，再处理这一 wave 的 W2，然后进入下一 wave。
2. Runtime V2 模型请求超时固定为 `120s`；每个逻辑 round 首次失败后最多自动重试一次，即单 round 最多两次实际模型请求。
3. 移除 pending Sweep 对开市日 02:01 realtime polling 的 ticker 级全局阻塞。

本方案不重复设计或修改 `message_freshness_and_sweep_repair_plan_20260914.md` 已覆盖的消息时效、准入窗口、历史消息清理、source cursor、enrichment、去重与事故数据处置。实施时必须保留该方案已经落入工作树的改动，不能用本方案覆盖或回退。

## 2. 已冻结的产品与技术口径

### 2.1 Wave 口径

- wave 大小固定为 `20`，不是可随运行时临时变化的启发式参数。
- `sweep_members` 的既有冻结顺序保持不变，不重新排序、不重新抓取、不重新生成 Case。
- 每个 wave 的执行顺序固定为：

```text
wave N: W1(member 1 → member 20)
     → W2/skip-W2(member 1 → member 20)
     → wave N+1
```

- W1 在 wave 内继续严格串行，后一个成员必须能看到前面成功提交的 provisional。
- W1 失败且重试耗尽的成员按既有失败隔离逻辑结算，不阻塞同 wave 其余成员，也不进入 W2。
- `OLD + normal` 保持显式 skip W2；其他已有条件路由保持不变。
- W3 batch、候选选择、O2/O3 maintenance 仍在全部 wave 结算后启动，本次不改变这些阶段的业务边界。

### 2.2 Timeout 与 retry 口径

- `DOXAGENT_PERSISTENT_RUNTIME_V2_TIMEOUT_SECONDS` 默认值改为 `120.0`。
- 每个 W1/W2 逻辑 round 最多两次自动请求：一次初始请求，加一次自动重试。
- `provider_timeout`、可重试 provider 错误以及可重试结构化输出错误都受同一个“两次请求”硬上限约束，不能因错误类型叠加额外自动次数。
- W1 的 R1、R2、R3 是三个独立 round；每个 round 最多两次，因此完整 W1 理论上最多六次自动模型请求。
- 人工明确发起的 node/round rerun 属于新的运维动作，必须保留独立 generation 和审计记录；它不能由普通恢复或 scheduler 自动触发。
- 已存在的 attempt 记录不可删除或重编号。历史上已经有三次 attempt 的 round，升级后不得自动产生第四次请求。

### 2.3 Realtime 口径

- 到达开市日 02:01 的 REALTIME 状态后，只要 binding 到期且没有同 binding 的在途 poll，就允许调度 realtime polling。
- 任意 `PENDING` 或 `RUNNING` Sweep、O2、O3、maintenance 都不能作为停止 realtime polling 的条件。
- 保留现有 binding 级 `_inflight` 互斥、provider 限流、消息幂等和 dispatch ownership；移除的是 ticker 级 Sweep 屏障，不是来源级并发保护。
- final Sweep 与 realtime 重叠时的消息准入、时间窗口和所有权由消息时效修复方案负责，本方案不再创建第二套判断。

## 3. 实现设计

### 3.1 将 CLOSED Sweep 改为可恢复的有界 wave

主要落点：

- `src/doxagent/persistent_runtime_v2/coordinator.py`
- 必要时扩展 `src/doxagent/persistent_runtime_v2/journal.py` 的通用 receipt/checkpoint 用法；不新建另一套任务系统。

`RuntimeCoordinator._sweep()` 保留现有 `sweep_members` 冻结步骤，之后按以下方式执行：

1. 将冻结成员按原顺序切分为长度最多 20 的 wave。
2. 为父 Sweep 持久化最小 checkpoint：
   - `wave_size=20`
   - `wave_index`
   - `wave_phase=W1|W2`
   - `member_index`
3. 在当前 wave 的 W1 阶段顺序调用 `_case(child, phase="W1")`。
4. 每个成员成功或隔离失败后推进 durable checkpoint；进程退出时不得只依赖内存下标。
5. 当前 wave 全部 W1 结算后，将 phase 切换到 W2。
6. 对当前 wave 成员调用现有 `phase="ALL"` 路径，使其执行 W2、skip W2 及既有 Router 结算。
7. 当前 wave 全部完成后，推进到下一个 wave；最后一个 wave 完成后才进入现有 W3 batch、effect dispatch 和 maintenance。

checkpoint 只是恢复游标，真实完成状态仍以 Case、turn、effect 的 durable receipt 为准。恢复时必须重新核对成员实际状态，不能因为 checkpoint 已前移而跳过未提交成功的成员。

#### 旧 Sweep 兼容

升级时可能存在没有 wave checkpoint 的在途 Sweep，例如已经完成部分全量 W1 的事故批次。兼容规则如下：

- 不修改既有 `sweep_members`，首次恢复时按固定 20 条切 wave。
- 已有成功 W1 turn 的成员复用现有结果，不重新调用模型。
- 已有 W1 终态失败的成员视为该 wave 的 W1 已结算，不重新自动扩充重试预算。
- 当前 wave 完成 W1 后立即进入该 wave 的 W2；即使后续 wave 在旧版本下已经提前完成 W1，也仍按 wave 顺序结算。
- 已有成功 W2、skip receipt 或最终路由的成员通过幂等读取直接跳过，不重复产生副作用。

#### 租约与让出

一个 wave 仍可能持续较久，因此父 Sweep 和 Case 租约必须在现有 fencing 机制下续租。每个成员结算后允许 scheduler 观察停止/暂停状态；不在一次不可中断的 Python 循环里连续占用全部 20 条。此次不引入并行 W1，也不通过提高 worker 数规避顺序合同。

### 3.2 将 timeout 固定为 120 秒、自动重试降为一次

主要落点：

- `src/doxagent/settings.py`
- `src/doxagent/persistent_runtime_v2/factory.py`
- `src/doxagent/persistent_runtime_v2/service.py`
- `src/doxagent/persistent_runtime_v2/transport.py` 仅核对透传，不增加 SDK 内部重试

具体修改：

1. `persistent_runtime_v2_timeout_seconds` 默认值由 `60.0` 改为 `120.0`。
2. `persistent_runtime_v2_retry_attempts` 默认值由 `2` 改为 `1`，并将配置允许范围收紧为 `0..1`。
3. `factory.py` 的冻结配置校验改为“恰好一次自动重试”，并只向 service 传递一次 retry delay。
4. `_call_with_retry()` 删除 `budget = 3` 的硬编码，使用冻结到 service 实例的 `max_retry_attempts + 1` 计算单 round 总请求上限。
5. SDK client 继续保持 `max_retries=0`，避免 service 重试和 SDK 隐式重试叠加。
6. 现有第二次 retry delay 配置暂时保留读取兼容，但不再参与 Runtime V2 请求，也不再作为 factory 启动条件；后续可单独做配置清理，本次不扩大改动。
7. 启动日志或运行诊断输出必须能确认生效值为 `timeout=120s`、`automatic_retries=1`，不能只依赖默认值推断生产配置。

`round_resume` 的预算扩展必须只响应人工明确 rerun。普通崩溃恢复、租约过期重新领取、父 Sweep 重启都不得创建新 generation 或增加 attempt 上限。

### 3.3 移除 pending Sweep 对 realtime polling 的阻塞

主要落点：

- `src/doxagent/persistent_runtime_v2/bus_orchestration.py`

目标逻辑为：

```python
if realtime:
    # 保留 next_dispatch_at、binding _inflight 和 provider 限流判断
    ...
```

不得继续查询“同 ticker 是否存在未完成 Sweep”作为 realtime polling 的前置条件。

当前工作树中的消息时效修复已经删除了该屏障。实施本方案时应保留并补齐测试，不重复重写 `bus_orchestration.py`，尤其不得覆盖同文件中已经加入的 `window_start` 和 `admission_context` 传递。

## 4. 状态与可观测性

现有任务和 Case schema 不新增业务枚举。父 Sweep receipt/checkpoint 至少应能查询：

- 总成员数、总 wave 数；
- 当前 `wave_index / wave_phase / member_index`；
- 已完成 wave 数；
- 当前 wave 的 W1 成功、W1 隔离失败、W2 完成、skip W2 数量；
- 最近更新时间。

运行状态页面本次不做大改，但后端数据必须足以区分：

- Sweep 仍在 W1；
- 当前 wave 已进入 W2；
- 后续 wave 尚未开始；
- Case 因 W1 失败被隔离，而不是“仍在无限重试”。

模型 turn 继续逐 attempt 留痕，必须能从数据上证明单 round 自动 attempt 不超过 2。历史 attempt 3 仍保留，不应被清洗成新口径。

## 5. 失败与恢复语义

| 场景 | 预期处理 |
|---|---|
| wave 中某条 W1 两次请求均失败 | 隔离该 Case，记录最终错误，继续下一成员 |
| wave 中某条 W2 两次请求均失败 | 按既有 W2 失败路由/隔离规则结算，继续下一成员 |
| 第 12 条 W1 后进程退出 | 恢复当前 wave，复用前 12 条 receipt，从首个未结算成员继续 |
| wave W1 已完成、W2 尚未开始时退出 | 恢复后直接进入该 wave 的 W2，不重跑 W1 |
| W2 成功但副作用提交前退出 | 使用既有幂等键和 effect receipt 恢复，不重复候选、归档或路由副作用 |
| final Sweep 长时间运行 | 02:01 realtime polling 仍独立运行 |
| realtime 与 Sweep 同时发现同一消息 | 由既有 dispatch ownership 与消息准入方案保证唯一 Case |
| 120 秒 timeout 后发生迟到响应 | fencing 拒绝迟到写；最多只启动一次自动重试 |

## 6. 最小测试集

只执行与本修复直接相关的测试，不扩大为全仓回归或真实模型压力测试。

### 6.1 Wave 单元测试

1. 45 个冻结成员必须形成 `20 + 20 + 5` 三个 wave。
2. 调用顺序必须严格为：前 20 条全部 W1 → 前 20 条 W2/skip → 第二组 W1，不能出现第 21 条 W1 先于第一组 W2。
3. wave 内第 N 条 W1 失败不阻塞 N+1，且失败成员不调用 W2。
4. `OLD + normal` 不调用 W2，但必须持久化 skip reason 并计入 wave 已结算。
5. 在 W1 中途、W1/W2 边界和 W2 中途分别模拟崩溃，恢复后不重复成功 round 或业务副作用。
6. 用已有部分 W1 turn、无 wave checkpoint 的旧 Sweep fixture 验证升级兼容。

### 6.2 Timeout/retry 单元测试

1. factory 创建的 transport 实际收到 `timeout_seconds=120`。
2. 可重试 timeout：第一次失败、第二次成功，总调用数为 2。
3. 连续 timeout：总调用数仍为 2，第二次后形成终态失败。
4. 不可重试错误：只调用一次。
5. W1 R1/R2/R3 各自不超过两次；已有 attempt 3 的历史 round 不产生 attempt 4。
6. 普通恢复不创建 `round_resume` generation；人工 rerun 才允许新 generation。

### 6.3 Realtime 解耦测试

1. ticker 存在 `RUNNING` Sweep、调度状态为 REALTIME、binding 已到期时，仍创建 realtime poll task。
2. 同 binding 已在 `_inflight` 时不重复调度。
3. pending Sweep 不修改 realtime poll checkpoint。
4. Sweep 与 realtime 同时运行时，消息所有权测试仍保持单 Case。

## 7. 部署与在途任务迁移

1. 先合并消息时效方案的在途改动，确认 `coordinator.py`、`bus_orchestration.py` 没有未解决覆盖冲突。
2. 在测试环境构造含 45 个成员的 CLOSED Sweep，完成最小测试集。
3. 部署前只读记录生产当前 commit、有效 Runtime V2 timeout/retry 配置、活动 Sweep ID、成员数、各 round attempt 上限和 W2 已启动数量。
4. 受控停止 scheduler，不清空 Runtime DB、不删除活动 Sweep、不重建 `sweep_members`。
5. 部署新版本并启动；确认旧 Sweep 生成 `wave_size=20` 的恢复 checkpoint，已有 W1 结果被复用。
6. 验证第一个 wave 完成 W1 后已经出现 W2/skip-W2，不等待全部成员 W1。
7. 到 02:01 语义已成立的环境，或通过离线时钟 fixture，确认 pending Sweep 不阻断 realtime；生产验收以实际 poll task/receipt 为准。
8. 连续观察少量真实请求，确认 timeout 生效为 120 秒、单 round 不出现第三次自动 attempt。

本方案不要求清空事故批次。事故成员的准入和清理由消息时效方案单独处理；如果其处置先完成，本方案仍需使用普通 fixture 验证 wave 行为。

## 8. 验收标准

以下条件必须全部满足：

1. 任意 CLOSED Sweep 都按固定 20 条切 wave，最后一组允许不足 20 条。
2. 第一个 wave 的 W2/skip-W2 在第 21 条 W1 之前启动。
3. W1 保持 wave 内顺序执行，失败成员隔离后不阻塞同 wave。
4. 进程在任意 wave 边界退出并恢复，不重复成功模型 round、候选、归档、路由或其他副作用。
5. Runtime V2 transport 的有效请求超时为 `120s`。
6. 每个自动逻辑 round 总请求数不超过 2；不存在由 scheduler 恢复自动生成的额外 retry generation。
7. 存在 pending/running Sweep 时，02:01 后到期 binding 仍能建立 realtime poll task，并推进独立 checkpoint。
8. 消息时效方案中的 `window_start`、`admission_context`、成员级准入和 dispatch ownership 行为不回退。
9. 相关最小测试通过，并在 `changelog` 追加具体代码变更、测试范围、部署 commit 和生产验证结果。

## 9. 回滚

- 代码回滚不能回滚或删除已经产生的 Case、turn、wave receipt 和业务副作用。
- 若 wave 编排出现恢复错误，优先暂停对应 ticker 的 Sweep 领取，保留 realtime polling；不得通过重新启用 ticker 级 `pending_sweep` 屏障止血。
- 若 120 秒导致 provider 并发槽长期占用，可临时降低 Runtime worker 并发，不将 timeout 回退到已证实过紧的 60 秒。
- 若必须回退 wave 代码，已有 wave checkpoint 作为未知扩展字段保留；旧版本仍以现有 Case/turn 幂等状态恢复。回退前必须确认不会把已经 W2 完成的成员重新执行。

## 10. 明确不做

- 不修改 W1/W2 prompt、模型、reasoning effort 或业务判断职责。
- 不并行运行 CLOSED Sweep 的 W1。
- 不在本方案中处理 Finnhub 30 天历史窗口、消息发布日期或事故数据清理。
- 不修改 W3 batch、O2/O3 maintenance、Candidate selection 或交易策略。
- 不以增加全局 worker 数、无限加 timeout 或关闭重试来掩盖编排问题。
- 不覆盖其他对话已经产生的未提交改动。

## 11. 本地实施结果（2026-09-14）

- CLOSED Sweep 已按固定 20 条切分可恢复 wave；父 Sweep receipt 持久化 wave、phase、成员进度和当前 wave 结算统计。
- 已有部分进度的 Sweep 复用 Case/turn/任务终态；W1 失败成员隔离后不进入 W2，其余成员继续。
- Runtime V2 默认模型超时已改为 120 秒；单 round 自动重试改为一次，service 不再使用三次请求硬编码。
- transport 继续关闭 SDK 内部重试；factory 启动日志输出有效 timeout 和自动重试次数。
- 已保留工作树中移除 pending Sweep realtime 屏障的改动，并补充对应回归测试。
- 7 项本方案定向测试通过；相关源文件 Ruff 与 mypy 通过。未调用真实模型、未处理事故数据、未同步或重启远端。
