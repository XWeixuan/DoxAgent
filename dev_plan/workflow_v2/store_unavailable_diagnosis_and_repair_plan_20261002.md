# STORE_UNAVAILABLE 根因排查与修复方案

日期：2026-10-02。范围：Overview 标的监控及各页面共用读取机制。本轮只做只读诊断、隔离查询实验和方案，不修改或部署业务实现。

## 1. 结论与证据边界

这不是一个可以通过隐藏提示解决的前端问题。当前读取层把查询超时、排队超时、进程不可用和 SQLite OperationalError 都归为 `STORE_UNAVAILABLE`；同时，较昂贵的历史快照查询、包含排队时间的 2 秒总预算、硬超时后的慢进程重建，以及前端关闭自动恢复，共同放大了故障。

**已经确认：**

1. 生产 Web 日志在 UTC 09:22:53–09:23:50（北京时间 17:22:53–17:23:50）记录了七次 `/overview/tickers` 503。同一天还有 `/overview/metrics`、BE `/message-bus/status` 503，说明影响跨模块。
2. UTC 08:51:27、08:51:31 两个普通读取请求分别用时 2101.6、2005.3 ms，达到硬截止时间后两个 read worker 被终止；Web 路径确认两者是 `/read-context`。08:51:35 请求又因无容量失败。首个替代 worker 到 08:51:47 才 READY，第二个到 08:52:05 才 READY。重试落在这段窗口内无法恢复。
3. 生产实际配置是 read workers=2、queue=16、普通请求预算=2 秒、正文/下载=5 秒。`QueryRunner.run()` 从入队时开始计时，队列等待侵占执行预算；普通查询给 SQLite 的软截止时间最多只有约 1.8 秒。`Limits.load()` 还把普通预算上限锁在 2 秒。
4. Overview 的历史快照路径明显比当前水位路径昂贵。生产同一份数据、同一接口，在本轮隔离测量中：最新水位约 63 ms；保存视图约 478 ms；首次读取保存视图约 1067 ms。视图创建后投影继续推进，`seq != highwater` 就进入历史分支，这在正常运行中很常见。
5. `bus.aggregates_many()` 的历史快照执行计划存在 `MATERIALIZE s/p`、`SCAN s/p LEFT-JOIN`；Overview 最新消息查询存在 `USE TEMP B-TREE FOR ORDER BY`，且选择完整 `payload`，会调用 Native Content 解码，即使页面只需要消息时间。
6. 已用生产数据验证一个只读查询候选：把消息源聚合改成先限定绑定，再按精确 ID 批量读取所需标量。两轮共六次测量原查询约 277–303 ms，候选约 8–9.5 ms；正常/异常计数、平均延迟和样本数一致。最新消息标量读取候选也逐标的核对了时间一致性：MU 从约 287 ms 降为 2.3 ms，RKLB 从约 73 ms 降为 3.9 ms。此候选仅在诊断脚本中执行，没有替换线上代码。
7. 前端 `core/cache.ts` 全局 `retry: false`、`staleTime: Infinity`、关闭 mount/focus/reconnect 自动读取；`ApiClient` 丢弃后端 `retryable` 和 `Retry-After`。用户每次点击重试仍可能遇到相同慢 SQL 或 worker 重建窗口。
8. 本轮浏览器在已登录 Chrome 独立标签页观察到四个标的正常显示，人工刷新成功。内置浏览器没有登录会话，未输入或读取凭据。当前正常不等于历史故障机制已修复。

**不能逐条断言的部分：**

- 七次 Overview 503 的具体 SQLite 错误码没有被记录。代码会把协作 deadline 导致的 `SQLITE_INTERRUPT` 和 `SQLITE_BUSY/LOCKED` 等一起吞成 503，内层 HTTP 503 被 query runner 当作正常 HTTP 返回，默认日志不留下原因。因此，超时机制及查询放大已确认，但不能声称七次全都是同一种 SQLite 错误，更不能宣称数据库损坏。
- 并行运维已经在 `changelog` 记录根盘曾只余 8.4 GiB（96% 使用）并完成清理。本轮实测约 29 GiB 可用，API 无 OOM kill、无 cgroup CPU throttling，四标的接口可正常读取。磁盘紧张可能加重 I/O，现有证据不足以认定它是唯一根因。清理由另一个任务完成，不是本方案执行的操作。
- 隔离诊断容器限制为 1 CPU/1200 MiB，API 单独注入诊断 principal，并重用已有 view 内容；没有经过线上鉴权或 query pool，也没有模拟历史故障时的完整负载。上述数字用于定位和比较查询，不是生产端到端 SLA。

