# Site Access 双轨 Browser Runtime 与 Browser Identity 可执行实施方案

日期：2026-09-22。状态：代码勘察与实施设计，尚未实施生产改造。

## 1. 目标与明确决策

保留 Managed Playwright Runtime，新增 External Chrome Runtime；正文补全、新消息爬虫继续调用同一 Site Access API。将浏览器资源的所有权从单站点 Profile 提升到 Browser Identity：多个 Site 可以显式引用同一 Identity，共享一个 Chrome 进程、持久化 Profile 和固定出口，各站点继续独立决定域名归属、正文策略、crawler recipe、权限验证、健康与 fallback。

本轮直接实现两轨、共享 Identity、Registry 迁移、生命周期管理、桌面维护及生产业务验收。不建设浏览器集群、容器编排平台、通用插件框架或浏览器自动伪装系统。

关键决策：

1. 默认 runtime_kind=`managed_playwright`。External 必须人工注册；未知域名不自动分配常驻正式 Chrome。
2. External 使用官方 Chrome `153.0.8010.52` 和 Playwright `1.63.0` 的已验证组合。版本精确锁定，后续受控升级。
3. 新增单个 `v2-chrome-supervisor` sidecar，内部普通启动多个 Chrome 进程；不为每个 Identity 创建容器，也不给任何业务容器 Docker socket。
4. Supervisor 管进程、Profile writer lock、显示与重启；Site Access 管页面、任务、站点判定和 Playwright CDP 连接。Site Access API 重启不应主动关闭 External Chrome。
5. Identity 绑定一个物理 Profile 和一个 Egress。一个 Identity 同时只能有一个 Runtime；两轨不能同时写同一 Profile。
6. 页面隔离是每任务一个 Page，并清理其 popup、listener 和 CDP session；Cookie/localStorage/Service Worker 在同 Identity 内按 Chrome 原生行为共享。新建无痕 BrowserContext 会破坏这个目标，禁止用于常规任务隔离。
7. 先完成数据模型和基础设施，再迁移各站点；直接把实验容器当生产 owner 或复用实验脚本不算完成。

## 2. 实验证据及推论边界

| Barron's 配对实验 | 实际结果 |
| --- | --- |
| 普通启动官方 Chrome 153 | 人工两轮 challenge 后登录并读取正文 |
| Playwright launch_persistent_context 启动官方 Chrome 153 | challenge 后 Access is temporarily restricted |
| 普通 Chrome + CDP attach + Playwright | 人工两轮 challenge 后登录、正文可读；容器重启后验证文章 200，article 45 段、8,145 字符 |

这些结果支持优先生产化第三种模式，但没有定位到某一个启动参数。不能因此承诺所有网站不再出现 challenge，也不能把完整生产访问栈视为已经通过测试。特别是现有生产的导航门、popup 管理、recipe 与实验并不相同。

首批 External 迁移包括用户明确指定的 Reuters 之外现有具名站点；其中 Yahoo、Seeking Alpha、WSJ、MarketWatch 属于待验收的人工配置例外，不宣称已得到 Barron's 同等实验证据。Reuters 继续 Managed；generic 继续 Managed。

## 3. 当前实现与需要改动的位置

本节依据本轮本地实际代码勘察；没有把先前部署记录当成当前远端 Registry 清单。

