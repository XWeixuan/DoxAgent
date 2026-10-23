# 消息轮询与正文补全优化：开发与测试交付记录

日期：2026-10-24（北京时间）。依据 `message-source-polling-and-body-completion-optimization-plan-20261023.md`。

## 1. 交付状态与边界

本轮完成代码、配置迁移工具、只读审计工具及本地回归；生产仅进行有界抓取对照、身份状态读取、配置 dry-run 与一次资源采样。尚未发布镜像或 apply 生产配置。

开发测试通过不等于线上 P0/P1 已验收。生产同身份双页的风控表现、九 ticker 自然发现时延及三个交易日新文章 FULL/SHORT_FULL >80%，仍须在正式发布后验证。Reuters/DIGITIMES 的历史 VALID 标签不作为当前权益或 BODY 成功证明。

保留 ARTICLE_ONCE：真实更新刷新原消息和修订记录，UI 显示待核查、核查失败与正文取得时间；不产生第二个 Stream offset，也不重新触发已经消费原文的下游。

## 2. P0：调度与资源竞争

|层级|已实现行为|
|---|---|
|源级|五个目标源及同组成员容量配置为 2；持久调度按到期顺序和实际组容量派发，不先派九个 ticker 等待同一源锁；standalone 调度可独立推进健康 binding|
|Sweep|列表完成即释放 binding/group 轮询责任；正文等待仍保留自己的结算责任|
|分页|页面观察、BODY 任务与 cursor 同事务提交；已见消息先接纳，后续慢页不阻止它们进入补全过程；累计计数不漏计或重复计算|
|Site/Identity|全局 browser、Site、Identity 容量与启动节奏联合准入；没有槽位时不占着另一层资源等待；准入等待最多 3 秒后返回可恢复延期|
|CRAWLER/BODY|全局 browser 默认 4；BODY 常规最多 2，无 crawler 等待时可借第 3 槽；每身份 BODY 最多 1；低优旧文复查不获得前台任务的老化提升|
|Worker|同身份允许两个 execute；每条命令独立 frame ID、任务、配置快照和 runtime 快照；取消只针对该命令；维护/配置写操作等待读任务退出并阻止新读插队|
|身份稳定性|保留原 Chrome、profile、egress、Cookie、probation 与维护原页；配置变更仅 drain controller。HTTP 限流不再清空浏览器活动身份路由|

未新增 Chrome profile、Redis、独立下载平台或额外 SQLite writer 服务。页面槽仍有总量限制，任务结束归还自己的页面与容量。

## 3. 来源路径

|来源|实现与已取得证据|
|---|---|
|Yahoo|30 秒 RSS 发现优先，立即分页接纳；原 browser 每 60 秒补充覆盖。RSS 与原 NCP Cookie jar/session 分离，保留固定路由及端点退避。未知时间元数据每轮最多补查 3 条，轮转失败条目；待核验链接和已证实时间持久保存。列表已经取得的文章 HTML 可供 BODY 质量核查复用|
|GlobeNewswire|已验证的 Site HTTP_PUBLIC 固定出口路径优先；每片最多 2 请求或 15 秒，保留 query/head/tail 进度，连续页下一 tick 续跑；每 4 个成功请求安排旧续页，不将 max_pages 或失败次数当作覆盖完成|
|InvestorsHub|实测 HTTP 被拦截，继续原身份 browser；获得源、Site、Identity 双页容量和 CRAWLER 优先。没有把 HTTP 403 当作空列表成功|
|Investing|保留已有原生 redirect/同 frame response 证据和普通/Pro 完整性门禁；地区 URL 使用明确的 www/in/uk/ca/ng 主机合同，不改写文章域名。地区 HTTP 实测 403，未提升为默认轻路径|
|Barron's|保留现有原生新闻 feed 和 15 秒 shared capture，新增获取时间、消费时间和 capture age 的区分；双页配置适用于实际 source scope 的身份。付费 BODY 不走 Reader|

这些变化没有删除 query、缩小 ticker 订阅或用 RSS 代替 Yahoo browser 补齐责任。

## 4. P1：正文结果、更新与任务生命周期

