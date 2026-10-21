# Device Check 卡死与恢复门禁：根因、修复方案及验收

## 已确认根因

2026-10-22 北京时间约04:45—04:50读取生产实际现场，不以状态码或 iframe.src 猜测：

1. Reuters 父文档1566字节，iframe.src仍是 `/interstitial/`；`Page.getFrameTree`不含跨进程子框架。实际OOPIF为 `/captcha/?t=bv`，标题 `You have been blocked`，正文明确 `Access is temporarily restricted`。这是已拒绝，不是还在计算Device Check。旧识别只读父DOM/FrameTree，漏读终态；solver因此一直WAITING_SLIDER，AUTO任务又在维护列表隐藏。
2. Reuters AUTO恢复无总等待终止条件；15分钟只清理页面再新建，不能改变同事件长期AUTO状态。只有真实交互才升级人工，拒绝、未知和超时自动验证缺少可人工检查的出口。
3. InvestorsHub已返回正常页面和14条可解析新闻；请求 `/stock-market/NASDAQ/MU/news` 正常重定向到 `/stock-market/NASDAQ/micron-technology-MU/news`。业务适配器接受同ticker重定向，恢复验证却只接受字符串完全一致，造成200/CONTENT_ERROR无限循环。这不是当前Challenge。
4. 被升级为人工待办的恢复事件走旧`handoff_challenge`，会把整个Profile投影为MAINTENANCE；即使CRAWLER已经独立验证通过，仍被这个全局门禁二次阻断。待人工处理Task不能等同于正在占用Viewer的人工会话。
5. 实测Open Original另有并发交接缺陷：`recover_maintenance`遇到在途访问直接抛`profile_is_busy`，而不是先停止新准入、排空已有任务。旧`release_viewer`只释放Viewer标记，不释放仍有BrowserRecovery的Profile维护投影，可能继续堵住业务。
6. 本轮2Captcha真实付费验收发现Cookie转换错误：provider返回`Domain=.reuters.com`，旧代码`lstrip('.')`后传给CDP，产生仅限`reuters.com`的host-only Cookie。对同一份provider结果与浏览器Cookie在内存中比对：apex可见且值相等，`https://www.reuters.com/`不可见。故provider ready/APPLIED是真实的，但业务网站根本没有携带该结果。未输出或保存Cookie值。

验收中的判定纠偏：DataDome正常`t=fe`滑块也使用`You have been blocked`作为HTML标题，不能把该标题单独当作终态拒绝。真正拒绝需实际`t=bv`或明确拒绝句；加载后的`t=fe`即使只剩Retry也仍是交互验证码，不是新的自动Device Check。相关正反例均已补测。

## 可直接执行的窄修复

### 1. 精确读取跨进程验证框架

- 复用原生CDP，仅在已有、允许的验证iframe中观察；通过DOM.querySelectorAll/describeNode取得该iframe的frameId，再读取对应TargetInfo实际URL和DOM。不得从同一Chrome里随意选一个captcha target，保持精确页面归属。
- 同进程iframe兼容contentDocument/FrameTree；读取失败明确UNKNOWN，不能仅凭旧src无限宣称自动验证仍运行。
- 将同一观察方法用于DataDome solver和人工/后台原页检查，实际 `/captcha/?t=bv` 或已加载验证页明确拒绝应标ACCESS_BLOCK，不再AUTO_CHECK。
- 不构造或改写captcha URL/t参数；t=bv不向2Captcha购买无效任务。实际可处理滑块仍走现有同出口付费流程。

### 2. 自动恢复有限等待，保留人工接管和后台探测

