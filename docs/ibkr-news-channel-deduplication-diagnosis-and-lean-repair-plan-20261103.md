# IBKR 多频道同文重复：勘查与精益修复方案

日期：2026-11-03。本轮完成代码核对、生产只读取证及当前代码离线复现；**未修改产品代码、生产数据、订阅或服务状态，未部署。**

## 1. 结论与修复取向

**采用 IBKR/Dow Jones 的来源身份修复，复用现有全局文章与发布去重设施；不另建全局相似度系统，不放宽通用正文 hash 的质量门槛。**

这不是简单的“同一个 source 不执行去重”。当前通用发布判重没有禁止同源匹配；真正缺口是：

1. IBKR 将同一 Dow Jones 文章包装为多个频道专属 ID，系统直接把这些投递 ID 当成不同文章。
2. 多数原文没有网站 permalink，公共 provider 介绍页被正确排除，无法靠 URL 合并。
3. 通用完整正文兜底只能覆盖一部分：短快讯没有硬正文指纹；部分原生长文虽已判为 FULL，却因再次经过网页式完整性检查而拿不到指纹。
4. 短快讯的正文经常只有版权模板，事实在标题中。不能把模板 hash 升格为全局硬键，否则不同事实会被误删。

**最新生产代码仍可确认漏重。** 11月2日 INTC 一篇长文跨5频道发布5次；BE 两条短快讯各跨2频道发布2次。9条发布均创建了 Runtime Case，合计6个额外 Case。这不是仅由旧历史或尚未部署此前修复造成。

## 2. 取证范围与统计口径

### 2.1 样本与版本

- 主窗口：`2026-10-27T04:00:00Z` 至 `2026-11-03T10:51:20.973848Z`；覆盖10个当前启用 ticker，取得408条 IBKR **已进入 Stream 的发布成员**，其中 INTC 315条。一天按 America/New_York 的 Owner 发布时间划分；源发布时间另存，不混作采集时间。
- 每个 Raw 主键点查首个 `message_content_revisions` 的正文，在服务器侧独立计算纯正文 hash，不使用有混合语义的旧 `logical_message_versions.body_fingerprint` 代替正文证据。408条首发正文均可直接读取，无 Native 展开；12条当前正文与首发不同，首发统计与当前状态分开。
- 408个 Standard ID 通过 Runtime 的 `source_message_id` 索引点查，408条均存在 Case。这里证明接纳和重复成本，不声称这些 Case 均执行了 W1/W2/W3。
- 生产仓库为 `cd9226d1`。Collector/BODY 使用镜像 `498d9b2c6b33`，Delivery 使用 `4de6b1304064`；镜像不同，但三个角色各16个相关关键函数的 AST 均与当前本地实现一致。核对包括 IBKR 转换、文章键、正文指纹、Live 接纳及发布，不仅查看 Git HEAD。

### 2.2 重复定义及结果

**严格字段组**：同 ticker、原始展示标题相同、首发纯正文 hash 相同、源发布时间精确相同。每组首条以外计额外副本。这是可重算的字段重复统计，包含没有业务事实的占位记录，不能全称为有效新闻重复。

|ticker|IBKR发布成员|严格字段重复组|严格额外副本|同 Dow Jones 核心 ID 组|核心 ID 额外副本|
|---|---:|---:|---:|---:|---:|
|INTC|315|53|151|52|171|
|MU|38|3|12|3|12|
|NBIS|26|5|11|5|11|
|SNOW|8|1|4|1|4|
|BE|6|2|2|2|2|
|CEG/COHR/IONQ/RKLB|15|0|0|0|0|
|合计|408|64|180|63|200|

“核心 ID”指当前可验证的 `DJ-RTA$1fadd811`、`DJ-N$1fadd811` 等 ID 中相同的 `1fadd811`，**不是任意来源的 `$` 后缀规则**。63个跨频道组共263条发布，所有组的首发纯正文、源发布时间均一致；展示标题差异除一个仅有 `!` 的坏标题外，都是前置 `!`/`*` 标记。该证据支持当前 Dow Jones 家族的文章身份规范化，不证明未知供应商或任意未来 ID 格式都能这样解释。

