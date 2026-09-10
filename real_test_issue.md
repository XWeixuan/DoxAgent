# MU 真实初始化测试问题记录

更新时间：2026-09-10（Asia/Shanghai）
初始化：`init-mu-1f3e9130ae304b01a7fdf2991a35e028`
标的：`MU`
环境：新加坡服务器 `doxagent-sg`

## 当前结论

C3 的报告末尾格式差异不应阻塞 D1 或后续初始化节点，应归类为产物一致性告警/可修复的局部问题。真实执行中，当前实现仍把它作为 `StructuredOutputInvalid` 抛出，导致 `d1.c3` 失败；随后 D1 因失败预算耗尽，父初始化进入 `FAILED`，后续 `O2`、D2、D3、O4 和激活节点均未开始。

本次记录不改变任何远端产物，也不把不完整的 C3 结果冒充为已发布结果。后续应在保留失败审计的前提下，从当前失败集合恢复，复用已成功节点并继续未完成流程。

## C3 证据

- attempt：`init-35e413106c2946d781c452a304817a1a`
- Codex thread：`01a08a28-320b-7963-b8a2-071ea1f48ffc`
- turn：`01a08a28-4403-7931-a432-925f6666a234`
- worker `job_status`：`succeeded`
- `progress.json`：`completed`，六个必需章节均已完成
- 直接错误：`StructuredOutputInvalid: report_draft.md does not match report_markdown`

结构化返回的 `report_markdown` 以如下内容结束：

```text
...终端生产数据。
```

工作区 `output/report_draft.md` 以如下内容结束：

```text
...终端生产数据。 |
```

规范换行并去除末尾换行后，结构化文本为 16136 字符，工作区文本为 16138 字符，首个差异位于结构化文本末尾。校验器只处理换行和末尾换行，不会忽略表格尾部的 ` |`，因此将一个不影响语义的 Markdown 收尾差异判定为失败。

校验发生在 C3 输出晋级和 `completion.json` 写入之前，因此本次 C3 没有生成可晋级的完成产物；这解释了前端显示“失败”以及继续显示已保存内容。

## 相关执行历史

C3 在最终成功返回 thread 之前还有三次执行层失败：

1. 两次 `CODEX_WORKER_ERROR`：Codex Responses 接口返回 `403 Forbidden`。
2. 一次 `CODEX_TURN_TIMEOUT`：turn 超过 1800 秒。
3. 第四次 thread 正常完成，但在本记录的产物一致性校验阶段失败。

当前 thread 期间另有 9 次 `source_capture.capture_source` 30 秒超时；它们属于数据捕获质量风险，不能与本次最终的 Markdown 一致性错误混为同一直接原因。由于 progressive 校验已失败，citation 校验未执行。

## 当前初始化状态

- 父初始化：`FAILED`
- 父错误：`retry budget exhausted: d1`
- `manual_resume_required`：`true`
- `active_revision_id`：`null`
- 已成功：`cdecr`、`d1.c4_pre_scan`、`d1.c1`、`d1.c5`、`d1.c4_enrichment`
- 失败：`d1`、`d1.c3`
- 待执行：`o2`、D2、D3、O4、激活及运行时启动节点

父 D1 记录中还出现了 `WorkflowEvent payload exceeds 16 KiB`。这说明失败事件/上游错误文本没有被充分压缩，形成了第二个需要单独修复的可观测性问题；它不应掩盖 C3 的原始失败原因。

## `WorkflowEvent payload exceeds 16 KiB` 的具体原因

这次 resume 的失败链已经在远端复现并还原：

