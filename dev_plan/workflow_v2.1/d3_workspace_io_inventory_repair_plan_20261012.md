# D3 v2.1 内部通信恢复与 inventory 减负修复方案

日期：2026-10-12。性质：代码审查与可执行实施方案，本轮不实施代码、不部署、不启动或恢复研究任务。

## 1. 冻结范围与结论

仅处理两件事：D3 内部 Worker/workspace 通信暂时失败后能在原任务自动恢复；削减 D3 确认存在的 inventory 和派生导航 I/O 开销。

覆盖 D3 v2.1 initialize 的 Discovery、Planning、Build、Integration，以及复用同一 Runner 的 Maintain、断点核验、四文件发布。共享 Worker/client 仅增加必要的兼容接口；D1、D2、D3 v2.0 保持原调用行为。

不改 provider、模型、reasoning effort、研究超时、并发数、业务分批、Policy/Lead/Agenda 契约、prompt/skill，不修本次 PARTIAL 的内容缺口。不重跑 INTC，不迁移生产，不删除历史 workspace/索引/断点。

推荐落地：一个 D3 专用 workspace 恢复适配器、一个明确文件集合的 inventory 接口、D3 专用的材料级紧凑索引模式、发布返回范围收窄。继续使用现有 SQLite 任务和 run JSON，不增加调度服务、消息队列或新数据库表。

## 2. 审查基线与证据

本轮本地 HEAD：`273255c8005cf6c6b26534c73312822591a1229f`。实际实施前重新记录 HEAD 与 dirty 状态，保留其他任务修改。

运行证据：`intc-v21-20261011-01-d3`，远端隔离测试库 `/data/intc-v21-test/research.sqlite3` 和 Worker JobStore。本轮只读查询，无远端状态变更。

| 证据 | 已确认事实 | 不能推出的结论 |
|---|---|---|
| 第一次退出堆栈 | Runner 收集输出的 inventory GET 传输超时，异常击穿主编排 | 不能确认唯一根因是内存、网络或磁盘 |
| 第二次退出堆栈 | 已接受任务 snapshot GET 返回前断连，恢复进程再次退出 | 不能断定一定是连接池旧连接 |
| 当前 client | GET 已有最多3次传输重试，退避0.25/0.5秒 | 不能保证持续几十秒的服务拥塞可恢复 |
| S0002 补研11 | input_files=8575，context_index 路径文件=8284，扫描前8648、扫描后8652文件，约30MB | 8284不是8284张分页，指标统计的是所有索引路径文件 |
| S0004 补研27 | input_files=8493，索引路径文件=8199；input inventory约19.98秒，第一次 output inventory约27.66秒 | 不能把这些扫描耗时直接当作模型耗时 |
| S0002 补研11 | input inventory约8.48秒，第一次 output inventory约28.89秒 | 文件体积不大不代表小文件扫描没有成本 |
| 已保存 host_spans | 344个 inventory span，累计约1787.5秒，最大约38.88秒 | 累计跨并发任务且恢复会覆盖历史 span，不等于墙钟节省29.8分钟 |
| S0002 实际索引目录 | 8320文件/约22.6MB；其中 materials=8282，tasks=38；224个材料索引目录 | 主要开销不是历史 task 导航堆积，不能仅清理旧目录了事 |

本次两个补研超时任务后来第二次 attempt 成功；本方案不调整它们的1800秒预算。

## 3. 代码审查发现

### 3.1 通信恢复只覆盖了很短的 GET 波动，流程边界仍会退出

`src/doxagent/codex_runtime/client.py`：

- `_request()` 对 GET 的 TransportError 重试3次；PUT/POST不重试。
- 所有 HTTPError 都包装成 WorkerUnavailable；因此不能仅凭该异常类或 `retryable=True` 判断是否可恢复，必须辨认原始 cause/status。
- `run()` 对提交/查询原 Worker Job 已有30分钟基础设施恢复预算及幂等 request；不应在外面再套一轮研究重试。

