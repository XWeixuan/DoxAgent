# SEC EDGAR 消息源实现与运行验收

目标：所有当前与未来监测 ticker 自动订阅 SEC 官方 By Ticker 源，30 秒持续发现，不启动浏览器。范围严格为 8-K/8-K/A 的 4.02、2.04、1.02、3.02、1.01、1.05、8.01、4.01、5.02、2.03、2.05；6-K/6-K/A 全部、424B5。

## 执行方案

1. 一个 sec_edgar 源及专用 scheduler group，官方 company_tickers 映射到 CIK，支持显式 CIK 覆盖；共享映射缓存，submissions 条件轮询，严格校验返回 CIK。Item 在元数据阶段筛选；8-K 元数据缺失时查官方文件，不能当作不匹配静默丢失。
2. 添加默认 profile 与现有 ticker 绑定的幂等补齐，并保证自定义 profile 的未来 ticker 也适用；保留显式停用/删除操作。SEC 不受市场休市停止轮询，仍尊重 ticker 暂停和源/绑定停用。常规新鲜度准入不改成历史回放。
3. 主文和附件在既有异步 BODY 队列补全；轮询不等待附件。按申报官方 index 的 document type 收集正文及经济附件，排除机器 XBRL、图片和纯认证/同意书/授权书，不因文件长而截断。不追下载引用到其他历史申报或站外网站的任意链接。
4. 使用专用申报抽取保留可见 inline XBRL、表格、金额、利率、期限、契约及附件标签；清理封面复选框、隐藏标签、签名尾页及完全重复段落，不使用有损 LLM 摘要。生成公司+表单+事项标题，保留 accession、CIK、原 URL、原始 acceptance UTC 时间和附件清单/失败原因。存在经济附件失败、扫描件无法读取或超限时不能报告 FULL。
5. 复用 BodyResults/任务租约、接纳、标准化、ARTICLE_ONCE、修订与投影；accession 作为申报身份，修订表单新 accession 是新申报；相同申报重复发现不重复发布。
6. HTTP 声明现有有效 SEC_USER_AGENT，发现与正文各限制最多 2 请求/秒（共享主机进程间文件节流），为原有研究 SEC 请求保留余量；403/429 冷却，固定 HTTP 路径，无浏览器/身份轮换。请求/文件大小及全文上限只为实际内存保护，触及时明确部分失败，不伪装全文。
7. 单元/合同覆盖 Item 范围、修订、时间、缺失 Item、CIK 多股类/未知 ticker、条件轮询、跨进程节流、附件失败/PDF/表格/去重、自动订阅、非交易日轮询、完整入库链。真实官方 8-K、6-K、424B5 附件 canary 在隔离本地库验收，不向生产注入历史新闻。生产验收至少自然 30 秒多轮及配置/运行镜像与 UI 投影。

依据：[SEC API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)、[SEC Fair Access](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)。官方 API 典型更新延迟小于一秒不是保证；30 秒是本地调度目标，不能承诺 SEC 公布或附件就绪的绝对 SLA。

## 开发与验收记录

已完成接入代码。最终 SEC 专项 29 项通过；BODY/国防源/结果缓存/调度相关回归 94 项通过（含 SEC 专项，不能相加当作去重总数）；配置/现有源/初始化合同回归 44 项通过。默认源目录断言同步已上线国防源与本次 SEC；一个旧 Yahoo deferral 测试原先期望 never_polled，按既有独立 HEAD/SUPPLEMENT unit 的聚合语义改为 PARTIAL，未修改 Yahoo 调度逻辑。Ruff 与 diff 检查通过。

真实官方样本经完整 EnrichmentHub → Raw → Standard → Stream 隔离库链路，3 条均 FULL，content_evidence 也均 FULL：

| ticker / form | accession | 正文字符 | 文件 |
|---|---|---:|---|
| RKLB 8-K | 0001819994-26-000067 | 5687 | 主文、EX-99.1 |
| NBIS 6-K | 0001104659-26-117978 | 8613 | 主文、EX-99.1，含 35 亿美元融资公告 |
| RKLB 424B5 | 0001753926-26-001459 | 259385 | 主文、申报费用附件 |

样本用真实原始 acceptance 时间和隔离 CLOSED_SWEEP 窗口，不改成当前时间、不写生产 Raw。标题优先采用附件中明确的新闻标题，其他按公司/表单/Item 生成；保留原始申报文件身份。所有金额、条款、可见表格保留，正文超保护阈值或附件缺失时不假装 FULL。无法文字提取的扫描 PDF 明确 requires_ocr；第一版不启动 OCR/浏览器。没有下载站外展示材料或递归历史引用，文内原链接保留。

SEC 抓取失败在既有 BodyResults、执行/结果台账中保存，有限重试后仍未齐全，不将 SEC 泛标题和 snippet 发布到研判流。下轮发现按现有结果复查规则恢复，不批量重投历史。

生产 HTTP 预检：已有真实 SEC_USER_AGENT，company_tickers、submissions 与 filing-index 在生产直连均 HTTP 200；无新增代理或浏览器身份。正式发布及自然轮询验收继续追加。
