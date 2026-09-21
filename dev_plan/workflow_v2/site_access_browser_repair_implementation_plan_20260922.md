# Site Access 浏览器与消息源访问修复实施方案

日期：2026-09-22。状态：**已实施并完成首轮生产技术验收；人工登录/订阅验证与 24 小时业务观察仍按本文边界分开验收**。详见 `site_access_browser_repair_remote_acceptance_20260922.md`。

依据：`site_strategy_governance.md`、`site_strategy_governance_implementation_plan_20260921.md`、既有 xRDP / Site Access 对照诊断、本轮代码复核及远端只读诊断。远端复核基线为提交 `0a4d39a`；实施前重新核对版本与人工维护会话。

## 1. 决策与边界

目标不是伪装某个浏览器指纹，而是修复不必要的环境差异、错误的访问/健康判断、Profile 生命周期与维护互斥，使人工维护和后台访问真正共享一个稳定、独立、固定出口的浏览器身份。

本轮方案采用以下明确边界：

1. 保留现有 Site Access 单 Owner、Playwright persistent context、CfT、Xvfb / VNC、xRDP 桌面维护工具和 Registry。**不开展上一轮建议第 7、8 项所涉及的独立正式 Chrome + CDP 架构替换与 stealth 方向**。不做自动验证码、MFA、指纹随机化或复制用户桌面 Chrome Cookie。
2. CfT 保持 headful；升级为与 Playwright 官方配套的 153 大版本。不是把桌面 Chrome 的任意可执行文件塞给旧版 Playwright。
3. 强风控站点以正常浏览器访问为主；消除全量资源拦截，但不能同时删除 SSRF、域名归属和固定出口约束。仅保留确有必要的文档导航安全门，具体见第 5 节。
4. Profile 不原地换出口。需要直连或新节点时创建新 Profile + Egress 组合；旧身份原样保留，可回退。节点池不是每次请求随机换 IP。
5. 本文的开发、迁移与首轮远端技术验收已于 2026-09-22 执行；订阅身份维护由用户通过 xRDP 工具单独完成，未在自动验收中伪造登录成功。
6. 不重构消息源配置，不修改 Yahoo 的 crawler → NCP → RSS 顺序，不接管 NCP/RSS；第三方文章继续按最终 publisher 归属治理。未注册站点继续走通用策略。

必须接受的客观边界：降低不自然特征不等于保证绕过网站风控。浏览器版本、时区、sandbox 等修复有一致性/安全价值，但目前不能把其中任一项断言为封禁主因。保留 Playwright 启动架构时，`navigator.webdriver` 仍可能为 true；本轮不通过脚本或启动参数伪造为 false。

## 2. 追加排查结论及证据等级

### 2.1 已观察到的差异

| 项目 | xRDP Chrome | 治理浏览器当前值 | 结论与处理 |
| --- | --- | --- | --- |
| 浏览器 | 正式 Chrome 153.0.8010.36 | CfT 151.0.7922.34，Playwright 1.62.0 / revision 1234 | 升级受控版本对，不认定版本是唯一原因 |
| 用户 / 系统 | doxagent-desktop，Ubuntu 26.04 | root，Debian 12 容器 | 改 non-root 并验证真正的 renderer sandbox；不要求复制整个桌面 OS |
| 显示环境 | xRDP :10，1920×1080 | Xvfb :99，1440×1000；默认 viewport 1280×720 | 保留 VNC 架构，改 native viewport + 固定真实窗口 |
| 语言 | 浏览器 en-US / en | OS C.UTF-8；浏览器已是 en-US / en | 是 OS/浏览器配置不统一，不是“浏览器当前说中文” |
| 时区 | Asia/Shanghai | UTC | Profile 级固定；不因请求/GeoIP 临时改变 |
| 自动化特征 | 已观测 webdriver=false | webdriver=true，Playwright 默认参数较多 | 精简非必要功能关闭项，不做 stealth |
| Profile | 长期人工使用，已有登录/挑战历史 | 独立目录已持久化，但若干是新身份 | 保存并正常维护治理身份，不借用桌面 Profile |
| GPU | 对照采样 WebGL 返回 null | 软件渲染特征 | 没有证据称桌面一定有硬件 GPU；不伪造 GPU |
| 出口 | 对照时服务器直连 43.163.67.97 | US 38.181.82.188 等固定代理 | 同一 IP 也可能因 Profile 状态不同得到不同结果 |

既有配对观察：

- Barron's：正式 Chrome 新身份走 US 节点返回 412；CfT 新身份同节点亦返回 412。CfT **headful** 新身份改服务器直连可打开 Sign In 200。说明节点/出口影响有直接证据，也说明 CfT 并非必然不能访问。
- xRDP 成熟身份在直连可打开 Barron's 登录/客户页面；**登录页 200 不是订阅正文权限证明**。
- Reuters：成熟桌面身份直连 search 200；正式 Chrome 新身份与 CfT 新身份在直连均出现 401。Profile 历史可能重要，但 Cookie 数量不能证明某一个 Cookie 是原因，也不能据此复制或清空用户成熟身份。
- 早先 headless 失败与后续 headful 成功不是同时间、同身份的严格 A/B；只能支持继续使用已验证可工作的 headful 路径，不能宣称已经证明 headless 是唯一原因。

