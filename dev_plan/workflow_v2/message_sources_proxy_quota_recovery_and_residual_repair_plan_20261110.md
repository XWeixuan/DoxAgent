# 消息源集中失败：代理额度故障与恢复残留修复方案

2026-11-10，北京时间。范围：现有消息源采集、站点访问和正文补全；用户已授权诊断、修复与必要发布，不要求所有外部站点零失败。

## 1. 已确认的共性根因

生产 UTC 11-09 23:18 的基线包含 85 个启用 Source、542 个 required 采集单元；34 个 Source 至少一个单元失败。不能把所有失败归为网站 Challenge，也不能把 Source 数、ticker 回执数或请求数互相替代。

### 1.1 故障传播链

用户确认代理订阅额度用尽。故障时同一 Collector 容器内的独立进程对照：服务器直连和 `server-direct` 均返回 IP 服务 200；原 `nl-standard-2`、`de-standard-1` 的 HTTPX 为 ConnectError，curl 为 TLS connect error/SSLError。原始 curl 与业务 PublisherSession 的直连表现一致，没有证据支持本轮由 curl 清理代码或所有网站同时变更模板引起。

故障向上产生多种表象：

1. HTTP/RSS/行业适配器通过固定代理请求，在 CONNECT 后的 TLS 建连失败，表现为 SSLError、包装后的 RuntimeError、两路采集失败或 endpoint backoff。
2. Site Access 的独立出口探测连续失败，把 5 个非直连槽位标为 UNAVAILABLE；浏览器候选被准入器排除，实际未发出站点请求，却报 access_combinations_unavailable / SiteAccessError。既往正常的 CRAWLER/BODY 验证记录仍在，不能据错误码认定 Cookie 丢失或 Challenge 复发。
3. 多个主备槽位共用同一订阅供应商。不同出口 IP 能隔离站点侧单 IP 风险，但不能隔离订阅账户额度故障；同一供应商下配置了备用，并不代表拥有供应商级冗余。
4. 出口探测连续失败后的间隔最长 900 秒；适配器也有持久化 endpoint 重试时间。因此额度恢复不代表所有状态立即转绿。需要恢复底层，再观察正常业务回执覆盖旧失败，不能直接改状态或清库。

额度重置后 UTC 23:29 的相同对照，原 NL/DE 槽位 HTTPX/curl 全部 200，实际出口分别仍为 205.198.126.114 / 205.198.126.113。现有服务器配置及认证已能工作，**无需同步新订阅**。本轮不为处理额度问题创建新 Profile、换 IP 或切到直连。

### 1.2 不能混为同一故障的部分

- Reuters 有独立人工 Challenge 恢复记录；网络恢复不是其 Challenge 通过的证据。
- 直连 Wccftech 返回真实 403，与代理额度不足产生的 TLS 错误不同，不据此自动改成直连。
- IBKR 的 Gateway 登录/连接、少数付费正文、模板/解析错误需按恢复后新回执核实。
- 主机存在明显 CPU 排队压力，但没有本轮消息组件 OOM/重启证据；不把当前资源样本推导成所有 TLS 错误的根因，也不借本轮调整交易 Worker 的容量或调度逻辑。

## 2. 执行顺序与变更边界

1. 保留 `eval/message_sources_repair_20261110/` 的脱敏基线：实际容器版本、可用历史窗口的采集周期汇总、当前采集单元、出口探测、正文结果和底层传输对照。查询时间下界为两小时前，但周期表受既有 GC 保留期限制，不能宣称覆盖已回收的历史。以 acquisition.sqlite3 为调度事实库，不读取已退休 bus poll 表冒充当前状态。
2. 逐个调用既有 `POST /v1/egresses/{id}:probe`（5 个代理和 server-direct），使用服务自己验证 IP 并恢复 READY。只在验证成功后恢复；不写假 READY，不 bump generation、不修改节点/Profile/Cookie、不重建 Supervisor。
3. 给原调度器至少数轮自然执行机会。按完整窗口统计每个 Source 的物理 run 与不同 ticker/unit 的真实结果；兼顾 SHARED、QUERY、TAIL 和多入口，不拿一个 ticker 成功外推整个 Source。
4. 对仍失败的 Source，沿最新周期的 acquisition_metadata、endpoint 错误、Site Access 候选理由和 BODY 结果定位。仅必要时使用一次有界、同出口同解析器的真实 canary，不发布历史测试消息，不重复制造 Challenge。
5. 若发现网络恢复后客户端池、持久退避或候选状态仍异常，先用最小可重现案例确认，再在既有对应 owner 内修复；保留检查点、去重、30 分钟准入/闭市 sweep、相关性、免费/订阅权限及首次正文机制。没有可复现内部缺陷就不为“有代码提交”而改代码。
6. 若需要应用修改：main 工作树实现并追加 changelog，运行相关回归；精确 SHA 发布受影响 Collector/Bus/Enrichment/Site Access 角色。仅网络/配置复核有效时无需重建容器。保留另一会话变更与当前 Chrome 现场，不能用重启全栈掩盖问题。

## 3. 验收与交付

- 三层分别报告：出口通、采集真实成功（含有效空列表）、正文 FULL/SHORT_FULL；HTTP 200 和候选 READY 都不替代正文验收。
- 对照故障基线的 34 个失败 Source，列出恢复、部分恢复、仍失败及原因。按自然新周期覆盖旧错误，不清零 failure_count 冒充恢复。
- 发布/恢复后的采样窗口单列，不把恢复前成功混入；不将共享回执扇出计为独立网络请求。正文没有新稿或新回执时明确未覆盖，不主动开启已关闭的普通 recheck。
- 给出仍需人工登录/Challenge、上游限流或待观察事项，允许保留少数外部失败；不承诺仅凭短时窗口达到长期零故障。
- 保存实际执行与验收记录。订阅账户额度属于外部资源约束，本轮不增加自动跨供应商/跨 IP 轮换服务；是否增加独立供应商冗余另议，避免改变身份绑定及风险边界。

### 已确认的单点代码修复：AviationWeek 列表缓存

UTC 23:35 同一 US 固定代理、同一主题、同一解析器做两个有界只读请求：普通 URL 为 200 / Age=321 / X-Cache=HIT / 25 篇，既有 120 秒校验拒绝；附加既有 `_dox_poll` 参数后为 200 / Age=0 / MISS / 25 篇。原适配器只有 `Cache-Control:no-cache`，CDN 仍返回旧缓存，RSS fallback 另有 403，故在缓存生命周期中周期性失败，并非额度重置没有生效。

具体修复：仅为 `public_listing.RECIPES['aviationweek_rss']` 的五个既有主题开启 `fresh_query=True`。复用一分钟桶参数，不增加每轮请求数、不修改频率/页面范围/文章 URL、120 秒门槛或 fallback 表达。现有 checkpoint 仍以 recipe URL 为键，已知文章仍去重，不触发额外正文。回归覆盖分钟桶切换后无重复消息、checkpoint/validator 延续，以及服务端仍返回旧缓存时继续明确失败。仅 Collector 需要发布新代码，不重建 Chrome 或 Site Access。

## 4. 回滚

真实出口 probe 只更新健康观测；不需要业务回滚。若后续有代码修复，使用既有发布工具回滚相关角色到发布前精确镜像。所有检查点、消息、正文、认证信息和 Profile 保留，不做破坏式清理。

后续具体残留根因、变更和验收结果追加到实施记录；若没有新增代码缺陷，明确按运维修复交付，不虚构程序修复。
