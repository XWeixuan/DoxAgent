# Ticker 新闻源正文与时延：诊断、修改及真实验收

日期：2026-11-13（北京时间）。按 Barron's、Investing、InvestorsHub 顺序执行。新闻发布时间→首次列表发现、首次发现→总线可消费、正文网络/渲染分别统计，不把 BODY canary 耗时冒充端到端指标。

## Barron's：已修通正文与认证链

### 失败机制

- 两日最近19个文章工作样本仅1个 FULL。多篇首轮恰好8秒超时，随后重试再耗时2–8秒；实际发现→消费存在18–31秒样本。固定8秒阶段预算在唯一浏览器路径上切断正常启动/渲染，增加第二轮排队。
- `.crawler` **不是可靠的预览判定**。主 Chrome 的真实文章可见正文仍在这一外层类中，付费段落使用 `.paywall`，其祖先 display/visibility/opacity/overflow 均正常。旧代码按类名将这些正文直接判成 subscription_required，进一步污染身份认证。
- 主身份原 LOGIN 原页已不存在，但会话为 RELEASED、Profile 保留 MAINTENANCE；旧 Close Login 直接调用不存在的控制句柄，无法收尾。主身份只有一个 New Tab 的原生证据与精确旧 target 缺失均已核实。
- Scope 候选筛选丢弃原因，使维护、缺少认证等内部门禁统一退化成 access_combinations_unavailable。
- 用户确认已登录且可读订阅正文。本轮观察支持这个信息；早期旧识别器对备用返回 PARTIAL，不能据此确定备用真实没有订阅。

### 修改

1. 沿用普通 Chrome、原 Profile/出口和 CDP，读取当前 URL slug 对应的 article；仅复制实际可见的 data-type=paragraph 正文，排除推荐/导航/版权区。检查隐藏、透明、aria-hidden 和祖先裁剪；不修改页面可见性/权限，不读取 Cookie 或账号存储。
2. 正文连续两次一致且完整才返回最小快照；最多12秒且受现有请求预算限制。BODY 与管理员 PROBE 使用同一读取。付费完整性与认证证据保留，短正文复用既有 Candidate.complete 合同；隐藏/裁剪/不完整仍失败。
3. 唯一 Barron's 浏览器路径默认阶段预算15秒，显式 Registry 配置仍优先，总作业预算和重试次数不增加。
4. Close Login 复用现有精确原页检查和失效登录收尾，仅对普通 LOGIN、确认丢页时释放预约；不丢 Challenge，CDP 查询不确定时不释放。
5. 保留候选被拒绝的原因；HTTP列表每轮单次新鲜查询，未增加双路重复轮询。

### 列表新鲜度对照

同一 MU JSON：普通HTTP Age11/CloudFront HIT，fresh query MISS，正文哈希及前五条完全相同；普通HTTP0.21–0.75秒。现有 Chrome/CDP 路径2.08秒，前五条及发布时间仍相同。没有证据支持将当前HTTP主路径换成浏览器会改善时效。已保留HTTP并避免CDN缓存键复用；不会把“时间戳重写”当时效优化。

部分列表发布时间只精确到分钟；滚动更新的文章输入还出现晚于初次发现的最新发布时间，不能混作首次发现延迟。当前没有每次旧列表全文的完整历史，不能倒推出历史每篇文章进入上游 ticker 索引的精确时点。

### 真实验收

原失效会话 Close Login 返回 `session_closed=true / original_page_lost=true`。之后复用主 Chrome，临时建立的诊断登录会话在真实文章 Verify 后返回 `VALID / READY / FULL`、`session_closed=true`、`cleanup_pending=false`。Chrome instance `af9b4264383b43ae8243b88dcad57047` 保留，Supervisor 容器未重建。

在正式 Enrichment 容器内经 SharedContentExtractor/body_v2.2 执行三篇实际失败文章 canary；没有创建/发布新的消息：

| 文章 | 结果 | 正文字符 | 全管线 canary 耗时 | 浏览器次数 |
|---|---|---:|---:|---:|
| Intel CEO insider purchase | FULL | 2606 | 2.954s | 1 |
| ETFs beating S&P without Nvidia | FULL | 3641 | 3.010s | 1 |
| Micron memory chip stocks / Nvidia | FULL | 1855 | 3.618s | 1 |