- 复用现有BrowserRecovery/ChallengeTask，无新增控制服务。记录明确的manual_review_reason/时间，区分“需要人工检查”与“已识别为交互验证码”。
- 明确访问拒绝立即进入人工检查；AUTO/UNKNOWN持续未恢复，在3—5分钟有界等待后进入人工检查，不反复清零期限。
- 有界等待不依赖成功读取DOM：持续读取超时或确认丢页，同样生成可打开原页/明确TARGET_LOST的人工复核出口，原因`verification_observation_unavailable`；继续隔离探测，不因渲染器失去响应而永远隐藏。
- 人工检查状态保持稳定，不能因父DOM旧/interstitial/再被重分为AUTO并隐藏；真实业务证明通过后才结束事件。原页存在则交接CHALLENGE并保留，丢页则创建TARGET_LOST待办，允许Reopen；普通Open Login继续独立。
- 人工检查仍每10分钟进行既有隔离业务探测；OPEN人工会话优先，不导航/刷新用户页。可用fallback继续接替；不假装不可用fallback已验证。
- 若同事件同时影响CRAWLER/BODY，先验证CRAWLER，BODY失败不能让可用采集一直等待同一篇旧正文；各能力独立提交真实证明。
- 上游仍可见延期/阻断，但附具体人工检查原因和Task，不再只有无期限site_recovery_pending且无入口。不得仅为清错误而伪造READY/FULL。

### 3. 修复InvestorsHub规范化URL合约

- 仅该source允许HTTPS、同固定host、同交易所、同ticker的short-symbol→company-slug-symbol新闻列表转换；不同ticker/交易所/路径、登录/首页/外域仍拒绝。
- 沿用实际parse_investorshub解析器，必须有真实列表证明，不以200直接解锁。补正BODY验证不应使用/news列表的通用判定。

### 4. 修通人工入口并隔离维护投影

- 有BrowserRecovery、无人OPEN时，创建CHALLENGE Task与原页所有权，但Profile保持AVAILABLE；未恢复能力继续受BrowserRecovery门禁控制，不假造健康。
- 真正Open Original时先进入DRAINING，最多20秒等待在途任务结束，再接管；超时/取消回滚准入投影。数据库OPEN会话继续阻止后台控制抢占。
- Later/Release Viewer保留原页及Task；若有未完成BrowserRecovery，释放Profile兼容维护投影，恢复后台探测和已证明能力。无恢复事件的历史人工维护语义不改变。
- 明确显示MANUAL_REVIEW及具体原因，不冒充已经识别出滑块/点选验证码；准入冲突返回固定、安全的具体原因。
- 对已识别交互验证输出`human_challenge_pending`，provider处理中输出`datadome_solver_pending`，各门禁路径均携带Challenge Task ID；普通自动等待与已拒绝终态不再混成一种解释。

### 5. 修复2Captcha Cookie交接

- 有Set-Cookie Domain属性时，写CDP时使用domain-cookie语义：`.reuters.com`；无Domain属性时保持实际网站host-only。继续只接受该publisher的datadome Cookie，不扩大域权限。
- `Network.setCookie`成功后必须通过`Network.getCookies(urls=[实际业务网址])`确认同值、同域Cookie确实会发给网站，才刷新原页并记录APPLIED；该校验无外部网站请求，不输出凭证。
- 修复前已经付费的具体任务仅进行一次有留痕的作用域纠正：不创建新provider任务，复用原taskId；使用同一Recovery租约、原target/instance、Identity revision与egress generation/IP/绑定指纹，OPEN人工会话优先。纠正完成仍必须等待真实业务证明，不能直接改READY。
- 后续新任务自动使用修正后的流程并记录`cookie_scope_verified=true`；不新增常驻修复进程，不反复购买同事件验证码。

### 6. 测试、发布和2Captcha验收

- 回归：空FrameTree且OOPIF重定向t=bv、同进程/读取失败、跨页关联拒绝、自动超时一次升级且不隐藏、丢页人工入口、OPEN用户保护、crawler/body独立推进、InvestorsHub同ticker规范化与反例。
- 仅发布相关Site Access（若确有消费者变化才发布对应组件），不重建Supervisor、清Profile、换Cookie或绕过健康证明。保留原页证据、发布基线与逐文件哈希。
- 通过真实来源轮询/恢复探测验收，不把一次HTTP200或容器healthy当完成。现成可处理DataDome滑块才尝试2Captcha；无现成滑块不制造请求、不持续盯守。若必须换出口，使用现有身份的受控换出口流程并同步solver绑定/同IP检查，不在浏览器中伪造环境。