| 路径 | 当前职责与约束 | 本轮处理 |
| --- | --- | --- |
| `src/doxagent/content_enrichment/extractor.py`、`managed.py`、`pipeline.py` | SiteAccessTransport 发 HTTP_PUBLIC；SiteAccessBrowserReader 发 BROWSER，HTML 在正文链路继续抽取；trace 含 site/combination/profile/egress | API 不改为浏览器句柄，仅增加 identity/runtime provenance |
| `src/doxagent/site_strategy/client.py` | SiteManagedBrowser 提供 get、yahoo_latest_news、reuters_search；Yahoo 使用 BROWSER_FETCH+recipe | 保留调用合同，两轨都返回相同 AccessResult/recipe_result |
| `src/doxagent/crawler_plane/runtime.py`、`factory.py` | HTTP 调 Site Access；浏览器由 SiteManagedBrowser 注入；跨站 redirect 有重新解析 | 保持 source/acquisition 层行为，验证两轨 recipe 等价 |
| `src/doxagent/message_bus_v2/news_adapters.py`、`adapters.py`、`scheduler.py` | 消息获取与延迟重试；Yahoo crawler/NCP/RSS 层级 | 不将 NCP/RSS 重构成 Identity fallback |
| `site_strategy/runtime.py` | PersistentBrowserPool 实际按 profile_id 池化；launch_persistent_context；12 小时 idle；容量 LRU；全局 page semaphore | 抽取两轨 adapter，池键改 identity_id，常驻不被普通 LRU 淘汰 |
| 同上 `_DocumentNavigationGate`、`_RawBrowserCDP` | 正文、recipe、登录页均装 Fetch Document 导航门及 popup 首请求门 | 保留安全语义，解耦进程启动；External 集成必须实测这层 |
| `site_strategy/schema.py` | Combination 重复保存 profile_id/egress_id；BrowserProfile 含 site_id、auth_state、environment、维护状态 | 引入 Identity、逐站 IdentityAuth；去除 site 对 Profile 的排他所有权 |
| `site_strategy/service.py` | validate_strategy 强制 profile.site_id==site；ProfileUseCoordinator 按 Profile 维护互斥；SiteBudget 按 runtime_key | 改按 Identity 维护互斥；Site+Identity 原子预算；逐站认证和健康 |
| `site_strategy/repository.py` | SQLite revisions/heads、profiles、egresses、site_runtime；directory_key 唯一 | 原库增量迁移，不另建数据库产品 |
| `site_strategy/resolver.py` | 域名决定 Site；generic runtime_key 按 host 派生；support_hosts 不夺取 publisher 归属 | 保留；共享 Identity 不合并域名归属 |
| `site_strategy/profile_storage.py` | 关浏览器后锁 Profile、整目录快照/恢复 | 将停机和锁交给对应 owner；修正 Chrome singleton socket/symlink 的快照处理 |
| `deploy/site-login-admin.py`、`site-login-ui.py` | 列表/验证按 Profile+site；固定 VNC 5900 | 改 Site+Identity 选择，保留单一桌面入口和本机 5900 |
| `deploy/Dockerfile.site-access`、`site-access-entrypoint.sh` | 当前同容器 Xvfb 1440×1000、x11vnc、UID10001 | Managed 保留；External 独立 Supervisor 与每 Identity display |

注意：BROWSER_FETCH 当前是访问模式/recipe 路由标签，不是支持任意远程 JavaScript 的公共接口。此次不开放任意脚本执行，也不把所有 HTTP 请求偷偷转为浏览器请求。

## 4. 数据合同与存储

### 4.1 BrowserIdentitySpec：唯一的浏览器配置来源

在 schema.py 新增严格模型，Registry 采用人工 apply + revision：

```yaml
identity_id: dowjones-main
revision: 1
enabled: true
runtime_kind: external_chrome
profile_id: dowjones-main
egress_id: us-standard-5
environment:
  locale: en-US
  timezone_id: America/Los_Angeles
  window_width: 1440
  window_height: 1000
browser_release: google-chrome-153.0.8010.52
lifecycle:
  residency: always_on       # always_on | on_demand
  idle_seconds: 43200
access:
  max_concurrency: 1
  min_interval_ms: 3000
credential_ref: null
```

`browser_release` 为服务器批准的 release 标识，不允许 Registry 任意填写可执行路径、shell 命令、CDP URL 或 Chrome flags。release 映射由部署 manifest 固定。Managed 仍使用既有锁定 CfT，不全局切换为官方 Chrome。

