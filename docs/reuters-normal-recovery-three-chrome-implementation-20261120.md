# Reuters 正常恢复、终态修复与三 Chrome 实施验收

日期：2026-11-20（北京时间；以下账本时间统一 UTC）。

## 结果与边界

本轮已在 main 开发、推送 origin/main 并发布必要的 Site Access；没有重建 Chrome Supervisor，没有切换既有 Main/Backup1 的 Profile 或出口。第三身份 `reuters-active-3` 已建立独立空 Profile、正式 Chrome 153，与你人工完成验证/登录后的同一会话衔接，三身份均可参与正常列表和正文调度。

**没有得到“fresh_probe / 新上下文 / Cookie 转移可以可靠主动解封”的结论。** 本轮现场反而证明，仅原页露出正常内容就宣布恢复会造成假阳性。这一内部验收缺口已修复；真实正常恢复和人工恢复分开记账。没有以更换身份、移植验证 Cookie 或自动操作 CAPTCHA 的方式处理阻断。

执行方案：`dev_plan/workflow_v2/reuters_normal_recovery_three_identity_implementation_20261120.md`。现场和脱敏回执：`eval/reuters_parallel_recovery_20261120/`。

## 一、非付费恢复：可确认什么

### 历史成功不是单变量实验

已复盘前轮记录：新浏览器上下文、Device Check、Cookie 转移、页面 fetch/reload 与 Verify 曾混合出现；不能据此断言某一个动作产生了恢复。此前同持久化 Profile 的正常 fresh_probe 确实出现过真实根文档/API 网络 200，并且当次不要求更换 Cookie，但也出现约 7 分 20 秒后再次阻断、正常 Verify 仍失败的负对照。详细时间链保留于 `reuters-cookie-acceptance-and-native-recovery-audit-20261120.md`，不将历史小样本扩大为服务端机制结论。

本轮开始时，Main 与 Backup1 已在列表减负后自行恢复，至 UTC22:13 已分别连续运行约 49/80 分钟。不能主动制造 Challenge 作为对照，也不能把这两条正常身份再请求 200 当作“主动解封”成功。

### 本轮第三身份的真实正负样本

1. 独立空 Profile 首次普通访问在22:30:05进入 AUTO_CHECK；22:30:13原页观察把它记为 `RESOLVED / CRAWLER READY / original_document`。
2. 22:30:20真实列表仍返回401；22:30:28重新进入恢复。这说明第一步不是可靠的业务恢复证明。
3. 22:35:36既有定时正常业务探测启动；22:35:50结束，真实401、`STILL_BLOCKED`，原页留存；现场被确认是 DataDome 人工滑块。此次 fresh_probe **未能主动恢复**。
4. 你随后在同一 Profile/出口完成人工验证。22:39:42的 Verify Challenge 完成了 Challenge 清除；其 BODY 子检查却误选 `/world/` 栏目页，留下 `CONTENT_ERROR / IdentityOperationError`，认证仍 UNKNOWN。这个失败不能解释成账号没登录，也不能把泛化异常认定为唯一根因。
5. 修正实际验收入口后，普通 Login Verify 使用搜索结果中的真实文章，得到 `CLEARED / VALID / READY / FULL / session_closed=true`。没有输入账号密码、移植 Cookie 或调用解码平台。

因此，正常会话自行恢复真实存在，但当前不能证明是 probe 本身主动解除服务端限制，更不能称为可靠“免费解码”。本轮没有实施跨上下文 Cookie 移植或新的身份替换实验。保留同 Profile 的正常业务恢复探测和人工出口，以真实 API/正文证明结束恢复；不能只依据旧页、Cookie 可读或 READY 字样。

## 二、已发布的内部修复

### 1. RESOLVED 清理不再复活人工任务

`GovernanceRepository.save_recovery` 的人工升级分支排除 `phase=RESOLVED`。带有历史人工原因的成功清理、probe 元数据回写不会再创建新人工Task，也不会把已关闭旧页重新包装为 TARGET_LOST。保留失败中的真实人工升级、CAS 和页面所有权；未批量删除旧历史任务。