### 2.2 本轮确认的具体实现缺陷

1. **300 秒不是正在生效的自动关闭机制。** `PersistentBrowserPool.close_idle()` 存在，但未找到调用方。当前容量上限 4 下的 LRU 淘汰才是实际关闭路径之一。延长参数必须同时接通维护调度；不能拿改一个默认值冒充常驻修复。
2. `runtime.py` 正文/验证路径执行 `page.route("**/*", ...)`，目的是 URL/归属安全检查，不是单纯图片屏蔽。Playwright 文档说明 routing 会关闭 HTTP cache。原始 `login-open` 路径没有同样的全量 route，因此“人工登录成功、随后 verify 失败”可能对应两种网络行为，必须统一验收。[官方 routing 说明](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route)
3. 当前治理 runtime 未发现显式禁用 Service Worker，也未发现显式 `Network.setCacheDisabled` 或手工 UA/Client Hints override；不能把不存在的配置列成已定位根因。HTTP_PUBLIC 的 curl impersonation/独立请求头是另一条传输链路，应与浏览器健康分开。
4. 维护排他有竞态：普通任务在等 site budget 之前读取维护状态，等到额度后没有原子重新领取；`verify` 更新 auth_state 后，维护页可能仍打开；桥接锁无法约束所有直接 API 调用。仅靠 `AuthState.MAINTENANCE` 不够。
5. Pool 获取 entry 与 active_pages 增加不在同一原子区间，可能被清理/淘汰抢先关闭；lazy start 也需串行化。已有 `context.close()`，但 shutdown 未完整 drain `_inflight`，关闭异常被忽略后释放锁不够可靠。
6. 412 未被统一判为失败；限制页关键词覆盖不全。Reuters 等策略抛出通用 RuntimeError 后，HTTP 风控状态可能被当 transport error 重试。必须先修诊断，否则会把错误路径测成“恢复”。
7. half-open `_probe_combination()` 当前使用 HTTP_PUBLIC，不能代表同一持久浏览器身份可用；也不能用这种探测成功清除浏览器风控状态。
8. Dow Jones 登录支持域规则缺口：exact `accounts.dowjones.com` 不覆盖 `sso.accounts.dowjones.com`；实际 Barron's 客户页也应纳入该站登录支持域。必须按观测新增精确规则，而非放开所有 publisher 归属。
9. 桌面工具只列 `auth.requirement=required` 且有 login_url 的站点；Reuters 当前 optional 且无 login_url，无法正常人工养成治理身份。需要小幅扩展现有维护入口，不做新后台。

### 2.3 远端安全、网络与升级条件

