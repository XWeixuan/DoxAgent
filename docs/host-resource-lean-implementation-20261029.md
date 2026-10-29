# 全局资源精益修复实施记录

依据：`host-resource-lean-architecture-and-repair-plan-20261029.md`（历史坏例复审版）。

本轮范围：R0—R8逐包实施、整体review、正式build发布，启动前清退用户授权的历史负担，上线后90分钟低频观察。24/72小时稳定性仍是后续验收边界，不能以90分钟代替。

|包|状态|证据/结果|
|---|---|---|
|R0|本地/发布前取证完成|05:50五角色仍停止；随后发布前PSI没有持续内存/I/O停顿，磁盘87GiB可用；冻结容器版本、配置、数据挂载及其它会话改动指纹|
|R1|实现、回归并上线|普通publisher恢复转按需；删除全scope冷canary、重复旧恢复迁移；贯通真实请求半开，保留人工/付费/必要source恢复|
|R2|实现并上线，本地及Linux真实Chromium隔离/退出通过|必要身份独立worker/Node不合并；显式public_context或public_identity，零需求退出；匿名Profile一旦人工证明FULL则转冷Profile复用|
|R3|实现、回归并上线，性能验收保留缺口|物理关闭成功才归还槽；失败保留占用并可重试；页面池按真实并发/下次周期保留；冻结仅限明确允许的三站，Reuters业务页不冻结；减少Barron's重复整DOM质量解析|
|R4|实现、回归、启动清理并上线|兼容生产者进入live；一次旧任务迁移后领取不再查旧表；provider期限不截180秒；Raw/Standard准备与短提交分离；不可变result有限缓存，ticker元数据独立|
|R5|实现、回归并上线，真实Serving契约核对通过|展开前跳过无Serving消费者的effect/namespace和task inputs/receipt；正文搜索每digest一次，历史/权限/Unicode保留；派生索引按有界cursor维护|
|R6|实现、回归并在实际空闲窗口切换Worker|owner一次归属快照、CLOSED退出恢复；启动PID取新快照，发信号保留启动时刻核验；只维护已退休Profile；宿主旧Reuters自动启动已退役|
|R7|容量机制/受控回归完成，真实成本待观察|生产全局8页、BODY2/borrow=false，已是8核配置；删除borrow额外魔数。现有20ticker共享正文/ARTICLE_ONCE/事务内0Native I/O回归通过，不把该回放冒充三站20ticker/30s实网证明|
|R8|正式build/发布已执行；90分钟观察结果见文末|正式角色重建、离线历史清理、实际窗口和修复过程均保留，不将部署完成等同性能通过|

清理边界：清退旧文例行复查、已完成/重复工作、过期无交付义务任务与失效历史补扫；保留唯一发布身份、完整正文和checkpoint。尚未交付的有效消息和真实内容更新先对账，不能按文章年龄一刀切删除。清理收据记录原因和数量，不伪造成功。

## 实现与整体review

