# Calcalist、NRC、SDxCentral 长期失败修复

## 现场与诊断

2026-11-04 北京时间，生产只读基线与对比结果保存在 `eval/source_repair_20261104/`。三个来源不是同一种故障，也没有全局代理不可用证据。

* **Calcalist / Search**：三个 ONDS 搜索单元反复抛出 `calcalist_search_http_403`，外层只显示 RuntimeError；同机、同路由、新建适配器顺序搜索三个原始词均为 200。现有 CSE 短期令牌在内存缓存 900 秒，失效识别遗漏 `Invalid request token`，且未处理已缓存令牌的普通 403。历史失败响应正文未持久化，因此不能声称已经证实每次 403 的 Google 原始错误均相同；已排除当前搜索词本身失效和整体网络故障。
* **NRC / Event Notifications by Licensee**：真实日报 en58514 为 Power Reactor，使用 Facility=Cooper，没有 Licensee。旧解析器要求所有事件都有 Licensee，一个合法变体导致整份日报报 `nrc_event_licensee_missing`，之后表现为端点退避。仅跳过该事件会遗漏核电事件；直接将电站名当 Licensee 又无法正确执行 CEG 过滤。
* **SDxCentral / News**：直连、代理、标准库 TLS 均返回 Cloudflare 403；Managed Chromium 也未走出验证页。独立正式 Chrome 153 + CDP、DE 出口自动通过验证，得到真实新闻卡片及正文。因此不是把 403 当正常空列表，也不是修改 selector 就能解决的问题。

## 实施

1. Calcalist CSE 缓存缩为 300 秒；识别 Invalid request token；已缓存超过一分钟的令牌遭 403 时同路由重新 bootstrap 一次。新令牌仍失败即返回失败，不轮换出口、不无限重试。仅最终已解析的响应作为 listing 证明，端点元数据记录 `cse_token_refreshed`。
2. NRC 分别保留 Licensee 与 Facility。无 Licensee 的核电事件按 [NRC 官方运营方表](https://www.nrc.gov/facilities-safety/operating-reactors/list-power-reactor-units) 解析运营方，再应用现有 Licensee 别名配置。运营方表按日缓存并进入 checkpoint；辅助请求标为 article，避免污染 listing 传输证明。运营方无法确认时明确报错，不因正文提到 CEG 就分发。原事件 HTML、ID、版本 hash、发布时间及原生正文流程保留。
3. SDxCentral 采集和 BODY 均通过 Site Access，绑定独立 `source:sdxcentral_news`，主备分别为 `sdxcentral-news-main`、`sdxcentral-news-backup`，正式 Chrome 153、独立 Profile、相同环境、不同 DE 出口。备用冷启动，未经验收不宣称可接替。幂等配置入口：`scripts/ops/configure_sdxcentral_browser.py --sqlite <Registry>`。既有 Registry 修订不被覆盖。
4. Site Access 加入 SDxCentral 的真实 listing 契约校验，复用业务解析器；不以 HTTP 200 认证可用。Browser 错误继续携带现有 SiteAccessError 分类，避免重新包装为模糊端点退避。仍采用原 60 秒轮询与既有相关性分发。

## 验收与边界

本地初轮 134 项回归通过，覆盖三类来源解析、正文、CSE 有界刷新、NRC 运营方过滤、SDxCentral 身份配置及同 scope 的 CRAWLER/BODY 调用。修复前独立读取已得到 SDxCentral 28 条真实新闻和 3,442 字符 FULL 正文。生产发布及自然轮询结果见后续收尾记录。

NRC 当前三条事件分别属于 Cooper、Veterans Health Administration、Team Industrial Services；Cooper 的官方运营方为 Nebraska Public Power District，均不属于 CEG。正常零条不等于来源失败。此修复不回填超出既有时效窗口的旧消息，不承诺消除外站未来的风控变化，不将一次成功等同长期稳定。

## 生产收尾

运行代码 `35a7396e03c722d703ccb4a51084ef21488bef46` 已推送 main，并按标准精确 SHA 流程重建、发布 Collector、Content Enrichment、Site Access。Supervisor 容器仍为 `37881d9a4b92`，启动时间仍为 11-01 22:08 UTC；其他既有 Profile 未重建。三个角色六处相关模块哈希与提交内容一致。后续仅补部署配置脚本和验收文档，不需要因此重建业务容器。

完整自然观察窗 **UTC 2026-11-03 19:22:45—19:31:44**（北京时间 11-04 03:22:45—03:31:44）：

| 来源 | 去重物理 listing 成功 | 实际范围 | 观察窗内采集失败 |
| --- | ---: | --- | ---: |
| Calcalist | 30 | ONDS 三个搜索词各 9 次，尾页 3 次；已跨一次 300 秒令牌续期 | 0 |
| NRC | 9 | CEG；一次辅助运营方表读取，checkpoint 保留 55 个电站映射；没有符合 CEG 的当日报告 | 0 |
| SDxCentral | 9 | COHR / INTC / MU / NBIS / SNOW 各接纳 9 次同一共享采集，不能当 45 次网站访问 | 0 |

自然采集的三类来源最新单元错误均已清除。Calcalist 的尾页启停属于调度状态，不计为采集失败。抽样 BODY：Calcalist 希伯来文文章 **FULL / 4,012 字符**，NRC 原生 Cooper 事件 **FULL / 1,478 字符**（仅验证抽取、不向 CEG 发布），SDxCentral **FULL / 3,405 字符**。这些是正文能力验收，不能冒充观察窗内新文章的业务发布量。

SDxCentral 备用的第一次限定身份请求返回 ACCESS_EXHAUSTED，触发现有异步冷 canary；随后实际备用 listing 请求成功 200。BODY 首次也存在 canary/预算延期，保留失败回执；冷校验后最终限定备用的真实 BODY 请求为 **SUCCESS / 200 / FULL / 3,405 字符**。主身份恢复操作 DONE/ready=true；验收没有将临时延期写成全程成功。主备出口实测为两个不同 IP，地区记录均为 DE。维护验证改为 `public_access`，配置了一条已验证的公开正文 URL，不要求订阅鉴权。

相关回归 **134 + 65 = 199 项通过**，另在最终公开验证配置修改后重跑 4 项配置/接口测试通过。此为约九分钟观察结果，不是数日无人值守稳定性的证明。Calcalist 的旧 403 原始响应缺口仍保留，不能把本轮修复过的令牌缺陷等同于所有历史 403 的唯一已证实原因。
