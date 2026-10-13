# 多消息源再失败与桌面登录维护不可用：根因及可执行修复方案

## 2026-10-14 修订：代理恢复后的实施边界

用户确认此前是持续数小时的代理全节点故障，现已恢复。因此下文10/13的UNAVAILABLE、失败计数和各源状态仅为历史事故证据，不能当作10/14现状，也不能据此默认换节点、切国家或重建Chrome。

本次先复查出口与业务源，恢复的原绑定继续使用；A1改为“验证现有路径与备用的真实独立性”，不再默认更换德国节点。A2订阅刷新仅在确认实际订阅来源与当前运维方式后接入，不能凭旧配置推断订阅过期或贸然安装另一套刷新任务。

B（维护窗口/导航解耦）、C（当前延期状态）及D（源级批准后备、Globe RSS XML读取）仍是与此次上游恢复无关的确定性修复。C优先复用现有SourceStatus的current_error/health_group表达当前延期，保留poll_status为上次结果，避免不必要的wire枚举重构。

SSH已由用户恢复，本轮重新取得生产证据并完成窄发布。新取证时间从北京时间10/14 02:38开始（UTC10/13 18:38），不将10/13旧事故JSON改日期冒充新取证。以下原始调查各节保留为历史证据，以本节修订及实施回执为当前状态。

### 10/14复查结果及实际执行

- 六个固定listener真实HTTPS全部200、Egress READY；德国1/2仍各承载两个槽位，原绑定不变。故本轮不默认换节点/国家、不重建Chrome；恢复不能证明主备已跨供应商独立。
- Barron's、InvestorsHub、DIGITIMES列表、Yahoo、HuggingNews、TrendForce及Taiwan已有新的自然成功。Reuters/Investing仍被既有manual_attention门控，不强制解除；DIGITIMES两篇付费正文仍subscription_required，不能以缓存VALID宣称授权恢复。
- 新增独立诊断：IBKR四ticker超时是7496 socat的后端127.0.0.1:4002没有监听，且Gateway/TWS进程不存在，不是官方新闻API失稳。保留交易边界，需人工恢复Gateway登录，不修改client ID、不重启交易组件。
- B已发布：空白维护窗口/会话先创建，导航后置且失败不关窗口；真实桌面两个Identity均可打开GUI与TigerVNC、正常关闭，Reuters打开1.27–1.94秒，Investing6.91秒。Supervisor、已有Profile、Enrichment和Projector容器保持不变。
- C不增加持久化PollState字段：复用last_error_code=`SOURCE_DEFERRED:<reason>`、安全last_error_message、next_dispatch_at/updated_at；poll_status/最后成功仍是历史结果，API current_error/health与前端Badge表达当前延期。这避免严格旧schema消费者被新字段破坏。手工维护和出口状态在维护列表分别显示；没有新增完整多原因状态协议。
- D已发布：Hugging/Yahoo批准DE1+TR1独立HTTP后备，仅传输错误触发，403/429不换路由；Yahoo确定404使用600秒endpoint circuit，RSS独立继续。Globe仅该RSS显式启用同官方feed浏览器后备，覆盖403和传输断连，HTTP单次尝试与浏览器共用30秒；实测20条XML、两篇FULL，已有自然成功。BE IR普通HTTP403、浏览器200却不是有效XML，仍拒绝接纳，没有伪成功或替换成其他新闻源。
- A2增加唯一小时刷新入口，root-only保存两个实际订阅来源、候选快照与stale状态，不自动改活跃Identity出口。首次实际拉取：SSRDOG返回200但不是节点YAML，Flower转换/原始订阅均403，尚无有效新目录；保留既有129节点运行配置。定时刷新已部署，但“新订阅可用、独立候选已验证”尚未完成，不能将timer运行等同于订阅恢复。
- Yahoo新browser/NCP接口适配、真实独立且已认证备用、人工challenge/订阅验收及24–48小时/下一次sweep验收仍未闭环。详见 `docs/message-source-login-repair-20261014.md` 与 `eval/message_source_repair_20261014/`。