`runner_v21.py` 的故障点不止 inventory：终态 snapshot replay、输入物化、收集输出、snapshot写入、主 workspace 落盘均访问 Worker。`orchestrator_v21.py` 的 `_freeze`、`_verify_frozen`、`_write_view`、最终四文件写入/发布/核验也直接访问 Worker。只在两个报错行局部加 retry，会把下一个 PUT/POST 断连留成新的退出点。

Runner 的 `except Exception` 把 worker.run 异常转成任务 error，随后继续验收。这会把 `InfrastructureRecoveryExhausted` 当作普通研究错误，甚至消耗下一次模型 attempt。必须显式透传基础设施恢复耗尽错误。

`ticker_initialization/substeps.py` 的正式 durable 包装已有 `manual_resume_required` 处理；修复必须沿用这个边界，避免内层15分钟等待耗尽后又被外层当作节点可重试错误无限套娃。

### 3.2 现有任务状态足以承载恢复，无需新状态机系统

`StateV21` 已保存 request、attempt_count、job、files、snapshots。claim只在新研究 attempt 增加预算，RUNNING恢复复用原request，终态Job可复用。

不足在于：Job终态之后的“读取/验收/提交”没有明确的持久进度；异常恢复会重新准备索引和扫描。采集到的文件直到所有snapshot写完才保存为终态，中间失败会丢失已经采集完的本地结果集合。

此外，正式 `@durable` 可能直接返回已成功子节点的结果，和独立正式脚本直接重入 Runner 的路径不同。两种入口都要验证，不能只复现测试脚本。

### 3.3 inventory范围与材料派生粒度共同造成开销

Runner前后扫描采用 `context/`、历史输出的父目录、当前outputs：

- `context/`包含本次不使用的旧任务导航和旧材料版本；父目录会额外覆盖邻接输出。
- 校验实际需要的是本次 frozen 的明确文件集合，而非这些文件所在目录的全部内容。
- 即使改成精确文件集合，本次 frozen 本身仍有约8200个索引文件，目录收窄无法消除大头。

`materials_v21.attach_material_indexes()` 对每个材料调用共享 `context_index.build_index()`。共享索引同时生成完整材料、递归语义记录、正文分页、章节分页、entry、pointer和catalog；材料级cache只避免重复构造，没有减少首次文件数，也没有减少每轮完整性哈希。

`LocalWorkspaceStore.inventory()` 遍历目录并实际读取文件计算SHA；Worker `app.py`用单槽 `DiskBudget`限制此类操作。取消客户端请求不会立刻终止正在运行的磁盘线程，因此盲目增加inventory并发或快速重试可能加重排队。

### 3.4 发布阶段仍隐藏着全workspace扫描

`LocalWorkspaceStore.publish()`完成确定性release后，返回 `self.inventory(run_id)`，扫描整个run；D3实际只需要本次release下的4份文件和manifest。此处应一并修复，否则Build减负后，最终发布仍可能因全量扫描超时。

## 4. 实施A：D3专用通信恢复边界

### 4.1 接入方式

新增 `src/doxagent/workflows/codex_document3/workspace_io_v21.py`，提供 `D3WorkspaceIO`。组合原 `legacy.workspace`，不修改或替换legacy对象本身。

Runner.workspace使用该适配器；orchestrator既有 `self.workspace = self.runner.workspace` 让preparer、核验、view、发布自动共用同一边界。仅实现D3实际使用的方法，不做任意方法动态代理。先列出inputs_v21和维护分支的完整调用清单，防止遗漏snapshot/fork等读写。

现有client保留共享默认行为。D3包装器内部使用单次workspace HTTP尝试，避免“GET三次 × D3恢复多轮”形成重试乘积。确定采用一个轻量 `workspace_view(transport_attempts=1)` 视图，复用原Http client连接与capability，每次调用把尝试数传给内部_request；原client公开方法默认仍GET三次。该视图只有workspace方法，不包含worker.run/cancel，不原地改变共享client配置、不自行关闭共享连接、不每次新建HTTP连接。恢复预算也通过该视图按次传入timeout上限。

