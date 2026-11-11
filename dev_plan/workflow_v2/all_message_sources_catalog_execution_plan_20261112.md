# 全局「全部消息源」目录与分类体系开发方案

日期：2026-11-12（北京时间）。本轮只排查和制定方案；未修改产品代码、配置、数据库、订阅关系，未构建、发布或运行采集探测。

## 1. 推荐实施边界

新增一个只读全局目录、一份人工维护的静态分类文件和一个批量只读 API，复用既有订阅、采集账本、文章定额抽样、消息统计桶及 V2 组件。不增加分类管理后台、数据库表、历史回填、源探测、定时刷新或常驻缓存。

页面从消息总线和全局顶栏进入，地址建议 `/message-bus/sources`。保留应用已有的唯一全局顶栏，页面内部不再创建顶栏、Logo、Ticker 导航或第二套全局菜单。全局目录不依赖当前选中的某个 Ticker。

有两个口径需要先写清，不能靠布局掩盖：

1. **全局通用是适用性，不等于当前已经订阅全部 Ticker。** 例如企业 IR 是通用能力，生产目前只有9个 Ticker 绑定；目录必须展示实际绑定，不补造第10个订阅。本需求不自动绑定/解绑。
2. **全局卡片是现有各订阅指标的消息源级汇总。** 保留每个绑定已经上线的文章抽样，再汇总其事实。不是给每个源另建“全源历史最近20篇”统计。延时按实际 Ticker 发布消息计，正文率按 canonical 文章去重；当天新增沿用正式发布条目数。下文明确每项分子、分母和样本，避免把共享正文或共享轮询重复解释成独立工作量。

## 2. 勘察基线及实测现状

### 2.1 版本与证据

- 审查工作树 `C:/DoxMetrics`，代码 `5c5a875b07438466ad57b3612fb75e46bdbef5aa`；勘察时远端 origin/main 同版本。
- 共享主工作区 `C:/Users/WEIXUANXIE/Desktop/DoxAgent` 仍在 `bd7542c00`，有其他会话未提交改动；没有拿它的旧版本替代本轮已发布实现，也没有重置或合并它。
- 生产只读探针时间 UTC 2026-11-11 18:45:05（北京时间11月12日02:45:05）。读取少量配置、绑定、当前采集头和按绑定最新5条文章定位信息，实际数据库段约 **0.135秒**；此数字不是新页面接口的性能承诺。
- 探针使用只读 SQLite、9秒中止预算，先通过现有 `ReadStore` 解析 serving 指针再连接实际库。未读取文章正文、模型记录或历史轮询窗口。
- 证据目录：`eval/global_source_catalog_audit_20261112/`，含 inventory.json、summary.json、只读探针、附件 L1 提取及摘要。

### 2.2 当前生产目录

| 项目 | 结果 | 对实现的意义 |
| --- | ---: | --- |
| 登记的 SourceDefinition | 105 | 完整目录不能只从有订阅的源反推 |
| 当前有未删除绑定的源 | 79 | 另26个也需要目录卡片，标明未订阅/停用/测试 |
| enabled=false 的源 | 21 | 不能当作数据丢失，也不擅自删除旧合并源 |
| delivery_mode=test | 6 | 全部无普通 Ticker 绑定；保留测试标记，不制造运行指标 |
| 未删除的 Ticker 绑定 | 285 | 含暂停/禁用绑定，不仅限运行中的 Ticker |
| Ticker 数 | 10 | BE、CEG、COHR、INTC、IONQ、MU、NBIS、ONDS、RKLB、SNOW |
| 采集头 required!=0 | 864 | 含331个退休头；不是864次物理采集，不能直接拿来算源数或成功率 |
| 缺 source.entry_url | 17 | 需复用公共参数/静态入口补足，不能只显示 entry_url |

分发方式：67个 by distribution、26个 by ticker、5个 by search、6个 massive distribution；旧 stocktwits_messages 记录未显式存 acquisition_mode，业务模型默认 by ticker。少数旧记录也未显式存 delivery_mode，应沿用现有业务默认值，不把缺字段误判为测试或未知新模式。

已实测同一 canonical 文章跨 Ticker：Yahoo 样本 `c285a583…` 同时对应 BE/NBIS，发现时间不同；Barron's 样本 `18029c55…` 对应 BE/COHR/INTC。说明不能用文章ID丢掉独立的 Ticker 发布延时，也不能把共享正文当三次补全。

### 2.3 当前代码能够提供什么