日期：2026-10-13。现场只读取证约北京时间 **18:04–18:20**（UTC 10:04–10:20）。本轮仅诊断和制定方案，未切节点、改 Registry、开启/关闭人工维护会话、重启服务或删除数据。

## 1. 结论先行

这次不能再概括成“网络偶发波动，重启一下”。证据链是：

1. **昨天恢复的代理再次失效，而且原先看似四个不同出口实际只有两条德国线路。** `18080/18082` 同为德国标准2，`18081/18083` 同为德国标准1。四条真实 HTTPS 测试全部 ConnectError；Mihomo 原生节点测试德国1/2均503，日志还记录同一上游网关 `116.242.224.5:11056/11057` 连接超时。不是 Reuters/Barron's 等所有站点同时把这些请求风控了。
2. **现有恢复探测在正常运行，但只有“发现原节点还坏着”，没有可执行的有效接替。** 两个德国故障域承载多个 Identity、RSS 和 Yahoo HTTP 后备，关联故障范围很大；所谓“主备”里存在相同实际线路、未认证备用、purpose override 排除、人工门控等，不能承担真实容灾。
3. **桌面维护的直接代码缺陷是把“打开浏览器”和“网页成功导航”绑定成一个原子操作。** `open_login()` 的 `page.goto()` 因代理断链抛错后立即关闭页面，API又没有处理该 Playwright Error，返回500；GUI只在open成功后启动Viewer。人在本应拿来修复 challenge 的工具里，连浏览器都看不到。
4. **前端状态不够真实，不只是显示刷新问题。** egress延期只改 next_dispatch_at/updated_at，保留数小时前的成功或错误。Barron's仍显示成功，实际最后成功约13:56；InvestorsHub显示旧“Error”，当前实际阻断已是出口不可用。必须区分上次结果、当前可用性和正在延期的原因。
5. **上轮只恢复了当时的路径，没有消除结构性缺口。** Yahoo browser/NCP 404、Globe RSS403、BE IR403、Reuters/Investing人工门控均非本轮新出现。上轮的短时成功有证据，但不足以称为可靠恢复；尤其有待补项不能在总体交付中淡化。

本方案不重构双轨 Browser Runtime / Browser Identity，不启用维护 Codex Worker，不增加另一套 agent/watchdog，不重建已有 Profile。重点是：**真实独立出口 + 可持续订阅维护 + 独立可用的人工维护窗口 + 准确状态 + 补齐已确认可行的采集后备。**

## 2. 当前实际状态，不沿用昨天的结论

### 2.1 运行基线及排除项

- 根盘约37GiB空闲（Site Access采样38,750,732,288 bytes，约36.1GiB）；不是再次满盘，storage=ready。
- Site Access：`doxagent-site-access:lifecycle-20261013-r2`，driver READY、epoch4、5 attachments、0 acquiring、无stalled；最近恢复原因max_driver_age。今天凌晨修复过的页面创建/收尾逻辑仍在，不能假定这些补丁未上线。
- Bus：`doxagent-v2:disk-repair-bus-20261013-r2`，已运行约13小时；不是昨天 source-recovery-r1 那个完整镜像。
- Enrichment：`doxagent-v2:source-recovery-enrich-20261012-r1`；Supervisor仍为原9/24容器。
- 真实运行文件核对：runtime.py=`8af3d7ce4a15bdebada46889c67b5b6654a1e1f3eff96ce4bc6d57ce71746311`；service.py=`1c31591ba600f3095b4cc1239f22b8d70a2bbdae589dc69fb8974a0347c2c360`；egress.py=`ee2cf42a2b90ce4b5e6320758202f6e621537c10e9cd3baf66db5ea758a29617`。消息源修复及生命周期修复仍存在，未发现本次由旧镜像覆盖它们造成。
- 六个正式External实例status均running；VNC 5910和5920–5925全部实测返回 `RFB 003.008`。xRDP active，桌面用户当前可登录display10。桌面bridge以真实桌面用户调用list成功、session=null、vnc_ready=true。
- 没有证据指向此刻全局Chrome/CDP死亡或VNC服务全灭。Supervisor累计oom_kill=2属于历史计数，当前内存约2.0GiB/5GiB，不能当作本次故障的时间定位证据。当前I/O压力较低，但CPU竞争存在；不据此停止研究Worker。
- Site Access与Supervisor共享正确网络命名空间（Site network mode=container:Supervisor），localhost CDP/VNC拓扑不是本次断点。

