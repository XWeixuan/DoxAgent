# COIN 统一监测词应用、消息源启用与运行观察

日期：2026-11-26。以下时间除明确注明外均为北京时间。

## 结果

已应用用户提供的 `COIN_unified_monitoring_terms.json`，生产监测词 revision=1；已为 COIN 绑定并启用全部 34 个适用的行业与通用消息源。COIN 保持原有 running 状态，未重新触发初始化。

完成 00:39:18–00:53:34、共 14 分 16 秒的真实生产观察。观察期间 COIN 有 46 条投递进入 Message Bus，其中包含监测词启用后释放的待处理消息，不能视为 46 篇自然新发布文章，也不能据此证明发布到入 Bus 的时延达标。

用户随后明确：先完成绑定和启用，通用消息源既有阻塞无需处理。因此停止进一步处理 Bloomberg、Business Wire、GlobeNewswire 等通用源的既有正文问题，保留真实失败状态，不将其描述为全部健康。

## 配置核验

- 上传文件 SHA256：`7706242e954558eda015e4b2004fdebec74886c9339fb2bd939e31db4ca97d24`。
- 生产配置与原文件通过同一配置模型规范化后计算 SHA256，结果一致；未改写正则或自行调整相关性规则。
- 配置包含 19 个普通监测词组、15 个 Massive 监测词组和 3 个 L1 概念；实际绑定所需语言为英文。
- 9 个 X 账号：`coinbase`、`coinbasenews`、`cbfutures`、`coinbaseinsto`、`coinbaseintexch`、`base`、`brian_armstrong`、`faryarshirzad`、`iampaulgrewal`。
- 最后一次生产读取确认：34 个 Source 均 enabled，34 个 COIN binding 均 enabled，绑定的 polling.enabled 均为 true。X 使用既有长连接，实际不通过轮询抓帖子。
- 保留各来源现有轮询频率。企业 IR 沿用聚合源中的 COIN 渠道。

原有 25 个绑定保留，补齐下列 9 个通用源绑定：

`access_newswire_massive`、`bloomberg_massive`、`businesswire_massive`、`globenewswire_massive`、`google_news_search_rss`、`prnewswire_massive`、`stocktwits_messages`、`thefly_ticker_news`、`tmx_newsfile_massive`。

当前生产的五个 Newswire 和 Bloomberg 均已是 production delivery，不再是历史上的测试隔离配置。空的 `newswire_rss` 是未配置 RSS URL 的禁用通用适配器模板，不是实际消息源，因此未将其启用；未订阅与 COIN 无关的半导体、能源、量子专属源。

完整 34 个来源 ID 见 [启用核验回执](../artifacts/coin-activation-20261126/acceptance-summary.json)。

## 观察结果

| 项目 | 真实结果 |
| --- | --- |
| 观察时长 | 856.185 秒，14.270 分钟 |
| COIN 有轮询执行记录的来源 | 33 个，另有 1 个 X 长连接来源 |
| 至少出现一次 CAPTURED 的轮询来源 | 31 个；不代表每一轮都成功 |
| 未出现成功主列表记录的来源 | Benzinga、SBC；原因见下文 |
| 监测词配置阻塞 | 未再出现 TERMS_MISSING / CONFIG_INCOMPLETE |
| COIN 入 Bus 投递 | 46 条，含启用时释放的积压 |
| X 连接 | CONNECTED、HTTP 200；全局 19 个账号包含 COIN 的 9 个账号 |
| 观察期自然新 X 帖子 | 未观察到 COIN 新帖，不能宣称新帖端到端验收通过 |

46 条投递的来源分布：RG 21、Unchained 8、Banking Dive 5、Decrypt 2、Google News 2、Ledger Insights 2、The Block 2；CoinDesk、Cointelegraph、SBC、Yahoo 各 1。

共享采集队列在该观察时间范围内有 78 个 ACCEPTED capture、无待接纳 capture；此统计是共享采集存储口径，不是 COIN 独占计数。采集、BODY、相关性筛选和最终投递是不同阶段，不能将这些数字混用。采集执行耗时也不等于文章发布时间至首次发现耗时。

## 本轮已经完成的修复

1. **Investing COIN 缺少新闻入口配置。** 原错误为 `investing_ticker_locator_missing`。仅将 COIN 绑定的 listing_url 配为 `https://www.investing.com/equities/coinbase-global-news`，未改其他 ticker 或 Chrome 身份；修复后观察到 19 次 CAPTURED。窗口中的 9 次旧失败仍保留在审计记录。
2. **Stocktwits 供应商新增必填 start 参数。** 供应商返回明确的 HTTP 400 参数错误。请求改为使用本轮/恢复窗口起始日期；没有显式窗口时回看 30 分钟，避免跨午夜漏覆盖。两组边界回归及既有适配器回归共 3 项通过。代码提交 `4d288864a71280d16fe3580b5f26db938e41d47a` 已进入 origin/main，并通过现有发布机制部署 Collector 和 Message Bus；发布后连续观察到 4 次 CAPTURED。

IBKR 初期两次数据源不可用随后自行恢复，窗口累计 39 次 CAPTURED，未改网关、客户端 ID 或交易模块。

生产镜像核验时 Collector、Message Bus 为 `4d288864a`；BODY 与 API 保持先前的 `762d7c72a`。四个进程均 running、重启次数 0，Collector/API 的 Docker health 为 healthy。配置通过现有 owner 服务应用，无额外常驻服务或定时检查器。

## 保留的已知限制

- **Benzinga：** 按官方 token 认证仍返回 401 Access denied；用户已确认暂无可用凭据。保留订阅与启用配置，真实标记失败，未伪装为成功。
- **Bloomberg：** 列表可以采集，现有持久化身份的人工验证任务阻塞 BODY。已打开原验证任务；根据最新要求停止后续验证处理，未切换身份或出口。
- **Business Wire：** 存在原生短金融表格正文被完整性检查判为 PARTIAL 的样本；尚未修改正文判定。
- **GlobeNewswire：** 有 PARTIAL 样本及 Reader 回退 401；不能将回退 401 等同于原始发布网站需要登录。根据最新范围停止进一步修复。
- **SBC：** 原 HTTP 入口阻塞、RSS fallback 仍可产出；沿用用户此前暂停浏览器验证的决定，HTTP 失败状态未隐藏。
- **The Defiant / 部分其他发布商：** 仍有付费正文或访问限制；不通过降低 FULL/SHORT_FULL 标准掩盖。

这些限制不影响“绑定并启用”的完成状态，但意味着不能宣称全部源的正文或端到端抓取均已健康。此次短观察也不足以给出长期稳定性或所有来源的真实发现时延结论。

## 回执与复核入口

- [启用与观察摘要](../artifacts/coin-activation-20261126/acceptance-summary.json)：原文件校验值、34 个启用来源、观察区间与发布计数。
- [生产镜像回执](../artifacts/coin-activation-20261126/release-verification.json)。
- [激活脚本](../scripts/ops/coin_activation_20261126.py)：通过现有 MonitoringTermsService / MessageBusV2Service 应用配置，保留原绑定和节奏。
- [有界只读观察脚本](../scripts/ops/observe_coin_activation_20261126.py)：采集周期、投递及 X 连接；输出对错误 URL 中的 token 等参数脱敏。

原始监测词、完整配置备份、网页与挑战会话材料未加入公开 Git；生产操作回执保留在 `/data/bus/ops/coin-activation-20261126/`。