同一 profile_id 只能被一个启用 Identity 引用；directory_key 全局唯一。Egress ID 与 Profile 长期绑定；切换出口 ID 要创建新 Identity/Profile。节点 generation 变化需要 drain、核验出口、受控重启，不能让运行中的 Profile 无声漂移。locale/timezone/runtime_kind/release 更新也须 drain，不能每请求临时覆盖。

BrowserProfile 收敛为物理数据资源：profile_id、directory_key、创建时间、存储/快照信息。environment、credential_ref、运行状态以 Identity 为准，禁止同时维护两份权威配置。旧字段仅作为迁移读取/兼容投影。

### 4.2 Site 的 combination 引用 Identity

```yaml
site_id: barrons
access:
  combinations:
    - combination_id: barrons-1
      identity_id: dowjones-main
      priority: 10
      enabled: true
    - combination_id: barrons-2
      identity_id: dowjones-backup
      priority: 20
      enabled: true
```

保留 combination_id：它是站点级优先级、健康与 fallback 的稳定键。WSJ、MarketWatch 用自己的 combination_id 指向同一 main/backup Identity。旧 profile_id/egress_id 输入经兼容解析后归一化为 identity_id；二者与 Identity 不一致必须拒绝，不维护双写真相。

Site→Identity 关系仅由站点 active revision 的组合列表决定；不在 Identity 再维护一份可修改 site_ids。成员列表由 Registry 查询派生。

### 4.3 认证、健康、维护的归属

新增 `site_identity_auth`，主键 `(site_id, identity_id)`，字段：auth_state、verified_at、verification_url、reason_code、observed_session_revision。generic 必须使用实际 runtime_key，防止不同未知域名串状态。

Identity 仅保存 `session_revision` 和 operational state；没有“整个 Identity 已订阅”状态。Barron's VALID 不能使 WSJ VALID。共享 SSO 只意味着后者可能免输密码，仍需在其域名验证订阅正文。账号登出/重新登录/恢复快照等会增加 session_revision，其他成员验证记录标记待复核；不粗暴给所有成员写 REAUTH_REQUIRED。未知但已有共享 SSO 的成员允许一次受控验证，成功后才能执行要求登录的业务任务。

健康分三层：

- `(site runtime_key, combination_id, lane)`：challenge、429、站点封禁、内容错误，沿用现有 SiteRuntimeState。
- Identity：进程死亡、CDP 断连、Profile busy、维护状态。影响该 Identity 的全部成员，但不写成网站风控。
- Egress：确定的代理不可达/配置失效，影响引用该出口的资源。

一个站点 challenge 不默认冻结全部 Identity 成员；共享 SSO 确认失败可提示关联成员复核，但不推断所有订阅权限失效。fallback 仍由站点选下一个 combination，整体切到另一 Identity，不对原 Profile 换 IP。

### 4.4 SQLite 增量表

增加 identity revisions/heads（与 Site 相同 CAS revision 结构）、identity_runtime（operational state、session revision、generation、诊断）、site_identity_auth、schema migrations。保留 site_runtime、egresses、events、outcomes。进程 PID、临时 CDP 端口不是 Registry 的可复用权威记录；Supervisor 返回 live instance_id/generation。

配置提交单事务校验：Identity 存在启用、Profile 唯一、Egress 存在、release 合法、域名无冲突。旧站点 revisions 不重写，读取时使用确定的迁移映射。Site revision 与 Identity revision 分别审计，避免对一个环境更新制造大量 Site 修订。

## 5. 双轨统一接口与 Owner

新增 `browser_runtime.py`、`managed_runtime.py`、`external_runtime.py`；现有 runtime.py 保留访问执行与导航策略。

```python
class BrowserRuntimeAdapter(Protocol):
    async def acquire(self, identity, deadline) -> RuntimeHandle: ...
    async def new_page(self, handle, task_scope) -> PageLease: ...
    async def detach(self, handle) -> None: ...
    async def stop(self, identity_id, reason, deadline) -> StopResult: ...

# handle: identity_id, identity_revision, instance_id, runtime_kind,
# existing_context, control_generation; 不出 Site Access 进程边界
```

