# 多 ticker 持久化运行耗时异常排查（2026-10-14）

## 结论

当前 20–40 分钟的端到端耗时不能仅归因于跨境网络。已确认生产实时 W1/W2 共享只有 2 个线程的执行池，与每 ticker 4 Case 的准入并发不匹配，产生跨 ticker 队首排队；历史未处理 provisional 候选又造成部分 W1 首轮输入约 30 万 token，超时进一步占用这两个槽位。

本轮进行了只读生产排查和两次合计 177 token 的独立网络探针模型调用；未修改生产配置、业务记录或编排代码，未重新执行历史 Case。

## 恢复流量包后的网络实测

北京时间 2026-10-14 21:15:39 起，在实际 v2-scheduler 容器内，使用当前生产阿里云 endpoint、认证和模型。未使用 HTTP/HTTPS/ALL_PROXY。

Endpoint 为 https://dashscope.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1，模型 qwen3.8-flash，reasoning effort=medium，SDK retries=0。真实探针只要求返回 OK，store=false。

| 测量 | 第一次 | 第二次 | 第三次 |
| --- | ---: | ---: | ---: |
| DNS | 2.9 ms | 6.6 ms | 4.5 ms |
| TCP 建连累计 | 96.6 ms | 93.7 ms | 91.6 ms |
| TLS 完成累计 | 316.7 ms | 310.7 ms | 192.7 ms |
| 首字节累计 | 412.1 ms | 403.6 ms | 280.6 ms |
| 请求总耗时 | 412.2 ms | 403.7 ms | 280.7 ms |

无认证 HTTP 探针稳定返回预期 401；它验证网络往返，不验证推理。随后两次真实 Responses 推理成功返回 OK，分别耗时 10.007 秒、1.608 秒。调用包含服务端调度/推理，不能全部视为网络延迟。

这证明恢复流量包后的当前链路可以正常使用，未表现出分钟级连接/首字节延迟。没有挂载恢复前的同口径网络测量，也没有腾讯云控制面/计费回执，因此不能确认改善全部来自流量包，或声称已验证云端流量包计费归属。小探针也不代表 1 MB 业务请求的上传与长上下文推理成本。

证据：eval/runtime_latency_20261014/network_after_package_restore.jsonl。

## 真实慢 Case 时间线

从生产读取最近 450 个 Case 及每轮真实 started_at、finished_at、latency_ms、错误和 token 回执。以下均为 REALTIME，不是 CLOSED sweep 的既定串行波次。

“模型阶段”是首次模型调用开始至最后一次已记录调用结束的跨度，包含阶段内重试/间隔；不是各轮时间简单相加。端到端数值以下表这两个分段之和近似，不包含末尾少量持久化耗时。

| ticker / Case | 准入至首次模型调用 | 模型阶段跨度 | 两段合计 |
| --- | ---: | ---: | ---: |
| INTC / case_47e41665221f49b59cf3ecc521257cab | 30.79 min | 2.91 min | 33.70 min |
| MU / case_2f5c1f43b6c14fb29e5b6f22e58ae030 | 18.88 min | 4.16 min | 23.04 min |
| RKLB / case_2df85570474a4ca698a564a002256554 | 19.42 min | 5.54 min | 24.95 min |

INTC 示例：北京时间 20:39:33 准入，21:10:20 首次请求，21:13:15 最后一轮调用结束，21:13:17 Case 完成。模型请求前已等待近 31 分钟。“准入至首次调用”包含准入排队、上下文冻结准备和模型池等待，当前落盘字段不能把每一段精确拆开，不能把整段都算成网络或模型推理。

UI runtime.tsx 展示的 duration_seconds 来自 completed_at-created_at（v2_read/projectors.py），这是端到端耗时，并非纯模型处理耗时；hot_path_latency_ms 又从 _resume_hot 开始计时，包含模型池排队。数值口径确有不同，未发现仅凭 UI 格式即可解释 30 分钟的显示错误。

## 问题一：全局两线程执行池形成跨 ticker 瓶颈（优先修复）

生产实际 service.py 确认：

    self._hot_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prv2-hot")

同一个服务承载多个 ticker；REALTIME 每个 Case 把 W1、W2 各提交一个 Future 到这个共享池。W1/W2 的整个热路径、R1/R2 请求和超时重试期间都占着槽位。coordinator 的 Case 池为 64，而 journal.claim 的 CASE 限额是每 ticker 4，无法改变内部全局两槽位限制。

