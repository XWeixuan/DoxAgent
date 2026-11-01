# 消息管线跨消息源去重勘察（2026-11-02）

范围：当前代码与生产已发布消息；评估全ticker接入Google News Search RSS前，既有去重能否避免重复进入下游。仅勘察，不修改生产配置、数据或产品代码。

## 1. 结论与上线判断

**现有系统有跨源去重，但不能认为它已经兜住Google News扩量。建议先修确定性的文章身份与live发布缺口，再全ticker绑定；不需要先建设语义去重系统。**

本轮在生产只读抽取美东2026-10-28、29、30三个完整自然日的全部Owner发布记录，覆盖10个ticker、53个来源，共**2,913条article×ticker**。这里每条均对应独立Stream item/Standard Message，尚未发布的候选不计入此分母。

- 首发快照可确认：**31组跨源同篇副本、65条发布、34条多余发布**，占全样本发布量**1.17%**。其中17组是同ticker、同规范化文章URL；另以首发完整正文指纹补充，交集去重后得到31组。没有把“同标题”直接判成重复。
- 这65条均已找到独立Runtime Case，34条后到副本也全部建了Case，**下游幂等没有把跨源副本挡住**。65个Case当前61 COMPLETED、3 UNAVAILABLE、1 FAILED；不把Case数量直接换算成LLM次数或费用。
- Google当前实际只绑定**INTC、BE**。两者样本共有Google发布539条（INTC492、BE47），其中**29条位于上述跨源重复簇，涉及27组，占Google发布5.38%**。这是两ticker在这三天的已发布样本，不是全ticker未来重复率，也不是原始RSS候选重复率。
- 按当前修订后正文重看，同URL/相同完整正文的并集扩大至**46组、95条、49条多余发布**。Google涉及38条，占539条的7.05%。这一组用了后续BODY结果，**不能冒充首发时已取得完整正文的历史结论**。
- 同标题规范化还检出120组、319条候选。它们包含上述已确认项、转载、同事件独立报道、动态新闻和正文缺失，不把199个“后到标题成员”直接算成漏重。

最主要的结构性问题是：**旧接纳路径的URL/完整正文去重、live文章键及live发布路径没有统一成同一套身份合同。** live路径绕开旧的正文匹配，部分API又没有提供可信文章URL类型；结果是“正文其实相同、甚至URL完全相同”，仍拥有不同文章键、逻辑消息ID和Standard ID。资源或调度正常也会稳定漏重，不能靠加大资源解决。

还有一个必须同时修的质量问题：当前live合并可能忽略后来来源提供的原生FULL正文。**不能用“把所有来源的键并到一起，然后忽略后到输入”来节省成本。**

## 2. 当前执行链路和去重规则

### 2.1 去重对象与范围

当前业务对象是**同一篇文章/同一新闻版本的多次发现**，不是“同一个事件只允许一篇报道”。同一文章被Google、Yahoo、API、直采同时发现应合并；不同记者的新事实或独立分析不能只凭标题接近丢弃。完整同文转载可列为强副本候选，但须检查原publisher、时间和独立发布证据。

发布唯一性作用于**ticker内**。同一篇文章分别与INTC、MU相关，各自进入该ticker的Runtime是正常覆盖，本轮没有把跨ticker传播算成重复。BODY下载则可以跨ticker、跨发现来源共用。

### 2.2 当前live主路径

```mermaid
flowchart LR
    A[列表发现/持久化capture] --> B[文章键与输入hint]
    B --> C[live_articles + article×ticker delivery]
    C --> D[正文阶段: Google wrapper解析]
    D --> E[canonical预约 / 已有FULL复用 / 必要时下载]
    E --> F[正文结果与修订]
    F --> G[已发布URL查询 / 相关性]
    G --> H[live发布: Raw + Standard + Stream]
    H --> I[Runtime按Standard ID接纳]
```

