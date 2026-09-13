# V2 正文补全优化：本地实现与验收记录

日期：2026-09-12。依据 `v2_body_enrichment_optimization_plan_20260912.md` 开发。

**状态：核心本地实现与 1000 条隔离回放完成；完整方案尚未通过上线验收。** 新策略默认关闭，未部署、未回填 V1、未写业务消息库。Seeking Alpha 会员端到端验收仍被人工验证阻断；独立 50 条对照回放已完成；完整 gold 标注和服务器小流量验收尚未完成。不能将本记录理解为所有失败类型已恢复。

## 1. 已落地的实现

| 范围 | 实现与边界 |
| --- | --- |
| V2 独立内核 | `content_enrichment/quality.py`、`pipeline.py`、`transport.py`，通过 `SharedContentExtractor` 接入；V1 提取判定保留，仅给共享结果添加可选 diagnostics |
| 候选质量 | 比较 JSON-LD、明确正文容器、article、trafilatura；区分 FULL、SHORT_FULL、PARTIAL、UNAVAILABLE；检查标题证据、纯媒体、行情/列表、截断及访问墙 |
| 原文发现 | HTTP 逐跳、Finnhub 明确 meta refresh/location、Yahoo 明确原文链接；最多两次原文跳转，记录来源链，拒绝循环；不猜首个外部资源 |
| 正文边界 | Seeking Alpha 只取首个实际正文容器，排除摘要/作者介绍/披露拼接；Reader 保留 Coinbase 段落及表格，清除 GlobalData 推广和 newsletter/privacy 尾部；ChartMill 支持 Title header 模式 |
| 展开/浏览器 | 有限同页按钮展开、匿名浏览器渲染、每站单飞、全局 2 页；独立 publisher profile、cookie HTTP 优先，不完整时才进入渲染；浏览器故障不丢弃 provider fallback |
| 认证 | Seeking Alpha 常规 Login → Google 登录恢复；每次任务最多一次，恢复后重取原文章。账号选择、密码、MFA、人机验证进入人工出口；不自动购买、不绕过访问限制 |
| 队列 | Job 冻结 pipeline_version；新增 claim_token、20 秒续租、写 Raw 事务内校验所有权；过期任务不再发起请求；持续补位，有限窗口中按域名分散领取；单次瞬态重试 |
| HTTP | 单跳/任务双重预算，响应体上限，HTTP-date Retry-After、共享 429 cooldown；等待限流锁后再次检查 cooldown；验证初始地址与每个跳转地址 |
| 幂等 | 原始 provider message 用于 identity/source_item_key，保留 provider raw_hash；正文发现后的 URL 不改变原始去重身份；失去 lease 的旧 worker 不能回写 Raw 或删除新 claim |
| 兼容/回滚 | 原 status/succeeded/reason 继续存在，新增 outcome/page_kind/stage/reason_code/source_chain/body_source_url/candidate hashes/auth state 等；旧 job 无版本走 legacy，未知冻结版本保留 fallback |
| 运维 | 新增 `login --host --url` 命令；Compose 为 enrichment 独占挂载 `v2-enrichment-identities:/identities`，不挂载给其他业务服务；重要变更已追加 changelog |

本次将少量站点规则集中在 quality/browser 中，没有为每个域名复制一套提取管线。

## 2. 回放口径与证据

输入：`exports/doxagent_finnhub_body_incomplete_1000_20260911.csv`。

SHA256：`a47c613637b387cdfa974745c25504cca382a4b247ac0af9d4097dffd1e60894`。

CSV 没有原正文/summary，回放用空 body，优先 resolved_url。这是提取能力回放，不是原始 DB 状态、旧队列等待时间和 fallback 发布行为的完整复刻。fallback/去重/Raw 恢复另外通过临时数据库测试验证。

证据目录：`exports/body_enrichment_implementation_20260912/`。

- `replay/baseline.jsonl`：1000 条旧提取内核，同轮取得 1423 个 HTTP 响应。
- `replay/candidate-hybrid.jsonl`：复用旧响应，对缺少的原站/reader 路径仅补抓；新增 270 个 HTTP 响应。该文件保留开发中间结果，**不是最终判定**。
- `replay/candidate-final-v5.jsonl`：最终正文判定，复用 baseline＋补抓快照，无再次全量联网。
- `comparison.csv`：逐条历史标签、新旧结果、变更分类、核查状态、URL 和正文 hash。
- `comparison_summary.json`：转移矩阵、分层和代码 hash。早期 `candidate-offline` / `candidate-reviewed` / `candidate-final-v2` 等文件是保留的中间证据，不混入最终统计。
- 响应 `.txt.gz` 含公开页面快照，逐个校验解压后 SHA256；不保存认证 cookie、storage_state、密码或 OAuth token。
- `browser_live_runtime_installed.json` 是修正 SA 误判前的现场记录，**其中 SA 1617 字接受已被最终规则否决**，不能当会员成功证据。

