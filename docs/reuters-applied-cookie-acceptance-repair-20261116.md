# Reuters：APPLIED Cookie 接受链专项复核与生产身份替换

**收尾结论：新身份在真实生产负载下也复发，新增自然付费任务再次出现完整交接后 API401。本轮没有实现持续恢复，已回退原生产主备，保留新身份与现场，Backup继续运行。不能把下文的中途成功当最终验收通过。**

日期：2026-11-16。承接 `reuters-applied-401-forensics-and-targeted-plan-20261113.md`。本次不改等待/重试时间、不清 Cookie、不扩大代理白名单；使用已部署取证和少量实际业务对照。下列时间为 UTC。

## 已证实的失败环节

11-12 23:24:40 取证发布后至本次 11-16 14:08 的完整留存窗口，共有 **17 次真实 APPLIED**，不是人工重新购买来补样本：

| 环节 | 结果 |
| --- | --- |
| Provider Cookie 指纹 = Chrome 写后指纹 | 17/17 |
| 同一指纹随原页文档导航实际发送 | 17/17 |
| 同一指纹随首次原生搜索 API 实际发送 | 17/17 |
| Chrome 阻止该 Cookie 的 blockedReasons | 全部为空 |
| 原页 HTML 响应 | 17 次 HTTP 200 |
| 首次 `/pf/api/v3/content/fetch/articles-by-search-v2` | 16 次 HTTP 401、1 次 HTTP 200 |
| 确认窗口内真实业务验证 | 1/17，5.88% |
| 留存已知费用合计 | $0.02465 |

失败 API 的原始响应有 `x-datadome: protected`、DataDome bootstrap，CloudFront `LambdaGeneratedResponse`；不是仅凭 401 猜测。失败请求已经携带 Provider 的原值，**不同值的 Set-Cookie 出现在这次拒绝响应之后**。不能再把这16次失败归为首次请求前 Cookie 被覆盖、CDP 未写入、Domain/SameSite 导致未发送或路由到另一个 Profile。HTTP 200 的文档也不能算搜索会话已经通过验证。

代表失败 Main：task **84433538786**，11-16 11:58 应用，原页与 API 均携带同一 Provider 指纹，随后 API 被 DataDome 401 拒绝。代表成功 Backup：task **84406495239**，11-13 05:58:33 应用，API200，05:58:54 `VERIFIED_IN_WINDOW`。成功与失败的返回 Cookie 属性及相关 UA/语言/Client Hints 没有发现可以解释分歧的差异。

**可准确定位的边界是：本地交接链成功，受保护业务 API 不接受该 Cookie/会话上下文。** Provider 私有浏览器中的验证码验证端点是否接受了答案、它是否还有未返回的浏览器状态，不在 API 回执和 CONNECT 日志的可见范围内。不能据此断言供应商答案错误，也不能假设本地遗漏了一个验证 POST。公开协议返回 Cookie，现有客户端确已按协议写入并发送。

16 个确认失败中，部分数小时后由后台 probe 恢复，另有人工恢复；保留它们的 `background_probe/manual` 归因，没有倒算为打码成功。逐笔证据见 `eval/reuters_applied_repair_20261116/chain-summary.json` 和原始脱敏 `ledger.json`。

## Squid CONNECT/403 结论

逐任务关联既有域名日志，DataDome 核心域名 `geo/static/ct.captcha-delivery.com`、`dd.reuters.com` 与 `www.reuters.com` 均有认证成功的 CONNECT/200，没有它们被 ACL403 拒绝的记录。

403 的目标主要为 Google、广告、OneTrust/CookieLaw、`ip-api.com`、`worldtimeapi.org`。**唯一业务验证成功的任务也有这些相同域名的403**，因此它们不是单独足以导致全部失败的条件。未找到“必要验证码依赖被阻断”的正面证据，没有放开 Google/广告，也没有用 TLS MITM 读取验证码内容。CONNECT200仅证明隧道，不等于 HTTPS 内验证端点的接受回执。

原 Main/Backup 认证账号对应的 Squid 父代理仍分别绑定 Chrome 使用的固定 Clash listener；不更改现有绑定。新增试验身份只增加自己的认证账号，沿用 Main 的父代理；语法校验后 HUP，代理未重启、既有账号与白名单不变。

## 当前 Main 与身份对照

14:14 原 Main `digitimes-de-1` 精确原页 `FC776D0A11F6C7CBBB881D5ED88A6EB2`、Chrome instance `1ed4fec4a6f94e5bb8eaee4e9b1892a1`、PID2157239，实际可见是 **t=fe / RETRY**。通用标题 `You have been blocked` 不能单独证明 t=bv/IP永久拒绝。本次未丢弃、刷新或重新购买该旧任务。

出口库存虽有四个德国 slot，实际只有两条 Flower 出口：Main `.113`、Backup `.114`。不能把换 slot 名称当成换 IP。另一个 SSRDOG 德国节点的独立隔离连通测试出现 TLS EOF，订阅刷新返回403，未投入生产；试验 relay 已删除，没有增加常驻代理组件，也未重启现有 Clash。

