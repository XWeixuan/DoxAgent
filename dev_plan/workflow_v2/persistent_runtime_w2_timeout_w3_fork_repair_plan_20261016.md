# 持久化运行 W2 超时与 W3 未启动：排查结论及修复实施方案

日期：2026-10-16。统计快照截止北京时间 17:16:56，覆盖此前 48 小时；独立计时实验于当天 17:28 后执行。文中时间统一为北京时间，原始证据使用 UTC。

本轮仅排查并拟定方案，未改业务代码、提示词、生产配置或业务记录，未重启容器，未重放生产 Case。独立实验使用当前生产 provider，对两条本轮问题 Case 的冻结 W2 R1 输入进行计时回放，store=false，不产生生产 Case 或交易。证据目录：`eval/runtime_failure_audit_20261016/`。

## 1. 结论与优先级

两个问题对应不同故障链，不能用“阿里云网络慢”统一解释。

1. **W2：medium 思考模式下，R1 的内部推理长度高度波动，叠加非流式请求的 120 秒读取超时，造成大量最终失败。** R2 也有同类长尾，但数量远少于 R1。模型在持续生成期间，现有非流式客户端看不到进度。其最终 JSON 很短，并不意味着推理成本很低。当前同一链路的小请求正常；回放已直接观测到首个事件很快返回、后续持续推理接近两分钟。
2. **W3：fork 已在 Worker 被接受，但调度器只等 60 秒，没有按持久化 operation 查询恢复；三次 HTTP 超时后把 Effect/Case 判失败，而 Worker 仍继续创建 child thread。** 38 个问题 Case 全部有晚到且已完成的 fork 记录，全部没有提交 W3 模型任务。这些 Case 确实没有进入 W3 研判，不只是页面显示问题。
3. **失败展示存在第二层错误：Case FAILED，却丢失失败原因，读模型又仅凭“尚无 W3 最终结果”推导 PENDING。** 因而真正的准备阶段失败被展示成等待。

优先修复 W3 的 operation 恢复和状态投影；随后修复 W2 的传输观测与分轮调用策略。保持 W1、W2 R2/R3 的业务判定和 W3 交易授权边界，不引入召回分片、多模型投票、向量库或新的任务调度系统。

## 2. 当前版本与数据口径

本地、远端源码均为 `e929134d70995933d16e23df6e66c6195d7ac3b8`。scheduler/API/Web 使用 main-e929134d；Worker/projector 仍使用 main-618fd9c5。核对 scheduler 内 w3.py、factory.py、codex_runtime/client.py 的文件哈希，与本地提交一致。本文不是按旧实现猜测生产状态。

当前实际参数：

| 参数 | 实际值 |
| --- | --- |
| W1/W2 provider/model | 百炼 / qwen3.8-flash |
| endpoint | https://dashscope.aliyuncs.com/compatible-mode/v1 |
| reasoning effort | medium，W1/W2 共用 |
| OpenAI SDK timeout | 120 秒 |
| SDK 自动重试 | 0 |
| 编排自动重试 | 1 次，间隔 5 秒，即通常最多两个 attempt |
| Session cache 显式开关 | false；仍可能有服务端隐式缓存命中 |
| W3 模型执行预算 | 600 秒，与 fork HTTP 的 60 秒读取预算不同 |
| 内部 Worker HTTP | http://codex-worker:8791；connect/write/pool=30 秒，read=60 秒 |

48 小时共 853 个 Case：688 COMPLETED、156 FAILED、5 UNAVAILABLE、2 PENDING_W3、2 RUNNING。最终 Case 错误中 provider_timeout 105 个，全部来自 W2：R1 102 个、R2 3 个。另有 38 个 W1/W2 已成功、路由 W3 的失败 Case，主 Case error_code/error_message 均为空。

还观察到 7 个 `database is locked` Case，以及少量其他 provider/输出契约错误；它们不属于本次两个主故障链，不混入 W2 timeout 或 W3 fork 的归因。

### 最近 24 小时的同口径比较

以下“超时”是 attempt 数，不是失败 Case 数；耗时分位数只统计成功 attempt，超时请求另列，因此成功分布受到 120 秒截尾影响。

