# 消息网站策略治理与正文补全 / 爬虫访问体系：可执行开发方案

日期：2026-09-21。状态：开发方案，尚未实施。

需求依据：[site_strategy_governance.md](site_strategy_governance.md)；实现依据：[上一轮代码勘察](body_crawler_policy_governance_code_survey_20260921.md)及本轮关键代码复核。用户已确认：迁移全部现有专项正文适配，重点调优 Yahoo Finance、Reuters、Barron’s、WSJ、Seeking Alpha、MarketWatch；Yahoo / Reuters 同时验收网页新消息抓取；复用远端 Clash 订阅节点。

## 1. 交付决定与实现边界

本轮完整交付“Registry → 站点解析 → 稳定访问组合 → 共享访问预算 → 网站适配 → 可比较的真实结果”。配置层、访问层和首批网站调优一起完成，不以 Registry 表建好作为终点。

确定采用以下实现：

| 项目 | 决定 |
| --- | --- |
| 包与命名 | 新增 `src/doxagent/site_strategy/`，统一使用 Site Strategy，不再引入另一套 Site Policy 名称 |
| 持久化 | 独立 SQLite 文件；一个服务写入，CLI/API 经该服务维护；JSON/YAML 是导入导出格式，不是另一个事实源 |
| Runtime Owner | 新增一个 `v2-site-access` 服务，同时持有浏览器、HTTP session、站点队列与健康状态；单进程、单 Uvicorn worker |
| 浏览器 | 每 Profile 一个持久 user-data-dir；按需启动、闲置关闭进程，保留磁盘身份；Crawler 和正文通过服务共用 |
| 出口 | 复用现有 Mihomo，一个被选中节点对应一个固定 listener；不改变原有通用代理端口的规则 |
| 策略维护 | Registry 选择已有 crawler capability、正文 adapter 与参数；不复制 Crawler Plane 的代码资产生命周期 |
| 管理入口 | 完整 CLI + 内部 API；首版不增加前端页面、不增加新的 agent tool 框架 |
| 登录凭据 | Registry 记录统一归属与引用，服务本地受限文件保存秘密；不引入外部 secrets infrastructure；首版人工登录 |
| 运行效果 | 风控故障组合切换、正文优先共享预算、按实际 publisher 路由、真实成功率与晚抓统计必须全部落地 |

不改 SourceDefinition / Binding 的职责、Ticker 搜索词与 cadence、原生 IBKR/Benzinga 正文、标准 API/RSS/数据库/LLM 出口。网页内使用的文章 API、XHR、字幕请求属于当前网页操作；同 hostname 上独立运行的 Yahoo NCP/RSS acquisition 仍不纳入治理。是否治理由“调用用途 + URL”共同决定，不能只拦截域名。

## 2. 本轮核实的事实及对旧勘察的修正

2026-09-21 只读远端检查：仓库 `main@d7d1c24`；Mihomo API 确认 `v1.19.31`。容器为 `doxagent-egress-clash`，位于 `doxagent-v2_default`，没有发布宿主机端口，配置挂载自 `/opt/doxagent-egress-clash/config.yaml`。

- 配置含 90 个 Shadowsocks 条目，其中包含流量/到期提示名称；不能将全部条目当作已验证有效节点，更不能当作 90 个独立公网 IP。
- 尚无专用 listeners。规则最前两条为 Reuters 域名固定荷兰标准 2、其余 `MATCH,DoxAgent-Egress`。后者为四节点自动 fallback，当前选中日本标准 6；其余候选为日本高级 1、美国标准 5、荷兰标准 2。
- controller 只监听代理容器的 `127.0.0.1:9090`；宿主机直接访问失败不代表 controller 故障，经容器网络命名空间读取成功。无需为本方案将 controller 暴露到公网。
- 旧 `doxagent-reuters-chrome.service` 当前 `activating/auto-restart`，`ExecMainStatus=1`，快照重启计数 959；CDP 不可达。本轮未分析退出的具体原因。上一轮 `inactive` 是不同时间快照，不应继续当作当前状态。
- `news_adapters.py` 的 Yahoo browser 429 会同时更新 `_page_api_retry_at`，使 NCP 被页面失败连带抑制；这条跨层联动必须移除。
- `SharedContentExtractor` 只接受 `body_v2.1`，EnrichmentJob 已冻结 pipeline version；升级必须处理在途版本，不能静默改写历史 job。

本需求对旧勘察优先：不把账号强制纳入不可分割的三元组合；不强制外部秘密管理服务；不把普通 403、普通超时、登录失效一律判成风控；不将 Yahoo NCP/RSS 迁入新访问服务。节点可达性、账号授权与网站访问质量仍需实施阶段真实验证，本轮没有重新扫描节点或抓取文章。

## 3. 运行结构与协议

```text
Message Bus / Source adapter                 Content Enrichment Hub
  ├─ 非网页 API / NCP / RSS → 原 transport     ├─ 原生 provider body → 原校验
  └─ 网页 crawler                             └─ ArticlePipeline + BodyStrategy
       └─ Crawler Plane / builtin bridge             │
                       └──────── SiteAccessClient ────┘
                                      │ 内部 HTTP
                               v2-site-access
                      ┌───────────────┼────────────────┐
                 Registry / Resolver  站点预算与健康   Browser / HTTP owner
                      │ SQLite                              │
                      └────────────────── 固定 Clash listener → 指定节点
```

### 3.1 Owner 的职责

Owner 只执行有界访问操作，不接管正文队列、Message Bus 发布、爬虫 checkpoint、文章最终质量判定。它负责 resolve、组合选择、请求排队、访问、访问风险识别、切换、诊断和 Profile 生命周期。正文质量与 crawler 业务解析仍在现有管线。

一个有界操作可以是 `http_fetch`、`browser_read`、`browser_recipe`。recipe 是可信的注册能力，如 `yahoo_latest_news`、`reuters_search` 或正文的 `wait/expand` 规则，不开放任意网页返回值驱动的 Python/JS 执行接口。复杂可信 JS 随代码发布，Registry 中的参数必须通过 recipe schema。

### 3.2 最小协议

`SiteAccessClient.resolve(url, purpose)` 返回 `ResolvedSite`：`site_id/runtime_key`、`strategy_revision`、`body_strategy_ref + parameters`、`crawler_strategy_ref`、auth policy 和 domain match evidence。未知站点返回 generic 模板及派生 runtime key。

`SiteAccessClient.execute(AccessRequest)`：

```text
AccessRequest
  request_id, operation_id, parent_job_or_poll_id
  purpose: BODY | CRAWLER | PROBE | LOGIN
  url, method, parameters, allowed_headers
  mode: HTTP_PUBLIC | BROWSER | BROWSER_FETCH
  recipe_ref?, recipe_parameters?
  strategy_revision?, remaining_budget_ms, max_response_bytes
  excluded_combinations[]                 # 本次业务操作已试过，防止重复循环

AccessResult
  status, final_url, headers_filtered, body_or_recipe_result
  disposition: SUCCESS | ACCESS_EXHAUSTED | CONTENT_ERROR | AUTH_REQUIRED
               | REDIRECT_REQUIRED | BUDGET_DEFERRED | SERVICE_UNAVAILABLE
  failure_category, reason_code, retry_not_before?
  site_id, runtime_key, strategy_revision, body_strategy_ref?
  combination_id, generation, profile_id, egress_id, exit_ip_observation
  attempts[], queue_wait_ms, network_ms, redirect_evidence?
```

Owner 根据 URL 校验调用方传入的 site/revision，禁止通过伪造 site_id 借用另一个站点的登录身份。response 中不返回 Cookie、Set-Cookie、Authorization 或完整代理凭据。HTML/响应体只返回到原有采集/正文执行域，诊断表只存摘要与受限 artifact 引用。

HTTP 传入相对剩余预算；Owner 在入口转为本机 monotonic deadline。排队、冷启动、每次访问和组合重试均消耗同一预算。客户端取消或 deadline 到期必须取消队列项、关闭该操作 page、释放名额；不能留下后台继续抓取。