对于本地store和测试fake，保持同语义的接口，不凭类名推断可重试。

### 4.2 可恢复与不可恢复错误

自动恢复：可确认的TransportError（连接失败、读取超时、协议断连），及502/503/504。分类依据保留的原始异常或结构化HTTP状态，不解析错误字符串，不对所有WorkerUnavailable一概重试。

立即暴露：401/403、路径错误、404、409、文件缺失、输入/snapshot哈希不符、schema/JSON错误、业务验收失败。500默认不自动恢复，需有明确的基础设施错误码才能加入白名单。取消立即向上传递。

`InfrastructureRecoveryExhausted`沿既有基础设施错误路径透传，Runner不得把它变成模型error、PARTIAL或新attempt。

### 4.3 恢复预算与持久留痕

- 连续通信故障恢复窗口固定15分钟；退避2、5、10、20秒，之后30秒。不新增面向用户的一组配置旋钮。
- 按一次逻辑I/O操作计窗口；请求耗时计入预算，HTTP单次timeout不得超过剩余窗口。成功清理该操作的活动等待记录；不同成功操作之间不累计“整场运行等待预算”。
- 只在故障状态变化/重试时记录operation、scope、path摘要、首次失败时间、deadline、次数、异常类/状态和累计等待，不保存凭据或请求正文。
- 使用现有run JSON的 `workspace_io_recovery` 保存活动等待（key为scope+operation+path/hash摘要）及汇总计数；SQLite短事务合并字段。并发owner不能以旧run整体覆盖对方字段。活动项成功删除，历史只保留聚合值及最后故障，不无界堆日志。
- crash恢复沿原deadline，不因进程重启无限刷新15分钟。已耗尽项自动重入仍停在原边界；显式人工恢复可以重开通信窗口，但不清request/job/attempt。
- 耗尽时抛既有InfrastructureRecoveryExhausted，保留业务phase/任务状态和断点，报告通信阻塞；不伪造一个缺研究材料的PARTIAL。需要人工介入时也不得自动创建新run。

正常请求不多读一次文件、不增加模型调用。等待期间允许其他已进入owner队列的工作继续；不改变max_concurrency、owner隔离或ticker配额，也不为故障恢复设计新的公平调度器。

### 4.4 各操作的可重放边界

| 操作 | 恢复动作 | 不能做的事 |
|---|---|---|
| GET文件/选定inventory | 重试同run/path/query | 不换run，不吞缺失或哈希冲突 |
| 输入PUT、snapshot文件PUT、最终文件PUT | 同一操作固定path及预期内容。响应丢失后先读取；内容/hash匹配即成功，缺失则补写，合法可写目标保持原写入语义 | 不每次正常PUT前额外GET；不覆盖冲突的immutable输入 |
| 可变view/宿主输出PUT | 同一workspace/path写入使用适配器级锁，锁覆盖重试和确认；后来的写入排在旧写入恢复之后 | 不让延迟重试覆盖后来的新版本；不全局串行所有owner |
| 发布POST | 冻结四文件hash与paths，重试同一确定性release；返回后仍完整核验manifest和发布副本 | 不对任意POST统一重放，不新增version |
| 若D3调用snapshot/fork | 仅复用store已有相同identity幂等语义，冲突立即报错 | 不将不同源/目标的fork当作幂等成功 |
| worker.run | 使用原client的同request/idempotency Job恢复，不套D3 workspace恢复循环 | 不重新研究以绕过文件通信故障 |

PUT确认只适用于宿主控制且当前没有agent同时写同一路径的阶段。现有owner锁必须覆盖输入准备至输出验收；目标冲突和只读保护继续由原store判断。不可变文件已存在且内容不同，立即失败，不“修复覆盖”。

