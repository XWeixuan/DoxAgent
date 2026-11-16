# Massive Distribution 业务说明与正则调试取样指南

日期：2026-11-12。面向负责相关性正则调试的 Codex。本文件说明业务定位、当前实现与取样方法；不是启用正式分发或修改词库的指令。

现状依据本地实现及北京时间 **2026-11-12 02:46** 的生产只读快照。计数会随采集、修订、清理变化，不是完整交易日统计，也不是覆盖率或准确率验收。

## 1. 业务上是什么，为什么需要它

Massive Distribution 是第四种消息获取类型，代码值 `massive_distribution`。它先从一个平台的全局新闻池发现消息，再用各 Ticker 的相关性规则决定分发对象。一篇文章只需采集、补全一次，就能分别判断是否与多个 Ticker 有关。

背景是企业 IR / Press Release By Ticker 源只能直接覆盖绑定主体的公告。例如监测 RKLB 自身 IR，无法系统覆盖其客户、竞争对手、供应商及监管相关主体在其他平台发布的重要公告。全局 Newswire 池补上这类发现渠道，避免必须提前列出所有相关企业。

| 类型 | 业务入口 | 相关性前提 |
| --- | --- | --- |
| By Ticker | 已指定公司、账号或主体的页面/接口 | 入口通常已有主体约束 |
| By Search | 用监测词向站点搜索 | 搜索先缩小候选范围 |
| By Distribution | 共享行业/栏目新闻池，再向 Ticker 分发 | 栏目通常已有一定行业约束 |
| Massive Distribution | 大平台全局企业稿/新闻池，再逐 Ticker 筛选 | 行业和主体约束弱，必须应对大量无关消息 |

它与 By Distribution 共用处理链路，但保留独立类型，便于将来分别调整规则与策略。**Massive 是业务获取/分发语义，不是某一种抓取技术**：RSS、HTTP 列表、RSC 都可以作为它的发现方式。

首批是五家 Newswire；后来加入 Bloomberg。Bloomberg 是综合新闻而非企业新闻稿平台，应独立分层评估，不能把六个源统称为六家 Newswire。

## 2. 为什么正则需要更窄

全局池包含大量其他行业企业稿、地区商业消息、诉讼招揽、活动宣传和市场研究报告。普通栏目里尚可接受的 `AI`、`cloud`、`energy`、`quantum`、`data center` 等宽词，在全局池里很容易命中无关文章。正文还可能含有长公司介绍、市场报告覆盖名单和重复模板，出现词并不等于该新闻对监测 Ticker 有经济意义。

此类源当前**不走 Decision Model**。正则误判没有模型二次把关，直接成为测试发布候选。因此调词应强调准确率，同时保留能够解释的行业间接相关事件，不能简单缩成“只能出现公司名”。

建议人工标签：直接公司事件、可解释的间接经济相关、仅行业背景、无关/模板提及、无法判断。只有前两类作为拟接纳正例；间接相关应写明客户/供应/竞争/政策等传导关系。

## 3. 当前链路和隔离方式

`共享列表发现 → 采集持久化 → Live文章去重/正文复用 → BODY补全与清洗 → 每Ticker的L2正则 → 普通Raw/Standard消息构建 → 测试发布表`

- 六个源均启用且 `delivery_mode=test`，测试目标均为 INTC、MU、BE、COHR、NBIS、CEG、RKLB、SNOW、ONDS、IONQ。
- 目标是内存生成的测试绑定，不是十个正式源订阅。一个物理端点的采集结果供十个目标使用，不是下载十份；同一站多个端点共享平台调度并分别执行。
- 使用现有 Collector、Live、正文和标准化能力；没有第二条测试消息总线。最后在正式发布边界写 `test_publications`，不进入正式 Bus/下游研判。
- 当前要求真实完整正文：FULL / SHORT_FULL 才能进入有效相关性判断。PARTIAL、UNAVAILABLE 等落为 NO_PUBLIC_BODY，不能拿标题或列表摘要冒充完整正文，也不能当作正则阴性。
- 文章与 Ticker 投递分开建账；同篇文章可以对多个 Ticker 判定。去重、正文复用和 ARTICLE_ONCE 仍适用，历史修订不等于独立新文章。
- 测试 BODY 复用既有 HTTP/Reader，资源容量受限且正式任务优先，不批量开启浏览器补测试正文。列表成功与正文成功必须分别评价。
- 现有测试详情清理目标为14天，分批删除符合条件的终态测试记录并释放引用；在途/延迟任务可能保留更久。需要固定评测集时应及时导出，不承诺永久保留。

## 4. 现有哪些源、到什么阶段