最后的类型注解/格式整理、未知版本保护、lease 丢失处理不改变本轮正文结果；最终代码 hash 与执行回放时 hash 分别保留。既有回放未保存 job 等待与精确端到端延迟，不虚构 p50/p95、账号成功率或全量假全文率；工具已补充未来运行的 latency_ms 字段。

## 3. 最终结果

| 人群 | 条数 | 本轮旧内核接受 | 最终 V2 候选接受 |
| --- | ---: | ---: | ---: |
| 历史 failed | 777 | 369 | 366 |
| 历史 not_recorded | 223 | 151 | 139 |
| 合计 | 1000 | 520 | 505 |

**这些是程序接受数，不是准确率。** 最终 498 FULL、7 SHORT_FULL、255 PARTIAL、240 UNAVAILABLE。旧版没有等价的完整 outcome 分类，故转移矩阵左侧保留 ACCEPTED/FAILED，不伪造旧 SHORT_FULL/PARTIAL。

| 转移 | FULL | SHORT_FULL | PARTIAL | UNAVAILABLE |
| --- | ---: | ---: | ---: | ---: |
| 旧版 ACCEPTED | 493 | 3 | 5 | 19 |
| 旧版 FAILED | 5 | 4 | 250 | 221 |

496 条共同接受，471 条共同拒绝，9 条新增接受，24 条新增拒绝。

新增接受包括 5 条 GlobalData 旗下站点的 reader 正文和 4 条 Yahoo/GuruFocus 短正文。已逐项检查对应快照的正文边界，去掉 Cookie/推广尾巴、保留文章末段；不是仅按长度认定恢复。9/480 是本轮旧内核失败集合中的新增接受比例，不能外推至历史 1000 条或其他账号权益。

24 条新拒绝中，18 条属于已确认错误内容类型：10 条 Benzinga quote、5 条 headline-only 推广、3 条 Seeking Alpha 截断正文＋介绍/披露。另 3 条标题/版本不匹配、2 条原站 401、1 条 challenge 保留核查，不能一律包装成“纠正误判”。USA Today 的替代标题、ChartMill reader 标题，以及 CNBC 推荐入口误识别在核查后已修复。

237 条发现了原文链，但本轮匿名条件下**没有一条最终原站全文被接受**。发现原文不等于取得权益，这一主体失败来源尚未因本次代码改动而消失。

| 最终未接受原因 | 条数 | 后续动作 |
| --- | ---: | --- |
| http_401 | 178 | 核验对应原站账号/权益；不继续无凭据重试 |
| subscription_required | 112 | 有权益 profile 实测；目前不伪装已取得全文 |
| challenge_required | 83 | 人工认证或站点访问条件确认，禁止循环触发 |
| unsupported_media | 50 | 保留原消息；需要合法 transcript 才走正文路径 |
| publisher_identity_mismatch | 21 | 核对滚动更新文章、原文版本和目标链接 |
| empty_extract | 18 | 分站保留证据；Moby/动态站点仍需 adapter 现场验证 |
| http_403 | 13 | 区分网络、站点拦截及授权条件 |
| non_article_target | 11 | quote/listing 不补成新闻全文 |
| headline_only_source | 7 | 使用 provider 原始标题/summary，不取推广段落 |
| http_404 | 1 | 核对失效 URL |
| offline_response_missing | 1 | 补抓时 timeout，没有可复用响应；明确未取得 fixture，不称网站永久不可用 |

## 4. 实测与测试

- 本轮相关 Message Bus 20 项已验证；其中旧测试用单参数 record_raw 替身，新增可选 guard 参数曾导致兼容失败，已改成仅 enrichment claim 存在时传入，失败项复测通过。
- 最新正文补全专项：47 passed，覆盖期限、重试、全局并发、原身份去重、陈旧 lease 回写、域名公平、原文链、短全文/长截断/行情/推广、Reader 广告、SA 正文边界、浏览器会话恢复和人工出口。
- Ruff：涉及的补全、Message Bus、settings、replay 与新测试通过；mypy：content_enrichment 10 个模块通过。不是全仓库测试声明。
- 本机 Playwright 1.62.0 缺少 chromium 1234 运行时，已用官方安装命令补齐后重测。TheStreet 为 challenge；Moby 渲染未获得可靠目标文章身份；InvestorsHub 返回 4318 字正文。只做少量现场验证，没有把所有页面默认浏览器化。
- Seeking Alpha 的匿名 HTTP 曾返回 1617 字“正文”，现场审阅确认包括摘要、截断 thesis 和披露，被修复后的 SA 容器规则拒绝；未将其计入会员成功。
- 用户 Chrome 的常规 Google 登录入口已打开，但停在 Seeking Alpha `/auth/gplus` 的 HUMAN / “按住”验证，未进入 Google 账号选择完成态。尚无账号有效、过期恢复、权益不足和重启复用的真实会员结果。


