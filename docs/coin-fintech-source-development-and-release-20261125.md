# COIN 行业消息源接入与发布验收

> 2026-11-26 更正：本报告按 Age 推断“列表过旧/不通过实时验收”的结论证据不足，新增生产失败门禁已撤销。后续浏览器/HTTP 实际列表对照未发现 CFTC、Deribit 与 The Defiant 已核对的 TradFi 栏目的内容差异；Unchained 原生请求已修复。历史发布回执保留，当前结论请见 [本轮更正报告](fintech-source-freshness-correction-20261126.md)。

2026-11-25。按用户确认，本次完成发布及 COIN 订阅预配置；COIN 仍走现有初始化完成后的正式启用流程，不提前创建监测状态、不调用下游研判、不启动交易。

## 实现范围

- 新增 14 个外部消息源，内部拆为 25 个独立列表任务；同站同板块的多个栏目合并为一个外部源。
- 沿用当前 20 秒周期、交易日历、共享 HTTP 资源预算、分源调度、列表检查点、去重、正文补全与普通分发链路。没有新增服务、数据库或 Chrome 身份。
- 企业 IR 聚合源增加 `coin.blog`、`coin.ir`、`coin.deribit` 三个内部渠道，均为 20 秒。既有 13 个渠道的参数和限频保留。
- COIN 准备 16 个绑定：上述 14 源、既有企业 IR 聚合、既有 X Official Stream。其他 ticker 不新增订阅。
- X 预设 `faryarshirzad`、`coinbase`；首次正式配置统一监测词时填入，之后用户显式清空仍然有效。无须新建 X 连接。
- 同步消息源分类目录，金融主体归 L1-09；BleepingComputer 同时覆盖安全行业。

## 原站勘察与抓取决策

下表条数为 11 月 25 日真实网络诊断中单次列表结果，不代表新消息数量。FULL 是选取的真实正文样本结果，不代表各源生产长期成功率。

| 外部源 | 实际列表路径与范围 | 单入口条数 | 正文与限制 |
| --- | --- | --- | --- |
| `theblock_crypto` | 官网 `/news` 的只读 POST，Nuxt 结构化文章；排除非 post 节点 | 13 | 公开样本 FULL；Pro 标记保留订阅限制。使用官方 `tbcoPublished`，不采用相差约一分钟的另一 CMS 时间字段 |
| `coindesk_crypto` | `/policy`、`/business`、`/tech`，只读 POST；Finance 的实际路径为 business，finance 路径 404 | 各 16 | 三入口真实正文均 FULL；提取卡片 time，不扩展无关栏目 |
| `rg_prediction_markets` | `/news/prediction-markets` 只读 POST、页面 RSC 与栏目卡片交叉校验 | 19 | FULL；修复作者简介 Show More 导致的正文未展开误判，保留原始公开/订阅证据 |
| `unchained_crypto` | 查明页面实际使用 ALM AJAX GET 分类过滤；正确过滤请求受限，暂保留 regulation/business/defi 各自 RSS 兜底 | 各 10 | RSS 原生全文样本 FULL；HTTP 状态明确失败，不能认作 HTTP 实时迁移通过 |
| `decrypt_crypto` | News、Business、Technology、DeFi 四页只读 POST；仅解析 NextData 的 ArticlePreviews 查询 | 各 12 | 四入口样本 FULL；忽略无关 hydration 推荐记录 |
| `cointelegraph_crypto` | 三个指定 tag 的只读 POST；Nuxt/URQL 文章与实际 tag 卡片取交集 | 14–15 | 三入口样本 FULL，避免菜单/缓存中其他 tag 的文章混入 |
| `bankingdive_fintech` | Fintech 分类页，stdlib GET；请求桶参数实测使旧缓存 HIT 变为 MISS | 22 | FULL；缺少精确日期的卡片保留 UNKNOWN_FIRST_SEEN |
| `bleepingcomputer_crypto` | Cryptocurrency 分类页 GET，既有固定美国 HTTP 出口 | 20 | FULL；发布日期/时刻按纽约时区解释 |
| `sbcamericas_prediction_markets` | 分类页 HTTP 受限，保留精确 prediction-markets RSS | 10 | 原生正文 FULL；HTTP 失败/RSS 兜底，不开浏览器 |
| `ledgerinsights_news` | News 分类页只读 POST，限定 category-news | 8 | 公开文章 FULL；实测发现长 Pro 预览曾可被通用长度规则误判，已按站点订阅提示修正为 UNAVAILABLE/subscription_required |
| `thedefiant_fintech_regulation` | 两指定栏目只读 POST，仅 Recent articles，排除 Videos 与其他主题 | 各 10 | 两个选取样本明确订阅受限；列表重复请求仍见 326/4727 秒 Age，不承诺实时通过 |
| `cftc_trading_rules` | 原生列表 GET，COIN、KEX 两个组织过滤；本地再次逐行核对组织 | 各 50 | 真实 COIN filing 67740 的两份 PDF 合并为 FULL，约 16,408 字符；一个入口 Age 330 秒，另一个 Age 0，缓存风险仍在 |
| `cftc_press_releases` | 官方 Press Releases 表格，stdlib GET | 37 | 正文 FULL；列表 Age 11,848 秒，不能作为及时发现验收通过 |
| `occ_newsroom` | 官网前端实际 NewsItems/Search POST；五类 NewsTypes 同时传给服务端并本地二次过滤 | 40 | HTML FULL；增加官方演讲/证词直接 PDF 读取；Alert 不入选 |
| 企业 IR / Coinbase Blog | 官网公开 Contentful 入口，Most Recent 对应 EditorialTemplate、日期倒序，仅投影 id/title/slug/date | 30 | 真实公开正文 FULL；无需凭据。响应从约 539 KB 降至约 7.5 KB |
| 企业 IR / Coinbase IR | 官方 Q4 PressRelease JSON API | 30 | FULL，约 17,207 字符；Q4 公告时刻按纽约时区转换 |
| 企业 IR / Deribit | Exchange Updates 页面，只提取本栏文章及 time | 24 | 公开样本 FULL；CloudFront 静态列表 Age 34,854 秒，缓存门禁拒绝当健康实时列表 |

