# Dots 概览 QUERY_TIMEOUT 排查与修复

## 根因

2026-11-24（北京时间）核对当前生产版本：消息源目录已支持既有202延后读取，但 MCP `/overview/operations` 仍受普通 API **3秒**执行期限及外层 MCP **15秒**限制。生产日志多次记录此路由 queue_wait 约0.1ms、execute约3007–3358ms、SQLITE_INTERRUPT/QUERY_TIMEOUT，说明是执行截止，不是长排队或仅凭投影落后可以解释的故障。

受限只读剖析（总SQL预算3秒，不复制生产库）：前三ticker的 Runtime 预热约0.010秒、Overview行约0.057秒；各Runtime约0.0003秒；Bus分别约0.75/0.64/0.70秒，其中真实24小时轮询证明聚合约0.34–0.44秒、文章指标约0.23–0.29秒。单页开始第4个ticker时，剩余0.6秒的旧提前分页判断不足以保证该ticker完整读完；一旦截止，已完成的行也无法返回。解释了首页及仅BE/CEG/COHR之后的续页均会失败。

近期24小时准确窗口需要更多有界事实读取，不能恢复旧窗口或删掉字段伪造提速。前端分开请求页面，MCP把多个ticker串在一页，旧聚合期限不再适配。与近期空间/IO优化并存，但没有证据表明需回退其存储结构或物理IO优化。

## 最小修复及资源边界

- API仅将精确GET `/overview/operations` 接入已经存在的 DeferredQueries，和目录一样**直接提交一次**。不先做必然失败的3秒尝试再重跑，不增加另一种查询或统计口径。
- 延后查询保留原单worker、2个在途/排队任务上限、最多16个短期内存结果、最长30秒查询及原owner/tier隔离。复用既有view范围pin与过期清理，不增表、索引、数据库副本、长期快照或周期任务。按需状态轮询只取内存结果，不重复执行聚合SQL。
- MCP仅summary（含可能读取下一API页的续页）预算改45秒，以覆盖现有30秒执行与40秒HTTP轮询。目录初次45秒不变、目录内存续页及其他查询仍15秒。普通API全局3秒限制不变；没有无限超时或自动重试风暴。
- 每个概览API页最多3个ticker，仍受调用方limit和原字节预算约束；保留真实has_more/next_cursor及原view。这样不把10个ticker的一次完整串读集中占用worker二十余秒；调用方照原分页协议即可读全，不需换参数或重做插件配置。
- 保留当前Bus 24小时口径、所有指标和分页128KiB/20条限制、投影时钟及历史gap；不改交易、Projector或微信通知行为。

相较不停失败重试，本修复让同一组ticker完成一次读取；会允许该真实读取占用原有延后worker数秒，不承诺零CPU/IO成本。没有扩大单次ticker数、时间窗口或历史保留范围。每请求仅使用原有少量技术pin/cursor，沿用过期机制，不新增业务记录流。

## 本地验证

55项相关回归通过：概览/目录的直接延后提交、首页与续页真实API分发/范围pin、原MCP/Case/字段投影/微信通知、202结果读取与所有权边界。测试确认每个概览API页只执行一次HTTP查询，先pin再读，普通foreground runner不执行该聚合。保留已有依赖弃用提示。

## 生产验证与边界

功能提交 `31021fb82`，补充分页提交 `fd744971f`，均已推送origin/main。最终API镜像 `c11e51fdcb3b`、MCP镜像 `a0e7cfa0c66c`，仅更新API/MCP两个容器；其余现有容器ID与发布前一致。三份小源码覆盖前均核对生产字节与对应Git基线一致，保留当前IO/存储优化及所有secret/Compose覆盖，按原release锁、健康与回退发布。

没有只凭延长时间就宣布完成：第一阶段默认10ticker确实成功，但冷启动总耗时28.2秒；随后分段剖析在较高负载下每ticker Bus约1.43–2.37秒，所以追加每页最多3ticker，分散单任务占用。未通过删掉真实24h数据来提速。两次最终本地回归均55通过，保留依赖弃用提示；Ruff通过。

最终实际Linux stdio MCP会话：

- 概览按3/3/3/1完整返回BE、CEG、COHR、INTC、IONQ、MU、NBIS、ONDS、RKLB、SNOW，未漏/重。冷首页约14.64秒，续页6.03/8.06/4.03秒，均成功。
- status、全局metrics、Gateway和余额四块均AVAILABLE，Live/Paper均CONNECTED。
- 目录5页完整85个唯一源，首读约6.05秒，续页为既有内存页；没有新增消息源读取体系或后台预热。
- 四源采样head/checkpoint一致，checked_at正常推进；Runtime历史gap为17,218，仍保留。这证明当前已观察源头追平，不替历史完整性签收。

随后从**当前ChatGPT插件的实际MCP通道**重新查询首页及全部续页成功：3/3/3/1共10ticker，每页约6.14/5.45/5.42/3.31秒，四个全局模块AVAILABLE。切换后首次插件通道曾返回120秒 `MCP request timed out`；这与原API的3秒 `QUERY_TIMEOUT` 不同，保留该首测失败，不倒写成首次即通过。之后同一插件无需改业务参数即可完整读取，最终通道验收通过；未替Dots执行/安排巡检，也未发送微信。

最终API/MCP均healthy、restart_count=0；当前API验收日志未出现新的 `failure=QUERY_TIMEOUT`，不据此承诺所有未来负载永不超时。根盘最终约8.04GiB可用，容量压力仍需既有空间治理；本轮没有整库试写、VACUUM、全机prune、复制业务库或无关清理。

证据：`artifacts/audits/mcp_overview_timeout_20261124/`（55测试、真实stdio、真实插件完整分页、两阶段镜像与最终容器检查）。私密canonical回退记录在 `/var/lib/doxagent/agent-mcp/overview-timeout-20261124/` 及 `...-pagecap/`；不提交state/compose私密快照。源查询仍是按需真实24h读取，因此每次完整巡检有原本必要的读取成本；本轮没有实施大型SQL改造或长期存储性能合同验收。