### 2.2 各源分类

时间以下均为北京时间，源的状态以durable PollState为准。

| 来源 | 当前事实 | 本次主要阻断 / 修复方向 |
| --- | --- | --- |
| Barron's ticker news | 四ticker最后成功约13:56–13:57，此后仍保留succeeded | 主德国出口死；备用UNKNOWN认证且也落在另一已死德国线路。旧绿色状态不能表示当前可用 |
| InvestorsHub | 四ticker最后成功约13:31–13:32；旧Error最后attempt约13:56–13:57 | 原External出口失效，后续资源延期没有刷新实际阻断；不是已经证明parser再次坏了 |
| DIGITIMES English | 三ticker最后成功13:49，最后失败14:00；此前已有subscription_required | 原德国线路不可用；付费正文权限问题须在网络恢复后独立验收 |
| Yahoo | 四ticker最后成功约13:43–13:44；现持续RequestError | browser page_js_fetch到 `/xhr/ncp`仍404；独立NCP/RSS都依赖已死18083。上次恢复的是RSS后备，不是browser recipe |
| HuggingNews | 三ticker最后成功13:32；现在持续ConnectError | 唯一feed_proxy_url=18083，没有批准后备；与Yahoo共用故障出口 |
| Reuters | 四ticker持续manual_attention_required | 旧challenge门控仍在，德国资源现在又不可用；恢复网络不等于解除challenge |
| Investing | 四ticker持续manual_attention_required，最后成功仍为10/6 | 旧人工门控未解决；public-markets-nl与InvestorsHub共享已死出口 |
| GlobeNewswire Semiconductors RSS | 持续HTTP403 | 上轮确认Managed读同一feed可得到XML20条，但没有把fallback接入生产，本轮仍是未完成项 |
| BE Newswire/IR | 从未记录成功，22196次连续失败；URL为investor.bloomenergy.com/rss/pressrelease.aspx | 独立HTTP403；此前只按“be_ir_rss”猜source ID会漏掉，真实source=newswire_rss、binding=BE:newswire_rss |
| TrendForce两路、Taiwan RSS、ETNews、The Elec、Tom's Hardware、Globe Search | 现场10:05及10:19 UTC均有近期成功 | 这些并非现场全部失败；Taiwan批准直连后备有效，TrendForce实测访问使用DIRECT。不能扩大成所有消息源同时崩溃 |
| Benzinga/Finnhub/IBKR、Google News Search RSS等 | 对应启用ticker近期成功 | 继续保持现有实现，不借本轮替换稳定源 |

正文06:00 UTC后已有Globe FULL17、TrendForce FULL4、The Elec FULL4+SHORT_FULL1、ETNews FULL5+SHORT_FULL1，部分generic/ETNews同时有egress_unavailable。再次说明采集、最终publisher正文、身份认证要分别验收。

### 2.3 前端是不是只落后了？

只读对照Read与Bus：18:19:53，Bus head=2369633、Read bus checkpoint=2369629，差4；MU的Read对象与当前Bus状态相符。不能把这次大量失败归因于昨晚磁盘整理留下的长时间projector积压。

真正问题是原始PollState的延期表达：`scheduler.py`的两条site_access_deferred分支不更新last_attempt_at/status/error，只更新下次调度等字段。Read忠实投影了这个不完整状态。Barron's“正常”、InvestorsHub旧Error都是这一机制的表现。

## 3. 为什么昨天换完出口，今天又一起坏

### 已确认

| listener / egress | 真实固定节点 | 当前 |
| --- | --- | --- |
| 18080 / jp-standard-6 | 德国标准2 | UNAVAILABLE，连续完整探测失败20轮 |
| 18081 / us-standard-5 | 德国标准1 | UNAVAILABLE，失败19轮 |
| 18082 / nl-standard-2 | 德国标准2 | UNAVAILABLE，失败19轮 |
| 18083 / de-standard-1 | 德国标准1 | UNAVAILABLE，失败19轮 |
| 18084 / server-direct | DIRECT | HTTPS成功，43.163.67.97 |
| 18085 / tr-standard-1 | 土耳其标准1 | 本轮HTTPS成功，185.40.106.84；历史也有连接超时，不能直接称稳定替代 |