| 位置 | 已有事实/能力 | 缺口 |
| --- | --- | --- |
| message_bus_v2/schema.py | SourceDefinition：ID、名称、entry_url、adapter_ref、enabled、acquisition_mode、delivery_mode、正文策略开关 | kind 只有 api/crawler；没有列表/正文主备方式和 L1 适用性 |
| manifests.py 及 technology_sources.py、energy_catalog.py、defense_sources.py、corporate_ir/catalog.py | 静态源、feed、入口、参数和默认目标 | 不能把默认目标当实际订阅；默认目标也不是行业分类 |
| TickerSourceBinding | source_id、ticker、enabled、polling、streaming、tombstoned_at | SourceStatus DTO 仍以单绑定为身份 |
| api_v2/bindings.py | 单绑定配置、可添加 API 源及 CAS 修改 | available-api-sources 特意排除爬虫、已绑定、停用及测试源，不是目录 API |
| api_v2/bus.py、bus_dashboard.py | 单 Ticker 状态、绑定列表、7项源卡指标、采集状态格式化 | 没有全局目录/跨 Ticker 汇总；不能循环调用10次 dashboard 代替 |
| api_v2/article_samples.py | 每绑定近24h至少5篇则取最多20篇，否则最近5篇；已有紧凑定位索引 | 返回的是压缩后的均值输入/计数；全局正文去重还需要保留内部 article_key |
| bus_metrics.source_counts_today | 已有 bus_messages 当天统计桶 | 计量单位为 Ticker 正式发布条目，不是全局独立文章数 |
| collection_plane/observability.py、source_rounds.py | 批量采集头、逻辑轮次、近20轮实际间隔、赛马与互补条件 | recent_rounds 入口仍以单 Ticker 读取，需要批量化；不能直接复用旧卡的逐秒倒计时 |
| frontend app.tsx | 已有唯一应用顶栏及 /audit/cost 全局路由 | 顶栏仍写“成本审计”，审计页正文已写“收益 / 成本审计” |
| frontend core/cache.ts、page-query.ts | 可复用认证请求、DTO、错误反馈、QueryClient | 默认缓存无限；普通 PageContext 会自动续期，新管理页不能照搬这些生命周期 |

现有消息源卡实际显示“API/爬虫”与“单条/分批发布”，不是完整采集分类。`publication_mode=immediate/buffered` 是发布节奏，不能当四种 acquisition_mode；SourceKind 继续服务原业务，不为展示标签重写其枚举。

## 3. L1 行业字典与目录分区

依据附件《DoxAgent_板块地图_L1-L2_V5.xlsx》“总览”工作表 A16:C28。SHA-256：`a969fec170d076f40d5695d81ef6fd9e7a25452f5f8dbea39e45810e4076d4c9`。

| ID | 名称 |
| --- | --- |
| L1-01 | AI、半导体与计算基础设施 |
| L1-02 | 软件、云、网络安全与前沿计算 |
| L1-03 | 电力、公用基础设施与能源转型 |
| L1-04 | 传统能源与能源服务 |
| L1-05 | 航空航天、国防与安全科技 |
| L1-06 | 医药、生物科技与医疗创新 |
| L1-07 | 汽车、智能交通与自主系统 |
| L1-08 | 机器人、自动化与先进制造 |
| L1-09 | 数字金融、Fintech 与 Crypto |
| L1-10 | 原材料、矿业与战略资源 |
| L1-11 | 消费科技、互联网、媒体与娱乐 |
| L1-12 | 交通、物流与供应链 |
| L1-13 | 政策敏感型产业 |

另加一个非行业区块“全局通用消息源”。不引入 L2、Ticker→行业推断或 L2 维护系统。L1-13是产业类别，不能当所有政府/政策消息源的兜底分类。当前无消息源的 L1 不显示空卡片区块，筛选字典仍保留13项。

多行业源通过 `sector_ids` 数组表达，数组第一项作为默认展示分区，一源一张卡，避免跨区重复铺卡。行业筛选后，行业源放入当前匹配分区；仍显示其全部行业标签。全局通用源不填13项行业，而用 `applicability=GLOBAL` 表示全部适用。

默认展示全部登记源，停用、未订阅、测试源各有明确状态并在区块内靠后，不悄悄过滤掉。“启用/停用”“正式/测试”只是既有状态筛选，不另建生命周期体系或把旧源自动归并删除。

## 4. 静态分类与维护方式

### 4.1 唯一维护位置

新增 `src/doxagent/message_bus_v2/source_catalog.json`，随代码版本发布，含13项 sectors 及按 source_id 索引的展示元数据。Python API 加载一次只读小文件，不写数据库、不复制到每个绑定、不增加管理表单。

每个源只维护：

```
description: 一句事实性的用途说明
entry_points: 公共入口/模板的必要补充，可空
listing: { primary: [...], fallback: [...] }
body: { primary: [...], fallback: [...] }
applicability: GLOBAL | INDUSTRY
sector_ids: 有序 L1 ID 数组，GLOBAL 时为空
```

名称、enabled、delivery_mode、acquisition_mode、实际订阅来自现有事实，不在 JSON 再存第二份。四种分发方式本身已经是人工配置，读取该静态业务字段满足需求，无需重复手写分发标签。人工说明与模式修改同一提交更新。

主方式枚举：RSS（含Atom）、API、HTTP、PLAYWRIGHT、CHROME_CDP。正文另允许 `SOURCE_BODY`（随源提供）、`PUBLISHER_ROUTED`（按原文站点策略）、`NOT_APPLICABLE`；这三个是如实表达现有业务所需，不是复杂运行原因系统。