| 节点 | attempts | 成功 | timeout attempts | 成功 P50/P90/P95，秒 | 推理 tokens 中位数 |
| --- | ---: | ---: | ---: | --- | ---: |
| W1 R1 | 431 | 428 | 2 | 24.8 / 42.7 / 49.1 | 1,385 |
| W1 R2 | 431 | 422 | 1 | 23.9 / 55.6 / 63.7 | 1,190.5 |
| W2 R1 | 556 | 369 | 178 | 54.6 / 103.4 / 110.6 | 3,170 |
| W2 R2 | 109 | 102 | 5 | 33.5 / 66.5 / 92.7 | 1,824 |

W1 确实有少量超时 attempt，但该窗口没有 W1 最终因 provider_timeout 失败的 Case；用户看到的节点差异成立。

## 3. W2 根因证据

### 3.1 120 秒是什么限制

`transport.py` 把 timeout=120 传给 OpenAI SDK，max_retries=0；使用非流式 `responses.create`。这是 HTTP 各阶段的超时配置，尤其是读取等待，不是严格的整个 attempt 墙钟上限。因此多数失败约 120.4 秒，但少量可能更长；不能把 120 秒写成严格总执行期限。

`_safe_transport_error` 将 timeout 统一映射成 provider_timeout，并丢失具体 connect/read/write/pool 阶段。编排再重试一次，仍失败后 `_fail_case` 将 Case 终止；不会把 W2 timeout 当作 NO HIT 放行。该业务边界应保留。

示例 `case_890462aff8b04ab8b501f1b59372e41f` / INTC：W1 R1/R2 分别成功耗时 58.1/27.8 秒；W2 R1 两次分别 120.374/120.370 秒，最终失败。不是 W1/W2 共用两线程池造成的等待：当前按 ticker 12 worker 运行，近期日志中的 lane queue_wait_ms 通常只有 0–几十毫秒。

### 3.2 真正波动的是内部推理量

最近 24 小时成功请求中：

- W2 R1 latency 与 reasoning_tokens 的 Pearson 相关系数为 **0.9251**；与 input_tokens 仅为 -0.1130。内部推理占该节点 output_tokens 的约 **96.95%**。
- W2 R2 latency 与 reasoning_tokens 相关系数为 **0.9516**。
- W2 R1 48 小时成功请求的 reasoning_tokens 从 143 到 10,855；P95 8,096。最终返回虽只有最多 3 个 ID 和 reason，后台推理并不短。

实际 R1 并非比较几条候选：INTC 示例每次比较 173 条 Policy，projection 约 134.7 KB，其中 match_scope 约 110.3 KB；MU 示例 264 条、193.0 KB，其中 match_scope 约 156.5 KB。当前已只发送 policy_id/title/match_scope，并未错误注入完整 Conditions。R2 则仅注入召回的少数完整 Policy，已经是独立显式上下文，不会错误继承整个 R1 projection。

因此不应再通过删除本来必需的 match_scope、任意截断 Policy，或把一段简短的输出 reason 当作全部模型工作来优化。

### 3.3 原输入回放与当前网络

当天远端 scheduler 容器内，无认证 HTTP 探针分别 0.322/0.306 秒返回预期 401；两次最小真实模型请求为 4.680/2.298 秒。它们只证明当前链路正常，不证明历史所有超时都没有网络因素。

使用原冻结 prompt、projection、消息和输出 Schema，仅改为流式计时，结果如下：

| Case / 消息 | effort | 总耗时 | 首个事件 | reasoning tokens |
| --- | --- | ---: | ---: | ---: |
| MU，Seismic Valley 博物馆公告，`case_ac4bde226050421e97d00b87f5b1e82c` | medium | 11.861 s | 2.950 s | 410 |
| 同一 MU 输入 | low | 10.199 s | 6.541 s | 296 |
| 同一 MU 输入 | none | 6.525 s | 6.441 s | 0 |
| INTC，Intel Foundry/KeyBanc 多主题报告，`case_aa0ee8b7874d40aa82b02959c495bf49` | medium | 118.315 s | 1.251 s | 5,632 |
| 同一 INTC 输入 | low | 167.331 s | 3.676 s | 10,071 |
| 同一 INTC 输入 | none | 7.378 s | 2.132 s | 0 |