新增正式 Chrome153 独立持久化 Profile **`reuters-backup2`**，en-US、Europe/Berlin，与 Backup 同国家，使用 `.113`（不同于 Backup 的 `.114`）。这是“新 Profile、沿用旧 Main 出口”的对照，**不是第三个新 IP 已验证**。没有复制旧 Profile、Cookie或账户密码。

| 时间 | 新身份真实测试 |
| --- | --- |
| 14:26:32—14:26:57 | 首次 Micron 搜索401，保留验证页 |
| 14:29:04 | 现有恢复流程自行完成 fresh_probe，RESOLVED；没有付费任务、没有人工解码 |
| 14:32:26 | Intel 搜索200 / 20条，1.535秒网络阶段 |
| 14:34:02 | Micron 搜索200 / 20条，4.664秒网络阶段 |
| 14:34:42 | Intel 文章200，正文质量 FULL / 2319字符，7.127秒网络阶段 |
| 14:42:34 | Nebius 搜索200 / 20条，2.443秒网络阶段 |

新身份在相同 `.113` 出口可用，而旧 Main 持续 RETRY，说明 **IP 单因素不足以解释现状，旧 Profile/会话上下文是有效的隔离与替换方向**。但这不是严格的“某一个 Cookie/扩展被标记”的因果证明，也没有把短时可用宣传为长期抗风控通过。

## 已实施的生产试验及回退能力

新增运维脚本 `scripts/ops/promote_reuters_identity.py`，用于这次明确的 Reuters 身份迁移，不引入新运行组件、三活调度或新状态机：

1. 对旧 Main、新身份通过现有 `identities:apply` 清除可选 owner hint并排空其各自 CDP Controller；实际排他关系仍由 `scope_identity_owners` 维护。Backup Controller不操作。
2. 单个事务将新身份提升为源 Scope 的 Primary，原 Backup不变。旧 Main 移入 `publisher:reuters-retained-main-20261116`，迁移对应当前 Recovery/未完成 Task/未关闭会话的 Scope，原 allocation/target/instance 不变。保留现场不是绕过 live-target 删除保护后把页面孤立掉。
3. 旧身份 `automation_active=false`，作为保留维护现场，不继续参与生产或对同出口制造恢复流量；人工仍可处理原任务。新身份保持现有双活调度、去重及自动恢复。
4. 保留 Backup 的有效能力指纹版本；新身份必须经既有 activation canary取得自己的真实能力证明，不复制旧主证明。迁移脚本额外覆盖 generation fencing，防止旧队列观察覆盖新归属。
5. CAS版本检查、活跃维护/付费/恢复拒绝以及事务回滚覆盖真实迁移风险；不加后台守护器。默认 dry-run，生产配置小快照存私有 Registry目录，脚本不保存密码。

14:43:53 配置上线；既有 `ACTIVATE_PRIMARY` 已 DONE / ready=true / CRAWLER / warm=true。三个 Chrome 的 instance/PID均与迁移前相同，包括原 Main和原 Backup。没有重建 Site Access、Supervisor或其他容器：运行应用源码没有修改，此次生效项是 Registry/私有代理绑定和运维工具。没有降低60秒轮询目标；新身份先沿用试验的单并发/3秒节奏，Backup仍双并发/1.5秒。14:52 因一次正文 canary在单并发额度下排队3秒未取得许可，恢复为与原生产相同的双并发/1.5秒；二次 activation 14:52:57 完成。14:54:33 新 Main通过正式源 Scope取得另一篇4221字符的FULL，排队1.488秒、网络3.426秒。该FULL是手工业务canary，未伪写成自然正文账本。

最终回归：迁移/回退、双活与取证组合35项通过；其中迁移/回退专项7项，不重复相加。

## 验收口径、剩余问题与下一步

只把真实 ACCESS_RESULT 计作新网站访问；CAPTURE_CONSUMED、逻辑 QUERY/TAIL 回执另列。11月当前采集真值在 `acquisition.sqlite3` 的 units/cycles，旧 `bus.sqlite3.poll_states/poll_runs` 已退役，不能把旧表空窗当本次无轮询。正式 BODY FULL来自 Registry body_outcomes；手动 canary FULL单列。

收尾自然业务窗口及原页/资源验收追加于下节。切换过程出现短暂 `scope_activation_in_progress` 必须单列，不隐去，也不把它归为新的 DataDome失败。

尚未解决：外部打码令牌在旧会话中的接受率低，内部写入/发送链正确不代表该问题已修复。已具备给2Captcha核对的精确 taskId、应用/首次请求时间、同出口绑定与脱敏响应证据；下一步需要核对供应商实际验证回执及其浏览器上下文适配。未获明确信号前，不靠扩白名单、改UA、重试等待或无限重购处理。新身份的短窗结果也不能证明未来不再 Challenge。

