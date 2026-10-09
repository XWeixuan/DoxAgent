# Reuters / Investing 反复失败：诊断、修复与验收

日期：2026-10-06。时间除注明外采用 UTC（北京时间 +8 小时）。

## 1. 最终结论与边界

- **Investing.com ticker news 已恢复**：MU、INTC、BE、RKLB 的生产轮询均重新成功，失败计数归零。独立 canary 获取 10 条新闻；两篇普通新闻正文补全成功，分别为 2,999 / 2,001 字符。
- **Reuters 尚未完成恢复验收**：荷兰出口收到静态 403；德国备用和正式 Chrome/直连组合曾成功打开搜索页或获取新闻卡片，但随后再次收到 DataDome 401 challenge。因此没有把单次 200 算作恢复，也没有宣称当前 Reuters 正文补全已经可用。仍需人工处理 challenge，再复测多个真实轮询周期和正文。
- 发布的是 Site Access 三个文件的窄更新。Message Bus、Scheduler、Content Enrichment、Chrome Supervisor、Clash 和业务 Worker 均未重启；Source Maintenance Codex Worker 未部署、未启用。
- 六个原有正式 Chrome 的 instance_id 全部不变，已有 Profile / 登录态没有复制、重建或删除。

## 2. 排查依据

只读查询生产 `poll_states`、`access_events`、`site_runtime`、`body_outcomes`，并调用现有 Site Access owner API 做有限真实检查。没有使用第二个 CDP 控制器，没有读取或转移 Cookie，没有自动处理 CAPTCHA。

实际基线：

| 服务 | 排查时运行镜像 | 本次处理 |
| --- | --- | --- |
| Site Access | `doxagent-site-access:recurring-repair-20261004-r5` | 三文件 overlay 更新至 `source-recovery-20261006-r3` |
| Message Bus | `doxagent-v2:sweep-priority-20261006` | 不更换、不重启 |
| Scheduler | `doxagent-v2:event-maintenance-20261006` | 不更换、不重启 |
| Chrome Supervisor | `doxagent-chrome-supervisor:server`，运行约 11 天 | 不更换、不重启 |

远端 Site Access driver 当时 READY、活动页面约 2–3 个，没有出现历史修复中的共享 driver 死亡 / 全局队列堵死。两个源也并非同时从同一时间开始失败。因此，本次不能直接重复 10/04 的 driver 重连修复。

## 3. Investing：一次 challenge 被放大成超过一天的停摆

### 3.1 确定性的恢复链缺口

2026-10-05 08:26:23，`investing-external-nl` 收到 challenge，进入 60 秒 cooldown，截止 08:27:23。排查时已过期一天，但仍为 COOLDOWN、risk_strikes=1、manual_attention_required=false。

机制如下：

1. 浏览器组合的 cooldown 到期后不会直接变为 READY，必须先完成 HALF_OPEN 探测；HTTP_PUBLIC 的恢复逻辑不同。
2. 生产 Investing Registry 的 `access.probe_url=null`，维护循环直接跳过该站。
3. crawler / body override 都只允许 `investing-external-nl`，登记存在的其他组合不进入候选集。
4. 每分钟轮询照常执行，却在健康门控处失败，根本没有再次访问 Investing。

四个 ticker 累积约 1,500 次连续失败，不等于 Investing 拒绝了 1,500 次真实网页请求。绝大部分是本地门控重复报错。这是明确的程序 / 配置缺陷，不是随机网络扰动。

### 3.2 第二个确定性缺陷：把列表探测当正文抓取

补上既有公开验证 URL 后，又发现 Runtime 仅按 `purpose == CRAWLER` 选择列表选择器。PROBE 请求访问 `/equities/intel-corp-news` 时等待 `#article, [data-test="article-content"]`，而该页面是列表，应等待 `[data-test="article-item"]`。

结果：即使列表页面正常返回 200，探测也可能因等待不存在的正文节点而超时，被标记为 uncertain，继续 cooldown。普通正文模板、Pro 正文模板和列表模板必须分开判定，不能只凭请求 purpose 推断。

