# 消息管线 Site Strategy 治理与双轨浏览器架构速查

更新基线：2026-09-23，代码基线 `6a601e1a`

本文面向后续负责“新增消息源、修复新消息爬虫、提高正文补全率、维护登录态或浏览器访问”的 Codex。它描述当前已落地实现；实时站点、出口与认证状态必须再查询 Registry，不能仅凭本文推断。

## 1. 模块边界

Site Strategy 是消息网站访问层，不是 Message Bus 消息源配置的替代品。它只统一治理：

- URL/域名属于哪个消息网站；
- 该网站的新消息爬虫策略与正文识别策略；
- 访问使用的 Browser Identity、Profile、Proxy Egress 和 Browser Runtime；
- 登录验证、访问节奏、cooldown、组合 fallback、诊断与统计。

它不治理 Message Bus 的来源优先级或非浏览器后备链。例如 Yahoo 的“crawler → NCP → RSS”仍由 Message Bus 决定，只有 crawler 访问经过 Site Strategy。Yahoo 抓到的 Barron's 文章在正文补全时按最终文章域名归 Barron's，不继承 Yahoo 的策略。

## 2. 一张图理解当前实现

```text
正文补全 body_v2.2 ─┐
                    ├─ SiteAccessClient ── /v1/resolve + /v1/access/execute
新消息抓取/Crawler ─┘                         │
                                              ▼
URL ── SiteResolver ── Site Strategy ── Access Combination
                                              │
                                              ▼
                                     Browser Identity
                              ┌───────────────┼───────────────┐
                              ▼               ▼               ▼
                       persistent Profile  Proxy Egress   Runtime kind
                                                          ├─ Managed Playwright
                                                          └─ External Chrome + CDP
```

上层只接收统一的 `AccessResult`；结果携带 `site_id`、策略 revision、combination、identity、egress、runtime kind/instance/generation 和出口 IP。正文、Crawler、健康与运维据此留痕，不需要知道 Chrome 是怎样启动的。

## 3. 核心资源与不变量

| 资源 | 职责 | 关键不变量 |
| --- | --- | --- |
| `SiteStrategySpec` | 一个网站的域名、正文、crawler、认证和访问组合 | 站点配置有 revision，CAS 更新，可回滚；非 generic 站点必须声明 publisher/API 域名 |
| `AccessCombination` | 某站点可选的访问组合及优先级 | 新模型引用 `identity_id`；可分别为 body/crawler 覆盖组合顺序 |
| `BrowserIdentitySpec` | 浏览器身份资源 | 唯一绑定一个 Profile、一个 Egress、一种 Runtime、固定 locale/timezone/window/lifecycle；多个 Site 可共享 |
| `BrowserProfile` | 持久 Cookie、Storage、Preferences | 单 writer；绑定 Egress 不可漂移；不得热拷正在写入的 Profile |
| `ProxyEgress` | Clash 固定 listener 和实际节点状态 | Profile/Identity 绑定稳定 egress ID；节点可在固定 slot 后替换，但不要偷偷换 ID/端口契约 |
| `SiteIdentityAuth` | 某 Site 在某 Identity 上的认证结果 | 认证状态是 `site_id + identity_id` 维度；共享 Chrome 不等于共享“已验证”结论 |
| `SiteRuntimeState` | Combination 健康、active、cooldown | HTTP 与 Browser 健康分轨，generation fencing 防止旧结果覆盖新状态 |

域名解析采用 exact/suffix 规则，最具体规则优先；冲突会被拒绝。`support_hosts` 只允许当前站点导航/加载受信辅助域名，不取得文章归属权。未注册域名进入按 host 隔离的 `generic` 策略和派生 Profile，不能与其他未知域名共享 Cookie。

Registry 持久化在 SQLite WAL 中，保存策略/Identity 全量 revision、head、Profile、Egress、逐站认证、运行态、事件和正文结果。应用策略与 Identity 都要求 `expected_revision`，不要在线直接修改 SQLite。

## 4. 双轨 Browser Runtime

### Managed Playwright

- Playwright 直接启动 CfT 153 persistent context。
- 适合低风控、访问稳定、无需特殊浏览器连续性的站点，也是默认轨道。
- Profile 仍长期持久化并固定 Egress/locale/timezone/window；不是每次请求创建匿名无状态浏览器。