1. resume 先进入同一个 D1 child run `init-mu-1f3e9130ae304b01a7fdf2991a35e028-d1`，尝试恢复已成功的 `d1.c4_pre_scan` durable receipt，而不是先重新执行 C3。
2. 该 receipt 的 `NodeOutput` 是用持久化内部字段名保存的，例如 `entity_relations[].relation_subject`、`relation_description` 和 `future_nodes[].time`、`future_event`；当前 `EntityRelation`/`FutureNode` 只接受中文 alias `关系主体`、`关系说明`、`时间`、`未来事项`。
3. 远端实际恢复时，原始返回值 JSON 约 14,825 bytes，但 Pydantic 生成了 310 个 `missing`/`extra_forbidden` 校验错误；完整 `ValidationError` 文本约 67,654 字符。durable 恢复重试仍在同一旧 receipt 上失败，未创建新的 worker job/研究 turn。
4. `_execute_or_partial` 随后把完整 `str(exc)` 放进 `node.failed` 的 `WorkflowEvent.payload.error`。事件模型按 UTF-8 JSON 严格限制 16 KiB，因此事件构造再次失败，最终父 D1 只保存了二次错误 `WorkflowEvent payload exceeds 16 KiB`，遮住了真正的“已成功 durable receipt 无法按当前 alias schema 解码”问题。

对照证据：历史 C3/C4 403 的持久化错误文本只有 1,120 字符，对应 workflow event 约 1.1 KiB，故本次 16 KiB 不是 403 HTML 本身导致；本次 resume 在研究 worker 产生新 job 前就失败了。

已做最小修复：`EntityRelation`/`FutureNode` 恢复时同时接受 alias 与内部字段名；D1 失败事件只记录有界异常文本，保留类型/前缀而不把完整 Pydantic 输入值写入事件。没有修改 SQLite、删除 workspace 或伪造 `NodeResult`。部署后应再次从同一 initialization resume，成功 child 继续复用，C3 获得新的正式研究执行机会，然后再进入 O2/D2/D3/O4/Activation。

## 修复部署与继续 resume

- 本地定向回归：`tests/test_codex_document1_workflow.py`，`10 passed`。
- 仅同步了 `codex_runtime/schema.py` 和 D1 orchestrator 两个后端文件，重建远端 `doxagent-v2:server`；没有改动 `/data` 中的控制库、research 库或工作区。
- 第一次重启命令因 Compose 依赖重新拉起 `v2-migrate`，迁移容器在真正迁移前被现存 executor 拒绝：`EXECUTOR_ALREADY_RUNNING`。该容器退出 1，随后确认没有迁移完成或业务数据变化，使用 `--no-deps` 只重建并启动 `codex-worker`、`v2-initialization`；worker 已 healthy，容器内兼容性探针通过。
- 上一次人工 resume 已把 `d1.c3` 留在 `PENDING`、父 `d1` 留在 `FAILED`，所以修复后按正式 CLI 对父 `d1` resume（不能再指定一个非 FAILED 的 `d1.c3`），系统保留既有 C3 receipt/输入并继续其未完成子步骤。命令接受后 `state_seq=74`；当前父 D1 generation 4、`d1.c3` generation 3 均为 `RUNNING`，没有 failed node，新的 C3 worker job `init-ba14d822bedf464d8455439551113aca` 正在运行。
- 当前尚未宣称完成：C3 新 turn 尚在执行，后续 O2、D2、D3、O4、Activation 仍待 D1 成功后按正式编排推进。

## 403 的发生阶段与编排处理

两次 C3 的 `403 Forbidden` attempt 均发生在 worker 建立新 Codex thread 的阶段，而不是 C3 研究已经开始后的 MCP 或 shell 调用阶段：

- attempt 1：`2026-09-10 02:13:50`（UTC `2026-09-09T18:13:50Z`）开始，约 26 秒后失败；
- attempt 2：紧接着开始，约 25 秒后失败；
- 两次均为 `thread_id=null`、`turn_id=null`、MCP 调用数 0、命令调用数 0，`agent_loop.jsonl` 事件数 0；
- worker 错误为 `CODEX_WORKER_ERROR`，响应地址为 `https://chatgpt.com/backend-api/codex/responses`。

因此，这两次 403 位于 `OpenAICodexRuntime.start()` 的 `thread_start()` 请求阶段，早于 `thread.turn()`、研究提示词执行和数据工具调用。首轮 D1 的 C4 预扫描、C1、C3、C5、C4 enrichment 在相同时间窗口也出现同类 403，说明它不是 C3 内容或单个研究工具导致的错误，而是共用 Codex Responses 入口/认证/出口链路问题。后续同一服务器上的 C3 thread 能成功建立并执行，说明 403 不是本次 C3 最终产物校验错误的直接原因。