INTC 严格副本分布：10/27为1、10/28为1、**10/29为87、10/30为58**、11/02为4。后两项历史日数已复现用户提示；`363条/154副本` 的完整原始窗口与行清单未随提示提供，本轮明确窗口的315/151不冒充与其完全同分母，也不覆盖或否定原统计。额外有界检查了10/20–10/27的25条 INTC 发布，未为凑齐旧分母继续回扫全部历史。

“应仅 IBKR 存在”不能作为全局保证。对前次冻结的2,913条、10/28–10/30多源样本，以同源、同标题、同源发布时间和可用纯 FULL 正文 hash 检查，找到的111个额外副本都属于 IBKR；这支持当前优先做来源修复，但未覆盖所有无正文、近似转载及未来来源。

### 2.3 最新生产坏例

|文章|真实源时间 UTC|Owner发布时间 UTC|频道及核心 ID|实际后果|
|---|---|---|---|---|
|INTC：AI Fears, Elections, Earnings Make a Combustible Week -- Barrons.com|11/02 12:40:00|12:41:14.254–12:41:16.049|DJ-N/RTE/RTG/RTA/RT，`1fb2cef4`|5个 Stream成员、5个 Case；首发正文完全相同，6,847字符|
|BE：California Resources and Bloom Energy to Evaluate Potential Development…|11/02 22:46:00|22:46:09.021、22:46:09.431|DJ-RTPRO/N，`1fb4b64d`|2个成员、2个 Case；510字符版权模板|
|BE：…Identify Commercial Pathways…|11/02 22:47:00|22:47:59.037、22:47:59.503|DJ-N/RTPRO，`1fb4b6a9`|2个成员、2个 Case；510字符版权模板|

## 3. 发生机制与根因

### 3.1 从频道投递到独立文章

当前链路：Tick 292/历史列表 → `_poll` 按 `(providerCode, articleId)` 合并传输重送 → `_complete_rows` 为各投递获取原生正文 → `_result` → Collector 持久采集 → Live article/BODY → Delivery → Raw/Standard/Stream → Runtime。

例如：

```text
DJ-RTA / DJ-RTA$1fadd811
DJ-RTE / DJ-RTE$1fadd811
DJ-RTG / DJ-RTG$1fadd811
DJ-RTPRO / DJ-RTPRO$1fadd811
DJ-N / DJ-N$1fadd811
```

`external_id` 又拼成 `DJ-RTA:DJ-RTA$1fadd811`；`identity_key_for` 得到 `ibkr_news:external:DJ-RTA:DJ-RTA$1fadd811`。这是合理的**传输/来源记录身份**，但不是最小业务文章身份。现有文章键在没有可信 URL 时继续使用这个完整 ID，产生5个 BODY owner。

这里的“不同 site”准确说是不同 **IBKR provider 子频道**，不是五个新闻网站。不能按所有 `ibkr_news` 只有一个 publisher 来合并，因为 Briefing 等真正不同供应商也在同一个 adapter 中。

### 3.2 FULL 状态不等于当前硬正文键一定存在

- `native_article` 验证 IBKR `articleType=0`、articleId 和正文对应关系，原生路径可直接产出 FULL/SHORT_FULL。
- `LiveRepository.prepare_result` 的 `full_fingerprint` 又调用 `assess_media_body`，要求网页式 `complete_like` 及至少40个不同词；`content_evidence` 也有类似判定。
- 最新 INTC 6,847字符长文是原生完整内容，文中包含几处正常的 “Read more about…” 相关阅读措辞。通用 `TRUNCATION_MARKERS` 含 `read more`，再次检查返回 `truncated_or_paywall_marker`，因此硬正文指纹为空；5个频道都绕过了 hash 候选查询。
- 509/510字符的快讯模板则得到 `short_summary`、指纹为空。通用判重拒绝把模板作为硬键是正确的；错误在于缺少独立于网页正文形态的供应商文章身份。

此前通用修复确有收益：当前代码重放一组正常长文时2次输入仅发布1次。但它不是 IBKR 频道身份修复，不能据此前31组跨源重放通过就声称所有同源频道场景已覆盖。

### 3.3 原生快讯与版权模板混为“正文”

124条当前记录以 `(MORE TO FOLLOW) Dow Jones Newswires` 开头，属于需识别的模板候选；抽查及坏例表明其事实在标题里。另找到15个“同 ticker、同正文、同源时间，但业务标题不同”的组：这些常是同一分钟的多条财报/电话会快讯，**绝不能仅按模板正文合并**。

