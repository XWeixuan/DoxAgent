# V2 正文补全试跑 badcase 排查与修复（2026-09-15）

## 本轮结论与交付状态

按用户要求先执行第一项：排查免费/现有来源理应可获取的正文，完成局部代码修复及真实样本验证。第二项 Reuters、Barron’s、WSJ、MarketWatch 远端账号添加与测试暂不执行；用户将在远端正常登录后继续。

**代码已在本地完成，远端只执行了隔离测试；未提交、部署、修改生产开关或重放历史业务消息。** 隔离测试将本轮模块放在运行容器 `/tmp` 下，通过独立进程加载，不替换已部署源码，不调用生产接收/发布入口。工作区其他 Yahoo 采集等未提交修改不属于本轮交付。

最重要的运行原因：CSV 的 405 条记录中，402 条 `pipeline_version=legacy`、3 条 `legacy_unversioned`。检查远端正文 worker 的白名单环境变量时，V2 pipeline/browser 开关均未设置，默认关闭；因此这批试跑没有执行已有 `body_v2.1` 链路，不能以其失败率评估 V2 抽取器。`message_bus_v2.factory` 在开关关闭时将新任务 pipeline version 固定为 `None`；开启后新任务才固定为 `body_v2.1`。不自动改变旧任务冻结版本。

## 样本口径

输入：`exports/MU_published_body_enrichment_failed_72h_url_title_dedup_20260915.csv`，配合 `exports/mu_enrichment_20260915_support/remote_stream_snapshot.json` 中原始正文及 payload 排查；没有用 CSV 截断 excerpt 代替原始正文。

- 405 条 URL/标题去重代表记录。
- 149 条按用户口径排除：`deadline_exceeded`、`http_429` 或 Investors 域名。
- 余下 256 条逐条登记在 `exports/body_trial_20260915/badcase_triage_405.csv`。
- 原导出 1,219 条失败发布记录，URL 去重到 432，再标题去重到 405。IBKR providers 通用页面曾合并 231 次观察、161 个标题；MarketWatch newsviewer 合并 23 次观察、17 个标题。因此 405 不是严格文章级全集，不能据此计算线上文章成功率，也不能把修复代表记录数外推为恢复发布数。

| 256 条中的分组 | 条数 | 验证含义 |
| --- | ---: | --- |
| 原生 API 正文严格匹配 | 6 | 当前真实 Raw 输入满足新规则；Hub 测试验证零网络接收 |
| 隔离管线真实抓取成功 | 13 | 5 条普通文章 + 8 条 CNBC 官方字幕；非生产落库验收 |
| 真实响应解析验证 | 3 | Benzinga 1、Yahoo 2；只保证保存响应的解析结果 |
| 需远端会员身份验证 | 50 | Seeking Alpha 44，Reuters 3，Barron’s 3；MarketWatch 本批代表记录已有 IBKR 原生正文 |
| Yahoo 原站/授权待逐篇验证 | 21 | source_summary_only；其中已实证 MT Newswires 存在正文内订阅墙，不能统一认定免费全文 |
| 官方字幕为空 | 1 | 保持 unsupported_media |
| 其他免费站点访问/运行路径待复测 | 162 | 代表样本仍有 WAF、HTTP 拒绝、地区限制或 reader 拒绝；未逐篇实测全部 162 条 |

前三组合计 22 个不同代表记录具有恢复或可接收证据，**不是 22/256 的线上恢复率**。部分真实抓取在主机重启前成功，重试不一定保持成功。

## 原因还原与已实现修复

### 1. IBKR/Benzinga 原生全文被当作待爬取短正文

旧全文判断偏向长度和网页抓取：短的授权简讯、通用链接、正文里的 Micron IR 风险因素链接都可能触发 HTTP 补全，最终以网页 401/403/404 覆盖“正文其实已存在”的事实。

新增 `content_enrichment/native.py`，只认可两种显式来源：

- IBKR：provider=ibkr、articleId 存在、articleType=0、articleText 为文本。
- Benzinga：provider=benzinga、id 存在、body 为文本。

必须去 HTML/实体并归一化后与当前 message.body 精确相同，至少 120 字符，排除显式挑战/订阅提示与截断结尾；摘要、任意长文本、IBKR 非文本附件均不接受。真实 CSV 全部 405 条中有 24 条满足规则，其中本轮未排除范围内有 6 条：2 条 Benzinga、4 条 IBKR。完整 CSV 中其他 18 条属于用户排除的 Investors 范围，单列而不计入本轮 6 条。