| 平台 / source_id | 当前发现方式 | 当前边界 |
| --- | --- | --- |
| Business Wire / `businesswire_massive` | HTTP POST newsroom，`RSC: 1`结构化列表；RSS失败兜底 | 已采用官方公告ID+语言统一RSS/网页身份；公开正文仍严重受限 |
| PR Newswire / `prnewswire_massive` | HTTP POST新闻列表；RSS失败兜底 | 有完整正文样本，也有大量PARTIAL，不能都当有效分类样本 |
| GlobeNewswire / `globenewswire_massive` | HTTP load/more列表 + 原全站RSS独立并行 | 两路统一去重；不能假设HTTP总比RSS快 |
| ACCESS Newswire / `access_newswire_massive` | HTTP newsroom及分页 | 没有用博客RSS冒充企业稿兜底 |
| TMX Newsfile / `tmx_newsfile_massive` | 正常RSS | 用户明确保留RSS，不是HTTP失败降级状态 |
| Bloomberg / `bloomberg_massive` | 现有六路RSS + 自身news/latest sitemap | 多入口非严格时间排序、统一去重；付费正文及媒体内容存在可用性限制 |

说明：Bloomberg自身sitemap是此前既有配置。本轮没有新增sitemap；Business Wire未用Google News或其他sitemap替代。

六源配置周期均以30秒为目标，不等于网站发布后30秒必达。HTTP能返回200不代表列表及时，上游缓存/索引、窗口覆盖、采集排队及正文处理都要区分。2026-11-10 BW最终约10分钟观察21/21轮成功，单轮平均0.917秒；两篇新稿发布至Live接纳平均54.494秒。这是历史小样本，不是11月12日的实时SLA。

生产快照（文章计数为当前保留的测试文章；发布数为article×ticker×内容/词库版本的记录，不能直接相除当命中率）：

| 源 | FULL / SHORT_FULL文章 | PARTIAL文章 | UNAVAILABLE文章 | 测试发布记录 |
| --- | ---: | ---: | ---: | ---: |
| Business Wire | 13 | 0 | 5,023 | 4 |
| PR Newswire | 3,829 | 1,701 | 89 | 2,100 |
| GlobeNewswire | 6,590 | 133 | 54 | 4,243 |
| ACCESS Newswire | 967 | 0 | 23 | 523 |
| TMX Newsfile | 1,117 | 0 | 94 | 357 |
| Bloomberg | 1,071 | 1,591 | 184 | 845 |

BW另有7条NOT_APPLICABLE历史记录，未混入正文失败或完整样本。所有六源在正式 `standard_messages` 中均为0条。测试发布累计数可能高于当前TEST_PUBLISHED投递数：发布表保留版本记录，而投递表主要反映当前状态，不应强行对齐或据此判断台账损坏。

## 5. 消息实际存放在哪里

生产主机 SSH 别名 `doxagent-sg`。以下均为**容器内路径**，本地仓库的docs/eval文件不是实时消息库，不要将容器路径直接当作宿主机路径。

| 位置 | 内容与用途 |
| --- | --- |
| `/data/bus/bus.sqlite3` → `live_articles` | 已接纳的去重文章：source_id、url、title、first_seen_at、state、outcome、input_ref、result_ref、正文/输入修订等；测试记录明确delivery_mode=test |
| 同库 → `live_deliveries` | article_key×ticker投递：state、regex_result、terms_revision、last_error等；通过article_key关联文章 |
| 同库 → `test_publications` | 通过正文、相关性及发布门槛后隔离保存的标准消息，含ticker、source_id、content_revision、terms_revision、body_outcome、payload_ref |
| 同库 → `live_origins` | 文章来源、端点、别名映射；分析重复及多入口贡献时使用 |
| 同库 → `ticker_monitoring_terms` / `ticker_monitoring_term_revisions` | 当前词库指针与词库版本记录，核对采样时适用的版本 |
| `/data/bus/native-files/` | 上述ref指向的原始输入、补全文本及标准消息内容；按内容哈希/分块压缩保存，必须用既有codec解析 |
| `/data/bus/acquisition.sqlite3` → `cycles` / `units` | 物理轮询、端点健康、发现/失败及缓存观测；不是最终新闻正文库 |

`input_ref` 是发现时输入，可能只有标题/摘要；`result_ref` 是处理后的RawMessageInput；`payload_ref` 解码后包含 `raw`、`standard`、`terms_revision`、`test_publication`。最终可读消息使用 `payload.standard.title/body/url/published_at/collected_at/metadata` 等字段。

不要直接把ref当作JSON正文，不要只复制SQLite而漏掉native-files。不要在正式 `raw_messages`、`standard_messages` 或普通Bus列表找测试文章——那里为0是预期隔离结果。

时间也要区分：`test_publications.published_at` 是**测试落表时间**；新闻原始发布时间在 `payload.standard.published_at`；首次发现/接纳看 `live_articles.first_seen_at` 与采集证据。保留原时间精度标识，分钟精度不是精确到秒。

## 6. 给调词Codex的只读取样入口

优先用已有CLI，不执行enable/configure/replay，不改线上词库。远端执行：

