# 免费新闻站点抓取修复与实测（2026-09-13）

## 结论

用户指出的免费访问情况成立：Chrome 界面中，三个站点各检查一篇同 URL 历史失败文章，都可直接阅读到结尾。TheStreet 显示 Sign in、Barchart 显示 LOGIN，没有执行登录。不能从这些反爬响应推导文章必须购买订阅。

本轮打通 Barchart 的本地匿名有界面 Chrome 补全链路，连续三个历史样本成功。TheStreet 与 24/7 Wall St. 的独立自动化浏览器仍遇到反爬检查，未宣称自动抓取成功。没有部署或修改生产数据。

## 真实原因与证据

| 站点 | 历史样本数 | 抓取响应 | 原代码问题 | 本轮结果 |
| --- | ---: | --- | --- | --- |
| TheStreet | 28 | HTTP 403，DataDome bootstrap，提示开启 JavaScript、关闭广告拦截 | 直接响应未准确识别挑战，最终依赖 reader 的挑战提示才归因 | 直接响应即可识别 challenge_required；用户 Chrome 可见全文，独立浏览器仍被拒绝 |
| 24/7 Wall St. | 13 | HTTP 403，Just a moment，Cloudflare challenge-platform 与 _cf_chl_opt | 最终只保留 http_403；初始渲染页可被判为 incomplete_extract | 识别 challenge_required；用户 Chrome 可见全文，独立浏览器仍被拒绝 |
| Barchart | 9 | HTTP 202，JavaScript is disabled，awsWafCookieDomainList / gokuProps | 当成正常文章进行标题匹配，产生 publisher_identity_mismatch，不会触发浏览器 | 识别 render_required，进入浏览器并等待正文；3 条真实管线测试成功 |

50 条原始响应快照均重新执行直接页面分类，得到 TheStreet 28 challenge_required、24/7 Wall St. 13 challenge_required、Barchart 9 render_required。此项只验证诊断分类，不代表 50 条全文均已抓取。

## 实现

- `quality.py`：在身份匹配前区分 AWS WAF JavaScript 引导页、Cloudflare 与 DataDome 检查页。组合检查页特征，并排除已有文章段落的页面，避免仅因正常文章包含安全脚本就判为挑战。
- `browser.py`：WAF 引导页即使已有很长导航文字，也等待文章节点出现，等待上限 8 秒并继续受任务 deadline 约束；不以全页长度作为正文加载成功证据。登录/订阅判定复用文章范围检查，避免正文外订阅广告直接清空正文。
- `pipeline.py`：挑战的后续动作改为 review_browser_challenge，JavaScript 引导页使用 enable_or_review_browser_rendering，不再引导匿名反爬错误去配置会员账号。
- `settings.py`、`extractor.py`、`cli.py`：支持显式选择本机 Chrome 和有界面模式，默认仍是原有无界面运行方式。不自动启动有界面模式，不把三个域名配置为会员站点。

本地已验证配置：

```text
DOXAGENT_CONTENT_ENRICHMENT_PIPELINE_ENABLED=true
DOXAGENT_CONTENT_ENRICHMENT_BROWSER_ENABLED=true
DOXAGENT_CONTENT_ENRICHMENT_BROWSER_CHANNEL=chrome
DOXAGENT_CONTENT_ENRICHMENT_BROWSER_HEADLESS=false
```

实测通过 SharedContentExtractor 的等价构造参数执行，不读取或写入业务队列。当前机器使用代理 DNS，测试显式开启既有 trusted_proxy_dns 参数；这不是通用生产默认值。以上配置要求安装 Chrome 且具有图形显示环境，不能据此认为无显示环境的服务器已具备运行条件。

## Barchart 端到端结果

使用独立新建匿名浏览器上下文，不导入个人 Chrome 登录态，不配置 authenticated_hosts。原 CSV 标题用于最终身份匹配。三个 URL 从先前回放中已确认的 Finnhub 跳转目标取得，本轮从发布站 URL 开始，不重复测 Finnhub 重定向。

| Article ID | 正文字数（字符） | 方法 | 结果 |
| --- | ---: | --- | --- |
| 4498322 | 4965 | browser_article_dom | FULL |
| 4493944 | 5540 | browser_article_dom | FULL |
| 4489575 | 5138 | browser_article_dom | FULL |

第一篇与 Chrome 可见正文的首尾及结论对照；另外两篇检查所抽取文本的首尾，未建立逐段人工金标。输出正文不包含 Barchart 页尾订阅模块和披露尾部。HTTP 初次导航可能返回 202，随后页面完成加载；证据中保留初始导航状态，没有伪改为 200。

初步环境对比中，默认 headless Chromium 和新 headless 模式受到拒绝；独立有界面 Chrome 成功。差异支持采用有界面 Chrome 作为本地可选路径，但不足以证明单一指纹字段就是根因，也不能保证跨网络、跨时段稳定成功。

## 验证与剩余问题

- 正文管线与浏览器专项测试合计 43 passed；相关 Ruff 通过；正文补全模块 mypy 10 files 通过。
- 新增覆盖：三类检查页不误报标题错误/付费墙、正常文章含安全脚本不被误拒、202 进入浏览器、长引导页等待正文、侧边栏订阅广告不误报正文付费墙。原有真实登录墙及订阅墙回归仍通过。
- TheStreet 和 24/7 Wall St. 尚未恢复独立自动化抓取。已分别验证普通 Chrome 免费可读与独立浏览器被拒绝的差异；延长等待和换为有界面 Chrome 均未使此次样本恢复。下一步应评估受控普通浏览器的匿名持久会话或站点提供的公开分发渠道，并独立验证稳定性；不应把“配置付费账号”作为解决方案。
- 未重跑 1000 条全量或改写旧回放统计；未启用生产实验开关。

## 证据文件

- `exports/free_publishers_20260913/targeted_snapshot_diagnosis.json`：50 条历史快照分类对照。
- `exports/free_publishers_20260913/barchart_pipeline_live.jsonl`：三个真实管线结果、尝试链、正文哈希。
- `exports/free_publishers_20260913/*.body.txt`：三篇抽取文本。
- `exports/free_publishers_20260913/code_manifest.json`：本轮核心文件哈希。
- `exports/free_publishers_20260913/*.html`、`exports/free_publishers_chrome_20260913/*.html`：独立匿名浏览器/HTTP 诊断快照。未导出个人 Chrome cookie 或账号资料。
- `exports/free_publishers_pipeline_probe.py`：本轮隔离测试入口（写测试文件，不访问业务 DB）。