1. 不恢复旧共享driver故障域。普通publisher的共享仅限其匿名执行分区；必要source和凭证身份保留原Profile、启动方式、出口与独立driver。公开站并不被自动认定为无状态：已验证匿名身份保留冷Profile；既有非聚合消息源对应site不迁移。
2. 公共分区真实双Context测试使用本机受控Chromium：同一Browser、Cookie隔离，最后任务结束后Browser断开、运行域为空，约0.61—1.03秒。它是本地生命周期证明，不是公网站点耗时或Linux生产资源证明。清理错误不丢弃已下载结果；只针对精确PID/启动时刻/公共Profile参数收尾，未确认则保留占用，不重复创建另一套环境。
3. 原有HTTP publisher隔离池、live prepare/commit、结果spool、配置周期和独立频道合同继续使用，不再增加一套队列或压力门禁。本轮没有改出口、降低FULL标准、关闭必要频道或修改W1/W2/W3模型/并发/ready契约。
4. `lean_startup`只在正式迁移角色执行：先安装小型Read派生表/索引；退旧复查并保留真实更新检查责任；旧BODY按原捕获准入迁移有效续作、保留not_before；清退确已过期/不可读旧任务且留收据；离线接纳持久列表delta，已接纳仅ack，过期空delta不重新BODY；完成已有Raw后链路；只清理SWEEP/SOURCE_SWEEP历史任务。不全库备份/VACUUM，不清Profile/业务内容。
5. 旧Raw/Standard兼容收尾拆出prepare和标量commit，正常live路径不再回到旧job/raw恢复轮询。历史兼容方法仍保留供离线迁移及已有接口使用，不能把它们的全部旧事务实现误称为已删除；本次生产热路径退出旧表以迁移完成标记证明。
6. 网络guard原本已对停止容器返回DEFERRED，本轮保留这一行为，并修正重绑时丢失正式Compose覆盖文件的问题。没有新增“看到PSI就停业务”的策略。
7. Worker代码没有现成的无损drain接口；不能把方案文字当成已实现协议。发布前核对真实active/cold/queue，在实际空闲窗口替换，若仍有在途则等待其完成，不能强制取消W任务。仅管理发布owner切换，不新增日常业务限制。
8. 本地另有未提交交易/前端及W3提示词改动，属于其它会话工作；保留文件及指纹，不将未review内容夹带进本功能镜像。远端唯一server Compose私有差异为Collector的host-gateway，与本地一致，纳入核对。

## 回归记录（计数有交集，不相加为唯一测试数）

- Identity/Recovery/Reuters/Storage/Pipeline/Read第一组124项通过。
- Message Bus/live/BODY/Worker/W3和生产读契约综合141项通过（269秒）。
- 周期兑现、读模型、维护与新精益测试57项通过；最终安全专项31项通过。
- 新精益测试覆盖清理失败保留槽、共享公共执行/退出、成功下载不被清理错误覆盖、provider600秒下界、owner扫描、搜索历史与Unicode、原捕获有效续作、Site版本化迁移、不可变正文复用。
- 综合回归发现测试冻结时钟在懒加载模块中泄漏，修正测试导入边界后全组通过；另更新已过期的源清单、pressure门禁及expire_runs mock契约，没有为迁就旧测试恢复已取消的硬阻断。
- 证据：`eval/host-resource-redesign-20261029/implementation/`；以下追加生产发布与观察，不宣称长期稳定性已达标。

发布前整体review补充：发现维护循环仍先预热后回收，且为几百个普通publisher读取预热状态、在隔离worker路径计算无效的全Profile轮换标志。本轮改为先回收、仅检查必要source热身份，原生回收先过滤非External；56项生命周期/Reuters回归通过，1项平台相关跳过。

## 正式发布与启动前减负

正式发布由`storage_release.py`基于已提交源码归档和稳定依赖层build，通过原`doxagent-v2` Compose及完整私有/release覆盖文件替换容器。没有使用临时服务容器、代码覆盖层或宿主临时Python进程代替正式服务。迁移使用原正式`v2-migrate`一次性角色，结束后恢复原迁移命令。没有执行全库备份、VACUUM、全库JSON回填或删除Volume/Profile。

启动前收据：

|动作|数量|保留内容|
|---|---:|---|
|安装小型Read搜索派生结构|1|业务消息、正文和历史权限不变|
|清退历史补扫|4|原始捕获和checkpoint保留；不伪造采集成功|
|清退已无必要交付义务的旧兼容recheck|325|完整正文、发布身份及原记录保留|
|延后已完整旧文的例行复查|298|不清空正文、不重置first_seen；真实实质更新和后续到期责任保留|
|普通publisher按需执行配置|490 Context / 26冷Profile|保留原目录、Cookie和版本化before收据|
|普通publisher旧恢复转按需|5|保留历史，不标成虚假RESOLVED|
|受保护或人工场景|7|保留原业务身份、Profile和恢复义务|

