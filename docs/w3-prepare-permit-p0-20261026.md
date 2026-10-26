# W3 PREPARE 大面积超时：根因、修复与生产验收

日期：2026-10-26。用户授权排查、修复并正式发布；失败 Case 仅在可简单恢复时重跑。

## 结论与影响

本次故障发生在模型任务提交之前。Worker 冷启动许可被永久占用，使后续 W3 准备操作一直排队，最终报 `trade_decision/PREPARE: ready-slot preparation failed: TimeoutError`。这解释了界面 Case 失败、却看不到 W3 模型启动或输出的现象。

首次快照的近三天 Case 中，160 条为 `w3_ready_prepare_failed`，另有 67 条 `provider_request_failed`；后者不合并归因于本故障。本次修复不改变模型、提示词、交易判断或券商行为。

## 生产证据与因果边界

修复前 Worker 发布版本为 `3d0ae94cd85edd06df698345b6fe9eb1ee4baa7a`。运行中的 `startup_admission.py` 与修复前 main 的代码哈希一致，排除“修复代码没有发布”的混淆。

- `/v1/resources` 显示 `cold_active=true`，但 `cold_waiters=0`、运行与排队 job 均为 0。
- 没有 capsule 子进程；Luna/Sol 预备池均为 0，整体 `ready=false`。
- 近期失败准备操作没有 child thread，`startup_attempts=0`，未 dispatch Fork、没有 SDK 启动回执；它们在等待启动许可时耗尽准备预算。
- 当时内存保护为 NORMAL、启动压力开关为 false，不能用内存熔断或大量模型并发解释空闲 Worker 上仍被占住的许可。

原许可没有记录持有任务，无法从存量日志认定最初是哪一个任务、哪一次取消触发泄漏。下面的取消竞争可稳定复现，并与上述“任务和进程已消失，许可仍占用”的生产状态吻合；不把未观测的原始取消栈当作已抓获证据。

## 为什么前轮修复仍会出问题

前轮将昂贵冷启动统一收敛到一个 Worker 内共享许可，并保留优先级、准备预算、恢复身份及并发限制。这解决了重复启动与资源竞争，但许可释放本身存在取消安全缺口：

```python
finally:
    async with self.condition:  # 可能在这里等待，也可能在这里被取消
        self.active = False
        self.condition.notify_all()
```

持有任务退出时若通知锁正被其他协程占用，它会在清除 `active` 之前挂起。此时取消该任务，许可永远无法归还。之后每一个 Case 都只能等到自己的准备超时；增加 Fork 超时或重新入队不能解除这个进程内死占用。

`PriorityGate` 也被生命周期 helper 复用，因此在共同实现中修复，而不只针对某个 W3 调用点打补丁。

## 已实现的修复

1. 在任何可取消的 `await` 之前，同步清除许可占用和 owner；之后才等待通知锁并通知排队任务。即使通知被取消，现有等待者也会在最多约 250ms 的既有检查周期内重新检查许可。
2. 用当前任务与本次 `admitted` 标记限定释放者。取消一个排队任务不会释放仍在正常工作的持有者；不通过强制过期、抢占活任务或放开冷启动并发来绕过问题。
3. 资源接口新增 `cold_owner`，包含持有任务存活状态、业务标识和持有时长，便于直接区分正常启动与许可泄漏。

修改文件为 `startup_admission.py`、`jobs.py` 和相应测试。保留原 Case/slot/operation/thread 身份规则、冷启动并发 1、优先级及原准备期限。

## 验证与正式发布

`eval/w3_prepare_p0_20261026/reproduce_permit.py` 在通知锁被占用时取消退出任务：

| 版本 | 原 owner 已结束 | active | 后续申请被卡住 |
| --- | --- | --- | --- |
| 修复前 | true | true | true |
| 修复后 | true | false | false |

相关回归 **50 passed**，覆盖正常退出取消竞争、任务体取消后再次取消、等待者取消、启动准入、writer 交接、ready slots、边界计时及 Worker 资源预算。Ruff、编译与差异检查通过（无关前端文件换行警告未修改）。

正式代码提交：`d5f43fb27286bf54d4d0b51d072924e72aef2504`，已 push 到 main。

通过标准 `storage_release` 构建并只替换 `codex-worker`，替换前确认 job 队列为空，没有中断运行中的模型任务。其他服务容器 ID 均未变化。