|层级|实际规则|能省什么 / 不能保证什么|
|---|---|---|
|相同来源重复输入|origin包含source_id；material hint取稳定title/summary/body及明确更新字段，排除抓取时间等噪声|避免不变列表反复BODY；不是跨源同篇判定|
|live文章键|`hash([locator, access_scope, language])`；locator优先可信article URL，否则退回`source_id:external_id/...`|同键共用BODY与article×ticker delivery；未知URL类型、语言缺值、包装链接可分裂|
|wrapper解析|Google最初以wrapper/GUID入队，BODY阶段查持久映射；新wrapper经原有解析器解析|已有映射可避免重复解析取网；并非列表阶段已知道原文|
|canonical预约与结果合并|`reserve_resolved`及`commit_result`用canonical键、live_origins合并owner和delivery|在下载前/结果后都能合并；依赖同一键规范和兼容输入|
|BODY复用|完整结果、publisher URL、语言、访问scope和更新责任满足条件则复用；已验证wrapper映射也可复用|能避免第二次正文下载；不等于一定阻止第二次发布|
|live发布前旧文查询|`known_published_article`按ticker查强URL alias，再查同source/binding/identity；核对访问scope、已知语言和冲突|找到后沿原Raw/Standard ID更新；**不按完整正文指纹找跨URL同文**|
|live发布|按known Raw或`hash(article_key:ticker)`得到Raw/Standard ID；Stream member对Standard ID唯一|同文章键/已知旧文可ARTICLE_ONCE；不同键且未命中known可能分别发布|
|Runtime|inbox以`ticker:source_message_id`幂等；source_message_id是Standard ID|防同一Standard重复消费；不同Standard的文章副本仍可各建Case|

本轮2,913条中**2,531条是raw_live路径，382条为其它/兼容Raw ID**。旧路径仍需理解，但不能用它的单元测试替代live主路径验收。

### 2.3 URL到底如何归一化

`article_url`当前会：

1. 排除明确generic及首页、`/search`、`/news`根列表等地址。
2. 如果`url_kind=unknown`，只认可特定路径形状，如`/article/`、`/story/`、`/news/某条`、`.html/.aspx`、四位年份日期路径；匹配不到则返回None。adapter明确声明`article`才跳过这一步猜测。
3. scheme/netloc小写，去fragment；query排序；删除`utm_*`、fbclid/gclid/mc_cid/mc_eid。
4. `.tsrc`只在`finance.yahoo.com`删除。**其它publisher上的`.tsrc`、Barron’s的`siteid`/`mod`仍保留**；也不统一www、http/https、不同slug、AMP、不同国家域。选择文章的id/query参数同样保留，这是必要边界。

因此相同网页地址并不必然形成同一硬键：URL类型不明而路径不在白名单时，系统甚至不把这个URL作为文章证据。

### 2.4 旧路径的强去重与冲突规则

旧`record_raw → _dedup_candidate`在ticker内查询：同source identity、可信URL alias、adapter明确verified alias；没有身份命中时，再按**完整正文的精确指纹**找候选。标题查询主要用于旧数据采用/冲突判断，**标题相同不是硬合并键**。

正文指纹使用NFC及空白规范化后的精确文本hash，不是语义相似度。须达到完整正文质量、不是失败时复制的summary，且至少40个不同单词；短全文、模板短讯、PARTIAL不直接变成跨provider硬键。

冲突检查包括：明确发布时间相距超过36小时、独立发布标记、不同真实URL而无已验证映射、复用ID但标题严重不相容；正文匹配还核对标题词重合、数字及rise/fall等相反词。它们不能直接当现代live路径的统一合同：例如Google尾部publisher名“24/7 Wall St.”会给标题多出24、7，被旧数字规则误认成事实不同。本轮当前同文组中抽取比较的33个正文候选关系有12个会被当前旧冲突函数拒绝，包含这种后缀误伤，也有真实标题重写；**不是简单重新调用旧函数就能完成修复**。

匹配成功后即使有正文补充或实质编辑，仍更新原消息及修订记录，不再次发Stream，保持已确认的ARTICLE_ONCE。不同事件/不同文章则继续发布。

### 2.5 确实有效的部分