官方依据：[DataDome Device Check](https://docs.datadome.co/docs/device-check)会放行、追加验证码或拒绝；[2Captcha DataDome](https://2captcha.com/api-docs/datadome-slider-captcha)明确t=bv为IP被拒绝，不是可直接提交的fe滑块。

## 实现与真实验收

### 已落地代码与发布

- `verification_frames.py`：精确DOM iframe→TargetInfo/OOPIF观察，共用于`maintenance_document.py`与`datadome_browser.py`；不接管其他页面、不改指纹、不额外请求验证站。
- `challenge_detection.py`：真实终态、滑块/Retry、自动验证分流；`recovery.py`：有界AUTO→人工复核、后台探测、CRAWLER优先与一次性更换旧BODY样本；`governance_repository.py`：稳定Task及独立Profile准入。
- `service.py`/`maintenance.py`与桌面UI：具体延期原因、人工复核展示、接管排空与Viewer释放；`verification.py`修正InvestorsHub正文候选边界。
- `datadome_browser.py`/`datadome_solver.py`：provider Cookie域语义和实际网址可见性证明，旧任务终态更新、保留同事件单次购买/每日限额。
- 相关提交：`9130bfd2`、`6deee245`、`02b158db`、`089c02c9`、`1c95238a`、`a3d03e35`、`c42a0212`、`69d2831c`、`6dbdb4e9`，已推送origin/main并拉取远端。仅相关Site Access重建；最终生产版本`6dbdb4e9`，UTC2026-10-21T21:47:06Z启动，镜像`sha256:9c52f07de265aa86522bb2398f39054dd62be0cce6542b565fd62ffa41f6d6ee`。18个相关源文件哈希全部匹配。
- Supervisor容器`cd1affdb3d94`与当前网络命名空间绑定未改变，Cookie/Profile未重建，备用原页`D9003AA1AD579842DA4EC858538742EC`保留。Reuters主身份原本不运行，本轮通过普通Supervisor冷启动现有Profile进行对照，不是复制/重置身份。桌面UI原位更新，没有重装xRDP。
- 最终12组离线回归229通过，Ruff F/E9通过；较早扩测发现3个旧Investing测试夹具未适配ChallengePolicy和独立probe_recovery正文验证接口，已修正夹具后重新全组通过。新增回归覆盖完全无DOM、确认丢页、父页面已有大段业务DOM但仍被真实全屏验证码覆盖、前端延期携带人工Task。没有用放宽生产判定修复测试。

### 已确认真实回执

全部时间以下使用UTC，转换北京时间加8小时；统计只算完整窗口内AccessResult，不把恢复探测或原Task seen_count重复算成普通业务成功。

- InvestorsHub：21:00:12真实列表解析通过，旧恢复事件RESOLVED；20:59:04—21:31:08自然窗口93个CRAWLER SUCCESS、3个BODY SUCCESS及3个FULL；另有2个采集预算延期、2个正文预算延期。发布期间出现的ConnectError及正文deadline_exceeded单列，不能计成Challenge或抹去。本轮观察是多轮，不是只打开一次网页。
- 人工路径：Reuters同一原页Open Original实测DONE/VIEWER_READY；随后Release Viewer实测DONE/page_preserved=true，Profile恢复AVAILABLE，Task与原页保留。没有代用户做验证码或伪造Verify成功。
- 备用Reuters：实际`t=bv`拒绝页，任务`challenge_da4c00110ca6414a8d916bc4dffb7580`可见且原页留存；不向该拒绝页购买无效任务。新BODY样本从真实搜索结果选择了一次，独立BODY仍被401拒绝，未声明FULL。
- 主Reuters：21:19:39真实搜索canary通过后切到已有主身份；随后自然请求出现真实`t=fe`滑块。此处证实“页面可用一次”不能代表已稳定恢复。
- 首笔2Captcha：任务`84198857025`，21:20:18提交、21:21:19返回并写入，但因旧Cookie域错误未恢复。21:29:50用相同paid result纠正作用域并证明实际www网址可见，没有新增购买；21:30:28独立真实搜索验证HTTP200、14条recipe rows、Recovery RESOLVED，21:30:48又有自然CRAWLER SUCCESS。这条链已经超过provider ready/APPLIED，证明至少一次付费结果能恢复实际业务。
- 21:31:05新的Nebius自然搜索再次产生新滑块，距上述恢复约37秒，属于新事件，不是旧Task更新。因此不能据首笔成功宣称Reuters长期稳定。新事件由生产自动提交第二笔`84198950204`，新代码在21:32:00记录APPLIED/作用域已验证，费用同为$0.00145；之后业务探测有TimeoutError，未取得该事件真实恢复证明。
- 第二笔后的只读现场：父页394764字节含完整Search Results/无结果文案，但仍有`position:fixed;top:0;left:0;z-index:2147483647;height:100vh;width:100%`的真实DataDome遮罩；不能凭后方业务DOM放行，也不能先按父DOM大小提前忽略已观察到的验证子页。当前场景与paid task场景指纹已经不同，状态RETRY；这是仍在出现验证，不是Cookie仍写错域，也不是可忽略的广告弹窗。未自动重复购买同事件。
- 主身份新Task同样实测Open Original DONE/VIEWER_READY与Release Viewer DONE/page_preserved=true。该测试没有解验证码或伪造业务Verify通过；第二笔仍可交人工检查。

### 收尾快照与交付边界（UTC21:50:20）

- 跨本轮逐次发布的20:59:04—21:50:20累计窗口：InvestorsHub 146个CRAWLER SUCCESS、11个BODY访问成功、8个FULL回执；另有3个采集预算延期、1个采集SERVICE_UNAVAILABLE、5个BODY预算延期。成功数不是独立文章数或ticker全轮完成数，也不代表最终版本已经连续运行51分钟。
- 21:35:42曾观察到InvestorsHub九ticker最近状态均无错误；最终几次发布造成连接失败和启动预算延期，21:50:20快照仍有未被下一轮覆盖的ConnectError/SiteAccessError/预算延期记录（最后错误多在21:46—21:47），同时真实采集成功已持续到21:50:16。保留这些状态，不人工清空；未声明当前九ticker全部绿色。旧恢复事件已RESOLVED，不再被同ticker URL规范化误拒。
- Reuters该累计窗口15个CRAWLER SUCCESS，但最新一次21:30:48，不能把此前成功冒充当前可用；当前九ticker明确`SOURCE_DEFERRED:human_challenge_pending`，主身份真实滑块/RETRY待处理、后台隔离探测保留，备用真实`t=bv`拒绝。BODY无本轮FULL证明。
- 共两笔真实paid task，总provider记录费用$0.00290；首笔复用付费结果纠正作用域并恢复过真实采集，第二笔仅APPLIED，未证明业务恢复。没有把两笔都报成成功，也没有为备用IP拒绝购买验证码。
- 原有24小时只读验收仅更新版本/事实基线，保留原截止UTC2026-10-22T20:23:20Z；不新增打码守候，不由监测主动触发网站、provider请求或购买。旧Task更新不重复算新增挑战。

### 当前边界

- 本轮修复的是隐藏恢复死锁、终态漏识别、能力门禁、人工接管及Cookie交接；不会伪造或更换浏览器指纹来保证网站永久放行。
- 备用IP拒绝仍是外部真实限制；一次成功后几十秒又挑战说明Reuters仍有高频复发风险。后台有界重试、实际可用身份接替、人工待办与每日付费限额保留，不把付款成功当消息源健康。
- 新Captcha原页失败时继续留存，自动任务不抢占OPEN人工窗口。只使用自然出现的真实挑战进行有界验收，不制造验证码，不新增等待验证码的自动化。
- 私有原始证据/Registry备份/发布回执保存在远端`/var/lib/doxagent/devicecheck-repair-20261022/`及`/var/lib/doxagent/challenge-recovery-20261021/`；本地脱敏测试及验收材料`eval/device_check_deadlock_20261022/`。其中诊断脚本/原始评估产物不混入业务代码提交，凭证/Cookie不进入Git。
