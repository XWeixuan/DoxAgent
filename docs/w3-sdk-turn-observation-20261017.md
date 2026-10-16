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

尤其要区分“读 task 中的 Policy 字段”和“重新打开 D3 资产”：关键词标签只是工作主题线索，是否真的读取上游研究文件需结合 `research_resources` 和具体调用复核，不能只看 D3_POLICY 计数。

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

## 实际生产发布及自然样本验收

- 初版 `a3713de3` 已提交 main、push、标准构建并于北京时间 2026-10-17 02:00:56 完成发布。原有 W3/Fork 确定性修复依赖一同发布，未手工恢复历史 38 条失败 Case。
- 首条自然样本确认独立回执完整，但暴露 JobIndex 返回副本导致 job 引用的原地修改未持久化。修正为随原终态提交保存，并从完成结果恢复，新增持久化/ACK及重启恢复 2 项测试；41 项相关测试复测通过，不增加每事件数据库写入。
- 修正版 `3c30365a0586959f632d1a62041d2b6ccdd7ea0c` 已 push、重新通过标准 Dockerfile 完整构建，于北京时间 02:19:37 完成 Worker/Scheduler/API/Projector 发布。镜像 `doxagent-v2:main-3c30365a`；源 archive SHA256 `95118b5901b38bbffe12da5d4bf494717227034c2047e3bed68ff38280128d77`。四份关键观测源码与提交 Git blob SHA256 一致；未使用热覆盖。
- 两次都先停止旧 Scheduler 派发，等待已有 Worker 模型任务完成后退出，未中断模型 turn；其他容器 ID 保持一致。API 和 Web 在 API 冷启动后恢复 healthy，Worker healthy，Scheduler/Projector running。
- 新版本的自然 Case `case_9d52460dac224be88de14e65465eb520` 已完成，10 轮 response、9 批工具、约 99.933 秒；独立回执和 job 引用均落盘，ACK 后引用仍保留。该次投影 53.785 ms，原生覆盖完整，没有截断或非法行。
- 采集有效至北京时间 2026-10-17 **10:00:54**，修正版发布没有延长截止时间。已设置本会话 15 分钟一次的只读采集验收：达到 20 条完整且覆盖 HIT/非 HIT，或截止后，停采并汇总；无异常时保持安静。截止时不足样本则按实际覆盖报告，不重放、不补造 Case。

两次完整构建令磁盘余量一度降至约 1.5GiB；只按精确 ID 回收了本轮两个构建的已核对、未使用、非共享缓存，没有删除镜像、容器、卷或业务数据。回收后可用约 6.4GiB，保留发布镜像用于回退。缓存筛选语义核对了 [Docker 官方说明](https://docs.docker.com/reference/cli/docker/buildx/prune/)，实际目标与操作结果在本轮 eval 目录留痕。

### 首批行为线索

以下 5 条均为自然 OPEN Case；HIT/RELATED 和形成意图的路径尚待覆盖。它们处于发布、预热及重建窗口，保留用于机制验收与行为审阅，不当作稳态 p90。详细逐工具、逐 response 表见 `eval/w3_turn_observation_20261017/natural_report.md`。

| Case 短 ID | release | SDK 观测秒 | response 数 | 工具批次 | 首轮输入 token | 最后输入 token |
|---|---|---:|---:|---:|---:|---:|
| f8c91d46 | a3713de3 | 71.652 | 8 | 7 | 46,008 | 86,834 |
| 2b3cde14 | a3713de3 | 103.109 | 11 | 10 | 46,146 | 120,266 |
| e8712e90 | a3713de3 | 125.203 | 10 | 9 | 46,262 | 115,370 |
| 890995f5 | a3713de3 | 125.432 | 13 | 12 | 46,214 | 132,019 |
| 9d52460d | 3c30365a | 99.933 | 10 | 9 | 47,779 | 115,780 |

首例涉及 4 批控制合同读取、2 批 Web；4 批控制相关返回合计约 102KB（分类重叠，不能和其他组相加），工具区间并集仅 8.821 秒，其余约 62.831 秒仍是未归因混合区间。最后一次 Web 返回到最终 usage 锚点约 25.389 秒。它说明控制合同往返与最后决策区间值得优先审查，不能据此断言 Web 返回直接导致变慢。

其他样本也出现导航、D1 读取和多批 Web，前四例工具输出合计各约 139–290KB。各轮缓存 token 明显增长，后续轮多数既有前缀已命中缓存；因此不能将 8.7万–13.2万总输入都当成新增 prefill。后续应同时检查往返数、增量 token、缓存、输出体积和最终产出区间，而不是只比较总输入 token。

表中 SDK 观测秒是本地 stream 观察、usage 补全到投影计时点的时钟区间；正式 SDK `sdk_duration_ms` 仍保留在 job telemetry，并随采集导出，两者口径分别使用。

本轮实现与生产生效已经验证；代表性业务瓶颈结论、HIT/意图覆盖及稳定时效分布，留给正在继续的自然采集，不以当前 5 条外推。