- 离线正常对照：两来源同可信URL、同语言，live只产生1篇文章、1次fake extractor执行、1条Stream。
- 生产能看到已合并来源的实际记录，如INTC的SDxCentral/Google同稿、Tom’s Hardware/Google同稿，MU的GlobeNewswire搜索/行业RSS同稿。
- Raw的message_sources在样本中有5条带多source标记，但live合并不保证完整累加这个数组，所以**5不是总去重命中数，不能据它计算去重成功率**。

## 3. 生产抽样：样本、分母与实际重复

### 3.1 抽取口径与完整性

- Owner发布窗口：`2026-10-28T04:00:00Z ≤ stream_items.published_at < 2026-10-31T04:00:00Z`，对应美东10/28–10/30三个完整自然日，包含盘前、盘中、盘后和故障/混合版本时段。不是用文章声称的发布时间筛发布分母。
- Stream → member → Standard → Raw逐条关联，10个ticker全部成功导出，无截断；每ticker查询上限10,001，实际最高1,545。
- 以Raw当前规范URL、内容指纹找候选，随后读取强候选的**首个content_revision**，确认首发URL/首发完整正文。当前正文后补成功不能倒填首发。
- 同ticker内只合并不同source_id的强关系，并用连通分组去除URL/正文关系重叠，不把两种判据的命中数直接相加。
- 首发规则是高置信下界：没有枚举所有相似标题、相近正文、窗口外前稿；也没有为无完整正文样本补造相似度结论。
- 对强候选中47条带首发FULL指纹的记录，另外读取首发正文复算hash，全部与证据吻合；最少127个不同单词，段首人工核对为对应文章内容。没有仅凭系统标注FULL，就把共享页头/模板算成同文；这项核验也不等同重新验收所有来源的正文完整率。

|美东发布日|全部发布|Google发布|首发强证据多余发布（按后到成员归日）|
|---|---:|---:|---:|
|2026-10-28|631|130|3|
|2026-10-29|1,025|148|4|
|2026-10-30|1,257|261|27|
|合计|2,913|539|34|

全部发布按ticker：BE194、CEG128、COHR205、INTC1,545、IONQ7、MU380、NBIS307、ONDS40、RKLB38、SNOW69。

|判据|跨源组|组内发布|后到多余发布|Google参与|
|---|---:|---:|---:|---|
|相同规范URL，首发URL核对通过|17|34|17|16组/16条Google|
|首发已经有相同完整正文|15|32|17|12组/14条Google|
|上两项取并集、去交集|**31**|**65**|**34**|**27组/29条Google**|
|当前修订后的相同完整正文|30|63|33|21组/23条Google|
|当前URL/当前完整正文并集|46|95|49|36组/38条Google|

首发31组分布BE2、INTC28、MU1。没有从其它ticker未检出强重复推导“它们完全不重复”：当前Google尚未绑定它们，正文缺失、转载差异也会让精确判据失去检出能力。

### 3.2 已进入Runtime的具体例子

下表时间均UTC；这些均不是只在前端显示重复、后台实际已合并的情况。

|同篇内容|ticker|来源与Owner首发|硬证据与结果|
|---|---|---|---|
|10 Industrials Stocks Whale Activity In Today’s Session|BE|Benzinga 10/30 17:36:01；Google 17:50:29|相同Benzinga文章URL；两个Standard、两个Case|
|Intel Q3 Earnings Preview…|INTC|Google 10/28 17:58:35；Benzinga 18:02:20|相同文章URL；Google更早，不能按来源优先级丢Google|
|Dow Jones Futures / Amazon、Intel earnings|INTC|Yahoo 10/29 20:55:10；Finnhub 10/30 02:14:30|相同investors.com最终URL，均PARTIAL；两个Case|
|Trump’s Washington Is Now a Shareholder in 40 Companies|INTC|Google与Yahoo 10/28约16:30:53|首发FULL指纹相同；原站URL只差`.tsrc`，两条均发布|
|Micron Stock Drops Again. Surging Memory Prices Aren’t All Good News.|MU|Barron’s 10/30 15:07:33；Yahoo 15:18:22|同publisher全文完全一致；URL只差`siteid=yhoof2`|

