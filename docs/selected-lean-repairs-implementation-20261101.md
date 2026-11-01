# 指定精益修复执行记录（2026-11-01）

范围：R1、R2、R5、R6、R7；R4仅Investing。不实施R0独立统计/监控工程、R3、DIGITIMES及其它publisher修复。R2中首次结果正确性属于其必需改动。其他会话的W3/回测未提交改动保留。本轮尚未获新发布指令，先完成代码与相关测试。

- [x] R1 HTTP单publisher恢复
- [x] R2 首次BODY与能力续作
- [x] R4 Investing代码修复；生产完整正文访问未验收通过
- [x] R5 IBKR
- [x] R6 Calcalist
- [x] R7 有证据的减法与薄诊断
- [x] 整体review、最终测试回执、changelog

基线hash与测试回执：`eval/selected-lean-repairs-20261101/`。

## 1. 实际实现与边界

### R1：请求超时与publisher局部恢复

改动位置：`publisher_http.py`、`poll_trace.py`、`industry_sources.py`、`adapters.py`、`collection_plane/worker.py`。

- 在请求的内存上下文保存最后HTTP阶段与是否真正开始发送请求头；原有TRANSPORT_SEND证据仍只在实际发送时产生。连接/TLS阶段不能冒充取网成功。
- 本地容量等待不计为池故障。进入HTTP执行后，连续两次发头前超时，仅退休当前publisher/route客户端一次；复用既有active/retired/close_later，活跃旧请求完成后才关闭旧客户端。成功清计数；替换后持续拒绝不会每轮重新建池。
- PoolTimeout/明确broken继续走既有立即退休；已发送但响应慢不按发头前池故障处理。异常可携带`publisher_pre_send_timeout`/`publisher_response_timeout`及最后阶段，Collector沿用cycle.reason，不建设另一份事件库。上层自身取消/截止没有具体异常信息时仍保留实际TimeoutError，不能补造阶段证据。
- SharedFeed统一总预算：纯HTTP 12秒、原已批准的GlobeNewswire Browser fallback 30秒；最多一个有剩余预算的重试。修复内建TimeoutError未被SharedFeed及Newswire已配置路线捕获的问题。不新增国家/出口，不延长既有源周期。

**真实端点小对照：** 生产Collector网络内、原`de-standard-1`出口，HuggingNews复用两次及fresh各200/42条，约934/800/1365ms；CEG首个已配置feed各200/10条，约6782/391/4290ms。见`http-route-canary.jsonl`。这些是本次临时客户端的复用与fresh比较，无法读取原生产Python进程的长寿命池，不能证明历史故障一定是池泄漏，也不能证明所有CEG端点恢复；没有据此修改路线。

### R2：首结果落定，恢复责任保留

改动位置：`content_enrichment/schema.py`、`service.py`、`message_bus_v2/live.py`、`live_repository.py`。

- live文章遇到`site_recovery_pending`/`domain_cooldown`，本次形成真实UNAVAILABLE或已有证据的PARTIAL，不再回退attempt/fetch_round后每5秒无限WAIT_BODY。仍按既有策略决定发布；强制完整源继续NO_PUBLIC_BODY，不放宽FULL判定。
- 同一live_article用现有next_check_at保存恢复责任；优先采用provider重试时间，无明确时间取60秒。新增一个`capability_resume`工作类型，与普通30分钟更新复查区分；按背景工作执行，不占正文槽等到期，不挤占新稿优先级。过期能力待办不被普通24小时旧文清理静默抹掉，最终发布仍受原业务有效期规则约束。
- Hub按source×publisher host暂存已知Site不可用截止；同一进程其余文章先利用已有正文缓存，否则复用该不可用事实，不重复RPC。它不是跨身份/跨source通用熔断器；并发首次探测仍可能受现有并发槽影响，重启也会丢失这份内存提示。逐篇续作截止保存在数据库，不依赖这份缓存保证责任不丢。
- 背景领取按到期时间公平推进，仍保留原单背景在途能力；foreground优先规则保持。能力恢复后FULL继续更新原消息/修订，ARTICLE_ONCE保持，首次失败事实不改成成功。
- 只增一个薄字段`execution_spent_ms`累计真实尝试耗时，诊断仍只留少量记录。预算不再从最后4条诊断重建；首结果前新的listing hint保留年龄、尝试与累计预算，不冒充新的content_update。已完成后真正内容变化仍按更新责任处理。
- 保留既有结果spool、body复用、claim token与本地提交续作；没有另建任务账本。普通短暂失败5秒有限重试、Retry-After与明确provider延期保持。

**业务变化：** 非强制完整源可以在首败后按原政策更早交付；强制完整源不会因该修复提前放行。该改动消除无结果热循环，并不等于访问能力恢复或BODY成功率达标。旧长期待办随正常claim落定，不在本轮直接改生产历史数据/水位。

### R4：仅Investing

改动位置：`site_strategy/service.py`。

- 当前Investing为optional认证，BODY使用既有public-markets-nl身份，BODY override/备用绑定没有发现另一个可用的授权身份；两者都无credential。没有将匿名身份伪装成已登录，也没有换国家/启动方式。
- 修复明确的错误放大：匿名公开身份读到某篇登录墙时，保留该篇AUTH_REQUIRED/ENTITLEMENT原因与原响应，不再把整份公开身份标成缺登录，再把真实文章失败压扁成`no_authenticated_profile`。必须登录或已有凭据的身份沿原逻辑。
- 本次两个正常公开文章Site canary分别为12秒access_budget_exhausted和44ms site_recovery_pending，未取得完整正文。**实际Challenge/访问恢复仍未通过**；没有尝试用预览正文冒充FULL，也未改认证、Profile、seed或其它publisher。

