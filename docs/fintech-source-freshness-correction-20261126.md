# Fintech 消息源内容新鲜度复核、修复与生产回执

2026-11-26，北京时间。调查起于11月25日晚，现场时间均保留UTC原值，展示延迟时按同一时间轴计算。

## 结论与上一轮更正

上一轮以 Age 超过30秒判定这批列表失败，并据此推断 CFTC、Deribit、The Defiant 列表滞后，证据不足。这项新增生产限制已撤销。Age、Date、Cache-Control、缓存命中标记继续作为诊断记录；它们不能单独证明文章缺失或消息发现滞后。

实际浏览器与 HTTP 结果对照显示：CFTC Press Releases 的37条、COIN规则50条、KEX规则50条、Deribit Exchange Updates 的24条完全一致。The Defiant TradFi 栏目 Recent articles 的10条也与 HTTP 一致，最新 Coinbase 稿件已经在 HTTP 结果中。没有发现这些已核对列表存在浏览器可见而 HTTP 遗漏的新文章。

Unchained 的 HTTP 入口确有实现问题：上一轮没有完整复现前端 ALM AJAX 参数，且解析器错误地读取 `<li>`，实际卡片是 `.archive-item`。已修复，三个栏目各9条与正常浏览器各9条完全一致，恢复 HTTP 主路径，保留现有 RSS 故障兜底。

这证明了内容对照及具体实现修复；**尚未取得修复后新发布文章的连续对照样本，不能宣称 HTTP 的发布→首次发现延迟已经量化下降，或已满足某个秒数 SLA。** 没有用 HTTP 200、执行耗时或缓存 MISS 替代这项验收。

## 实际内容对照

测试使用本机正常有界面的 Google Chrome，单个临时持久化测试 Profile，无登录、指纹伪造或挑战绕过。HTTP 使用生产既有固定出口与原采集实现。浏览器和服务端网络位置不同，所以本轮结论限于文章集合/标题及现场可见机制，不将网络差异归因为某个具体风控因素。测试浏览器已关闭，没有新增生产 Chrome 身份或常驻浏览器任务。

| 来源/入口 | 浏览器结果与 HTTP 对照 | 已确认机制、处理 |
| --- | --- | --- |
| CFTC Press Releases | 37/37，URL集合完全一致；HTTP Age4452秒 | 可见新闻在服务器返回表格中，未发现另外加载更新新闻列表的XHR。撤销新增 Age失败条件，保留原 stdlib HTTP 路径 |
| CFTC COIN规则 | 50/50，完全一致；Age804秒 | 本地逐行核对COIN组织、详情ID及附件计数逻辑保留 |
| CFTC KEX规则 | 50/50，完全一致；Age834秒 | 本地逐行核对KEX逻辑保留；正文仍读取全部关联附件 |
| Deribit Exchange Updates | 24/24，标题、URL及可见日期一致；Age2070秒 | 前端有 Next/RSC 导航预取，但未发现这次对照中有比HTML多出的新文章。保留轻量HTML采集，不因Age更换浏览器或枚举其他入口 |
| The Defiant TradFi & Fintech | Recent10/HTTP10，最新稿一致 | 页面另外3条属于2022–2024年旧Videos，现有解析器排除正确；不把Videos当漏抓 |
| The Defiant Regulation & Politics | HTTP10条正常解析；浏览器遭Cloudflare限制，未取得正常渲染对照 | 不给该栏目作“浏览器与HTTP已完全一致”的结论；也不能从挑战页推断列表过期。保留当前可工作的只读HTTP POST |
| Unchained Regulation | 修复后浏览器9/HTTP9，缺失0，多出0 | 完整原生AJAX请求、正确归档卡片解析 |
| Unchained Business | 9/9，缺失0，多出0 | 同上，使用对应实际分类ID |
| Unchained DeFi | 9/9，缺失0，多出0 | 同上 |
| SBC Americas | HTTP与本次普通Chrome均被挑战页限制，未取得正常前端 | 用户明确要求“无法通过，暂时先不要管了”，已停止进一步尝试；保留既有RSS，不新增Chrome方案，也不依据RSS或Age宣称网页时效已验收 |

CFTC/Deribit 的配对抓取约相隔70–82秒，The Defiant TradFi约108秒。因此“完全一致”是两次有明确时刻的现场快照结果，不能放大为零延迟或长期100%覆盖。原始时刻保存在证据文件。

刚开始曾根据Top Stories位置怀疑The Defiant置顶漏抓；成对HTTP结果证伪了这个判断，已向用户更正，没有为错误判断改动解析范围。

## Unchained 具体修复

正常分类页会在浏览器中发出 `/wp-admin/admin-ajax.php?action=alm_get_posts`。完整请求除了 taxonomy、order 外，还包含对应的 `post_id`、`slug`、`canonical_url`、原始offset、repeater、分页状态等。实际栏目配置为 regulation=8、business=2634、defi=5，首页9条。