- 在 Bus 原库增加 `article_body_results`：正文由 NativeContent 承载，结果与 ticker 的发布收据分开。下载键不含 ticker、binding、半小时桶或每次 provider pubDate。
- 首次观察、失败和未发布文章也有 cohort 记录。入队和首次实际获取前分别检查缓存；同文在途者短延期并释放 Hub 槽。9 binding 测试证明一次实际下载、九份独立发布收据。
- 公开文章可按已验证 URL 复用；成功跳转的最终 URL 建立有索引的精确别名。付费/认证访问 scope 与语言隔离；包装 URL 在最终站点未证实前按来源隔离。没有模糊 URL 合并。
- 更新提示按来源保存，避免不同 provider 摘要互相触发检查；pubDate/图片等噪声不代表正文更新。仍在列表上的文章到期进行低优 30 分钟检查，支持 ETag/Last-Modified 与真实 304。
- 内容 hash 未改变时保持原正文取得时间，只更新检查时间；失败不得降级旧 FULL；过期 fetch owner 不得覆盖新 owner 的结果。
- 正文 180 秒从首次实际获取开始计时，首次 claim 可重定位旧队列 deadline；后续真实重试不续命。未开始任务另受 10 分钟 pending 上限约束。
- 终态保留 COMPLETED/FAILED；每次 claim 有独立 execution ID。中间真实失败重试也同时进入 Bus audit 与 Site outbox；缓存命中没有伪造网络执行或新身份能力证明。
- HTTP、browser/browser_fetch、Reader 严格按策略顺序执行并短路；阶段默认预算分别为 8/25/8/12 秒。纯准入/恢复延期不再继续其他回退路径。
- Apple Atom 优先 HTML alternate 主链接，图片 enclosure 单独保存；ETNews 去除 reporter/email 尾部再判断短稿完整性；Investing 地区主机在策略与文档解析两侧一致。

## 5. Read、UI 与可解释性

新增结果、观察和终态任务已注册增量 CDC 与 backfill；纯 seen_count、last_seen 和下载 lease 变化不产生额外业务历史。原消息更新会刷新已有 message projection，不增加 Stream。

主正文指标为首次发现文章 cohort 的 FULL/SHORT_FULL，包含未入流失败项；排除 NOT_APPLICABLE 和懒导入的旧正文。真实网络次数与成功、缓存复用分别显示；旧指标保留 legacy 口径。

轮询区增加近 30 分钟成功采集均值、p95、样本及延期/失败次数；源状态提供分层耗时、采集距今、对应首次接纳耗时和待覆盖 query/待核验时间数。页面先接纳 RSS、随后才完成 browser 的情况下，首次接纳使用对应第一次 capture 时间，避免与最后一次 capture 错配。

## 6. 真实有界抓取对照

下列为 2026-10-23 生产旧版本环境的读取/抓取样本，不能解释为发布后整体收益：

|样本|结果|
|---|---|
|Globe Micron/Intel page 1|经 Site HTTP_PUBLIC 分别 433/620ms；文章 ID 与精确时间数组与浏览器样本一致|
|Globe Micron/Intel page 2|经 Site HTTP_PUBLIC 分别 381/475ms，均取得下一页 10 条；尚未完成 page 2 的浏览器逐条对照|
|Globe 浏览器旧路径|MU runtime 4352ms；INTC runtime 2396ms，加 Site 排队 1344ms|
|InvestorsHub MU 浏览器旧路径|runtime 11825ms，Site 排队 14995ms，端到端 27036ms；独立 HTTP 403|
|ETNews 实际短稿|`20261022000126`，HTTP 200，去尾部后 178 字符，SHORT_FULL/complete_article|
|Investing in/uk/ca/ng 实际文章|独立固定出口 HTTP 均 403；未据此宣称正文恢复|
|Reuters/DIGITIMES 当前状态|存在 session revision、UNKNOWN/not_tested 和 TimeoutError 等不一致；旧 VALID 不能替代当前文章 FULL 样本|

原始证据：`eval/news-light-paths-current.json`、`news-globe-http-coverage.json`、`news-etnews-current.json`、`news-investing-regional-http.json`、`news-auth-capability-current.json`。一次资源基线在 `eval/news-server-resource-baseline.jsonl`，仅为采样点，不是优化后的资源下降证明。

## 7. 测试回执

最终 Python 回归：357 passed、1 skipped、0 failed（191.64 秒）；覆盖 29 个测试文件。机器回执为 `eval/news-optimization-final-tests.txt` 与同名 XML；另一本机真实 Chromium 测试 1 passed。摘要见 `eval/news-optimization-evidence-20261024.json`。

