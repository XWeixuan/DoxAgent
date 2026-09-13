# DoxAgent V2 正文补全：Badcase 诊断与整体优化开发方案

日期：2026-09-12  
状态：诊断与开发方案，尚未实施业务修复；供评估后进入开发。  
代码基线：`ed82234669408c45ccea13931a07cfec82e67a56`。  
范围：V2 全局 Content Enrichment Hub 的正文获取、候选选择、来源追踪、访问状态、合法账号复用、Session 生命周期、必要的运行边界与验收。覆盖用户九类思路之外的全部样本失败类别。  
本轮实际操作：读取输入与代码、61 条公开 URL 的本地探测、8 项确定性代码复现、浏览器辅助观察、形成本文与证据。未修改应用代码、业务 DB、账号或远端部署。

## 1. 建议结论

保留现有 V2 耐久队列和单一全局 worker，升级其正文提取内核为“有证据的候选获取与选择”，并接入独立的 Publisher 账号身份管理。不要重写整个 Message Bus，也不要只通过增加 Playwright 或降低字数门槛提高 success 计数。

本次发现不仅有漏抓，也有错抓：Finnhub 一条 Smartkem 新闻跳到 Benzinga 股票报价页，现有提取器把 1,354 字行情与公司介绍判作全文。因此第一优先级必须同时包括文章身份与正文完整性；否则扩展抓取能力会放大错误内容进入 Runtime 的风险。

开发按三个工作方向在同一轮推进：

1. 公开正文：候选抽取、Continue Reading、原文追踪、动态页面、媒体混排、访问异常。
2. 账号正文：按 Publisher 实验并支持已授权 Session 的 HTTP、站点数据请求及浏览器访问。
3. 共同基础：状态证据、账号生命周期、队列预算、去重边界与真实回放验收。

先完成共同结果契约与预算约束，然后分别实现公开站点与认证站点适配；不以“所有公开站点完成”为会员能力的启动条件。各站点可以独立启用、降级和回滚。

## 2. 输入、统计与证据边界

### 2.1 测试集核对

- CSV：`exports/doxagent_finnhub_body_incomplete_1000_20260911.csv`，1,000 条、1,000 个唯一消息 ID、1,000 个原始 URL。
- XLSX：用户 Downloads 下 `doxagent_badcase_网站_failure_reason_sample_url_10条.xlsx`，包含网站分组、失败矩阵、缺失 resolved_url 分组及 1,000 条明细。
- 已验证 XLSX 明细与 CSV 的消息 ID、resolved_url、failure_reason 完全一致。
- 新闻发布时间范围为 2026-06-20 至 2026-09-09；这不是当前所有新闻的无偏样本。
- 777 条为 `failed`；223 条为 `not_recorded`，后者是 203 条 `not_attempted_or_no_result` 与 20 条 `empty_body`。
- `source_name=Yahoo` 有 853 条，但真正 `resolved_url` 指向 finance.yahoo.com 的只有 623 条。不能把 source_name 当最终 Publisher 域名。
- 46 个有 resolved_url 的域名／原因分组，另有 8 个无 resolved_url 的来源／原因分组；共 54 组。

| 历史 failure_reason | 数量 | 占 1000 条 |
| --- | ---: | ---: |
| source_summary_only | 376 | 37.6% |
| not_attempted_or_no_result | 203 | 20.3% |
| timeout | 112 | 11.2% |
| empty_extract | 100 | 10.0% |
| http_403 | 80 | 8.0% |
| unsupported_media | 44 | 4.4% |
| http_429 | 24 | 2.4% |
| incomplete_extract | 20 | 2.0% |
| empty_body | 20 | 2.0% |
| http_502 | 10 | 1.0% |
| poison_or_navigation_extract | 6 | 0.6% |
| http_401 | 3 | 0.3% |
| http_404 | 1 | 0.1% |
| http_500 | 1 | 0.1% |

### 2.2 本轮实际验证范围

本地调用当前 `extract_media_record`，使用生产同款 curl_cffi、trafilatura 和 reader fallback；选取用户重点样本并补足每个分组至少一条，共 61 条。54/54 组均覆盖，逐组结果见附录。

当前程序判为成功 29 条，失败 32 条；失败标签为：403 8 条、incomplete 7 条、empty 6 条、summary 5 条、media 4 条、401 2 条。**29 条仅表示现有程序接受了候选，不代表人工核准的完整正文，其中至少一条已确认为错误文章类型。**这些结果不是优化收益，也不能据此推算整体恢复率。

本轮对有 resolved_url 的记录从该 URL 开始，缺失时从原始 URL 开始。CSV 只有旧正文字符数，没有旧正文文本，因此探测输入 body 为空；这会影响“相对旧正文增长两倍”的判定。本轮不是 V2 Hub 的 1,000 条端到端回放，也不是 V1 事故时刻的网络复现。

所有历史行只记录终态，缺少当时 HTML、完整 attempt 链、运行版本及身份状态。故：

- 代码缺陷可用当前代码与确定性复现确认。
- 当前站点障碍可用本轮响应确认。
- 过去某条为什么失败，若缺少历史证据，必须保留“历史原因未证实”。
- 一个分组只探测代表条目，不能将该条根因自动复制到组内所有记录。

### 2.3 证据文件

证据目录：`exports/body_enrichment_audit_20260912/`。

- `dataset_summary.json`：输入哈希、统计与 XLSX 对照。
- `live_probe.json`：61 条逐条结果、请求与响应状态、候选长度、响应哈希、文件名。
- `coverage.json`：54 组总体数量及对应实测消息 ID。
- `deterministic_repros.json`：8 项不依赖真实站点的复现结果。
- `probe.py`：可复用的只读诊断脚本，不连接任何业务库。
- `std_*_*.txt`：本地公开响应快照，仅用于诊断；包含第三方页面内容，不能作为可信指令或直接作为全文发布。

## 3. 当前 V2 真实链路及不能沿用的旧假设

当前代码链路：

```text
Message Bus producer
  → enqueue_enrichment（provider identity + raw_hash 幂等）
  → SQLite content_enrichment_jobs
  → ContentEnrichmentHub
  → SharedContentExtractor（长期 session、共享域名 controller）
  → monitoring.media_enrichment.extract_media_record（旧内核）
  → body 成功替换／原 body 或 summary fallback
  → accept_message → Raw / Standard / Stream
  → delete_enrichment_job
```