上一轮的简化URL无法代表这条原生请求，现场持续403。完整的浏览器请求使用 curl 与 urllib 都返回JSON；加Referer并不是成功所必需的。没有依赖登录Cookie、Cloudflare clearance或伪造浏览器身份。生产采用已逐栏目实测的请求构造。

JSON中的HTML是 `.archive-item` 卡片，标题在 `h3/a`，日期在 `.group-date`；旧解析器查找 `<li>` 也会把正常JSON误当空列表。已改为真实卡片结构，限定卡片范围以排除其他widget。归档只显示日期，继续标DATE，不将午夜当精确发布时间；后续正文可提供官方datePublished。

使用现有外部源 `unchained_crypto` 和三个稳定内部FEED身份，不增加消息源、队列、数据库、浏览器或框架。三个栏目仍独立调度20秒；某一栏目失败可降级自己的对应RSS，且保留真实HTTP失败原因。

## 真实发现样本与后续接纳阻断

从生产台账与官方RSS对应文章时间取得的两例自然文章如下。这两例发生在修复前的既有RSS兜底路径，不能算修复后HTTP性能成绩。

| 样本 | 官方发布时间UTC | DoxAgent首次发现UTC | 差值 |
| --- | --- | --- | --- |
| Unchained 乌克兰加密市场法案 | 2026-11-25 15:45:35 | 15:46:16.495350 | 41.50秒 |
| Unchained 法国参议院税收法案 | 2026-11-25 15:30:50 | 15:32:34.389507 | 104.39秒 |

这里只能说明当时该RSS路径的这两例实际发现延迟，不能证明高Age导致分钟级延迟，也不能证明换HTTP必然更快。修复后需要继续使用既有首次发现/来源台账观察新文章；本轮不另建持续测试体系或注入历史消息。

还有两个与列表缓存不同的真实阻断：

- COIN目前已有running状态和25个绑定，但统一监测词仍未配置。上述已取得FULL正文的两篇文章停在 `CONFIG_INCOMPLETE / TERMS_MISSING`，没有进入Bus。这是发现后的配置接纳问题，不能算列表发现滞后。本轮保留正常监测状态，不编造财务/相关性监测词，待按既有配置流程补齐。
- The Defiant最新Coinbase文章已在23:23北京时间进入首次发现台账，但正文结果为 `UNAVAILABLE / subscription_required`，交付为 `NO_PUBLIC_BODY`。这属于订阅正文能力限制，不是这篇新闻未被列表发现。完整正文门槛保持不变，没有用预览冒充全文。

## 生产撤销范围与验证

1. 新增14个Fintech源的缓存证据改为非阻断诊断；30秒只留作观察提示。HTTP状态码、列表结构、栏目/组织范围和普通去重校验保留。
2. 删除本轮为COIN三个IR渠道增加的 `max_listing_cache_age_seconds` 参数与运行时失败逻辑；live和当前初始化candidate均已通过owner API窄范围更新，IR源v6→v7。其他13个IR渠道、各ticker绑定、已有其他源策略不变。
3. 先发布撤销修复 `cb79d615045b9c2e386e151fe3292ef64a07cef8`，然后将Unchained原生HTTP修复 `762d7c72a26c36194d5bf279a6db62a370376198` 发布到Collector、BODY、Bus、API，确保实时及旧闭市补抓路径均不继续使用本轮新增门禁。
4. 更新Unchained分类目录，保留HTTP主列表/RSS兜底，正文按实际路径标注；原11月25日报告顶部已加明确更正，历史发布事实不抹去。

相关集成回归188项通过；另做撤销范围、幂等、原生参数、卡片过滤等19项专属回归通过，目录10项通过（集合有重叠，不累加）。Ruff E9/F与diff检查通过。

最终四个生产组件均运行 `762d7c72a26c36194d5bf279a6db62a370376198`，镜像 `sha256:f27b38c81e6b…`；源码哈希与提交工作树一致，重启计数均0，Collector/API health为healthy，Bus/BODY未配置Docker health。初始化、Scheduler、Site Access、Chrome服务均未发布或替换。

发布后直接调用生产ListingAdapter：Unchained三个栏目各9条、各有真实物理请求校验证据、无失败；对应最新公开文章正文三例均FULL，经HTTP正常取得。生产正常调度台账中，关注的11个内部任务（Unchained3、The Defiant2、CFTC3、COIN IR3）最新完成记录均为CAPTURED。live/candidate撤销脚本再次预览，两处待删除门禁参数均为空，source v7，COIN现有running状态保留。没有用手工诊断结果冒充业务发布回执。

## 证据

`artifacts/fintech-freshness-20261125/`：`content-comparisons.json`、`unchained-content-comparison.json`、`natural-latency-samples.json`、`age-gate-withdrawal.json`、HTTP采集/运行状态/发布后回执与测试输出。

正常浏览器原始DOM、Cloudflare挑战资源、网站完整JSON/正文和Profile不提交Git；归档仅保留必要的公共标题/URL、计数、时刻及状态。SBC后续按用户指示暂停。本轮没有足够的修复后新文章样本证明长期发现时延改善，明确保留该验收边界。