## 缓存与真实时效边界

列表使用现有缓存证据机制，新增入口的可见 Age 超过 30 秒时失败，不以 HTTP 200 覆盖失败。没有 RSS 的源不会去请求虚构 feed；有 RSS 的两源在兜底输出时仍保留 HTTP 失败、覆盖 PARTIAL。缓存失败不阻塞其他源/栏目，也不把旧正文缓存年龄与列表实时性混为一谈。

POST 只用于网站只读列表或文章读取。实测已证明 POST 不能普遍消除上游缓存，The Defiant 就是反例。也没有把 no-cache 头当作缓存绕过已生效的证明。

Coinbase 原始 landing HTML 多次出现 Cloudflare challenge/地区跳转。前端代码明确使用 `contentful.coinbase.com`，其公开 API 无须认证。裸查询复测 Age 161 秒；前端支持的 `cache-version` 只使外层 MISS，上游仍 HIT，未采用为解决方案。首轮 `sys.updatedAt[lte]` 同样只影响外层，使用 1970 年控制查询仍返回 1815 条，确认被代理忽略，已移除。进一步实测有效的 `fields.publicationDate[lte]=1970-01-01` 返回 0 条、`[gte]=2026-11-24` 返回 1 条、不存在的 slug 返回 0 条，三次 CDN/上游均 MISS。最终采用本次时刻作为真实 publicationDate 上界，只读已发布记录；实际当前上界查询也为双层 MISS，返回的近 30 条 ID 及顺序与原始查询完全一致。不使用被忽略的系统字段或随机参数；实际发布时间→首次发现验收仍未通过。没有访问 Contentful Preview 或使用私有令牌。

Unchained：空壳分类页面不能证明发现覆盖；不带有效分类的 AJAX POST 会返回全站五条消息，也未采用。正确原生分类 GET 受限；分类 RSS 实测 Age 322/1112 秒，早期还出现逾一小时缓存。SBC 分类页 HTTP 同样受限。若后续必须提升这两站 HTTP 覆盖，需要用户评估是否采用现有 Chrome。CFTC、Deribit 的核心证据是上游缓存，不能据此保证加浏览器就解决。

