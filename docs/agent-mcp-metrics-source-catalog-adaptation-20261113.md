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

生产部署与当前新鲜度核验将在下文记录；历史修复回执中的性能、历史 gap 数量和 85/275 目录快照不作为今天的验收值。
