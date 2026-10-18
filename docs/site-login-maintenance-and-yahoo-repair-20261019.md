# Site Login Maintenance 与 Yahoo 恢复记录（2026-10-19）

本轮范围：普通 Login/Challenge 解耦；TARGET_LOST 新页与 Viewer 路径；Supervisor 网络失配的重复发生机制；上轮 Supervisor/资源治理收尾；Yahoo 列表及正文。不是再次重写站点治理，也不启用消息源维护 Codex Worker。

## 1. 根因与证据

### 1.1 用户操作为 Login，内部却恢复旧 Challenge

生产持久 operation `maintenance_op_9281a470b09043349fbe0e7354194c59`、`maintenance_op_edac770e9b394f85aca73925562df1b3`、`maintenance_op_48e91ef0d6b64650a2130aca01f2323a` 均为 **OPEN_LOGIN、session_id=null**，并非用户点错按钮。

`SiteStrategyService.open_identity_login()` 原先看见 `profile.challenge_url` 就执行 `runtime.adopt_challenge()`。该字段本应是旧接口兼容投影，却变成了新登录的路由依据，因而旧 target 已经 LOST 时仍沿用旧 token/target，根本没有分配新登录页。

已修复：

- 新 Login 只使用站点 maintenance/login URL，走新 allocation/token/page，不从 challenge_url 选择地址，也不自动 adopt。
- 已确认 TARGET_LOST 的旧 Challenge 会话占位可以关闭并释放；**原 Task 仍保持待处理**，直到实际验证或关联替换，不能当作已解除风控。
- 仍存活的 Challenge 不被普通 Login 偷走或丢弃；返回明确的 Challenge 维护提示。Open Challenge 才走 exact target 恢复。
- `OPEN_LOGIN + CHALLENGE session_id` 被拒绝，避免从恢复会话入口重新混轨。
- 新登录页再次出现 Challenge 时，以新 allocation 建立关联替代 Task，不再返回旧 LOST target。
- Reopen 失败且没有取得新页引用时关闭空会话；有新页引用的失败仍保留，不能把场景与空占位混为一谈。

### 1.2 DNS 是错误网络命名空间的症状

修复前：

| 对象 | 实际值 |
| --- | --- |
| Site Access | 容器447c5faf…，NetworkMode仍指向`container:b0ada7b5…` |
| 当前 Supervisor | 容器e1e1730e…，不是b0ada7b5… |
| Site namespace | `net:[4026532634]` |
| Supervisor namespace | `net:[4026532586]` |
| 创建时间 | Site先于当前Supervisor约18秒启动 |

宿主机及当前 Supervisor 可解析 Reuters；Site Access 失败。只重绑 Site Access 到当前 Supervisor 后，Reuters/Yahoo DNS 均恢复，无须改公共 DNS 或换解析服务器。该错位也让当前 Chrome 的 loopback CDP/VNC 与 Site 分处两个网络空间，解释了新页/Viewer 的复合失败。