同 request_id 的并发调用合并，短期缓存结果（默认 60 秒、总量 32 MiB，超出按 LRU 回收）；进程重启后不承诺网络 exactly-once。业务操作仍依赖原有 job lease、poll/checkpoint 与 Raw 去重；服务重启不得额外推进 checkpoint。

内部 API 固定为 `/v1/resolve`、`/v1/access/execute`、`/v1/outcomes:batch`、`/v1/sites`（含 revision/apply/rollback）、`/v1/egresses`、`/v1/profiles`（含 login/verify）、`/v1/stats`、`/healthz` 与 `/readyz`。Access/resolve/outcome 使用 worker token，配置与登录命令使用 admin token；readyz 检查 Registry 与浏览器驱动可启动条件，不因单站账号失效判整个服务不就绪。协议业务错误返回结构化 disposition；鉴权/输入错误使用 HTTP 4xx，不将目标网站的429变成Owner自身的限流响应。

### 3.3 故障边界

Owner 不可达时客户端返回明确错误，不私下创建临时无身份浏览器或转为宿主机直连。正文按原 durable queue 的剩余时间重试，保留消息的 UNAVAILABLE 发布语义；Yahoo 可以依据“页面能力不可执行”进入原有 NCP/RSS，但记录服务故障，不宣称组合已全部尝试。

## 4. Registry 数据模型与配置生效

SQLite 路径：容器 `/data/site-strategy/registry.sqlite3`；WAL、foreign keys、5 秒 busy timeout；所有写操作由 Owner 执行。配置和健康持久化，正在等待/运行的请求不建第二套 durable job 队列。

### 4.1 七张表足够

| 表 | 主键与必要字段 | 说明 |
| --- | --- | --- |
| `site_strategy_revisions` | `(site_id, revision)`，`spec_json, digest, created_at, actor` | 不可变策略快照，组合列表内嵌；内置 `generic` 同样版本化 |
| `site_strategy_heads` | `site_id`，`active_revision, enabled, updated_at` | 当前指针；修改使用 expected_revision 比较更新 |
| `proxy_egresses` | `egress_id`，`node_ref, node_fingerprint, listener_port, endpoint, enabled, observed_ip, observed_at, generation` | 固定节点资源，秘密留在 Clash 配置 |
| `browser_profiles` | `profile_id`，`bound_egress_id, directory_key, credential_ref?, login_url?, auth_state, session_revision, created_at` | Profile 与 egress 一经使用不原地换绑 |
| `site_runtime` | `runtime_key`，`state_json, generation, updated_at` | active combination、各组合 cooldown/probe/auth eligibility；站点内原子更新 |
| `access_events` | `event_id`，`operation_id, site_id, combination_id, occurred_at, category, payload_json` | attempt、切换、出口变化、登录、配置变更；索引 site/time、operation |
| `body_outcomes` | `job_id`，`final_site_id, strategy_ref, combination_id, outcome, reason, completed_at, payload_json` | 每 job 一个终态，upsert 幂等；不按重试次数膨胀成功率分母 |

`site_runtime.state_json` 是有 schema 的小对象，不写无限列表；历史写 events。Generic 派生状态使用同一表，不自动建立一个人工 Site Strategy。配置历史保留全部；events 默认 30 天，body_outcomes 默认 90 天；清理不能删活跃配置、Profile 或业务原文。首版直接有界 SQL 统计，不增加报表数据库或后台指标平台。

### 4.2 SiteStrategySpec

```yaml
site_id: yahoo_finance
revision: 1
domains:
  - {match: exact, host: finance.yahoo.com, role: publisher}
  - {match: exact, host: query1.finance.yahoo.com, role: api}
  - {match: exact, host: query2.finance.yahoo.com, role: api}
support_hosts:
  - {match: suffix, host: yimg.com, role: asset}
  - {match: exact, host: login.yahoo.com, role: login}
access:
  combinations:
    - {id: yahoo-a, profile_id: yahoo-a, egress_id: jp-standard-6, priority: 10, enabled: true}
    - {id: yahoo-b, profile_id: yahoo-b, egress_id: us-standard-5, priority: 20, enabled: true}
  overrides: {}          # 可选 crawler/body -> 本站组合 ID 列表；默认完全共用
  max_concurrency: 2
  min_interval_ms: 500
crawler:
  ref: builtin:yahoo_page@1
  parameters: {}         # 只放网站结构/访问参数，不放 ticker、查询词或 cadence
body:
  ref: builtin:yahoo@1
  access_order: [http_public, browser, reader]
  parameters: {}
auth:
  requirement: none      # none | optional | required，可对 crawler/body分别声明
  login_url: null
```

此示例中的节点名是已有候选的稳定逻辑 ID，尚不代表通过新验收。Profile 的 credential_ref 可为空；账号不加入组合 ID 或强制三元绑定，多个 Profile 可以引用同一账号，但真实测试必须检查网站并发会话限制。

一个 Profile 只能归属一个逻辑站点（及其明示 alias/support host），同站多个组合不能用同一 Profile 绑定不同 egress。`overrides` 如配置，则保存目的对应的 active ID，但仍共用站点预算和组合健康；未配置的 crawler/body 始终共用同一个 active/generation。首批全部使用默认共享模式，例外必须由真实测试证明必要后人工写入。

### 4.3 生效、版本与修改约束

1. `validate` 检查 host 冲突、引用存在、Profile/egress 绑定、strategy 参数、crawler 参数 schema 兼容性、组合重复与 active 引用；不可用节点不能伪装为已部署入口。
2. `apply --expected-revision N` 在单事务中写新 revision 并切 head；保存 actor/diff/digest。旧 in-flight 操作继续使用已解析 snapshot，新操作使用新 revision。
3. 每个正文 queue attempt 对每个解析到的站点固定一个 revision，组合健康仍实时读取；queue retry 可使用新 revision并留痕。Crawler 单次 execution 固定策略和 crawler asset version。
4. 回滚复制旧 spec 为新 revision；不回退健康事件、不复活失效账号、不删除 Profile。禁用组合停止新分配；已运行请求允许有界完成。紧急停止是显式 cancel，不混入普通配置更新。
5. 更换节点建立新的 egress/profile/combination，再改站点组合列表。节点凭据更新但节点身份不变可做配置维护；节点目标身份改变不可复用原 egress ID 静默代换。
6. `enabled=false` 表示已注册站点暂停受管网页访问，返回 `SITE_DISABLED`；不能悄悄降到 generic 绕过人工暂停。需要删除归属时先显式 review resolver diff。

## 5. 域名归属、跨站跳转与 Generic

### 5.1 Resolver 规则

规范化为小写 IDNA hostname、移除末尾点；支持 exact 与带点边界的 suffix，`foo.com` 不匹配 `evilfoo.com`。exact 优先、suffix 最长优先；同优先级跨站冲突拒绝写入。裸域与 www 显式种子覆盖；不用任意 regex 和隐式公司归属推断。

`domains` 决定主文档/publisher 归属；`support_hosts` 只说明某页面依赖的 API、登录、CDN，不把共享 CDN 抢占成某网站的 publisher。已注册域名可显式 exclude 子域，再交给更具体规则或 generic。首版种子覆盖六个重点网站的裸域/www及经实测出现的页面、API、登录子域，禁止粗放注册全部 `yahoo.com`。

页面的样式、脚本、iframe、图片和站内 XHR 均沿所属 Profile 的固定代理走，不对每个 asset 重新挑选组合。公用 CDN 可以被多个站点引用；因其失败不能直接判定 publisher 的全部组合有风控。涉及正文数据的跨站 API 必须声明支持关系，否则按新目标重新 resolve。

### 5.2 Publisher resolution 的实际顺序

“先知道最终 publisher 再选策略”不能假定完全零网络：Google/Finnhub wrapper 有时必须先访问。按以下顺序执行：