生产确认`legacy-body-complete`已持久化，旧queued/running/retry_wait为0；没有可迁移的唯一有效新稿续作被按年龄丢弃。后续旧生产入口直接进入live，不重新启用旧job领取循环。详细收据在`/data/maintenance/lean-20261029/`和`/var/lib/doxagent/releases/lean-20261029/`。

发布角色包含Supervisor、Site、API、Projector、Maintenance、BODY、Bus、Collector及Worker。角色只在其必要代码变化后替换；后续Site/Collector专项修复不要求无关角色同一hash。Worker在08:30:23完成切换，切换前两次资源检查确认active/cold/queued/cleanup均空；等待期间没有取消IONQ初始化或W3任务，也没有新增W1/W2/W3门禁或修改模型、并发、ready策略。原Runtime Scheduler等未涉及角色保持原运行版本。

## 生产发现与追加修复

### 1. 预算超时引起额外Browser启动

原`_execute_deadlined`将HTTP/DB/队列等通用超时也变成Browser failover canary，初次上线因此额外创建公开HTTP来源的浏览器/Node。已移除通用Timeout的无条件Browser恢复；真实类型化访问失败仍保留必要恢复。同时删除已准入冷启动内部反复等待全局PSI恢复的循环，保留现有真实槽位、身份维护互斥和有界操作预算。

### 2. 必要页面复用与空闲脚本成本

对明确允许的Barron's、InvestorsHub、Investing成功业务页空闲时冻结，下次真实请求前解冻；Reuters普通业务页即使调用者要求也不冻结，Yahoo未被顺带加入。没有统一五秒截断，也没有为省资源复用旧列表冒充新请求。

首次Site收尾和策略修正前后，同一Investing原生实例的target由约58降到6、RSS求和由约4.4GiB降到1.3GiB；这是页面关闭与冻结的组合对照，不能全部归因于freeze，也不能把RSS相加当作私有内存。后续冷导航仍会生成iframe，故验收看真实进程/cgroup走势，不把保留DOM的iframe数量直接当泄漏或清零为目标。

### 3. 原生Chrome冷启动端口碰撞：本轮实测暴露的严重遗漏

实际发现`dowjones-main`与`reuters-backup`同时使用9222，两个CDP地址指向同一Reuters浏览器。每身份锁没有保护跨身份的启动资源分配：分配器只看已启动实例，首个Chrome监听前，另一启动者可以拿到相同端口/display/VNC。

Git追溯：未预占资源的实现始于`48f398fd7`，2026-09-22 16:34:38。它是旧缺陷，本轮完整重建后的并发冷启动将它触发；**本轮发布检查未提前发现，不能以代码较旧免责**。发现后短暂停止消息浏览器域，保留数据/Profile，正式重建恢复。

修复是启动首个await前同步预占端口、display、VNC和Profile占用；取消同样收尾，未确认退出不归还预占；启动/重连时仅做一次CDP browser PID与实际启动PID核对。没有在每个业务请求上再加PID检查，也没有把全部身份冷启动改成串行。8身份并发冷启动、取消收尾、错PID拒绝测试通过；恢复后7个原生身份端口/PID逐一确认独立。同时移除周期性冷预热，真实高频访问继续复用已有运行环境。

### 4. 实际取数与列表证据关联缺陷

- Site轻HTTP成功将真实wire时间放在AccessResult顶层，没有identity attempts；旧客户端只读attempts，导致Globe真实取数甚至新稿已发布，周期却全记UNPROVEN_LISTING。现在读取真实顶层wire时间；没有wire字段时仍不能凭RPC时间补造证据。
- CTEE多页及Defense多个并行feed被“只能有一个成功请求”的默认校验误拒。现在按已解析页/最终endpoint的physical_request_id绑定；一个正常feed不能覆盖失败兄弟，bootstrap/错误响应不能充当列表证据。79项相关回归通过。
- Collector重启时只对内存`self.desired`做旧单元退役，不能发现启动前持久化的过期query hash。Google两个已替换查询仍被记required，新增查询正常执行却显示额外20个漏轮次/10分钟。现在以本次完整编译集合对账持久单元，原子写DISABLED边界并保留旧checkpoint/历史；20项Collector回归通过。**不回改退役时点以前的分母，统计修正不计作网站性能提升。**

