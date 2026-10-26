# 软件、量子与硬件消息源接入

日期：2026-10-26。当前归并为 13 个常规消息源（26 个内部采集入口）和 1 个 The Fly By Ticker 源。首次上线记录保留于文末；粒度纠正与迁移见 `technology-source-granularity-correction-20261026.md`。

## 实现与资源边界

- 常规源为 By Distribution，多个 ticker 共享采集及正文结果。外部按同站、同板块、同采集方式统一管理；内部每栏目有独立 FEED 任务、checkpoint、失败退避和采集记录，健康栏目无需等待同源慢栏目结束。同网站共用 scheduler_group 和 HTTP 客户端，最多 2 个并发请求、请求间隔 0.5 秒；采集进程的公共 HTTP 池上限 4。
- 目标间隔 60 秒，沿用现有会话日历。不新增 Chrome、Profile、Site Access 身份或常驻页面。Quantum Zeitgeist 使用标准库 HTTP；其他入口使用已有 curl_cffi。The Quantum Insider、The Register、Wccftech、DARPA 使用实测可用的固定美国 HTTP 出口。
- 只有解析通过后才提交 ETag/Last-Modified 基线。支持 304、未变化内容跳过、5 分钟检查心跳及 60–900 秒失败退避；429 遵循 Retry-After。实时 checkpoint 持久化至现有 PollState，闭市 sweep 不覆盖实时进度。
- 正文复用现有文章台账与 ARTICLE_ONCE，保留 URL、语言及访问范围隔离。常规源仍要求 FULL/SHORT_FULL，预览、登录页、验证页均不冒充全文。
- The Register 云栏目合并 `off-prem`、`off_prem` 两套标签，保留覆盖；移除广告跟踪参数。VentureBeat security 补全实际 feed 路径。SDxCentral 仅提取新闻卡片中的 `/news/` URL，排除杂志推广。
- SiliconANGLE 的 RSS 可能保留旧标题。只有精确 canonical URL、文章类型、正文容器、原发布时间全部吻合，才采用发布者新标题并接收正文。

## 源与订阅映射

| 分组 | Source ID | 订阅 |
| --- | --- | --- |
| 量子 | quantum_computing_report、quantum_insider、quantum_zeitgeist | 为 IONQ 预留 |
| The Register | register_software_cloud_security | SNOW |
| VentureBeat 软件 | venturebeat_software | SNOW |
| SiliconANGLE 软件 | siliconangle_software | SNOW |
| CRN | crn_cloud_security_datacenter | SNOW |
| 通用 | darpa_news、siliconangle_ai、venturebeat_ai、sdxcentral_news | SNOW、INTC、MU、COHR、NBIS |
| Wccftech | wccftech_hardware | INTC、MU、COHR、NBIS、BE |
| TechNews | technews_hardware | INTC、MU、COHR、NBIS、BE |
| The Fly | thefly_ticker_news | 当前已注册 ticker |

常规生产订阅目标 34 个。IONQ 尚未注册时，3 个量子源（5 个入口）仅注册源并保留配置，不创建 IONQ、不启动其轮询；注册后重新执行配置脚本即可建立这些订阅。SNOW 没有词表时创建专用英文 L1/L2/定义种子；其他 ticker 的已有词表保持原样。

## The Fly 的公开能力与限制