INTC medium 回放收到 1,764 次 reasoning delta：连接很快建立，后续长期在生成。low 实验甚至用了 167 秒，说明“改成 low 就会稳定变快”并不成立。none 两例明显更快，支持将**纯召回 R1 的非思考模式作为优先评估方案**，不代表已完成召回质量验收。

多主题 INTC 在 medium/low/none 下的第三个候选不同，原生产重试结果也不同；不能宣称不同档位业务等价，更不能用两条计时样本替代质量回归。MU 空召回在三种档位一致。

原 MU Case 的生产 R1 曾先 120.467 秒超时、后 11.760 秒成功；同一冻结业务输入并不固定耗时。重试附加的错误纠正文本与随机生成也可能改变结果。对于没有响应体的历史超时，本轮无法恢复它当时的真实推理长度或精确网络阶段，应保留这一证据边界。

百炼官方文档确认 qwen3.8-flash 支持 none/low/medium/xhigh；max_output_tokens 对 Qwen3.8 同时覆盖推理和最终答案。因此不能直接用很小的总输出上限来截断思考，否则可能只得到 incomplete/残缺 JSON。[官方 Responses 文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)

## 4. W3 根因证据

### 4.1 已路由，失败发生在模型任务提交前

38 个 Case 全部满足：

- 主 Case route=W3，W1/W2 最终结果已成功保存。
- route_to_w3 Effect 为 FAILED、attempt_count=3，last_error=`ReadTimeout: `。
- trade_decision Stage 为 FAILED，thread_id/worker_job_id 均为空。
- Runtime 的 CASE thread 记录已创建，但 thread_id 为空，owner_case_id 后来被释放。
- Worker 的 `w3-fork:<record_id>` operation 全部 COMPLETED，实际 child thread 均在 Case 已失败后创建。
- 这些 child thread 对应的 Worker 模型 jobs 数全部为 **0**。

不是 W2 新 Schema 校验拦住了 W3，也不是 W3 模型超时。Worker 持久化记录已证明 fork 最终执行成功，而调度器没有取回结果。

| Case | W3 接受时间 | Case 判失败 | child 真正创建 |
| --- | --- | --- | --- |
| `case_c0e3bbe8d9d8443fa07961e86455a54d` / INTC，TSMC 扩产消息 | 10/16 04:42:59 | 04:49:32 | 05:26:13 |
| `case_aa0ee8b7874d40aa82b02959c495bf49` / INTC，Foundry 报告 | 10/16 05:55:16 | 05:59:47 | 11:15:24 |
| `case_ac4bde226050421e97d00b87f5b1e82c` / MU，博物馆公告 | 10/16 06:08:52 | 06:12:54 | 11:15:40 |

创建时间取自 Codex state_5.sqlite 的 threads.created_at_ms，不仅依赖 UUID 时间推测。

### 4.2 不完整的 fork 协议与无界串行等待

调用链为 `W3Execution.trade → _thread → agent.fork_thread → HttpCodexWorkerClient.fork_thread → POST /v1/threads/fork`。

当前客户端把 fork 当作一次普通 HTTP POST，60 秒读不到结果即直接抛 ReadTimeout。与 `HttpCodexWorkerClient.run` 已有的“同一 durable job 持续查询恢复”不同，fork 没有对应的 operation 查询/等待协议。

Worker 已有 operation_key/request/thread_id/status 持久化表以及同 key 的锁；但是 HTTP handler 同步等待 list/fork/close 全部结束才返回。`AppServerThreads.request` 还用一个全局 asyncio.Lock 串行执行 list、fork、read/status、delete；在锁内调用 SDK request，无 RPC deadline。安装 SDK 底层等待 JSON-RPC 响应也没有有限超时。一个生命周期调用长期等待，就能拖住后续 fork 和清理请求。调用方 HTTP 超时并不会自动结束服务器端任务。

生产访问日志也显示生命周期接口出现长时间空窗，而 jobs 查询仍正常；10/16 11:16 后大量历史 status/delete 成批返回。说明问题集中于 thread lifecycle 平面，而非整个 Worker 或 provider 全面不可用。

**已证实的是无界串行生命周期等待、晚到 fork 和缺少调度端恢复形成的故障链。** 现有日志没有记录每个 RPC 的开始、锁等待与结束，无法进一步指认首次阻塞究竟在 list/fork/read/进程关闭的哪一步。不能把推测的单一底层 RPC 或清理任务写成已证实原因。修复必须消除这类阻塞的全局放大和歧义失败，而不只延长 read=60。

