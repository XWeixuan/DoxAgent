# STORE_UNAVAILABLE 修复与上线验收（2026-10-02）

## 实现

按 `dev_plan/workflow_v2/store_unavailable_diagnosis_and_repair_plan_20261002.md` 实施。本次没有数据迁移、清理历史记录或扩大读取池。

- 消息源聚合只读取 binding/source/poll 的标量字段；固定身份条件进入 current/history 两个分支，保留缺 source、禁用、tombstone、partial 等原语义。
- Overview ticker 筛选在分页前完成；历史分支明确使用既有 `history_recent`，避免 SQLite 选择主键范围扫描。最新消息先选 sort/id 候选，再读取时间字段，不读取正文或 Native Content。
- 分类为 SERVICE_BUSY、QUERY_TIMEOUT、STORE_BUSY、STORE_UNAVAILABLE、CONTENT_UNAVAILABLE、INTERNAL_ERROR。仅明确可恢复的 503/504 带 Retry-After。父子请求共用 request ID；失败日志只记录路由模板、阶段、slot/PID、排队/执行耗时及 SQLite 代码，不输出凭据、查询参数、正文或原始异常。
- 默认排队 1 秒，checkout 后普通执行 3 秒、正文/下载 5 秒，硬截止宽限 1 秒。软截止完整响应保留 worker；硬截止、断管、进程退出和客户端取消才退休。多个缺失物理槽并行恢复，仍计入启动/退休中的进程，不超过容量。
- 修复 asyncio.wait_for 完成与取消同时发生的竞态；流式任务异常在 asyncio.run 返回外层后仍按显式 job deadline 分类。
- 延后 workflows 和 Document3 包的 runner 导入，公共导出不变；本地 import-time 从 2.14 秒到 0.53 秒，实际生产启动仍包含日历预热等步骤。
- 前端仅在 ApiClient 对明确可恢复的普通 GET 重试两次，2/4 秒退避及小抖动，优先 Retry-After，上限 20 秒。写操作、内容错误、鉴权错误、deferred polling 不叠加重试。Gateway 探测不自动重放。
- 刷新失败保留已有组件内容，显示中文错误，request ID/错误码收进折叠诊断。Overview 在取消、会话、周期、筛选和 view 变化后拒收迟到结果。未加入高频轮询。
- Contract 与标准 Compose 同步预算，API healthcheck 使用 readyz。

## 本地测试

- 定向后端及 Document3 兼容回归 72 passed；随后新增 HTTP 故障 envelope/脱敏测试 4 项通过，流式、节点路径与故障测试 16 passed；另补 Overview frozen view 筛选/keyset/limit+1 测试，故障与读取回归最终 15 passed。
- 前端全套 Vitest 55 passed；单独运行新增重试测试 9 passed；TypeScript、修改文件 ESLint、production build 通过。构建保留既有 bundle 大小提示。
- 扩大后端回归 146 passed / 4 failed。四项在修改前 HEAD `eb5bb328` 的隔离源码下复现：Gateway 测试旧 mock 缺 batch_get；消息 ingestion-origin 测试旧输入未产生 raw；reference delta 测试旧响应路径；生成 schema 的来源 hash 已不一致。没有将它们混入本轮修复。对照见 `eval/store_unavailable_20261002/baseline-failures.txt`。
- 测试覆盖 frozen seq 后继续投影、current/history、禁用源、source 缺失、tombstone、partial、同排序键 id 顺序、正文不读取；排队/执行分离、软响应复用、硬截止回收、取消、双槽并行及物理上限；SQLite deadline/nondeadline interrupt、BUSY/LOCKED/CANTOPEN/CORRUPT、缺内容、DTO 与安全请求关联；GET 成功恢复、次数/时间上限、不可重试/写入/Gateway、取消和会话变更。

## 生产验证

生产基线 API/Web 均为 `d2-download-47f6515f`。在既有 release Compose 上叠加独立 override，只替换 API/Web。

- 最终镜像：`doxagent-v2:store-recovery-20261002`、`doxagent-v2-web:store-recovery-20261002`。
- 操作目录 `/home/ubuntu/doxagent-store-recovery-20261002`；基础 Compose 位于 `/home/ubuntu/doxagent-case-release-d11f7fa1`，env_file 明确指向 `/home/ubuntu/doxagent/.env.v2`。
- 最终容器内 14 个修改源文件与本地 SHA256 全部一致；read/stream/control 均 ready，API/Web healthy。实际环境保持 2 个 read worker / 队列 16，queue=1、query=3、detail=5、grace=1；healthcheck=readyz。
- 生产隔离只读数据对照：消息源计数和延迟三次一致；BE/INTC/MU/RKLB 最新消息时间与旧 page 实现一致，正文外置仍无需解码。
- 五次保存视图聚合 8.5–10.3 ms；五次 Overview ticker 路由 200.7–209.4 ms，离散 p95=209.35 ms，全部 200。事故保存视图、近期保存视图、查询开始 head 三组 ticker 请求均 200，208–258 ms。隔离 TestClient 数据性能结果与真实网页验收分别记录，不混为同一证据。
- 第一版 ticker SQL 在生产隔离验证中暴露错误索引选择（20 秒 deadline），部署前已改为显式 history_recent；此失败并非线上请求。
- 网页验收：已登录 Chrome 独立验收页，Overview 首次打开、人工刷新、周期切换、运行状态筛选及跳转返回均成功；刷新期间保留旧内容。MU 基础投研完整正文、预期 Shell/Unit、策略 KPI/Policy、事件指标/列表/重要标签、消息流/消息源/统一监测词配置、运行 KPI/图/记录、成本审计汇总/趋势/节点表均读取成功；BE 消息流与源状态成功。验收时生产 API 无 500/503/504 或 query failure 日志。未对生产制造故障；故障恢复上限与诊断通过隔离测试验证。
- 容器 ID 比对：仅 API/Web 替换；消息获取/正文补全、投影、调度、交易、Codex Worker、Guardian 等保持原容器。

## 回滚

将 override 的 API/Web 镜像改回 `d2-download-47f6515f`，恢复 query=2 并切回 healthz；只对 v2-api/v2-web 执行 `up -d --no-deps`。业务数据、冻结视图和投影继续保留。本轮没有 DDL。

后续正常整包发布须携带本次源码和标准 Compose 预算/readyz 修改，避免仅采用旧 release Compose 丢掉 override。

最终 Overview 独立验收页刷新成功，四个标的完整显示，截图 `eval/store_unavailable_20261002/overview-final.jpg`。部署 digest 与容器差异保存于 `eval/store_unavailable_20261002/deployment-final.txt`。