- Site Access：root、`no-new-privileges=true`、init=true、shm=1 GiB、未单独配置 stop timeout；资源覆盖为 6 GiB 内存 / 7 GiB memory+swap 上限。浏览器池上限 4，不应把所有站点双 Profile 全部启动常驻。
- 宿主允许 unprivileged userns，但启用了 AppArmor userns 限制。用现有镜像、non-root、no-new-privileges、network=none 的一次性容器运行 user namespace 测试返回 EPERM。**只改 USER 和 chromium_sandbox=True 不足以完成部署**；要处理每服务 seccomp/AppArmor 权限，再验证 Chrome，而不是降回 no-sandbox。
- 数据卷目前 root 所有；管理/worker token 挂载文件当前 uid/gid 1000、0600。选择新服务 UID 后必须迁移受限目录和 token 可读权限，否则启动即失败。不得为了省事把 token 改为全员可读。
- egress 当前观测：US 38.181.82.188、JP 212.107.30.197、DE 205.198.126.113 为 READY；NL 不可用。这里 READY 仅表示出口探测，不证明目标网站可访问。
- Clash/Mihomo 当前固定 listener 指向 ss 节点；未发现 TLS MITM 配置。Barron's 直连与经代理 CONNECT 的证书 leaf SHA-256 相同，Amazon RSA 2048 M01 签发、验证返回 0。支持“该链路未观察到 TLS 替换”，不是全部站点的无限保证。SSO 代理证书探测出现 EOF，需纳入后续实际浏览器复验，不能用 Barron's 的结果替代。
- 固定 listener 的 `proxy` 字段会直接指定出站；若以后加安全 `rule` 但保留 `proxy`，不能假设规则已执行。规则化 listener 必须验证命中与终端出站。[Mihomo inbound 文档](https://wiki.metacubex.one/en/config/inbound/)
- 官方版本清单确认 **Playwright 1.63.0 → CfT 153.0.8010.12 / revision 1243**。采用此版本对，和桌面 153 同大版本；不强行指定桌面 .36 补丁版本。现有 uv.lock 已锁 1.62.0，因此问题不是完全没锁版本，而是需要显式 pin、升级程序及镜像核验。[官方版本清单](https://raw.githubusercontent.com/microsoft/playwright/v1.63.0/packages/playwright-core/browsers.json)

## 3. 目标数据模型：最小增量，不另建治理系统

在现有 SQLite Registry / schema 上做向后兼容迁移，保留既有 revision / audit：

| 对象 | 增量 | 使用规则 |
| --- | --- | --- |
| BrowserProfile | `environment: {locale, timezone_id, window_width, window_height, revision}` | 创建后稳定；环境变更显式修订并在浏览器关闭后生效 |
| Profile 运行维护状态 | 独立 operational state + maintenance session ownership | 不再用 auth_state 同时表示“登录有效”和“被人占用” |
| AuthPolicy | `maintenance_url: str\|None`、`verification_kind: public_access\|subscription_article` | 已有 login_url / verification_url 继续使用；默认兼容旧订阅验证语义 |
| 组合健康 | browser 与 HTTP_PUBLIC 的最近结果分开；`manual_attention_required` | 复用健康存储，增加少量字段，不新建通用健康平台 |
| Profile 版本 manifest | browser/Playwright/build/env revision、最近 clean shutdown、snapshot id | 随 Profile 保存在受限持久目录，不写入用户密码或 Cookie 值 |

维护状态只需要 AVAILABLE → DRAINING_FOR_MAINTENANCE → MAINTENANCE → AVAILABLE，以及服务退出时 DRAINING_FOR_SHUTDOWN。状态改变与 Profile 使用权领取在同一锁下完成。服务端维护会话 token 继续只暴露给 root 桥接，不新增桌面凭据。

旧 `AuthState.MAINTENANCE` 迁移为 operational state；没有可靠历史认证证据的旧记录保持 UNKNOWN，不推断 VALID。进程重启后根据 lease / 浏览器存活情况恢复或清理，不把“维护结束”当“登录成功”。

## 4. 浏览器环境与常驻生命周期

### 4.1 精确启动合同

`runtime.py` 继续调用 `launch_persistent_context`，显式传入：

- `headless=False`；`chromium_sandbox=True`。
- `no_viewport=True`；窗口初始 1440×1000，与 Xvfb 实际屏幕一致；不再传默认 1280×720 viewport。浏览器内容区小于外窗口是正常现象，不要求两者像素完全相等。
- `locale=profile.environment.locale`，初始 en-US；镜像安装 locale/tzdata 和正常字体，OS LANG/LC_ALL=en_US.UTF-8。不再单独拼 Accept-Language 请求头。
- `timezone_id=profile.environment.timezone_id`；创建 Profile 时按已确认节点地区选择并固定。JP Asia/Tokyo、DE Europe/Berlin、NL Europe/Amsterdam；US 依实际节点城市选择美国时区，不能只凭“US”猜东西海岸；服务器直连身份默认 Asia/Singapore。
- Proxy 来自绑定 egress，Profile 数据目录不变。语言、时区、出口改变不得每请求随机化；成熟旧身份环境如需调整，先快照、一次性受控迁移，不暗中反复切换。

UA / Client Hints 使用实际 CfT 自然值。不写 init script 修改 webdriver、plugins、GPU、语言、权限或 canvas；不加入 stealth 库。软件渲染可以保留，不伪造硬件。

默认参数清理采用**精确 allowlist 式删除**，以 1.63.0 实际默认参数快照为依据：优先去除无必要的 `--disable-dev-shm-usage`（已有 1 GiB shm）、后台网络/组件扩展/前后缓存的测试性关闭项。合并在 `--disable-features` 中的开关逐项审查，不能整串盲删。保留驱动所需通信、进程管理和固定出口相关安全参数；本轮不通过移除 enable-automation 追求 webdriver=false。

现有禁 QUIC / 限制非代理 WebRTC 先保留：它们与避免代理外泄有关，不当作无条件可删的测试参数。只有确认所有协议均受同一出口约束后才缩减；不为了所谓原生行为开放直连旁路。

### 4.2 常驻与锁

1. `browser_idle_seconds` 改为 43200（12 小时），维护循环每 60 秒调用 idle cleanup。清理仅作用于真正 idle、无 lease、非人工维护的 context。
2. 保持 browser_max_processes=4、max_pages=4 初始值；按需启动，不预热全部 28 个身份。最近使用的高频身份常驻，容量不足只 graceful 淘汰 idle LRU；没有可淘汰项时排队/返回可重试容量结果，不能杀活跃页。维护 identity pinned。
3. 修 pool 的 entry 获取、active lease 计数、closing 标志原子性。池初始化单独 async lock；不要持有全池锁等待长达数十秒的浏览器退出，先标 closing，再异步关闭并通知等待者。
4. 保留全局 OwnerFileLock；增加 Profile 独占 OS flock，持有时间覆盖整个浏览器上下文/进程生命周期，备份/迁移也必须拿相同锁。Chrome 自带 SingletonLock 是第二道保护，不能删除正在使用的锁以“修复启动”。
5. 没有空闲槽时维护应等待或只淘汰 idle context；不能阻断不相关站点整个系统。业务 site concurrency 与 browser pool capacity 分开。

### 4.3 维护、verify 与 shutdown

- login-open：服务端保证全局只维护一个 Profile；原子置 DRAINING，拒绝该 Profile 新任务，等待已领取业务 lease 完成，再打开维护页。超时恢复原可用状态并明确说明，不强杀业务 Chrome。
- 每个普通任务在**真正领取 Profile**时重新检查 operational state，不能只在等待 site budget 前检查。
- verify：校验维护 session token / Profile 所有权；在同一维护 context 内串行验证。验证更新认证/权限结论，但始终保持 MAINTENANCE，直到 login-close 完成。后台 probe 同样不得侵入。
- login-close：关闭维护页、确认流程结束、释放 session 与 Profile 维护占用；保留 persistent context 常驻。关闭 Viewer 不等于 close。GUI 仍可恢复/取消。
- SIGTERM：停止接纳新任务，readiness 置不可用；最多 45 秒 drain 普通 inflight，协作取消超时任务并回收页/预算；最多 30 秒关闭 contexts，再停止 Playwright / Xvfb。Compose `stop_grace_period: 120s` 留出余量。
- 计划发布前发现人工维护正在进行，部署脚本停止并说明，不能自动结束用户登录。服务异常重启则记录 interrupted maintenance，供 GUI 恢复/清理。
- 正常生命周期不得用 SIGKILL。极端进程挂死/宿主 OOM 是异常处置：标记 unclean shutdown，保留诊断；未确认旧 Chrome 退出前不释放使用权并启动第二 writer。不能声称系统永远不会发生强杀。

## 5. 原生网络行为与必要安全边界

### 5.1 要删除什么、不能删除什么

最终取消生产浏览器正文/verify 的 `page.route("**/*")`，不做图片、字体、脚本、analytics 的通用资源屏蔽。不禁 Service Worker，不设 cacheDisabled，不用 curl/requests 重放浏览器请求。网络采集用 request/response 事件、performance / CDP Network observation；不保存 Cookie、Authorization、OAuth 查询串、完整响应正文或 HAR。

但是当前全量 route 承担 SSRF 和跨 publisher 导航限制，**不能直接删掉这一行就上线**。纯 response observation 在请求发出后才发现非法目标，不是等价的请求前安全保护。

### 5.2 具体替代：既有 Clash 做网络边界，最小文档门做归属边界

实施分两个可独立验收的小改动，不另建代理平台：

1. 现有固定 listener 改为显式安全 sub-rule：拒绝非公网目标（IPv4/IPv6、loopback、link-local、metadata、RFC1918、ULA 等），最终 MATCH 固定原节点或 DIRECT。移除会跳过规则的 listener `proxy` 字段。生成器必须保证引用的 sub-rule 存在、终端节点唯一，缺配置 fail closed，禁止隐式走全局默认代理组。
2. Chrome 所有 HTTP(S) 包括 localhost 都强制走该 listener（显式消除隐含 loopback proxy bypass）；容器外连只允许所需 proxy/DNS 通道与服务返回流量，阻止浏览器任意直出。既有服务内部通信先盘点再收紧，不误伤 Site Access API。DNS fake-IP、实际解析地址和 IPv6 都要测试；仅写一个域名黑名单不算完成。
3. 顶层初始 URL 仍调用现有 public_url 和 resolver；使用局部 CDP Fetch Document 安全门，仅在 request stage 对 main-frame / popup 文档导航做 publisher / 精确 support_hosts 检查，放行后不修改请求头、请求体或 response。iframe 文档不误当 publisher，仍受公网网络边界限制；SSO popup 在发出首个请求前完成绑定检查。
4. 该 CDPSession 只是当前 Playwright context 的小型安全附件，**不是独立 Chrome + CDP 重构**。资源与 Service Worker 不被全量拦截。support_hosts 允许该身份登录依赖，不使第三方 publisher 文章归属改变。
5. fetch gate 必须异常 fail closed、无悬挂 paused request；弹窗/重定向、新 target、Service Worker 导航路径逐一测。无法在首个请求前覆盖的路径不能用“事后关闭页面”冒充已保护。

这里保留必要的文档级 interception，是对安全合同的最小让步，不能宣称“完全零拦截”。在实验 context 中先验证它不会重新禁用所有资源缓存；若它仍使文档缓存受限，记录确切范围，不伪报完全原生。资源二次加载缓存、Service Worker、SSO 必须实测。

上线门槛：SSRF、重绑定/重定向、固定出口、popup 归属与缓存测试同时通过后，才移除现行全量 route。未通过时不能削弱安全边界，也不能把本项报完成；问题限定在这个小型安全附件/Clash 配置修复，不扩展为 MITM 网关。

### 5.3 HTTP_PUBLIC 与浏览器分界

强风控六站的 crawl / body browser-first，不在同一任务前面先用未登录 HTTP_PUBLIC 刺激站点再进入浏览器；必要公开 HTTP fallback 保留为独立策略/独立结果，不能读取或复制浏览器 Cookie。其他站点继续现有通用策略，依实际结果调整。

浏览器 JS fetch 如策略确有需要，继续在对应站点真实 context 内执行；不把浏览器请求改造成宿主 HTTP 请求。HTTP 健康、browser 健康、账号权限三个结论分开记录。

## 6. non-root + 真 sandbox 的部署做法

只修改 Site Access 服务，不全局更改服务器安全设置：

1. 镜像建立专用用户 UID/GID 10001（安装时先检查冲突），受限 HOME、可写 runtime/tmp、Profile 卷；entrypoint 的 Xvfb、x11vnc、Python、Chrome 全部由该用户运行。
2. 一次性迁移程序仅处理已验证的 Site Access 卷绝对路径，关闭 Owner / 取得锁后变更所需目录 owner。Profile 与 credentials 目录 0700，文件按用途 0600；不递归修改宿主 `/home` 或仓库。
3. token 使用专用受限挂载副本/组读取：root-owned、专用组、0440，只有 Site Access 服务身份可读；保留 worker 原有可读路径，避免迁移一个服务令其他服务失联。root 桥接继续经固定动作读取，不把 token 下发 GUI。desktop 用户不得进入 Docker/secret 组。
4. 引入仅此服务使用的 seccomp profile，基于当前 Docker 默认策略增加 Chrome 用户命名空间所必需的 clone/setns/unshare 许可；按宿主 AppArmor 需要安装局部 profile 允许 userns。保持 no-new-privileges；不使用 privileged、SYS_ADMIN、全局禁 AppArmor 或全局解除安全约束作为正常方案。[Playwright 官方容器指导](https://playwright.dev/python/docs/docker)
5. `chromium_sandbox=True` 后启动失败必须显式失败，不能偷偷降级 `--no-sandbox`。检查实际进程参数、UID、`chrome://sandbox` / renderer namespace、seccomp 信息；仅 unshare 命令成功还不够。
6. 验证非 root 能继续打开旧 Profile，并验证既有 Cookie 可读取/登录能续存。不要同时改变密码存储后端/密钥来源；本轮不从现行存储迁移到桌面 keyring。若旧数据不可读，恢复快照排查，不覆盖成空 Profile。
7. VNC 宿主绑定继续 `127.0.0.1:5900`，不暴露 CDP/public proxy；桌面 sudoers 和 root-owned bridge 不扩大命令面。

## 7. 失败分类、节奏、fallback 与健康恢复

### 7.1 先修结果真实性

在 `service.py` 和 recipe HTTP 响应转换处统一执行分类：

- 非 2xx 不能默认成功；412 遇 restriction/unusual activity/challenge 等证据判风控，其他 412 判明确未知 HTTP 失败，不一概声称所有 412 都是 bot。
- 200 同样检查 challenge、登录墙、订阅墙；状态码与语义分开。加入截图已出现的 restriction 文案及站点特定标记，避免用单个常见词误判普通文章。
- recipe 不再抛失去状态上下文的通用 RuntimeError；传递 typed failure / RuntimeResponse，带 status、最终 URL（脱敏）、少量分类特征。401 不一概当账号过期：也可能是 WAF，必须看页面/响应证据。
- AUTH_REQUIRED、NO_ENTITLEMENT、CHALLENGE、EGRESS_UNAVAILABLE、TRANSIENT_TRANSPORT、ARTICLE_NOT_FOUND 分开；无法判断时 UNKNOWN_FAILURE，不误写 auth VALID/INVALID。
- 截图/标题/URL 的诊断记录都脱敏；不保存用户输入、Cookie 内容或 token。

### 7.2 强风控六站节奏

Yahoo Finance、Reuters、Barron's、WSJ、Seeking Alpha、MarketWatch 初始统一 site max_concurrency=1、min_interval_ms=3000；正文、爬虫、browser probe 共享预算。其他站点先保留现有 2 / 500ms，实测出现限制再定点收紧，不全系统串行化。

- 正常 challenge 页面最多允许一次约 10 秒稳定等待，不能不断 reload。
- transport transient 按现有有界重试；明确 challenge / 412 restriction 不走 transport 立即重试。
- cooldown 初始阶梯 60 / 300 / 900 秒，读取合法 Retry-After 并设有界上限；一次 429 只进入短 cooldown，不永久冻结身份。
- 持续人机挑战写 `manual_attention_required`，停止该组合自动刷页；可切到**已经验证可用的**另一组合。全部需人工时返回可恢复等待，不反复轮换几十个节点。
- 半开只用同一 Profile + Egress 的低频浏览器探测，低优先级、维护期间跳过；HTTP_PUBLIC 成功不能清除 browser blocked。出口 echo 仍可独立判链路，不作网站健康结论。

认证状态与风控状态互不替代：已有订阅登录可能被 challenge；未登录身份也可能公开页面可用。fallback 的每个 Profile 独立认证，不继承主身份 auth。

## 8. 登录支持域、人工维护与 Profile 固定出口

### 8.1 可操作的维护入口

扩展现有桌面 GUI / root bridge，而非新建工具：

- 列表改为所有显式配置 maintenance_url 或 login_url 的站点，required 不再是唯一条件。先覆盖六个优先站点，包括 Reuters 的 challenge / 可选登录。
- 打开地址优先 maintenance_url，否则 login_url；Reuters 可从已经验证的站内 search / 首页进入正常人工维护，不凭空发明登录地址。
- “Verify and finish” 使用 registry 验证地址；public_access 只报告 `Public access ready`，subscription_article 必须检测真实权限才报告已登录/可读订阅。继续自动 login-close，即使验证失败也清理维护会话并保留准确结果。
- 保持现有英文界面，新增 `Needs manual challenge` / `Public only` 等准确标签即可；不再次引入缺字中文。主身份由当前 combination priority 计算，不能假定所有 `*-1` 永远是主身份。
- 新直连身份可以叫 `barrons-direct-1`；出口列显示地区、egress ID、最近确认 IP 和探测时间，不误把节点中文名称等同于真实出口。
- 提醒不同地区同账号可能受并发登录限制。首轮只要求配置已选主身份，备用明确标未登录/仅公开内容。

SSO 域名修复先覆盖 Barron's / WSJ / MarketWatch 实测登录链，包括 `sso.accounts.dowjones.com`，以及 Barron's 实际客户站点；共享 SSO 只在 support_hosts 精确注册。不把 Dow Jones 所有子域无条件认定为三个站点的 publisher。

### 8.2 直连与节点选择

保留当前固定节点机制。新增逻辑 egress `server-direct`，通过 Clash 专用 listener（建议预留 18084，先检查占用）最终 DIRECT，浏览器仍走相同连接/审计/安全入口。这保留端到端 TLS，不引入 HTTP 重建。

需要更换出口时创建新 Profile 与组合；现有 `profile.site_id / egress_id / directory` 不可变规则保留。不能把 US listener 悄悄改 DIRECT 后继续称原 Profile 固定出口。节点订阅变更导致指纹/实际 IP 变化必须可见、停止把旧“验证通过”当永久事实。

选择起点：Barron's 优先直连候选（已有可打开登录页证据）；Reuters 优先直连新治理身份并留人工 challenge 入口，不承诺未经人工的新身份立即可用；其他站点按第 10 节测试选定。NL 当前不健康，先排除自动候选；JP/US/DE 的 READY 不代表网站可用。

## 9. 版本锁定、快照与回滚

1. `pyproject.toml` pin `playwright==1.63.0`，更新 uv.lock；镜像仍 `uv sync --frozen`，通过该依赖的 install 下载官方绑定 CfT。构建和启动记录并断言 153.0.8010.12 / revision 1243，不从运行容器临时升级。
2. 版本清单纳入仓库，记录 Playwright、浏览器版本/revision、镜像 digest；后续升级走显式 PR + 对照测试。不是永久停在 153，也不是自动追 latest。
3. 当前共享基础镜像安装 Playwright，故升级必须回归仍使用它的旧 crawler/browser consumers。先 build 对应 BASE_IMAGE，再 build Site Access 派生镜像，避免并行拾到旧基础镜像。只滚动本次受影响服务，不无理由全量重启生产。
4. 大版本升级、用户/存储环境迁移前：停止 admission、drain、关闭 context、确认 Chrome 退出、拿独占锁，然后备份完整 Profile（含 Local State、Cookies、IndexedDB、SW、Preferences 等）与版本 manifest。Registry 使用一致性 SQLite backup，不复制正在写入的单个 db 文件。
5. 快照权限与 Profile 同级受限，不放仓库/普通桌面目录。保留最近两个有效升级前快照，默认 14 天并设磁盘容量检查；空间不足阻止该 Profile 升级而不是删当前唯一可恢复副本。
6. 回滚要恢复**旧镜像 + 旧版完整 Profile 快照 + 对应配置/Registry**。不得用 151 直接打开已升级为 153 的目录。先隔离升级后的目录保留诊断，不覆盖用户升级后新登录成果。
7. 上线记录每个 Profile 的快照、升级、clean exit、回滚位置。Cookie 不打印；是否保持登录靠重启后真实验证文章确认，而非 Cookies 文件存在。

## 10. 全部站点的分阶段远端验收

### 10.1 覆盖范围

当前 Registry 为 13 个显式站点 + generic，共 14 项，全部纳入验收：

| 范围 | 站点 | 必测路径 |
| --- | --- | --- |
| 优先六站 | Yahoo Finance、Reuters、Barron's、WSJ、Seeking Alpha、MarketWatch | 主站、实际登录/挑战入口、真实公开文章、订阅验证页（适用时）、现行 crawl recipe（若已配置） |
| 其余显式站点 | TheStreet、Finnhub、CNBC、24/7 Wall St.、Motley Fool、ChartMill、Benzinga | 当前真实正文路径、已有 crawl recipe / 可抓列表（如有），支持域与跳转 |
| generic | 选一条未注册域公开文章，外加跨站 URL / SSRF 样例 | 通用提取、正确 publisher 归属、安全拒绝 |

不为只有正文需求的站点虚构一个全新发现爬虫。验收记录中明确 crawler=未配置/不适用，不冒充“全部爬虫通过”。API-only 来源不强行改为浏览器，也不把其 API 成功当对应网页成功。

### 10.2 测试顺序与调节点边界

1. 完成代码和安全本地/隔离 Linux 验收后，在远端创建候选新组合；不操作用户 xRDP 成熟 Profile，不在生产 Registry 中反复覆盖现有绑定。
2. 每站先验证服务器直连；若失败，再串行测试最多两个已确认健康且地区合理的固定节点候选。沿用相同测试 URL/浏览器版本/固定环境；各新组合分别持久化，不在一个 Profile 上来回换 IP。
3. 低频单并发、请求间至少 3 秒；遇明确挑战记录后停止该组合自动连续探测。不是为强行得到 200 穷举几十个节点。前三候选均失败时记录人工需求/供应商限制，再决定是否追加有依据的候选。
4. 初筛通过后用**真正的生产 Site Access 调用、正文 recipe、crawl recipe**重测。原始 page.goto 成功不能替代有安全门/预算/分类器的正式路径。
5. 相隔至少 30 分钟重复一轮，确认固定出口和结果稳定，再提升组合 priority。能确认两个可用组合则配置 fallback；只能一个可用就如实标单组合，不把未经验证的备用报可用。
6. 本阶段目标是尽可能恢复可正常展示内容的 200 / 正常登录入口。需用户账号或人工挑战的记录为 `MANUAL_REQUIRED`，不自动填账号，不把未登录订阅墙算工程故障或成功正文。
7. 用户之后通过现有桌面完成主身份维护。人工完成后再验订阅正文、原生爬虫、login-close 后后台访问，以及容器重启后认证续存。这个阶段需要用户动作，不能在此前预先写“已验收”。

每条结果至少保存：site、purpose、URL（去秘密参数）、recipe revision、profile、egress/实际 IP/时间、浏览器/env version、主文档 HTTP 状态、最终 publisher、语义结果、正文长度/提取质量、耗时、是否需人工、一次 fallback 结果。不要只统计 HTTP 200 比例。

统一语义结果：`PUBLIC_CONTENT_OK`、`LOGIN_PAGE_OK`、`SUBSCRIBED_BODY_OK`、`CHALLENGE`、`AUTH_REQUIRED`、`NO_ENTITLEMENT`、`ARTICLE_NOT_FOUND`、`NETWORK_FAILURE`、`UNKNOWN_FAILURE`。HTTP 200 challenge 不计成功；无订阅权限不误报未登录。

### 10.3 上线验收清单

- 环境：确为 CfT153 官方配对版本、non-root、有实际 sandbox；native viewport、locale/TZ 与 Profile 配置一致，没有新的身份 override。
- 网络：direct/proxy 出口浏览器内实测；Barron's、Dow Jones SSO、Reuters TLS 验证正常、无额外信任根/ignore_https_errors/请求重建；Clash 为隧道转发。证书轮换/CDN 可能有不同合法证书，不要求跨时间永远同 hash。
- 原生行为：同一静态资源第二次加载可命中缓存；SW 可注册并跨导航正常工作；无 page.route 全资源钩子；页面资源没有人为大面积 blocked。
- 安全：主文档非法跨 publisher、私网/metadata、IPv6、DNS rebinding、302、popup、SW 路径全部有对应测试；失败不直连、不落入默认代理组。不能以真实敏感内网服务为攻击靶，使用受控测试服务。
- 生命周期：同 Profile 竞争启动/备份第二 writer 被拒；budget 等待期间进入维护不会漏入；verify 成功到 close 之间仍排他；维护 pinned；不同 Profile 正常并行。
- 重启：正常 SIGTERM 无 SIGKILL、无遗留写入 Chrome、clean manifest 正确；重新启动 Profile Cookie/存储续存；故障恢复不伪造认证结论。
- 资源：6 GiB 下四个 context + 正常业务观察峰值、排队、OOM、restart；若达上限先收敛 idle context，不按站点数无界常驻，不吞掉其他服务资源。
- 桌面：Reuters 等可选维护可见；无 sudo 密码、无 token/CLI/VNC 地址手工输入；英文字体可读；取消/重入/验证失败可恢复；桌面用户不能读 token 或运行任意 Docker。
- 业务：人工完成后观察 24 小时正文成功率、challenge、队列等待、crawler first observation/晚抓与 fallback，不只看容器 healthy。输出失败分类与基线对照，不声称系统上线立即消除所有站点风控。

## 11. 开发文件与执行批次

| 批次 | 文件/模块 | 交付与完成标准 |
| --- | --- | --- |
| A：结果与互斥 | `src/doxagent/site_strategy/{schema,repository,service,health,budget,runtime,api,client}.py`；相关 recipes / `crawler_plane/worker_runtime.py` | auth/维护分离、原子 lease、全局单维护、typed HTTP failure、412/200 challenge 分类、browser health 独立；竞态单测先通过 |
| B：环境与生命周期 | `runtime.py`、`factory.py`、`src/doxagent/settings.py`、Profile manifest/备份辅助模块 | 环境固定、native viewport、常驻/idle 回收、close/drain、Profile flock、快照恢复；故障注入验证 |
| C：网络行为 | `runtime.py`、`resolver.py`、`egress.py`、`deploy/site-egress-config.py` 及受限 listener 配置 | 安全 sub-rule、最小文档门、observation、缓存/SW 恢复；先验证再删除全量 route |
| D：容器与版本 | `pyproject.toml`、`uv.lock`、`Dockerfile.v2`、`deploy/Dockerfile.site-access`、`deploy/site-access-entrypoint.sh`、生产/服务器 Compose、专用 seccomp/AppArmor 文件 | 确定版本构建、UID/secret 权限迁移、sandbox、stop_grace_period；隔离容器先通过 |
| E：站点与维护 | `seeds.py`、Registry 显式 migration、`deploy/site-login-admin.py`、`deploy/site-login-ui.py`、安装器/运维文档 | 六站节奏、SSO 精确规则、可选登录/挑战维护、新组合注册；不覆盖人工已有 Registry 配置 |
| F：测试与发布 | `tests/test_site_strategy.py`、`tests/test_site_login_desktop.py`；新增 lifecycle/network/sandbox 集成测试与远端验收 MD；`changelog` | 离线/隔离验证、备份→升级→逐站候选→切 priority→真实路径复验→人工交接→24h 观察 |

实现顺序 A → B；C、D 可分别开发/测试，集成后再 E → F。发布不能只有 D（版本/容器）而没有 A/B，否则新的身份仍会遭遇错误重试和维护竞态。不要为每个小改动反复重启线上 Chrome；本地及隔离环境完成后一次受控迁移。

建议拆分测试文件为 `test_site_strategy_lifecycle.py`、`test_site_strategy_network_policy.py`，Linux integration 标记单独执行；不在普通 pytest 中请求第三方网站。定向回归还需覆盖旧正文 enrichment、Yahoo fallback、Reuters crawler 及共享 Playwright 的调用方，既有失败独立记录。

关键单测必须覆盖：412 未识别不能成功、200 restriction、401 challenge/登录墙区分；probe 不清浏览器 block；queued task 与 maintenance 同时发生；verify 成功仍保持占用；两个 login-open 竞争；idle eviction 与 page lease 竞争；start 竞争；close 异常不启动第二 writer；旧 schema 迁移；Profile 不可变绑定；升级失败完整回滚；GUI public-access 与订阅认证不混淆。

## 12. 交付定义与剩余不确定性

开发完成应交付：可回滚的代码与镜像、受控 Registry 迁移、Profile 快照、14 项站点逐项记录、可用主组合/真实备用清单、人工维护清单、24 小时业务效果记录和 changelog。软件测试通过、生产网络通过、人工订阅验证通过三个状态分别标注。

已具备足够证据直接实施：结果分类、维护互斥、持久生命周期、版本配对、固定环境、non-root/sandbox 部署前置、全资源 route 的缓存副作用、Reuters 维护入口缺失。

仍需实现/验收时求证：各节点对各网站的实时信誉，最小文档安全门的缓存/弹窗/SW 行为，宿主具体 sandbox 权限组合，旧 Profile 跨用户/大版本的认证续存，人工 challenge 后 Reuters 的稳定访问。上述项均有明确测试和回滚路径，不以“理论应该”代替远端验收。

最终承诺范围是修复自身访问体系、建立稳定身份并尽可能恢复正常访问；**不承诺所有站点无需人工挑战、不保证任意订阅账号有权限，也不把 HTTP 200 当作正文补全成功率。**