BrowserRuntimeManager 解析 Identity、复用 Handle、选择 adapter。相同 Identity 并发启动采用单锁/同一启动 future。请求总 deadline 覆盖排队、启动、attach、导航和抽取；容量等待不能无限循环。

Managed adapter 从现有 PersistentBrowserPool 提取 launch/context/lock/快照功能，保持当前 runtime 行为。每次请求仍只获取新 Page。External adapter 通过 Supervisor ensure 获取 live endpoint，再 connect_over_cdp 并使用 `browser.contexts[0]`；不调用 launch、new_context、storage_state 导入或复制 Cookie。

正文 reader、Yahoo/Reuters recipes、登录 verify 使用共同的 PageLease。统一结果保留 HTML、recipe_result、redirect_url、status、failure_category；增加 identity_id、runtime_kind、identity_revision、runtime_instance_id、runtime_generation。不能把当前 site runtime_key 改成 identity_id，否则站点健康与缓存会串。

请求去重/cache key 必须包含 Site revision、Identity revision/session revision、所选组合及请求语义。Identity 变更、恢复快照或重新登录不能复用旧认证失败/旧权限结果。

## 6. External Chrome Supervisor

### 6.1 最小实现及部署

新增 `chrome_supervisor.py` 作为单进程服务，使用 asyncio subprocess 启动普通官方 Chrome；system/container 负责运行 Supervisor。与 Site Access 分开容器、独立生命周期，共享仅用于控制的 Unix socket volume。

接口只提供 ensure、status、drain/stop、snapshot/restore、maintenance display selection；请求参数是经验证的 Identity 配置与 revision，不允许命令字符串、任意路径或任意 CDP 地址。Unix socket 按 UID/GID10001 限权，Supervisor 不暴露公网 TCP 管理口。

为让 CDP 保持真正 loopback，两容器共享专用网络 namespace（Compose `network_mode: service:v2-site-access`），Supervisor 自身 CDP 只监听 127.0.0.1。同一网络空间可有多个正式 Chrome，端口从受限固定范围逐个保留，使用非零端口；避免 `--remote-debugging-port=0` 与成功实验不一致。端口冲突只能选新空闲端口，不连接未知进程。宿主不 publish CDP，业务 worker 无权获得 endpoint。

独立 namespace sidecar 随 Site Access 容器重建必须一同受控重建，这是明确的 Compose 约束：普通 Site Access 进程重启/CDP 重连不关 Chrome；容器 recreate、镜像升级需要维护窗口与 Profile 续存验收。不能声称任何部署都完全不断 Chrome。

### 6.2 普通启动环境

启动参数以成功实验为基线：user-data-dir、proxy-server、proxy-bypass-list、window-size、no-first-run、no-default-browser-check、固定 loopback remote-debugging address/port、about:blank。UID10001、sandbox、现有 seccomp/AppArmor 保留。环境 LANG/LC_ALL、TZ 由 Identity 在进程启动时固定，必要 locale 在镜像生成。

禁止自动继承 Playwright default args；不注入 UA/Client Hints、stealth、随机化、disable-web-security 或 no-sandbox。代理通过现有 Clash CONNECT，保持 TLS 端到端。

生产的 QUIC/WebRTC 出口约束优先由网络层限制 Chrome 直接出站、私网/metadata/本机目标；不得仅依赖 `--proxy-server` 认为所有协议都受代理保护。复用并核验 Clash sub-rule，补充 Supervisor 网络出站规则：仅允许必要 DNS、已注册代理 listener 和本地控制；CDP/VNC 不允许网页导航访问。若必须添加浏览器 flags，单独记录与成功基线的差异，并重新做实际登录验收。

### 6.3 Profile 与进程生命周期