### 4.3 状态为什么仍显示 PENDING

`_record_w3_failure` 更新了 runtime_v2_w3_cases，但没有把错误写回主 Case；`_refresh_case_completion` 把主 Case 改成 FAILED 时，沿用原本为空的 error_code/error_message。`record_failure` 又用 str(ReadTimeout) 写 Stage，得到空字符串。

`DomainProjectors.case` 对初始路由 W3、未有最终结果的 Case 直接设置 w3_status=PENDING，忽略了 W3 route/Stage/Effect 的 FAILED；API detail 继续使用这个 summary。因此数据库已有 FAILED 的 W3，页面仍显示 PENDING，且主 Case 没有可见错误原因。

## 5. 修复实施方案

### P0-A：复用现有 fork operation，完成接受、查询与恢复

修改 `codex_runtime/client.py`、`codex_worker/app.py`、`codex_worker/thread_lifecycle.py`、`persistent_runtime_v2/w3_execution.py`、`w3_threads.py`。

1. 保留 `w3_thread_operations`，不再新建一套任务引擎。POST fork 先按原 idempotency_key 登记原始参数，立即返回 operation 状态；已 COMPLETED 直接返回原 thread。增加认证 GET operation 接口。Worker 后台完成同一 operation，重启时恢复未完成项。可为现有表增加 created_at/updated_at/error 字段，使用显式、幂等的加列迁移。
2. 客户端 POST 结果丢失时，首先按同一 key GET；CREATING 表示已接受或正在恢复，不是一次业务失败。查询未登记才允许用原参数、原 key 重发；不能改 key 重新 fork。完成后回收同一个 child。
3. 在第一次发 POST 前，把 `record_id` / operation_key 保存到现有 Stage.input_refs。Thread 尚未返回时，不以“thread_id 为空”为依据释放 Case→record 的关联。需要重试时必须恢复这个 record，而非生成新的 record_id。
4. 在 Effect 接收层增加明确的“线程准备中”处理，类似现有 w3_template_initializing：保留 PENDING_RETRY、5 秒后继续查询，不计入通用三次异常失败预算。Worker 明确 operation FAILED 才形成终局失败；认证、参数冲突、取消、租约丢失继续按各自语义处理。
5. operation 给出有限恢复预算，初版建议 180 秒；到期必须先做一次同 key / cwd+parent 对账，再决定终止。终止和取消必须关闭本 operation 的 app-server、等待其退出，再检查是否已有 child，避免“标失败后仍无界创建 child”。恢复查询失败属于明确的基础设施错误，不伪装成未 fork。

### P0-B：隔离生命周期 RPC，杜绝一个调用拖住全部 W3

1. 将共享 AppServerThreads 的生命周期改为 **operation 范围内的 app-server 会话**。同一次 fork 的 list→恢复检查→fork→close 使用一个会话；同 operation_key 仍串行，不同 operation 不共享一把无界大锁。status/delete 使用各自短会话，避免 housekeeping 占住前台 fork。
2. 单个 RPC 初版 deadline 45 秒；进程关闭另给 5 秒，未退出则按已有 process-tree 工具终止本 helper。不能只取消 asyncio.to_thread 的等待者而让底层进程/同步 waiter 继续存活。所有成功、异常、超时路径都回收 helper；保留 fork 完成后释放 writer 的现有要求。
3. 启动复用现有资源治理边界，不增加 ticker/Case 全局小并发闸门；不触碰正在运行的模型 capsule。为 housekeeping 每轮限定数量和总时间，保存续跑位置，避免重启后一次扫完近千历史线程。初版每批 25 条、总预算 30 秒，未完成短间隔续跑；现有 48 小时保留规则保持。
4. 记录 operation_key、RPC method、锁/启动等待、RPC/close 耗时、状态、错误类型。这样下一次能指明真实阻塞位置，而不只有空 ReadTimeout。

这里的 180/45/5 秒属于初始工程预算，需用故障注入和正常 fork 实测验收；不是再次放宽一个不透明的 HTTP 总等待。

### P0-C：修复失败状态和错误归因

修改 `service.py`、`w3_execution.py`、`v2_read/projectors.py`、`api_v2/case_detail.py` 与共享 wire types / 前端详情。