第一例的两个Case是`case_f6867283651847c7850f6f3a59517768`与`case_f5a06c20fbff4fac9162caefc82644ca`；对应Standard为`std_live_2ddb73dd36dcffb510c25ab57db09ebf`与`std_live_02946493a9b10113ef4b33ab42ab518d`。完整原始ID及其它样本保留在证据文件，不在正文铺开几十条。

首发强簇里有**11组是Google先到**。全局按publisher禁用Google，或固定“直采优先、Google一律丢弃”，会损失有价值的先到发现，不能作为本次优化捷径。

## 4. Google News特别风险与反例

### 4.1 漏重一：adapter没有建立可信文章身份

17个同URL跨源组中，**每组恰有一个成员的URL被`unknown`形状规则拒绝**，共17条：15条Benzinga、2条Finnhub。比如Benzinga的`/markets/options/26/10/62752031/...`既不是四位年份路径，也不包含`/article/`或`/news/`；API又未声明url_kind=article。系统便退回source-specific locator，不建立该URL强alias。

Google解析后声明的URL是article；Benzinga/Finnhub那一侧却可能没有可匹配的URL alias。`known_published_article`只查alias和同source ID，live又不执行旧正文兜底，于是两个文章键和两个Standard一路发布。

**这不是同一时刻的数据库竞态才能触发。** BE例相隔14分多钟；离线按相同缺失字段串行输入也稳定产生两条Stream。

### 4.2 漏重二：包装、语言和参数构成不同物理键

- Google初始locator是`news.google.com/rss/articles/GUID`，语言en；真实原文在BODY阶段解析后才有。
- 生产539条Google都有language=en；同窗Yahoo295条中294条缺language、Benzinga77条中76条缺language、Finnhub19条均缺language。空语言与en参与不同文章键，妨碍前置共享；已发布URL查询能兼容一边缺语言，但它只是后一道机会，不保证合并物理文章与BODY工作。
- `.tsrc`被附在247wallst、TIKR、Stocktwits等原站上时不会删除；Barron’s的`siteid=yhoof2`/`mod=RTA`也会保留。全文一样却形成不同URL身份。
- Yahoo/Finnhub最终跳转和Google包装链接不总是在同一阶段建立同一种canonical锚点。存在先发布wrapper/中间URL、后续修订才变成原文的样本；已经发布成两个Standard的记录不会因后来发现同URL自动合并为一个下游Case。

不能用“删除所有query、去掉所有国家子域”粗暴处理：id等可能选文章，Investing国家域和明确访问scope有实际访问语义。

### 4.3 漏重三：live绕过正文匹配，且指纹列语义不一致

旧`_dedup_candidate`被`record_raw`调用；**live的prepare_publication/publish直接写Raw/Standard/Stream，没有调用该匹配流程**。其known查询也没有跨URL正文hash候选搜索。因此TheFly原站/Yahoo同文、Barron’s/Yahoo带追踪参数、同文转载都可能漏过。

更具体地说：

- `content_evidence.body_fingerprint`是规范化**纯完整正文**hash。
- live的`body_digest`是`hash([规范化标题, 正文])`，用作内容修订/交付版本判断，成功条件与纯正文FULL证据也不完全相同。
- live却把body_digest写到`logical_message_versions.body_fingerprint`；known更新分支又不持续更新该列。旧匹配则拿纯正文hash去查这个索引，合同失配。
- 样本有1,828条raw_live具有当前纯正文指纹，这1,828条全部与版本表指纹不一致；不能把该列直接当成“全管线统一正文指纹”统计或复用。

Git追溯：上述live写入`row['body_digest']`的代码来自`dcd7333b`（2026-10-29 00:47:49北京时间，`refactor(message-bus): shrink realtime work and isolate acquisition resources`）。这是实现来源，不据commit时间推断每条历史数据的实际部署时间；本轮已直接核验当前生产该逻辑仍存在。

### 4.4 合并副作用：后到FULL输入可能被忽略