- Supervisor 持有 External Profile 的 OS writer lock 到真实 Chrome 退出；Site Access 不重复取这个锁。Managed 仍由 Managed owner 持锁。
- always_on：启动时恢复启用主 Identity，不因请求结束/idle/LRU 退出；备用按需启动，保持长 idle。
- CDP 断连：先重连同一 instance；只有确认进程死或不可恢复才重启。旧 PageLease 带 generation，不能在重连后误关新任务页面。
- API owner 单写控制：保持 Site Access owner lock；Supervisor 记录 controller lease/generation，拒绝双 owner 同时控制。同一 Chrome 可以短暂失联但不得被第二个业务 owner 抢用。
- 关闭：暂停新任务→drain→通过控制连接 Browser.close 或 SIGTERM→等待进程及子进程退出→flush/写 clean manifest→释放锁。正常流程不用 SIGKILL；超时标记 UNHEALTHY/UNCLEAN，不释放 writer lock后再启动第二进程。运维强制回收作为显式异常路径。
- Playwright detach 仅断开连接；禁止用 `context.close()` 当 External 归还页面/连接的方式。Playwright `browser.close()` 的 attach 语义必须用真实进程测试确认，显式停止浏览器由 Supervisor 执行，不依赖库方法名推断。
- Supervisor 意外死亡但 Chrome 残留时，重启后先识别 PID start time、Profile 路径、instance nonce 和锁持有者；无法证明归属则报 PROFILE_BUSY，禁止删除 Chrome SingletonLock。
- 持久化目录还要覆盖影响 Cookie 解密的 HOME/密钥存储配置。不能只备份 Cookies SQLite。首版沿用已验证的 Linux 环境，不额外加入 password-store=basic；使用重建容器后的登录续存作为验收。

## 7. Page 隔离、导航安全与业务能力

一个 Identity 的默认 context 内，每个正文/crawler任务新建 Page；task_scope 显式包含 Site/runtime_key/operation_id/deadline。listener、CDP session、popup 归属于 PageLease，finally 清理。不能使用 `context.pages[0]` 承载生产业务，它可能是维护页或其他任务页。

统一执行器保留：普通 goto/DOM/expand/wait_selector、Yahoo network observation+页面内 fetch recipe、Reuters search、HTML 返回和失败分类。External 不单独复制一套正文抽取器/crawler。

现有 `_DocumentNavigationGate` 会 Fetch 拦截 Document，`_RawBrowserCDP` 有 browser-level popup 管理。需重构成每 Identity 单个 broker、每 Page 独立 Site policy，不得把第一个站点的 policy 当 context 全局策略，也不得多个 attach 控制器竞争 `Target.setAutoAttach`。

同 Identity 不意味着跨 publisher 自动放行。Barron's 任务跳到 WSJ，仍产生 REDIRECT_REQUIRED、重新解析 WSJ/策略/预算/权限；底层可能复用同一个进程。SSO support_hosts 按当前 Site 明确允许。

默认保留现有导航安全门并在 External 上做真实登录和业务测试。如果该层重新触发风险，提供显式 `navigation_guard=observe` 的受控配置：导航后 observation 判定 publisher、跨站结果丢弃并重派；同时私网/metadata/控制端口必须已有网络级前置拒绝。observe 模式不能声称阻止了第一条跨公网请求；它只能阻止错误归属内容进入业务结果。未验证网络隔离之前不得启用 observe。首批选择需在灰度验收记录中明确，禁止默默删门。

HTTP_PUBLIC 保持独立 HTTP session 与既有出口，不共享浏览器 Cookie，不为 HTTP-only 请求启动 Chrome。只在命中明确 browser recipe 时执行 BROWSER_FETCH；不开放自动带登录态任意 URL fetch。

## 8. 并发、节奏、资源与 fallback

将 SiteBudgetManager 演进为小型联合 admission：一个 condition/队列同时检查 Site 和 Identity 两个计数器以及两个 next_start。不得先持有 Identity permit 再等待 Site permit，或反之，造成无效占位/饥饿。BODY 优先 CRAWLER，PROBE 最低；等待超过30秒的 crawler提升到同等 FIFO，防止持续正文造成晚抓。等待仍受原请求/队列 deadline 限制。