编排处理链如下：

1. worker 在 `runtime.start()` 抛出 403；`WorkerJobManager` 捕获后将 job 标为 `failed/CODEX_WORKER_ERROR`，不产生 thread/turn，也不在 worker 内自动重试。
2. D1 runner 将“无成功 job/final response”转换为 `StructuredOutputInvalid`；D1 orchestrator 记录该内部节点失败，并继续尝试其他并行子节点。
3. 当前 D1 代次结束时，只要 checkpoint 仍有失败节点，就记录 `workflow.failed` 并让父 D1 失败；初始化 worker 再按父节点预算自动尝试一次。
4. 首轮 D1 的共同 403 消耗了第一代；第二代恢复并复用了成功节点，C3 经历 timeout 后成功建立 thread，但在报告一致性校验处失败；父初始化最终因 D1 失败预算耗尽进入 `FAILED/manual_resume_required=true`。

## 本次人工恢复结果

已按同一 initialization、限定 `--node d1.c3` 发起一次人工 resume。命令被接受并短暂进入 `QUEUED`，但约 8 秒后父 D1 再次以 `WorkflowEvent payload exceeds 16 KiB` 失败；`d1.c3` 保持 `PENDING`，没有获得新的研究执行机会，O2 及后续节点仍未开始。根因已定位为 durable receipt 的 alias schema 兼容性问题；修复后会继续沿同一初始化 resume，不重建初始化、不清理历史产物。

## 后续处理原则

本次应使用同一 initialization 的人工 resume，目标限定为失败的 `d1.c3` 及其 D1 父节点：成功的 CDECR 和 D1 子节点不重跑，恢复后继续未完成的后续节点。不得直接修改 SQLite、删除工作区、伪造 `NodeResult` 或手工激活 revision。

## 2026-09-10 再次阻断：C3 引用别名来自旧 attempt

本次 SSH 复核的当前状态：

- 初始化状态：`FAILED`，阶段 `UPSTREAM`，`state_seq=94`；父错误仍为 `retry budget exhausted: d1`，`manual_resume_required=true`。
- 本次 C3 attempt：`init-2e00e1d960fc4950b4b003d1cdffb286`；Codex thread：`01a08a28-320b-7963-b8a2-071ea1f48ffc`；worker job 为 `succeeded`，不是 403，也不是 1800 秒超时。
- 直接失败：`StructuredOutputInvalid: unresolved citation aliases: O149, O196, O209, O223, O225, O238, O240, O241, O242, O248, O251, O254, O257, O261, O268`。
- 当前 attempt 的 `observations.sqlite3` 为 0 observations / 0 calls；但 `agent_loop.jsonl` 中的 Data MCP 读取了 `context/mcp_data/init-ba14d822bedf464d8455439551113aca/...`，而旧 `init-ba14...` 的 Observation Store 有 271 个 observations / 36 个 calls。

因此这不是报告内容本身“引用不存在”的随机错误，而是跨 attempt 的 thread/MCP 作用域错配：`thread_resume` 继续使用旧 Codex thread 时，Data MCP 与 Source Capture 的 stdio 子进程仍持有旧 attempt 的启动环境；当前 attempt 的输出被写入新目录，但工具证据留在旧 attempt。C3 引用晋级只接受当前 attempt 的私有 Observation Store，拒绝这些旧别名是正确的安全行为。

同时确认一个上游缺口：C4 预扫描的引用主要位于 `entity_relations` / `future_nodes` 的结构化字段，旧逻辑只从 `report_markdown` 生成 citation manifest，故其 C4 manifest 曾为 0 entries，不能向 C3 重绑定这些证据。

已做局部修复并完成本地验证：

1. D1 节点存在历史 attempt 时不复用旧 thread，改用新 thread，使 Data MCP/Source Capture 与本次 attempt 对齐；正常首次运行的跨节点 thread 连续性不变。
2. citation promotion 扫描完整的已验证 `NodeOutput`，不再遗漏 C4 结构化字段中的 `【cite:O#】`。
3. upstream rebinder 递归改写结构化字段中的引用，并继续重绑定 `observation_candidates.source_aliases`。
4. `uv run pytest -q tests/test_codex_document1_workflow.py tests/test_codex_d1_attempt_protocol.py`：`27 passed`，仅有 3 个既有依赖弃用/实验性警告。