已存在的能力：Finnhub 跳转、curl_cffi 浏览器 TLS 模拟、JSON-LD、trafilatura、HTML fallback、部分域名 Jina reader、共享域名限流、全局 8 并发、短暂网络错误一次 retry、失败保留原始内容。V2 镜像也已安装 Chromium；缺的是正文链路的浏览器与身份集成，不是单纯缺依赖。

当前没有：正文级 Publisher 追踪、候选之间的质量择优、浏览器展开动作、账号仓库、持久登录身份、权限证据、Session 恢复状态机。

`message_bus_v2_content_enrichment_repair_plan_20260911.md` 是重要背景，但部分描述已与当前代码不一致：

- `deadline_at` 当前主要限制追加 retry；claim 后没有“已超时则禁止网络”的前置判断，也没有整条 pipeline 的剩余时间预算。
- 现有过期测试明确断言 `extractor.calls == 1`，只证明不再追加 retry，不能证明超时后零网络。
- 当前 finalization 是 `accept_message()` 后单独删除 queue 行，并非一个总事务。已有重复 Raw 的恢复路径，不应将其描述成跨整个流程 exactly-once。
- intake 已加入 provider raw_hash；正常 poll 会跳过已见 provider payload。不能指望升级 extractor 或恢复账号后，相同旧消息自然重新补全。

以当前代码为开发基线。本轮没有读取远端容器与开关，因此不作远端实际版本和启用状态的结论。

## 4. 已确认问题、样本证据和对应修复

### 4.1 Continue Reading 至少有三种语义

1. 同页正文早已在 HTML 中，只是视觉折叠。
2. 同页交互后加载正文。
3. 跳到外部 Publisher 阅读。

Yahoo Micron 样本 `.../micron-stock-has-reclaimed.html` 的 HTML 与浏览器都确认：Continue Reading 是指向 Barron's `/articles/micron-stock-price-memory-8d770124?siteid=yhoof2` 的外部链接，不是同页展开。本轮直连抽取只有 595 字且包含重复摘要，代码直接归为 summary。原站浏览器当前遇到 DataDome 设备验证，尚未验证账号或订阅状态。

另外几条用户归为 Continue Reading／媒体混排的 Yahoo 页面，本次 HTTP HTML 已能取出正常文字，现有 trafilatura 直接成功。因此不能为整个类别固定“点击按钮”的解决方案。

实现：先识别按钮／链接的作用与正文候选，再决定解析隐藏内容、同页展开还是 Publisher 追踪；保存 expansion 的前后段落数、正文哈希与是否获得文章结尾，不能仅以点击成功作为补全成功。

### 4.2 摘要标签过早结束，而且混入真正付费内容

`media_enrichment.py:1050` 对 Yahoo/TheStreet 按短文本质量直接返回 `source_summary_only`；`:1068` 的 reader fallback 允许列表不包含 summary 或 continue-reading 原因。

Yahoo Intel Market Chatter 样本本次正文中明确出现“升级阅读 MT Newswires”和 Silver/Gold 订阅要求，仍被判成 summary。它证明“Yahoo 摘要都应去原站解决”也不成立：可能是 Yahoo 自己承载的授权 Premium 内容。

实现：summary 只是当前候选的内容状态，不能终结整个路由。先检查站点内访问权益、原文链接和展开证据，分别进入 Publisher、账号或动态读取策略。只有所有符合条件的下一步都耗尽后才最终 fallback。

### 4.3 并非任何视频都被代码直接拦截；但 URL 型媒体门禁确实错误

当前 `_is_unsupported_media_url` 只检查 `/video/`、结尾 `/video` 和 jwplayer 域名；没有“HTML 出现视频就失败”的通用判断。用户列出的 Micron/Sandisk、Dell、Michael Burry 三条混排样本，CSV 历史标签实际均为 `source_summary_only`，不是 unsupported_media；本次三条均被旧提取器接受。

但 `_candidate_failure_reason` 在检查正文前就根据媒体 URL 拒绝。确定性复现证明：给 CNBC `/video/` 页面提供 1,080 字有效正文，也仍返回 unsupported_media。

实现：媒体类型与文本质量分开。只有“媒体主体 + 无可用文章／逐字稿文本”才 media_only；视频页面若有完整逐字稿可标记 transcript 并按独立内容类型接受，短播放器简介不能冒充新闻全文。不在本轮默认引入音视频转写。

### 4.4 第一份非空候选抢占后续正文

`_extract_article_content` 先返回第一份 JSON-LD articleBody，再返回第一份非空 trafilatura，再尝试 HTML。它没有在各候选间比较身份、完整性和结构。

确定性复现：JSON-LD 只有 16 字，而替代 extractor 提供 1,080 字，仍选择 16 字并终止候选探索。真实网页还可能有多个 NewsArticle、推荐文章 JSON-LD 或过时元数据。

实现：独立收集候选、逐个判定，再选择最佳可信候选。JSON-LD 必须关联当前 canonical/headline/mainEntity，不能因为有 articleBody 就认定是目标全文。站点文章容器、结构化正文、trafilatura 都是候选；不能简单选最长文本。

### 4.5 reader 清洗规则既丢正文，也吞掉诊断信息

已复现的规则缺陷：

- 遇到 Continue Reading／Yahoo 分隔线就停止，后面真实正文不会读取。
- Yahoo 只要一整行包含 `coinbase` 就删除，会丢掉真实 Coinbase 新闻段落。
- 全文关键词匹配把合法文本中的“read more”“captcha”等视为障碍，缺少正文节点与页面状态区分。
- Yahoo reader 起点过度依赖上游标题匹配，网页改标题、编码损坏会导致返回空。
- reader 本身 HTTP 200，但内部是验证码或拒绝页时，告警前缀会被正文清洗丢掉。

TheStreet 两条重点样本本次均是 direct 403 → reader 200 + CAPTCHA warning + 空 Markdown；最终 empty_extract。Seeking Alpha 样本是 direct 403 → reader 200 + Press & Hold 人机验证 → incomplete_extract。它们不能据此认定“提取器不会识别文章节点”或“账号没买 Premium”。

实现：先解析 reader 的响应封装、警告、实际来源 URL 和页面类型，再进行正文清洗。以文章容器边界替代全局词黑名单；广告／推荐节点剔除，不按公司名称删除文字。

### 4.6 完整性判定既误杀短全文，也放过长摘要／付费截断

