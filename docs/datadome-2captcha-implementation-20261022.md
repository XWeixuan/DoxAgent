# DataDome / 2Captcha 自动处理实施记录

## 设计与边界

基于现有 `BrowserRecoveryCoordinator`，不新建恢复服务或并行业务爬虫。DataDome 处理启用范围通过 Site Access 私有配置中的 sites/bindings 维护，首批 Reuters 主备 External Chrome Identity；不更换 Profile、出口、UA，不解其他 CAPTCHA、不处理账号密码或订阅授权。

1. 真实 iframe URL + 原浏览器 UA 提交官方 `DataDomeSliderTask`，不从截图猜 CID，不伪造 t=fe。供应商返回的 datadome Cookie 仅允许写回同 publisher、同 Browser Identity、同 Chrome 实例及同原页。
2. `RETRY` 或 `t=bv` 仅允许一次原页正常刷新，刷新前保存证据。若仍不能成为真实可处理页，保留人工 Task 和原页、不付费提交。2Captcha 明确 t=bv 代表 IP 被拒绝，不保证正常刷新能解除；不会为此自动更换身份/出口。
3. 新增私有 SQLite `datadome_solver_jobs`，在提交前持久 SUBMITTING，收到 taskId 后转 PENDING；轮询采用 15 秒短 tick，不占 240 秒连续租约。进程中断后凭同一 taskId 继续；提交结果不确定时不重新购买。任务最多等待 240 秒；每事件最多一次购买、滚动 24 小时默认 20 次，全局预留在短事务内。提交超时即使没有返回 taskId 也继续占用日预算，避免跨事件重复使用可能已花费额度。
4. 每次创建任务与回写前，通过受认证代理真实查询公网 IP，与该 Identity 的 Registry Egress 观测对照。配置指纹、出口 generation/IP、UA、验证码 URL、target/instance、恢复 lease/generation 任一改变则拒绝应用旧解。
5. OPEN 人工会话禁止后台改原页。原页丢失时仍走既有恢复探测创建并留存真实新现场；仅可精确操作 CHALLENGE/RECOVERY owner，不能重建 Chrome 后冒充旧原页。
6. Cookie 应用只表示 APPLIED，不更新认证/采集/正文健康。之后由原有真实 listing/body 证明流程提交恢复、解除对应门禁和恢复自然轮询。失败走现有人工 Task、退避与合格 fallback，正常爬虫频率不变。

实现：`datadome_solver.py`（供应商、持久状态、同出口预检）、`datadome_browser.py`（原生 Chrome 精确 target 读取/刷新/Cookie），现有 recovery 插入一个可选 tick，治理库增加一张表和 7 天有界保留。只读管理接口 `/v1/recoveries/{recovery_id}/solver` 仅返回 task 状态、时间、收费和安全错误码。

## 代理入口

使用成熟 Squid，不自己编写公开 TCP 代理。独立小容器 `doxagent-captcha-proxy`，接入现有 Docker egress 网络；按随机强认证账号绑定各 Identity 的既有 Clash listener，`never_direct` 禁止父代理失败后静默直连。腾讯云公网 IP 是入口，不假定等于 Reuters 实际出口。当前主备应分别得到 `.113` / `.114` 出口，不移动 Chrome 身份。

- TCP 18791；腾讯云防火墙仅允许 2Captcha 官方地址 `138.201.188.166/32`，用户已确认放行。
- 应用再检查来源 IP，认证后只允许白名单 Reuters / DataDome / ipify 的 HTTPS CONNECT 443；拒绝私网、非 CONNECT、其他域名和端口。Docker 内部采样仍必须认证。
- 无 TLS MITM、无 HTTP 内容重建、无内容缓存；目标 TLS 从客户端直达原网站。入口采用 2Captcha 支持的 HTTP CONNECT + Basic 认证，**入口认证握手本身不是 TLS**，因此须同时保留单 IP 白名单和可轮换强密码，不能扩大到全网。
- 非 root、cap_drop ALL、只读 rootfs、128 MiB / 0.25 CPU，连接/请求时限、16 连接限额、约 1 MiB/s 聚合带宽；Docker 日志 2×5 MiB，仅身份/状态/字节，不记 URL/query/请求头。
- 密钥仅在现有 credentials 目录 `datadome-solver.json`，UID 10001、0600。Squid 密码仅存 hash，配置 root:13、0640。API key、代理明文密码、返回 Cookie 不写日志、Git 或 job JSON。