完整回归后的 scope/旧正文导入加固另跑 28 项通过；只读审计和分页/CDC 对账组 4 项通过。跳过项仅因本机 Windows 账户没有创建符号链接的权限。

额外实测和检查：

- `test_news_browser_capacity.py`：本机真实 Chromium、临时测试 profile，同时租用两页；取消一页后另一页仍可读，所有槽位归还。未接触生产 profile。
- `test_identity_worker_multiplex.py`：父级并行、等待准入超时不取消在途页、维护排他、任务快照互不污染。
- `test_news_timeliness.py`：第一页提交时尾页尚未释放、总计数准确、未发布失败进入分母、实际 CDC 投影和 lease/seen_count 去噪。
- `test_body_completion_receipts.py`：九 ticker 一次下载、旧文刷新不重投、真实重试两侧收据一致。
- 前端 schema 生成和 TypeScript typecheck 通过；15 文件、67 单元测试通过。
- Playwright 1262/1559 两种宽度均通过，无页面 JS 错误或横向溢出。使用明确标记的合成 UI 合同 fixture，其 84% 数字不是生产成功率。

## 8. 配置迁移与发布步骤

`scripts/ops/configure_news_latency.py` 默认只读 dry-run；已对当前生产生成 `eval/news-configuration-final-receipt.json`：5 source、9 Yahoo binding、5 Site、10 个实际路由身份。只配置有效 source scope 的身份，不连带提升未使用的历史 profile；维护/不健康/probation 身份保留跳过原因。apply 使用原 Owner/CAS，逐项记录旧/新 revision，不覆盖并行维护版本。

正式发布顺序：

1. 保存当前组件镜像、配置 receipt 与资源基线；核对 `storage-status` 的实际组件登记名。
2. 先发布兼容终态过滤和新 Read/schema 消费者的版本，再启用终态 writer。Read 按既有 CLI 的 `projection-indexes` 入口执行 `migrate_projection_indexes`，注册新 Bus outbox 表并 backfill 新表；不扫描百万旧 CDC 做前置重建。
3. 用 `scripts/ops/storage_release.py` 正式镜像发布受影响的 Site、Bus、Enrichment、持久 scheduler 和实际 API/Read/web 消费者。Site 父子 Worker 必须使用同版协议；不重启 Chrome Supervisor、不换浏览器版本或 profile。
4. 先 dry-run，再通过 `configure_news_latency.py --apply` 应用 receipt 中的窄配置；需要生产 Bus/Registry 路径、Site URL 与 admin token 文件。脚本无 apply 参数不会写生产配置。
5. 验证实际五源与九 ticker 路由、两页重叠/精准取消、HTTP/原 browser ID/时间/页覆盖以及普通/Pro BODY。来源权益未恢复项继续记失败，不阻塞健康源 P0。
6. 只读运行 `audit_news_timeliness.py --bus ... --read-store ... --since ... --until ... --out ...`；单窗口最多三天，历史轮询最多 20000 行并显示截断，按 binding 给出真实 capture 间隔、首次接纳及阶段耗时。可传 Linux `--pid` 采集 RSS/CPU 累计计数；不混同嵌套阶段百分比。

## 9. 回滚与尚待验收

- 身份挑战或取消串扰增加：只把受影响 identity 退到 1，保留健康源。
- BODY 导致 crawler 发现 p95 在两个 5 分钟窗恶化 >10%：先关闭 BODY 借第 3 槽，再暂停低优复查新增 browser 准入；保留 pending 责任。
- HTTP 覆盖不等价：关闭该 endpoint 的 `http_listing_verified`，回原 browser；不改变其他已验证路径。
- 回退程序前使用本版兼容消费者；可通过 `DOXAGENT_MESSAGE_BUS_ENRICHMENT_TERMINAL_RECEIPTS_ENABLED=false` 停止保留新终态，但不能回退到把所有 job 行当 pending 的旧消费者。保留正文、审计和修订证据。

仍待正式发布后的业务验收：生产原身份双页稳定性、Globe 更多 query/page 的持续覆盖、Yahoo DOM 独有项及最老待核验条目、Investing 地区普通/Pro FULL、Reuters/DIGITIMES 当前身份真实 BODY 能力，以及三个交易日的 P0 时效与 P1 >80% 稳定性。