对 20:00 UTC+8 之后的真实 R1/R2 调用区间做重叠统计（排除独立的 R3），观测并发峰值确为 2，与源码一致。快照中 BE/MU/INTC 各已有 4 个 RUNNING Case，但内部 LLM 池全局仍只有 2 个槽位，所以 RUNNING 不代表请求已发出；一个 ticker 的长请求也会拖延其他 ticker。

建议实现边界：保留每 ticker 4 Case 的准入约束，移除全 ticker 两线程隐式总闸门。可按 ticker 懒创建 8 worker 的 W1/W2 池（4 Case × 2 lane），并在服务 close 时统一回收；REALTIME W1/W2 用所属 ticker 的池，CLOSED sweep 原波次与串行边界保持。不要只把全局 2 改成另一个小常量。

必要验收：两个 ticker 各 4 Case 时能够实际同时发出各自 8 个 lane，慢 ticker 不阻塞另一个；同 ticker 超过 4 Case 仍由原准入排队；失败/关闭无悬挂 Future。发布后比较首次调用前等待与真实在途请求，而非只看 RUNNING 数量。

## 问题二：历史 provisional 回退造成长上下文（需修复业务边界）

| ticker | provisional 条数 | 发给 W1 的该字段字节数 | W1 R1 input tokens | cached tokens |
| --- | ---: | ---: | ---: | ---: |
| INTC | 2,281 | 1,004,358 | 294,368 | 16,128 |
| MU | 2,609 | 1,143,124 | 332,815 | 1,024 |

INTC 最早候选日期为 2026-09-22，当日候选仅 97 条；MU 最早为 2026-09-11，当日仅 72 条。该字段名称虽然是 today_provisional_facts，实际不是只有当天数据。

service._visible_provisional 仅在 visibility day 等于 Case trading_date 时启用 current_only；否则 SQLite repository.visible_provisional 返回当天以及所有更早且 daily_status != PROCESSED 的候选。核验时两 ticker 的 visibility day 均为 2026-10-13、Case day 为 2026-10-14，因此触发全历史未处理回退。

这是确定的长上下文来源，但尚不能断言所有旧候选都应删除：未处理状态可能代表维护失败、尚未消费或旧遗留记录；日维护最近也同时存在 FAILED 与 SUCCEEDED 回执。不能用随意截断前 N 条、删除旧记录或强制标 PROCESSED 来修复。

建议实现边界：以已发布 Event Library / 已提交 visibility watermark 为依据，区分已被发布版本覆盖的历史候选、需要短期跨日保留的候选和确实尚未吸收的维护缺口。修正热路径可见集合或增加可追溯的紧凑召回，R2 仍按稳定身份读取完整事实；对维护消费边界单独验收，避免修性能时丢失新旧事件判断证据。这部分应先确认具体候选的消费回执，再确定淘汰规则。

## 放大因素与观测改进

在最近 450 Case 中，选取北京时间 19:00 后准入且已有调用记录的 56 Case，记录到 33 次 provider_timeout，累计占用约 4,112.9 秒请求时间，另有 2 次 structured_output_validation_failed。超时并非 56 个 Case 均失败；这里统计的是调用尝试数。同一轮的 120 秒超时及 5 秒退避会继续挤占全局两槽位。

没有事发前的连接/上传/首字节分段数据，不能把所有历史 provider_timeout 都归因于缺流量包。长输入、输出推理及服务端排队也可能触发现有 timeout；当前应优先解决确定的执行池瓶颈和输入膨胀，再判断是否需要调整 120 秒请求预算，而不是先统一拉长超时。

建议增加准入排队、模型池等待、请求在途、重试和 W3 阶段的分段观测，并在详情中说明总耗时口径。当前 UI 总耗时保留，不把其中等待直接隐藏。

## 证据与范围

本地证据目录：eval/runtime_latency_20261014/。包括 cases_snapshot.txt、summary_stats.json、queue_probe.txt、input_sizes.txt、provisional_sizes.jsonl、provisional_dates.txt、maintenance_receipts.txt 与网络实测 JSONL。候选内容和模型密钥未输出到报告。

本轮没有改生产，不将尚未实施的并发/可见集合修复描述为已经生效。腾讯云套餐实际挂载归属需云控制面或计费侧证据；当前结论限于恢复后的远端实际链路表现。