```sh
sudo -n docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.test_delivery status
sudo -n docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.test_delivery export --source globenewswire_massive --limit 100
sudo -n docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.test_delivery export --source bloomberg_massive --limit 100
```

export返回含已解码payload的JSON；limit上限500，按测试发布时间倒序。这只能看**已筛出**样本，没有ticker过滤/完整分页，不能用一次500条导出冒充全量评测集。

补充正则阴性候选时，对同一库用SQLite `mode=ro`、小窗口分页，先取标量和ref，再解码选定正文：

```sql
SELECT a.article_key,a.source_id,a.title,a.url,a.first_seen_at,a.outcome,
       a.result_ref,d.ticker,d.state,d.regex_result,d.terms_revision
FROM live_articles a JOIN live_deliveries d USING(article_key)
WHERE a.delivery_mode='test' AND a.source_id=:source AND d.ticker=:ticker
  AND a.outcome IN ('FULL','SHORT_FULL')
  AND a.first_seen_at>=:start AND a.first_seen_at<:end
ORDER BY a.first_seen_at,a.article_key LIMIT 100;
```

大窗口按(first_seen_at,article_key)续页。不要将JOIN后每个ticker的一行当作独立文章。补全结果读取使用项目原生接口，而非手工拼哈希文件：

```python
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.live_repository import LiveRepository
repo = MessageBusV2Repository('/data/bus/bus.sqlite3', initialize=False)
live = LiveRepository(repo)
message = repo.read_only(live.load, selected_result_ref)
# message是RawMessageInput形状；只为已选中的少量记录解码。
repo.close()
```

IRRELEVANT是当前规则的结果，不是人工负例真值；NO_PUBLIC_BODY是缺少可判断正文，也不是负例。TEST_DUPLICATE是发布重复，ADMISSION_REJECTED是接纳条件拒绝，CONFIG_INCOMPLETE是词库配置问题，均不能混入“正则不相关”。

## 7. 调词时必须知道的实现约束

**目前没有Massive专属窄词库。** Live处理按ticker读取统一监测词，调用同一个 `regex_relevant`；普通By Distribution也使用它。需求上Massive需要更窄，不代表代码已经自动选择更窄的一套。直接收紧统一L2会影响普通源召回；本说明不授权新增词库机制或直接改生产配置。

当前L2语义：

- 多group之间OR；一个过宽group会使其他group的严格约束失效。
- group内any至少一项命中（如非空），all必须全部命中，none任一项命中即否决该group；none不是跨所有group的全局否决。
- field可以是title、summary、body、all。all拼接三者；默认不区分大小写。literal支持whole_word，regex由开发者显式写边界；单条正则有20ms超时。
- 先做NFC及空白归一化；测试按文章语言选规则，并尝试英语回退，不是只看标题，也不走L1搜索词。
- Massive正则未命中直接IRRELEVANT，不进入Decision Model兜底。配置也明确jev_enabled=false。

建议调试顺序：

1. 按source×ticker×固定时间窗冻结完整正文候选、原规则版本和人工标签，保留已命中与未命中两类，另列正文不可用集合。
2. 用现有relevance函数离线跑新旧规则。先处理明确公司/产品/高辨识度实体，再处理有业务链路依据的间接事件；避免单个行业泛词或ticker短缩写独立通过。
3. 对宽行业条件增加同group的实体/业务动作上下文；利用title字段提高事件主体约束，检查body中的免责声明、名单、市场报告模板误命中。不要一律删除融资、诉讼、监管或竞争对手消息，这些也可能是真正重要事件。
4. 分别报告人工precision、已标注相关样本recall、误判原因和逐ticker/来源差异。只看已发布样本可估命中准确率，不能测漏判；不能拿命中率代替准确率。
5. 单列普通By Distribution对照集，确认共享L2修改的召回影响。若确实需要Massive独立规则，明确提出最小机制变更需求，不假设已有source级词库配置。
6. 调词验证尽量复用已取得的正文，不重新下载新闻、不重跑线上BODY，不把历史测试样本晋升到正式Bus。

## 8. 代码及历史资料入口

- `src/doxagent/message_bus_v2/newswire_sources.py`、`newswire_listing.py`：平台目录、端点及发现方式。
- `live.py`、`live_repository.py`：正文门槛、Ticker判断与文章/投递台账。
- `relevance.py`、`monitoring_terms.py`：正则真实语义及统一词库结构。
- `test_delivery.py`：测试绑定、最终隔离、导出与清理。
- [初版开发方案](massive-distribution-newswire-development-plan-20261103.md)、[启动记录](massive-distribution-implementation-20261103.md)、[HTTP迁移记录](newswire-http-discovery-migration-20261107.md)：历史背景；早期RSS/HTTP受阻状态已被后续修改部分替代，不能当作当前配置。

本说明只新增文档，没有调整词库、源配置、采集服务、消息状态或生产发布行为。