### 5. BODY与CRAWLER路由的错误耦合

DIGITIMES列表已切到匿名备用身份，BODY无法使用其已认证主身份，被`candidate_activation_required`挡在下载前。修复为配置主身份可在通过原认证、人工维护、恢复和冷却检查后，直接承担真实BODY请求，不改变健康的列表活跃路由；付费备用身份原有验证要求不被删除。102项恢复/故障转移回归通过。

真实请求随后使用`digitimes-nl-1`、约7.94秒，返回200及`subscription_required/ENTITLEMENT_MISSING`，说明程序门禁已解除，但现有会话的订阅能力仍不足。这个样本不计FULL、不绕过权益、不批量重试旧文章；恢复真实订阅权限是未完成的外部访问事项。

### 6. 物理实例变更与target旧账

复核发现旧逻辑仅清Managed实例旧target，External在实例替换后会留下旧LIVE/CLOSE_PENDING事实；反过来，Managed同一实例仅从UNHEALTHY恢复AVAILABLE也会误清当前target。现在仅在实际instance_id变更时退役该身份旧实例事实，Managed与External一致；同实例恢复不动现存target，不新增周期性全表对账。

24项identity/Reuters回归通过。实例变更使用启动/重连实际provenance，不以健康状态变化冒充物理进程退出。

### 7. 全局统计查询遗漏既有时间索引

全局事件诊断在6秒SQL预算内不能完成，逐站时间查询明显不同；排查发现数据库已有`julianday(occurred_at/completed_at)`时间索引，但旧统计使用字符串范围，未利用对应时间索引，可能在Registry锁内扫描历史。已将全局access/body统计改用已有UTC表达式索引，逐站请求继续使用已有site/time索引，不新增DDL、投影或宽表；EXPLAIN明确SEARCH范围，跨时区边界计数回归通过。真实统计接口对10分钟652条access、6条body的汇总约43毫秒。

整体复核还将统计放入现有独立只读scope，API在执行线程读取，避免大窗口统计占Registry writer锁或API事件循环。WAL并发测试证明：writer持有未提交事务期间统计能完成，且不暴露未提交事件。复用现有读预算，不新增统计投影/队列。末尾这些发布的观察时间短于90分钟，不能说所有最终代码均连续跑满90分钟。

## 生命周期、Serving与运维收尾验证

- Linux正式Site容器以实际10001服务用户运行双Context实验：共享Browser、Cookie隔离，最后任务退出后Browser断开且runtime为空，约1.77秒。第一次用root运行被Chromium sandbox拒绝；改为真实服务用户验证通过，没有为通过测试关闭sandbox。
- 正式Bus→Projector→Read有连续新stream记录，旧队列仍为0。生产API镜像内用真实Read数据执行现有API转换/闭合schema校验：9个已启用ticker共18条MessageSummary、32条代表性SourceStatus通过，包含IR端点；IONQ仍初始化、没有订阅和消息，单独保留其0样本状态。此项是生产数据/代码契约验证，不冒称已完成浏览器登录后的全UI交互。
- 原宿主Reuters Chrome/relay自动启动已disable，未删Profile/config/relay程序；消息访问采用现有Site scope。网络guard更新后重新启用，确认Site与Supervisor使用同一真实net namespace，重绑时保留原完整Compose覆盖文件。
- 过渡镜像按引用收据逐轮清退；最终数量见文末。保留运行容器、当前/前一可回退版本、依赖、Compose和动态template引用。不删除Volume，不强删仍有引用镜像，不把正常回退镜像当作“旧版垃圾”。

## 观察口径与阶段结果