### 4.5 任务后处理的最小持久进度

在task JSON增加可选 `io_phase`：`PREPARE`、`WAIT_WORKER`、`COLLECT_OUTPUTS`、`COMMIT_OUTPUTS`；不增加新的业务status或数据库表。

1. dispatch前保存request与原attempt，进入WAIT_WORKER。
2. 收到终态job后，先持久保存job，再进入COLLECT_OUTPUTS；重入只复用job，不能先claim新attempt。
3. 保持原只读校验和输出选择规则。采集完成后将待提交files、snapshot映射、原job错误保存到 `pending_acceptance`，再进入COMMIT_OUTPUTS。
4. 按确定性路径写snapshot与宿主输出。断连/进程退出后从pending_acceptance继续，不重新运行模型，不重新枚举已经冻结的结果集合。
5. 全部写入确认成功后，一次短事务保存现有终态files/snapshots并清pending_acceptance；业务层Policy验收逻辑不变。
6. 已终态任务snapshot replay和run `_verify_frozen`仍核验真实文件，不凭SQLite内容跳过哈希校验。缺snapshot依旧是完整性错误。

旧task无io_phase时按原字段推导：终态走现有replay；RUNNING且有终态job走收集；其余复用现有request路径。旧文件和snapshot路径不变。

## 5. 实施B：inventory改为明确集合，不削弱校验

### 5.1 增加只读批量查询接口

Worker新增 `POST /v1/workspaces/{run_id}/inventory-query`，body为严格schema：`paths: list[str]`、`prefixes: list[str]`，两者默认空，空集合返回空结果，绝不能解释成全run。

- paths只查询确切文件，不递归目录；prefixes只用于当前声明的输出目录。
- 复用service auth、inventory capability、路径containment/symlink检查；跨run、绝对路径、`..`与非法路径直接拒绝。
- 返回现有WorkspaceInventory，文件去重并排序；未存在的确切path不返回，调用方区分输入待物化和输出校验缺失。
- 单请求paths最多512，prefixes最多32。客户端依确定顺序切分paths顺序发送并合并结果；不要用包含数千路径的GET URL，也不要为减少请求一次传无限数组。
- 该POST无副作用，在D3恢复白名单中按只读查询处理；不能因此放开其他POST重试。
- LocalWorkspaceStore新增精确选择函数；复用_metadata和实际SHA，不引入mtime-only缓存。GET inventory原默认不变。
- 仍经DiskBudget，批量请求之间让出队列；本轮不提升共享磁盘并发。

### 5.2 Runner使用三个明确集合

`protected_paths = frozen.keys()`：全部本次声明的完整输入、prompt/skill注入副本、schema、导航文件和受保护历史输出。不再通过这些文件的父目录扩大扫描。

`output_paths/output_prefixes = outputs`：文件路径精确查询，目录只枚举该目录；沿用现有output授权判断，避免字符串前缀误认相邻目录。包括late文件。

`baseline_outputs`：模型执行前对本次outputs记录hash，供失败时过滤未改变旧输出。必须保留该行为，不能因为优化inventory把旧文件当新成果。

- dispatch前：查询protected_paths与本次outputs，物化缺失输入；新增/写入的frozen文件按成功写回的hash确认。
- Job终态后：查询同一protected_paths和本次outputs；受保护文件仍逐个实际SHA比对，缺失/被改动即拒绝。
- 不扫描整个context、attempts、published，不扫描历史输出父目录或邻居文件；也不保护与本任务无关的旧导航。
- 二进制输出维持现有文本后缀过滤，不能恢复成读取JPEG/PDF文本。目录中的非文本文件可以被枚举，但不进入accepted text集合。

这是“校验具体文件”替换“扫描其所在目录”，不是跳过只读完整性。

## 6. 实施C：D3材料索引减少递归派生副本

仅收窄扫描仍需校验约8200文件，因此本轮一起降低有效frozen派生文件数。

