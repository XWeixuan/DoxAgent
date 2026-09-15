# Yahoo 页面网络截取与 RSS 本地交付 / 远端无落库验证

日期：2026-09-15。基线：本地与远端 `c74826e7`。本次修改仅留在本地，未提交、部署、重建或重启生产服务。

## 结论

- RSS 已验证本地、远端可用；远端新 adapter 实际在 NCP 429 后切换 RSS，返回 18 条有效消息、0 个字段解析失败，耗时 2.207 秒。
- 在用户随后明确授权尝试页面 JS 主动 fetch 后，Playwright 获取能力已在本地真实验证成功：共享 BrowserRuntime 获取 20 条新闻，20/20 有摘要及精确 pubDate，包含浏览器启动总耗时 4.922 秒，页面获取阶段 3.078 秒。成功方法明确标记 `page_js_fetch`，不是原生自动 XHR。
- 远端 Chromium 直接访问新闻页、以及后续使用普通 Chrome UA / en-US 并从首页初始化会话均被 Edge HTTP 429 阻止；后一轮首页 429 即停止，不继续访问新闻页或 NCP。本地能力可用不等于远端可用。
- 因远端尚未通过验收，页面方案默认仍关闭。默认自动链路为 **NCP → RSS**；显式开启 `page_network_enabled: true` 后为 **浏览器获取 → NCP → RSS**。浏览器内部依次尝试原生网络响应、受限 hydration JSON、真实页面 JS 主动请求。
- Query1 / Query2 Search 与 Reader Search 退出自动轮询。原实现保留在 `poll_legacy_search`，仅显式诊断调用，不自动启用。

## 修改边界

1. 新增 `yahoo_sources.py`：页面 response 监听在 goto 前注册，严格匹配 Yahoo hostname、`/xhr/ncp`、`latestNews` 与请求 ticker。短暂观察普通滚动，再检查 SvelteKit 序列化的 latestNews 响应；无有效数据时在页面 JS 中调用 same-origin POST fetch，携带浏览器 cookie，使用现有 NCP JSON mapper。20 秒总截止、AbortController、清理监听与回调任务；metadata 分别标明 `native_network` / `ssr_hydration` / `page_js_fetch`，主动请求不会被 listener 误标为原生自动请求。
2. 复用 Crawler Plane 的 Playwright browser/context；每次仅新建并关闭页面，不新开 Chromium 进程。不修改 Reuters 提取、O1/O2/O3 编排或 skill。
3. 普通页面探测失败冷却 5 分钟；429 使用 5/15/30 分钟退避并尊重 Retry-After，成功才复位。进入等待前预约探测，避免多个 ticker 排队重复探测。页面导航 429 即停止浏览器内请求，并让 adapter 跳过 NCP 到 RSS，不立即对相同 API 再请求。
4. RSS 复用现有进程级 Chrome `curl_cffi` session、cookie jar、全局单并发及 stagger。NCP/API 与 RSS feed 的 circuit 分组：RSS 成功不解除 NCP 的 5/15/30 分钟退避，各 ticker 仍共享对应分组 circuit。
5. RSS 保留原生 GUID、链接、pubDate、description；摘要不冒充正文，缺失摘要保持缺失，非 RSS 文档报错。UUID 仅提供身份候选，不以字符串格式宣称验证了 URL 别名。
6. RSS 与浏览器获取窗口覆盖报告 `PARTIAL`；仅页面 JS 主动请求能准确记录 requested_count，普通 polling 使用 10–20、bootstrap / gap recovery 使用 100；原生请求 / hydration 数量由站点决定，RSS 也没有 snippetCount，不虚报请求数量。

## 验证证据