临时采集窗口固定为**07:20:36.537512—08:50:36.537512北京时间**。仅绕过Message Bus休市门禁，不改共享交易日历、配置周期、身份规则或W1/W2/W3。初次启动、失败、重建停顿、延期和窗口末尾在途均保留。后续修复没有重置这90分钟窗口。

|十分钟快照结束时点|全部应执行周期|按时取网并解析周期|全周期覆盖|程序FULL/SHORT_FULL（真实首次见canonical）|必需交付≤10秒|
|---|---:|---:|---:|---:|---:|
|08:05:44|3961|2976|75.13%|1/10|8/20|
|08:18:04|3961|3477|87.78%|1/1|0/4|
|08:30:08|3947|3515|89.05%|2/2|4/4|

分母变化包括窗口内过期查询单元正式退役；不可将这部分变化解释成采集加速。08:30四次交付均值2.70秒、最大3.57秒，但只有两篇新稿；08:18四次交付均值14.16秒、最大15.15秒。所有样本均保留，因此不能拿末尾小样本宣布10秒或80%稳定通过。

完整正文仍需publisher抽检，不能只用程序标签。抽查ETNews新稿为862字符、10个非空段落；HuggingNews为1801字符、8段，包含导航杂质仍需区分内容质量。Hugging样例首次见08:09:45.14、首次BODY08:09:46.75、取得正文08:09:55.44，BODY路径约8.7秒，最终四ticker发布约13—15秒；它与08:28以后约1秒取正文、2—4秒交付的样本共同表明，尾部获取成本仍未消失。

|宿主/容器快照|MemAvailable|CPU some avg60|内存 some avg60|I/O some avg60|Site / Supervisor cgroup当前内存|
|---|---:|---:|---:|---:|---|
|07:59:12|约2.61GiB|21.36%|1.08%|13.07%|3.68 / 4.52GiB|
|08:10:53|约2.06GiB|25.46%|1.12%|19.15%|4.25 / 4.36GiB|
|08:29:07|约3.75GiB|9.12%|0.12%|2.84%|2.69 / 4.17GiB|
|08:42:17|约4.65GiB|13.26%|0.07%|5.11%|2.15 / 3.62GiB|

该区间相关cgroup没有memmax/OOM事件；这只证明该观察段，不能证明下一交易时段或72小时。08:00附近一次本轮只读诊断SQL使用了不当历史连接，SSH超时后残留，后续核对PID/start-time/只读acquisition FD后在08:10终止；此阶段观察受本轮诊断负载干扰，已保留，不将其I/O全归生产代码。后续查询加时间边界和SQLite执行预算。

08:32—08:42的Site实际CRAWLER完成/延期集合：Yahoo175项全成功，network均值1.14s/P95 4.76s；Investing88项87成功，均值1.43s/P95 4.86s；Globe245项全成功，均值0.40s/P95 0.71s；InvestorsHub89项76成功、6运行不可用、7预算延期，均值6.00s/P95 19.19s，其中76项浏览器阶段均值4.20s/P95 8.44s。阶段样本不包含没有返回阶段的轮次，含窗口内冷启动，不能冒充纯warm P95；阶段嵌套不相加。

这些数据明确表明：IHub仍是30秒/20ticker的容量缺口。若每ticker至少一个物理请求、浏览器占槽均值仍4.2秒，则20/30×4.2≈2.8个持续槽位，已超过2槽假设，尚未计BODY和长尾。该计算不是CPU成本测量，不据此盲目扩容；应继续降低实际单次网络/执行成本并验证原生进程CPU，不能宣布三站未来30秒能力已经通过。Barron's本窗口阶段样本不足，同样不补造结论。

## 尚未通过的业务验收与后续边界

