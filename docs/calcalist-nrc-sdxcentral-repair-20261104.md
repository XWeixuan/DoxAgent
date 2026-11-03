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