平均3.194秒是本轮真实 BODY canary 耗时，**不是**新的消息发现→总线平均耗时，不能与用户24.76秒直接作同口径对比。历史失败和初次结果没有改写，recheck仍关闭，未批量重新发布旧文章。总体新文成功率及消息端到端改善需自然新增样本验收；不能称已跨日稳定。

128项相关回归通过、1项Yahoo锚点测试排除；先前该Yahoo实浏览器用例出现重复导航断言失败，本轮未改Yahoo。后续订阅认证完整性与Bloomberg兼容补充组合75项通过、1项同用例排除。

## Investing：列表缓存排除、正文跨地区参考链接修复

实际 primary 为 `public-markets-nl` External Chrome，保留30秒目标周期。NBIS、MU四次普通/fresh对照均200、Cloudflare DYNAMIC、private/no-cache/no-store，列表一致，0.91–2.01秒。既有 origin fetch 本身已使用 cache:no-cache，未发现本地缓存或旧CDP页面复用上一ticker结果。

真实坏例英国版 CoreWeave/Nebius 比较文章中，`Read more` 引用美国站的另一篇财报。旧跨host逻辑将其误认“本文章的原文”，产生 source_summary_only→额外抓取其他文章→登录墙/超时。现在仅在当前文章区域判断展开，Investing同品牌不同地区的其他文章仍为引用；真实外站原文与付费墙保持。

部分11分钟级延迟样本实际是Google RSS召回国家版本后交接给Investing，原始 `google_handoff.from_source_id` 和 live_origins 均证实，不能作为美国ticker列表的延迟。美国原生召回也有约6–7分钟长尾，现有现场fresh结果与普通相同，无证据归因缓存；上游索引出现时点与首次发布时间之间差异不能靠改客户端等待消除。普通BODY多在2.5–6.7秒，长尾另含Google解析、排队和多跳；不改变交接和指标归属业务规则。

该修复84项相关回归通过。正式 Enrichment 管线复测上述英国版坏例，取得 FULL、4383字符、5.725秒，仅一次浏览器请求，不再跳转相关文章。发布后首次 canary 曾因 `site_queue_timeout` 在3.214秒延期，未发出网站网络请求；一次稍后复测成功。此延期单列，未把它解释成文章风控，也未据此扩大全局并发。

## InvestorsHub：阶段预算过短，时间戳须区分粒度

普通/fresh MU、NBIS四次列表对照均200、Cloudflare DYNAMIC、Date当前、没有Age，内容集合相同，耗时2.52–5.25秒。未发现列表缓存复用的证据。列表只有 `28 minutes ago` 等相对时间，旧输入包含 DATE 粒度的美东正午占位；不能将该占位当精确发布时间计算真实抓取延迟。现有正文对标题匹配的精确发布时间校验与时效准入逻辑保留。

最近失败记录中，正文先浏览器8秒超时，再走403的公开HTTP。对同一Micron文章做独立Profile对照：备用真实取得 FULL、1602字符，但整次访问10.656秒，网站/浏览器段10.215秒；主身份则确实返回403验证页，并非同一次超时换个名字。由此确认8秒阶段上限会中断正常但较慢的可用路径。

修复只调整该站正文浏览器阶段：生产 Registry revision16→17，`browser_timeout_seconds=12`；seed 同步默认值。现有20秒总预算、重试次数、访问顺序和备用身份不变。Investing/InvestorsHub 的访问验证判定不再重复运行通用正文抽取，保留DOM/安全验证识别与最终正文质量校验，不放宽真实登录墙或Challenge。

正式管线两篇 canary 在配置生效后为 FULL（5.609秒、5.287秒）。最终代码发布后再次真实验证：

| 文章 | 结果 | 字符 | 耗时 | 浏览器请求 |
|---|---|---:|---:|---:|
| Micron Announces Participation in Investor Events | FULL | 1602 | 8.022s | 1 |
| Intel, RadixArk Collaborate to Advance Open AI Inference | FULL | 3502 | 4.825s | 1 |

