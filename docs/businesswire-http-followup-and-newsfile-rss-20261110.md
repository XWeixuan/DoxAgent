# Business Wire HTTP 重试与 Newsfile 保持 RSS（2026-11-10）

本轮遵循 HTTP 优先、必要时 Playwright 再 CDP 的顺序。**已找到可工作的低成本 HTTP 路径，因此未启动或部署浏览器。没有使用任何 sitemap 作为主源、兜底或平替。** 五源仍按既有 Massive Distribution 测试隔离运行，不增加正式订阅或向 Message Bus 下游发布。

## Business Wire

- 普通 `GET /newsroom` 仍403。只读 `POST /newsroom?page=N` 成功；增加网站 Next.js 页面导航采用的 `RSC: 1` 头，可直接取得 `text/x-component` 结构化服务端列表，约346KB，比完整HTML约626KB更小。
- 使用不带 `language` 的全语言列表，避免把原全语言RSS静默缩成英文。响应 `Cache-Control: max-age=0, no-cache, no-store`，没有Age、ETag、Last-Modified。**这些头不等于零发布/索引延迟**，另做新稿与原RSS对照。
- 前端 newsroom JavaScript 的筛选/分页实际用路由参数更新；可读数据在RSC的`news.items`中，包含官方`newsItemId/language/releaseDate/headline/teaser`。没有调用保存搜索、邮件设置或登录的Server Actions，也不需要维护构建版本相关的action hash。
- `page`已验证有效；limit/pageSize/rows=100均仍10条，未把它们当作扩窗手段。默认只请求第一页，有突发或恢复需要时按需翻页，最多8页、HTTP总预算12秒；页码未实际变化、页面结构/缓存异常会明确失败并保留原RSS兜底。
- URL按网站前端公开的slug算法生成，并统一为原RSS的尾斜杠形式；RSS/HTTP只保留稳定标题为内容版本线索，将列表摘要保留在原始证据中，避免两路摘要格式差异制造重复BODY任务。正文抓取方式、质量判定、测试BODY预算不变。
- 原Google News sitemap仅做过诊断，存在数分钟缓存；用户补充要求后已明确排除，**生产代码没有加入任何sitemap路径**。已有Bloomberg自身的原sitemap配置不属于本轮修改范围。

生产网络内试跑新适配器：首轮3页、14条近30分钟候选、约2.683秒；重复轮1页、0增量、约0.520秒。该试跑尚不能证明长期稳定或所有新闻已被及时发现，周期性观察另记。

## Newsfile

将 `tmx_newsfile_massive` 明确配置为 `discovery_mode=rss`、kind=api，保留原官方RSS、30秒周期、10个测试Ticker、正文方式与隔离。正常RSS读取不再冒充“HTTP失败后的兜底”，不再生成恒定的HTTP失败状态。

初始化默认值与既有 `configure_newswire_http.py` 同步遵循这一选择，避免以后重跑配置脚本又把Newsfile改回受阻爬虫。其他源的配置保持不动。试跑RSS首轮8条、约1.145秒，重复轮0增量、约0.245秒。

## 验证与发布

相关回归覆盖真实BW RSC数据、全语言、精确时区、官方URL/原RSS文章键和BODY版本签名一致、请求头与只读POST、无效翻页失败且保留首屏、Newsfile不调用爬虫/浏览器、配置幂等及原隔离路径。

后续追加实际回归计数、生产版本、周期性观察和新稿对照。观察不足时明确保留限制，不以HTTP200或响应速度替代时效验收。