### External Chrome + CDP

- Chrome Supervisor 以普通方式启动锁定版正式 Google Chrome 153；Playwright 只用 `connect_over_cdp` 接管默认 context。
- 不使用 Playwright launch 参数、stealth、UA/Client Hints override、资源拦截或 TLS MITM；Chrome 原生生成网络与浏览器身份。
- Supervisor 拥有 Chrome 生命周期、Xvfb、loopback VNC、Profile OS writer lock 和优雅 `SIGTERM`；Site Access 重启只断开/重连 CDP，不应杀死长期 Chrome。
- 每次业务任务新建独立 page；同一 Identity 统一限流、并发和生命周期。`always_on` 启动时预热，`on_demand` 可在空闲后回收。
- Chrome、Site Access 均以 UID 10001 运行并保留 sandbox。CDP 只在共享容器 network namespace 的 loopback；VNC 只发布宿主 `127.0.0.1:5900`。

选择原则：默认 Managed；只有控制实验已证明 Playwright 启动方式导致 challenge/login 失败，或明确需要长期真实 Chrome 连续性时，才使用 External。不要为普通站点批量迁移到 External。

当前首批映射：Barron's、WSJ、MarketWatch 共享 `dowjones-main/-backup`；Seeking Alpha 使用独立 main/backup；Yahoo 使用独立 main/backup；它们走 External。Reuters 和 generic 保持 Managed。实际 egress、启停和认证状态以在线 Registry 为准。

## 5. 正文补全链路

启用 Site Access 时，Message Bus 使用 `body_v2.2`：

1. `SharedContentExtractor` 根据文章 URL 调用 Site Access。
2. Resolver 按最终文章域名选择 Site Strategy，而不是按消息来源选择。
3. `body.access_order` 决定 `http_public`、`browser`、`reader` 的尝试顺序；需要登录的首批站点通常只走 browser。
4. Site Access 选择健康 Combination、校验认证、取得 Site+Identity 联合预算，再由对应 Runtime 返回 HTML。
5. `ArticlePipeline` 使用 Registry 的 body strategy ref 和参数识别正文，最后经过统一质量门；结果和完整访问 provenance 写入 trace/outcome。

现有正文策略既有通用抽取，也有站点适配。Registry 支持 `body_xpath`/`remove_xpath`；代码适配覆盖 Yahoo、Reuters、Barron's、WSJ、Seeking Alpha、MarketWatch、TheStreet、Finnhub redirect、CNBC、247wallst、Fool、Chartmill、Benzinga。新增策略 ref 必须在 catalog 注册并通过参数校验，不能从 Registry 注入任意代码或任意 JavaScript。

## 6. 新消息爬虫链路

- Message Bus 的 `site:auto` adapter 在每次 poll 时解析 listing URL，并读取当前 Site Strategy 的 `crawler.ref`。
- 小型、稳定、可信的内建 recipe 当前包括 Yahoo 页面和 Reuters 搜索；它们通过 `SiteManagedBrowser` 调用同一个 `/v1/access/execute`，因此自动继承 Identity、Egress、Runtime、节奏和 fallback。
- 复杂或需独立版本治理的爬虫使用 `crawler:<crawler_id>`，由 Crawler Plane 完成 package、certify、promote、checkpoint、retry 和 artifact 管理。
- Crawler 子进程不直接持有任意网络能力；HTTP/Browser 请求由父进程 broker。启用 Site Access 时，broker 将请求转成 `SitePurpose.CRAWLER`，并记录 cassette/provenance。

不要把“新增 Site Strategy crawler ref”误当成“新增 Message Bus source binding”。Crawler 策略、SourceDefinition、binding/schedule、消息归一化/发布是相邻但不同的配置层，验收时都要检查。

## 7. fallback、风控和认证

- 候选组合按 active、priority 排序；风险失败后在同一次请求中尝试下一个 Combination，不轮换同一 Profile 背后的临时匿名出口。
- Browser 与 HTTP_PUBLIC 分别维护 cooldown。429/challenge/block 的退避为 60/300/900 秒；Browser 连续两次 challenge/block 后标记需要人工关注，等待显式维护/清理。
- Egress 定时探测并保存 observed IP。已验证 READY 节点第一次完整探测失败仍保留最后成功状态，连续第二次失败才置为 `UNAVAILABLE`；真实访问失败仍由正常 fallback 处理。
- 认证要求可以按 body/crawler 覆盖。`required` 且该 `site + identity` 非 `VALID` 时不会发起业务抓取。
- Identity 的 session revision 变化会使旧认证观测失效；Dow Jones 三站即使共享 Profile，也必须分别验证，不能因 Barron's 已登录就把 WSJ/MarketWatch 标为有效。