- RSS虽然通过HTTP下载，也只标RSS，不同时标HTTP；公开网页/同源公开JSON列表归HTTP，独立供应商服务归API。
- Playwright驱动外部Chrome/CDP，展示CHROME_CDP；不能因为用了Playwright客户端就一律标Playwright。托管浏览器才标PLAYWRIGHT。
- 真正并行的RSS/HTTP路径都属于 primary，显示“RSS + HTTP”；有失败后切换关系才放 fallback，不把赛马叫fallback。
- 同一源不同 Ticker 入口方式不同，primary 可以有多个，简短说明“按入口配置”；不动态扫描执行路径求标签。
- Google/Yahoo/聚合API可能指向不同原文站点，正文可标“原文站点策略”，辅助列出已配置的方法。不承诺每条文章都走同一浏览器。
- 不适用正文的短帖如实标不适用，不能显示正文成功率100%；API能带正文也不意味着所有API源都不需要补全。
- 未标注的新源仍可显示卡片和既有运维数据，标签只显示“未标注”；不因展示配置缺项让整个服务启动失败。CI 检查已登记的初始源和分类文件覆盖，不做生产运行时探测。

### 4.2 需要人工核对的实际例子

| 源/家族 | 已核实的代码事实 | 分类落地要求 |
| --- | --- | --- |
| Barron's | industry_sources._barrons_http 首先请求公开 market-data/api/news，失败可退浏览器；原生浏览器feed仍保留 | 列表主HTTP、备浏览器；浏览器究竟CDP/托管，按当前配置人工确认后填。正文单独按付费文章Site策略维护，不把列表HTTP当正文HTTP |
| Google News Search RSS | 已有by search配置及聚合跳转、原站补全 | 列表RSS，正文按原文站点策略；不把RSS标题/摘要当完整正文 |
| SDxCentral | TechnologySource显式transport=browser | 不按kind=crawler猜具体运行环境，核对Identity后静态标CDP或Playwright |
| The Register合并源 | 多个公开结构化feed入口 | 列表RSS/API按实际接口协议人工归类，多个入口不拆出重复外部源 |
| IR | 多Ticker、多端点，RSS与HTML并存 | 一张企业IR卡，方法数组和展开入口涵盖实际端点；不按每端点另造消息源 |
| SEC / IBKR / Finnhub等 | 原生API/文档能力与通用正文补全不同 | 分别核对随源正文、补全文档及备用路径，不能套一个所有API共用的正文标签 |
| 大规模分发6源 | 既有test模式，无普通Ticker绑定 | 分发标massive distribution、测试状态；未取得业务消息样本时显示未记录，不伪装已上线生产 |

初始映射必须覆盖105个登记ID（含旧停用源）；以注册源为卡片身份，按adapter家族批量核对，少量差异逐ID覆盖。附录给出建议行业分组；抓取主备标签需要实现前对配置逐条确认，不能将本表未核实的浏览器类型写成已确认事实。

维护说明见 `docs/message_source_classification_maintenance_draft_20261112.md`。实施时转为正式短文并链接到既有 `docs/handoffs/message-source-engineering-handoff.md`，不改业务Prompt/Agent权限规则。

## 5. 全局运维指标：精确计量单位

### 5.1 推荐汇总方式

对每个未删除绑定 b，完全复用当前文章集合 S_b：首次发现滚动24小时至少5篇取最多20篇，否则取最新5篇。全局该源使用所有绑定的 S_b 事实集合，不在汇总后再次截20，不把日期选择器引入管理页。

因此一个有10个订阅的源最多有200个消息观察样本；不能标“全源最近20篇”。卡片tooltip简述“各订阅按近24h最多20篇、不足取5篇后汇总”，显示实际有效n即可，不建设无样本原因面板。

| 卡片指标 | 全局口径 | 复用及注意点 |
| --- | --- | --- |
| 最近成功 | 所有实际绑定最后逻辑成功时刻的最大值 | 表示最近任一订阅成功，不代表全部正常；同时保留状态分布，不能用这个最大值掩盖坏绑定 |
| 当天新增 | 当前业务日该source的 bus_messages，跨全部相关Ticker求和 | 单位是正式发布消息条目；同一文章分给3个Ticker是3条。标题下/tooltip写清“全部Ticker发布条目”，不是独立文章数；复用已有桶，无新写入 |
| 平均端到端耗时 | 所有S_b中合法发布消息的耗时总和 / 有效消息数 | 不平均各Ticker均值；同文章不同Ticker有独立判定/发布等待，应分别保留 |
| P95端到端耗时 | 同一批消息耗时排序，取ceil(0.95*n) | 不平均各Ticker P95，n=1也显示 |
| 真实抓取延时 | 同一消息样本中合格Publish Time至该Ticker首次Bus发布的平均值 | 沿用EXACT/verified资格、分钟00秒、负值排除；共享原文不抹掉不同Ticker发布延时 |
| 实际刷新间隔 | 近20逻辑轮次各绑定有效间隔总和 / 有效间隔数 | 保留订阅轮次口径；不能把所有Ticker成功时点拼一起求相邻差，后者会把一次共享轮询的多条回执算成零秒间隔。也不声称该值是物理HTTP请求间隔 |
| 正文补全成功率 | ∪S_b按canonical article_key去重后FULL/SHORT_FULL数 / eligible文章数 | BODY共享，不按订阅数放大权重；缺结果的eligible仍入分母，明确不适用排除。需要内部selector保留article_key，不能仅合并现有比率 |

