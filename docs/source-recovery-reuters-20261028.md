# Reuters RETRY 与持续失败消息源修复（2026-10-28）

本轮以实际部署 `74c07266` 和新的独立 Collector 为基线，保留其他会话未提交的 Google News、交易、前端和 Compose 修改，不整体重写或重启 Supervisor。基线证据 `eval/source_recovery_20261028/baseline.json`。下文明确区分已证实机制、实现修复与生产验收；不会把付费 ready/APPLIED 或一次 200 当作长期稳定。

## 已证实机制与首批修复

1. **Reuters 备用原页仍在，但原生 DOM 命令超时。** 同 instance/target 精确核对成功，DOM.getDocument 8 秒未返回。旧 free RETRY 在 10-27 12:34Z 记录 UNCERTAIN，此后没有新付费任务。旧实现甚至在前置场景检查、坐标读取尚未执行时，就标为“已可能点击”，前置失败也会冻结事件。新实现区分 PREPARING、NOT_DISPATCHED、实际输入前的 UNCERTAIN、CLICKED；已知未点击的失败不消耗点击额度。旧 UNCERTAIN 仅能基于新鲜、同 instance/target 的可见 RETRY 控件作有界复核，仍最多两次实际尝试；不重复购买、不改写验证码 URL、不模拟滑块答案。
2. **先 attach、后清理形成循环依赖。** 原代码必须先完成 Playwright 对整个 Chrome 的 CDP attach，才能清理旧 BUSINESS 页面；未初始化/高负荷的遗留页又能阻断 attach。改为先用独立 browser-level CDP 核对持久所有权、清理遗留业务页，再 attach。该清理连接不启用 autoAttach，不暂停或接管任何人工页。热连接额外低频重试 CLOSE_PENDING，跳过 LIVE/ALLOCATING 和人工现场；失败继续留待收尾，不伪造 CLOSED。
3. **真实进程资源压力，而非只看 free。** Supervisor pids.events 已命中 max；后续 docker exec 实际报 Resource temporarily unavailable。历史 memory.events oom_kill=11 不是本窗新增 OOM 的证明。public-markets-nl 单身份有 5 个 page、122 个 iframe，进程 RSS 求和约 4GiB（包含共享页，不能当精确独占内存），约 862 线程；其业务 CLOSE_PENDING 页面仍物理存在。不能将所有 457 个未终结账本记录都当作真实活页；按当前 instance 的原生清单核实后再治理，不清 Cookie/Profile、不杀人工页。
4. **CTEE 游标自锁。** 实时轮询沿用 page 并逐步越过 max_pages，while 从不执行，永久空结果且无物理列表证明。实时固定从首页开始；max_pages 约束每批实际请求数，补抓可继续 page=9/10；真正结束后复位游标，未结束不伪报 window_done。保留原时效接纳及语言/分发策略。
5. **TrendForce News 不必要地依赖浏览器。** 同宿主机现行 server-direct 公开 HTTP 实测 200、现有解析器得到 7 条。复用 Press Releases 已有的 public_http 优先、单次失败后浏览器 fallback、60 秒传输重试冷却，不新增代理轮换或绕过私有登录。明确保留实际物理请求与列表证明。

## 其余基线失败与边界

- Yahoo 必需浏览器频道：RSS 正常不能抵消 SUPPLEMENT_HEAD 的 site_queue_timeout/access_budget_exhausted。当前仍需核实旧实例残留页及资源压力、真实导航/控制耗时，不能只加超时。
- AviationWeek 两入口：现行固定 HTTP 路由均 403，定向实测仍 403；尚不能归因 parser。继续测试普通 Managed 浏览器可用性，不能把拒绝页当成功列表。
- IBKR：过去一小时 TimeoutError 多，但采集进程最近重建后状态需重新观察；不未经证据修改另一会话交易代码或 Gateway 登录。
- Newswire RSS：有 ConnectError/ConnectTimeout，需区分其 Newswire/NCP 链路和网站浏览器，不把 API 故障归为 Challenge。
- 其他来源偶发的发布窗口 ConnectTimeout/CANCELLED，与当前持续失败分列；不为历史偶发失败改动已恢复的健康来源。

## 验证与发布

第一批核心回归 70 通过、1 条有条件跳过；新增适配/控制回归 29 通过（批次有重叠，不累加去重总数）。后续补记生产精确版本、保留现场、paid task/费用/应用/原页通关/正式列表/正文/复发与剩余人工事项。