1. 近100%周期兑现未通过。Reuters仍有真实人工Challenge，IBKR持续Timeout；Bloom两个非press-release RSS端点返回403；Benzinga、InvestorsHub等仍有失败/延期。没有取消这些必需来源/频道来制造达标。Globe/CTEE/Defense的证据修复与网站实际时效分别记账。
2. 全量真实新稿FULL>80%未被证明。窗口内DIGITIMES一批真实新稿因准入/订阅权限失败仍保留在分母；不能用排除付费失败或只选新近两篇FULL来宣告通过。
3. 全量采集至可消费发布<10秒未通过。保留失败、未完成及14—24秒交付；不把成功轮询后台耗时当作端到端时延。不同周期的全局fresh gap均值混合了30/60/600秒，不能拿其约55秒直接判每个30秒源的能力。
4. 8核16G及最多20ticker：按物理端点/查询/频道编译请求需求，页面全局8、BODY2且borrow=false，未按固定9ticker设阈值，也未残留旧4核临时适配。20ticker本地共享正文/短事务回放已通过，不等于Barron's/IHub/Investing全部20ticker、30秒实网承载证明。
5. HTTP的真实发送和解析证明不等同于掌握publisher全部未曝光新闻，也不能保证CDN后源站每次都重新生成内容；receipt保留validator/age，不能补造上游覆盖。24/72小时、完整交易时段、资源平台和故障恢复仍需真实持续窗口，不由本轮90分钟代替。

上述未通过项保持开放，不以“服务healthy、无OOM或部署成功”关闭性能与长期稳定性验收。

## 固定90分钟验收结论

**本轮业务验收未通过。** 原窗口未因发布、停机或追加修复重新计时，末尾改动仅有较短观察/定向验证，不能宣称最终代码连续稳定运行90分钟。

|指标|07:20:36.537512—08:50:36.537512结果|判定|
|---|---|---|
|全部应执行周期|35,593|包含失败、逾期、修复暂停及末尾在途|
|按时真实取网并通过列表解析|24,667 / 35,593，**69.30%**|未接近100%|
|无启动记录周期 / 截止时在途|6,302 / 2|不从分母剔除|
|canonical首次见、适用正文补全文章|29篇，16篇FULL/SHORT_FULL，**55.17%**|未超过80%；程序标签仍需publisher抽检|
|必需article×ticker交付|43次，42次已发布|AviationWeek的1次NO_PUBLIC_BODY保留为未可消费|
|10秒内可消费|23 / 43，**53.49%**|未达到普遍10秒以内|
|已完成交付耗时|均值9.92s / P95 22.79s / 最大24.02s|不能用成功样本均值低于10秒替代全量验收|

Source阶段的共同缺口包含本轮端口修复停机：以MU/ETNews持久轮次为例，07:37:41.18至07:54:16.93没有新dispatch，间隔995.75秒，约16分36秒。该停机不能从本轮分母删除，也不能机械归为正常运行能力下降；另列末尾窗口说明正常运行仍有真实缺口。

**最后完整10分钟（08:40:36—08:50:36）**：3,462 / 3,941按时周期，87.85%；28次无启动、0末尾在途。4篇适用正文文章仅2篇完整；6次必需交付全完成，其中4次≤10秒，均值7.41秒、最大22.79秒。该窗口同样未达标，不能只引用08:30较好片段。

13篇非完整正文中，9篇为DIGITIMES；另有AviationWeek订阅不足、Google来源HTTP失败、Yahoo及Investing获取超时各1篇。不能排除这些失败后宣告80%通过。固定窗口原始收据：`/data/maintenance/lean-20261029/observations/pipeline-2026-10-29T005036.537512+0000.json`；本地摘要`pipeline-fixed90.json`及逐源/逐文章数据保存在实施证据目录。

08:51确认临时控制revision 7为`EXPIRED/effective=false`，Collector已应用该revision，恢复原交易日历；没有为了继续测试再次延长临时开关。关闭后的既存任务可收尾，旧队列仍为0，最新消息及Read投影继续一致。

## 窗口后必要修复与定向复核

最后10分钟IHub 90轮中：26次CAPTURED、24次UNPROVEN_LISTING、15次SiteAccessError、25次site_queue_timeout。**其中40次是真实访问/准入失败，不能用证据修正掩盖。**