卡片数值不因为源失败就隐藏；失败排除是原顶部均值的规则，本页没有再造一个顶部全局均值KPI。正常0、未记录、不适用沿用现有Value语义。所有7项保留，不添加成本、历史累计量、成功率趋势等新指标。

**本期明确不做：** 如要将“当天新增”改成跨Ticker独立文章数，或将全局取样改成单源全历史严格最新5/20篇，那是另一个统计口径，现有桶/按绑定索引不能直接准确支持。不能悄悄用全日消息COUNT DISTINCT、历史排序或先LIMIT再猜全局首次发现。本方案选择复用现有条目计数及绑定样本汇总，不新增聚合持久化。

### 5.2 订阅关系与状态

- Ticker标签来自全部未tombstone绑定，包含禁用绑定和已暂停Ticker；禁用标签弱化，不直接删掉。全局源启用与绑定启用分别保留；当前无绑定写“未订阅”，不写“无消息源”。
- 顶部状态为现有来源状态的分布，例如“运行6 · 休眠3 · 异常1”；全部禁用/全部休眠/未订阅时使用对应简短状态，不用一个最近成功时间推断全源健康。
- 各Ticker状态继续复用source_status，读取采集账本，保留IR/SEC休市继续采集策略及原逻辑轮次条件。
- 卡片需呈现现有current_error价值：默认一条简短错误与受影响Ticker，多个错误本地展开。不是逐卡再发诊断请求。
- 不复用旧SourceCard的每秒setInterval、倒计时环与自动请求。页面显示取数时刻和静态状态；目标间隔可显示同值或配置范围，不播放一个已经过时的全源轮询倒计时。

## 6. 页面、卡片与筛选设计

### 6.1 设计方向

延续 V2 的中性背景、殷红交互、轻边界及14px正文，作为专业管理目录设计；不增加新UI库、装饰英文、渐变、图片Logo下载或动画。用户本轮明确要求三列大卡片，采用充足信息承载的卡片，不用大块空白撑高度。

内容宽度沿用1480px上限，两侧24–28px。1262和1559两种桌面宽度均三列，`repeat(3,minmax(0,1fr))`、间距16–20px，禁止缩小字体来硬塞。每行stretch，卡片底部指标对齐；不锁死整页卡高，不做瀑布流。

页面只有：标题及手动刷新/快照时间 → 一排可换行筛选 → 适用性区块标题与源数 → 三列卡片。区块标题显示全局通用/附件正式L1名称，数量按该组真实源数；页面总数是去重source_id数。

### 6.2 卡片内部组织

1. 名称16–17px、适度加粗，右侧一个简短状态汇总；名称和状态不挤压为单行截断，长名称可两行。
2. 下接公共入口短名称/域名和外链图标；多个入口显示首个和“查看N个入口”，本地展开完整入口/模板及对应Ticker，不为卡片全部常驻列出长URL。
3. 一句用途说明，最多两行；正文选择和复制可用，整张卡不设笼统点击跳转，链接/展开/Ticker各自独立。
4. 紧接两行小标签：“列表 HTTP · 备用 Chrome/CDP”“正文 Chrome/CDP”，主方法使用轻底实边标签，备用用灰色辅助字，不让主备看起来同级。分发方式与行业标签自然接在属性流中，不各自套一个面板。
5. Ticker小标签保留可见订阅关系，通常10个可以两行放下；过多时显示前两行和“+N”本地展开。点击Ticker去其消息总线，禁用状态不能仅用颜色表达。
6. 用一条轻分隔和2列指标格承接7项指标：最近成功/当天新增、平均端到端/真实抓取、P95/实际间隔、正文成功率；标签在上、数值在下，数值等宽数字。同一行标签和数值有稳定对齐，不塞成七列。
7. 错误作为卡片内容的最后一行；样本n和正文分子分母放在相关指标tooltip/可访问说明里，不增加常驻解释区。

这些是一个连续阅读顺序，不是“源信息/分类/运维”三个独立大盒子。卡片高度主要由真实内容决定，正常密度目标约460–540px，避免为了等高给全部卡片加大量留白；展开内容自然增高。

### 6.3 筛选规则

使用既有Select/Popover/Badge/Button：抓取阶段（任一/列表/正文）、抓取方式、分发方式、行业、适用属性（全部/全局通用/行业）。可加一个名称/域名关键词输入，全部本地过滤，不发搜索请求。