确定性回归覆盖旧页 LIVE/CLOSED 两种状态：修复前分别可出现所有权冲突/幽灵任务，修后完成同一终态且无新任务。生产当前恢复/人工任务另核验，不把历史 superseded TARGET_LOST 算作本轮新增。

### 2. 轻量 Cookie / WireEvidence / cache 取证

- Cookie 摘要增加 SameSite、到期时间和长度，不记录原值。
- 早到 ExtraInfo 即便没有 DD Cookie 也能合并；区分权威无 Cookie、不可观察与仅请求头可见。
- 无关请求、结束与失败清理暂存，保留原六请求/八暂存上限。
- 正确识别 memory/disk/prefetch cache；没有关闭原生缓存，没有随机查询参数。

这些修复改善证据，不代表 Cookie 已被网站接受。付费 APPLIED 与真实业务结果仍分别记录。

### 3. 本轮现场追加：原页 SSR 不能证明搜索 API 可用

`verify_capture(... observed=True)` 不再凭 Reuters 原页的 SSR 链接、搜索壳或空结果文案独自宣布 CRAWLER READY。使用已有、受预算控制的正常业务 probe；不增加探测循环。

人工 Verify 同样不再复用一个缺少真实 recipe/API 结果的 Reuters 原页作为搜索成功证明。真实 recipe 的有效空结果契约仍被接受；没有将“零条新闻”改为错误。

### 4. 本轮现场追加：正文验收不能使用栏目页

`body_verification_url` 排除 Reuters 非文章路径，包括当前生产误用的 `/world/`。从已有正常搜索结果选择真实日期型文章入口；没有额外构建一个发现爬虫。人工已登录的第三身份由 UNKNOWN 正确验收为 VALID/FULL，维护会话正常结束，不强写认证状态或放宽订阅正文证明。

### 5. 没有在短窗成功处收尾：共享 BODY 软资格会使空闲浏览器饥饿

UTC23:01开始自然出现新一组多ticker `site_queue_timeout`，三身份均无新Challenge。23:02:41的只读诊断显示：global_active=0、body_active=0、各站点/身份active均0、节奏时钟均已就绪、所有控制worker为IDLE；但Reuters及其他站点仍在等待，Newsfile有多条BODY等待资格。不能把这类失败写成网站风控。

代码根因：`JointBudget.permit` 将 `attached=false` 的延期BODY软资格纳入 eligible，再选为 best。该资格此刻没有正在申请的协程，无法执行“best is waiter”的领取分支；它又持续压住其他站点。三次BODY后放行Crawler的公平机制也无法救场，因为根本没有发生新授予，streak不增长。

修复复用原结构：软资格保留年龄及同身份BODY排序，但只允许真实attached申请参加物理许可选择；不阻塞其他身份的就绪Crawler/BODY。不改总容量、优先级、持久ready_since、软资格期限或请求频率，不建新组件。

独立加载修复前 `5dd7fe4b4` 的原始预算类重放同样的空闲场景，Crawler和另一身份BODY均在0.3秒窗口内饥饿；新版本两者均正常领取，旧资格年龄继续保留。此前同身份老正文优先测试仍通过。证据 `soft-claim-before-after.json`。

原现场的旧诊断未暴露pending_physical，不能把未知值填为0或排除所有其他占用因素。本轮在既有手动诊断中仅补充pending_physical、detached_body_claims标量和waiter的attached属性；不新增采样后台或持久日志。确定性代码缺陷与旧现场相符，最终生产观察单独分段，不把重启后暂时清空内存队列当成唯一修复证明。

## 三、三身份正常采集实现

| 业务角色 | Identity / 独立 Profile | Runtime | 出口 |
|---|---|---|---|
| Main | reuters-backup2 | 正式 Chrome 153 + CDP | de-standard-1 |
| Backup1，并行成员 | reuters-backup | 正式 Chrome 153 + CDP | jp-standard-6（历史节点别名） |
| 新 Backup2，并行成员 | reuters-active-3 | 正式 Chrome 153 + CDP | 复用 Main 既有出口 |