现有主要阈值为 800 字符、4 句；部分路径接受 600 字符且相对原文增长两倍。短快讯天然无法满足。`_is_acceptable_enrichment` 的第二分支仅排除 html_or_entity_body，未排除 truncated_or_paywall_marker。

确定性复现：1,080 字加 Subscribe to continue，在 Seeking Alpha URL 下仍被 `_candidate_failure_reason` 接受。反过来，完整短快讯可能因为短而被拒绝。

实现：采用“文章身份 + 结构证据 + 完整性证据”，长度只作辅助。若已确认处于订阅截断或展开未完成状态，即使很长也不能 FULL。短讯若有对应 article ID、明确正文节点和结尾，允许 SHORT_FULL。找不到结尾且只有字数优势的候选保留 PARTIAL／UNKNOWN，不覆盖可信全文。

### 4.7 错误目标 URL、行情页和导航页属于独立问题

实际样本 `std_84aa0e4ddc8c4ac99f309d9416233015`：Finnhub 302 到 `https://www.benzinga.com/quote/SMTK`，提取出来的是报价、EPS 解释、公司概况与 ticker 列表。当前代码接受 1,354 字为全文。

另一类已观察输入是 `investorshub.advfn.com/market-news` 列表页，而不是文章。`_titles_do_not_dominate` 仅排除标题重复，目标标题完全没有出现也可通过；它不是文章匹配验证。

同时 `_external_url_from_html` 会选择第一个外部 href，甚至任意外部 URL。确定性复现中，播放器 URL 排在文章 URL 前就被选中。不能断言历史 jwplayer 样本全部由此产生，但机制本身成立。

实现：增加 article identity 与 page_kind；对 quote/listing/search/player/navigation 单独分类。追踪链接按站点语义与同篇文章证据排序，不能选第一条外链。已失去文章入口时，保留 headline-only／原 body；仅在同一 Publisher 官方页面存在可验证文章映射时做有限恢复，不以相近标题的另一篇新闻替换。

### 4.8 JavaScript／应用壳确实存在，不能都记 empty_extract

Moby 样本本次 HTTP 200，返回 Flutter web 启动壳，JSON-LD、trafilatura、HTML 候选全部为空；浏览器即时 AX 也没有给出文章正文。能确认“收到应用壳”，还不能确认账号权限或最终加载是否成功。

实现：页面壳标为 render_required，进入有预算的浏览器渲染；根据已观察到的本站正文请求建立可测试的 HTTP adapter。无法观察正文／仅出现登录提示时保留具体状态。不能凭路径猜内部 API，也不预设 Chromium 一定能成功。

### 4.9 HTTP、reader、跳转的失败被压成一个终态

`extract_media_record` 在 reader 失败后覆盖 reason/method，但 `reader_result.http_status or direct_status` 可能留下前一次状态；因此“timeout + 429”完全可能来自两次不同请求。历史 Yahoo timeout 的 91 条中，84 条同时记录 http_status=429，不能全部按慢服务器处理。

本轮两条历史 Finnhub 502 已成功跳转，下游分别遇到 403 和摘要判定。当前已支持最多 5 次 Finnhub 跳转；502 标签本身不证明跳转算法不足。

成功的 Finnhub 跳转目前也没有累积到 `MediaExtractionResult.attempts`；本轮诊断包装器才捕获到了这些响应。普通正文请求采用自动重定向，中间跳转不可见，最终 Publisher 也可能未按自己的 limiter 获得许可。

实现：记录每一跳、真正返回错误的端点与 provider；失败原因引用同一个 attempt，不能拼接不同阶段的 reason/status。保留 direct challenge 和 reader failure 两层事实。reader 既占原站预算，也占 r.jina.ai 自己的配额。429 支持数值和 HTTP-date 两种 Retry-After；在全局域名／reader cooldown 内不继续冲击。

另一个重试偏差：当前 transient_hint 会将通用 `access denied` 等关键词也视为临时错误，Hub 又对任意历史 attempt 的 transient_hint 返回可重试。因此“前一步限流、后一步已确认稳定失败”也可能整体再跑。新策略必须依据当前尚可恢复的终止状态决定 retry，不能仅因任意前序步骤曾瞬态失败就重试整条链路。

### 4.10 没有尝试记录，不等于正文无法获取

223 条缺失补全记录可能涉及历史选择条件、限额、未入队、旧正文质量判断、进程中断或字段记录缺失。CSV 无法区分，不能追认单一根因。

本轮这些类别的代表样本有成功、订阅摘要、验证码和纯视频等多种结果。其中一个 empty_body 样本还暴露了上述 quote 页假成功。

实现：V2 入队、跳过、排队、执行、截止、失败、成功必须可区分；正文为空允许进入队列，但 headline-only 仍是合法的局部 fallback。对旧记录标记 NOT_RECORDED，禁止伪造 attempted 或将标题拷贝成全文。用 V2 隔离队列回放验证流转，而不是推断 V1 历史入队机制。

## 5. 覆盖全部历史类别的处理决策

| 类别 | 开发处理与停止条件 |
| --- | --- |
| source_summary_only | 候选失败后继续站点内展开／结构化数据／可信原文／权限路线；穷尽后 summary_only，并记录原站是否已找到 |
| not_attempted_or_no_result | 保留历史 unknown；验证 V2 intake/skip/queue/finish 覆盖；不能把未尝试数计作 extractor 失败 |
| timeout | 明确 connect/read/render/limiter_wait 阶段，按剩余预算取消，瞬态一次 retry；不无限加长超时 |
| empty_extract | 按 response 挑战页、JS 壳、候选解析失败、真正无文本分流；未知保留 unknown，不误报权限 |
| http_403 | 区分 challenge、geo、明确授权拒绝和 unknown_403；仅有证据的瞬态一次 retry；已有身份可用时按站点策略访问 |
| unsupported_media | 验证是否真无文章／逐字稿；有文字则独立评估；纯媒体终止文本补全 |
| http_429 | 分清原站与 reader 配额，执行共享 cooldown 和 Retry-After；超 deadline 则 fallback |
| incomplete_extract | 比较候选、验证标题／article ID、处理短快讯、截断／付费、错误页面和动态加载；禁止仅降字数阈值 |
| empty_body | 输入空正文不阻塞；尝试目标文章；原始消息确为标题快讯时 headline_only，不能抓 quote 页填充 |
| http_502 | 标明 redirect 或 publisher 或 reader 端点，有限网络重试；若已有经过验证的映射可使用，不猜目标 |
| poison_or_navigation_extract | 响应级访问检测与文章级 DOM 清洗分离；匹配正确文章后选候选；列表页／推荐页不冒充文章 |
| http_401 | 只证明未授权响应；先识别账号／Session 状态，再判 subscription；无证据不直接推断“没会员” |
| http_404 | 记录本次真实 missing；尝试站点明确迁移／canonical／原文映射；无证据则 unavailable，不替换相似文章 |
| http_500 | 按具体端点执行一次瞬态重试；失败后 fallback 并保留技术原因 |