- 不同维度AND；同维度多选OR。抓取方式默认只匹配primary；通过“含备用方式”开关才匹配fallback，避免搜CDP时把仅备用CDP误当主方式。
- 行业选择X且属性为全部时，显示GLOBAL源及sector_ids含X的行业源；选“行业”后不带GLOBAL。全局源不需要人为挂13个标签。
- 来源实际订阅不参与行业自动分类；可保留Ticker标签点击导航，不本期再造Ticker行业字典。
- 多行业源默认只放sector_ids第一项分区；筛选行业后按匹配行业展示，整页仍只出现一次。
- 筛选值写URL查询参数，刷新后保留；手动刷新平滑替换数据，不清空筛选、已展开卡片或滚动位置。无匹配时一条空态及“清除筛选”即可。

## 7. 入口与收益/成本审计UI调整

### 全局和消息总线入口

- app.tsx 的 account-area 改为两个同级NavLink：“全部消息源”“收益 / 成本审计”，图标16px、文字14px、控件高36px；共享hover/active/focus样式。退出保持独立图标按钮，权限标签保留。不能把两个链接做成两块高饱和主按钮。
- Audit已有正文标题正确，统一顶栏旧名称；继续保留 `/audit/cost` 与 `?audit=REVENUE`，不为文字改名迁移审计接口或破坏书签。
- 当前消息总线右上是STREAM/CONFIG分段Choices，而非独立抽屉。扩为“消息流 / 配置 / 全部消息源”：前两个保留原mode参数，第三个是语义链接到全局页，不把它写进原mode后渲染Ticker内伪全局目录。
- 全局页无TickerSwitcher和TickerWorkspace导航。可用路由state保留来自哪个Ticker，供一条“返回消息总线”链接使用；顶栏直接进入时不随意选默认Ticker。

### 审计页整体视觉整理

- 现状：标题带收益/成本选择；成本又有API/Codex、周期/刷新、标的/节点/模型、维度选择等多排控件；收益页另有LIVE/PAPER、期间、模式、7项KPI。功能有效，但控件层级和两个页面的间距、原生select、数字样式不统一。
- 标题行统一 h1+收益/成本分段；下一行统一当前视图筛选和刷新。成本保留API/Codex、周期、Ticker，节点/模型收拢在紧凑筛选条，NODE/MODEL维度放到用量表标题行；收益保留账户/周期/模式，不预取未打开的另一种审计报告。
- 成本6项、收益7项KPI保留真实业务含义。1559px成本6列、收益7列可用时平排；1262px按成本3×2、收益4+3重排，不缩小字号挤大金额，也不按卡片逐项加装饰色块。
- 趋势/占比继续用当前数据；统一section标题、边框、内边距、无数据态。用量表第一列节点/模型/渠道分清主次；其余数值右对齐，thead/td同列padding，缓存创建等低频分项继续按需展开。
- usage缺失、未计价、历史覆盖、实盘/模拟及假设收益提示都保留可读。收益正负不与费用升降混用颜色。收益详情抽屉及分页交互不改变业务逻辑。
- 样式作用域限定 global-actions、source-catalog、audit；不重写整个styles.css或波及Runtime/Overview。

## 8. 最小后端与前端数据方案

### 8.1 一个按需接口

新增 `GET /api/doxagent/v2/message-bus/source-catalog`，复用现有认证、can_read、query runner、Resource/Value/Coverage/Meta及request_id。管理页只读，不要求can_operate，不提供绑定编辑能力。

响应建议 `SourceCatalog`：`snapshot_at`、`semantic_day`、13项`sectors`、`sources[]`。每个元素为source_id/名称/公共入口/介绍/静态分类/已有模式与启用状态/实际订阅数组/聚合状态/7项metrics及必要样本数。不要把单绑定SourceStatus的binding_id伪造为全局ID；另定义一个小GlobalSourceCard类型，复用其值类型和最近指标结构。

本页不需要ReadContext、PageKind、view_id、MVCC长期视图或续期定时器。一次请求捕获同一个now、semantic_day和Read短事务；采集账本另一个短只读连接，属于同次取数的双库近时快照，不能宣称跨库原子一致。沿用现有投影新鲜度与缺失反馈，不新增状态跟踪。

### 8.2 批量读取流水线

1. 通过当前对象kind索引读105条源的小字段、285条未删除绑定及相关Ticker状态；不是扫描全部object_current。公共参数只输出允许展示的URL/模板，复用已有脱敏校验，不透传整个default_parameters、schema、Cookie、Key或Identity连接地址。
2. 一次批量读采集头。复用binding_states的逻辑；对ledger缺失绑定，在一个批量查询中预取现有native poll头。调用source_status时传入source/ticker/live状态，避免函数退回逐绑定store.get。
3. 将 `recent_rounds` 提取为可接收一批绑定/预读unit的helper；一次连接批量定位各unit有界cycles，继续复用evaluate_rounds、逻辑分组及退休计划边界。不能按10个Ticker循环开连接、重复加载所有采集头，也不调用30分钟诊断接口。无订阅源跳过轮次读取。
4. 提取 `select_article_facts_many(store, binding_pairs, now)`：利用现有article_sample_source_time，SQL中每绑定LIMIT20，再按current_identity精确取delivery及所选BODY/message小字段。先形成S_b，保留内部article_key/eligible/full/e2e/capture，再由旧Ticker接口和全局接口各自reduce。原Ticker返回契约和样本不变。
5. 当天新增一次批量归并现有bus_messages当天桶，按source_id跨Ticker汇总；沿用bus_aggregates_from及覆盖语义，未覆盖不是0。优先按当前已知Ticker列表定位现有索引，然后按source汇总，不跨所有历史日。
6. 在本次请求内一次分组计算卡片，分类筛选留前端。返回已经算好的均值、P95、比率；不把原始采集回执、采集头checkpoint、文章正文或几千条样本发给浏览器。

