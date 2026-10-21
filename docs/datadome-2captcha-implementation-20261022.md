# DataDome / 2Captcha 自动处理实施记录

## 设计与边界

基于现有 `BrowserRecoveryCoordinator`，不新建恢复服务或并行业务爬虫。DataDome 处理启用范围通过 Site Access 私有配置中的 sites/bindings 维护，首批 Reuters 主备 External Chrome Identity；不更换 Profile、出口、UA，不解其他 CAPTCHA、不处理账号密码或订阅授权。

1. 真实 iframe URL + 原浏览器 UA 提交官方 `DataDomeSliderTask`，不从截图猜 CID，不伪造 t=fe。供应商返回的 datadome Cookie 仅允许写回同 publisher、同 Browser Identity、同 Chrome 实例及同原页。
2. `RETRY` 或 `t=bv` 仅允许一次原页正常刷新，刷新前保存证据。若仍不能成为真实可处理页，保留人工 Task 和原页、不付费提交。2Captcha 明确 t=bv 代表 IP 被拒绝，不保证正常刷新能解除；不会为此自动更换身份/出口。
3. 新增私有 SQLite `datadome_solver_jobs`，在提交前持久 SUBMITTING，收到 taskId 后转 PENDING；轮询采用 15 秒短 tick，不占 240 秒连续租约。进程中断后凭同一 taskId 继续；提交结果不确定时不重新购买。任务最多等待 240 秒；每事件最多一次购买、滚动 24 小时默认 20 次，全局预留在短事务内。
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