新版站点的公开配置指向 [tfb.thefly.com](https://tfb.thefly.com)。其新闻组件实际使用 `/news/latest`、只读 POST `/news/more` 和 `/news/get`。旧 news.php 和 permalink 会跳到首页或营销页，不能用作成功证据。

此次直接使用这些公开 JSON 接口，按纽约时区转换时间，以稳定文章 ID 和内容版本识别更新，无需浏览器。完整公开文章从原生字段清洗入库，重复 ticker/文章共用正文结果；详情 API URL 保留可追溯的 id/date。

9 个现有 ticker 的 440 条样本中，75 条为公开 FULL，365 条仅预览。详情接口仍返回相同预览，因此不为它们追加无效 HTTP/浏览器重试，明确记录为 PARTIAL/subscription_required，不向消息流发送不完整快讯。当前不能宣称已取得全部付费快讯全文。后续如提供已有付费账号或身份，可以进一步扩展受限正文能力。

## 验证与执行

- 在生产网络执行真实新适配器：常规 26/26 入口采集成功，各入口取 1 篇实际文章，26/26 获得完整正文。第二轮验证 RSS/HTML 304 和 API 未变化内容跳过，未写入生产消息或修改发布时间。
- The Fly 九 ticker 均取得有效列表；单次批量提交的采集完成时间约 1.3–4.9 秒，包含共用限流等待。这是采样结果，不等于开盘后长期轮询 SLA。原生 BODY Reader 对公开全文和预览逐条分类。
- 隔离检出中的 114 项相关源、正文 Hub、监测词及控制层回归全部通过。覆盖基线不污染、退避跨轮恢复、闭市隔离、栏目不互阻、同站物理并发上限、预览不发布、文章正文复用和配置幂等；不依赖其他会话尚未提交的修改。
- 入口、固定路由和 parser 位于 `technology_sources.py`、`thefly_source.py`；正文入口为 `technology_body.py`，均接入既有 AdapterRegistry/ContentEnrichmentHub。
- 配置脚本为 `scripts/ops/configure_technology_sources.py`，词表种子为同目录 `technology-monitoring-terms-20261026.json`。默认只预览；`--apply` 应用常规源和订阅；`--include-fly` 另外绑定现有 ticker 的 The Fly。
- 现有 API/Web 已支持动态源语言及通用 DTO，本次只需发布 Bus/BODY，不调整浏览器容量、身份、日历或其他运行组件。

运维脚本不打包到精简生产镜像。将该提交中的配置脚本和同目录种子文件一起复制到 Bus 容器的 `/tmp/technology-onboarding/` 后执行：

```sh
/app/.venv/bin/python /tmp/technology-onboarding/configure_technology_sources.py --bus /data/bus/bus.sqlite3 --apply --include-fly --receipt /tmp/technology-source-configuration.json
```

## 首次生产发布与配置验收（粒度纠正前的历史记录）

运行代码：`e2b308bc8aad4da29428e5648e573ca0e8da5861`，功能提交为 `5a0b2c4c`。已推送 origin/main 并同步新加坡仓库，通过标准 storage_release 锁及构建/发布函数更新 `v2-content-enrichment`、`v2-message-bus`。两组件实际镜像均为 `sha256:0e4864f33ab3ee293c0d390dc3fa04b783174417f265f6b8631f150f154f30b8`，运行正常、零重启；新增三个模块的实际文件 SHA256 与提交一致。其他组件发布记录不变，资源回收 timer 已恢复。

已配置并启用 66 个新订阅：常规 57、The Fly 9；再次应用全部显示 unchanged，没有重复绑定或词表版本。

| Ticker | 本次新增订阅数 |
| --- | ---: |
| SNOW | 17 |
| INTC、MU、COHR、NBIS | 各 10 |
| BE | 6 |
| RKLB、ONDS、CEG | 各 1 |

5 个量子栏目已注册，IONQ 的 ticker 状态和绑定均不存在，没有启动其监测。原有 182 个绑定的数据哈希全部不变，原有 7 个 ticker 词表版本不变；SNOW 从未配置状态建立词表 v1。统一监测词语言检查也通过：SNOW 为 en，原有半导体 ticker 保持 en/ko/zh-Hant，ONDS 保持 en/he。

验收时处于休市，未调整日历、未把历史样本回灌生产。外网实测和正文样本验收、隔离全链路测试、生产镜像/绑定/幂等核验分别记录，不将它们表述为开盘后的长期自然轮询 SLA。

生产核验快照中 Bus 约 163 MiB、BODY 约 93 MiB；这是当时快照，不作为长期峰值或优化幅度。实现没有引入新浏览器身份或常驻页面。

主机回执位于 `/var/lib/doxagent/technology-sources-20261026/`：`verification.json`、`technology-configuration.json`、`technology-idempotent.json`。含环境变量的发布快照/manifest 仅留在主机私有目录，没有导出。公开实测回执及测试记录在本地 `eval/tech_sources_20261026/`。