样本之外的 timeout 细分、DNS/TLS、redirect loop、错误 MIME、响应过大、解析异常也在通用 transport/schema 中有出口；单条异常不得阻断其余内容任务。编码修复须使用响应编码与可验证字节，不做不可逆的猜测替换。

## 6. 目标提取流程

```text
durable job + policy snapshot + deadline
  → URL 与文章身份线索
  → 公共 HTTP（逐跳记录、限制大小与时间）
  → 访问／页面类型证据
  → 多候选抽取与同篇文章验证
       ├─ FULL / SHORT_FULL → 接受
       ├─ 本页展开 → hidden DOM / 站点数据 / 有限浏览器
       ├─ 转载摘要 → 可信 Publisher → 回到访问与候选判定
       ├─ 登录／订阅 → 已授权账号访问 → 回到候选判定
       ├─ render_required → 浏览器／已验证站点数据 adapter
       ├─ 公开页面仍失败 → 允许站点的 reader fallback
       └─ 无合法下一步／预算耗尽 → 原正文或 summary fallback
```

该图是路由图，不是每条消息必须顺序执行的长瀑布。Publisher adapter 决定适用策略；例如已知 Yahoo Premium 直接检查 Yahoo 账号能力，不先浪费一次 reader。

### 6.1 三个小接口

- `Transport.fetch(request, budget, identity_ref?) -> FetchObservation`：状态、实际 URL、重定向、响应类型、耗时、大小、访问提示，正文内容不承担最终决策。
- `PublisherAdapter.inspect/extract/next_actions(observation, article_identity) -> candidates + evidence + actions`：只实现站点确定性逻辑，不操作队列或业务 DB。
- `CandidateEvaluator.evaluate(candidate, identity, access_evidence) -> quality + decision`：统一防止摘要、行情页、导航、截断误入 FULL。

V2 的 `SharedContentExtractor.extract(record)` 保持调用接口，可在内部接新 pipeline。通用抓取与解析助手复用；不要复制整个 1,400 多行 legacy 文件。新行为默认只从 V2 路径启用；若共享函数要变更，用明确 policy 参数或新增函数，保持 V1 默认语义。

### 6.2 Publisher 链与文章身份

候选原文证据按可靠性排序：明确“阅读原文”的文章链接／结构化 original source → 同篇 canonical／版权来源数据 → 经过站点规则验证的链接。canonical 指向本站或多个转载页时不盲跳。

每次跳转验证 URL 类型、headline 相似性、article ID、发布时间与来源关系。headline 改写允许合理差异，但实体、时间和 article ID 冲突不能放行。追踪不靠 LLM 搜索猜文。

建议初始预算：最多 5 次 HTTP 重定向、最多 2 次 Publisher 跨站追踪、每个策略在同 job 内一次；visited URL 去环。精确阈值由 fixture 与现网样本校准。

每一跳只允许 HTTP(S) 公共地址，限制响应大小，拒绝本地／私网／云 metadata 地址及跨域凭据转发。这是原文追踪引入的具体防护要求，不是新增业务阻塞门禁；失败仅影响该条正文。

区分 `input_url`、`resolved_article_url`、`body_source_url`、`canonical_url`、`origin_publisher` 与 `source_name`。业务 source/publisher 兼容值保留，真实承载全文的 Publisher 写独立字段。保留原始 provider payload 和 identity，防止跨站追踪把同一 Finnhub 消息变成另一身份。

## 7. 账号、会员与 Session 生命周期

### 7.1 本轮要实现的能力与实验边界

账号能力必须包含首次登录后的复用、服务重启恢复、失效识别、有限自动恢复和人工恢复出口；不能只有一个 cookie 文件参数。

本轮没有获得任何站点会员账号，也没有尝试读取个人浏览器凭据。Seeking Alpha／Barron's／WSJ 的付费正文成功率、Google OAuth 自动恢复率和 Session 有效期均未验证。工程方案可以确定，站点最优路径必须在已授权真实账号条件下比较。

优先实验矩阵：

| Publisher / 权益 | 首选实验 | 备选 | 本轮已知／待证实 |
| --- | --- | --- | --- |
| Yahoo Finance Premium / MT Newswires | Yahoo 已授权身份的文章响应或页面已观察的正文请求 | 保留 Yahoo profile 的浏览器 | 已确认样本要求 Silver/Gold；具体会员账号是否覆盖待验证 |
| Barron's | 独立 Dow Jones 身份，比较 HTTP 与浏览器可读结果 | 同身份浏览器 | 原文链接已确认；本次先遇设备验证，不能提前判订阅 |
| WSJ / MarketWatch | 同样分站确认账号状态与实际文章 entitlement | 浏览器 | 本次 401；不能假设一份 Dow Jones 账号自动覆盖全部产品 |
| Seeking Alpha | 正常已授权身份的页面／文章数据请求 | 浏览器文章读取 | 本次先遇人机验证；登录、Premium、Pro 权益必须分别验证 |
| TheStreet / Pro | 公开页先做访问与解析；Pro 单独确认权益 | 站点 profile | 公开样本也受 challenge，不能一律归订阅 |

其他 Publisher 复用通用账号接口，未经站点验证不宣称认证支持。

### 7.2 运行组织

建议为正文功能配置独立 `PublisherIdentityManager`，由同一 enrichment 服务中的独立组件管理少量账号状态和按需浏览器。首版无需引入新调度平台。