证据目录：`eval/store_unavailable_20261002/`。`production-503.txt` 已去掉用户 IP、User-Agent、view token；`production-pool.txt` 保存进程生命周期；`production-profile.jsonl` 保存 SQL 模板与执行计划；`query-candidate-results.jsonl` 保存候选对照结果。

## 2. 修复目标与边界

- 先修查询和恢复机制，再给执行时间合理余量。保留同一 `view_id/seq` 下的 MVCC、筛选、分页、金额精度和权限语义。
- 让偶发暂时失败在组件内有限恢复，不要求用户连续刷新，不清空已经显示的数据，也不让一个模块阻塞其他模块。
- 超时不能轻易拖垮整个普通读取池；真正的库/文件不可用必须如实报告。
- 不迁移数据库，不引入 Redis/外部队列，不增加定时高频刷新，不扩容所有 worker，不重放业务，不删除数据库历史或业务内容，也不增加无界重试。
- 本轮已验证的查询候选可以直接成为实现起点；其他页面只针对同类执行计划逐项调整，不全局机械替换所有 SQL。

## 3. 实现一：精确查询，去掉不必要的物化和正文解码

涉及 `v2_read/repository.py`、`api_v2/bus.py`、`api_v2/overview.py`。

### 3.1 消息源聚合

将 `aggregates_many()` 的三份通用快照关系 JOIN 改成三组有界查询：

1. 查询指定 ticker 的可见 `native:ticker_source_bindings`。在 current/history 两个分支内分别放入 kind、ticker、`valid_from <= seq`、历史 `valid_to > seq` 和 tombstone 条件；仅取 binding ID、source ID、enabled、polling.enabled。
2. 对这批 source ID，批量查询对应全局 source definition 的 enabled 和存在性；对 `(ticker,binding_id)` 批量查询 poll state 的 status/last_latency_ms。两个分支都先限定精确身份，再 UNION；不物化整个 poll state 集合。
3. 在 Python 对这批有限标量做现有聚合。定义缺失时计数仍是 SOURCE_GAP；未轮询、禁用、failed/partial/succeeded 的业务含义和延迟样本规则保持不变。

可在 repository 增加仅供内部调用的标量批量读取 helper，列/JSON 路径必须是代码白名单，值必须绑定参数，身份最多 500 个并分块。不得通过该 helper 输出源配置秘密、打开正文文件或允许客户端任意列/路径。

使用现有身份索引，不先增加索引或执行全库 ANALYZE。只有执行计划证明仍需索引时才加窄索引并说明成本。Overview、Message Bus status 复用同一聚合函数。

### 3.2 Overview 最新消息

每个 ticker 分别从 current 和可见 history 取按 `(sort_key DESC,id DESC)` 的第一条，只取 ID、sort_key、valid_from。比较两条候选，确定真正最新版本后，按精确 `(kind,ticker,id,valid_from)` 读取 `stream_published_at` 标量。

不得在排序前读取或解码完整 message payload。两分支不可因 current 有数据就跳过 history；较新的消息可能只在历史分支对该 seq 可见。保留相同排序和无消息时 NOT_RECORDED。

标的列表本身也先在两个分支内限定 ticker/kind 和状态筛选，再排序分页；limit+1 和 cursor 仍在当前 frozen view 下计算，不能分页后筛选。

### 3.3 同类查询检查

对 metrics、消息源列表、Policy/Event/Case 列表及引用详情检查 EXPLAIN 和读列。只修发现的非必要大 payload、身份 JOIN 物化、LIMIT 前排序问题。Metrics 已有桶聚合继续使用；distinct Policy hit 不能替换为普通相加。不能把所有历史读取改成当前水位来换性能。

## 4. 实现二：区分错误，能关联到具体请求

涉及 `api_v2/app.py`、`query_runner.py`、`errors.py`、API Contract。