德国1/2与土耳其节点的日志均曾出现同一入口网关不同端口的timeout。跨国家标签不一定跨供应商/入口故障域。Mihomo原生delay测试德国1/2失败503、TR289ms，有限另测HK高级3节点均504。没有找到可据此直接宣布可靠的新德国节点。

当前配置是129条inline代理，没有真正HTTP proxy-provider订阅源；live API中Compatible providers只是静态兼容分组，updatedAt零值，不能当自动同步的SSRDOG/Flower订阅。配置目录与systemd timer检查没有发现订阅刷新任务。磁盘与容器挂载的config SHA完全一致，**本次排除了单文件bind mount旧inode未更新这一猜想**。

### 不能越过证据的结论

- 不能证明运营商内部到底是入口端口失效、路由问题、账号/订阅变更还是其他原因；日志足以确认到代理上游链路，不足以断言“订阅过期”。
- 无可追溯provider元数据，不能确认当前129条是否完整含有两份最新订阅。node名字几十个不代表两个供应商都在有效更新。
- 原生API里alive=true但无history，实测delay却失败，alive单字段不能作可用性证明。
- 昨天两条德国路当时确实200且有真实业务成功；今天是新故障。问题在“修到当时能通就收尾”，没有真实独立备用和持续维护闭环。

## 4. 桌面维护为什么连窗口都不出现

实证日志有两个Reuters login-open失败（identity及profile入口），栈为：

```
api identity_login_open / login_open
  -> service.open_identity_login
  -> runtime.open_login
  -> page.goto(..., wait_until="domcontentloaded", timeout=30000)
  -> Playwright Error: net::ERR_CONNECTION_CLOSED
```

当前代码的失败链：

```
创建页面 → 选择VNC目标 → await goto
                            ↓断链
                 gate.close + lease.__aexit__关闭页面
                            ↓
        API未捕获Playwright Error → HTTP500
                            ↓
      bridge未保存session → GUI不执行_launch_viewer
```

所以看到“没有Chrome窗口”不等于Chrome未启动。此时VNC各端口仍有真实RFB握手，Chrome实例仍在。日志直接证明Reuters路径；其余绑定已死德国出口的维护行具有同一失败机制，但本轮未逐行创建维护会话，不声称全部逐行复现。

另有两个代码层明确的潜在问题，需一起窄修：

- `open_login()`没有把既有请求预算传入page获取；select_maintenance及page.__aenter__也在主要try之外。异常可能绕过lease归还；为人工维护新增独立无限等待不可接受。
- relay在每次TCP连接建立时固定upstream；GUI若已有viewer仍存活就直接复用，但取消/验证没有主动结束该Viewer。切换Identity时仅改vnc-target不会迁移旧TCP连接，可能仍显示上个Identity。此项是可从代码推出的缺陷，**尚非本轮现场“所有窗口消失”的已证实原因**。