`LiveRepository.accept`的changed依赖“同一个origin已经存在且hint改变”。另一来源首次出现时origin尚不存在，即使带有更好的原生FULL正文，也不必然替换已有UNAVAILABLE输入/结果。`reserve_resolved`对已有结果的owner也可能直接join，不能把“已有result_ref”当成“已有可复用的完整正文”。

离线复现：第一来源同可信URL得到UNAVAILABLE，第二来源带原生FULL；**最终仍UNAVAILABLE，Stream为1，后到正文没有晋级**。这是现有代码反例，尚未统计成生产发生比例。它直接约束修复：去掉第二次发布时，必须同时保证有价值的后到内容补充进入原消息，否则以节省成本换正文质量。

### 4.5 离线对照结果

全部使用临时SQLite、真实业务函数和fake extractor，无任何外部请求。表中extractor次数是测试调用数，不是生产下载量。

|输入场景|旧接纳路径Stream|live Stream|live extractor调用|
|---|---:|---:|---:|
|同可信URL、同语言|1|1|1|
|真实Benzinga路径、缺URL类型/语言，与另一明确article/en来源|1|2|2|
|同可信URL，仅语言空/en|1|1|1；但生成2个live article|
|两个URL、完全相同FULL正文|1|2|2|
|原站URL只差`.tsrc=rss`，正文相同|1|2|2|
|同可信URL，第一来源失败、第二来源原生FULL|—|1|结果仍UNAVAILABLE，未吸收后到FULL|

这些实验与实际生产字段和缺口相符；不能把正常第一行的测试通过当成其它场景已经覆盖。

## 5. 有价值的优化及优先级

建议保留live的轻事务/薄状态方向，**补齐一个统一的文章身份入口和轻量发布判重；不要把整个旧接纳事务接回热路径。**

### P0-A：先统一确定性文章身份，同时保证后到内容晋级

改动点：adapter身份声明、`deduplication.article_url`、`body_results.article_key`、`live_repository.prepare_input/accept/reserve_resolved/prepare_result/commit_result`。

1. Benzinga等可信API的真实文章URL由adapter明确声明article；Finnhub中间跳转与真正原文区分，最终解析成功后建立受支持的URL alias。不要通过无限扩大猜测regex把所有页面都当文章。
2. 用一份URL规范用于BODY键、canonical alias和发布查询。对已有同文证据支持的publisher追踪参数加入窄规则，如原站`.tsrc`、Barron’s的siteid/mod；保留文章选择参数、国家域和显式private scope。必要的www/AMP/slug别名只接受真实canonical/redirect或明确publisher article ID，不凭字符串相似合并。
3. 给确定英语的API补齐source/消息语言，或在已有强URL证据下做兼容的空语言接回；已知不同语言仍区分。不能全局把未知都当英语。
4. 新来源的原生FULL必须能补充旧UNAVAILABLE/PARTIAL，即使是新origin；优先直接使用已有正文，更新同一个article/Raw/Standard。已有FULL不被后到摘要/失败覆盖；无实质变化不重做BODY/相关性。保留真实内容更新责任。
5. 在BODY前的canonical预约和发布提交点保持身份一致。若并发准备后另一来源先提交，仅重新准备本地接纳/更新，不再下载；在短事务内用现有强alias/文章键做最后一次薄预约/核对，不展开Native对象或重新扫描历史。
6. 新URL/语言规范接回旧source ID、旧key与已发布Standard，保留first_seen、checkpoint和发布身份；按已有ID/alias具体接回，不让一次键规范修正把窗口内旧文当新消息重发。历史已建Case不删除、不批量改写，不将后来的去重修复伪装成历史从未重复。

**直接收益：** 对应17个相同URL组及大量追踪参数同文。可减少额外Case，也可让Google用上API已经取得的正文。不能声称17组会全部只靠改一个字段消失：语言、alias、后到FULL晋级及发布竞态须一起验证。

### P0-B：给live补上纯FULL正文的轻量跨URL兜底

改动点：`content_evidence`、`LiveRepository.prepare_result/prepare_publication/publish`、现有`logical_content`索引。