External 高风控 Identity 默认并发1、间隔3秒；Site 继续自己的限制。间隔计任务导航/recipe启动，不节流页面每个 CSS、图片或请求。HTTP_PUBLIC 只消费 Site/HTTP预算，不抢浏览器 page slot，但仍服从 Identity 维护所需的站点操作限制。

全局页上限首版4，Chrome运行进程上限首版6：三组 External 主身份 + Reuters/通用 Managed + 一个备用余量。预热最多三个 External 主身份，启动串行间隔，复用当前资源安全信号；不要给每个常驻 Chrome 一个独立高额内存保留。具体 mem_limit/cgroup 先依据远端资源实际复核后设置，不能把限制相加当实际内存。

always_on 主身份不参与常规容量 LRU；先回收空闲 on_demand，再延迟冷启动。PRESSURE 停新冷启动而继续可用现存 Identity；CRITICAL 只按现有资源策略逐个 drain 非维护、非活跃浏览器。维护 Identity 不自动回收；页面任务失败可重试，真实账号登录流程不能被后台回收。

Site risk fallback 不重启共享主 Chrome、不关闭其他成员 Page。切换站点当前 combination 后，其他站点仍可使用该 Identity。进程/Egress 确定故障才影响全部成员。不得从 External 静默改回 Managed 来“补救”同一个 Profile。

## 9. 登录维护与桌面适配

保留 xRDP 的 Site Login Maintenance GUI、受限 root bridge、全局单人工会话。列表显示 Site、Identity、Runtime、Egress/地区、该 Site 的 auth_state和共享成员，不按 Identity 广播 VALID。

维护会话改为 `(identity_id, selected_site_id, token, generation)`。打开前 drain 该 Identity 全部站点任务；正常正文/crawler不能并行占用同 Identity。其他 Identity 继续工作。

External 每 Identity 使用自己的 Xvfb display，维持1440×1000；人工维护选择对应 display。宿主仍只提供 loopback 5900，改由受控 VNC relay按当前维护会话指向 Managed 显示或 External 对应 display。内部端点不由 GUI 任意输入；bridge只接受已注册 identity/site，管理 token 与 login_token仍留在root状态文件。

点击 Verify 默认只验证选中站点，关闭维护页、释放 Identity维护锁，但保持 Chrome常驻。可提供明确的“验证共享站点”操作逐一访问各站验证URL；结果逐站展示，不自动反复触发兄弟站 challenge。

API重启后的遗留维护：Supervisor保留浏览器，Registry记录会话；GUI recover后重新绑定同 identity/instance的维护 target，若无法确定则提示安全结束维护。不能因API启动自动关闭用户页面。完成迁移的旧 profile API仅兼容一对一映射；共享场景缺失site_id应返回可读的明确错误，不猜第一个站点。

## 10. 首批 Identity 与迁移

| Site | 主 Identity | Runtime | 出口 | 备用 |
| --- | --- | --- | --- | --- |
| Barron's / WSJ / MarketWatch | dowjones-main | External、always_on | us-standard-5 | dowjones-backup，JP，on_demand |
| Seeking Alpha | seeking-alpha-main | External、always_on | 保留当前配置主出口 | 独立备用，保留当前配置 |
| Yahoo Finance | yahoo-main | External、always_on | 保留当前配置主出口 | 独立备用，保留当前配置 |
| Reuters | 原主/备用一对一 Identity | Managed | 保留当前Registry | 保留已有Profile及认证状态 |
| generic 未注册域名 | 按现有host+egress派生Managed Identity | Managed、on_demand | 保留通用策略 | 保留通用fallback |

当前 seeds 有六个具名站点加generic；上表不是允许用seed覆盖远端人工Registry。部署前导出真实配置，再生成 dry-run 差异。Yahoo/Seeking Alpha选择External是本轮用户指定迁移，验收失败只阻塞其激活，不拖垮已验证站点。