第三身份不复制 Main/Backup1 的 Cookie 或账号存储。Main 与新 Backup2 共享出口，**Profile/Chrome 故障隔离不等于出口级隔离**；这是本轮固定网络变量的选择，不声称解决了共同 IP 风险。

生产 source scope revision7：

```yaml
crawler_routing: active_pool
body_routing: active_pool
additional_active_identity_ids: [reuters-active-3]
max_parallel_concurrency: 4
min_parallel_interval_ms: 750
```

- 复用原 `JointBudget.route_members`，CRAWLER 与 BODY 共用已有调度/物理许可；没有第二个队列、重复 ticker 计划或消息发布路径。
- 各身份原上限仍是2并发/1500ms。站点总并发4、合并节奏750ms与两身份模式相同，第三身份不会将其扩成6并发/500ms。单身份可用时收回该身份的真实容量；保护期及平滑升载继续生效。
- 公共成员解析覆盖独占 owner、ScopeResolver、worker 快照、恢复归属、维护列表/打开页面、长期驻留。第三身份能正常维护，不是只写了一条备用配置。
- 列表与正文分别做能力、鉴权和恢复准入；不会借用其他身份的登录或 FULL。维护/Challenge 中的成员被排除，其余成员继续工作。
- routing-only CAS 保留既有成员的能力版本，新成员从无证明开始。配置脚本 preview 默认、Owner API/CAS apply；人工维护后再次 preview 为 `changed=false`，没有重置新登录。
- 随附正文准入方案已在生产生效：**每 ticker 首个 L1、10 QUERY / 0 TAIL、QUERY 60s**。本轮保留该目标，未将轮询间隔主动改成更长。

回退不删除 Profile：可以通过原 Identity Owner API 暂停新成员，保留范围归属及现场；恢复后重新验证。若使用配置脚本 `--disable` 移出成员，仍要求先释放其活页/维护所有权，否则 CAS 明确拒绝，不强行丢页。

### 查询稳定归属：不能只用任务数代替网站实际请求负载

初始动态轮转确实将采集任务均摊，但10个固定QUERY在三身份间轮转，会逐步让每个Profile重复填充相同查询的浏览器缓存。被动窗口中，人工前的两身份短窗大多命中缓存，而三身份接替后的未缓存比例上升。窗口长度和冷缓存不同，不能将它折算为长期网络放大量；也不能只看任务数降约三分之一，就宣称实际到站请求也同比减少。

最终复用既有dispatch增加最多128项的纯内存QUERY归属：新查询按已归属数量分给三个配置成员，正常时保持同一Profile，10个QUERY约为4/3/3；身份不可用或预测明显更慢时借用健康成员，不改原归属，恢复后回归。BODY继续使用原动态负载均衡。未加队列、表、定时器、浏览器参数或缓存override；控制器/服务重启后重新学习，允许短时冷缓存，不声称跨重启完全固定。

同一查询的多轮稳定性、第三身份失效借用/恢复、128项上限，以及原双活、正文公平、优先级预算等51项回归通过。实际HTTP/缓存/查询归属验收另记录，不能用本地均摊测试替代生产网络证据。

## 四、真实验收方法

### 来源与版本

- `d2a4f9c8a`：终态与 WireEvidence 修复。
- `4534d05c2`：三成员列表/BODY正常调度、Owner/CAS配置。
- `889bccb45`：不受旧 Main on_demand 配置影响的成员驻留。
- `5dd7fe4b4`：真实 API 证明与正文验收地址修复；UTC22:44:53重建 Site Access。
- `571e8d7c6`：观察末段发现的共享软资格饥饿修复，只重建Site Access。
- **生产最终代码 `200dffc2d`**：在相同总频率/并发下保持QUERY缓存局部性，BODY保持动态均衡。
- 中间版本12个相关源码 SHA256 已与5dd7匹配，571e含budget的13文件匹配；**最终13文件与200dffc2d完全匹配**。Supervisor始终是11月17日启动的同一容器；Main/Backup1 instance仍分别为 `1947c4dc…` / `02337cf2…`，第三身份为 `a8d0b9a5…`。
- 消息总线、Collector、正文执行服务、Scheduler、API/Web本轮未因本功能重建；此前减负规则已生效。