1. 分开保存/使用“标题+正文修订digest”与“纯FULL正文指纹”；不再混用同一列语义。正文hash在锁外计算并随必要结果写入；SHORT_FULL按其真实质量证据处理，短模板不硬合并。
2. canonical URL未命中时，按ticker+纯正文hash用现有索引取少量候选，检查原publisher、语言/访问兼容、发布时间及独立发布证据；只将确认同篇/同版副本接到原发布身份。
3. Google仅为身份比较删除**与RSS source精确一致的末尾publisher后缀**，保留展示/原始标题和真实数字；不因“24/7 Wall St.”拒绝同文，不删任意标题数字或相反事实校验。
4. 命中已有文章先复用已有接纳/发布身份；若必须更新正文，在原消息修订。ARTICLE_ONCE不变，不再次唤醒已消费下游。
5. 只对当前及后续必要写入修正指纹，历史通过具体候选按需接回；不做全库正文重新下载、全历史Native hash补扫，不为本次建设新向量库或LLM判重。

**收益边界：** 首发强并集34个额外Case是本样本可验证的去重机会，不是未来固定节省量。仅等全文拿到后才识别的转载，只能省相关性/发布/Runtime重复成本，**不能倒过来宣称正文获取成本也已省掉**。

### P1：将已知Google wrapper映射的复用前移

改动点：Google adapter/接纳准备、现有`PollingRepository.google_url`、`ContentEnrichmentHub._google_url`与canonical预约。

- 重复列表输入先批量/局部查询已有持久wrapper→原文映射，原文已知时直接走统一文章身份，原wrapper作为发现证据保留；不用已经解析过的wrapper再制造一份BODY owner。
- 新wrapper仍复用现有singleflight与有限解析槽，解析后先预约文章/复用FULL，再做有价值的正文访问。不要在Collector持列表槽把100条结果逐一联网解析，更不要为判重打开浏览器。
- 若只有标题/摘要相似、无URL/原文证据，就保留消息；不要用来源优先级或粗暴publisher黑名单掩盖没有识别同篇的问题。

现代Google wrapper首次解析通常仍需网页+batchexecute两次HTTP；**仅凭RSS域名和标题不可能零成本、零误删识别所有副本**。既有映射和确定性文章键可以消掉重复解析与正文工作，但不能承诺新wrapper第一次识别完全免费。

### P2：暂不建设的东西

- 不新增向量库、相似标题自动丢弃、事件级全局合并、跨ticker单Case、来源固定优先级等待、全局大锁或另一套恢复账本。
- 120组同标题只做人工复查候选，不据此直接扩张模糊规则。先完成P0并用自然新稿复核，残余转载成本仍显著再决定是否需要轻量内容近似指纹。
- 不因去重提高正文质量阈值、不降低Google召回，不改变原源周期或现有身份。对先发布摘要后FULL的文章，更新原消息而不重触发下游，明确遵守既有ARTICLE_ONCE业务决定。

### 修复后的最小验收与扩量顺序

1. 冻结本轮31组首发强样本，以及同事件独立报道、同标题不同正文、不同ticker/语言、短模板、不同文章id参数、真实重要更新负例。
2. 覆盖Google先到/后到、wrapper不同/相同、Native FULL后到、并发准备后提交、重启重送。每ticker同篇只一个Standard/Stream及Runtime接纳；正文不降级、真正更新保留修订。
3. 单独记已有FULL复用、新wrapper解析、真实BODY访问和新增Case；用操作计数/实际发送证明成本下降，不拿Stream少了当BODY请求也少了。
4. 正常网络/数据库负载下对当前两个Google ticker验收，再扩到全ticker并覆盖一个完整活跃交易日；先到消息不为等直采而延迟。不能只跑旧dedup测试；必须覆盖LiveQueue→BODY→Delivery→Runtime完整路径。
5. 在31组已知强样本没有漏重、负例不误合并、后到FULL可正确晋级前，**不建议直接全ticker开启Google**。不要求先解决所有可能的语义转载，避免把上线前提扩成大工程。

## 6. 限制、可复查证据与验收

### 6.1 生产版本和历史窗口边界