1. 先使用已有 verified alias、明确 publisher URL 和可离线解析 wrapper；消息的 source_id 只保留发现来源。
2. 未知最终目标时，按当前 URL 所属策略访问包装页；从 3xx、受支持 original link、canonical 及文章身份证据解析下一目标。
3. HTTP 每一跳禁止自动无条件跟随；跨站后先返回 `REDIRECT_REQUIRED`，释放旧站点 permit，重新 resolve/申请新组合。保留现有最多 6 个 HTTP hop、3 个 publisher 页面检查和循环检测，不在组合切换后重置这些全局上限。
4. 浏览器在 main-frame 导航发送前拦截跨 publisher 请求，返回目标交给调用方重新 resolve；同站路径可继续。登录 recipe 只允许显式 login support host 的 SSO 来回跳转，在同一 Profile 完成；不把 SSO 当作正文 publisher。
5. 继续校验标题、文章 ID、URL/内容证据；canonical 是候选证据，不能因为页面写了某 URL 就认定有完整正文。失败仍记录访问过的各站点，不能把 Yahoo wrapper 成功当作 Barron’s 正文成功。

新服务复用并收紧现有 `public_url`、响应体大小和 DNS 检查；对 redirect、browser navigation、XHR 同样生效。登录专用管理路径与公网采集路径分开，网页不能访问 Owner 管理 API、CDP、内网元数据。代理不成为 SSRF 绕过路径。

浏览器禁止未代理的 QUIC/WebRTC 出站，HTTP CONNECT 与代理DNS路径也纳入网络验收。这里要求验证固定入口的实际流量，不以设置了一个 proxy 参数推断全部流量都经过代理。

### 5.3 Generic 的身份和状态

未注册站点使用一个人工维护的 `generic` 策略模板，包含两个固定 egress 候选、通用正文 adapter 与访问顺序。运行 key 默认采用规范化 hostname；www/裸域仅按明确通用归一规则合并，不将任意子域合并。

需要浏览器时懒创建 `generic/<host_hash>/<egress_id>` 持久 Profile，runtime health 按 host 隔离；这属于运行资源，不生成 SiteStrategy 配置。HTTP session 同样按 runtime_key + combination 缓存。不同未知网站不能共享同一个 cooldown、登录 cookie jar 或一个全局故障开关。

Generic Profile 只按需启动，闲置进程回收而目录保留；统计最近使用与磁盘大小，通过显式维护命令清理，不按每次请求创建临时 profile。未来人工注册该站点时，可显式接管原派生 Profile，维持原 egress 绑定。

## 6. Clash 固定入口与真实出口观测

### 6.1 复用一个 Mihomo 实例

给选中的不同节点生成独立 mixed listener，例如内部端口从 18080 分配；所有 Profile 和 HTTP client 使用该 listener。listener 的 `proxy` 直接引用具体代理节点，不能指向 fallback/url-test/load-balance 组。

```yaml
listeners:
  - name: doxagent-jp-standard-6
    type: mixed
    listen: 0.0.0.0
    port: 18080
    proxy: "日本标准 6 的真实订阅节点名称"
```

端口只在现有 Docker 网络使用，不映射公网。固定 listener 对页面所有 hosts 使用同一节点，避免现有 Reuters 特殊规则把某个组合的请求再次改道。现有 7893 和其规则保留，Yahoo NCP/RSS 等旧 transport 继续使用原配置。