这证明慢于旧阶段上限的可用请求现在可在同次作业内完成，但没有证明网站网络耗时恒定。主身份此次产生的恢复记录随后自动转为 RESOLVED，最终无待人工Challenge任务；未清Profile、切出口或购买验证码。

## 发布、回归与业务边界

本轮源码提交均已推送 origin/main，使用现有 `storage_release.py` 精确SHA发布，未重建Supervisor。发布清单（UTC，均2026-11-12；北京时间为次日）：

| 提交/发布目录 | 更新角色 | 完成时间 |
|---|---|---|
| 7ae91d476 / barrons-latency-20261113 | Site Access、Collector、Enrichment | 21:00:12 |
| 79f4ca522 / barrons-visible-20261113 | Site Access、Enrichment | 21:09:00 |
| 2b4ace907 / barrons-paid-proof-20261113 | Site Access、Enrichment | 21:14:09 |
| d051fad986 / investing-body-scope-20261113 | Site Access、Enrichment | 21:25:15 |
| 1e940a9a4 / investor-access-latency-20261113 | Site Access | 21:40:45 |

收尾比对运行容器内六个相关源码文件SHA256与Git提交完全一致。Supervisor容器仍为 `9bc69ac8...`（11月10日启动），Cookie/Profile和出口保留；recheck仍为false。未将历史坏例补发到Bus、未重排旧失败作业、未改变业务时效窗口、订阅或轮询频率。当前实际列表周期：Barron's20秒，Investing/InvestorsHub30秒。

最终相关组合59项正文/验证回归、21项ticker列表测试、32项维护/访问判定测试通过；组合有重叠，不累加为独立用例总数。广扫市场源测试另外发现已退役的 `globenewswire_search` 仍被旧测试要求默认启用，出现默认清单断言/KeyError；本轮未为让旧测试变绿而恢复该源。前述Yahoo锚点失败也未隐去。

最终发布前后26分钟逻辑采集回执分别有 Barron's1529、Investing492、InvestorsHub494个 CAPTURED，但混有本轮重启导致的 `site_access_unavailable`、队列延期及少量失败，不能当纯稳态成功率，也不能把共享capture复用当独立网站访问。最终发布结束后的短窗（21:40:46–21:41:58 UTC）分别71/24/23个逻辑CAPTURED，尚在执行2/0/1个，其余无失败；十个ticker均有当期采集完成记录。这只是收尾可用性证据，不是长期SLO验收。

21:14:09–21:40:41 UTC，Barron's和Investing均无自然新增文章；InvestorsHub有一条自然FULL及一条修复前UNAVAILABLE。因此，**本轮已修通失败文章的正式正文链，但尚未得到同口径的新文章总体成功率、发布时间→Bus和发现→Bus平均耗时**。3.194秒BODY均值不能替代用户24.76秒的端到端均值；旧失败样本未重写，短期历史统计不会立即变好。

### 仍存在的问题和后续判断标准

- 上游ticker索引/国家版/Google RSS交接带来的分钟级发现长尾，未通过本轮客户端缓存对照消除。没有精确的历史索引出现快照时，不能把剩余长尾全归为上游，也不能承诺更换CDP就能解决。若自然新增样本持续显示本地漏抓，应按原始文章URL、精确时间戳和对应列表capture继续核对。
- 有效浏览器路径仍可能发生排队、网络波动或新的验证；本轮将其与错误正文识别、固定阶段超时分开，未宣称全部外部失败消失。InvestorsHub当前不需要人工操作，后续新阻断仍按既有恢复机制处理。
- 下一次效果评估必须使用新版本自然新增文章，分别看准确发布时间→首次capture、首次发现→consumable、BODY实际工作耗时及FULL比例；排除发布时间占位，单列Google交接、发布窗口和队列延期。缺少新样本时继续保留“尚未证实”，不增加重复采集或启用recheck来制造验收结果。

证据目录：`eval/source_latency_body_20261113/`。归档摘要不含正文、Cookie、管理令牌或网站凭据。