当前规模候选上限 `285×20=5,700` 条小事实；每源P95排序其有效消息样本即可。正文按source+canonical去重。本次临时dict/数组随请求结束释放，不持久保存跨Ticker样本缓存。轮次查询量受已有unit与20轮限制，需要记录实际EXPLAIN及耗时；不能把864个采集头当可任意拉48小时历史的理由。

已做一次只读批量查询可行性探针（UTC19:01:38）：285个绑定一次SQL读到3,377条候选，按既有规则选出1,808个消息观察样本、对应1,040个source+canonical文章；约1.986秒。EXPLAIN确认定位表走article_sample_source_time，delivery/BODY/message均走完整current_identity，无历史排序。生产写入为0。这里只验证文章查询路径，不是完整API或轮次汇总的验收；也未把该探针接入产品。详见sample-feasibility.json。

先测已有循环有界seek是否足够；若SQL往返成为主耗时，再改为json_each参数批量相关LIMIT。同一进程连接内的索引seek不等于逐源HTTP请求，不为了凑“一条SQL”重写整套轮询统计。

### 8.3 前端生命周期

新增页面独立useQuery：首次进入请求一次，离开释放页面数据，手动刷新再请求；`refetchInterval=false`、focus/reconnect关闭，不调用useBusMinute/usePageContext，不保留无限gcTime。建议该query局部 `gcTime=0`，遵守全局QueryClient但不修改其他页面默认策略。

刷新中保留上次结果，只禁用刷新按钮并显示轻量忙状态；失败保留旧内容和request_id。初次失败不能显示“0个源”。筛选/展开只操作已有响应，不发请求；不为105张卡各建立query、observer或1Hz定时器。不需要虚拟列表、全局状态容器或后台预热。

## 9. 到文件的执行顺序

| 顺序 | 文件/模块 | 具体交付 |
| --- | --- | --- |
| 1 | source_catalog.json（新增）；docs中的维护说明 | 固定13项L1；105个源逐个人工填主备与适用性；校验公共URL、ID覆盖、行业引用、主备分离及镜像/打包包含该JSON。核对当前实际Site配置，不执行网站探测 |
| 2 | api_contract/doxagent-v2-api.types.ts；schema-source.ts、core/api.ts、generate-schema.mjs；api_v2 DTO/OpenAPI注册 | SourceCatalog/GlobalSourceCard；服务端和浏览器schema根类型同步；不遗漏新DTO导致运行时500 |
| 3 | api_v2/article_samples.py；collection_plane/source_rounds.py | 批量小事实selector及预读heads入口；旧Ticker路径用等价测试确认不回退，不改采集调度 |
| 4 | api_v2/source_catalog.py（新增）；api_v2/app.py | 一个只读聚合路由；静态标签+批量配置/订阅/状态/桶/有界样本；完整错误/权限/未知值语义 |
| 5 | pages/source-catalog.tsx、其局部样式（新增）；message-bus.tsx、app.tsx | 三列目录、筛选、公共入口、订阅标签、无定时器的7项指标；两处入口及NavLink样式 |
| 6 | pages/audit.tsx、revenue-audit.tsx/css及必要局部样式 | 全局入口统一改名；审计控件、KPI及表格视觉整理；API和业务计算不改 |
| 7 | 定向测试、changelog、验收文档 | 真实聚合案例、SQL计划、资源观测、两宽度浏览器验收；重要代码修改追加changelog |
| 8 | 标准发布流程 | 实施获确认后，先整合并推origin/main，再仅构建/发布v2-api和v2-web。若需要Projector改动，必须另列具体原因；本方案不需要它，也不替换Collector/Bus/Worker/Scheduler/Site/BODY |

## 10. 性能边界与验收标准

### 数据与资源

- 新增业务写入 **0**、新表/索引 **0**、新后台任务 **0**。不更改源采集/分发/休市策略、正文策略或生产订阅。
- 初次/手动各一次目录请求；60秒闲置、窗口切回、展开卡片、选择任意筛选均不得产生新统计请求。主界面原消息流/分钟刷新不受影响。
- 不查objects历史、不扫整个message/body历史、不逐源/逐Ticker走HTTP；文章事实≤20×未删除绑定数，正文只读选中ID小字段。SQL计划必须出现source前缀LIMIT和完整identity关联，不允许跨源排序及大范围临时表。
- 管理API继续服从既有查询预算，先以生产规模在预算内返回为门槛；记录冷/热各一次wall time、峰值内存差和读取行数，目标完整热请求约4秒内、临时对象约10MB内。文章查询单独实测已约2秒，不能把配置探针0.135秒冒充完整接口速度。这些是实施验收目标，不是已完成基准。若未达标，先去重复读取/循环连接；不先加缓存服务、索引或常驻汇总。
- 不为验证新页触发真实采集、模型、浏览器登录或交易。读库已有积压时报告快照新鲜度，不强制全库重放填满卡片。