当前仍未直接修改远端控制库或手工补写 citation。下一步是用新镜像部署这两个局部修复，按同一 initialization 的正式 `resume` 重新执行 `d1.c3`，确认当前 attempt 的 Observation Store 有数据且 citation manifest 可解析，再继续 O2/D2/D3/O4/Activation。

## 修复后正式 resume 结果（2026-09-10）

- 已构建并部署新镜像；只使用 `--no-deps --force-recreate` 重启 `codex-worker` 与 `v2-initialization`，没有重启或执行 `v2-migrate`。
- 通过正式 CLI 对同一 initialization 执行 `resume --node d1.c3`，控制面接受后状态为 `QUEUED`，随后进入 `RUNNING`；`manual_resume_required=false`，没有 failed node。
- 新的 C3 attempt 为 `init-60683824fe444a028de17d1758eb9cd4`，执行版本 SHA 为 `d0fe303c59cc39105212be2d9c7400d393712acf5b18882696f1f268cfd5f3a1`。
- 新 attempt 已产生 38 个观察文件，`progress.json` 为 `in_progress`；与旧 attempt 的观察库隔离，未再看到旧 `init-ba14...` 路径被当前 attempt 直接使用。
- 最近一次 SSH 复核：父 D1 与 `d1.c3` 仍为 `RUNNING`，阶段 `UPSTREAM`，没有新的 403、16 KiB 或 OOM；worker healthy，主机可用内存约 5.1 GiB。

当前不能宣称初始化完成：C3 仍在真实研究和最终结构化校验阶段，O2、D2、D3、O4、Activation 仍待 D1 成功后由编排自动推进。半小时一次的 SSH 轮询继续保持运行。

## 2026-09-10 最新阻断：已成功 C3 后被历史 C4 失败标记否决发布

15 分钟低频复核时确认，新 C3 attempt `init-60683824fe444a028de17d1758eb9cd4` 已成功，`d1.c3` generation 4 为 `SUCCEEDED`；父 D1 随后在确定性发布阶段失败：

- 初始化：`FAILED` / `UPSTREAM`，`state_seq=113`，`manual_resume_required=true`；
- 父 D1：generation 5，错误 `RuntimeError: Global Research publish blocked by: c4_pre_scan`；
- C1、C3、C5、C4 enrichment 均已有成功产物；C4 pre-scan 控制节点也为 `SUCCEEDED`，并保留非空 `entity_relations` / `future_nodes`；
- 未进入 O2，故本次没有消耗后续节点预算，也不是新模型或 MCP 执行失败。

直接原因是远端镜像仍使用旧 Global Research 发布 gate：它只要看到 workflow checkpoint 中任意历史 `failed_nodes` 就否决整份发布。C4 pre-scan 早期 403 留下的失败标记因此覆盖了当前可用成果，即使相关控制节点及核心 C1/C3/C5 已成功，仍被当作全局失败。这与“C4 缺口可降级、核心研究主体可用即可交接”的既定边界冲突。

本地鲁棒性批次已包含对应修复：发布 gate 改为只检查 C1/C3/C5 是否具有可读报告，C4 enrichment 缺失时复用有效 pre-scan，历史可选节点失败仅记录 `workflow.partial`；同时保留 ticker/输入身份、artifact hash、核心报告缺失和发布存储等硬门禁。新增回归覆盖 C4 pre-scan 执行失败而 C1/C3/C5 可用时仍能发布，避免再次由历史失败标记阻断。

### 部署与恢复结果