- trade_decision 的准备或执行失败，统一保存 error_code、非空 error_message、失败 phase 到主 Case、W3 route Case 和 Stage。空异常文本使用异常类型和调用位置补充。
- 读模型按持久化事实判状态：已提交 trade decision → RESOLVED；否则终局失败 → FAILED；可恢复失败 → FAILED_RETRYABLE；真实在运行的模型 job → RUNNING；线程准备/尚未派发 → PENDING。不能仅凭没有最终结果就覆盖 FAILED。
- 保持 trade-first 语义：已形成并提交决策后，operations 失败只影响 operations 状态，不把整个 Case 或交易决策倒改为 FAILED。
- W3 route/Stage/Effect 更新也要触发所属 Case summary 重新投影，不能要求另一次主 Case 写入碰巧刷新。API 返回 W3 的失败 phase/code/message；前端区分“线程准备失败”和“模型研判失败”。已有状态枚举足够，无需新增业务结果枚举。
- 对本次 38 个历史 Case 重建读模型即可修正 FAILED 展示；主业务记录的修复应另外记录前后值和原因，不能借读模型重建悄悄重写历史决策。

### P1-A：W2 分轮传输与预算

修改 `transport.py`、`service._call_with_retry`、`factory.py`、`execution_bundle.py` 和必要的配置/turn 可选字段。

1. W2 R1/R2 使用流式 Responses；只在 response.completed 且最终严格 Schema/语义校验通过后记 OK。reasoning/output delta 仅统计进度和长度，不把残缺 JSON 或半个候选集合交给 Router。
2. 明确区分网络空闲超时和总预算：建议 connect=10、pool=10、write=30、read-idle=45 秒；W2 每 attempt 墙钟上限 180 秒。总预算必须覆盖首事件前等待和后续持续流，并支持取消，不能只在收到事件后检查。到期主动关闭流。W1 的调用方式和现有预算先保持。
3. 保持“一次自动重试 + 5 秒退避”，不叠加 SDK 隐式重试。180 秒是让有进展的正常长请求完成的有限窗口，不承诺降低推理耗时。超过预算仍按技术失败，绝不将 timeout/incomplete 视为 NO HIT/NO RELATED。
4. turn 增加首事件耗时、最后进度时间、网络错误阶段、deadline、实际 effort、输入/Schema 字节量和 request/response ID；错误回执尽可能保留已收到的 usage/进度。现有 provider_timeout 可保留为历史兼容分类，但明细必须能区分 connect/read/pool/write/deadline。
5. 不直接设置很小的 max_output_tokens 来解决本次问题；如后续增加总输出预算，必须把 incomplete 单独识别并纳入有限修复重试，不依赖 JSON 解析异常来猜测截断原因。

### P1-B：W2 R1 的轻量调用方式

优先评估 **仅 W2 R1 使用 qwen3.8-flash + reasoning.effort=none**。R2/R3 保持 medium，W1 和 W3 不变，prompt、Policy match_scope、候选上限 3 和后续判定规则均保持。low 本轮未证明有效，不推荐作为默认修复。

实现按 round 冻结一个小 profile 到现有 runtime_models.json / Case frozen_inputs，例如 W2:R1 的 effort/stream/deadline；无需通用模型路由框架。原 w12_effort 继续作为默认，旧 Case 无分轮 profile 时按原 medium 和原冻结参数读取。新 profile 随 Execution Bundle 发布，不能因进程重启而静默改写旧 Case 的模型策略。

上线前使用至少 24 条冻结 Case 对照：空召回、单 Policy、多个重叠 Policy、长正文、短正文/缺正文和已有超时样本都覆盖。评估 required/allowed 候选覆盖以及经过 R2/R3 后的业务身份与路由；多候选不能简单要求 top-3 集合和顺序完全等于一次旧随机输出。质量不下降且尾延迟明显改善后启用 none；若未通过，先发布 P1-A，保留 medium，不用一次计时结果强行降档。

这是一项明确的召回能力变更验收，不应扩展成自动按内容“聪明地”切换模型，也不在空召回时额外调用 medium 猜测结果。

## 6. 历史修复与上线次序