### R5：IBKR增量交付与请求收尾

改动位置：`message_bus_v2/ibkr_news.py`。

- historical等待、发请求异常均在finally移除request event/rows；晚回调不再setdefault重建已结束请求。已收到的有效新闻进入原stream等待接纳。article请求同样成对清理event/results，晚到正文不产生无主结果。
- 单个历史请求失败与真实连接异常分型；前者不盲目断开所有ticker共享socket，后者继续现有session/router恢复。保留SDK owner、shield及取消后的线程收尾，不新增连接军团或文章SDK槽。
- 删除等整批文章正文的gather屏障；既有4槽内按已完成批次on_page。一篇正文异常不阻止其它已完成文章接纳；失败项保留，慢项继续已有有限等待。
- 选入返回列表不等于接纳成功：stream项在on_page成功后才确认移除。按具体观察版本确认，处理中同article新tick保留；本地提交失败不提前标记delivered。
- 分批checkpoint只保存已接纳文章ID，不提前推进整段last_sdk_until；全批完成后最终结果才可推进历史覆盖游标。重启依原重叠窗口与发布去重接回，避免慢/失败旧文被提前游标跳过。

### R6：Calcalist有依据地更新CSE token

改动位置：`message_bus_v2/defense_sources.py`。

- 只有短错误体明确表示token invalid/expired才刷新一次并重试；普通403/429/权限拒绝不每轮清缓存附赠bootstrap。并发刷新只作原token对象的局部失效，不扩大锁范围。
- query、日期、语言、分页、原出口与覆盖判断保持，不拿首页RSS替代搜索。此次同一原出口、同一Ondas查询，bootstrap200，原参数与加Referer对照都200且含results（33095 bytes）；未证实需要修改请求头/接口/路线。
- 当前可访问不等于历史403已被归因为token过期；以后权限/配额403仍真实失败。本修复削除无依据的额外请求，不伪造恢复。

### R7：只做有证据的减法与薄诊断

改动位置：`persistent_runtime_v2/w3_context.py`、`bailian_chat.py`、`codex_worker/jobs.py`。

- W3 frozen history命中直接返回；只需计算匹配关系时绕过整case自动Native展开，decode实际event/policy匹配字段及必要provisional，不读无关正文/工具证据。冻结成员、历史文章内容、条数、顺序、交易语义不变。
- 同一合成case的比较：无关Native读取4,200,022→0 bytes，匹配输出一致；测量约103.29→0.034ms。**这是隔离样本的逻辑Native加载量，不是生产物理I/O节省量，也不是整段W3时间改善比例。** 完整历史正文仍来自原冻结trade记录；未裁历史。
- Bailian失败附finish_reason、response_id和耗时薄信息，仍只接受stop及有效ID，不加隐藏重试/模型fallback。
- Worker一般异常保留末尾8帧的文件名/行号/函数名/phase/type，不打印局部变量/Prompt/源代码。旧START_THREAD没有足够原始frame，不猜测加全局锁或重构字典所有权。
- 没有已定位的Bus/Scheduler CPU热点，所以不盲目改pump间隔、堆缓存、删W3预热或削减W1/W2/W3容量。

## 2. 整体review结论

正常路径没有新增数据库、队列、常驻进程、全局锁或健康探测循环。新增持久状态限于一个累计整数；能力续作复用原article与既有调度。HTTP恢复作用域止于publisher/route；BODY首败与后续能力恢复分离；IBKR取数完成与持久化接纳确认分离。各源周期、必需频道、正文质量与ARTICLE_ONCE保持。

未纳入：R0独立统计工程、R3 Reuters、DIGITIMES/其它publisher、无证据CPU重构、W3业务规则/预热以及其它会话回测改动。未做生产发布、重启、清积压或写库迁移操作；远端取证只有有界查询和少量真实端点/Site canary，后者可能产生既有访问日志/恢复状态，不能称为完全无副作用只读。

## 3. 测试与尚待生产验收

- R2与精益回归首轮40通过；本轮收尾定向47通过（含更新旧测试的首败语义，后续又补了内建TimeoutError两例及IBKR覆盖游标断言）。最终收尾回执见下方补记。
- 初次综合119通过、4个W3失败；以HEAD原版history_payload替换测试进程实现后，相同4个失败均复现为RUNTIME_DELAY_EXCEEDED。它们不是本次历史薄读取引入，不修改交易时效规则规避；完整失败与基线回执保存在full-first.xml/w3-baseline.xml。最终综合仅精确排除这4个node ID，其余继续回归，列表保存在test-selection.json。
- 静态未定义符号/重复定义检查与git diff --check通过。其他会话已有本地修改保留，没有整库格式化或覆盖。
- 生产验收必须在实际发布后覆盖正常交易轮次，分别核对原HTTP端点、IBKR早完成文章接纳与checkpoint、Investing真正FULL、首败/当前BODY及所有应交付长尾。此次单元测试和小canary不代表周期接近100%、≤10秒或新文FULL>80%已经达标。

### 最终测试补记

- 综合回归：**296通过、4个已独立复现的基线用例精确排除**，约343秒，见`final-regression.xml/log`。
- review后收尾：**102通过**，约187秒，覆盖最后的IBKR整段checkpoint、Newswire内建TimeoutError兼容、BODY累计预算及背景领取一致性；新增测试覆盖了超过24小时的能力责任和新稿优先。见`post-review.xml/log`。
- 两轮按testcase去重共**299项通过**，不是296+102个不同用例；保留4个W3基线失败原始回执，不把排除当通过。
- 文件hash、当前HEAD、精确排除清单与测试统计保存在`validation.json`；seed hash与本轮开始一致。changelog已追加。本轮代码未提交、未推送、未部署，生产性能目标需正式上线后另验收。