- 本地提交并推送 `d0b09e5b4981d752573da222513fdf16e4a4d6e9`；远端 `/home/ubuntu/doxagent` 已通过 `git pull --ff-only` 快进到同一提交。
- 远端既有工作树先保存为 `stash@{0}: pre-d0b09e5b-sg-working-tree`；与本次提交无关的部署配置已恢复到工作树，stash 继续保留作为恢复点。
- 重新构建共享 `doxagent-v2:server` 镜像，并用 `--no-deps --force-recreate` 更新十个后端常驻服务；未执行 `v2-migrate`，未修改 `/data` 中的业务库或历史工作区。API、Codex worker 和 Web 健康，其他后端服务均在运行。
- 正式 CLI 对失败父节点 `d1` 执行 resume 后，D1 generation 6 成功；`d1.assemble`、`d1.publish` 均生成独立成功回执，证明本次只恢复确定性组装/发布，没有重新运行已成功研究节点。
- 初始化已自动进入 O2：`RUNNING` / `O2`，`state_seq=131`，当前节点 `o2`、`o2.o2-survey`，无 failed node。后续继续按 15 分钟间隔低频复核。

## 2026-09-10 O2 最终修复后的断点恢复阻断

15 分钟低频复核确认，O2 的 16 个 wave、global reconciliation 和 repair-001 均已成功；repair-002 对 1,622 行日期解析账本完成了格式恢复，逐行 tolerant ingest 为 `BAD=0`。但父初始化随后以 `retry budget exhausted: o2` 失败，出现两个相互独立的问题：

1. 基础 O2 阶段已全部完成、仅修复阶段失败时，resume 跳过阶段循环后仍引用循环内的局部变量 `result`，触发 `UnboundLocalError: cannot access local variable 'result' where it is not associated with a value`。
2. repair-002 的 bundle 在正确 initialization context 下仅剩 `INITIALIZATION_IMPORTANT_ALL_FALSE` 一个 ERROR。996 个 delta 被确定性保留为 pending，没有身份、hash、关系闭包或账本结构错误。重要性属于研究判断；系统既不能伪造 `is_important=true`，也不应因此阻断整个 ticker。该诊断应保留为 WARNING，并让 bundle 以 `PARTIAL` 发布。

最小修复为：resume 从远端 immutable inventory 选择最新已有 repair bundle，且缺少本轮模型 `O2RunResult` 时直接依赖确定性 validator 的 coverage，不再引用未初始化变量；validator 将 `INITIALIZATION_IMPORTANT_ALL_FALSE` 保留为 warning，不改变其他语义/身份硬门禁。新增回归覆盖 repair-002 bundle 选择和 all-false importance 的 `PARTIAL` 可发布行为。

部署提交为 `51b6d9a1`；本地相关测试 `18 passed` 且 Ruff 通过。远端只重建并替换 `v2-initialization`，未执行迁移、未改数据库或 workspace。第一次替换误用了非生产 Compose 基文件，容器因缺少配置在业务启动前退出；随后立即按原 `docker-compose.v2-production.yml` + server overlay 恢复，容器内已确认新代码。正式 resume 同一 `o2` 后，O2 从 repair-002 bundle 确定性恢复并发布，初始化已进入 `RUNNING / D2`、`state_seq=206`，当前运行三个 D2 O0 candidate 节点；`failed_nodes` 中仍保留历史 `o2.o2-repair-002` 记录，但不再阻断父流程。

## 2026-09-11 D2 并行 Shell 在 Data MCP 启动超时后失去结算

SSH 恢复后确认初始化在 `state_seq=300` 以 `retry budget exhausted: d2` 失败。D2 O0 已完整成功；O1 的直接证据包括：一个 finalization 在创建 thread 前因 required Data MCP 20 秒握手超时失败，一个 gaps 随后报 `Cannot send a request, as the client has been closed.`，另一个 realization 被父节点停止并记为 `parent stopped before internal stage settlement`。前者是零 token、零 MCP 调用的瞬时能力启动故障，不是研究内容错误。

编排缺口是 O1 shells 使用默认 `asyncio.gather`：任一分支逸出原始传输异常时，父 D2 立即退出并关闭共享 HTTP client，但其余并行 coroutine 尚未完成，因而产生 closed-client 和未结算子节点。局部修复改为等待所有 shell 分支完成/失败后统一结算；仅允许已分类为 format/transient/shell 的 D2 错误及明确的 closed-client 传输错误降级为失败 shell，SYSTEM、身份、存储、租约和 capability 错误仍硬阻塞。