- 每个 Publisher／账号隔离身份引用、cookie jar 与 profile；只保存账号标识及状态到普通 metadata，凭据存单独受限挂载目录。
- 公共请求使用匿名 session，认证请求使用对应账号 session。会员 cookie／token 不发给 Jina 或其他抓取服务。
- profile 在远端持久卷保存，写入原子化、版本化；进程重启重载。一个 profile 同时只有一个浏览器所有者，恢复时采用单飞锁。
- 浏览器进程可复用，页面按需创建；首版建议全局最多 2 个活动页面、每个账号最多 1 个恢复动作。这是起始上限，需根据目标服务器内存实测调整。
- 不每篇启动浏览器，也不定期强行刷新所有账号。成功文章响应即可更新最近验证时间，疑似失效才触发受限检查。
- 初次人工登录使用服务器同一独立 profile；管理入口限定已有受控访问通道。日志、截图和网页快照必须排除 cookie/token/密码与完整会员正文。

### 7.3 HTTP 与浏览器共享身份的选择

对每站按成本低到高比较：

1. 授权 cookie 的轻量 HTTP。
2. 浏览器 `context.request` 或等效站点 HTTP client。
3. 已在实际页面中观察并验证的正文 XHR/GraphQL 请求。
4. 同 profile 的浏览器页面读取／展开。