还有3条标题只有 `!`、正文只有模板的无事实记录被当作 SHORT_FULL 发布；另有1条标题是 `!` 但正文是真实长文，后者必须保留。不能用统一删除 `!` 标题的规则解决。

### 3.4 当前代码离线复现

使用生产冻结原始 SDK headline/articleText，经真实 `_result → Live → Hub → Delivery`；时间平移到接纳窗口，ticker暂用隔离测试绑定，不发 SDK、新闻 HTTP、浏览器或模型请求。

|场景|输入|当前发布|说明|
|---|---:|---:|---|
|正常原生长文、同核心ID|2|1|通用 FULL hash 可兜住|
|短快讯、同核心ID|5|5|仍漏重|
|不同事实标题、同一分钟模板|2|2|这是必须保留的正确负例|
|空 `!` 标题、只有模板|2|2|无事实占位也进入发布|
|11/02 INTC 含相关阅读措辞的完整长文|5|5|FULL但没有硬正文键，仍漏重|
|11/02 BE 同核心ID短快讯|2|2|最新生产问题可复现|

所有组的 HTTP extractor 调用为0，说明问题不依赖外网失败或浏览器身份。这里的0不代表生产 `reqNewsArticle` 为0；原生 SDK 内容已经取好才进入重放。

## 4. 统一修复方案：小范围身份修正，沿用现有设施

### P0-A：把 Dow Jones 文章 ID 与频道投递 ID 分开

**改动位置：** `ibkr_news.py`、`body_results.article_key`、`deduplication` 的文章别名入口。

1. 增加一个很小的纯函数，识别本轮已有证据的 `DJ-N/DJ-RT/DJ-RTA/DJ-RTE/DJ-RTG/DJ-RTPRO` 和相应 `providerCode$hex` articleId；要求 prefix与实际 providerCode 对应。返回命名空间明确的 `ibkr:dowjones:<core>`。未知频道/格式与非 Dow Jones 保留原规则，不全局截取 `$` 后缀。
2. 这个 ID 用于统一 Live/BODY 文章 locator，并按现有访问 scope、语言隔离；同 ticker 的发布唯一性继续由原机制承担。**external_id、source_item_key、SDK请求参数、recent_sdk_articles、checkpoint及每频道取网覆盖保持原样。** 不为了少一个下游消息停采某个频道。
3. 将同一 ID 接入已有 `verified_article_aliases/message_identity_aliases`，`publication_match` 接受该有出处的别名，避免只生成别名而查询时仍只查 URL。它是几行通用接线，不是新的判重引擎，也不把 generic provider 页伪造成 canonical URL。
4. 不包含标题或正文 hash 作为该稳定文章 ID 的必需部分，否则正文更新又会生成一篇新文章。原始频道ID仍保留在既有采集记录和传输 checkpoint 中，不为副本另建正文快照或渠道账本。

### P0-B：修正快讯内容形态，保留真正更新

**改动位置：** `ibkr_news.py` 的原生内容准备、`content_enrichment/native.py`、`LiveRepository.prepare_input/accept`。