首批整合回归 91 通过、1 条有条件跳过。`cc628390` 于 UTC 2026-10-27 18:24:21 完成 Site Access/Collector 窄发布，Supervisor 容器 ID 未变，Collector 的 `host.docker.internal:host-gateway` 保留。更新后 public-markets-nl 从 5 page/122 iframe 降为 2 page/无 iframe，RSS 求和从约 4GiB 降至约 0.4GiB；Reuters 备用原页与 Chrome PID/instance 未变，但 DOM 仍超时，不能宣称其已恢复。

进一步移除 Challenge 的 Open Original 对 Playwright attach 的依赖：已保留的 external 原页经持久所有权、OPEN 会话、精确 instance/target 校验后，直接选择 Supervisor Viewer 并激活该页；只对该页尝试解除调试等待、恢复 active 生命周期，不导航、不重新建页、不作 CAPTCHA 输入。渲染器不可读不再阻止 Viewer 开放。正常新建登录和 Managed 路径不变，Verify 仍要求真实原页和业务合同通过。相关 85 条回归通过。

## 原生对照与第二批修复

- UTC18:31 native Open Original 在 8 秒内 DONE/VIEWER_READY，原页保持同 target/instance。原页 `Runtime.runIfWaitingForDebugger`、`Page.getFrameTree`、`Runtime.evaluate`、`DOM.getDocument` 均超时，同 Chrome 空白页对应命令正常；不是一张已确认可操作的 RETRY。解除调试等待/active 不能修复该原页。
- 在本轮创建的 OPEN 维护会话独占期间，对该原页执行**一次** `Page.reload(ignoreCache=false)`，ACK 成功，target/instance/PID 不变。随后 DOM 81ms/OuterHTML367ms/截图202ms，未见活动验证；UTC18:34 主备 recovery 均 RESOLVED。此恢复属于人工操作触发的普通刷新恢复，**不是打码成功**。已释放本轮 Viewer；继续采集自然业务证据。
- 永久修复：外部原页连续两轮不可读、事件已超过180秒时，在现有 Profile 独占锁和 recovery generation/lease/target 校验内，允许一次同原页普通刷新；先持久保留 UNCERTAIN 预约，重启或响应丢失不重放。OPEN人工会话、PENDING/SUBMITTING/APPLYING付费任务、已丢页、已用过刷新均禁止。保留独立 `document_recovery` 账，不覆盖 RETRY 点击额度、不清 Cookie、不创造验证码 URL。重新观察与业务验收才能恢复，刷新 ACK 本身不是恢复证明。
- AviationWeek：普通 Managed CfT 实测两个入口均 Cloudflare403；现有 us-standard-5 与 de-standard-1 均取得列表25条、RSS100条。选定固定 us-standard-5 同时供采集与正文，不按403自动轮换、不新增 Chrome。实测两篇最新文章 HTTP200，但明确 subscription_required/PARTIAL，无订阅正文，不伪报FULL。正常免费文章仍沿现有正文识别器；付费内容需要实际账号权限。

## Investing 与资源边界

Investing primary 的旧业务页没有持久所有权记录（0C06E304…），另一个空白页也不可读；不把这些页当已登记 Challenge 或擅自清除。一次控制器 detach + 同页刷新 ACK 后，两个页仍不可读。对该身份先通过受限管理 API 预约 Profile 为 MAINTENANCE、排空在途、断开 Python/Node 控制器，核实没有 LOGIN/CHALLENGE/RECOVERY 活页，再执行一次正常 `:drain`。Chrome正常SIGTERM退出，未强杀、清理Cookie或重建Supervisor。随后撤销本轮操作预约，交给自然轮询使用原Profile重新启动；是否业务恢复仍需后续回执。

同时修正容器PID容量与合法身份并发不匹配：Supervisor允许最多16个External身份，原2304 task/thread限额低于16个Chrome的常见线程总量；Site Access还同时承载控制Worker与最多8个Managed Chrome，原1024也不足。实际已观察 pids.events max282及OCI spawn EAGAIN，且清理活页后旧Chrome仍可能留在不可读状态。保留内存限制，分别将PID上限调整为4096/2048；使用Docker live update生效，不为此重建Supervisor或其他身份。Compose保留相同值供后续正常发布。此举与遗留业务页清理一起降低反复线程创建失败风险，不以提高限额替代页面收尾或内存治理；实际每个来源的业务与资源增量继续独立验收。