给共享 `context_index.build_index/attach_index` 增加可选 `detail="full"|"source"`，默认full，D1/D2等调用完全不变。D3通过materials_v21显式使用source：

- 每个read_mapping材料仍有独立导航，保留完整材料文本/JSON、UTF-8分页、原ref、SHA与source元数据；Markdown章节导航保留。
- source模式只索引顶层材料，不递归为其内部units/state/values等对象再生成完整记录、entry、pointer、分页副本。
- `list --group fields/reports`、asset/pointer读取、分页与章节reader继续可用。没有独立units/records子项时返回明确空catalog，overview注明按材料阅读及完整read_mapping入口；不声称仍有深层pointer索引。
- 不裁剪正文、不减少参考材料集合、不改变agent可读取的文件，不改prompt/skill源文件。代价是取消D3可选导航中的递归子对象快捷目录，换取真实文件和I/O下降。
- 索引identity包含`detail`及固定profile revision，例如`d3-source-v1`；不得以旧immutable路径写入不同内容。
- task首次准备时固定index_profile；旧RUNNING/PENDING有既有request的任务未保存profile时沿用legacy full，禁止在同一未完attempt下改变frozen输入；旧终态只replay。不删除旧索引，新任务才使用source。
- 保持现有best-effort索引失败回退到完整原材料的行为。恢复异常不能被误认为索引不可用而静默丢弃。

不做虚拟文件系统、不增加懒加载服务、不改standalone reader协议、不对所有共享索引重写。若source模式在真实材料样本下不能显著减少文件数，应检查来源重复；不能用删除正文或放弃hash校验凑指标。

## 7. 实施D：发布只返回当前release

LocalWorkspaceStore.publish增加可选 `release_only=False`；HTTP publish增加同名可选参数，缺省保留旧全inventory返回，D1/D2/v2.0不改。

D3调用 `release_only=True`，在确定性release完成后只inventory该 `published/{release_id}/`，返回四个文件与manifest。不扫描整个run，不在主workspace做模糊manifest搜索。

D3继续核对paths/hash映射、manifest内容、发布副本内容和SHA，沿用原version reservation、commit及save_staged_v3幂等约束。响应丢失后同paths/内容重试得到同release，不得新增policy_set_version。

## 8. 文件落点与执行顺序

| 顺序 | 文件 | 实施内容 |
|---|---|---|
| 1 | `codex_worker/schema.py`、`workspace_store.py`、`app.py` | 精确查询模型/store/HTTP；release_only可选返回范围 |
| 2 | `codex_runtime/client.py` | 新只读批量查询方法及workspace单次尝试接入；保留原调用默认；明确HTTP cause |
| 3 | `codex_document3/workspace_io_v21.py`（新增） | D3限定错误分类、退避、同值写确认、同路径写锁、确定性publish恢复 |
| 4 | `codex_document3/state_v21.py` | JSON字段短事务合并辅助；活动I/O窗口及pending_acceptance；不新建表 |
| 5 | `codex_document3/runner_v21.py` | 适配器接入、精确集合、后处理进度、基础设施异常透传；保留attempt预算 |
| 6 | `codex_runtime/context_index.py`、`codex_document3/materials_v21.py` | source模式和profile版本化；共享full默认不变 |
| 7 | `codex_document3/orchestrator_v21.py`、必要的`inputs_v21.py` | 共用workspace边界、run恢复标识、发布release_only及冻结核验覆盖 |
| 8 | tests与`changelog` | 故障注入/规模/兼容验收，重要代码变更追加记录 |

不要先做索引优化再忘记旧RUNNING兼容；步骤1–7应在同一实现批次完成后统一验收。正式durable子节点复用的receipt必须包含已完成I/O提交结果；遇基础设施耗尽沿既有manual_resume_required路径停点。

## 9. 验收：用故障注入证明不重复研究

### 9.1 必须覆盖的通信场景