Playwright官方文档也明确网络不可达等情况会令goto抛错；不能将“浏览器成功创建”和“目标网页成功导航”合并为同一个成功条件。[Page.goto](https://playwright.dev/python/docs/api/class-page#page-goto)

## 5. 修复A：先恢复真正独立、可维护的出口

### A1. 本次恢复步骤

1. 从实际配置来源取回SSRDOG与Flower_SS最新订阅，保留root-only原URL与凭据；记录provider ID、抓取时间、内容hash、节点数、真实endpoint fingerprint及可取得的到期信息。不能只copy本地旧节点名称列表。
2. 节点名加provider前缀避免覆盖；只选小型候选池，每个高风控Identity优先同国家2个、尽可能跨provider/入口网关。先进行Mihomo原生delay、listener真实HTTPS和目标站只读列表三层验证，最多2并发。没有通过就明确不可用，不把“挂着HK/DE”当证据。
3. 先恢复现有主Identity的固定端口和绑定，不重建Profile、不改UA/locale/timezone。换出口属于受控配置维护，记录原/新node、IP和generation；正常drain相关请求后reload，必要时结束该listener旧连接让新连接使用新路由，不动无关Identity。
4. 除非实际同区无路，不把全部高风控账号统一切TR/DIRECT；跨地区变化要明确记录环境差异。普通RSS/API可按实测批准DIRECT或TR后备，但不能据IP查询成功宣称网站授权成功。
5. 检查crawler/body override，至少给真正准备使用的备用组合保留实际选择资格。未认证付费备用仍不可用；不复制Cookie伪造已登录。

### A2. 持续维护：简单确定性程序，不做智能维护平台

- 复用Mihomo订阅能力或root-only小型同步程序维护两份provider快照，**只采用一种刷新入口**。建议默认每小时更新节点目录；不能刷新即清空运行配置，保留上一份可解析版本并暴露stale。Mihomo本身已有provider interval/health-check能力，不需要自建完整代理平台。[官方配置](https://wiki.metacubex.one/en/config/proxy-providers/)
- 固定Egress仍固定到明确node，不把高风控Chrome绑到url-test/load-balance自动漂移组。更新节点目录不等于允许切换已绑定Identity出口；当前节点配置变更/消失才走受控应用步骤。
- 现有 `maintenance_once` / `probe_egress` 继续负责已绑定出口探测和有限退避；补批准候选的可用快照、provider/failure-domain来源。只测试小池，不每60秒扫129个节点。
- 自动业务fallback仍切换完整已注册组合；当前组合都不可用就明确resource_unavailable，不随机改账号出口、不自动解除challenge。备用认证、网站策略/override可用性在配置接纳时报告，不新增全局阻塞校验。
- 主备实际同endpoint/IP/入口网关时，界面与验收标注相关故障风险，不因egress ID不同宣称独立。
- 控制socket/secret继续仅受限管理员访问，不交给桌面用户。应用配置核对磁盘/挂载/live指纹；现有渲染器的原子replace若直接用于单文件bind mount需处理inode问题，尽管本次未发生。

## 6. 修复B：人工维护窗口不再依赖网站可达

最小修改文件：`site_strategy/runtime.py`、`service.py`、`api.py`、`deploy/site-login-admin.py`、`site-login-ui.py`，必要时relay增加诊断；沿现有token与Profile独占协议实现。

1. **打开与导航解耦**：创建/取得受治理空白Page，完成gate、VNC选择、会话登记后立即返回login_token；GUI自动打开Viewer。目标页面导航作为该唯一维护session拥有的有限task执行，不等待网页DOM成功才返回窗口。
2. 导航复用现有owner连接，优先用现有raw-CDP发起正常导航并观察状态，避免重新引入已证实的Playwright取消确认无限等待。不是第二个CDP控制器，不做拦截/stealth。会话保存navigation_state=PENDING/READY/FAILED与脱敏原因。
3. DNS/ConnectError/超时/挑战页面只使navigation=FAILED或待人工处理，**不关闭已创建窗口、不删除token、不自动release人工独占**。页面中可手工重试，GUI显示“出口不可达，浏览器已打开”，而不是“服务未运行”。跨站域名违规仍由原gate拒绝。
4. Chrome/page/显示器确实创建失败才返回结构化维护错误；stage、code、retryable与中文操作建议要到桥接层。HTTP错误不能直接暴露traceback/token。Playwright Error不能继续漏成500。
5. 打开阶段沿既有总预算覆盖page permit、Identity lock、select、绑定；所有已分配lease进入同一try/finally。只修维护入口，不叠加全局watchdog或重写生命周期。
6. session所属导航task在取消/验证/退出收尾时有界结束，收尾只操作该session创建的Page。GUI退出但选择保留维护时，原恢复协议继续有效；不静默关掉用户正在登录的页面。
7. 每次新维护session确认Viewer连接的是当前目标：关闭GUI自己创建的旧Viewer并重新连接5900；关闭Viewer不代表关闭Chrome/session。不要杀桌面用户任意VNC进程。relay read/write异常记录脱敏stage，不继续完全静默。
8. _inventory增加真实egress_status/最后成功探测时间，保留auth状态独立展示；“Profile AVAILABLE”“auth VALID”不能让死出口显示为可操作正常。显示warning但仍允许打开本地浏览器以便诊断，不新增“网络坏就不许打开”的阻塞。

预计正常已有Chrome点击后约5秒能显示窗口；网站导航、challenge、MFA不纳入这5秒承诺。未运行备用Chrome的冷启动独立显示过程，不伪造同一时延保证。

## 7. 修复C：状态与错误必须表达当下，而不是旧结果

- 保留历史last_success_at/last_failure_at，不以延期清空或重写成功记录。
- 按10/14兼容性核验后的替代实施：不增加PollState字段，by-ticker与shared统一复用last_error_code/message记录SOURCE_DEFERRED及安全重试时点，next_dispatch_at记录实际下次调度，60秒目标不变。
- Read/API复用current_error与health_group表达延期，前端标记“上次结果”或“采集延期”；不扩大PollStatus、wire合同或新增独立availability协议。
- 结构化reason使用net_error（ERR_CONNECTION_CLOSED等）、phase、status、egress/Identity、脱敏host与重试时点。修 `classify_exception` 只返回type(Error)的问题，保留安全摘要，不落完整账号URL或秘密。
- Read按原字段投影，API/前端同步窄发布；延期只写原小状态，不重新制造大CDC payload/分发重复写入。尊重既有storage retention与稳定business key。
- manual_attention和egress_unavailable可并存；维护列表分别显示egress/auth/manual状态，消息源状态暂沿单个当前错误，不声称已支持全原因聚合。

## 8. 修复D：补齐独立HTTP采集路径，不把RSS纳入浏览器身份重构

### Yahoo / HuggingNews

- 将Yahoo独立HTTP与SharedFeed的批准route选择复用成小型transport helper：primary+至多1批准fallback、原总deadline、只读传输重试、分别记录路由错误。NCP/RSS仍不继承Chrome登录Cookie。
- HuggingNews为现有feed配置一条真实验证过的后备；目前DIRECT曾403，不能未经测试写DIRECT就宣布恢复。
- Yahoo先修NCP/browser结构性404：使用现有owner对当前ticker页面正常网络观察，适配真实接口/响应；未找到替代前对确定404使用有限endpoint circuit，RSS可继续承载，必须显示是RSS成功。不要每个ticker每60秒反复跑已知失效recipe，更不能从相对时间造EXACT时间。
- 传输路线可用和HTTP200不足以验收：还需有效条目、准确时间和最终publisher正文。保持30min实时窗口/原sweep边界。

### GlobeNewswire RSS / BE IR

- Globe RSS已经有真实Managed可读取同一feed的证据，实施**仅该入口显式启用**的browser fallback；以原owner runtime读取，解开HTML文本包装，再严格XML解析/条目域名/时间校验。不要普遍把所有403转浏览器或扫描多节点。
- factory/adapter注入已有crawler_plane.browser；未配置该fallback的feed保留原403行为。Globe在403或HTTP传输断连后走该显式后备；不先耗两轮HTTP预算。总采集预算沿已有SharedFeed30秒上限，fallback只用剩余预算，不能额外叠加45秒；若该源确需更长，显式源级配置且不超过60秒目标节奏，不能全局拉长。
- 在 `BE:newswire_rss` 实际binding上核对官方IR RSS URL及正常HTTP/现有Managed同一feed。只有真实入口与范围一致才更新；仍拒绝就列官方访问限制，不关闭订阅、不用Globe综合feed冒充BE IR。
- 对403的浏览器fallback是源级明确例外，不破坏“普通feed不因403盲目换出口”的既有测试。

## 9. 修复E：网站侧人工动作，在维护工具可用后再做

1. Reuters维护 `digitimes-de-1` 的Reuters行；验证实际search query和返回结果，不以首页或DIGITIMES行代替。仅解除验证成功的site/purpose健康门控。
2. Investing维护 `public-markets-nl` 的Investing行；与InvestorsHub共享身份但不共享站点challenge状态。先有可用出口，否则重复验证码没有意义。
3. DIGITIMES English主Profile有缓存VALID，但仍须验证两篇当前付费文章；文章权限不足不因主Profile标签VALID而改判FULL。无订阅的备用只用于公开内容。
4. Barron's先恢复原主出口并验证当前两篇正文；只有真实失效再要求重新登录。备用认证一次后才是真实付费fallback。
5. Taiwan仍只抓免费、付费不publish；不要求用户开通会员。Jev仍402，只修provider circuit/可见性，不自动充值；正则轨继续独立工作。

## 10. 发布与验收：这次不再以一张绿色快照结束

### 发布顺序

1. 先修B维护窗口和C错误/延期状态，Site Access+宿主机桌面脚本最小发布，让人为恢复通道先可用；无需全站恢复才能显示窗口。
2. 更新并验证两份真实订阅，应用A固定出口配置，建立至少一个真实独立备用。
3. 发布D采集后备及Yahoo适配；Bus基于当前disk-repair-r2合并，不能退回昨天source-recovery整包。Enrichment只有实际协议字段不兼容时更新客户端，不重写正文抽取。
4. 人工完成E后自然轮询验收；没有经过人工验证的条目仍标待维护。

保存实际镜像ID、逐文件hash、compose完整chain、Registry/manifest版本及可回滚配置；Chrome Supervisor默认不重启，已有Chrome/Profile不重建。只在有证据证明单实例不可控且无人工会话时受控恢复该实例，不能以重启六个Chrome“顺便修好”。本地脏树与其他v2.1/磁盘任务不整体部署。

### 必测用例

- goto真实网络错误：窗口仍打开、token可恢复、维护独占仍有效；验证/取消正确收尾，无HTTP500。
- page获取/VNC选择失败：permit与Profile锁都释放；不是只测goto成功路径。
- 连续维护两个不同Identity：第二次Viewer确实新连接到第二个display，Cookie隔离保持。
- 一个固定出口失效：别的Identity继续运行，批准备用切换符合原组合/认证约束；两ID同故障域不计独立容灾成功。
- 代理目录刷新失败/节点消失/配置指纹不一致：保留旧可用配置或明确失败，不清空正在运行的绑定，不无声换地区。
- HuggingNews/Yahoo一条传输故障后备成功；普通403不扫路由；Globe显式例外返回真实XML而非拒绝页空成功。
- 原last_success不改，当前资源延期理由与UI一致；Read消费新字段不引入大CDC重复写入。
- 当前每源至少10个自然目标周期、每个ticker或共享入口分别留痕；正文至少2篇当前最终publisher文章，付费全文与预览严格区分。
- 技术本轮验收与24–48小时/下一次闭市sweep分开；长期观察尚未完成就写“尚未完成”，不再把短时恢复写成已闭环。

## 11. 证据索引与本轮边界

目录 `eval/message_source_diagnosis_20261013/`：

- bus.json：全部durable轮询状态、实际feed配置；
- site.json：ready、driver、六Identity、近期正文结果；
- details.json：六出口状态/探测退避、真实listener HTTPS对照、site健康；
- host.json：login-open失败栈、当前relay目标、压力及历史cgroup计数；
- config-provenance.json：disk/mounted配置同hash，129条inline；
- clash-live.json：真实Mihomo网络命名空间GET诊断，DE503/TR成功/HK504，未选择节点；
- read-projection.json：Read checkpoint与Bus head、MU前端原始对象对照。

`clash.json`是前置宿主机探测：controller只监听Clash自己的loopback，因此其中Connection refused不是Clash控制服务死亡；该推论已由clash-live纠正。宿主机TCP测试也不替代实际Mihomo测试。调查中有几次路径猜测失败，未据此认定文件/服务不存在。

使用project-discovery技能核对当前脏树、Python/pytest/Ruff、真实部署变化，因此本方案采用实际运行基线而非昨晚镜像假设。未执行浏览器登录、验证码操作、第二CDP attach、生产配置改动或重启；桌面list仅取得脱敏状态并使用其正常本地锁文件。没有启用维护Codex Worker。