### 回归

已确认修复100项；三成员/旧双活/恢复/维护/BODY准入主批136项；最终局部18项、生命周期43项；追加恢复54项、Solver/维护兼容82项、正文验收70项、发布后局部23项；软资格重放及公平/三成员回归46项、额外预算回归27项。**批次有重叠，不相加为独立测试总数。** 维护旧fixture补齐真实Page拥有的URL；旧DOM-only恢复fixture按新的真实业务契约更新，保留实际空搜索结果测试。

### 已取得的正文证明

这些请求是有界受控验收，不发布为新消息，不冒充自然新文章补全：

| 身份 | 独立文章 | 实际结果 | Site Access访问段/排队（访问段不是纯网络RTT） |
|---|---:|---|---|
| 新 Backup2 | 2 | HTTP200 + FULL | 1.63s / 2.529s，排队0–2ms |
| Backup1 | 1 | HTTP200 + FULL | 2.584s，排队0ms |
| Main | 1 | HTTP200 + FULL | 4.2s，排队1ms |

前三条通过普通三成员统一 BODY 调度选择，并非固定身份轮流强塞；Main当时被正常列表负载占用，随后单成员容量对照按原准入机制验收。两个纯 BODY 准入延期均 network_ms=0；Main在一个返回约270ms的准入时间后重试一次成功。四篇都 `served_from_shared_capture=false`，不是共享HTML复用。另有第三身份 Login Verify 的独立 FULL证明，单列而不混入上述四条。

### 网络/缓存观察与资源

UTC22:35:08–22:50:08，15分钟只读CDP观察：177条既有搜索/页面记录、41次有界DOM快照、0观察器错误。最终JSON约266.6KB；没有新增站点请求、Provider任务、截图循环或完整抓包，临时观察进程已退出。

其中200含明确缓存83条、未标记缓存93条，另有第三身份人工前401一条。权威ExtraInfo覆盖有限，不把其余不可观察Cookie的请求写成“未携带Cookie”。人工完成之后捕获多组真实网络搜索200，无新401；原生缓存保留。API响应存在 `max-age=300`，所以执行60秒轮询不等于每轮都从网站取到全新数据，不能隐瞒这一外部缓存约束。

整机末段可用内存约14.4GiB，memory PSI近期avg为0；Site Access/Chrome Supervisor分别约2.04/5.60GiB，原10GiB上限无需增加。磁盘约26GiB可用。CPU与I/O仍有整机负载，不宣称零开销，也不把整机内存变化全部归因于新增Chrome。活页记录保持小规模，未观察到持续新增活页；Backup1原有一条CLOSE_PENDING在前后都存在，未以清Profile方式处理。

## 五、统计限制与后续判断

- 末段自然业务窗口没有新 Reuters 文章，因此能证明当前查询覆盖、调度和真实旧文章正文能力，不能证明新消息发布链的新样本成功率。
- 原两身份在加入第三身份前已连续运行较长时间。列表减负、人工验证、发布窗口都是混杂因素；不能把短窗没有复发全部归功于三身份。
- 第三身份初始真实阻断由人工解除，不能计为本轮非付费自动解封成功。未来自然阻断仍按原机制处理，不承诺消灭外部限制。
- 本轮没有主动提交付费解码；完整窗口Solver账本没有新增job。第三身份的付费解码分支未被本轮端到端验证，不能据此宣称三身份全部Provider链路均已验收。
- 当前应保留本配置观察自然生产，按真实新恢复事件、首个网络拒绝/接受、各身份请求密度、QUERY覆盖和新正文结果评估较长时段效果；不再盲目叠加Cookie迁移、重试或等待参数。

最终窗口统计在执行收尾补记中列出；所有发布期和准入延期保留，不以当前绿色状态抹掉历史失败。

## 六、执行收尾补记：最终结果

最终读账截止 UTC2026-11-19 23:29（北京时间2026-11-20 07:29）；两段被动观察均已正常退出，没有安排后续守候、站点探测或自动化任务。