每个失败保留父请求 request ID，并在内部日志记录：route 模板、pool/slot、queue_wait_ms、execute_ms、total_ms、失败阶段、SQLite errorcode/errorname、worker PID、是否退休。日志不能包含 bearer、完整 URL 参数、正文、配置或异常中可能含秘密的原值。

建议公开错误分类（现有 Error.code 为字符串，沿用现有错误 envelope）：

| 情况 | HTTP / code | retryable |
| --- | --- | --- |
| 队列满、等待容量超时、worker 正在恢复 | 503 SERVICE_BUSY | true |
| 协作截止或真正执行超时 | 504 QUERY_TIMEOUT | true |
| SQLITE_BUSY / SQLITE_LOCKED | 503 STORE_BUSY | true |
| 确认的库打开失败、磁盘/数据库 I/O 故障 | 503 STORE_UNAVAILABLE | 按具体暂时性判断 |
| DTO/内部数据形状错误 | 500 INTERNAL_ERROR | false |
| Native Content 缺失/损坏 | 明确内容错误，使用相应 HTTP | false，不能无限重试 |

截止时间未到时发生的 SQLITE_INTERRUPT 不能误归类为自身 deadline。其余 SQLite 错误保留具体内部类别，不能仅根据 `OperationalError` 类型判定。

可重试 503/504 统一带 `Retry-After`。内层返回非 2xx 时也记录一次结构化失败；避免内外层重复报错。父子进程异常协议只传安全类别和诊断标量，不传完整异常文本。

## 5. 实现三：排队与执行分开，保留 worker

涉及 `query_runner.py`、`v2_read/settings.py`、`query_budget.py`、`native_content.py`、Compose/API Contract。

1. 普通 read pool 仍是 2 个 worker、16 个有界准入容量，stream/control 分池保持。等待 worker 上限默认 1 秒，超时 SERVICE_BUSY，不杀任何健康 worker。不要把 queued 与 active 数混用来解释统计。
2. checkout 成功后才设置执行截止时间。普通查询软预算默认 3 秒，可配置 1–5 秒；正文/下载软预算默认 5 秒，可配置 2–10 秒。普通执行后的强制终止宽限默认 1 秒。这样普通请求内部总上限约为等待 1 + 执行 3 + 回收宽限 1 = 5 秒，仍有限制。
3. SQLite 通过 progress handler 在软截止时返回 QUERY_TIMEOUT；文件分块读取、Native Content 解码的迭代边界增加协作 deadline 检查。不可把非 SQLite Python 操作假定为可被 progress handler 中断。正常收到完整失败响应就复用 worker。
4. 只有硬截止仍未收到响应、进程死亡、协议/管道异常才退休 worker。已收到 HTTP 503/504 本身不触发退休。
5. worker 恢复同时启动至多缺失的物理槽位；STARTING/TERMINATING 仍计入容量，旧 PID 未退出前不能占用同一槽位再扩容。避免现在一个握手完成才启动下一个的串行恢复。先用隔离 import-time 记录定位无关重依赖，确实与 API 无关的导入才延后；不要重写业务架构。
6. `/healthz` 保留存活语义，API Compose healthcheck 改用已有 `/readyz`，对外明确 read pool serving=0 时不就绪。一个正在恢复的 slot 不应把仍能服务的整个 API 判死，更不能自动重启所有业务服务。
7. 已有 30 秒 deferred 精确聚合机制保留，不把所有普通读取改为 deferred，不增加第二套后台任务框架。

这是有意调整现有 Contract 的 2/5 秒总预算，需要同步文档与配置，不做只改环境值、却被代码上限拒绝的修补。

## 6. 实现四：有限组件恢复与可理解的提示

涉及 `frontend/v2/src/core/api.ts`、`core/cache.ts`、Overview data hook 及共用错误展示组件。

