# W3 SDK 交易 turn 业务步骤观测（2026-10-17）

目标是用生产自然流量回答：Agent 在做什么、为何发生多轮往返、上下文在哪次工具返回后变重。探针不改变提示词、模型、权限、路由或交易决策，不新增模型调用，不用历史消息重放代替自然流量。

## 本轮实现

`codex_worker/w3_turn_probe.py` 只观测 `trade_decision`。SDK stream 的 item started/completed 保存单调时钟相对时间，包含 contextCompaction；token 通知保留到明确上限。turn 完成或流失败时，在工作线程一次读取该 turn 原生 rollout，补齐 code-mode 外层调用、内层工具名及逐 response usage。没有常驻 rollout 轮询和每事件数据库写入。

Case 只增加一个宿主 audit 身份文件，不改变 user prompt 或业务 schema。回执关联 Case、run、attempt、thread、turn、phase、模型档位、版本 pin 和 release revision；job ID、Worker generation 可由保留的 Worker job/资源回执关联。Worker `resource_receipt.w3_observation` 和 turn summary 保存紧凑引用，兼容原有客户端。独立回执保存在 `/data/workspaces/w3-observation/receipts`，不随 ACK 或 Case workspace 回收消失。

开始时先保存 `observing_partial` 身份。进程被强杀或停采后未完成的记录继续明确为 partial；不填零、不伪造成功。正常 stream 异常则尽力保存已收到事件及原生记录。缺失 rollout、起点、结束或越过容量上限均在 coverage 标识。

## 如何理解业务步骤

| 分类 | 可见依据与用途 |
|---|---|
| CONTROL_CONTRACT | AGENTS / Skill / task / schema；发现重复识别控制合同 |
| RESEARCH_NAVIGATION | index、find、ls、pwd；发现路径探索和未一次定位 |
| D1_RESEARCH | D1 原文、章节路径或明确业务词 |
| D2_EXPECTATIONS | D2、Shell、Unit、预期查询 |
| D3_POLICY | Policy / D3 路径或查询词 |
| EVENT_HISTORY | Reference、历史意图、WATCH、PIN 等 |
| WEB_RESEARCH | Web 调用；无额外依据时只归到这里 |
| SOURCE_VERIFICATION | 核实、verify、IR、SEC、press release 等表面证据 |
| FIRST_SOURCE_SEARCH | 明确“最早、首发、原发、earliest、original source”等词 |
| MARKET_PRICE | IBKR、历史 K 线等 |
| CAPABILITY_DISCOVERY | 查工具 catalog、ALL_TOOLS、provider/IBKR 代码 |
| OUTPUT_WORK / OTHER_TOOL | 输出工作及尚不能归类的工具 |

分类来自参数、文件路径和公开 commentary；不保存隐藏 reasoning。公开 commentary 只投影分类，不留原文。单批多项工作保留多标签和依据，不能将各组时间相加。一般 Web 搜索不能被自动认定为“找最早原发”。这是可解释的初步归类，精确业务目的仍需结合实际 Case 审阅，不新增分类模型。

回执保留相对研究文件名、来源域名、call ID、内容 hash、字节数、固定错误线索，不保留工具/网页正文、完整命令或完整查询。完全相同参数的重复调用、同文件反复读取、继续等待同一个 cell、同类错误再次发生可据此检查；“重复”只是候选，不自动等同于无效研究。

Data MCP 的 provider、latency、availability、inline/pack/raw bytes 直接从本 attempt 已有 Observation calls 复用，不重新调用工具。code-mode 内层 Web 如果没有独立返回时间，只记录名字和外层批次区间，不编造精确嵌套耗时。

## 如何判断轮数多还是上下文重

每个 response 保留 response ID、input/cached/uncached/output/reasoning-output token、相对前轮的 input 增量、前序返回工具列表及总字节、本轮发出的动作分类、前序业务分类，以及三个时间值：

- 相邻 usage 完成锚点间隔；可与工具区间并集分开。
- 该间隔内可见工具之外的剩余时间。
- 最后一次工具返回到当前 usage 锚点的间隔。

结合逐轮输入和前序输出，可以定位“大返回→输入上涨→后续轮变慢”的位置，以及“上下文不大但轮数很多”的路径。usage 锚点不是 provider 的 response started；以上间隔包含模型、服务排队、传输、生成 JS 等混合时间，不能称为纯推理、prefill 或服务端排队耗时，更不能仅凭相关性认定因果。

保留首轮和所有后续轮直到明确上限，不再只依赖旧的末尾 256 条事件或最慢 5 项。SDK item 的 compaction 起止若都存在可测区间；原生 rollout 只有完成标记则仍是 completion_only。

## 启停、容量及开销

默认关闭。控制文件在共享数据卷，下一条交易 turn 读取；已观测的在途 turn 在结束时再次检查，关闭后不再做原生投影，可能留下 partial 起点。无需重启、重新 build 或改变业务配置。

```bash
sudo docker exec doxagent-v2-codex-worker-1 python -m doxagent.codex_worker.w3_turn_probe enable --hours 8 --max-samples 500
sudo docker exec doxagent-v2-codex-worker-1 python -m doxagent.codex_worker.w3_turn_probe status
sudo docker exec doxagent-v2-codex-worker-1 python -m doxagent.codex_worker.w3_turn_probe disable
sudo docker exec doxagent-v2-codex-worker-1 python -m doxagent.codex_worker.w3_turn_probe report
```

最多保留 500 个回执、128 MiB、14 天，先到者触发滚动清理；CLI 上限 2000，时间最长 168 小时。并发完成时允许瞬时超过数量阈值，随后清理收敛。每 turn 最多扫描 32 MiB 原生片段、1000 个调用、1000 个 response、1000 个 SDK item/usage 通知；达到上限标记 partial，不静默声称完整。Worker 原有 dispatch loop 每小时清理到期文件，停采后仍清理，无新增定时服务。

事件热路径仅保存标量和小字典；起点文件及结束投影在线程执行。`projection_ms` 记录投影成本，业务失败不会因探针重试。保留与写入失败只降级观测。首次上线需检查真实回执大小、投影耗时和自然任务成功率；任何异常可立即 disable。

## 验证与上线边界

222 项相关测试通过，包括本轮 8 项新测试，以及原有 Worker、W3、回测、读模型、权限及资源治理回归。随后资源路径/错误线索增补再次通过 8 项新测试。测试覆盖字节计数、usage 去重、跨 turn 排除、缺失记录、并行区间并集、原生截断、控制过期、流失败和独立留存。

本次还提交前一轮已完成但尚未提交的确定性耗时/Fork 修复依赖，见 `w3_deterministic_latency_repair_receipt_20261016.md`。其他会话的 D2/D3 提示词改动不纳入发布。历史 38 条失败 Case 不在本轮手工恢复；自然流量观测不批量触发旧任务。

生产采用已提交 main 的标准 Dockerfile 完整构建，只替换 Worker 和 Scheduler，Worker 先发布；两者切换期间停止旧 Scheduler 派发。API/Projector 的前轮失败展示修复一起更新。保留原数据库、卷、认证与配置；不部署其他研究服务和 Message Bus/Site Access。发布 SHA、镜像、源码校验、健康状态及自然样本在后续发布记录追加。