本轮取证时Bus/BODY/Collector运行image `47589435e504…`，启动于2026-10-31 21:56Z；Delivery仍是`32b5af2f8f6f…`，启动于10/30 21:17Z。不能称全组件同一版本。

已从生产Bus、BODY、Delivery按类限定抽出60个关键函数实例并与本地AST比对：文章键/访问scope、`known_published_article`、live的`prepare_publication/publish`等关键行为一致；prepare_input/accept/部分context_conflict有版本差异，BODY处理还存在本地尚未发布的能力续作修复差异，保存逐函数结果。Google解析、canonical预约和本报告指出的live发布/指纹机制在生产仍存在。Delivery对RELATIVE发布时间的冲突处理仍有旧差异，但本轮同URL主要案例为明确时间，不能将其冒充这些案例的根因。

历史样本含当时故障、版本混合与恢复积压，不能代表未来稳定运行的完整重复率。当前修订结果是2026-11-02读取的snapshot；首发下界单独通过首个content_revision核对。窗口外旧稿、没有正文/URL证据的近似转载和未来新增Google ticker均未被完整覆盖。

### 6.2 取证成本

只读SQLite连接，先EXPLAIN，再以ticker/stream_offset索引及主键逐层关联；没有采用会全扫stream_members的初始SQL方案，没有遍历Raw/Native/CDC历史大表。10个ticker导出各约8–1,505ms；每ticker6秒SQL墙钟、10,001行上限，均完整结束。补充信息按Raw/Standard主键查；Case按source_message_id唯一索引查。

只对强候选读取有限Native输入/结果快照，每轮约1.59MB解压内容、184个Native内容对象（不是物理I/O计量），有12MiB硬取证上限；首发字段走版本主键薄查询。未调用新闻源、BODY网络或浏览器，未改产品代码/生产配置/生产数据、未部署重启或改变绑定。

### 6.3 主要实现入口

- [deduplication.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/deduplication.py:27)：URL、别名、完整正文证据与旧冲突规则。
- [body_results.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/body_results.py:13)：文章键、访问scope和正文复用。
- [repository.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/repository.py:855)：旧强去重；同文件1588行为当前known_published_article。
- [live_repository.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/live_repository.py:139)：输入接纳；1040行为canonical预约；1116/1281行为发布准备与直接写入。
- [news_adapters.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/news_adapters.py:604)：Google wrapper输入及标题/身份字段。
- [content_enrichment/service.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/content_enrichment/service.py:281)：wrapper缓存、解析与BODY接回。
- [coordinator.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/coordinator.py:159)：Runtime Stream/inbox接纳。

### 6.4 证据目录

`eval/cross-source-dedup-survey-20261102/`：

- `schema.json`、`query-plan.json`：实际表/索引与查询计划；Google绑定读回。
- `published.jsonl`及各ticker receipt：完整2,913条薄发布证据；`extra.jsonl`是正文纯hash和Runtime Case点查。
- `exact_url.json`、`exact_full_body.json`、`same_title_candidates.json`：强URL、当前纯正文、待人工审查标题候选。`db_body_fingerprint.json`只是最初用版本表列产生的诊断输出，**不是最终纯正文结论**。
- `cases-first.jsonl`：首发版本、当前文章键/输入/结果与有限成本计数；fetch_total等旧累计字段不等同实际下载次数，未拿它们估算网络节省。
- `first-body-verification.jsonl`：47条首发正文hash复算、长度/词数与短段首核对；这些正文均在薄JSON内，无额外Native展开。
- `findings.json`、`aggregate.py`：首发/当前分开、URL与正文去交集及Runtime关联的可重复计算。
- `bus-code-v2.json`、`body-code-v2.json`、`delivery-code-v2.json`、`code-comparison-v2.json`：当前生产代码实证及带类限定的逐函数比较；无v2后缀的文件保留为首轮提取，最终比较采用v2。
- `reproduce.py`、`reproduction.json`、`reproduction-final.log`：5组旧/live对照及后到Native FULL反例。仅临时本地数据库，不能当性能压测或生产修复验收。

本轮交付为勘察与优化判断，产品实现与生产绑定均保持现状。