Mihomo 的 listener `proxy` 支持直接指定出站节点，见[官方 listeners 文档](https://wiki.metacubex.one/config/inbound/listeners/)；mixed 入口见[官方配置](https://wiki.metacubex.one/config/inbound/listeners/mixed/)。上线前必须用远端固定版本 `mihomo -t` 验证生成配置，并验证实际请求链；文档支持不等于当前部署已验证。

### 6.2 维护流程

增加一个部署脚本：读取受限原始订阅配置 + Registry 导出的 egress manifest，保留原配置其他部分，重建仅 `doxagent-*` 命名空间的 listeners。流程为 render → 当前版本语法校验 → 精确目标备份 → 原子写候选配置 → reload → readiness/节点绑定验证 → 标记 egress 可用；失败回滚该候选配置。

这是运维命令，不在每次抓取时调用 Docker 或修改 Clash。Owner 无 Docker socket、无 root 权限；脚本从宿主机通过容器网络命名空间访问现有 controller。订阅后续同步必须走同一 merge 流程，避免覆盖固定入口。当前使用展开后的 proxies 列表，不假设远端已有自动 provider refresh。

节点导入只记录逻辑 ID、实际名称、配置身份 fingerprint、入口、可用状态；跳过流量/到期提示伪节点。不复制订阅 URL、密码或完整 node config 到 Registry / Git。订阅更新删除/重命名节点时将对应资源标为 `CONFIG_MISSING` 并通知维护；不能自动选同地区另一节点代替。

首轮从 JP、US、NL、DE 等已有资源选 6–8 个候选，轻量连通性筛选后测真实网页。每重点网站至少配置两套组合，尽量有两个不同 observed IP；同一节点可服务不同站点的独立 Profile。数量可扩展，不自动穷举 90 个节点做每站请求。

### 6.3 Exit IP 的意义与探测

新 egress 启用、浏览器冷启动需要新鲜观测时，以及活跃 egress 每 30 分钟，借同一固定入口做 IP echo；首版观测出站 IPv4，并禁止浏览器/HTTP 在目标连接上通过未观测的 IPv6 直连。两个受限公共 IP echo endpoint 中一个失败再试另一个，严格限时；不为每篇文章探测。

记录 `observed_ip, observed_at, probe_endpoint, egress_generation`。服务不能声称回显地址就是目标站一定看到的 IP，只将其作为该出口的观测值；固定节点配置与必要的代理连接日志共同证明路由。

实际出口改变：写 `EGRESS_IP_CHANGED`、增加 egress generation、使旧连接池/浏览器在已运行操作结束后重建并复核登录状态；保留原 Profile，不自动清 cookies，也不只因 IP 变动全站冻结。只有观察到风控才进入组合健康切换。多个节点同 IP 标记 `DUPLICATE_EXIT`，优先把不同 IP 的组合排在前面，不宣称具备独立冗余。

## 7. 持久浏览器与 HTTP 会话

### 7.1 一个容器中的唯一 owner

`v2-site-access` 基于现有 server 镜像扩展 Xvfb 与仅维护时使用的 VNC，以非 root 运行。常规运行用固定虚拟显示器上的 headed Chromium，版本随镜像固定；不依赖 xrdp 用户登录和 DISPLAY=:10。headless 可作显式测试配置，不能在运行失败时静默换模式和身份。

每 Profile 在 `/data/site-strategy/profiles/<profile_id>/` 调用 `launch_persistent_context`，从第一次启动即传入固定 proxy。Profile 状态包括 cookies/localStorage/IndexedDB 等浏览器持久存储；仅页面内存/sessionStorage 状态不承诺跨重启恢复，以重启后的登录验证为准。持久 context 和单目录不可并发启动的约束见 [Playwright 官方文档](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)。

Owner 启动持有根目录排他文件锁，各 Profile 再持有目录锁；第二个 Owner 或同目录外部 Chrome 必须拒绝启动。不是通过删除 Chromium SingletonLock 强行接管。容器退出同时终止所属浏览器，重启加载 Registry、cooldown 与 profile，再按需求启动。

初始容量：最多 4 个常驻浏览器进程、全服务 4 个活动 page、同站点最多 2 个活动操作；idle 5 分钟且无 login maintenance 的 runtime 可关闭。达到上限回收无活动页的 LRU runtime，繁忙时进入有界等待。HTTP 公共请求不必启动浏览器。沿用 `resource_safety.py` 的内存压力信号与当前共享父 cgroup 配额，不重新修改整机资源方案；等待压力解除也必须受请求 deadline 约束。

### 7.2 HTTP 与浏览器共享到什么程度

所有请求先选同一组合、固定 egress、预算与健康。但 `curl_cffi` 与 Chromium 的 TLS/JS/session 语义不同；不能通过相同 User-Agent 宣称它们是完全相同的浏览器身份。

- `HTTP_PUBLIC`：保留 `curl_cffi` Chrome impersonation；session 按站点/组合隔离。适用于公开稳定网页/API，HTTP cookie jar 可由 Owner 在受限 profile 旁持久保存，禁止跨组合复用。
- `BROWSER`：直接使用组合持久 context 导航、等待、展开、读取可见内容。
- `BROWSER_FETCH`：需要浏览器 cookie/session 的文章 API 或 XHR，在该 context 的页面中执行受支持的 fetch recipe；保持同一浏览器网络栈。认证站点禁止先匿名 direct 探路。

不做全量双向 cookie 同步或伪造通用 session 桥。需要 cookie 连续性的站点将 crawler/body 配为 browser/browser_fetch；公共站点可以保留成本更低的 HTTP。Browser Profile 与 HTTP jar 都归同一 Owner 维护，但诊断明确 transport 类型。

旧 body/crawler 中的 browser 创建逻辑在新模式下替换为 client adapter，禁止重复启动目录、CDP+proxy 新建临时 context 或 CDP 失败无身份降级。

### 7.3 人工登录与凭据

统一位置 `/data/site-strategy/credentials/<credential_id>.json`，目录 0700、文件 0600，只挂载到 Owner；Registry 保存 credential_ref、用户名显示标签、login_url、使用 Profile 与最近验证状态。import 从 stdin/隐藏交互输入，普通 list/export/status 永远不返回密码。本轮使用受限本地文件即可，不强制 KMS/Vault，也不为密码设计版本历史。

`profile login-open` 将指定 profile 进入 MAINTENANCE，停止分配新任务，等现有 page 有界结束，在同一 owner/context 打开登录页；通过 SSH 隧道连接仅 loopback 发布的维护 VNC。操作员完成普通登录/MFA/网站验证，`profile verify --article-url ...` 用真实文章验证访问范围后恢复服务。维护结束关闭远程访问，Profile 保留。

浏览器显示“已登录”不等于具有文章订阅权限：`AUTH_VALID` 和 `ENTITLEMENT_MISSING` 分开记录。未来自动登录任务可复用 `login-open / credential resolve / verify / login-close` 内部接口；本轮不实现 Codex SDK 任务、自动验证码或定时自动登录。

## 8. 健康、fallback 与访问预算的确定规则

### 8.1 分类决定行为

| 证据 | 分类 | 组合行为 |
| --- | --- | --- |
| 主文档/关键数据接口明确 429 | `ACCESS_RATE_LIMIT` | 单次即可 cooldown 并尝试下一组合；Retry-After 作用于失败组合 |
| 已有 DataDome/PerimeterX/CAPTCHA/WAF 特征，含 HTTP 200 挑战页 | `ACCESS_CHALLENGE` | cooldown 并切换；保留挑战证据，不把它记作 empty body |
| 403/401 且有站点适配确认的 IP/network block 证据 | `ACCESS_BLOCK` | cooldown 并切换 |
| 无风控证据的 401/403、普通 451 | `AUTH_OR_ACCESS_UNKNOWN` / `REGION_RESTRICTED` | 不自动换遍节点；账号/区域/权限单独诊断，明确 region block 规则可经站点配置归入 ACCESS_BLOCK |
| 明确登录失效 | `AUTH_REQUIRED` | 更新该 Profile 的 auth eligibility；不增加 egress strike，不随机轮换账号；该目的返回登录维护需求 |
| 无订阅权限/账号过期 | `ENTITLEMENT_MISSING` | 标记授权问题，保持出口健康；不自动换组合试权限 |
| 404/410/文章删除、非文章页、正文不足、selector/DOM/schema 错误 | `CONTENT_ERROR` / `EXTRACTION_ERROR` | 不切组合；正文可在同组合尝试其他受支持提取路径；crawler 可结束该 acquisition 路线 |
| 合法结构且无新消息 | `EMPTY_SUCCESS` | 成功，不切组合，不触发 NCP/RSS |
| 普通 timeout、DNS/TLS/reset、5xx | `TRANSIENT_TRANSPORT` | 预算允许在原组合最多重试一次，不据此判风控 |
| listener 不存在、代理明确不可达、节点配置消失 | `EGRESS_UNAVAILABLE` | 独立可用性状态，受影响组合暂不分配；可选择其他可用组合，不污染风控统计 |
| 浏览器退出、Owner 故障、内存压力、排队超时 | `RUNTIME_UNAVAILABLE` / `BUDGET_DEFERRED` | 原 profile 可重启一次，超时后返回，不给全部节点加 strike |

底层先分类 access，业务层再判断 extraction。解析器只上报结构化内容结果，不能直接调用“封禁节点”。Crawler raw 403 不经站点诊断不得升级为 ACCESS_BLOCK。

登录失效后仅从 `auth=required` 的候选中排除该 Profile；首版不自动因身份失效立即重试另一个账号组合。其余允许匿名的 crawler 目的仍可使用该组合；其他已有可验证身份的组合可供后续任务或人工切换使用，诊断必须标明身份选择而非风控 fallback。

### 8.2 状态机与并发切换

风控状态只有 `READY → COOLDOWN → HALF_OPEN → READY/COOLDOWN`。配置禁用、auth eligibility、egress 可用性是独立字段，避免用一个 HEALTHY 布尔值混淆原因。

- READY 使用当前 active；顺序按人工 priority，相同 priority 按组合 ID；正常请求不轮换。
- 第一次风险失败冷却 60 秒，连续风险失败 300 秒、900 秒，之后上限 900 秒；429 采用 `max(规则冷却, Retry-After)`，不把合法更长 Retry-After 截短。
- 切换在 site lock + SQLite 事务内更新 active/generation；其他等待请求在实际 dispatch 前重新读 active，不沿用入队时的失效组合。
- A 失败切到 B 后，A 已在运行的另一请求可以完成；它的迟到成功不得把 active 改回 A 或提前清 cooldown。对相同 operation/attempt 的重复报告幂等；旧 generation 只写历史，不覆盖新状态。
- 每次业务操作对每套组合最多做一次风险尝试；普通 transport 同组合重试最多一次。默认两套组合，无人工 max=2 硬限制；配置更多组合时，按剩余预算逐个尝试，不重新从 A 开始。
- 冷却到期不立即全部探测。无前台请求的轻量维护循环每 60 秒查看到期组合，每站最多一个 HALF_OPEN probe；有请求竞争时 probe 排最低优先级。probe 用已知有效页面和访问结构验证，404/DOM 错误记 probe 不确定，不再给节点加 strike。probe 成功恢复 READY，但不抢回仍健康的 active B。
- 全部已知可用组合确实失败/处于冷却时返回 `ACCESS_EXHAUSTED + earliest_retry_at`。若因为总 deadline 还有组合未尝试，返回 `BUDGET_DEFERRED`，不能伪称全试失败。

### 8.3 两层 fallback 和总时限

Yahoo 网页阶段默认总预算 45 秒（含等待），单组合 browser 操作上限 20 秒；剩余时间小于 3 秒不启动新浏览器尝试。默认两套组合足够在正常冷启动条件下完成顺序切换。更大组合池由站点配置同时增加网页阶段预算，不以无界重试拖慢 ticker。

网页成功（含合法空结果）立即返回；风控只在组合内部切换，待真正 ACCESS_EXHAUSTED 才进入 NCP，再按原逻辑 RSS。若 parser 错误，则已知换身份不能修复，结束网页策略后走 acquisition fallback；若 Owner 不可用，同样明确记录页面能力故障后走原 fallback。

仅预算耗尽但仍有未尝试组合时，首版返回页面延后结果，由第10.4节的专用 deferred 调度分支在初次5秒后再试（不推进 checkpoint、不声称 COMPLETE）；避免把 45 秒超时伪装成全部组合失败。上述 parser/runtime/budget 情况必须分别记录，不能混成“节点全部不可用”。

正文继续使用现有 job deadline（当前最大 180 秒）和至多两次 queue attempt，组合内部切换不增加 durable attempt_count。全部冷却且 earliest_retry_at 在 deadline 内时，按该时间 requeue；超出 deadline 则按现有契约保留原消息并记录 UNAVAILABLE。新增 `access_exhausted`、`budget_deferred` 的 queue retry 映射，不直接沿用当前 `challenge_required` 一律 terminal 的判断来跳过组合 fallback。

### 8.4 共享站点预算

单站默认并发 2、两次顶层操作启动间隔 500ms，均可 Registry 调整。permit 覆盖实际顶层 HTTP 操作或 browser recipe 的页面生命周期；不对每个静态资源再排队，否则易造成加载死锁。发布接口/API 与普通 acquisition RSS 不进入该预算。

Owner 维护每站 BODY、CRAWLER、PROBE 三个 FIFO 队列：每次发放名额先取 BODY；已执行的 crawler 不抢占。正文耗尽 CPU 的解析阶段在原 worker 执行，释放访问 permit 后继续，避免占着浏览器名额解析。

Crawler 等待最多 10 秒，BODY 等待最多 30 秒，且均受调用方剩余 deadline 截断；超时只返回 BUDGET_DEFERRED，不触发节点健康降级。保持严格正文优先；长期 crawler starvation 通过 wait_ms/延后指标暴露，按真实压力调整站点并发，不在首版另建公平调度系统。

原 Source request_permit 继续限定 source 的调用节奏，site permit 只限定受管网页操作；固定获取顺序 source → site，任何代码不能反向嵌套。一次 browser recipe/HTTP attempt 只获取一次 site permit，pipeline 和 parent broker 不重复加同一把限制。

## 9. 网站正文策略的具体拆分

### 9.1 一个管线，几个受控适配点

保留 `ArticlePipeline` 编排与 `choose_candidate`/正文质量门槛。增加 `content_enrichment/strategies/`，采用显式 factory catalog，根据 Registry 中的 `body.ref` 与参数实例化 adapter，不再在通用质量函数里散落 hostname 分支。

建议接口：

```python
class BodyStrategy(Protocol):
    def plan(self, target, parameters) -> BodyAccessPlan: ...
    def inspect(self, observation, title, parameters) -> Inspection: ...
    def next_targets(self, inspection) -> list[PublisherCandidate]: ...
    def browser_recipe(self, inspection, parameters) -> BrowserRecipeSpec | None: ...
    def public_api_request(self, target, parameters) -> ArticleApiRequest | None: ...
```

`BodyAccessPlan` 确定 access_order、auth requirement、reader permission、支持的 API/字幕路径。Adapter 返回候选与证据，最终 FULL/SHORT_FULL/UNAVAILABLE 仍由统一质量判定决定。站点配置不能通过把最小正文长度改成 0 来把摘要升格为全文。

简单 selector、等待条件、排除容器、展开按钮、路径模板、reader 模板起止参数可更新 Registry 后立即生效；复杂 API/signature/文章 identity 算法使用版本化可信 Python adapter，新增代码仍需测试与部署。首版不建设正文代码包上传、热执行与另一套认证平台。

当前 `quality.py` 保留通用 DOM/JSON-LD/trafilatura、身份与 completeness 判断；把站点专用候选生成迁入各 adapter。先用既有 fixture 对比迁移前后提取结果，再进行真实调优；同一逻辑不得在旧 if 分支和新 adapter 两处继续维护。

### 9.2 迁移与首批调优清单

| 网站/能力 | 首版具体做法 | 验收重点 |
| --- | --- | --- |
| Yahoo Finance | 注册 publisher link / canonical 解析、页面正文与摘要区分；公开 direct→browser→reader；列表/搜索继续 Yahoo recipe | 原站文章正文、第三方原文跳转、page_network_ncp 及真实新增 |
| Reuters | 编号 paragraph 正文 + browser-first；搜索沿用现有 query/offset 解析；初始优先 NL 候选但以实测决定 | 列表与正文同默认 Profile/egress；空结果和挑战页可区分 |
| Barron’s | 普通 article + live card 两类精确范围，认证 browser-first；DataDome 与缺授权分类 | 单篇、live coverage、标题身份、实际订阅可见内容 |
| WSJ | 迁移 paywall article 范围，检查可见性、截断与身份；认证 browser-first | 已登录仍付费墙、不同文章结构和同账号 Profile 重启 |
| Seeking Alpha | 迁移 content-container、文章 ID/标题、隐藏正文与有限登录恢复 | challenge、login、hidden body、正常正文四类分离 |
| MarketWatch | 建立独立 body adapter，真实 DOM 定位正文/订阅提示；不只继承 authenticated host 标记 | 新专项适配有效，完整正文不能混入推荐列表 |
| TheStreet | 迁移作者区截断；真实样本中按失败占比补正文容器/browser 策略 | 不因统一架构遗失原专项处理 |
| Finnhub wrapper | 迁移 meta/script 跳转 resolver | 最终域名重新治理、原发现 source 保留 |
| CNBC | 迁移官方字幕请求/覆盖检查；请求通过所属站点或声明的支持 host 访问 | 无字幕保持真实失败、不是正文短摘要 |
| 24/7 Wall St. | 迁移公开 WordPress API | 文章 ID/标题匹配、API URL 经过治理 |
| Fool / Chartmill / Benzinga reader 模板 | 迁移 reader 模板处理 | 不降低正文门槛；Benzinga 原生 provider article 独立保留 |
| Generic | 保留现行通用 extraction 和条件式路线 | 未注册新 publisher 可正常工作、站点故障隔离 |

六个重点网站至少准备每站两套 Profile+egress；认证所需 Profile 要分别验证 session，不能仅登记两行即称可用双组合。非重点旧适配同样迁移和回归；本轮失败样本排名靠前的公开网站再做有证据的小范围适配，避免把主工程变成无限网站专项开发。

### 9.3 Reader 的特殊边界

Reader 是第三方提取路线，不是同 Profile 的浏览器访问身份。只对公开且 policy 允许的文章调用，不带 cookie/账号内容；认证所需或已判断需订阅的文章禁止 reader。

访问 reader endpoint 的 429/403 更新独立 `reader:<host>` transport 冷却，不能封 publisher 的组合或把代理切换当作 reader 身份改变。统计标记 `transport=reader`，计入最终正文效果但从“直接站点访问成功率”中分开。reader 不能证明它上游访问 publisher 使用了本组合的出口 IP。

### 9.4 正文版本与 durable queue

新访问治理启用后使用 `body_v2.2`；`SharedContentExtractor.extract_version` 明确支持新版本。既有入队 `body_v2.1` 在短迁移窗口通过兼容实现完成，不修改其冻结输入；迁移时先停止新 intake、等待旧队列正常 drain，再切新 intake，最长等待现有 deadline，不要求丢弃队列。

`None/legacy` 与 native provider 路径保留原语义。回滚暂停新 intake，确保新版本 job 在兼容镜像完成后再切回旧版本，禁止让旧镜像处理不认识的 v2.2 job。网页风险失败只能在组合池处理完成后交给 queue 决定最终 retry；不能仍在 `PublicTransport.cooldowns[hostname]` 抢先退出。

## 10. 爬虫策略选择及与 Message Bus 的接入

### 10.1 引用现有能力，不复制版本管理

增加 `CrawlerStrategyRef` 两种取值：`builtin:<name>@<version>` 与 `crawler:<crawler_id>@active`。builtin 用于现有 Yahoo/Reuters 的稳定桥接代码；Crawler Plane ref 必须 resolve 到其已 promote 的版本，并在一次 execution 开始时固定 version/digest。

Owner 只存储与返回 crawler 引用，不读取/写入 Crawler Plane 资产库，也不反向调用 Message Bus。管理CLI在apply前调用现有Crawler Plane验证入口检查引用，执行时由parent bridge再次解析active版本并验证合同；记录验证时间与资产digest。没有可用资产时报告策略配置错误，不能暗中降generic执行不同逻辑。

Yahoo/Reuters 的原 Source adapter 保留 poll/map/coverage/参数职责；仅网页阶段通过 `SiteCrawlerBridge.run(site_id, PollContext)` 选择 Registry 中的 strategy。初始 builtin 路径复用当前 `yahoo_sources.capture_latest_news` 和 `reuters_search`，不强制把现有来源重新注册一遍。以后替换为 Crawler Plane asset，只改变 Site Strategy 的 ref，通过既有 create/live probe/certify/promote 完成代码发布。

用户已有 `crawler:<id>` Source 本身是显式选择，继续执行该资产；Registry 不任意把它替换成另一个 crawler，以免破坏 checkpoint。它的所有网页 HTTP/browser 操作仍按 URL 进入站点治理。Site Strategy 的 crawler ref 作用于站点可选择的 bridge 与新网站接入；文档和 CLI 必须说明这个选择优先级。

替换 crawler asset 时，先验证参数 schema、输出合同和 checkpoint schema；同 crawler ID 的兼容版本延续原 checkpoint。换 crawler ID 时保留旧 checkpoint 归档，新 ID 按原 bootstrap/recovery 流程建立 checkpoint，不伪造转换；不得静默推进漏抓窗口。

### 10.2 Parent broker 边界

`ParentNetworkSession._http/_browser` 改为调用 SiteAccessClient；worker 子进程仍没有直接网络权限。原 `ctx.http.get` 和 `ctx.browser.get` 接口兼容，响应增加可选 access diagnostics。

扩展 `ctx.browser.run(recipe_ref, parameters)` 为有界受支持浏览器能力，供需要页面 XHR/等待/点击的 crawler 使用；只允许执行已注册 recipe，不接收任意 JS 字符串。全新网站一般先用通用 get/声明式动作，复杂动作经可信 recipe 扩展。

RECORD cassette 记录实际 response 和 access provenance；REPLAY 完全不调用 Owner、不改真实组合健康、不探测出口。Secret headers 在 cassette 写盘前过滤；原 response body 大小限制继续生效。Live Probe 使用同服务，可显式指定测试组合并消费站点预算，生产 active 切换仅在正常执行或明确 promote 后发生。

### 10.3 Yahoo / Reuters 的精确改动

- 删除 Yahoo 页面路径 `_browser_retry_at` 的五分钟预留/冻结，用 Owner 的共享站点排队和组合 cooldown 替代；多个 ticker 同时失败不再各自轮流探测同一死组合。
- 删除 browser 429 对 `_page_api_retry_at` 的赋值与抑制 NCP 的联动。`YahooTransport` 对实际 NCP/API、RSS 响应产生的独立 circuit 保留。
- 同一 `finance.yahoo.com/xhr/ncp`：浏览器页面 recipe 内的请求属于受管网页身份；adapter 独立 NCP fallback 继续旧 transport。输出同时记 `acquisition_path` 和 `access_combination_id`，不凭 URL 混为一条路线。
- Reuters 公司名解析、日期精度、分页和 coverage 算法保留；只把网页访问与站点搜索 recipe 交给共同 Owner。真实空列表必须有页面结构证据。
- Source schema 不添加 Proxy/Profile 字段；`AdapterRegistry` 不给共享 httpx client 全局挂代理。Google wrapper 的正文 URL 解析属于正文治理，Google RSS acquisition 仍走原路径。

### 10.4 Deferred 的调度落点

现有 `optional_next_poll_hint` 只会延后正常 target_due，不能表达“5 秒后补试”。因此新增最小结构化 `acquisition_metadata.site_access_deferred`（reason、retry_at、tried/untried IDs）；仅网页 deferred 结果走 scheduler 的专用分支：保留 checkpoint、bootstrap/coverage 状态，`next_dispatch_at = max(now + 5s, retry_at)`，不重设业务 target cadence、不计成功或风控失败。

连续 deferred 每 binding 的补试间隔为 5/10/30 秒，上限 30 秒；成功或真正 acquisition 失败后回到既有 cadence。不得靠 metadata 伪造“窗口完整”；Sweep/gap recovery 同样返回未完成窗口，由原 owner 续跑，不推进 CLOSED/coverage。默认两组合和 45 秒预算下该分支是压力/资源异常边界，不是正常轮询机制。

Deferred计数与retry_at写现有poll-state的可选扩展字段，使用当前binding/run所有权更新；在`accept_poll_result`之前识别此分支，避免默认成功处理已经推进bootstrap/checkpoint。由parent可信代码构造typed deferred结果，worker提供的任意metadata不得直接操纵调度。`PollExecutionResult`增加可选`deferred_until/deferred_reason`，window_done=false、next_checkpoint为原checkpoint、error_code为空，明确区分暂缓与失败。

已复核`persistent_runtime_v2/bus_orchestration.py`：实时路径调用`scheduler._poll`，Sweep同样调用后根据error_code进入失败流程、根据window_done推进receipt。因此在Sweep收到typed deferred时必须先于error/checkpoint处理分支退出：保留page_cursor/page_coverages/job_ids，receipt仅追加deferred_until，复用`_yield_source`延后领取；不得将资源等待累计进三次失败预算，也不得把UNKNOWN追加到已覆盖页面集合。实时和Sweep测试均覆盖此分支。

## 11. 诊断、统计与不丢结果的落点

### 11.1 必须保留的字段

Access attempt：site/runtime key、host、purpose、strategy revision/ref、组合/Profile/egress、观测出口及时间、egress generation、transport、请求与返回时间、等待/网络耗时、状态码、风险分类、reason、是否切换、候选尝试序号、old/new combination、old/new generation。

正文终态：job_id、发现 source/ticker、输入与最终 publisher、经过的站点链、最终正文 adapter/version、最终组合、FULL/SHORT_FULL/UNAVAILABLE、内容/访问/身份分类、queue attempt_count、内部 access attempt_count、pipeline version。含多次跳转时，以最终目标归组，保留未知 publisher 桶，不把失败样本从统计中丢掉。

Crawler：poll_id、source/binding/ticker、crawler ref/resolved version、网页/NCP/RSS acquisition path、每路径 outcome、消息数量、coverage、checkpoint 是否推进、first_seen、延后原因及下次时间。空结果可以是成功，但不能充当“发现新消息”成功。

### 11.2 跨库结果可靠性

不能要求“写新 Registry 成功”才允许 Message Bus 发布，也不能只做一次易丢失的 fire-and-forget。对 Message Bus DB 增加小型 `site_strategy_result_outbox`（event_id、payload、created_at、delivered_at），只承担诊断投递：正文 `_finalize` 的 claim 校验后，在删除 enrichment job 的同一事务写终态 outbox；crawler poll 终态/状态保存的事务同样写诊断 outbox。

已有 accept_message 的幂等边界继续使用；失败 claim 不得写成功 outcome。若发布已完成但随后崩溃，原 job 重试依原机制去重，最后一次合法删除写同 job_id outcome。结果可以标识 delivered/deduped，不把 dedupe 当抓取失败。

`accept_message`也可能因admission返回FILTERED；outcome分别记录提取结果与publication decision，成功提取不等于成功发布。计数不能只从最终Raw表倒推，否则会遗漏被过滤/去重的补全样本。

既有 worker 小批量 flush outbox 到 Owner（默认每 10 秒、批量 100），按 job_id/poll_id 幂等消费；Owner 不可达时积压可观察，不阻塞业务发布。确认后保留 7 天再清理。这个单一 outbox 是为了验收统计可靠，不引入消息中间件或分布式事务。

### 11.3 查询与验收口径

CLI `stats --since ... --group-by site,strategy,combination,reason` 返回：

- 正文总体完成率 = FULL + SHORT_FULL / 全部需要网页补全的唯一终态 job；原生正文、SKIP 单列。
- 全部失败保留在总体分母，另外提供 access/auth/extraction 分层；不能剔除挑战和账号失败制造提高。
- 配对样本相同 URL/title/授权条件的前后结果；每站计数、成功率、错误迁移、P50/P95 耗时。
- crawler 网页路径成功率、任一路径 acquisition 成功率分别展示；fallback 次数和 NCP/RSS 承担比例。
- 晚抓分解：`first_seen - 精确发布时间`、poll scheduling lag、站点等待、fetch 时间、body queue time。只有 DATE 精度的 Reuters 等不进入秒级晚抓 P95；另用固定时段观察列表首次出现的时间作对照。
- Generic host Top-N 与失败占比，用来确定下一批人工维护网站；不自动注册新策略。

## 12. 管理命令与人工维护闭环

入口：`python -m doxagent.site_strategy.cli`。API 仅内部网络，读/执行 token 与管理 token 分开，来源为部署受限文件；不增加公共网站管理页。

| 命令族 | 必须支持 |
| --- | --- |
| `site list/show/validate/apply/export/history/rollback` | expected_revision 更新、脱敏导出、具体验证错误 |
| `resolve URL --purpose body/crawler` | 命中规则、site/ref、候选组合、无网络 dry-run |
| `egress list/import/render/probe/status` | import 管资源元数据，render 产部署 manifest；真正 reload 由宿主机脚本执行 |
| `combination add/disable/activate/status/probe` | 原子选择、说明禁用原因、查看 cooldown；强制 reset 是显式人工动作 |
| `profile create/status/login-open/verify/login-close` | 持久目录单 owner、维护状态、auth/entitlement 分开 |
| `credential import/update/status` | 隐藏输入、受限文件、普通输出只显示存在性/标签 |
| `stats / failures / events / generic-report` | 有界时间范围、分页与 JSON 输出 |
| `migrate seed / audit / reconcile-outcomes` | 幂等种子、域名覆盖/悬空引用/在途版本/诊断积压检查 |

增加一个新网站的标准流程：先选并验证现有 egress → 创建独立 Profile（如需登录先验证）→ 选择 generic 或已有 body adapter，填 selectors/API 参数 → 引用已通过现有 Crawler Plane 生命周期的资产（如需要新消息）→ validate/apply → 独立文章/列表 probe → 再通过原 Source/Binding 流程让 ticker 使用。以后替换网站策略不需编辑 ticker binding。

## 13. 部署、迁移与回滚

### 13.1 配置和文件

新增 `deploy/Dockerfile.site-access`、`deploy/site-access-entrypoint.sh`、`deploy/site-egress-config.py` 和 `docs/site-strategy-operations.md`。Compose 在 `docker-compose.v2-production.yml` 挂入服务、共享内部网络与独立数据卷，服务器资源覆盖加到 `deploy/docker-compose.server.yml`。

服务内部端口拟定 8011；只在需要登录维护时以 Compose override 将 VNC 端口映射到宿主机 loopback，经 SSH 隧道访问。服务 admin token 与 credential 文件不与 message-bus/content 容器共享；调用者只得到对应 worker client token。浏览器 profiles/Registry 由 Owner 单独挂载。

初始资源：browser page/process 上限见第 7 节；`shm_size=1g`、`pids_limit=1024`，加入现有 app slice，沿用已确定的共享父级弹性内存边界。读取 resource_safety 状态并限制 browser 扩张，不给每个站点独立分配常驻容器。

新增 settings 只放 bootstrap：`site_access_url`、client token file、Owner data root、owner enabled/legacy 切换、runtime 总容量。站点域名、节点选择、登录需求、selectors、cooldown 在 Registry 中维护。

### 13.2 实施顺序

1. 从冻结的当前版本、真实近期失败样本形成 baseline；记录 image、配置、节点观测、账号可见性。保存原 site-access 相关配置备份，不把历史 4/15、8/15 当本轮基线。
2. 增量安装 Owner、SQLite、Clash 固定 listeners；先 health/协议/固定出口验证，不改变业务 intake。一个模式开关控制新访问接入，不再增加多套永久兼容开关。
3. 注册六个重点站点与所有旧专项 adapter 的归属、generic 模板；用待审核种子 YAML 导入，不能启动时覆盖人工修改。节点按实测挑选，重点站点两个独立组合。
4. 建立/迁移登录 Profile。旧 Chrome 停止并确认无占用后，可备份其目录作迁移候选；宿主机 Chrome 到容器 Chromium 的版本/OS keyring 差异可能使 cookie 无法解密，不能承诺直接复制即可保留登录。失败则在新 Profile 正常登录。多个网站原来共享一个默认 Profile，不复制该整套身份到所有站点；逐站建立归属清晰的 Profile。旧目录原样保留供回滚。
5. 同时部署支持新模式的 message-bus/content worker（可先保持 legacy）。新 Owner 容器没有就绪前不启用；认证站点的两个组合分别 probe，没有可用授权的站点明确标 PARTIAL，不用空 Profile 冒充登录成功。
6. 短暂暂停新网页 intake，drain 在途旧版本，原子切新模式；新 crawler/body 两侧一起启用，共同 Profile 与 active 状态立即生效。恢复 intake 后立刻做定向真实样本、故障注入与自然消息观察。
7. 完成第 15 节后删除新模式中旧的匿名 CDP fallback、hostname cooldown/五分钟页面冻结。legacy 兼容实现仅保留一个发布回滚窗口，下一清理提交移除；旧环境项只给该兼容窗口或未纳入的 NCP/RSS 使用。

执行部署时仅重建本方案涉及的服务；当前 dirty worktree 中其他初始化/内存改动按各自交付状态处理，不能把整个工作区差异顺便上线。

### 13.3 回滚

先禁用新 intake，排空或由兼容版本完成 v2.2 job；切回旧访问模式与旧策略 snapshot，恢复原业务服务。保留新 Registry、profiles、events、outbox 与固定 listeners，不删除用户已完成的登录状态。原 7893 和节点规则仍在，回滚不需要重写其他 API 路由。

单站 adapter 配置问题优先回滚该 site revision，不全局回滚。节点失效优先禁用该组合、使用已验证备份。只有 Owner/协议或数据兼容性失效才做服务级回滚。任何回滚后的成功率按版本分别统计。

## 14. 可直接分解的开发工作包

各包依赖顺序为 A → B/C → D/E → F → G；这些是同一次完整交付的实现步骤，不是只交框架的分期承诺。

| 工作包 | 新增 / 修改位置 | 完成条件 |
| --- | --- | --- |
| A. Registry 和协议 | 新增 `site_strategy/schema.py, repository.py, resolver.py, client.py` | 七表迁移、CAS 更新、域名 resolver、generic、序列化合同测试通过 |
| B. 访问 Owner | 新增 `site_strategy/service.py, api.py, runtime.py, transport.py, budget.py, health.py` | 唯一 Profile owner、共享 FIFO、取消清理、风险状态机、持久重启、多组合访问闭环 |
| C. 出口和管理 | 新增 `site_strategy/egress.py, credentials.py, cli.py`、部署文件/seed/运维文档 | 固定入口可部署、出口变化可观察、CLI 可以完整维护一个网站 |
| D. 正文适配 | 修改 `content_enrichment/pipeline.py, extractor.py, browser.py, transport.py, quality.py, publishers.py, captions.py, service.py`；新增 `strategies/` | v2.2、全部旧适配迁移、跨站重新选身份、reader隔离、正确queue retry |
| E. 爬虫接入 | 修改 `crawler_plane/runtime.py, service.py, factory.py, worker_runtime.py, schema.py`；新增 `crawler_plane/site_bridge.py`；修改 `message_bus_v2/news_adapters.py, yahoo_sources.py, adapters.py, scheduler.py, schema.py`，对齐 `persistent_runtime_v2/bus_orchestration.py` | 所有网页 broker 走 Owner；Yahoo/Reuters bridge；NCP/RSS不被browser429联动；REPLAY零网络 |
| F. 诊断闭环 | 修改 `message_bus_v2/repository.py, service.py` 与 enrichment finalize；新增 `site_strategy/metrics.py` | 结果 outbox 与唯一终态统计、诊断可恢复、晚抓维度可查询 |
| G. 网站调优和交付 | seed 配置、六站 fixture / 真实受限 artifacts、迁移报告、changelog | 六站真实结果、故障注入、24h自然观察、前后配对报告和回滚演练 |

D/E 必须统一通过一个 transport client，不能各自实现 fallback。B 不引入通用分布式 lease 系统；单服务所有权、站点 lock、事务 generation 和原业务队列已经足够。

每个工作包代码变更后追加 `changelog`，保留用户现有变更。新增文件数以职责为边界；实现时可合并很短模块，不为表中每个概念单独建设服务。

## 15. 测试与真实验收

### 15.1 自动化测试矩阵

| 测试 | 必须证明 |
| --- | --- |
| Resolver | bare/www、IDNA、suffix边界、排除规则、冲突、CDN多站支持、未注册generic、Yahoo→Barron’s、Google/Finnhub跳转 |
| Registry | CAS冲突、回滚新revision、引用校验、disabled不降generic、活跃请求快照、人工策略不被seed覆盖 |
| Owner / Profile | 两客户端共用同一持久context；二次Owner拒绝；重启cookie/localStorage保留；取消页清理；idle回收不删目录；内存压力不无限等待 |
| Proxy | A/B固定不同listener；所有页面资源使用绑定入口；公共HTTP与浏览器出口一致；node缺失不隐式DIRECT；IP变化generation；重复出口提示 |
| Health | A429立即试B；A/B并发迟到结果不反切；挑战200可识别；404/DOM/auth/空结果/普通timeout不误伤；Reader失败不封publisher |
| Budget | BODY与CRAWLER竞争时BODY先发；并发2/500ms；无双重site permit；probe最低优先；排队取消与截止；跨站释放旧permit |
| 双fallback | 两组合失败才因风险走NCP；parser/runtime故障单独标记；NCP自身429只触发其原circuit；RSS成功不清NCP circuit；剩余组合未试不声称exhausted |
| 正文 | 旧适配fixture等价、标题/文章ID/可见性/完整性门槛、MarketWatch新增结构、认证禁止reader、字幕/native不退化 |
| Crawler资产 | active版本快照、checkpoint所有权、切换crawler ID显式bootstrap、REPLAY不影响真实健康、Live Probe参数合同 |
| Durable / 指标 | 旧/新pipeline版本、lease丢失不发布、内部fallback不增加queue attempt、诊断outbox重放幂等、终态与attempt分母分离 |
| 非网页回归 | IBKR/Benzinga native、Yahoo NCP/RSS、Google RSS、其他API client未走新proxy或新site budget |

有意义的本地整合测试使用测试网页服务器：设置cookie/返回429/重定向/空列表/延迟响应；两个独立 client 连接真实Owner与测试浏览器，检验共享身份和并发顺序，不能全部用mock自证。

沿用上一轮相关测试集合并按改动增加上述用例。上一轮 151 passed / 1 failed 仅是历史起点；实现时修正 `test_crawler_poll_lineage_and_checkpoint_ownership` 的固定日期夹具为受控时钟，再运行关联回归、Ruff与受影响模块类型检查。不将文档勘察当成新实现已经通过测试。

### 15.2 真实网站与出口验收

1. 六重点站点每站至少两套组合；测 listener可达、实测exit IP、公开列表/文章、认证状态、真实完整正文。每站至少人工检查5篇正常成功结果及5个失败/边界样本；特别核对“摘要、隐藏付费正文、live card串文”误报。
2. 在受控故障注入层模拟当前组合429/WAF，证明crawler和body后续请求共同转到下一组合；不主动打爆真实网站制造429。真实发生的风控另记，不用mock结果声称节点通过网站风控。
3. 以真实网站正常请求验证两套组合都可独立工作，不能只用gstatic延迟或IP echo证明站点可用。账号登录缺失、订阅权限不足明确列为未完成的站点验收项。
4. 正文与crawler跨进程同时访问同站点，使用Owner日志和实际Profile、listener路由证明共享；重启Owner后复测登录态和active/cooldown持久化。
5. 旧路径与新路径的同期配对样本单独保存，访问不写业务Raw，避免污染生产；访问频率纳入同站点预算，基线/新版本轮换顺序，避免先后顺序造成短期风控偏差。

### 15.3 正文效果目标

冻结一批至少300条近期可验证文章：六重点站点各至少30条，另外至少120条覆盖旧专项与generic；按近期失败类别和实际来源分布取样，不能只挑已知成功样本。另报告自然流量分布下的效果，避免人工均衡样本冒充生产加权成功率。

先跑同期旧版本基线，再跑新版本；记录同样URL/title、授权前提、时间、出口和版本。第一轮改进目标：总体完成率提高至少10个百分点，或相对失败率下降至少30%；两者是预设业务目标，不作为“已经达到”的声明。每站列出样本数、净新增成功、退化和原因；任一重点站点下降超过5个百分点必须逐条解释与处理，不能被其他站点平均收益掩盖。

对受控样本人工发现的伪FULL必须修复后重测。确实删除文章、缺授权仍计总体失败，同时单列可访问样本的 extraction成功率，以区分访问和解析改进。若工程合同通过但业务目标未达，交付报告标明“架构完成、效果验收未达”，继续处理主要access/extraction badcase，不通过改分母或放低质量阈值结项。

### 15.4 新消息及时性与自然流量

新模式运行24小时，覆盖当前实际ticker的Yahoo/Reuters；记录网页主路径、NCP/RSS fallback、连续失败、调度/预算等待和消息首次发现。正常压力下站点排队P95目标≤5秒；具备秒级发布时间的样本，采集延迟P95目标≤两个正常poll周期；原来已有站点列表发布时间延迟的情况单列解释。

按固定时间窗对比独立网页快照/来源列表，核对晚发现和遗漏消息，不能仅凭poll succeeded或固定20条结果证明覆盖。观察不足24小时或样本不足则效果验收保持未完成；这不阻止报告已证实的工程能力，但不能宣称业务目标完成。

### 15.5 对需求十项验收的映射

| 需求验收项 | 本方案落点 |
| --- | --- |
| 1 同站crawler/body共享 | 第3、7、8节；双client真实Owner测试 |
| 2 不同站点不同出口 | 第6节固定listener、实际出口观测 |
| 3 多组合 | 第4、6、8节；六站双组合清单 |
| 4 风控切换、解析不切 | 第8.1分类表与故障注入 |
| 5 单组合失败不冻结网站 | 第8.2原子切换与迟到报告测试 |
| 6 两层fallback | 第8.3、第10.3；穷尽/超时/策略故障分别证明 |
| 7 最终publisher归属 | 第5.2；跨域发送前重新选择身份 |
| 8 Generic继续可用 | 第5.3、第9.2 |
| 9 API/RSS/IBKR不受破坏 | 第1、第10节及专门回归 |
| 10 正文效果可比较 | 第11、第15.3配对与自然流量报告 |

## 16. 本方案最终交付物

- 可运行的 Site Strategy Registry、单Owner访问服务、固定Clash入口部署脚本及完整CLI。
- 全部现有正文网站适配迁移、六重点站点专项调优、Yahoo/Reuters网页采集接入；统一generic继续工作。
- 配置种子、节点/Profile/账号维护说明、逐文件变更、迁移/回滚runbook和changelog。
- 脱敏的域名覆盖报告、实际节点与出口清单、组合/身份状态、自动化验证结果、至少300条配对正文报告、24小时采集效果报告。

首版不包含自动登录agent、自动创建Site Strategy、复杂IP评分、多实例分布式调度、管理前端、Crawler Plane生命周期重建或全系统互联网代理。后续扩展入口已有明确归属，首版以访问实际改善和人工维护成本可控为完成标准。