## 收尾验收与未完成分支

按用户要求尽快收尾，不新增长时间监测或制造验证码。最终运行源码为 `30a2a184`，配置为 `b548b658`；随后提交的本文验收记录不需要再构建镜像。Site Access、Collector、Content Enrichment 已通过标准窄发布更新，12个相关文件的Git blob与实际容器SHA256全部匹配；Supervisor仍为 `d00bf01c2738…`。仅Investing主身份正常退出并使用原Profile启动为新instance `1b80d5b3557d4fe4b43ec34eefb689bb`，Reuters主备instance均保持。Collector原有host-gateway修改保留，没有纳入本轮提交；其他会话的本地改动同样保留。

核心恢复/solver/维护/适配回归183通过；最终新增人工隔离等覆盖批次50通过（与前批有重叠，不相加）。初始整合91通过、1条件跳过。详细脱敏证据在 `eval/source_recovery_20261028/`。

最终发布后的完整短窗为 **UTC2026-10-27 18:44:38–18:53:45**（北京时间10-28 02:44:38–02:53:45），`window-final-first.json` 不混充一小时或一天：

|来源|正式采集回执|窗口结束时必需单元|说明|
|---|---|---|---|
|Reuters|218 CAPTURED、1次site_queue_timeout|23成功、1失败|全部查询单元分别记录；不是218篇新增新闻，也不是零延期|
|Investing|68 CAPTURED、13次失败/延期|9 ticker均成功|前段包含旧Chrome退出及恢复过程，不称全窗无失败|
|TrendForce News|45 CAPTURED|5 ticker均成功|共享抓取回执按订阅目标计数，不等同45次独立物理HTTP|
|CTEE|18 CAPTURED|2 ticker均成功|已不再停于零请求游标|
|Yahoo|329 CAPTURED|18单元均成功|含9个RSS HEAD及9个浏览器SUPPLEMENT_HEAD|
|AviationWeek Rocket Lab|7 CAPTURED、2次失败|1单元成功|固定代理生效；与RSS分别维护|
|AviationWeek RSS|9 CAPTURED|1单元成功|正文付费权益另列|
|IBKR|81 CAPTURED|9 ticker均成功|另一会话修复host-gateway，本轮保留并验收|

随后针对当前失败单元只读复核仅见 InvestorsHub 一次site_queue_timeout，最近成功为18:47:40；之前发现的 Newswire、TheFly、Army、Calcalist、DoD、能源/技术RSS等均存在新的成功回执，不能把历史失败计数或一次endpoint backoff当作仍然永久失效。偶发网络/预算延期仍保留真实失败，不抹成绿色，也不对已恢复端点无证据换路由。

独立正文验收（不写假业务回执、不publish）：实际Bus文章经正式 `body_v2.2` 抽取器，Reuters《Constellation gets conditional $5 billion US DOE loan commitment…》HTTP200/FULL/1560字符/约5.1秒；Investing《Oracle, Avago among market cap stock movers on Tuesday》HTTP200/FULL/1946字符/约6.4秒。两条是受控canary，不能代替生产正式BODY成功率或全天延迟统计。AviationWeek两篇抽样付费文章均明确PARTIAL/subscription_required，需要订阅权限才能取得其付费正文。

Reuters备用另执行一次真实注册搜索，确认由 `reuters-backup` 原instance返回200/SUCCESS、无共享capture复用、rows=0；空结果仅证明该次搜索合同，不证明所有关键词召回。付费配置确认为enabled、主备均有绑定、daily_tasks=20。**本轮没有新paid task，也没有真实RETRY→slider→provider ready→应用→业务恢复的完整实证，因此这条端到端能力仍未验收，不能声称已打通。** 没有主动制造挑战、循环购买或将普通刷新恢复归因2Captcha。

**仍需后续处理的备用风险**：Investing备用 `investing-backup` 保留的旧原页/Chrome仍不可读，一次新自动刷新ACK后仍有CDP attach失败，未冒充已可接替；主身份9 ticker已恢复。本轮未为该备用丢弃人工原页或重建Chrome。Reuters本窗无新challenge，但短窗不能证明长期稳定；后续真实RETRY发生时，仍应分别核对原生输入回执、paid任务、费用、Cookie作用域、原页通关、自然CRAWLER与FULL以及复发，不用单个200/APPLIED替代结论。