CFTC 官网已公告 2026 年 10 月起 RSS 暂时停用，因此不把返回 HTML 的 RSS 样式 URL 当有效兜底。官方说明：[CFTC RSS](https://www.cftc.gov/RSS/index.htm)。

**本轮没有正式 COIN 监测状态，也没有自然新消息发布到 Bus。发布→首次发现→接纳→Bus 的真实延时验收尚未通过，待正常初始化启用后按既有持久台账统计。** 不把请求耗时、无 Age、MISS 或抽样正文 FULL 代替这项业务验收。日期级及 UNKNOWN_FIRST_SEEN 消息不冒充精确发布时间，不纳入精确发布延时统计。

## 正文、去重及附件完整性

列表任务不串行等待正文。使用现有正文任务台账、质量标记、文章复用和 ARTICLE_ONCE 语义；同 URL 跨栏目重叠由既有去重处理。发行方更新时刻和 CFTC 申报字段版本进入标准身份/版本证据。

CFTC detail 必须与申报 ID/组织匹配，并核对关联附件数量。全部 PDF/HTML 附件读完后才给 FULL，保留费用、金额、抵押和合同条款，不做摘要压缩。相同附件仅按文件哈希合并；任一附件失败、格式不支持或扫描页无可读文本，保留 PARTIAL/UNAVAILABLE，不静默丢掉附件。OCC 原生 PDF 同样要求全部页有可提取文字。

RSS `content:encoded` 只在同一文章 URL 且普通完整性校验通过时复用；摘要不算全文。付费文章无订阅时仍被发现和保留访问原因，不伪造完整正文。

## 配置、回归与发布回执

配置入口 `scripts/ops/configure_fintech_sources.py`：先预览，再通过现有 Bus owner API 幂等写入；IR 仅追加三渠道；同时准备 live 和当前初始化 candidate，避免正式安装 candidate 覆盖预配置。记录私有 before 快照和安全 receipt。操作前后 COIN 监测状态必须相同。

真实网络脚本 `scripts/ops/accept_fintech_sources_20261125.py --all-entries` 只做读取与原链路正文诊断；`scripts/ops/check_fintech_listing.py` 则直接调用生产 ListingAdapter、真实缓存门禁和物理请求证据检查。两者均不写 Bus，不创建独立测试处理体系。

本轮相关回归 263 passed，1 deselected；新接入专属回归 16 项全部通过，Ruff E9/F 与 diff 检查通过。扩展检查发现四个已有断言失败（Reuters 查询数、Yahoo 既有周期、两项 recheck 领取），在未修改的 origin/main `a647b61a7` 隔离副本上同样复现；未改动无关业务来迁就断言。正式回归仅排除其中所包含的旧 Reuters 断言。

以下为实际生产回执。原始 HTML、全文、站点前端配置和诊断包不提交 Git；安全回执仅保留标题、URL、时间精度、条数、缓存头、质量与附件哈希。

首轮生产发布代码 `26435872002e2b4da187a673127ca345c701bde2`，四组件相同镜像 `sha256:6ccaee2bf2be…`，运行中源码哈希与提交工作树一致，四者重启计数均 0；API、Collector 的 Docker health 为 healthy，其余两角色未配置 Docker health。初始化、Scheduler、Site Access 与 Chrome 未更换。

已通过现有 owner API 写入：live 的16个批准绑定全部 enabled；candidate 共25个绑定，含16个本次绑定与9个原有默认绑定；两处 IR 均 v6/16渠道；COIN 均 NOT_STARTED、无统一监测词。当前初始化 `init-coin-0a7ab32b3e9d4b2db5dae780a52dfe03` 未替换。X 已恢复 CONNECTED，实际规则仍为原十 ticker 的十个账户，COIN 两账户仅预设，未提前扩展订阅。

生产 ListingAdapter 实调25入口：16个通过缓存与物理请求证据校验；4个 RSS 兜底入口保留 HTTP_LISTING_FAILED_RSS_FALLBACK；其余5个（The Defiant 两栏目、CFTC 三入口）按实际过旧缓存返回 HTTP_LISTING_FAILED。没有把兜底或旧缓存伪装成健康轮询。COIN 三个 IR 的公开正文样本均 FULL，Deribit 列表继续因 Age 35516 秒失败。OCC 真实官方 PDF 全文6370字符、FULL。

CMS 控制查询修正后，相关回归再次50 passed（与263项有重叠，不相加）。后续代码 `00c493602769332d43039941ccac92691faf7bba` 已仅发布 Collector/BODY，镜像 `sha256:03e271a348fd…`；Bus/API 保持首轮代码。最终四角色运行且重启计数0，Collector/API healthy。再次实测 Coinbase Blog 30条、CDN与上游均MISS、FULL；Q4 30条、FULL；Deribit 24条、FULL正文但列表 Age 36042 秒，依然不通过实时列表门禁。live/candidate 再次核对16个批准绑定及 NOT_STARTED 不变。

安全证据目录：`artifacts/fintech-sources-20261125/`，包括 `production-listings.jsonl`、`fintech-production-ir-final-20261125.jsonl`、`fintech-production-release-final-20261125.json`、`fintech-verification-final-20261125.json`、`production-cms-final.jsonl`、`production-documents.jsonl` 与测试输出。生产保护的配置前快照及 owner receipt 位于 `/data/bus/ops/fintech-sources-20261125/`。

交付状态：代码、分类、生产发布、COIN预配置已完成；真实自然新文章的发布时间→Bus时延验收未完成。Unchained/SBC 的HTTP恢复需要后续浏览器方案评估；The Defiant/CFTC/Deribit 的缓存问题以及付费文章完整访问仍有明确限制，不属于本轮已解决项。