- 新容器启动：`2026-10-26T10:40:37.280186381Z`，北京时间 18:40:37。
- 容器：`375a41088e926f61a98b2b4e9eb7c48d0ab381dbd842fb6ebfe55933e0799e6e`。
- 镜像：`sha256:a110aab1fed9e94d123d0c7ad9bf4805001b1327a49cf0d272dd28876323654d`。
- 正式发布回执：`/var/lib/doxagent/releases/w3-permit-p0-d5f43fb27286/receipt.json`。
- 发布后代码哈希：`startup_admission.py=6faf4f88a3687ec141513c7251d0afe31d270a0bd3eb480401e67f3437b958f7`，`jobs.py=413defac96a3b176ad7c5e6d2120f7c93773d6eda5e28ab08869f7832600cd37`。

发布后预备线程已真实完成创建，Luna=2、Sol=1，`/v1/w3/readiness` 为 `ready=true`；冷启动许可为空闲，owner 为空。三个预备 child thread 分别为：

- `01a177cc-c0ee-71b0-a0a5-e34917475f3e`
- `01a177cc-d33e-77c1-aa88-cd04e36226fb`
- `01a177cd-6e70-7e00-ae03-85a4c690de87`

这些记录证明原来被阻塞的物理启动/Fork 链路已恢复。预备线程创建不等于业务模型 turn 完成；自然流量验收结果见下方补充记录，不以容器健康或空窗口零错误替代端到端验收。

### 自然流量端到端验收

截至 `2026-10-26T10:46:51.138199+00:00`（北京时间 18:46:51）的只读快照，发布后已有两条自然 Case；其中第一条已完成 W3 交易阶段，另一条仍在上游运行。新 Case 中未见 `w3_ready_prepare_failed`。该观察量用于确认故障链路恢复，不代表长期失败率或 p95 已验收。

| 验收项 | 实际记录 |
| --- | --- |
| Case | `case_c94fb7a21777434b9857ad83c8e2ff7b`，ONDS |
| Case 创建 | 10:44:24.787091 UTC |
| 交易 stage 首次接受 | 10:44:33.962017 UTC |
| Worker 交易 job 创建 | 10:44:35.807479 UTC；相隔 **1.845462 秒** |
| job / turn | `c813fe06164e3a68f4a728975b81c22fd37bc2dafbfaadb0cb1dd8e5acb02fc1` / `01a177cf-e5d1-7982-aca5-06862679c3dc` |
| 线程取得方式 | `POOL_HIT`，绑定上面的 `01a177cc-c0ee-71b0-a0a5-e34917475f3e` |
| 结果与落库 | job `succeeded`，交易 stage `COMMITTED`，10:46:28.163265 UTC 提交 `NO_TRADE`；Case `COMPLETED`、technical `OK` |
| 后续流程 | 同一 thread 上已启动 `post_decision_operations`；该快照时仍在执行，不算成后处理已完成 |
| 持续就绪 | `/v1/w3/readiness` 仍为 true，Luna=2、Sol=1；Research MCP 调用累计 11 |

没有人工构造该 Case。实际模型 turn 与非空业务决策证明 W3 已恢复启动和产出，而不只是 Fork / 容器恢复。

## 失败 Case 的简易重跑评估

**本轮跳过历史失败 Case 的重跑。** 已检查现有 `resume-node` / `resume_effect`，该入口只能把原 Effect 重新入队，不重置已失败的 ready slot。当前 `W3ReadySlots.claim` 会找到原 Case/record 对应的 slot；若 slot 有错误且原 operation 已 FAILED，就直接再次返回 `w3_ready_prepare_failed`。

最新五条失败样本的 slot 均已 RETIRED、没有 child thread、operation 为 FAILED 且物理启动次数为 0，原准备截止时间也已耗尽。例如 `case_c0841843d34146f2879ca06f37fb98c9` 对应 slot `27ceb17dd3734e0e927b14ea3c83a63f`。因此这不是调用一次现成恢复入口就能接回的孤儿线程；需要额外定义显式重试代次、重新绑定与截止时间语义。遵循用户“复杂就算了”的要求，不手工跨表重置、不删除失败历史、不延长业务有效期。

## 证据位置

`eval/w3_prepare_p0_20261026/`：`before.json`、`runtime_before.json`、`after_first.json`、`runtime_after_third.json`、`runtime_final.json`、`acceptance_final.json`、`release_receipt.json`、复现/只读检查/正式发布脚本与 `tests.xml`。只读采集器兼容 job 完成后 request 被退役为空；首次读取该空字段的采集失败只影响本地证据脚本，修正后重新读取成功，未改生产业务。较大的 Worker 日志仅本地留存；没有因观察而人工派发消息、历史回放或调用券商。