官方契约：[DataDome Cookie返回与同出口要求](https://2captcha.com/api-docs/datadome-slider-captcha)。本次未手工调用createTask或重放Cookie。生产既有solver自然产生一笔新任务，收尾仅对该现成任务进行一次getTaskResult只读核对。


## 最终复发、真实链路及回退

14:43:53—14:54:43 的中间窗口，真实 CRAWLER成功471次（新身份181、原Backup290），自然BODY FULL一条（Backup）；手工新身份正文FULL另列。26个当前必需QUERY头均为succeeded，10个ticker均有采集回执；逻辑单元成功间隔中位数约60秒、均值61.02秒，包含两次配置激活期间的延期。旧10分钟对照窗口有66个unit级site_queue_timeout，不能与新物理ACCESS_RESULT混算。

**14:59 左右新身份复发**，既有流程自动完成RETRY点击，14:59:57提交task **84434823900**，15:00:28 Provider ready，15:00:29.791写入，15:02之后最终STILL_BLOCKED。不是手工制造Challenge或追加测试购买。新任务$0.00145，原17笔加本笔为18笔、已知合计$0.02610。

这次新鲜证据：

- Provider回执只读核对的`solution`只有`cookie`，没有被客户端遗漏的UA/附加Storage字段；再次取得的结果指纹与实际应用值一致。该查询没有重新应用Cookie。
- 新Profile没有ArcP/Reuters登录Cookie，写后、文档实际发送、首次API实际发送均为同一个Provider指纹，无blockedReasons。HTML200，首次API401且原始WIRE DataDome标记明确；拒绝后才Set-Cookie换值。随后原页BLOCKED、独立业务验证也为DataDome401。
- 因而现在共18/18交接正确、17/18首次API拒绝、仅1/18在确认窗获得业务证明。**新Profile不含旧账户或历史Cookie仍复现，排除了“只要换旧Profile/重新登录就能根治”的路线。** 仍不能区分供应商内部验证接受、令牌上下文绑定、受保护API策略三者中的唯一原因，因为它们的关键证据在供应商/站点服务端。
- 新身份并非一接入就失败；先完成多关键词/正文验收及数百次自然请求，随后复发。这也不足以仅凭时序就证明请求速率触发，更不能把本轮暂时工作说成永久IP解封。

最终容量窗口14:52:58—15:00:46：301次真实CRAWLER成功，1次手工BODY成功；同时已有31次CRAWLER队列延期、2次预算延期和1个新Recovery。故稳定性验收**未通过**，没有隐藏失败或把APPLIED计作恢复。

15:07:08 使用同一运维工具的事务回退恢复`digitimes-de-1 / reuters-backup`生产组合，Backup能力版本及Chrome不变；新Profile留在`publisher:reuters-backup2`，保留这次原页和Task，`automation_active=false`，不继续自动购买或制造探测负载。它是待继续验证的资源，不是已验证可接替的生产备用。原Main恢复原有后台恢复政策，未清Profile/Cookie。两次迁移所有Chrome PID/instance均未改变。

回退能力覆盖中途写入失败全事务回滚及失败试验现场保留，不创建新的维护服务。最终仍未修复Main的外部DataDome阻断，生产可用性依赖原Backup，队列饱和风险仍在。当前没有可用的第三个德国出口：既有两个Flower IP之外，SSRDOG德国节点在原Mihomo中的单节点连通测试也返回504，避免将隔离relay测试失败误判为网站封禁。订阅刷新403；未把另一国家出口冒充同地区新节点。

下一步可执行的两条路径：

1. 提供一条实际可用、不同于`.113/.114`的德国出口，再用已保留的独立Profile做IP变量对照；保留原Backup，不更改其IP或借它的会话做破坏性试验。现有晋升/回退工具可直接复用，但必须先通过持续业务验收。
2. 给2Captcha核查task84434823900（新无登录Profile失败）、84433538786（旧Main失败）与84406495239（相同接口成功）。要求其确认私有浏览器验证响应是否被接受、实际浏览器UA/TLS/上下文与提交配置是否一致、返回Cookie是否具备受保护搜索API的适用状态。仅靠平台ready/solveCount=1不能回答这些问题。未代用户向外发送工单。

收尾资源样本：根盘仍49GiB可用，证据目录约268MB且未增长；宿主机CPU PSI较高，内存可用约9GiB，不能宣称整机CPU压力已解决。新增持久资源只有一个Profile/Chrome及少量受限账本/配置，隔离代理试验容器已删除，未新增常驻取证进程、循环截图或完整抓包。旧Main原页DOM仍含active DataDome frame，背景Search Results不作为通关证明。

15:11:48收尾只读复核：回退后原Backup仍有173次真实CRAWLER成功、46次队列延期；原Main仍BACKOFF，新身份保留为停止自动恢复的试验资源。付费任务总数仍18，没有追加第二笔控制任务。当前业务并未全面恢复，不能把Backup持续工作等同于原全量容量已恢复。