部署文件：`deploy/Dockerfile.captcha-proxy`、`deploy/docker-compose.captcha-proxy.yml`、`deploy/configure-datadome.py`。配置程序 root 执行，API key 只从 stdin 读取；已有随机密码保持不变，显式 `--enable` 开启，读取当前 Registry 生成准确 listener 绑定。修改 Registry 出口后需重跑配置并加载代理，未完成时 solver 的同出口门禁拒绝付费，不改为错出口解码。

## 依据

- [2Captcha DataDome 官方任务参数、t=fe/bv 与 Cookie 格式](https://2captcha.com/api-docs/datadome-slider-captcha)
- [官方代理同 IP 要求及访问源 IP](https://2captcha.com/api-docs/proxy)
- [异步结果轮询](https://2captcha.com/api-docs/get-task-result)
- [Squid 认证](https://www.squid-cache.org/Doc/config/auth_param/)、[父代理选择](https://www.squid-cache.org/Doc/config/cache_peer_access/)

## 验收记录（持续追加）

服务端代理：账户认证/正余额通过；主身份出口 `205.198.126.113`，备用 `205.198.126.114`；无认证及非白名单 example.com 均被拒绝。此项不是 2Captcha 从公网实际接入成功的证明，后者仍须真实 task 结果。

离线覆盖：Cookie 域/名称注入拒绝、真实 iframe 参数、RETRY/IP_BANNED 不直接购买、持续 RETRY 只刷新一次、创建超时不重买、poll 超时复用任务、应用 Cookie 不冒充业务成功、代理错误不购买、人工 Viewer 阻止后台改页、Squid 非开放代理与不可直连规则。具体最终测试数和真实收费/业务回执于发布后补充。

局限：当前原页操作适配 External Chrome + CDP；Managed Playwright 沿用既有自动恢复/人工，不宣称已具备 paid solver 接续。更换出口、账号掉号、付费权限和解析错误仍独立处理。供应商认为有解不代表 Reuters 接受，必须分别记录 provider ready、cookie applied、business recovered。

## 本轮真实发现与修复

- Reuters 历史事件 URL 未带 `offset=0`，但可信 recipe 会加入该参数。生产曾得到 HTTP 200、20 条真实 listing rows，却被严格字符串比较判作 CONTENT_ERROR。现仅对 Reuters 的指定 search recipe 按 query/offset 合约比较，默认 offset=0；不同查询、分页、外域及额外参数仍不能冒充同一验证。
- 原页从人工分类转自动、再升级为人工时，原先会复用历史 Challenge Task，而 Recovery 已指向新 target。现将过期人工待办明确归档并转交 RECOVERY，升级时生成绑定新现场的 Task；worker 用 identity/Chrome instance/target 解析物理页，不依赖会变化的任务 token。AUTO 现场寿命按该页创建时刻计算，避免旧事件年龄立即清掉新页。
- 浏览器控制原先等待所有 HTTP 与浏览器业务清空，公共 HTTP 可持续饿死恢复。现提前预留浏览器控制席位并仅等待该 Identity 的浏览器任务退出，独立 HTTP 不再互相阻塞；人工 OPEN 会话仍优先。
- 最终部署观察还发现丢页恢复反复耗尽 75 秒预算，诊断中 Reuters Worker 尚未建立，而普通 browser execute 正占共享容量。增加独立的一个恢复探测席位，避免恢复与连续采集竞争全部 business slots；仍受既有 Recovery 并发和 Identity 单写限制，正常抓取并发不变。该改动有“普通容量为零也可开始恢复、异常后归还席位”的确定性回归。
- 单独恢复席位确实使 Reuters Worker 得以建立，但后续一次真实 BODY 探测仍超时，不能把容量问题当作全部根因。原生只读 CDP 能读到新 Chrome 页中的 1566 字节 Device Check；自动等待末尾原仅给页面交接留下 5 秒。进一步改为保留 15 秒给 frame 判定、所有权 ACK/IPC 回执，并激活精确原页而不导航/刷新。原页能否可靠交接和业务能否恢复仍要分别观察，不能由这条代码修改推定为成功。
- 原生读取支持跨进程 DataDome iframe，并读取实际已加载子 frame URL，避免 iframe.src 尚为旧地址。UA 使用实际 Chrome `/json/version`，不伪造身份，也不依赖忙碌 renderer 执行 JS。已购买任务用真实 URL/网站/UA 确认现场，展示文案变化不丢弃同一个任务结果。
- 本轮真实新现场是 DataDome `/interstitial/` Device Check，不等同于官方 SliderTask 支持的 `/captcha/`。该类型允许一次前台正常刷新及观察，不篡改 URL 参数、不盲目计费；持续 RETRY 或 IP_BANNED 留待后续正常恢复或人工处理。无法承诺 DataDome 最终接受第三方结果。

## 发布验收边界

2026-10-22 北京时间 04:23:20 最终发布 Site Access `d0e31001`，镜像 `sha256:baea03574b08c9bfb32a03fee71527afb30b0d80c2ca7202058d4e85a9138057`。13 个相关生产源文件与该提交逐一 SHA-256 对齐，零差异；Site Access/Supervisor 网络命名空间一致。只更新 Site Access 与新增代理，既有 Chrome Supervisor container 未重建，未复制/清空 Profile、Cookie 或更换出口；保留逐服务发布 manifest 中其他会话的镜像选择。正常的旧丢页身份 idle 回收/新探测启动仍可能更换 Chrome instance，不能宣称本轮所有原页都始终存在。

代理验收：非 root、只读 rootfs、cap_drop ALL、128 MiB、重启计数 0；凭证 UID/GID 10001、0600。真实内部认证连接证实主备出口与浏览器既有 Egress 一致，拒绝未认证/非白名单访问。腾讯云白名单由用户确认，**尚不能仅据此宣称 2Captcha 外部入站已实际打通**。代理等待时限设为 300 秒以覆盖异步解码窗口。

最终代码完整主回归集合 **203 passed / 1 skipped**；solver 专项 32 passed、Ruff F/E9 通过。这两组有重叠，不相加冒充独立覆盖。

04:18:47 真实 BODY 探测在 70.359 秒内完成回执，HTTP 401、1566 字节验证页，`retained=true`，Recovery 从 LOST 变为 PRESENT/AUTO；它证明交接已完成，**不是正文恢复**。04:19:50 后台从精确原页识别 INTERSTITIAL，生成持久 REGENERATED job，执行一次前台正常刷新并开始短 tick 观察；没有提交付费任务。这是供应商前置现场识别/受限重生成分支的真实验收，不能替代 SliderTask 端到端验收。

04:21:10 原页仍是 INTERSTITIAL，job 转为 `WAITING_SLIDER / DATADOME_INTERSTITIAL`，没有再次刷新、没有购买 task，既有恢复探测继续。官方 [Device Check](https://docs.datadome.co/docs/device-check) 是自动验证，不能因名称包含 DataDome 就把它当作官方 SliderTask 已支持题型。后续实际加载 `/captcha/` 且 t 非 bv/it 的可处理场景才允许走付费任务；持续不转化的场景仍是本轮真实未恢复项。

04:22:12 第二轮自然恢复探测完成，63.319 秒、401、1566 字节，仍 `retained=true`；没有把超时解除或未测试正文冒充成功。04:23:20 最终窄发布后，通过原生只读 CDP 再次读到相同新原页及 `/interstitial/`，owner=RECOVERY，证明新现场跨本次 Site Access 重建保留，未额外访问网站。Reuters 仍处于待恢复状态，无付费 taskId；目前不需要用户再次放行防火墙，但也不能声称公网 solver 联通、解码或正文已恢复。

真实业务方面，URL 合约修复确实解除过旧事件；随后再次出现真实 DataDome 验证，Reuters 未被冒充为长期健康。当前付费提交、provider ready、Cookie APPLIED 与真实 listing/body 完整恢复，仍须由持久 job 和实际业务回执分别验收。**截至本记录初稿没有付费 task，因此不能宣称打码成功或端到端完成**。既有后台只读 24 小时验收会继续记录自然事件，不为验收额外购买任务或访问网站。

私有运行回执位于远端 `/var/lib/doxagent/datadome-20261022/`，含发布前 Registry 在线备份、原 manifest、发布回执、13 文件校验及时间标记只读审计。未将私有配置、Cookie 或原始验证码参数加入 Git。回滚可使用该目录保存的前镜像/manifest 中 Site Access 单服务配置；不回滚或覆盖业务数据库、Profile、其他组件发布项。

既有只读验收任务（automation 24）已更新为最新基线 UTC 2026-10-21T20:23:20Z，观察截止 2026-10-22T20:23:20Z；保持无实质变化静默，明确区分供应商任务/Cookie 写入/真实业务恢复，不额外访问网站或购买测试。自然验证码与长期稳定性不以本次离线用例代替。