- 独立对照：`controls_with_article_titles.csv` 共 50 个与原测试集不重复的 URL，覆盖 12 个域名，在提取结果出现前按域名轮转抽样。初始链接文字有些是正文中的短语，已用捕获页面的文章标题校准；原链接文字保留在 `control_title_evidence.json`，新旧内核均在同一标题和快照下重放。`controls/baseline-final.jsonl` 和 `controls/candidate-clean-boundary.jsonl` 均接受 48 条，无旧成功变为新失败；2 条共同未接受（内容不足、纯媒体）。没有按新规则结果筛掉失败对照。标题身份的历史变化仍由原 1000 条测试，不以此次标题校准证明身份判定准确率。
- 对照发现并修复公开 429 后 reader 回退、Fool 的 Title header 正文模式、MarketBeat/普通 HTML 的 newsletter 和推荐尾部；`controls/comparison_summary.json` 保留口径。50 条尾段全部检查，重点站点核查正文起止；尚不等于独立全文 gold 集。

## 5. 运行与回滚

环境配置示例（不要把账号内容放进这些变量）：

```text
DOXAGENT_CONTENT_ENRICHMENT_PIPELINE_ENABLED=true
DOXAGENT_CONTENT_ENRICHMENT_BROWSER_ENABLED=true
DOXAGENT_CONTENT_ENRICHMENT_AUTHENTICATED_HOSTS=seekingalpha.com
DOXAGENT_CONTENT_ENRICHMENT_IDENTITY_DIR=/identities
DOXAGENT_CONTENT_ENRICHMENT_DISABLED_HOSTS=
```

总开关和 browser 默认 false。只有通过对应验收的环境才启用。disabled_hosts 走既有 legacy；回滚关闭总开关不自动重写已发布正文。单个来源跳过仍遵循既有 source 配置。

初次授权需停掉持有同一个 profile 的 worker，再由操作员在有可见浏览器的环境执行：

```text
python -m doxagent.content_enrichment.cli login --host seekingalpha.com --url https://seekingalpha.com/article/4942766-semiconductor-etfs-the-good-the-bad-and-the-ugly
```

正常完成登录后按 Enter，状态回到 UNVERIFIED；重启 worker，由文章实际提取判定有效性。不要并发打开同一个 profile。用户主 Chrome 的登录不等于服务器独立 profile 已授权，未复制用户浏览器凭据。

本机 DNS 代理返回 198.18/15 虚拟地址，现场测试显式用了 `trusted_proxy_dns=True`。对应环境变量 `DOXAGENT_CONTENT_ENRICHMENT_TRUSTED_PROXY_DNS` 默认 false；仅受信本地 DNS 代理环境可开启。即使开启，也不接受字面私网地址或其他私有 DNS 结果。逐跳 DNS 检查不能替代生产环境出口网络隔离。

复现最终正文回放：

```text
python scripts/replay_content_enrichment.py --input exports/doxagent_finnhub_body_incomplete_1000_20260911.csv --output exports/body_enrichment_implementation_20260912/replay --mode candidate-offline --name candidate-final-v5 --include-supplements
```

同名运行断点续跑；更改判定规则时使用新 name，避免混用版本。baseline 缺失正文/summary 的限制始终保留。

## 6. 尚未闭合的验收项

1. Seeking Alpha 真实会员端到端及有效/过期/撤销/权益不足/人工验证/重启生命周期；当前 adapter 为 experimental。
2. 独立 50 条对照已完成，48 条既有成功全部保留、另 2 条原本为内容不足/纯媒体；做了全文边界抽查及全部尾段审阅，但不冒充逐字核准的完整 gold。全量 gold、假全文率、短文误拒率和数值上线目标尚未签定。
3. Moby 正文加载接口/语义 DOM、TheStreet 通过正常访问条件后的全文边界仍需验证；通用浏览器支持不代表这些站点恢复完成。
4. 服务器身份初始化、小流量运行、实际卷权限/资源和重启验收未执行。Compose 结构准备完成不等于生产验收。
5. DNS 重绑定的出口层保障、浏览器 HTTP 响应及整体浏览器内存的部署上限需在生产验证；本次没有声称解决所有网络安全边界。

因此可以审查和继续测试本地开发结果，**不能宣布整个开发方案全部验收完成，也不能默认打开生产开关**。