人工登录统一使用 xRDP 桌面的 **Site Login Maintenance**。受限 root bridge 只允许 list/open/verify/close/recover，不把 Docker 权限、管理 token、登录 token或网站密码暴露给桌面用户；同一时间只允许维护一个 Profile/Identity。业务 lease 会先 drain，验证 URL 必须属于当前站点，完成后恢复业务使用。

## 8. 新增或修复消息网站的最短路径

1. **先确认归属**：列出 publisher/API 域名、support hosts、可能的跨站 redirect；正文按最终 publisher 域名归属。
2. **先验证通用能力**：用 generic/现有 Managed 组合测试公开页、正文和 listing；只有证据证明不足才新增适配。
3. **写 Site Strategy**：声明 domains、body ref/参数/access order、crawler ref、auth policy、组合优先级和访问节奏；先 `validate`，再带 `expected_revision` apply。
4. **选择 Identity**：环境、认证体系和出口确实相同时才复用；否则创建独立 Profile+Egress Identity。默认 Managed，External 需要控制实验依据。
5. **实现站点适配**：正文优先增加受限 XPath/可信 adapter；新消息简单场景加 builtin recipe，复杂场景走版本化 Crawler Plane。
6. **分别验收**：出口实际 IP、主页、登录/challenge、订阅正文、正文抽取质量、crawler freshness/覆盖、fallback/cooldown、重启续存必须分开记录。HTTP 200 不能单独证明正文或消息源成功。
7. **上线与观察**：Registry/ Profile 先备份；Profile 冷迁移必须停 writer；部署后检查事件与 `/v1/stats`，不要用短测结果宣称长期成功率。

修复既有站点时，优先从 `/v1/resolve`、Identity live/runtime、Egress probe、Combination runtime、access events 和 body outcomes 重建事实链，避免直接“换节点”或改 stealth。尤其不要让自动探测打开、关闭或覆盖珍贵的人工登录 Profile。

## 9. 关键代码入口

- 数据模型与合同：`src/doxagent/site_strategy/schema.py`
- Registry 持久化：`src/doxagent/site_strategy/repository.py`
- 域名解析：`src/doxagent/site_strategy/resolver.py`
- 访问编排/认证/预算/fallback：`src/doxagent/site_strategy/service.py`
- 浏览器执行与导航安全：`src/doxagent/site_strategy/runtime.py`
- 双轨接口：`browser_runtime.py`、`managed_runtime.py`、`external_runtime.py`
- 正式 Chrome owner：`chrome_supervisor.py`
- 默认站点和出口：`seeds.py`；首批双轨迁移：`identity_rollout.py`
- Worker/Admin API：`api.py`；CLI：`cli.py`
- 正文入口：`content_enrichment/extractor.py`、`managed.py`、`pipeline.py`、`strategies/`
- Crawler 接入：`site_strategy/client.py`、`crawler_plane/runtime.py`、`message_bus_v2/adapters.py`
- 登录桌面：`deploy/site-login-ui.py`、`site-login-admin.py`
- 部署：`docker-compose.v2-production.yml`、`deploy/docker-compose.server.yml`

核心回归测试：`test_site_strategy*.py`、`test_content_enrichment_pipeline.py`、`test_crawler_plane*_runtime.py`、`test_crawler_plane_contracts.py`、`test_message_bus_v2_news_sources.py`、`test_site_login_desktop.py`。

## 10. 进一步阅读

- 需求与治理边界：`site_strategy_governance.md`
- 初版实施方案：`site_strategy_governance_implementation_plan_20260921.md`
- 双轨实施方案：`site_access_dual_runtime_identity_implementation_plan_20260922.md`
- 最近生产验收：`site_access_dual_runtime_identity_remote_acceptance_20260922.md`

上述方案文档解释“为什么”，本文和当前代码解释“现在是什么”；两者冲突时以当前代码、在线 Registry 和实测结果为准。