继续追到两项程序机制：

1. `_observed`原先给同次导航中的全部物理请求套用最终attempt的200；redirect与最终文档因此被当成两份成功列表。现记录各实际response的status，列表证明排除301/302等重定向，仍允许有有效基线的304，不用“最后一条一定正确”的启发式冒认结果。
2. `FetchAnchors.take`仍按创建时间固定五分钟淘汰，即使高频真实取网一直使用该页，也会强制关闭、重建与重定向。现删除固定页龄，保留真实空闲expiry、revision/egress变化、页面关闭、Challenge及失败处理。每轮仍真实请求；没有将旧DOM当作新列表。85项周期/freshness/Investing回归通过，包含长龄活跃页保留及真正空闲回收。

以上`2ea3a48c`在窗口后正式重建Site/Collector。第一次手工URL探针MU/INTC成功，SNOW耗尽预算，失败收据保留；随后按生产binding及原`MarketTickerAdapter`复测：MU 3.76秒/13条、INTC 4.18秒/8条、SNOW 5.80秒/12条，均取得一份精确列表证明。INTC真实301→200只将200关联为有效列表；SNOW使用生产配置的NYSE地址。两轮输入/暖态不同，不将改善全部归因于代码，更不把三次定向抓取当作新90分钟周期验收。

统计读隔离`f03b6d8f`随后正式重建Site，真实接口10分钟699条访问及6条正文汇总约33.6毫秒；并发writer/未提交数据测试通过。没有追加新的业务压力门禁。活跃页取消固定寿命后的长期target/内存走势，仍需正常交易时段及24/72小时验证。

最终运行代码：Site `f03b6d8f`，Collector `2ea3a48c`，Worker `c691ea40`，Bus/BODY `0281dbc5`，Supervisor `89eeaac0`，API/Projector/Maintenance `fea28e7f`。它们均包含各角色必要改动；不存在schema或协议要求所有角色同hash的变更，文档提交不触发无关容器重启。

## 最终收尾与未关闭事项

09:54的3秒宿主采样：CPU忙39.5%、I/O wait 1.5%，MemAvailable约2.85GiB。Site约3.60GiB、Supervisor约3.32GiB、Worker约1.57GiB；六个消息/浏览器/Worker角色均running，有健康检查的角色healthy，当前实例restart/OOM均0。09:51另一3秒样本CPU忙73.3%、MemAvailable约2.25GiB，说明负载和工作集仍明显波动，**没有形成足以证明长期安全的资源平台**，不能只选08:49约5GiB available的低负载片段。

默认24小时统计接口也已实测：56,323条访问及2,948条正文，约2.27秒完成；不是只验证10分钟小窗口。更多ticker、多日大窗口及长期历史增长仍需容量验证，不以读隔离代替实际工作量治理。

累计清退15个本轮确无运行/回退/依赖/Compose/template引用的过渡镜像tag，0错误；有引用历史镜像继续保护。保留镜像不等于任意旧版本均可单独回滚：薄搜索/派生格式须保留兼容Reader及API/Projector组合，不以回滚为由恢复过期任务或覆盖生产数据。

原长交易时段、24/72小时、20ticker及30秒容量验收保持开放；IHub真实访问/准入失败与尾延迟、Reuters访问能力、IBKR进展、付费正文可用性及部分HTTP端点拒绝仍需闭环。窗口后定向抓取与正常进程健康不替代这些业务结果。下一步优先恢复实际访问能力并在配置周期下逐单元复验IHub真实成本；必要站点恢复与新文章BODY/交付必须一起核对，不能只修统计。

本轮没有修改共享交易日历、W1/W2/W3模型或业务并发，没有降低完整正文标准、删除必需频道或恢复旧共享Node故障域。其它会话未提交的交易、前端及W3提示词文件保留；已冻结的交易/前端指纹复核未变，没有将未review代码夹带到不可变发布归档。