### 3.3 实际恢复

Registry 为 Investing 增加现有 Intel news 列表作为 probe_url；修正页面类型判断后，原有 `public-markets-nl` 正式 Chrome / 荷兰出口重新成功，不需要重建 Chrome 或登录。

09:45:45 起正常生产请求重新成功，之后四个 ticker 均恢复成功轮询。部署切换期间出现过一条短暂 ConnectError，后续轮询已自动恢复，不把该短暂错误混同于原来的长时间停摆。

直连正式 Chrome 的对照测试收到 Cloudflare 403（纯文本 `403`）；该失败 canary 组合已从 Investing 策略移除，未作为无效备用留在生产请求路径。

当前仍只保留已验证的 NL 主组合进入 Investing override。登记的 Managed 备用没有被证实可用，其中德国组合再次 challenge，需要人工处理；没有为“有 fallback”而启用一个已知失败的路径。

## 4. Reuters：真实拒绝访问与组合恢复治理叠加

### 4.1 时间线不支持“整个浏览器服务一直坏着”

生产事件显示，Reuters 从 10/05 至 10/06 08:40 之间仍有数千次成功访问；MU / BE / INTC 正常轮询最后成功约在 08:38–08:39。同期正文记录有 FULL 和 subscription_required 两类结果。当天约北京时间 16:40 后主组合开始持续被拒绝。

旧代码问题、订阅权限和新的站点拒绝必须区分，不能把此前所有 FULL / PARTIAL 记录统一解释为本轮故障。

### 4.2 主组合：出口通不代表目标网站放行

`reuters-1` 的荷兰出口观测 IP 为 `104.28.219.140`，ipify 探测 READY，但 Reuters 首页和搜索页均收到：

- HTTP 403；
- `server: AmazonS3`；
- `x-cache: Error from cloudfront`；
- 页面内容为 `Access Denied / Our apologies, the content you requested cannot be accessed`；
- 有 age、etag、last-modified 等静态错误页缓存头。

同一出口的浏览器请求和普通 HTTPS 请求都失败，其他出口返回的是不同的 401 JS/challenge 页面。**已确认这是目标网站访问层拒绝，而不是代理节点整体离线。**

仅凭这些头不能继续证明具体是地区、IP 信誉、WAF 规则还是源站策略。CloudFront 的 403 有多种原因，不能把 `Error from cloudfront` 当作“确定地理封禁”。参见 [AWS 官方 403 诊断](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/http-403-permission-denied.html)。

### 4.3 主备对照：不能以单次搜索页 200 验收

| 实测组合 | 结果 | 结论 |
| --- | --- | --- |
| NL / 原 Managed Profile | 首页、搜索静态 403 | 当前不放行 |
| DE1 / 原 Managed `reuters-2`，`205.198.126.113` | 一次真实搜索页 200、一次生产成功，随后 401 challenge | 短暂恢复，不稳定 |
| Direct / 已运行正式 Chrome + CDP，`43.163.67.97` | recipe 抓到真实 Micron 搜索卡片，随后再次 401 challenge | 正式 Chrome 不是无条件放行保证 |
| DE2 / 新独立 Managed Profile，`205.198.126.114` | 401 challenge | canary 禁用 |
| TR / 新独立 Managed Profile，`185.40.106.84` | 401 challenge | canary 禁用 |

曾计划将 `reuters-2` 按同一 Profile、同一出口切至 External Chrome 做对照，先保留恢复快照 `reuters-2-20261006T094200Z`。由于 Supervisor 的 6 个进程容量已满，未能启动此测试，身份配置随后已恢复 Managed。**没有拿这个未执行成功的测试作浏览器启动方式的因果证据。**

成功的 Direct 测试复用了原本运行的 `digitimes-1` 正式 Chrome / Profile，只增加 Reuters Site 映射；身份、出口、Locale、Timezone、进程均不变。这符合现有多 Site → 同一 Identity 的接口，但其 Profile 历史与原 Managed Profile 不同，因此不是严格隔离所有变量的实验。

### 4.4 为什么看起来“修好又坏”