- `ApiFailure` 保留 retryable 和解析后的 Retry-After；有兼容默认值，现有构造调用不破坏。给错误码提供中文文案，request ID 放到可查看的诊断详情，不直接把 STORE_UNAVAILABLE 当面向用户的正文。
- 普通只读 GET 对明确 retryable 的 SERVICE_BUSY/QUERY_TIMEOUT/STORE_BUSY/暂时性 STORE_UNAVAILABLE 自动重试最多两次；优先 Retry-After，缺失时 2 秒、4 秒，带少量抖动。总恢复窗口上限 20 秒并支持 AbortSignal。旧 view 过期、401、参数/权限错误、内容损坏不走这个重试分支。
- 只在一个地方重试，推荐 ApiClient；React Query 全局 retry 保持 false，避免乘法重试。deferred polling 和鉴权刷新沿用各自现有逻辑，不叠加该策略。控制 POST/PATCH/DELETE 不自动重试。
- 重试沿用同一 view/URL，不私自换快照。组件有旧数据就继续显示并提示“刷新失败”；首读失败则显示准确错误与本模块重试按钮。不要整页清空或闪烁。
- Overview 人工刷新继续获取新 context 后分别刷新 status/gateway/metrics/list；模块重试只恢复本模块。修正取消、切换周期、会话变化后的迟到结果归属，旧 view 结果不能覆盖新 view。通过现有 scope/view 校验实现，不增加新的全局状态系统。
- 不恢复高频自动轮询，不改变 IB Gateway 的打开/跳转/人工刷新探测频率，不再添加“数据源同步延迟”的常驻提示。

## 7. 实施顺序与必要验收

1. 先实现错误分类和请求耗时日志，为 SQL、锁等待和容量提供真实归因。
2. 实现已验证的消息源标量聚合、Overview 最新消息与 ticker 筛选查询；做 frozen seq 数据对照。
3. 修改执行预算、协作取消和并行恢复；保持物理进程上限。
4. 实现 GET 有限恢复和错误文案；更新 Contract/Compose，追加 changelog。
5. 本地必要测试通过后只构建/部署 API/Web；依既有部署工作目录及实际镜像基线制作窄覆盖。不能顺带 restart projector、scheduler、交易 executor、消息源或 Codex Worker。

必要测试：

- SQL：current 与历史两个分支；先取 view 后连续投影；enabled/tombstone/source 缺失/poll partial；不同 ticker 状态筛选、limit+1/keyset 分页；同 sort key 的 id 排序；最新消息正文被外置时确认不打开 native-files。与旧实现的字段结果逐项一致。
- Pool：排队和执行各自截止；软超时不退休 PID；硬超时退休且恢复；两个 worker 故障不串行等两个完整启动窗口；正在退休/启动时物理槽位不超上限；客户端取消不会导致旧响应被下一请求读取。
- 错误：注入 SQLITE_BUSY、deadline INTERRUPT、非 deadline INTERRUPT、打不开库、缺内容文件、DTO 错误，检查类别、retryable、日志关联和脱敏。
- 前端：可重试 GET 先失败后成功；次数和恢复窗口上限；不可重试/控制写入仅一次；取消/会话/周期切换；旧数据保持；Gateway 探测次数保持原规则。
- 生产真实验收：投影继续运行时，Overview 打开、组件重试、人工刷新、切周期和筛选；BE/MU Message Bus 状态；其余各页面至少打开并读取主要模块。定向查看新的错误日志，不能用 healthz 或直接 TestClient 200 冒充页面验收。

性能验收目标：在当前四标的生产规模下，保存视图的消息源聚合稳定小于 50 ms，Overview ticker query 执行 p95 小于 500 ms；正常单用户一轮页面并发读取无 503/504；超载时明确有界拒绝，后续轻请求恢复。用固定次数的真实打开/刷新观察，不对生产做无界压力测试。若性能目标未达，先查看具体 SQL/解码/锁等待，不继续盲目增加预算。

## 8. 部署与回滚

本轮没有实施本节。后续实施部署前重新确认实际 API/Web 镜像、Compose、并行改动和数据位置。默认方案不需要 DDL、全库迁移或停写，因此可以只回滚 API/Web 镜像和预算配置。保留原 API 数据契约 envelope；新增错误类别同步前端翻译。

磁盘及重负载运维继续按独立任务处理，不把清理备份、历史数据或扩大服务权限作为本功能上线前置。上线后的日志应能明确回答下一次失败发生在哪里，防止再次依赖多次刷新或笼统 STORE_UNAVAILABLE 判断。


## 2026-10-02 实施结果

本方案已实施并定向部署 API/Web，验证和回滚记录见 `docs/store-unavailable-repair-20261002.md`。原“本轮未修改业务代码/未部署”等语句描述方案产出时的只读排查阶段。