1. 先完成代码与故障注入回归，再同批部署 Worker、scheduler、projector、API、Web。只重启 scheduler 无法修复 Worker 里的共享生命周期等待；只修 UI 也无法让 Case 真正进入 W3。
2. 验证新 fork operation、Case→record 稳定关联和新失败状态，再用自然流入的新 Case 验收。W1/W2 并发仍为每 ticker 6 Case / 12 worker，不为这个问题退回共享两线程池。
3. 为 38 个历史 Case 生成逐条恢复清单：原 Case、Effect、Stage、record_id、operation_key、Worker child thread、是否有模型 job/结果/intent、原始交易窗口是否仍有效。本轮证据中 fork 均已完成、模型 jobs 均为 0。
4. 恢复时接回已有 child，不重跑已成功 W1/W2、不重新 fork。现有 resume-node/resume_effect 可作为入口，但需先补齐“恢复原 record/operation”和非空错误归因，不能直接重置通用重试计数然后让 create_case 生成另一把 key。
5. 已过原始输出窗口的历史 Case 不向生产补发交易；保留原输入、cutoff 和回执，转离线回放/诊断。是否恢复业务执行单独按既有 admission/output_permission/expiry 判定，不重设 accepted_at 或把旧消息变成当前新消息。
6. W2 历史 timeout 如需重试，使用显式恢复代次，保留原 attempt 和业务输入。新调用 profile 的使用必须在恢复记录中明确，不能覆盖旧失败回执或假装此前成功。

## 7. 必要验收

### W3

- Worker 已登记 fork、HTTP 响应丢失：按同 key 查询后接回唯一 child，Effect 不提前 FAILED。
- fork 超过 60 秒仍在执行：显示线程准备/恢复，不消耗普通三次失败预算。
- 一次 lifecycle RPC 永不返回：在 deadline 关闭本 helper；其他 Case fork 和 jobs 查询继续正常。
- housekeeping 慢/大量待清理：不拖住新 fork；续跑游标正确，不改变保留期。
- fork 后 Worker 或 scheduler 重启：恢复原 operation/record，不产生第二个 child。
- Case 被取消或租约丢失：停止后续写入，记录已发生的 operation，不能把运行控制错误吞成普通重试。
- 准备失败时 Case、W3 route、Stage、API、页面一致为 FAILED/FAILED_RETRYABLE，并有非空 phase/code/message。
- 已提交 trade decision、operations 失败：Case 仍按既有 trade-first 规则完成，operations 独立失败。
- 本次 38 个映射均可唯一对账，历史恢复不误派发过期交易。

### W2

- 首事件很快、持续生成 150 秒、最终合法：180 秒窗口内成功；不是 120 秒无结果就失败。
- 首事件前、流中途、写入、池等待各类故障有独立明细；断流和总 deadline 可中止。
- incomplete/只有 reasoning/残缺 JSON/合法 JSON 但业务 IDs 非法：均不能成为成功判定。
- 正常完整响应只计一次 usage；断流重试仍最多一次，无 SDK 重试叠加。
- 新 R1 profile 与旧 Case medium profile 并存；W1/R2/R3/W3 的调用策略不被连带更改。
- 24 条以上冻结样本同时评估延迟、超时比例、required/allowed recall 与下游路由，不能只看平均速度。

## 8. 证据索引与限制

- `snapshot.json` / `summary.json`：853 个 Case、逐轮耗时/token/错误、Effect/W3 route/task 回执。
- `correlation.json`：按轮成功请求的耗时相关性与超时后快速恢复的同 Case 对照。
- `detail.json`：3 个代表 Case 的标题、冻结模型、输入字段字节量、Policy 数量和准备阶段记录。
- `worker.json`、`links.json`、`orphans.json`：Runtime record 与 Worker operation、Codex 创建时间及模型 job 数对账；38/38 均为晚到 fork、0 模型 jobs。
- `provider_probe*.jsonl`：当前 HTTP/最小模型探针，以及 medium/low/none 的两条冻结输入回放。

统计只代表该截面；运行中的 Case 后续可能继续完成。当前网络探针不能追溯历史流量包状态，两个回放样本不能证明某档位全量质量。生命周期日志缺少分段计时，尚不能指定首次阻塞 RPC；本方案通过有限 RPC、operation 恢复和阶段观测解决已证实的系统性缺陷，不把未知部分包装成确定事实。
