# MCP 指标、全部消息源与投影修复适配

## 勘察结论

以 origin/main `e5359cc79` 的前端、共享 contract、API 实现及两份 Runtime→Read 修复回执为基线，在独立工作树 `C:/DoxMCPMetrics` 实施；Desktop 脏文件未改动。上线前逐文件核对生产 API 的 app、只读凭证、Runtime、Bus、文章采样、目录、views、projection_health 均与该基线一致。

1. `article_samples.py`、`bus_dashboard.py`、`runtime_metrics_data` 已实现新统计口径，MCP 继续复用；不复制采样/时间锚点/区间并集计算，也不因缺失时间补 0。原聚合 API 自动复用当前 Runtime 函数，已能取得相关数量、长研判数量及非模型延时；补齐 MCP 的真实抓取延时、Case 标记、筛选与展示投影。
2. 全部消息源页面读取 `GET /message-bus/source-catalog`，无需 view 或周期。只读凭证原先返回 403，本次仅将此 GET 加入白名单。既有 SourceCatalog contract 已有新字段，不必再造 API 或修改业务 schema。
3. 性能修复保持原 Read/API 数据形状及冻结 view，内部物化、索引定位、ACK/连接复用不要求 MCP 重建查询体系。但新增页面同步状态需要适配。实测发现：父 API 构造 worker job 时漏转发 `x-doxagent-projection-clock`，生产显式 opt-in 仍返回无 projection。补齐单个请求头透传，保留旧客户端未 opt-in 时的原响应。

## MCP 行为

- `doxagent_get_overview` summary 保留前端现有全局指标，ticker Runtime 区仅展示当前顶部 KPI：处理数、一轮/二轮平均延时、NEW/OLD、Policy 命中/相关两个数量、长研判数量、非模型平均延时、交易执行数。Bus 补充 `mean_capture_seconds/capture_samples`，直接使用消息加权且排除当前失败源的后端值。
- `doxagent_list_cases/search_cases` 支持 `result=LONG_W3_DECISION`；摘要补充 `runtime_tags/w3_model_seconds/non_model_seconds`。Detail main 补齐页面展示的长研判标记与 W3 模型耗时；缺失值保持 null/原 Value 状态。
- `doxagent_get_overview({"section":"sources","limit":20})` 替代旧 ticker 来源状态查询，读取全局目录。**此 section 不再接受 ticker、period、view_id**；有续页时保留 section/limit 并传 cursor。其他 section 的调用不变。
- 目录仅投影当前卡片展示的名称/简称、轮询计数、配置间隔、主备抓取方式、订阅、公开入口、异常及原七项运维指标。保留动态 sectors 与快照时间；按页面的全局/行业、未标注、停用未使用、测试分区顺序返回。分类与废弃源退出由现有目录 API 决定，MCP 不写死 85/275 或替代关系。
- API 的完整目录只按需获取一次，使用既有 4MiB/128 项、最多15分钟内存续页状态；单页最多20条且仍受128KiB限制。翻页不重查 DB、不新增轮询、缓存服务或索引。内存重启/淘汰后明确要求重新查询。
- 显式 opt-in ReadContext 同步字段，并在 summary/Case 等查询保留对应 view 的 projection：源坐标、核验时间、最近已同步源事件、最早待同步时间和历史缺口。保留原 freshness，不将当前追平解释为历史完整，也不把刷新时间当源事实时间。目录为自己的 live snapshot，不冒充与 Case 同一原子快照。
- 五工具注册方式、微信通知、只读查询权限限制及交易系统保持不变；本轮不发送微信通知。

## 验证

57 项 MCP/微信/全局目录/Bus 回归通过，Ruff 通过。新回归覆盖目录单次读取多页不漏行、停用/测试置后、隐藏字段剔除、空时间保留、长研判筛选、ReadContext 兼容 opt-in，以及真实父 API 的 worker job 请求头传递。原目录权限测试更新为允许精确 GET；写入及其他研究边界回归继续通过。

## 生产验收

应用提交 `76fd43173`，仅覆盖生产 API 的 app/agent_read_auth 两个文件及 MCP 的 client/projection/schemas/service 四个文件；保留当前镜像中的其他修复与所有凭证。API 镜像 `3f57b5f95f18`，MCP 镜像 `8f149bb8b53c`。使用原 canonical release 锁、Compose、健康检查及回退；没有重发 Projector、迁移数据库、清 gap、重建索引或触发交易/模型/微信消息。

**发布中发现并修正的配置漏项：** 原 canonical Compose base 命令没有微信 secret 覆盖文件，但 MCP 环境仍引用该文件。重建后的 Tunnel ready，真实 stdio 初始化却因 token 文件缺失失败。恢复原 `/var/lib/doxagent/agent-mcp/wechat-20261111/compose.wechat.json` 到 canonical base 并重建 MCP 后，五工具初始化及实际读取通过。没有删除或替换凭证；保留该过程，不把最初 ready 记成首次业务验收通过。API/MCP最终均 healthy、restart_count=0。窗口内 Web 被并行任务更新，已记录其容器变更；本任务 manifest 仅发布 API/MCP，不宣称所有其他容器 ID 未变。

2026-11-13 北京时间约 **04:35–04:36** 的生产 stdio 验收：

- summary 一次返回全部10个 ticker，约2.1秒、序列化样本约75KB；新 Runtime KPI 和真实抓取延时字段均存在。首次冷查询曾按截止时间返回7个 ticker及续页，不承诺每次一页覆盖全部。
- 全局目录 **85 个源、320 条订阅**。5页（20/20/20/20/5）无重复/漏行；首请求约1.1秒，续页来自同次内存快照。85/320是本次快照，不写入实现常量。Calcalist 为 L1-05，AWS Platform Marketplace 为 L1-02；来源身份、生产/测试标识、分类和退役结果完全沿用 API，不推断 Massive 是其他源的正式替代。
- `LONG_W3_DECISION` 正向命中 BE 的真实 Case；列表及详情均展示同一个标记和 **192秒 W3 模型耗时**。非模型耗时保持 null。与正常 Case 读取、五工具发现一起验证通过，没有读取全量 Case。
- 当前十 ticker 的 `non_model_mean_seconds` 均是 `NOT_RECORDED`，这属于既有后端可靠锚点覆盖边界；MCP没有填0或用总耗时猜补。新增字段存在不代表历史记录均有可靠锚点。
- summary 的四源 `checkpoint=head`，核验 error 均为空，最老待同步时间为 null；Runtime/Bus 最近已同步事件为近秒级事实，研究/初始化事件时间较早但源坐标同样追平。**只证明采样时追平已观察源头，不构成持续吞吐或源未产出证明。**
- Runtime 历史缺口仍为 **17,204**，其他源为0，aggregate freshness保持 **STALE**。当前消费延迟与历史完整性分开返回，不据追平删除历史缺口或改成FRESH。

证据目录：`artifacts/audits/agent_mcp_metrics_20261113/`，包含实际调用/字段/新鲜度、正向Case标记、部署与容器核验、57项测试收据。服务器私密回退快照位于 `/var/lib/doxagent/agent-mcp/metrics-20261113/`，不提交 state.before/compose.before 等私密配置。

完成后刷新 ChatGPT 的 DoxAgent 插件工具列表，以获得 LONG_W3_DECISION 枚举和新的 sources 调用约束。本轮从实际部署 stdio 验证，不冒充已替 Dots 刷新或执行巡检。