1. 共享一份 IBKR 专用的轻量内容整理函数，避免 adapter 与 native 校验各写一套：保留原始 articleText，正文视图只移除有明确边界的 Dow Jones 尾注/法律模板；不泛删正文里的 `read more`，不改全局网页完整性规则。
2. 对“有效新闻标题+仅有 MORE TO FOLLOW/版权尾注”的已验证原生 flash，以**新闻标题本身作为短快讯业务内容**，标为 SHORT_FULL，并沿用现有方法/原因元数据区分原生 flash。它不是全文长报道，不通过凑字数或拼版权声明获得 FULL；不为取网页长文额外启动浏览器。HTTP/Reader 的同标题预览不因此获得同样资格。
3. 标题和去尾注后的内容均无事实文字时，不送 BODY/Stream/Runtime；由现有采集回执记录过滤计数，仍正常确认原始 SDK 投递。`!` 标题但真实正文存在时保留，并优先采用同一文章已取得的有效标题。
4. 只在 IBKR 标题比较/展示入口移除已证实的前置 `!`/`*` 通知标记，不改文字中的标点、数字或相反事实。读到 SDK 明确的语言字段时保留它，不把所有未知消息强制设成英语。
5. 相同核心ID的新频道正常不重做 BODY/相关性；但后到已验证原生内容若带来真实正文或有效标题变化，仍应修订原消息。当前 `native_upgrade` 仅在旧 body_digest 为空时触发，不能照搬为最终条件；在锁外比较规范化后的现有 material_hint，允许真正变化进入原 content_update 路径，按既有 input_revision 拒绝过时结果。把 canonical 的 material_hint/title/body_digest 合入原有薄查询，不为比较每轮展开完整历史输入。相同模板/标记差异不算更新，无新恢复队列。
6. 原生 flash 后到同核心ID的完整长文可晋级；已有完整长文不被后到空模板/较差标题覆盖。按 ARTICLE_ONCE 更新原 Raw/Standard及修订记录，不再次生成已消费下游的 Case。不同核心ID的不同事实快讯继续独立发布。

### P0-C：按具体旧 ID 接回，不做历史迁移工程

**改动位置：** `repository.publication_match`、`LiveRepository.prepare_input/accept/publish`。

- 新别名尚未命中时，只对识别出的核心ID，按6个已知频道构造旧 `ibkr_news:external:<code>:<code>$<core>` 键，用已有 `(ticker,source_id,identity_key)` 索引取已发布候选；不查同标题历史、不扫 JSON/Native 历史表。
- 多条旧副本已存在时，固定选择最早已发布的那条，登记新 alias/canonical 到其既有 live key。以后按这个锚点更新；保留原 Raw/Standard/Stream身份、首次观察和修订历史，不把规范化上线变成一次重发。
- 重开与并发准备沿用现有短事务 alias 预约、发布末端标量核对和锁外本地重准备；不把模型验证、正文读取或完整对象展开放回写事务。
- 已发出的历史 Case 不删除、不重新合并/重算 W1W2W3，不宣称历史成本被撤销。旧候选点查只发生在当前真实输入需要接回时。

### 暂不做的工作

- 不建设全局模糊标题/语义去重，不提高 FULL hash 的宽松程度，不增加一个长期身份服务。
- 不只改 Gateway 的 `(provider,articleId)` 传输去重或关闭多频道，这会漏掉替代频道恢复与更新，也不能解决发布身份。
- 暂不合并 `_article` 的 SDK 下载缓存或加跨频道 singleflight。现有60秒缓存仍按频道ID，故本方案第一版不能承诺减少 SDK 取正文次数。当前最直接收益是 BODY本地处理、相关性和 Runtime 负载减少；如实测 SDK 本身仍是显著成本，再单独用上述稳定ID调整既有缓存，并保留新 tick 失效和另一频道失败恢复，不先加额外队列。
- 不回填全历史指纹或批量清理重复消息；不为凑齐363旧分母无界取证。

## 5. 实施顺序、收益与风险

|顺序|执行内容|完成条件|
|---|---|---|
|1|冻结本轮63个核心ID组、64个严格组及6组当前复现；写稳定ID/内容整理的纯函数测试|当前坏例可重复；未知格式/非DJ不变|
|2|实施 P0-A，接入 BODY文章键和发布alias查询|5频道同文只有一个 article owner；每 ticker 独立发布|
|3|实施 P0-B，包含短快讯、空占位与真实更新|不同事实不合并；原生长文不因相关阅读措辞失去稳定身份；flash内容真实可读|
|4|实施 P0-C，验证旧发布接回和并发|不新增历史迁移；不重发旧文章；Native/模型工作均在事务外|
|5|整组回归并审查变更；追加 changelog|旧Google/跨源去重、IBKR transport checkpoint和更新测试通过|
|6|后续获部署指令后，按真实改动统一构建Collector/BODY/Delivery及需要加载该代码的Bus角色|实际加载关键函数一致，不能只看镜像标签|
|7|观察自然交易日消息，覆盖多频道与flash/长文|按下节验收；无新样本如实留空，不制造测试新闻|