Playwright 官方确认 `context.request` 与对应 BrowserContext 共享 cookie jar；这有利于首次实现正确的 cookie 更新。独立 HTTP client 需要明确同步 Set-Cookie、domain/path/secure/expiry 以及 token 刷新，不能把所有 cookie 拼成一个全域请求头。[APIRequestContext 官方文档](https://playwright.dev/python/docs/api/class-apirequestcontext)

保存 storage_state 不等于保存所有浏览器身份状态；需要逐站验证 localStorage、IndexedDB、sessionStorage 和跨站身份依赖。长期 profile 也不能保证服务端不撤销登录。[认证状态文档](https://playwright.dev/python/docs/auth)

持久 context 使用独立 user_data_dir，不能并行启动多个实例共享同一目录，也不使用用户日常浏览器主 profile。[Persistent context 官方文档](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)

HTTP 重放若持续被站点拒绝，而正常已授权浏览器可以阅读，则将该站标记 browser_required。保留 HTTP 优先原则，但不把 cookie 导出当成跨站通用解法。

### 7.4 认证状态与文章访问权限分开

账号状态建议：`UNCONFIGURED / UNVERIFIED / VALID / SUSPECT / RECOVERING / REAUTH_REQUIRED / DISABLED`。

文章权限建议：`PUBLIC / LOGIN_REQUIRED / SUBSCRIPTION_REQUIRED / ENTITLED / ENTITLEMENT_MISSING / UNKNOWN`。

原因：用户可以已登录但没有当前文章权益；403 challenge 也不代表 Session 失效；新进程刚载入未验证的 cookie 不能直接称 VALID。

状态迁移：

1. 成功获取身份明确的目标全文 → VALID，更新验证时间。
2. 观察到登录重定向／明确 session 失效响应 → SUSPECT。
3. 该账号单飞执行一次站点正常刷新／重新打开授权页面 → RECOVERING。
4. 若第三方身份仍有效，可执行该站明确且已验证的正常重新授权流程；成功后重新读取原文章验证。
5. 密码、MFA、CAPTCHA、设备验证或授权页面结构变化 → REAUTH_REQUIRED，停止自动恢复循环。
6. 身份验证成功但文章明确要求升级套餐 → ENTITLEMENT_MISSING，不再次登录。
7. 人工恢复后提高 session revision 并验证目标文章，再恢复该站后续作业。

普通 403、偶发 timeout 不足以把账号标为失效。验证页本身不可达时标记检查未完成，保留上次成功证据。Google 登录有风险控制和额外验证，不能承诺“Google 仍登录就必定无交互恢复”。

### 7.5 与 180 秒正文 deadline 的关系

身份维护可以独立继续，但不能占住一条新闻无限等待。

- 剩余时间足够：等待一次有界恢复，成功后继续该 job。
- 需人工或超过预算：该 job 立即以原文／摘要最终化，写明 next_action；账号问题不阻塞其他站点。
- 恢复后后续新 job 正常使用新身份。此前已最终化消息不通过普通 poll 自动重跑；历史补全必须走显式选择的修复入口，并另行验证 Raw／Standard revision 语义。
- 本轮核心验收是新进入 V2 的消息与隔离回放；不回写用户 V1 数据库，不自动重新触发已处理历史 Runtime。

## 8. 结果契约与兼容设计

继续输出已有 `media_enrichment.status/succeeded/reason/method/http_status/attempts`；新增可选字段，不要求旧数据回填为新状态。

建议扩展结构：

```text
pipeline_version / publisher_adapter_version / policy_version
outcome: FULL | SHORT_FULL | PARTIAL | UNAVAILABLE | SKIPPED
stage: intake | resolve | fetch | access | render | extract | validate | finalize
reason_code                 # 兼容 reason 的具体来源
failure_attempt_id          # reason 与 status 指向同一 attempt
page_kind: article | listing | quote | media | challenge | app_shell | unknown
access_requirement / entitlement_state
identity_match: confirmed | supported | mismatch | unknown
input_url / resolved_article_url / body_source_url / origin_publisher
source_chain[]              # URL、来源依据、验证结论；限制长度
candidate_summary[]         # method、长度、正文 hash、质量证据；不堆全文
credential_ref / session_revision / auth_state  # 无凭据值
next_action / retryable / budget_exhausted
```

每个 attempt 至少记录 phase、transport/provider、请求端点、最终 URL、状态码、访问提示、耗时、响应字节数、是否重试与原因；时间消耗包含 limiter 等待，不只包括 network get。

reason 示例：`publisher_link_missing`、`publisher_identity_mismatch`、`expand_failed`、`login_required`、`subscription_required`、`session_expired`、`entitlement_missing`、`reauth_required`、`challenge_required`、`render_required`、`non_article_target`、`extractor_failed`、`deadline_exceeded`。unknown 始终有明确出口。

这些是字段语义，不是层层升级的 workflow 硬门禁。只有 FULL/SHORT_FULL 可以按既有规则替换正文；失败保留原 body→summary→空串。PARTIAL 候选默认只记质量摘要，不贸然覆盖已有更可靠正文。title 与 body 同时为空沿用 Raw-only。

聚合统计可以按阶段和 reason 展示，但不新增重复审计实体或让 LLM 正文混入错误日志。重要诊断保留在现有 completion metadata；敏感会员内容仅进入既有获授权正文存储，不进入通用调试工件。

## 9. 运行预算、调度和恢复边界

新增浏览器与多跳会放大现有运行边界缺口，因此必须纳入本轮：

1. claim 前／网络前检查绝对 deadline；将剩余时间传入每一跳、limiter 等待、reader、browser、账号等待。当前 180 秒作为总预算，不为新增策略静默放宽。
2. 一次 pipeline attempt 与一次 HTTP request 分开计数。最多 initial + 一次 transient retry；内部重定向／原文追踪／浏览器动作有单独上限，不能每层各自重试两遍。
3. 429 冷却同时覆盖 domain 与实际 reader provider；等待通过 not_before 留在队列，不耗尽所有 worker slot。
4. 当前批次 gather 会等最慢任务后再 claim 下一批；新增浏览器后应改为至多 8 个活动 job 的持续补位，避免慢站阻塞整个批次。按可执行域名取任务，减少同一域名等待者占满所有名额。
5. 当前 lease 为 60 秒，小于可能的整条 pipeline。采用有所有权校验的 lease renewal，或保证工作超时小于 lease；选择 heartbeat renewal 并在 finalization/requeue 校验 claim token，防止失去租约的旧 worker 回写。
6. 基础设施写入错误沿用当前显式失败和可恢复队列；网络、站点、账号、解析问题保持 item-local。不能把 DB 错误吞成网站失败。
7. 保留现有 Raw 幂等／处理中断恢复，不重新设计全局事务系统；新增定向验证覆盖“Raw 成功后 queue 删除前崩溃”与“浏览器过程中 lease 丢失”。
8. provider raw_hash 去重继续有效。复用同文抓取结果的缓存可放在后续优化；若首版引入，key 必须包含正文 URL／版本与账号权限分区，禁止将登录内容放公共缓存，禁止永久缓存 Session 失效或 challenge。

## 10. Publisher 实施顺序与非重点站点处理

| 站点族 | 首轮工作 | 验收重点 |
| --- | --- | --- |
| Yahoo（623 条已解析） | article DOM、多候选、Continue Reading 分流、Premium 识别、来源链 | FULL 与摘要／付费明确区分；正文混排不误删 |
| TheStreet（23） | direct challenge 与 reader warning 识别、正确正文容器、公开／Pro 区分 | 200 reader CAPTCHA 不再报纯 empty；有正文时不丢段 |
| Seeking Alpha（31） | challenge/login/subscription 分层、授权身份与恢复适配 | 明确区分人机验证、Session 失效和权益不足 |
| Benzinga（17） | JSON-LD 身份选择、quote/listing 拒绝、短讯处理 | SMTK 假全文被拒绝；正常正文保持成功 |
| CNBC（22）与播放器 | media_only 与 transcript、文章＋媒体区分 | 纯视频不制造全文，带逐字稿可正确标注 |
| Motley Fool（12）、Investopedia（8） | 优先复用普通 HTML，补 challenge/rate 与结构清洗 | 本次恢复不能记成新代码收益；正文边界无污染 |
| WSJ（2）、MarketWatch（1）、Barron's 原文链 | 真实账号权益实验；正常认证访问适配 | 每个产品独立确认 entitlement |
| QZ（2） | SSR 摘要／动态正文识别、reader 文章边界与分享控件清理 | 本次 12,807 字候选夹杂分享链接等，不能按长文本直接认可 |
| Moby（2） | Flutter 应用壳、渲染与已观察正文请求实验 | JS 壳、登录和无可用文本分别归因 |
| InvestorsHub（4） | 文章路径与 market-news 列表区分 | 不用列表文章拼成目标全文 |
| Stocktwits 新闻页（5） | 保留 news-articles 的文章能力 | 不能因 Stocktwits 短帖 source 默认 skip 而屏蔽 Finnhub 指向的新闻网页 |
| 其余站点 | 通用候选／身份／transport/reader，必要时小型 adapter | 至少每组代表样本有明确结果；未知不伪装成功 |

其余已覆盖包括 InvestmentMonitor、Axios、247WallSt、BeInCrypto、USA Today、Verdict、Power Technology、Private Banker International、Just Auto、CoinDesk 视频等。共享 challenge 外观只能共用检测器，不能假设不同站点正文布局或会员权益相同。

以上数字按 resolved_url，不包含来源缺失的 223 条；Finnhub 自身尚未解析的 12 条单列为跳转路径，而非 Publisher。

## 11. 可实施的开发工作包

| 工作包 | 主要文件与改动 | 完成判据 |
| --- | --- | --- |
| A：冻结基线／评价口径 | 在 `scripts/` 建 V2 隔离 replay 工具；测试 fixtures 保存公开最小结构、预期正文边界和身份标签 | 保持输入 hash；区分旧 failure、当前 baseline、candidate；可断点续跑且不写业务库 |
| B：候选与状态基础 | `content_enrichment/schema.py`；新增 `pipeline.py`、`quality.py`、`publishers/base.py`；`extractor.py` 接入 | 8 项复现转为有意义的回归；短全文接受、截断与 quote 拒绝；V1 默认行为不变 |
| C：公开站点适配 | `publishers/yahoo.py`、`thestreet.py`、必要的通用结构 adapter；新增受限 `resolver.py` | Yahoo 外链／同页展开／Premium 三分流；来源链无循环与身份串文 |
| D：认证基础与首批站点 | 新增 `identity.py`、`browser.py` 和站点认证适配；`settings.py` 与现有 V2 Compose 挂载 | 授权身份隔离、重启复用、单飞恢复、人工出口；至少一个实际会员站点完成端到端验证 |
| E：运行边界 | `content_enrichment/service.py/cli.py`、`message_bus_v2/repository.py` | 真正 deadline、lease 所有权、单次 retry、共享 cooldown、持续补位；失败不影响其他来源 |
| F：结果与兼容 | 既有 completion metadata；需要时扩展 V2 read DTO，不改 LLM 正文格式 | reason/status 同 attempt；source/identity/raw_hash 稳定；旧记录字段可缺省 |
| G：回归与部署 | `tests/test_content_enrichment_hub.py` + 新 pipeline/adapter 定向测试；部署与回滚说明；`changelog` | 1,000 核心回放、成功对照、账号 lifecycle、服务器小流量验收通过 |

A/B/E 的基本契约先收敛，C 与 D 随后同批进行；D 不等待 C 全站完成。每个工作包增量提交，禁止为迁移而整文件复制／重写旧内核。重要实现改动逐项追加 changelog。

首轮不加入：LLM/Codex SDK 登录代理、付费墙绕过、全网相似文章替换、自动购买订阅、默认所有页面浏览器化、V1 DB 回填、历史 Runtime 自动重放。公开页结构化数据只读取正常授权响应中已有的内容，不把解析当作获取未授权正文的手段。

## 12. 验证设计与验收标准

### 12.1 三层基线，防止虚假收益

1. 历史 CSV 的 777 failed / 223 not_recorded：只作历史分层，不是当前成功率分母的混用标签。
2. 冻结版本的当前 V2 baseline：对全部 1,000 条隔离回放并保存结果；补齐可取得的原 body/summary 或明确 fallback 缺失。
3. 新 pipeline：对同一 fixture、同一时间窗口 live 样本、同一凭据权益分区回放，与 baseline 比较。

对于网络时间差造成的可用性变化，优先使用同一份 HTML/reader 快照比较抽取结果；需要浏览器或权限的能力另做配对 live 验证。相同 URL 的响应 hash 变化必须可见。不能把本轮 29 个旧代码接受结果记为开发带来的恢复。

现有 1,000 条无成功对照，不能衡量新逻辑是否伤害正常新闻；补充约 50 条覆盖站点的已核准成功样本，以及短全文、长摘要、媒体混排、文章＋行情 sidebar、纯视频、改标题、表格正文、付费截断、错误原文链接等 fixture。

### 12.2 必须报告的指标

- 全部 1,000 条：新旧 FULL/SHORT_FULL/PARTIAL/UNAVAILABLE/SKIPPED 转移矩阵。
- 历史 failed 777 条、not_recorded 223 条分别报告，不混成一个 extractor 恢复率。
- 当前 baseline 失败、candidate 经核准成功的数量与分母；未知可获取性单列。
- 公开可读／有配置账号且有权益／无账号／纯媒体或失效 URL 分层恢复率。
- 原文发现率、发现后同篇匹配率、匹配后全文成功率，三个分母分别定义。
- 假全文率：摘要、订阅截断、quote/listing、错篇、推荐内容混入。
- 短文误拒率、媒体文字误拒率、错误标签准确率与 unknown 占比。
- HTTP 请求数、browser 比例、reader 比例、端到端 p50/p95、queue 等待、deadline 耗尽率、各域名 429。
- 账号按状态统计：初始成功、恢复成功、权益不足、人工认证、重启后复用；不展示凭据。

### 12.3 上线门槛

- 所有已确认结构性缺陷 fixture 必须正确；上述 SMTK quote、长付费截断不允许 FULL。
- 人工核准的关键 fixture 与成功对照不得退化；新增恢复条目在验证集中逐条核查，不用“字数超过阈值”充当真值。
- 全 1,000 条有终态或明确未执行原因，不能因局部解析／账号错误丢消息；审阅未知类别并提供下一步动作。
- deadline 到期后零新增网络，retry 上限、lease 丢失防回写、Raw 中断恢复和多域名持续推进通过必要测试。
- 第一批标记支持的每个账号站点都应验证有效登录、cookie 过期、Session 撤销、权益不足、人工验证和服务重启。未完成的站点标记 experimental，不能冒充认证功能已验收。
- 单站若只能浏览器访问，可独立启用 browser_required；不强求所有会员站都 HTTP 化。
- 不提前许诺“整体恢复 80%”等无依据目标。共同接受的公开可读与合法 entitled 样本，应在完成 gold 标注后设定数值目标；发布前至少给出相对 baseline 的经核准净恢复及所有回退项。

只跑与当前改动相关的测试。每完成工作包验证其边界，全部实现后进行一次核心回放及小规模 live 验收；不反复全量网络重试。

## 13. 部署、长期运行与回滚

1. 本地冻结 baseline、实现与公开 fixture 验证；本地账号实验不等于远端身份可复用。
2. 在目标服务器的独立 profile 进行初次授权与少量同站文章验证，测量浏览器内存、账户风险控制、站点响应和重启恢复；再开放该 adapter。
3. 新 pipeline 通过总开关＋站点开关渐进启用。已入队 job 冻结 policy/extractor 版本；升级时旧版本逻辑至少保留到相关队列排空／超时。
4. 保持单一 enrichment worker 服务和全局 8 job 上限；少量浏览器资源单独限制。确认 Compose 实际挂载与权限，不仅验证配置语法。
5. 单站失败率／challenge／429 激增时关闭该站昂贵策略或进入 cooldown；其他站点继续。账号恢复不引发跨站阻断。
6. 回滚关闭新策略，保留旧 extractor 路径与 additive metadata 兼容。已发布内容不因回滚自动重写。若发现错误全文已进入业务链，必须列出受影响消息并走显式内容修复，不靠重复抓取掩盖。

## 14. 进入开发前应明确的外部输入

公开页修复、状态契约和回放工具不依赖账号，可以直接开发。认证站点正式验收需要：首批 Publisher 清单、每个站点实际套餐权益、认证方式和可用测试账号；凭据应走安全交接，不放入方案、CSV、日志或普通聊天文本。

建议首批选择 Yahoo Premium（验证已发现的 MT Newswires 场景）与一个真正原始 Publisher；Seeking Alpha 或 Barron's/WSJ 由已拥有的权益决定。不能仅为覆盖样本就假设用户已买了全部订阅。

当前仍待证实：各站 Session 可重放程度、Google OAuth 自动恢复条件、历史失败时的网络／程序状态、Moby 最终正文加载方式、其余每条 badcase 的 gold 全文。本方案为这些问题给出实验与停止条件，而不是虚构已经解决。

## 15. 代码证据索引

| 位置 | 证据 |
| --- | --- |
| `src/doxagent/monitoring/media_enrichment.py:291` | 字数、句数与截断关键词判定 |
| `src/doxagent/monitoring/media_enrichment.py:418` | direct→reader 结果覆盖与终态 |
| `src/doxagent/monitoring/media_enrichment.py:569` | domain controller 与 pacing |
| `src/doxagent/monitoring/media_enrichment.py:600` | 自动重定向 fetch；reader throttle_target |
| `src/doxagent/monitoring/media_enrichment.py:660` | Retry-After 仅 float |
| `src/doxagent/monitoring/media_enrichment.py:743` | Finnhub 特例解析与成功跳转证据未返回 |
| `src/doxagent/monitoring/media_enrichment.py:826` | 首个非空候选直接胜出 |
| `src/doxagent/monitoring/media_enrichment.py:917` | reader 起点、结束和清洗规则 |
| `src/doxagent/monitoring/media_enrichment.py:1012` | 媒体 URL 提前拒绝与后续质量判定 |
| `src/doxagent/monitoring/media_enrichment.py:1050` | source_summary_only 与 fallback 允许列表 |
| `src/doxagent/monitoring/media_enrichment.py:1085` | 首个外链／任意 URL 选择 |
| `src/doxagent/monitoring/media_enrichment.py:1266` | 标题不占多数并不等价于文章匹配 |
| `src/doxagent/monitoring/media_enrichment.py:1330` | 长截断文本可能通过接受分支 |
| `src/doxagent/content_enrichment/extractor.py:21` | V2 仍复用旧内核，session 与 limiter 已长期存在 |
| `src/doxagent/content_enrichment/service.py:49` | 批次 gather、deadline/retry 与分步 finalize |
| `src/doxagent/message_bus_v2/service.py:606` | intake identity、raw_hash、已见 payload 跳过 |
| `src/doxagent/message_bus_v2/repository.py:618` | claim/lease 恢复与 attempt_count |
| `tests/test_content_enrichment_hub.py:376` | 过期测试仍期待一次 extractor 调用 |

附录的每行是代表性现场观察，不能替代组内所有记录的逐条归因。

## 附录 A：全部 54 个分组的现场覆盖

下列结果是旧内核在本轮环境中的输出。`accepted` 不是人工核准全文；明细 URL、请求状态及快照索引见 live_probe.json。无 resolved_url 的分组按历史 source_name 拆分。

| 域名／未解析来源 | 历史原因 | 总体条数 | 实测条数 | 本次程序结果 |
| --- | --- | ---: | ---: | --- |
| finance.yahoo.com | source_summary_only | 373 | 6 | source_summary_only:2, accepted:4 |
| unresolved:Yahoo | not_attempted_or_no_result | 148 | 1 | source_summary_only:1 |
| finance.yahoo.com | timeout | 91 | 1 | accepted:1 |
| finance.yahoo.com | empty_extract | 73 | 1 | accepted:1 |
| finance.yahoo.com | http_403 | 46 | 1 | accepted:1 |
| unresolved:Benzinga | not_attempted_or_no_result | 29 | 1 | accepted:1 |
| cnbc.com | unsupported_media | 22 | 1 | unsupported_media:1 |
| finance.yahoo.com | unsupported_media | 21 | 1 | unsupported_media:1 |
| thestreet.com | empty_extract | 19 | 2 | empty_extract:2 |
| finance.yahoo.com | http_429 | 18 | 1 | accepted:1 |
| unresolved:SeekingAlpha | not_attempted_or_no_result | 17 | 1 | incomplete_extract:1 |
| seekingalpha.com | incomplete_extract | 11 | 1 | incomplete_extract:1 |
| unresolved:SeekingAlpha | empty_body | 10 | 1 | incomplete_extract:1 |
| finnhub.io | http_502 | 10 | 2 | http_403:1, source_summary_only:1 |
| unresolved:Benzinga | empty_body | 9 | 1 | accepted:1 |
| benzinga.com | timeout | 8 | 1 | accepted:1 |
| investopedia.com | http_403 | 8 | 1 | accepted:1 |
| seekingalpha.com | http_403 | 8 | 1 | empty_extract:1 |
| seekingalpha.com | empty_extract | 6 | 1 | incomplete_extract:1 |
| seekingalpha.com | timeout | 6 | 1 | incomplete_extract:1 |
| fool.com | http_429 | 6 | 1 | accepted:1 |
| benzinga.com | http_403 | 5 | 1 | accepted:1 |
| unresolved:CNBC | not_attempted_or_no_result | 5 | 1 | unsupported_media:1 |
| benzinga.com | incomplete_extract | 4 | 1 | accepted:1 |
| stocktwits.com | http_403 | 4 | 1 | accepted:1 |
| unresolved:ChartMill | not_attempted_or_no_result | 4 | 1 | accepted:1 |
| fool.com | timeout | 3 | 1 | http_403:1 |
| investorshub.advfn.com | http_403 | 3 | 1 | accepted:1 |
| thestreet.com | source_summary_only | 3 | 1 | empty_extract:1 |
| finnhub.io | timeout | 2 | 1 | accepted:1 |
| fool.com | http_403 | 2 | 1 | accepted:1 |
| app.moby.co | empty_extract | 2 | 1 | empty_extract:1 |
| wsj.com | http_401 | 2 | 1 | http_401:1 |
| just-auto.com | poison_or_navigation_extract | 2 | 1 | http_403:1 |
| investmentmonitor.ai | poison_or_navigation_extract | 1 | 1 | accepted:1 |
| fool.com | incomplete_extract | 1 | 1 | accepted:1 |
| axios.com | http_403 | 1 | 1 | accepted:1 |
| 247wallst.com | http_404 | 1 | 1 | http_403:1 |
| qz.com | incomplete_extract | 1 | 1 | accepted:1 |
| beincrypto.com | http_403 | 1 | 1 | accepted:1 |
| marketwatch.com | http_401 | 1 | 1 | http_401:1 |
| just-auto.com | http_403 | 1 | 1 | http_403:1 |
| investorshub.advfn.com | incomplete_extract | 1 | 1 | incomplete_extract:1 |
| usatoday.com | http_403 | 1 | 1 | accepted:1 |
| verdict.co.uk | poison_or_navigation_extract | 1 | 1 | http_403:1 |
| videos.coindesk.com | incomplete_extract | 1 | 1 | incomplete_extract:1 |
| power-technology.com | poison_or_navigation_extract | 1 | 1 | http_403:1 |
| finance.yahoo.com | http_500 | 1 | 1 | source_summary_only:1 |
| stocktwits.com | incomplete_extract | 1 | 1 | accepted:1 |
| unresolved:CNBC | empty_body | 1 | 1 | accepted:1 |
| privatebankerinternational.com | poison_or_navigation_extract | 1 | 1 | http_403:1 |
| qz.com | timeout | 1 | 1 | accepted:1 |
| cdn.jwplayer.com | unsupported_media | 1 | 1 | unsupported_media:1 |
| thestreet.com | timeout | 1 | 1 | empty_extract:1 |

合计：1000 条总体，61 条代表性现场探测，54/54 组覆盖。没有对其余 939 条伪造现场结果。