| 分段 | UTC窗口 | Main / Backup1 / 新Backup2的CRAWLER成功 | 必须保留的限制 |
|---|---|---:|---|
| 加第三身份前基线 | 22:13:24–22:29:36 | 78 / 80 / 未注册 | 两身份已经恢复；不能将后续改善全部归功于第三身份 |
| 人工处理第三身份后 | 22:39:57–23:01:46 | 71 / 71 / 71 | 窗口末尾出现队列阻塞，不能称为全窗稳定 |
| 软资格饥饿修复后 | 23:06:30–23:16:26 | 33 / 34 / 34 | 约10分钟，未新增队列超时或失败周期 |
| 最终QUERY归属版本 | 23:18:30–23:28:56 | 42 / 31 / 32 | 105个访问回执；104个窗口内派发并完成的逻辑周期，边界口径不同不相加 |

最终版本窗口：

- 10个活动QUERY、0个TAIL，全部当前成功；每ticker完成10–11个周期。最大成功刷新间隔 **60.093秒**，平均列表周期完成耗时约 **0.83秒**。这是列表采集周期，不是含正文、相关性和发布的消息端到端耗时。
- 该窗口无FAILED/DEFERRED逻辑周期，无新的`site_queue_timeout`或Challenge。三身份均`AVAILABLE / VALID / CRAWLER READY / BODY FULL`，对应能力proof均ready，可选择；没有未完成的活动人工Task。
- 23:29最终队列快照：waiters=0、pending_physical=0、detached_body_claims=0；无任务时global_active=0是正常空闲，和修前“空闲且大量等待”不同。
- 最终10分钟被动采样：100次搜索API浏览器请求、100个响应均200；**10个QUERY各只出现于一个身份，4/3/3归属**。Main40次、Backup1/新Backup2各30次；明确缓存命中87，未标缓存13（5/4/4）。未缓存/重验证与完整新响应不可混为一谈，也不把缺失Cookie ExtraInfo认作未发送Cookie。
- 先前动态轮转的人工后约10分钟采样，搜索API未标缓存69次；最终窗口13次，支持减少重复缓存填充的方向。窗口及缓存冷热不同，**不将69→13宣称为长期固定降幅或Challenge概率降幅**。逻辑采集频率未下降。
- 末段被动采样JSON约119.6KB、0截图、0观察器错误；没有新增生产持久化队列、度量表或常驻进程。最终整机可用内存约15.4GiB、磁盘约26.1GiB，近期memory PSI avg为0；原容器内存上限保持不变。

### 不能被最终绿色状态覆盖的历史

本轮全窗仍保留 **45个`site_queue_timeout`和11个`search_query_failed`失败周期**：45个超时发生在软资格修复前；11个泛化失败中，一条是第三身份最初真实401，其余为多次发布附近的毫秒级失败。没有为呈现效果删除历史。发布附近的底层传输异常没有完整保留，因此不能逐条补写成已证实网络原因，更不能一概算为风控。

滚动24小时指标会继续包含这些旧错误；真实验收必须按发布版本和时间窗分段，不能为了页面变绿清零统计。

### 剩余问题与下一步

1. 非付费主动解封的因果机制仍未证实。本轮修好了“误把原页正常当API恢复”的内部问题；没有把不可靠的Cookie转移/新上下文方案上线。
2. 第三身份人工后约50分钟内未再记录新风控，但中途存在已修复的内部队列中断。最终完整版本仅有约10分钟采样；**不能承诺日级持续可用或给出长期Challenge平均频率**。
3. 末段没有新Reuters文章，4篇FULL为受控既有文章验收；自然新消息正文与发布样本仍待正常生产产生。
4. 保留当前减负、稳定QUERY归属和三身份容量。后续用自然生产日级窗口评估复发与新消息发现，继续分列浏览器缓存和真正到站请求；没有证据前，不继续堆叠Provider、清Profile或调整等待时间。

相关JSON、测试日志、修前/修后预算重放均保存在 `eval/reuters_parallel_recovery_20261120/`，不包含Cookie原值或账号密码；这些临时验收文件未整体加入Git。