**收益估计：** 本窗口63个核心ID组的263次历史发布可收敛为63次，即该集合少200次发布/Case（约76%）；相对全部408条为约49%。这是冻结窗口的反事实上限，非未来固定节省率，也不与严格组的180次相加。无事实占位的过滤另列，不能拿删除垃圾占位替代有效新闻去重。最新三组9次发布预期为3次，省6个新 Case；这些文章事实不损失。

**资源成本：** 一次常数级ID解析和既有内容整理；每篇至多增加现有 alias 表的薄键。正常命中复用原 canonical/文章路径，旧键接回为最多6个频道的索引候选；没有全局扫描、事务内 Native读取、后台恢复或定时重算。正文模板整理与 native校验共用结果/规则，避免增加两遍 HTML解析。没有实测前不承诺具体 CPU、秒数或SDK请求下降。

**主要风险及相称边界：** 核心ID规范只用于已观察并验证的DJ家族；未知格式沿旧逻辑。原文不完整、空模板与真实快讯需区分，尤其防止把不同标题的模板消息按body合并。规范化后后到新内容必须仍能更新原文章，这比单纯改一个键更重要。保留原始SDK投递与既有事务幂等，不增加第二套保护状态机。

## 6. 验收标准

1. **同篇跨频道：** 6组当前重放中，正常长文2→1、同核心flash5→1、新长文5→1、新BE flash2→1；有效输入仍完整保存发现证据，每 ticker仅一份发布身份。
2. **不误合并：** 同一时间、同一模板但不同业务标题/核心ID两条均保留；非DJ、未知ID格式、不同语言/private scope及不同 ticker边界保留。
3. **空占位/内容质量：** 空 `!`+纯模板不发布；`!`+真实正文保留；有效headline flash为真实短内容的SHORT_FULL，版权字数不计作新闻正文或FULL证明。
4. **更新：** FULL先到/后到、flash→长文、真实正文修正、标题修正、坏标题后到、旧结果并发返回都更新同一文章且不降级；ARTICLE_ONCE无第二Stream/Case。
5. **迁移和重开：** 旧6频道已有副本的窗口只接回确定锚点，不新增Raw/Standard/Stream；未发布在途任务按原输入修订围栏完成或失效；Journal/服务重开与同批并发不重复发布。
6. **真实运行计数：** 分别记录各频道取网/SDK原文请求、持久采集输入、唯一story owner、BODY执行、Standard/Stream及Runtime Case。采集条数多可以正常，但每ticker同一已验证story的新Case应为1；不能把SDK请求没降误报为“全链路下载节省”，也不能以停频道换数字好看。
7. **不回归时效：** 第一份可用内容立即进入原路径，不等待其它频道或主频道，不新增跨源等待窗口；对本轮截取的自然样本核对列表到可消费时间，并保留失败、延期和未完成样本。

## 7. 证据与取证成本

`eval/ibkr-channel-dedup-survey-20261103/`：

- `production.jsonl/export-readonly.py`：窗口、查询计划、408条薄发布证据及逐ticker完整回执。使用ticker Stream索引→成员主键→Standard/Raw主键；每ticker5秒SQL上限，实测1–255ms，均完整返回。
- `body-hashes.jsonl/body-hash-readonly.py`：408条首发/当前纯正文独立hash；合计检查2,029,245字节内联文本，Native展开0，耗时620ms。这是逻辑取证量，不冒充物理I/O。
- `samples.jsonl/recent-samples.jsonl`：117+9个主键的有界内容/SDK快照，单字段上限12,000字符；第一批115ms，供原文、模板和通知标记复核。较长文本截断有显式标记，完整hash采用前述独立点查。
- `runtime.jsonl`：408个Standard对应Case索引点查，40ms；`running-images.txt/code-*.json/code-comparison.json`：实际角色与关键函数一致性。
- `analyze.py/analysis.json/analysis.log`：首发字段重复与核心ID重复分开统计，不使用旧混合指纹列作最终正文证明。
- `replay-current.py/current-live-replay.json/.log`：当前实现的6组本地复现；不是已修复验收或网络性能压测。
- `older-window.jsonl`：附加有界旧窗口及25条INTC回执，单独保存，不悄悄拼入主分母。

本轮没有新闻网络访问、SDK订阅操作、Agent/LLM运行、生产写入或服务重启。修复方案先解决有证据的来源身份和内容形态问题，通用完整正文兜底继续保留原边界。