低频多站共享 Identity 能力本轮提供配置支持，但不默认把不相关网站塞进generic共享Profile。只有管理员明确接受共同账号/出口/环境/存储与共同维护窗口，才增加如 `public-lowfreq-us` Identity。共享是信任边界，不能作为隐式资源回收策略。

迁移顺序：

1. 备份 Registry；列出现有 profile目录、锁、active maintenance、Identity目标映射及配置摘要。
2. 对每个旧Profile创建一对一 Managed Identity；原目录与Profile ID不变。把旧auth状态复制到对应site_identity_auth，旧Combo ID不变。以migration version保证幂等。
3. 部署双轨代码，所有原请求先走兼容Managed，回归通过后激活External分组。
4. dowjones-main优先采用已成功CDP实验Profile的完整冷备副本。必须正常关闭该实验Chrome后快照、复制、校验，然后迁入唯一生产目录；禁止热拷SQLite/拼接三站Cookies/生产实验双writer。源副本保留以便回滚，部署前列出需暂停的实验会话。
5. 新生产Identity首次验证Barron's；WSJ/MarketWatch各自UNKNOWN并分别验证。未成功不得把三站共享登录当验收通过。
6. 原 barrons/wsj/marketwatch Profile及其历史保留，停用旧组合而不删除；Dow Jones备用新Profile需独立登录。
7. Reuters原Profile不转换浏览器版本/目录；Yahoo和Seeking Alpha为External创建独立Profile，已有重要登录态如需迁移则按同一冷备流程，禁止默认混用CfT与正式Chrome同目录。
8. 每站通过后修改active revision。保存前一revision和migration manifest；部分完成可继续运行。

回滚：drain目标Identity，停止External，恢复此前Site revision与一对一Managed映射，使用旧Profile目录；不得让旧Chrome读取已升级后的Profile。Registry全库恢复仅用于停服灾备，正常单站回滚不能抹掉其他站新登录状态。快照只剔除已确认可重建的 `SingletonLock`、`SingletonSocket`、`SingletonCookie` 运行时链接，完整保留真实 `Default/Cookies`、Local State、IndexedDB；恢复仍拒绝未知特殊文件。

## 11. API、诊断与配置接口

新增admin接口：Identity list/get/apply/validate/history、runtime status、显式 restart/drain、按Site+Identity login/verify/probe/snapshot/restore。延用现有admin权限及CAS revision。worker不持有CDP endpoint或Supervisor权限。

AccessResult/AccessAttempt/事件/正文trace增加identity/runtime元数据；Profile ID兼容保留，site_id与combination_id语义不变。日志不包含Cookie值、账号、OAuth query或CDP WebSocket token。

readyz区分控制平面ready与部分Identity不可用；单External启动失败不能使整个Site Access服务unready。Identity不可用返回已有SERVICE_UNAVAILABLE/BUDGET_DEFERRED及明确reason、retry_not_before；不能把冷启动排队写成站点风控，也不能让Message Bus把DEFERRED当抓取成功。

## 12. 按文件和依赖执行的工作包

| 顺序 | 修改/新增文件 | 可交付结果 |
| --- | --- | --- |
| A | schema.py、repository.py、resolver.py、seeds.py；新增identity_migration.py | Identity修订、逐站认证、兼容读、dry-run/幂等迁移与约束 |
| B | 新增browser_runtime.py、managed_runtime.py；改runtime.py、factory.py、settings.py | Managed抽取后原有功能/生命周期回归不变；共享Identity单进程 |
| C | 新增chrome_supervisor.py、supervisor_client.py、external_runtime.py；deploy官方Chrome镜像/入口/Compose | 普通启动、CDP attach、独立owner、writer锁、display、重连、优雅退出 |
| D | service.py、budget.py、health.py、runtime.py | Identity级维护、联合预算、逐站auth、分层故障、Page/popup导航策略 |
| E | content_enrichment/managed.py及trace消费者、site_strategy/client.py、crawler_plane相关适配与测试 | 两轨同一HTML/recipe合同；Yahoo/Reuters与通用crawler回归；采集fallback不变 |
| F | api.py、cli.py、profile_storage.py；deploy/site-login-admin.py、site-login-ui.py、安装脚本/VNC relay | 管理入口、逐站验证、共享Identity维护、快照/恢复、桌面一键操作 |
| G | seeds/migration manifest、部署/验收文档、changelog | 真实Registry差异、灰度迁移、重启续存、短时和24小时运行验收 |