- 组合第一次被 challenge 后可经历 cooldown / 探测；连续两次 challenge 后进入人工关注。这时即使 cooldown 时间已过，也不会自行放行。
- 普通首页 200 并不证明带关键词的搜索、后续页、正文和下一轮轮询都正常。
- 本轮实测已证实：同一组合可先返回搜索结果，再被 DataDome 拒绝；换正式 Chrome 也没有消除这一点。
- DataDome 的 JS / Device Check / CAPTCHA 与会话 Cookie 有关，不是一个可以靠放大 HTTP timeout 修复的网络错误。参见 [DataDome JS 文档](https://docs.datadome.co/docs/javascript-tag)及[响应页面文档](https://docs.datadome.co/docs/response-pages)。本项目仍使用固定身份、原生 Cookie、人工 challenge，不做 stealth、Cookie 注入或 CAPTCHA 自动解答。
- 当前最终聚合错误仍可能显示 `access_combinations_unavailable` / `access_resources_unavailable`，甚至 ACCESS_RATE_LIMIT / EGRESS_UNAVAILABLE；它们是组合耗尽的聚合标签，不是此次所有失败的真实 HTTP 原因。诊断需查看 attempts / access_events。本次增加恢复探测事件，保留真实 status / category / identity / exit 信息。

## 5. 已实现的程序修复

### `site_strategy/service.py`

1. 为旧策略缺省 probe_url 的情况，有限复用**已登记、同站点、public_access 类型**的 verification / maintenance URL；不猜登录地址，不探测其他站点。
2. 单个 egress / 组合探测异常隔离，不能阻止其他站点继续恢复；异常和取消释放 HALF_OPEN claim。
3. 启动时把旧 owner 遗留的 HALF_OPEN / probe_in_flight 恢复为有限 cooldown，保留真实人工关注标记。
4. 自动及管理员探测均使用 Site + Identity 联合并发 / 节奏治理，防止共享 Profile 上探测与业务任务互相抢占。
5. Profile 探测先 materialize Identity 引用；站点合法性以实际 Registry 组合关联为准，不再用旧 `Profile.site_id` 错误拒绝共享 Identity 下的其他 Site。
6. 增加 `COMBINATION_RECOVERY_PROBE` 事件，记录真实探测结果。
7. **人工验证真的成功后，同时修复组合健康记录**：解除 browser cooldown、risk strikes、manual_attention_required。此前只写 auth / session 状态，没有释放访问门控，是人工“已经维护”但爬虫仍不可用的另一条确定性机制。失败验证不解锁；公开验证实际命中维护入口时，才清理 crawler 的 purpose denial。

### `site_strategy/runtime.py`

按站点和实际 URL 识别 Investing / InvestorsHub news 列表，PROBE 使用列表节点；普通正文与 Pro 正文仍使用各自模板。错误页面在 commit 后等待 DOM，避免捕获未完成的空文档。

### `message_bus_v2/reuters_sources.py`

对于确认为 JS 验证文档的 401 / 403，最多等待 8 秒，让页面自行完成原生导航；只接受观察到的同一主 frame 的真实 2xx 文档响应。没有 reload 循环、伪造 200、点击 challenge 或改浏览器身份。静态 Access Denied 不进入此等待。

## 6. Reuters 最终 Registry 与人工维护

- 新正式 Chrome 组合：`reuters-native-direct` → `digitimes-1`，priority=0。
- 原 NL / DE / Direct Managed 组合保留，没有清掉其真实 challenge 状态来制造健康。
- 失败 DE2 / TR canary 组合及 Identity 均禁用，Profile 留存，未删除。
- probe_url、maintenance_url、verification_url 统一为实际入口：`https://www.reuters.com/site-search/?query=Micron&offset=0`。避免首页 / world 页面可访问而搜索仍被拒绝。

在 xRDP 的 **Source Login Maintenance / 消息源登录维护** 中刷新，选择 **Reuters** 的主身份 **`digitimes-1`（External Chrome / Direct）**，不是 DIGITIMES 站点行，打开页面人工处理 challenge / 必要时登录，再执行验证。该名字是已存在的 Profile 名，不意味着把 Reuters 正文当作 DIGITIMES 正文。

维护只隔离所选 Identity，不同时使用同一 Profile。完成后还需以 MU、INTC、BE、RKLB 的多个真实生产轮询及正文补全验收；人工页面正常不代表后续自动请求必然持续正常。

本轮没有擅自开启一个等待用户的维护会话。

已用 `doxagent-desktop` 账号通过原受限桥接程序执行只读 list：Reuters 的 `digitimes-1` 主身份可见，runtime_kind=external_chrome，manual_attention_required=true，vnc_ready=true，session=null。未替用户双击 GUI 或执行人工 challenge。

## 7. 验收、交付与回滚

- 本地定向回归：**68 passed / 1 skipped**；待发布三文件与生产基线组合加载的独立回归：**40 passed**。两组有重叠，不相加。
- Ruff 通过。跳过项为浏览器环境相关测试，真实访问另以 owner API / 生产 canary 检查，不冒充离线测试已覆盖所有浏览器行为。
- Investing：四个 ticker 正常轮询失败数归零；普通正文两篇成功。旧文章样本仅测试抓取 / 抽取，**未调用 bus 发布，未混入 30 分钟时效性窗口**。60 秒 polling 与闭市 sweep 规则未修改。
- Reuters：程序修复已上线，但 challenge 未完成人工验收；当前不能认定已恢复。
- 六个已有正式 Chrome 进程身份前后完全一致，详见 `eval/source_repair_20261006/before.json` / `after.json`。before 中部分旧 runtime 行是历史状态，真正活跃的六个在 after 中逐个查询 live status 复核。
- 当前 overlay：`/home/ubuntu/source-repair-20261006/compose.override.yml`；镜像 `doxagent-site-access:source-recovery-20261006-r3`。
- 文件 SHA-256 记录在 after.json；远端运行文件与本地 staging 文件一致。staging 从运行镜像基线抽取，仅重放本轮方法修改，**没有打包本地 Source Maintenance Worker hooks 或其他未提交业务改动**。
- 最终轮询状态、canary 正文样本与桌面维护入口收据见 `eval/source_repair_20261006/acceptance.json`。

回滚镜像：保留 `doxagent-site-access:source-recovery-20261006-base`，对应旧镜像 SHA `5dca9377ebddc63f6ad217f7e28c1441c632f9b7d63301172cec4b63835137ab`。使用原有 Compose 链、将最后 overlay 的 Site Access image 改回基线，然后仅 `up -d --no-deps --no-build v2-site-access`。不要重启 Supervisor，也不要误用旧 Bus overlay 覆盖今天的 sweep 修复。

Registry 原始 Site 配置见 before.json（Investing 当时已补 probe_url，原 revision=10 的 probe_url 为 null）；若回滚配置，先读取当前 revision，用 CAS apply，只回滚此次字段，不能覆盖之后的人工维护或新策略。Profile 快照恢复不是镜像回滚的必选步骤，禁止覆盖仍在运行的 Profile。

本地未整体提交 / push；远端没有拉取整个脏工作区。后续正式全量 build 前需要归并当前 overlay 的修复，不能把部署镜像记录当作代码已自动回流 main。

## 8. 尚不能宣称解决的事项

1. Reuters 的网站侧评分 / IP 信誉规则不可见；没有证据证明本轮具体规则，也不能保证人工 challenge 后永不再次触发。
2. 未启用已知失败的 Investing 备用。要形成可靠 fallback，仍须独立验证其列表、正文和连续轮询。
3. 仍有文档导航安全门控使用 CDP Fetch。它可能属于未来原生网络行为对照的变量，但本轮没有证据证明它就是拒绝原因，没有为了碰运气移除域名 / 导航约束。
4. outage 期间的历史新闻缺口没有补发；保持现有实时 / sweep 窗口。
5. 人工验证恢复门控的回归已通过，但本轮没有用户实际完成 Reuters challenge，因此该站端到端恢复仍待实测。