### 业务与统计测试

1. 105个源都能进目录，包括无绑定、禁用、旧合并源及6个测试源；79个有绑定源、285条订阅与快照一致。计数不把共享源复制成10个源。
2. 标签主备、并行、正文不适用/按站点均正确；SourceKind、publication_mode不再冒充分发方式；行业分类不改实际绑定。
3. 文章抽样0/4/5/19/20+边界与现有Ticker实现一致，global合并不再次截20；unpublished/pending不被替换成更早成功样本。
4. 相同文章3个Ticker共享BODY，正文分母只加1；3条正式发布各有独立延时和当天条目；两Ticker样本1条10秒/9条100秒时均值91秒，P95从10条事实计算。
5. 3个绑定同一轮同时成功，实际间隔仍是各绑定的真实间隔，不因排序拼接变成0秒；失败/休眠/未配置状态分别可见；IR/SEC休市策略无回归。
6. DATE/RELATIVE/未verified、负时间差不进入真实抓取；正文0%与无样本、不适用不同；统计桶未覆盖不当0；初次500不显示空目录，刷新500保留旧内容及请求编号。

### 浏览器与发布

7. 1262px、1559px三列卡片，长名称/入口/10个Ticker/多行业/多个错误不重叠、不横向溢出，14px主要文字可读；7项指标均在卡片内，无大量空白占位。
8. 两入口均到同一全局路由；深链接、返回、刷新保留筛选；全局页只有已有应用顶栏，无虚假Ticker上下文；读权限可看且不多给操作权限。
9. 审计两个视图的字段、金额、缓存率、coverage、账户、假设/实际收益与修改前相同；控件、表头与数值列对齐。仅当前选中审计视图取数。
10. 抓包证实新目录无自动轮询/续期/后台请求；离页无卡片定时器/常驻样本。生产API DTO与前端schema均通过实际响应验证，再核对API/Web实际镜像及资产版本。

## 11. 初始行业映射附录说明

下表是基于登记源用途的**建议静态映射**，不是系统已经存在的分类，也没有写回生产。逗号前第一项为默认分区。多行业归属实现前人工逐项复核；不得以当前绑定的Ticker推断行业。尤其NSF/NIST/DARPA等跨行业政府科研源，按实际入口范围维护，不强迫归入其当前唯一订阅者所属板块。

抓取方式不在本附录按api/crawler自动填充；须按第4节核对列表路径、正文配置及备用关系。行业附录和方法核对是一次配置整理，不是上线后的动态分类服务。