每个工作包必须形成可测试提交，避免在全部重构完成前没有可运行版本。旧行为兼容不是长期双模型：生产迁移完成后旧Profile写接口进入只读兼容，新增配置统一写Identity。

## 13. 验收要求与停止条件

### 自动测试

1. 三站指向同一Identity时只启动一个Runtime；并发首次请求同样只启动一次；不同Identity不同Profile/锁。
2. 两轨参数化验证：普通页面、expand、wait_selector、redirect、超时/cancel、popup清理、Yahoo recipe、Reuters recipe、错误分类和字节上限。
3. Barron's VALID不使WSJ/MarketWatch VALID；权限缺失不触发节点轮换；site challenge不污染兄弟站健康；Identity进程故障会影响所有成员。
4. 混合BODY/CRAWLER竞争满足两级并发和间隔；取消后无permit/页面泄漏；crawler不长期饿死；总deadline覆盖冷启动和排队。
5. login-open会等待整个Identity drain；业务不能绕过锁；verify/close不终止常驻Chrome；恢复维护不会误关未知页面。
6. Site Access进程重启后reattach仍是原Chrome PID；Supervisor/容器重启后Profile续存；断连只重连；未知残留writer拒绝启动。
7. snapshot/restore仅在Chrome退出后操作；升级后回滚使用旧版本快照；迁移重复执行不覆盖人工配置或认证结果。
8. 跨站publisher重派仍用目标Site策略，尤其Yahoo发现Barron's；CDP/metadata/private地址无法被网页访问；共享SSO support域按规则允许。
9. 测试HTTP_PUBLIC不启动Chrome、不读取Cookie；Managed低风控路径继续可用。

### 生产验收

先在完整生产runtime中验证Barron's：人工challenge/login→同一个Identity自动正文补全→容器重建后的再次读取。保留原生网络与安全门的差异记录；homepage200或cookie存在不作为订阅验收。

随后逐站完成WSJ、MarketWatch、Seeking Alpha、Yahoo；前三个Dow Jones站共享同一进程，但分别验证登录/订阅权限。Reuters确认原Managed登录态、搜索分页、新消息抓取和正文路径无回退。

新消息抓取验收应执行已有Yahoo/Reuters真实recipe及一个可控crawler contract用例；未配置专用新消息crawler的订阅站不能声称“新消息抓取已上线”，本轮验收其Browser能力和正文适配，不临时编造站点crawler。

30分钟重复验证+24小时自然运行，记录：各站请求/正文成功率、challenge/auth/内容失败分布、crawler新鲜度与DEFERRED、Identity启动/重连次数、Chrome RSS/CPU、维护中断数。与迁移前同口径窗口比较；按实际内容与发布/入库时间判断能力，不以状态码或固定条数代替覆盖率。

交付必须包含：实现提交、迁移dry-run及实际差异、自动测试结果、各站人工/自动验收状态、运行记录、回滚命令、changelog。人工未完成的站点明确标注待维护；已验收站点独立投产。

## 14. 开发边界

本轮不重构Message Source配置、ticker binding、消息调度、NCP/RSS fallback；不新增账号密码自动填写、MFA处理、Cookie跨站拼接或stealth。不把实验中成功的Profile直接标记为所有站点VALID。不要求先找到Playwright的唯一致因参数才实施已验证的External路径。

低成本Managed与高连续性External均是一等运行模式；共享Identity只压缩进程和统一身份，不压缩站点策略、权限与质量治理。