Hub 在通用 URL 跳过与网络补全前接收经验证的原生正文，记录 native_provider_article / provider_article_api、FULL 或 SHORT_FULL 与候选 hash；保留原 provider identity、URL、Raw payload 及领取 token 校验。无需为已存在的 API 正文再消耗网络预算。源级 SKIP 仍优先遵守，不改消息发布/同稿合并机制。

### 2. 免费站点普通 Chrome 路径与独立浏览器结果不同

TheStreet、Barchart 的正文外登录/订阅入口不表示正文受限。本轮在远端已有普通 Chrome 上，通过 CDP 自建页面进行了匿名隔离实测：TheStreet、Barchart、Investment Monitor、Fool、Yahoo 各 1 条抓到正文。普通 Chrome 与独立无界面 Chromium 的差异是已验证现象；不能据此断言仅 TLS 指纹造成全部 403。

新增 `DOXAGENT_CONTENT_ENRICHMENT_BROWSER_CDP_URL`，接入已有 operator Chrome context，仅关闭本轮自己创建的页面；关闭 worker 只断开 Playwright 连接，不关闭用户 Chrome/context。保留目标及子请求公共地址校验、任务 deadline、host 锁与并发上限；CDP 页面保留正常图片/字体请求，独立浏览器仍保持原资源限制。断连后清理缓存 context，在下次访问重新连接。

浏览器故障（browser_unavailable、browser_runtime_missing、render_timeout）保存诊断，并允许免费文章按原失败原因继续走公共 reader；有账号引用或正文墙的内容仍不会发送给公共 reader。

### 3. 页面/reader 质量检查误伤全文或收进页尾

- TheStreet 文章节点中的 About the Authors 及后续作者简介退出正文候选。
- Yahoo 等 reader 的 View Comments / Terms and Privacy Policy 页尾先截断，再判断正文内订阅墙，避免评论登录入口误伤正文。
- Benzinga 的部分真实 reader 模板只有 Title / URL Source / Markdown Content，缺少 H1；现在仅在源 URL 精确一致、标题匹配时接受该模板。保存的 whale-alert 真实响应恢复 8,110 字符，保留整个文章区。
- InvestorHub 的 Cloudflare Access Denied 在无实际文章节点时明确分类为 challenge_required，避免把挑战页面当作错误文章。
- curl_cffi 默认 V2 session 使用自身 Chrome UA/client hints，与 impersonation profile 对齐；不再叠加旧手写 Chrome 125 头。V1 默认 factory 不变。本项修正配置一致性，未宣称其单独解决远端 WAF。
- 免费文章的公共 reader 降级允许 challenge_required；有正文订阅墙、登录墙、凭据引用或报价/列表/视频页面仍拒绝该降级。publisher 与 reader 的冷却保持各自端点约束，不清除原站冷却。

### 4. 24/7 Wall St. 的公开文章 API

确认目标文章暴露 WordPress 公开 REST 路径，新增受限 adapter。根据文章 slug 获取 `/wp-json/wp/v2/posts`，必须同域同路径、status=publish、content.protected=false，随后仍验证正文标题/质量；原文章 URL 不改，实际 API URL 单独写入 body_source_url。

本机真实公开 API 返回完整正文；本机管线同篇文章正常 HTML 路径返回 5,168 字符。远端 HTML、公开 API 与普通 Chrome 代表样本仍被 Cloudflare 拒绝，因此这项是增加合法公开路径，**没有证据表明远端 18 条已恢复**。

### 5. CNBC 视频不是普通 HTML 文章

9 条 unsupported_media 均为视频。新增官方字幕路径：仅解析 JSON 数据（不执行页面脚本），要求 exact 当前视频 URL、标题匹配、premium=false、合理 duration，并仅接受该视频对象列出的 CNBC Akamai WebVTT 下载地址。拒绝相关视频、会员视频、外域伪造编码。

远端 8 条真实字幕完整覆盖视频时长：80–622 秒，清理滚动重复后正文 1,388–10,492 字符；写入 content_role=video_transcript、cnbc_public_captions 与真实字幕来源 URL，正文明确以 Transcript 开头。校验字幕开始/结束时刻与视频时长，截断字幕不标为完整。没有 ASR、模型生成或补写字幕。另 1 条只返回 45 字节 WEBVTT 头，继续 unsupported_media。

## 尚不能算修复成功的情况