| source_id | 建议适用性 / L1（首项为展示区） | 实际订阅数 | 当前状态 |
| --- | --- | ---: | --- |
| access_newswire_massive | GLOBAL | 0 | 测试 |
| airspaceforces_space | L1-05 | 1 | 正式 |
| ans_nuclear_newswire | L1-03 | 1 | 正式 |
| army_news_releases | L1-05 | 1 | 正式 |
| army_news_rss | L1-05 | 1 | 正式 |
| aviationweek_rocketlab | L1-05 | 1 | 正式 |
| aviationweek_rss | L1-05 | 1 | 正式 |
| aws_platform_marketplace_rss | L1-01, L1-02 | 2 | 正式 |
| barrons_ticker_news | GLOBAL | 10 | 正式 |
| benzinga_news | GLOBAL | 10 | 正式 |
| bloomberg_massive | GLOBAL | 0 | 测试 |
| breakingdefense_space | L1-05 | 1 | 正式 |
| businesswire_massive | GLOBAL | 0 | 测试 |
| calcalist_search | GLOBAL | 1 | 正式 |
| canary_media | L1-03 | 2 | 正式 |
| corporate_ir_press_releases | GLOBAL | 9 | 正式 |
| crn_cloud | L1-02 | 0 | 停用 |
| crn_cloud_security_datacenter | L1-02 | 1 | 正式 |
| crn_data_center | L1-02 | 0 | 停用 |
| crn_security | L1-02 | 0 | 停用 |
| ctee_semiconductor | L1-01 | 2 | 正式 |
| darpa_news | L1-05, L1-01, L1-02 | 5 | 正式 |
| data_center_frontier | L1-01, L1-03 | 6 | 正式 |
| dcd_energy_ticker | L1-03 | 2 | 正式 |
| dcd_infrastructure_rss | L1-01, L1-03 | 6 | 正式 |
| defensescoop_drones | L1-05 | 1 | 正式 |
| defensescoop_space | L1-05 | 1 | 正式 |
| digitimes_semiconductors | L1-01 | 5 | 正式 |
| digitimes_tw_rss | L1-01 | 5 | 正式 |
| dod_contracts | L1-05 | 2 | 正式 |
| dod_releases | L1-05 | 2 | 正式 |
| doe_energy_news | L1-03 | 2 | 正式 |
| enterprise_apps_official_rss | L1-02 | 1 | 正式 |
| etnews_rss | L1-01 | 5 | 正式 |
| finnhub_company_news | GLOBAL | 10 | 正式 |
| globenewswire_massive | GLOBAL | 0 | 测试 |
| globenewswire_search | GLOBAL | 10 | 正式 |
| google_news_search_rss | GLOBAL | 9 | 正式 |
| huggingnews_rss | L1-01, L1-02 | 5 | 正式 |
| ibkr_news | GLOBAL | 10 | 正式 |
| investing_ticker_news | GLOBAL | 10 | 正式 |
| investorshub_ticker_news | GLOBAL | 10 | 正式 |
| ionq_nautilus_rss | L1-02 | 1 | 正式 |
| ionq_quantum_actors_rss | L1-02 | 1 | 正式 |
| iridium_press_releases | L1-05 | 1 | 正式 |
| lakehouse_platform_official_rss | L1-02 | 1 | 正式 |
| nasa_2026_releases | L1-05 | 1 | 正式 |
| national_quantum_initiative | L1-02 | 1 | 正式 |
| nbis_official_actor_rss | L1-01, L1-02 | 1 | 正式 |
| newswire_rss | GLOBAL | 8 | 停用 |
| nist_news | L1-02, L1-01, L1-08 | 1 | 正式 |
| nrc_event_notifications | L1-03 | 1 | 正式 |
| nrc_news_releases | L1-03 | 1 | 正式 |
| nro_press_releases | L1-05 | 1 | 正式 |
| nsf_news | L1-02, L1-01, L1-06, L1-08 | 1 | 正式 |
| observability_vendors_official_rss | L1-02 | 1 | 停用 |
| optical_systems_official_rss | L1-01 | 1 | 正式 |
| power_engineering_energy | L1-03 | 2 | 正式 |
| power_magazine_energy | L1-03 | 2 | 正式 |
| prnewswire_massive | GLOBAL | 0 | 测试 |
| quantum_computing_report | L1-02 | 1 | 正式 |
| quantum_insider | L1-02 | 1 | 正式 |
| quantum_insider_business | L1-02 | 0 | 停用 |
| quantum_insider_capital_markets | L1-02 | 0 | 停用 |
| quantum_insider_national | L1-02 | 0 | 停用 |
| quantum_zeitgeist | L1-02 | 1 | 正式 |
| register_cloud | L1-02 | 0 | 停用 |
| register_security | L1-02 | 0 | 停用 |
| register_software | L1-02 | 0 | 停用 |
| register_software_cloud_security | L1-02 | 1 | 正式 |
| reuters_site_search | GLOBAL | 10 | 正式 |
| sdxcentral_news | L1-02, L1-01 | 5 | 正式 |
| sec_edgar | GLOBAL | 10 | 正式 |
| shephard_uncrewed | L1-05 | 1 | 正式 |
| siliconangle_ai | L1-01, L1-02 | 5 | 正式 |
| siliconangle_big_data | L1-02 | 0 | 停用 |
| siliconangle_cloud | L1-02 | 0 | 停用 |
| siliconangle_security | L1-02 | 0 | 停用 |
| siliconangle_software | L1-02 | 1 | 正式 |
| spacecom_launches | L1-05 | 1 | 正式 |
| spacenews_rss | L1-05 | 1 | 正式 |
| ssc_newsroom | L1-05 | 1 | 正式 |
| stocktwits_messages | GLOBAL | 0 | 正式 |
| technews_ai | L1-01 | 0 | 停用 |
| technews_hardware | L1-01 | 5 | 正式 |
| technews_semiconductor | L1-01 | 0 | 停用 |
| thefly_ticker_news | GLOBAL | 9 | 正式 |
| thelec_semiconductors_rss | L1-01 | 5 | 正式 |
| tmx_newsfile_massive | GLOBAL | 0 | 测试 |
| tomshardware_rss | L1-01 | 5 | 正式 |
| trendforce_news | L1-01 | 5 | 正式 |
| trendforce_press_releases | L1-01 | 5 | 正式 |
| unmannedairspace_counteruas | L1-05 | 1 | 正式 |
| utility_dive | L1-03 | 2 | 正式 |
| venturebeat_ai | L1-01, L1-02 | 5 | 正式 |
| venturebeat_data | L1-02 | 0 | 停用 |
| venturebeat_orchestration | L1-02 | 0 | 停用 |
| venturebeat_security | L1-02 | 0 | 停用 |
| venturebeat_software | L1-02 | 1 | 正式 |
| wccftech_ai | L1-01 | 0 | 停用 |
| wccftech_hardware | L1-01 | 5 | 正式 |
| wccftech_industry | L1-01 | 0 | 停用 |
| world_nuclear_news | L1-03 | 1 | 正式 |
| x_official_posts | GLOBAL | 10 | 正式 |
| yahoo_finance_news | GLOBAL | 10 | 正式 |