| 测试 | 结果 |
| --- | --- |
| 本地普通 httpx 请求旧 RSS | HTTP 404 / HTML，不能作为成功 |
| 本地 Chrome curl_cffi 请求旧 RSS | HTTP 200 / XML，18 条 |
| 本地真实 Playwright 页面网络监听 | 页面 200；多次滚动及两个新闻入口均未截获目标 NCP 响应 |
| 本地新 BrowserRuntime 浏览器获取 | `page_js_fetch`，20 条，20 条有摘要及精确发布时间；包含启动 4.922 秒 |
| 本地完整 adapter，禁用 HTTP/RSS 请求的断言探针 | Playwright 独立返回 20 条、0 failures，全部摘要/EXACT 时间/方法标记齐全；未调用 fallback |
| 远端 Chromium 新闻页及页面 JS 测试 | 页面和 API 均 Edge 429，无新闻，不视为成功 |
| 远端 Chrome UA / en-US / 首页会话初始化 | 首页即 Edge 429，无 Retry-After，停止后续请求 |
| 本地新 adapter 自动 polling | NCP 20 条，0 failures，0.578 秒，20 条均有摘要 |
| 远端直接执行本地 RSS 模块 | 18 条，0.241 秒；18/18 有标题、URL、摘要、可解析发布时间 |
| 远端执行本地新 adapter | NCP `YahooRateLimited` → RSS 18 条，0 failures，2.207 秒 |

远端测试通过 stdin 将本地 transport / sources / adapter 代码加载到独立 Python 进程内存中；没有替换容器文件，没有触发 Raw、Stream 或 Runtime 写入，没有使用生产 worker 的内存 session。远端现有 message-bus 容器测试前为运行状态，commit 与本地基线相同。

本次 RSS 18 条的最新发布时间为 `2026-09-15T07:48:52Z`，不是按返回顺序推断。单轮获取成功不证明 RSS 具备 60 秒发布新鲜度、完整覆盖或相同于 NCP 的条目集合；上线后仍需自然新增新闻验收。未添加本地 600 秒新闻结果缓存。

36 项定向测试通过，Ruff 通过。新增验收：SSR 无原生 XHR 时页面 JS 请求成功且明确标记来源、100 条请求参数、导航 429 在 fetch 前停止、页面 JS 429 不伪装为空结果、hydration JSON 不再请求。其余覆盖：原生 response 截取与 ticker 隔离、监听清理、超时；页面优先与冷却；页面 429 不立即请求 NCP；默认不启用未验证页面路径；NCP 429 后 RSS 成功且 API circuit 保持；RSS 文档类型、缺失摘要、原字段保留；原 NCP 数量、共享 session、并发/stagger、429 退避与保留的手动 Search 逻辑。仅执行相邻新闻源测试，不运行模型、维护或订单测试。

## 未出现自动 NCP 的归因

1. **不是 listener 注册晚**：监听代码在 goto 前执行，本次全量网络记录能捕获 i18n、quote、calendar 等其他 fetch / XHR；测试也验证了注册顺序。
2. **当前页面新闻由 SSR 提供**：原始 document 响应包含 12 个 `data-testid="storyitem"` 新闻卡片，导航/滚动无需重复请求 NCP。页面基于 SvelteKit，存在 `data-sveltekit-fetched` hydration 数据，但当前检查的页面没有 latestNews JSON 对应条目。
3. **未发现可替代新闻 JSON endpoint**：记录导航及滚动期间全部 fetch / XHR，未出现新的 ticker-news 请求；这是本次样本的观察，不是保证 Yahoo 永不改 endpoint。
4. **页面卡片不是完整字段替代物**：当前 card 没有正文/明确摘要，发布时间显示“2h ago”，无精确 datetime。未将这些粗粒度时间伪装成 EXACT，或用抓取时间替代新闻时间来发布。
5. **NCP 可在本地浏览器同源环境取得**：主动 fetch 确认 endpoint / JSON schema 仍有效。远端 Edge 429 对整个 finance 页面也生效，说明远端障碍已不只是自动 XHR 未触发；IP/出口信誉或其他环境限制是候选原因，未作已验证归因。

## 后续边界

RSS 可以作为 NCP 限流时的替代来源，但其新闻发布时间/覆盖不能由一轮成功推导。浏览器获取本地验收通过，远端出口 Edge 429 未解决，不能宣称生产可用，也不默认启用或进行立即重复请求。此次没有修改远端服务状态。