- Yahoo 65 条原 403 等剩余免费样本：已抓到正常 HTML、真实 reader 和 Axios 转发文章的代表全文，也遇到主机重启后 reader 403；不能只凭首次成功断言稳定。MT Newswires 两条实际正文区显示升级订阅提示，需授权，不属于“评论区登录导致误判”。其他 source_summary_only 逐篇验证原站链接，不批量猜测 URL。
- Seeking Alpha 44 条：已有账号可访问的历史个人浏览器证据不等于远端 worker 已有身份。本次远端没有 status.json / authenticated host 配置，不将展开摘要或转载摘要当作全文。
- Reuters/Barron’s 401：留到第二项。WSJ 本批无直接代表 badcase，但第二项仍需测试。MarketWatch 通用 URL 的本批代表有 IBKR API 全文，不应先去登录 newsviewer；其他真实 MarketWatch URL 在第二项验证。
- Investopedia：远端 Chrome 代表样本被挑战，reader 返回 451；没有把地区/WAF拒绝归为付费墙。InvestorHub 仍为明确 Cloudflare 拒绝。
- 24/7 Wall St.：公开 API 本地可用、远端仍拒绝；浏览器基础设施恢复后先低频复测同 URL，不承诺自动通过挑战。
- Yahoo 视频及 Finnhub timeout 等未获得恢复证据的类别留在逐条台账；不通过降低正文质量门槛强行标为成功。

## 远端运行与启用步骤

检查对象为 doxagent-sg，仓库 `/home/ubuntu/doxagent`，初始部署 c74826e74e7a28d53fc5bb890e3355ef3d469172。

复测文件传输期间远端主机发生重启（不是本轮发起）。随后容器重新启动，代码版本未变；Chrome unit 为 activating，DISPLAY=:10 桌面 socket 未恢复。本轮没有重启/杀掉用户 Chrome、修改桌面会话或越过资源治理。继续完成不依赖 CDP 的公共路径验证，并保留重启前浏览器响应。

生产激活需要独立验收：

1. 仅提交/同步本轮正文模块、settings、测试与本报告，不混入其他任务的脏文件；构建后重建 message-bus / content-enrichment 两个服务。
2. Chrome 普通桌面会话恢复后，从正文容器检查 `/json/version` 可用。已有 crawler CDP relay 为 `http://172.17.0.1:19223`，不是本机个人 Chrome endpoint。
3. 设置 `DOXAGENT_CONTENT_ENRICHMENT_PIPELINE_ENABLED=true`；新入队任务才使用 body_v2.1。再设置 `DOXAGENT_CONTENT_ENRICHMENT_BROWSER_ENABLED=true`、`DOXAGENT_CONTENT_ENRICHMENT_BROWSER_CDP_URL=http://172.17.0.1:19223`。连接现有 Chrome 时 HEADLESS/CHANNEL 不负责启动浏览器。公网 CDP 端口不开放。
4. 先在等价构造参数的独立进程复测本轮免费样本：正文尾段、标题与来源一致；明确区分浏览器故障和 publisher 拒绝。再检查新自然入队任务的冻结版本、候选 hash、body_source_url 与 outcome，不能仅看容器健康。
5. 不自动重放 405 条历史发布、不迁移旧任务冻结版本；原生 API 的旧失败标签需要历史修订时另行执行可审计的修订流程。
6. 用户完成远端正常登录后，执行第二项身份配置及文章级授权测试；不从本机提取或搬运浏览器密码/cookies。

## 验证与证据

本轮 5 个正文相关测试文件共 **86 passed**；Ruff 通过，正文模块与 settings 的 mypy 通过，git diff --check 无内容错误。验证包括原生正文 provenance/零网络、身份与 lease 保留、明确正文墙隔离、CDP 生命周期/重连、浏览器故障降级、公开 API 同稿与 protected 拒绝、视频身份/授权/字幕覆盖与滚动重复。

证据目录 `exports/body_trial_20260915/`：

- `native_diagnosis.json`：真实输入匹配与正文 hash。
- `body-trial-results/probe.jsonl`：重启前 19 个来源/域名/原因代表样本，5 条管线成功。
- `body-trial-results-v2/probe.jsonl`：公共路径 27 条测试，Yahoo 1 条 + 官方字幕 8 条成功；与首次样本有重合，不能相加为 14 条普通文章。
- `body-trial-results-v2/caption_review.json`：字幕去重前后字符数；对应 `.deduplicated.txt` 及实际 gzip WebVTT。
- `captured_response_review.json`、`body-detail/`：普通 Chrome HTML 与公开 reader 的真实响应，当前规则解析结果。
- `247wallst_public_api.json`、`wp_local_live.json`：本地公开文章 API/HTML 的真实结果。
- `badcase_triage_405.csv`、`triage_summary.json`：全部输入代表记录的范围、处置与验证边界。

原始网络响应包含站点公开页面及临时追踪数据，仅留本地诊断目录，不提交为源码或账号资产。