1. input inventory、输入PUT、输出inventory、输出GET、snapshot PUT、宿主输出PUT、终态snapshot GET分别注入断连，恢复后同run/task/request完成。
2. PUT服务端已成功但响应丢失：读回匹配后成功，不产生新research attempt；mutable同路径后续写入不能被旧重试覆盖。
3. publish已经创建release但响应丢失：只生成一个release/version，四文件hash一致，phase最终STAGED。
4. 终态job已存但COLLECT/COMMIT中崩溃：新Runner实例从持久字段恢复，worker.run的有效Job身份不增加；有pending_acceptance时不重新选择输出。
5. 请求结果未知且Job尚未保存：复用原idempotency，只有原Job；不能用伪造已终态job略过真实查询。
6. 15分钟预算耗尽：InfrastructureRecoveryExhausted，attempt_count不增加、不变成PARTIAL；重启不自动重置deadline。显式恢复只重开通信窗口。
7. 取消及时传播，401/403/404/409、哈希篡改、非法路径立即失败，不能进入恢复循环。
8. 真实正式durable入口和独立脚本直接入口均覆盖；D3 Maintenance复用Runner同样验收。

测试用注入时钟/sleep，不能真实等待15分钟或发模型调用。关键断言为worker Job数、request/idempotency、attempt_count、snapshot/hash及发布版本，而非只断言“没抛异常”。

### 9.2 inventory和规模验收

- 生成包含不相关旧context、attempt、published及相邻输出的fixture；精确查询不得读取其内容/hash。对_metadata/_sha256_path计数证明，没有仅改参数名却仍全扫描。
- 同时存在缺失frozen、被改动输入、旧输出、新文本输出和JPEG：完整性与旧输出过滤仍正确。
- 512路径边界/多batch合并去重/空集合/目录误用/路径逃逸/capability权限分别验证。
- publish release_only仅返回该release的五个文件；原默认仍保留旧行为。
- 用本次INTC任务材料只读副本或等规模fixture离线比较full/source：统计derived文件数、保护文件数、实际hash字节、inventory操作次数与耗时；同一材料完整正文可重组验证。
- source模式派生文件数目标至少减少50%；机器耗时用于同环境前后对比，不写固定几秒的脆弱测试。若目标未达先分析，不能静默放行“只收窄目录”的半修复。
- 不把344个历史span之和当作性能收益承诺；不承诺修复宿主机所有拥塞。

### 9.3 兼容回归与交付边界

优先回归现有 `test_workspace_transport_retry.py`、`test_d3_v21_repair.py`、`test_document3_v21_publication.py`，并增加精确查询、workspace恢复、D3索引profile测试。

随后运行 `test_codex_document3_v21_orchestration.py`、`test_codex_document3_v21_maintenance.py`、`test_context_index.py`，以及共享Worker/client相关 `test_codex_runtime_v2.py`、`test_worker_resource_budget.py`。原full索引reader测试必须保持通过。

静态/模拟通过只确认实现和异常恢复边界，不等于新的正式模型端到端验收。部署如后续获授权，应同时更新编排client与Worker实际/app/src版本，先离线/隔离故障注入验证，不以重跑INTC全部研究作为验收手段。

本轮审查基线已执行：`.venv/Scripts/python.exe -m pytest tests/test_workspace_transport_retry.py tests/test_d3_v21_repair.py tests/test_document3_v21_publication.py -q`，37 passed / 126.01秒；仅第三方opentelemetry DeprecationWarning。这是旧实现的兼容基线，不代表本方案的新恢复与减负项已实现或验收。

## 10. 完成定义

短暂内部通信故障不再要求人工docker start；成功研究只继续I/O验收/提交，通信等待不消耗模型attempt。长时间Worker不可用有明确预算与可恢复停点。目录扫描不覆盖无关历史文件；当前有效派生索引数量显著下降；真实输入/accepted snapshot/发布四文件完整性保持。默认共享接口、D1/D2/v2.0、业务契约、prompt/skill、模型和并发均保持兼容。