现有 `depends_on.restart: true` 并不是持续绑定保证。[Docker官方说明](https://docs.docker.com/compose/how-tos/startup-order/)将其限定在显式 Compose 更新/重启联动；独立重建网络所有者、错误部署顺序、同容器ID重启但namespace变化，不能仅凭配置声称消费者已重绑。

原健康检查只看Registry/driver及socket文件存在，导致两个容器都显示healthy。已增加：

1. Supervisor Unix协议只读`health`，报告自身 `/proc/self/ns/net`。
2. Site `/readyz` 通过实际socket连接比较双方namespace，失配/不可达返回503；不拿DNS偶尔成功或文件存在代替部署正确性。
3. 主机 `doxagent-site-access-network-guard.timer` 每30秒检查当前容器ID和真实namespace inode；失配时只优雅重建Site，绑定当前Supervisor。既检查重建换ID，也检查同ID重启换namespace。
4. 守护固定两个服务及镜像ID、不构建镜像、不操作Registry、不重启Supervisor、不删Profile；有部署文件锁，Supervisor未就绪或容器未运行时延期，健康状态下不做任何变更。
5. 本轮发布明确按暂停客户端→停Site→升级Supervisor→新建Site→恢复原客户端顺序执行；后续窄Site更新不替换Supervisor。

### 1.3 Viewer 还有独立误报，不能只验证5900有RFB

部署网络恢复后，实测曾有新页已经创建，但操作返回VIEWER_UNAVAILABLE，relay仍指向上一身份。直接连接对应原生VNC端口能读取有效`RFB 003.008`，旧 `_ensure_viewer` 却失败；健康首包实测约0.209秒，超过原0.2秒预算。10次快速中断连接不能弥补这个阈值问题。

已调整为3次、单次1秒的有界原生RFB探测；固定viewer错误码不再被错误地泛化成RuntimeError。另将RFB选择与`Target.activateTarget`焦点动作分离：焦点慢/被拒不等于Viewer不可用，焦点动作最多1秒，并记录不含秘密的错误类型。并非延长整个爬虫超时或反复重启Chrome。

最终维护验收同时检查：操作VIEWER_READY、新页面引用、relay-target等于当前Identity的vnc_port、真实RFB响应、Later后原页可再次接管。单看5900返回协议头不再算通过。

### 1.4 Yahoo：可见SSR卡片与旧NCP契约脱节

Yahoo `/quote/{ticker}/latest-news/` 在可用代理上返回200并包含12张真实 `data-testid=storyitem` 卡片，但没有旧 `/xhr/ncp?queryRef=latestNews` 响应或对应hydration数据。原实现无视卡片，继续主动请求失效NCP，最终404。服务器直连另有429，是不同问题；本轮没有把正文降级为直连，也没有绕过Challenge。

新增 `ssr_dom_article_metadata` 路径：

- 只接受 ticker页 `qsp-news` 模块的真实标题链接、UUID和publisher，不从通用推荐/导航栏取新闻；当前同源Yahoo文章允许新的`/technology/articles/`、`/markets/.../articles/`等路径。
- 卡片只有“2h ago”等相对时间，不伪造精确发布时间。通过浏览器原生同源fetch读取文章NewsArticle JSON-LD `datePublished`；不使用dateModified、不以首次发现时间补洞。
- 每次最多20张卡片、最多3个并行metadata请求，仍受原有总deadline；进程内最多512个公开发布时间缓存、24h失效，不保存Cookie/正文/账户数据。
- 无精确时间/被Challenge的文章不当作空成功。保留native network/旧hydration兼容及NCP/RSS业务优先级，不把历史卡片改成新消息。
- RSS/API与Jev、监测词、60s轮询、30min freshness、闭市sweep、去重及Taiwan付费排除均未更改。

## 2. 发布与代码边界

功能提交：`10a15ab6`（两路径/namespace/Yahoo）、`3abc8c47`（焦点解耦）、`b1f8237c`（RFB探测）。均已推送main并在生产应用。

生产修复前实际运行的是基线`99d2da1d`镜像，但服务器checkout仍为`160f352e`。本轮没有用旧checkout全量重建覆盖其他模块：采用**精确运行镜像作为父层 + 已提交修复包**的不可变镜像，记录父镜像digest与修复commit并验证依赖DiffID前缀。不能把修复镜像的label理解为所有应用文件都来自该main提交。

将99d2da1d已有的两个站点资源修复显式保留并纳入main：冷/退役Profile缓存维护及快照retention补偿。未把本地其它SDK/W3/初始化/Jev等脏改动一起提交或覆盖生产。

最终Site与Supervisor镜像均为`maintenance-b1f8237c`，之前刻意暂缓的Supervisor细化已应用，不再是“准备但未部署”。现有Profile/Cookie保留，无复制账号Cookie，无重新创建Profile目录；业务客户端仅停启原容器，镜像与ID不变。Viewer细化升级前只允许丢弃本轮acceptance自己创建的测试场景，按operation idempotency前缀核验并留下TARGET_LOST记录，拒绝静默丢弃其他人工会话。

主机守护为固定部署修复程序，不是消息源维护Codex Worker，不使用模型、Git补丁或自动合并。

## 3. 验收

### 回归

以最终功能提交b1f8237c的干净Git archive执行Site治理/维护/隔离/生命周期/爬虫/正文/Yahoo扩大回归：**342 passed / 1 Windows平台skip，148.76秒**；curl_cffi有Windows Proactor兼容警告。收据见`eval/site_maintenance_recovery_20261019/regression.json`。旧生命周期测试不再把“普通登录自动接管Challenge”当作正确契约，改为显式Challenge恢复，同时仍验证原页保护和业务阻断。

新增badcase覆盖：普通Login不adopt LOST、存活Challenge不被偷走、Login拒绝Challenge session、无target失败Reopen不留下空会话、新Challenge关联替代旧LOST、同ID/换IDnamespace变化、readiness失配503逻辑、焦点失败独立、真实TCP延迟250ms RFB、Yahoo精确时间/缓存/缺时间/403/过期消息。

### 生产真实结果

- Reuters主/备普通Open Login均为新LOGIN/token/target，VIEWER_READY；约5.85/6.89秒。测试页随后精确Close，没有输入账户或声称完成登录。
- Reuters主/备TARGET_LOST Reopen均得到新关联Task和新页，VIEWER_READY；约0.64/0.96秒（浏览器已热）。Later→Open Challenge→Later均成功，relay绑定所选Identity，保留新原页。
- Yahoo备用、WSJ冷身份打开Viewer通过真实绑定验收；冷启动约8–12秒，不能承诺每次5秒。阶段/ACK不阻塞GUI主线程。
- 另外三个原LOST任务（Investing备用、Barron's主、Investing主）均真实Reopen成功、Viewer绑定正确，约14.49/11.41/18.71秒；新原页保留并Later释放Viewer。因此旧的五条当前任务均已取得可操作的新关联场景，不是仅改成READY文字。
- 正式Yahoo Runtime对MU/INTC/BE分别返回12条；各取两篇真实文章均FULL。只读canary不发布消息、不篡改时间和业务checkpoint；最终收据保留精确pubDate与有效Identity。
- 网络守护处于active，Site/Supervisor namespace一致，Site内部Reuters/Yahoo DNS通过，readyz通过；VNC宿主端口仍限127.0.0.1。

真实操作详见 `eval/site_maintenance_recovery_20261019/receipts/site/`；最终namespace、DNS、image/source哈希和root空间见`receipts/host/final.json`。原始失败记录不改写成成功。

## 4. 你接下来如何维护

本轮只修维护基础设施，不自动登录、不解验证码、不确认会员权益。Reuters主/备、Barron's主、Investing主/备已有新原页，可在Challenges选对应Identity → Open，人工完成后Verify；不要再选被superseded的旧任务。若后续原页确实消失再用Reopen Lost Page。DIGITIMES英文仍需单独确认账号登录与阅读权限，不能用列表200替代订阅正文验收。

普通Login和Challenge不再互相暗中切换。若仍有**存活**Challenge，普通Login会明确要求使用Challenge页签，这是保护现场，不是旧LOST历史劫持。需要暂离使用Later，关闭Viewer不代表任务已完成。

Yahoo已不再是“旧404遗留待办”；但未伪造10个自然轮询周期或7天稳定性。闭市/实时窗口照旧运行，长期内存/磁盘净增长观察仍需时间。初查根盘约43GiB、终态44,467,392,512字节（约41.4GiB）余量，本轮未追加删除缓存、镜像、业务数据或Profile。

## 5. 证据与恢复

`eval/site_maintenance_recovery_20261019/`保存定向发布/验收/导出程序、回归收据及不含秘密的生产JSON。Registry一致备份、env.before和Profile只留在服务器受限目录，**不导出/不入Git**。回滚须同时保持namespace绑定与新维护协议兼容；不要单独回退Supervisor、让Site继续连旧网络空间，也不要覆盖后续新Task/认证状态。
