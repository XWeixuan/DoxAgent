# 消息总线：当前刷新故障、指标口径与调整方案

日期：2026-11-01，北京时间。生产采样约04:06–04:18，对应美东2026-10-31休市、业务语义日2026-10-31。

本轮重新检查当前实现，不沿用10月27日报告的性能结论。范围为只读排查与方案：没有修改产品代码、采集配置、数据库结构或数据，没有部署、重启、停采或删除记录。检查了生产日志、发布版本、真实数据和运行代码，未接管登录浏览器，未将服务器取数验证称为浏览器体验验收。

## 1. 当前结论

| 用户反馈 | 本轮结论 | 证据等级 |
| --- | --- | --- |
| 定时跳闪、读了一半的消息丢失 | 每分钟统计刷新更换查询key，暂时清空旧KPI/来源卡片；另有15分钟视图到期前1分钟的自动续期，调用整页刷新函数，只预装消息第一页。两个页面context分别装续期计时器，发生重复续期 | 代码确定；生产访问日志确认同刻双续期、双消息首屏读取。具体浏览器滚动位置未录屏 |
| 消息统计读取失败 | 后端新增`new_article_body.initial`，生成的后端schema仍拒绝这个字段，整份BusMetrics返回500 | INTC/MU/BE真实取数+正式DTO验证均复现；生产请求日志一致 |
| 周期新增、正文率一直不显示 | 两项共用同一份被拒绝的BusMetrics；此外计数0被编码成“未记录”，无正文样本的比率应为无样本 | 已复现；BE实际有2条新增也会因契约错误整份失败 |
| “正文补全成功率（旧口径）” | 这是前端没有取得`new_article_body`时的备用标签，接口失败时也会显示；不能据此判断后端切回旧统计 | 代码确定 |
| 休市源状态2/0，未显示全部绑定 | 2/0是当前成功/失败源数，未包含绑定总数；休眠单元不进入live状态读取，又被当成never_polled，导致正常休眠被遗漏 | INTC31源、MU30源、BE33源均复现为2/0；COHR实际32个绑定 |
| 两个源没有休眠 | 企业IR和SEC EDGAR被采集配置明确列为休市例外，仍在真实采集。页面却仅对IR豁免休眠标记，SEC卡片与后端运行不一致 | 配置代码、采集账本和源卡片输出交叉确认 |

**当前主要是契约错误、刷新状态管理错误和显示口径错误，已经不能用上次的“慢SQL+逐来源请求扇出”概括。**

## 2. 已变化的实现及当前性能

生产API、Web、Projector、Message Bus均来自`412d5cfad2890877ed5b04dcb56f7d67b927109d`。API/Web healthy。当前运行的`bus.py`、`bus_metrics.py`、`news_timeliness.py`、`dto.py`、采集面`observability.py`与本地对应源码字节哈希相同。

10月27日后已经实施的改变：

- 每来源当天新增改成`sources?today_counts=true`一次桶归并，旧N次完整metrics请求链已移除。
- status、metrics、sources拆成独立Query，不再三者一起成功/失败。
- cadence通过“采集诊断”开关按需请求，默认状态接口不执行它。
- 轮询状态主要从独立采集面`acquisition.sqlite3`读取，原始采集与业务消息接纳分开；部分历史轮询重建已退休。

当前生产轻量分解：源卡片整批约0.36–0.38秒，基础状态聚合约0.011–0.016秒，metrics计算本身约0.024–0.027秒，批量当天计数约0.006秒。后端DTO验证在约0.026–0.028秒失败。生产metrics日志通常几十毫秒即`INTERNAL_ERROR`，不是3秒SQL超时。

采样时I/O PSI avg10/60/300均为0，load约7–8，与上次严重I/O等待快照不同。这是本次时点的状态，不承诺全天延迟。没有为本次报告重新做全页并发压测。

## 3. 三项问题的具体原因

### 3.1 “自动刷新”有两种机制，不是同一个问题

**每分钟：统计快照更新。** `MessageStream`的`useMinuteReads()`只refetch minuteContext；但status/metrics/sources的queryKey都包含新的`minuteView`，且没有沿用上次成功数据的配置。每次新context到达，三个新Query暂时没有data：KPI变成横线、`sources=[]`让源卡片暂时消失；metrics很快500，错误提示又出现。提示插入/消失还会改变消息区上方高度。

这个路径本身没有主动刷新消息baseline，不应一概说成“消息列表每分钟重新查询”。采样访问日志有36次MINUTE context、36次metrics 500，但消息列表仅3次200，证明两者频率不同。

**约14分钟：消息视图自动续期。** 页面同时调用两个`usePageContext()`：选定周期的KPI context，以及ALL消息流context。每个hook都调用`useViewRenewal()`。生产view有效期15分钟，计时器在过期前60秒调用`refetch()`，该函数除了更换context，还会：

1. 为同页其他context也申请新视图；
2. 遍历所有活跃分页Query；
3. 新视图只读第一页，重建`pages=[firstPage]`、`pageParams=[undefined]`；
4. 替换context和查询缓存，消息path/view随之切换。

因此已“加载更多”的尾页会被丢弃，尾页卡片可能卸载，展开状态也可能丢失，列表高度减少影响滚动。两个context各自有独立续期锁，同一时刻可能重复调用这套逻辑。

生产Web日志（UTC，转北京时间需+8小时）：

```text
20:01:32.338  COHR /read-context refresh=MANUAL period=ALL
20:01:32.392  COHR /read-context refresh=MANUAL period=CURRENT_TRADING_DAY
20:01:32.441  COHR /read-context refresh=MANUAL period=CURRENT_TRADING_DAY
20:01:32.503  COHR /read-context refresh=MANUAL period=ALL
20:01:32.766  COHR /messages 新view A
20:01:32.891  COHR /messages 新view B
```

SSE另外还会在视图过期、语义日切换、baseline失效、客户端校验失败时调用reset；reset现在同样走`feed.refetch()`。没有发现分钟计时器调用`location.reload()`的代码，访问记录也不支持周期性浏览器文档reload。用户感受到的“整页刷新”有真实状态替换原因，但应修Query/续期，而不是去调浏览器刷新间隔。

### 3.2 统计失败和“旧口径”

完整错误是：

```text
ValueError: BusMetrics/new_article_body: additionalProperties
```

`body_cohort()`始终返回`initial={full,pending,failed,unknown,ratio}`。TypeScript契约和前端生成schema已包含它，**生产后端生成的ArticleBodyCohort schema没有它**，且`additionalProperties=false`。即便当日一个样本也没有，这个字段仍被返回，因而稳定500。

生成脚本只强制替换选定的根类型；遍历依赖时，若`backend.$defs[name]`已存在就直接返回。虽然`BusMetrics`根更新了，已存在的`ArticleBodyCohort`依赖没有更新。这是契约生成链路的缺陷，不能用放开额外字段校验掩盖。后续仅运行当前schema命令也未必能修好，必须处理受影响依赖同步。

真实数据示例：BE当日`published_revisions.current=2`，仍因正文子对象契约错误整份返回失败。INTC/MU当日没有新增时，`counts()`已返回数值0，但组装Metric用了`current[i] is not None and (i or current[i])`，把0转换成NOT_RECORDED；这是另一处独立问题。

“旧口径”标签由`metrics?.new_article_body ? 新文章正文成功率 : 正文补全成功率（旧口径）`决定。在metrics错误、变量未取得时也走后者。因此现在这个标签既可能表示真正的旧数据备用路径，也可能仅仅表示接口失败，具有误导性。错误提示说根据请求编号检查，但该页面Inline Notice只输出error.message，没有像通用Module那样输出可复制的requestId。

### 3.3 2/0、休眠和两个例外源

状态聚合只计：启用绑定中`status=succeeded`的normal，以及`partial/failed`的abnormal；没有total/sleeping字段。采集面`binding_states()`又只取`units.required=1`。休市时worker将不再应执行的单元标成`required=-1`，live映射便不包含这些源；API以never_polled替换旧poll状态，最近成功也被抹成未知。

| 标的 | 真实有效绑定 | live normal/abnormal | 其他绑定为何没计入 |
| --- | ---: | --- | --- |
| COHR | 32，均启用 | 采集面继续运行IR和SEC两类源 | 休市移出必需单元，不等于没有绑定或从未轮询 |
| INTC | 31 | 2/0 | 29类源休眠，不应被遗漏 |
| MU | 30 | 2/0 | 28类源休眠，不应被遗漏 |
| BE | 33 | 2/0 | 31类源休眠，不应被遗漏 |

INTC Yahoo三个主要单元的旧head仍保存着10月31日05:59 UTC的成功时点，只是`required=-1`后读取过滤掉了，不是数据已被删除。

`Configuration.allowed()`明确允许`corporate_ir_press_releases`和`sec_edgar`不受正常交易时段realtime门禁限制；临时采集override虽曾启用，但记录已过期，不能据此解释当前两个例外。两个源有采样时点前几秒到几十秒的真实成功记录。

页面`source_status()`却只豁免IR。在实际CLOSED_SLEEP context下，COHR返回IR=SCHEDULED、SEC=CLOSED_SLEEP，后者实际仍在采集，是显示规则与调度规则不一致。**要先决定保留IR/SEC休市采集例外，还是修改采集政策；修源数量显示不应顺便擅自停止它们。** 若保留例外，COHR应如实显示“已绑定32｜运行2｜休眠30”，而不是“全部休眠”或“2/0代表只有两个源”。

## 4. 当前页面各指标到底计算什么

### 顶层KPI

| 当前显示 | 计算口径 | 与目标的差异 |
| --- | --- | --- |
| 连续运行时长 | ticker处于RUNNING且native监控status=running时，view.as_of减continuous_run_started_at | 用户新清单不再需要，可移除，不能把休市理解为ticker已停止 |
| 周期新增 | 已正式发布MessageSummary的`bus_messages`桶，按Stream首次发布时间对应的语义日计；不是采集列表条数，也不把正文更新当一条新消息。按周期、来源/类型/路由筛选，有上期对比 | 口径可保留；修整份500及已知0的编码 |
| 新文章正文成功率 | 当前ticker关联的canonical文章，按全局first_observed_at进入周期，去alias重复，排除legacy/ineligible；FULL/SHORT_FULL数除以全文合格候选总数，分母包括pending/failed；不要求文章最终发布 | 有价值的文章口径。不是网络请求成功率；目前未按路由筛选，且initial首次结果只返回、未被UI采用 |
| 正文补全成功率（旧） | 关联已发布消息的旧body_attempt中，成功尝试数/尝试总数；按尝试发生日统计，可包含重试，同篇不必只算一次；未发布失败样本可能漏在外面 | 与文章率根本不同；新live路径不能依赖这套旧尝试账得到完整率，不宜继续作为同名无提示fallback |
| 数据源状态normal/abnormal | 当前启用且有live状态的源成功/失败数量 | 没有已绑定总量、休眠、停用、未知，不能表达全部源状态 |
| 启用源最近耗时均值 | 每个源只取最近一次采集状态latency；多单元源先平均其最近latency，再在源之间平均 | 不是近20次，也不是发现→正文→去重/准入→Stream的端到端耗时 |
| 采集诊断后的实际间隔/P95 | 近30分钟有证明的新鲜列表时间之间的间隔分布 | P95是“间隔的P95”，不是端到端耗时P95 |
| 诊断兑现/缺轮/延期/失败 | cadence按排班、due和网络证明推导expected/fresh/pending等；当前coverage=fresh/settled，不是无条件fresh/expected | 不与用户的x/20完全相同；UI未充分展示分母变化 |
| 候选待接纳/异常 | 旧摘要按capture里剩余候选求和；新采集面构造的listing receipts `message_count=0`且status=COMPLETED | 不能当成当前业务接纳/正文积压总量，可能一直不显示 |

### 右侧消息源卡片

| 当前显示 | 当前实现 | 限制 |
| --- | --- | --- |
| 最近成功 | 对当前必需采集单元，取各单元last_success_at的最早值，全部都有成功记录才给值 | 表达“最落后的必需单元”，不是简单最新一次；休眠被过滤后值丢失 |
| 当天新增 | 一次按source_id归并当天bus_messages桶，未匹配的源在桶可用时填已知0 | 已经批量实现，应保留；“当天”为美东02:00语义日，不是北京时间自然日 |
| 采集耗时/旧轮询耗时 | 当前源各必需单元最近latency平均，秒 | 只是一批最近采集任务的耗时，不含全部业务后处理，也不是近20次 |
| 采集距今 | 各必需单元最近capture时点中最早的一个距当前多久；有单元从未capture则未知 | 是新鲜度，不是实际刷新间隔 |
| 账本观察 | acquisition配置快照的at | 不等于该源最近成功或最近发布时点 |
| 实际间隔/周期兑现 | 仅采集诊断开关打开后返回，近30分钟cadence | 非固定近20轮；目前“间隔P95”只在悬浮信息中 |
| 待覆盖查询/待核验时间 | 原poll checkpoint及projection_counts | 新采集head不带旧checkpoint信息时可能为0/缺省，不能当全局积压 |
| 状态/倒计时/错误 | last poll结果与native绑定/交易时段共同决定 | last结果与当前是否应轮询是不同维度；当前SEC休眠标记存在冲突 |

右侧目前**没有**近20轮端到端均值、近20轮端到端P95，也没有近20轮正文补全成功率。

## 5. 目标指标建议定义：先消除三个歧义

### 5.1 “端到端轮询耗时”的起终点

按用户要求：起点是**该轮真正发现新消息的时点**，终点是消息完成必要的去重、正文补全/准入后**正式进入Message Bus Stream**的时点。

- 不从新闻在外部网站的publication time算起；也不从API点击时间算起。
- 终点不用采集cycle.finished_at、capture.accepted_at或Read投影完成时间替代；它们分别早于业务发布或晚于发布，口径不同。
- 一轮多条新消息时，轮次耗时建议取该轮所有“应发布消息”完成首次发布的最大延迟，避免一轮几十条消息的源在均值里被重复加权。
- 精确重复、策略明确排除/不应发布项不等待一个不存在的Stream终点；空轮询没有新消息，其端到端耗时记为“无样本”，不能记0。空轮询仍可以算轮询成功，进入覆盖率。
- 待完成/失败轮次没有完整耗时，单独显示其数量，不能假装0秒，也不能用只统计成功耗时证明整条管线健康。

如果选择按每条新消息取平均，实现会更简单，但应该叫“消息端到端耗时”，不能在未说明时当作每轮平均。用户目标要求轮次口径，本方案按轮次定义。

### 5.2 “20次”的分母要含漏轮

不能查询最近20条成功记录再计算成功率，这会天然接近100%。应先按排班产生应执行机会，再关联实际执行证明。延期、失败、根本没发出的机会都不能从分母消失；尚未到结算时限的正在执行机会可单列，展示分母时注明。

用户顶层的“20次预期、实际x次”按`x/20`理解；若顶层受周期选择器控制，则实际分母是所选周期内应轮询次数，不必固定20。右侧近20次使用最近20个已到结算时限的应执行机会，不跨配置generation混算。

当前来源可能有多个必需采集单元，如Yahoo HEAD/GraphQL/浏览器补齐。这些已被拆成独立调度机会，不能只用RSS成功替代全部源完成。简单实现建议按真实必需单元机会累计，向上按sum(success)/sum(expected)加权，而不是平均各源百分比。若希望“一轮”必须表示整源所有单元一起完成，需要另外做源级轮次归并，复杂度更高；不能把当前独立单元收据直接声称是整源20次。报告建议沿用真实单元机会并明确这个口径。

成功标准建议用经适配器验证、实际拿到有效列表的证明；空或未变化列表也可以成功。**采集轮询覆盖率不等于新消息发布率或正文成功率。** 休眠/停用期间没有应执行机会，不记失败；零分母显示“无应执行轮次”，不是0%或100%。若要“严格按时兑现率”，需另定义时间容忍度，不能与最终成功覆盖率混名。

### 5.3 正文率用文章，不能用网页访问次数替代

建议统一为：同一统计窗口内发现、关联当前ticker/source、确实需要正文的canonical新文章，去重后`FULL/SHORT_FULL数量 ÷ eligible文章总数`。分母含待完成与失败，同时给出其数目；无文章时显示“无样本”。API已提供完整正文、成功复用已有正文可以计文章完成，明确不适用正文的项目排除。

重试多次仍是一篇，正文更新/周期复检不作为新文章再入分母；若关心首次尝试成功率，现有initial字段可用于独立诊断，不应悄悄替代用户这次的正文补全指标。右侧“近20次正文率”是最近20个轮询机会所发现的文章cohort，不是最近20次HTTP请求的成功率。

## 6. 目标与当前实现的逐项对应

| 位置/目标 | 实现程度 | 建议落地 |
| --- | --- | --- |
| 顶层周期新增 | 已有 | 保持Stream语义日口径，修schema和0值；周期选择仅刷新指标 |
| 顶层消息源状态 | 部分 | 增加bound_total、enabled、running、sleeping、disabled、unknown/error分项。按绑定计源，不按unit数计源；休眠总量不能丢 |
| 顶层正文补全成功率 | 有新文章cohort，接口故障 | 修依赖schema；保留canonical文章率并明确pending/无样本；不要错误时换成“旧口径”标签 |
| 顶层平均端到端轮询耗时 | 未实现 | 按所选统计周期的新消息轮次汇总有效端到端样本，显示样本/待完成数；不拿采集最近latency均值充当 |
| 顶层周期轮询覆盖率 | 有近似cadence能力 | 复用排班和有效列表证明，改为用户选定的success/expected分母；20次示例应准确显示x/20。若固定近20轮，名称/选择器范围明确区分 |
| 卡片最近成功 | 已有但休眠丢失 | 保留最近一次有效配置的历史head，即使当前不调度也能显示；保持多必需单元最落后成功时点语义 |
| 卡片当天新增 | 已有且批量 | 继续使用一次桶归并，不恢复逐卡metrics请求 |
| 卡片平均端到端耗时（近20次） | 未实现 | 从最近20个有效轮次窗口内的完整端到端样本求均值；无新消息的空轮不填0 |
| 卡片P95（近20次） | 未实现，已有的是间隔P95 | 与上项同一耗时样本；最近秩法ceil(0.95*n)，20个样本取升序第19项，不足样本显示n |
| 卡片实际刷新间隔 | 有近30分钟诊断 | 改为真实新鲜列表到达时间的相邻间隔；20个间隔需要21个时点，不能把配置间隔或采集距今当成它。失败/漏轮造成长空窗应保留 |
| 卡片正文补全成功率（近20次） | 未实现 | 按该20轮发现的eligible文章汇总完成/失败/待完成，去重及别名规则与顶层一致 |

顶层周期新增按首次发布日，正文率按发现cohort，这两项天生可能不是同一批文章，不应承诺二者分母相等。消息路由不适用于还没发布的正文失败文章；建议路由只筛消息流，KPI至少对ticker、周期、来源/类型遵守明确一致范围，避免一边按路由筛、一边完全忽略却不说明。

## 7. 数据够不够，怎样简单实现

### 现成可复用的数据

- 采集面`cycles`：binding/unit、due/dispatch/finished、generation相关版本、valid listing证明、physical_run_id；`physical_cycles`有真实请求时点。
- `units.head_json`保存最近采集时点；休眠的旧head仍在。`cycles_schedule_ticker`、`cycles_unit_time`支持小范围取调度/最近轮次。
- 业务接纳的`pipeline_trace`已带capture_id、listing_received_at、capture_committed_at；后续message metadata还带decision_ready_at。
- `live_articles`有首次发现/首次结果、当前正文结果、fetch/full/cache计数；`live_origins`有来源/单元/目标关联；`live_deliveries`有ticker准入、standard_id和consumable_at。
- Stream item/member有正式发布时间与消息身份；Read MessageSummary已有collected_at、normalized_at、stream_published_at。当天新增仍可直接用原桶。

正常样本能串起链路：INTC某新消息从05:34:02.721739 UTC发现，到05:34:06.556888正式发布，相差3.835149秒；COHR某新消息约40.516秒。当前页面的0.022秒等采集均值不能代替这些总耗时。

### 不能直接拿历史字段盲算

保留消息的collected_at可能沿用更早业务版本，而当前metadata trace已被正文更新覆盖。INTC一个样本collected_at为10月24日，当前trace.listing_received_at为10月31日，分类为BUSINESS_UPDATE；用旧collected_at减首次Stream发布时间会出现约604,725秒。也有真实等待较长的NEW_ARTICLE，不能一律把大值截掉。

因此应冻结同一个新消息/轮次的发现锚点与首次发布终点，排除重复更新重计。当前trace有capture_id，但未在所有Read统计中形成完整、冻结的轮次→新文章→首次发布闭环。不能把`accepted_at`（只是接纳完成）当成发布完成。历史轮询明细48小时后转为小时汇总，精确的近20个间隔/P95不能从小时P95再合并恢复。

### 推荐的最小实施方式

1. **先修确定故障**：同步ArticleBodyCohort依赖schema；known zero正常显示；稳定Query旧数据显示与消息视图续期；绑定总量/休眠/例外状态一致。这个阶段无需新增指标库。
2. **采集统计取最近小窗口**：复用采集面的排班/真实收据，批量计算最近20机会、成功/失败/漏轮和最近21个fresh时间。不要每个卡片各发请求，也不要重新扫描业务MVCC历史。休市保留最后有效样本与时点。
3. **补少量首次链路事实**：在已有new_article/首次发布路径上，按稳定文章+绑定/轮次身份保留首次发现、cycle/capture关联和首次发布锚点；复用已有origin/delivery事实可表达的部分，缺失的只补必要标量。正文更新不覆盖首次锚点，同ID重试不重复加样本。空轮、过滤、重复、失败/pending分开处理。
4. **固定小摘要**：每绑定保留最近20轮所需的紧凑摘要/样本和21个刷新时点；所选周期均值用sum/count及原有桶机制累计。P95只对卡片真实小样本排序，不用“P95的平均”。新消息或首次正文结果/发布提交时更新，避免每秒全量投影。优先复用现有store/读模型，不新开服务或长期全量轮询历史表。
5. **一个页面每分钟一批**：一次新的统计context，再读取小的KPI/全部源卡片摘要，可复用当前少数几个端点或合成一个摘要响应；一次取全，不按源扇出。页面隐藏时暂停，窗口重聚焦不立即无条件重复，复用最近一分钟成功结果；旧请求取消或按generation丢弃过时响应。

如果第一阶段只复用现成时间戳，可先提供明确标注的“已发布消息端到端耗时”来验证数据，但它不等于用户要求的精确“最近20轮”口径，不应把这个简化版写成完整交付。建议从新数据开始积累严格近20轮，旧样本不足明确显示，无需复杂历史回填。

## 8. 具体刷新修复边界

- 保留每分钟统计刷新，但Query使用稳定模块key和最后成功数据；新context暂存，新的模块响应成功后原位换数值，不先把旧data设为undefined。失败保留旧数据，并显示一条稳定、可复制请求编号的错误状态。
- KPI刷新不改变消息path/view，不执行消息分页查询，不清空展开、加载页和滚动锚点。秒级倒计时继续纯本地变化，不产生服务器请求。
- 页面仅有一个消息视图续期协调者，去掉KPI/ALL两个context同时触发整页refetch的路径。续期不是用户手动“从头刷新”。
- 真实scope过期必须取得新baseline，不能继续使用已失效旧cursor；后台取得新第一页后按消息ID合并已加载卡片，保留本地阅读缓冲、展开ID及可见锚点，平滑切换SSE。旧视图页游标不能挪用给新视图；后续续页需从新scope取得有效游标并去重。若实现复杂，可暂时保留阅读缓冲，并给出“有新消息”入口让用户选择回到顶部，不能静默丢掉尾页。
- 新消息到达而用户正在下方阅读时，累计更新提示，不强制滚顶或让顶部插入改变当前阅读锚点。旧内容真实删除或权限变化另按现有语义处理，不能用阅读缓冲绕过权限。

不建议：只加长view TTL来延后问题、停掉SSE改每分钟整页拉取、放宽JSON schema、把未知比率显示0、把休眠源当失败、把两个例外源未经决策直接停掉。

## 9. 实施后的验收要求

1. 真实BusMetrics完整响应通过后端与前端同一契约，含initial有样本/无样本、已知0、无分母；回归必须覆盖生成脚本对已有依赖的更新，不能仅测body_cohort内部dict。
2. 当前用户页COHR及INTC/MU/BE可显示真实绑定总数。休市保留最近成功，运行/休眠计数与采集allowed规则一致；IR/SEC政策按最终选择验收。
3. 加载至少三页消息、展开尾页一篇并滚到中段：跨3个分钟刷新、窗口隐藏/显示、14分钟续期、统计500、SSE断线恢复后，阅读卡片、展开和锚点仍保留。记录实际DOM/滚动变化，而非仅构建通过。
4. 20次应轮询、成功17次、失败/未发出3次必须17/20；空但有效的列表算成功；无应轮询不造0%/100%。覆盖多单元、generation变化、休市、暂未结算场景。
5. 单轮多文章、正文失败后恢复、同篇跨源复用、bootstrap/正文更新、buffered首次发布、缺trace均有明确处理；端到端终点是真Stream发布，不是capture ACK。
6. UI只按一分钟读取小摘要，卡片无N次请求；实测20样本查询有索引边界，无全库/全历史扫描。1262px、1559px检查五个KPI和新卡片表头/字段对齐与稳定高度。

## 10. 证据与代码索引

证据目录：`eval/message_bus_page_review_20261101/`。

- `host.json`：当前正式镜像/环境、PSI、API失败样本。
- `current.json`、`current_probe.py`：INTC/MU/BE取数与正式DTO校验、绑定数、采集状态和schema内容，关键运行源码哈希。纯读取，不创建view/token、不初始化库。
- `traffic.json`、`traffic_remote.py`：生产Web前两小时采样；36次统计500、分钟刷新与双续期记录。日志计数不是全站成功率。
- `details.json`、`details_probe.py`：实际持久化CLOSED_SLEEP view、COHR32绑定、休眠历史head、消息时间样本、近20轮收据。
- `outliers.json`：旧collected_at与当前BUSINESS_UPDATE trace不一致的反例。

主要代码：

| 文件 | 检查点 |
| --- | --- |
| `frontend/v2/src/pages/message-bus.tsx` | minuteView进入各Query key；旧口径fallback；源卡片与错误Notice |
| `frontend/v2/src/core/page-query.ts` | useViewRenewal过期前60秒；refetch只预装第一页；双context协调 |
| `frontend/v2/src/core/{paged-query,live-query,stream}.ts` | 分页key、分钟调度、SSE reset |
| `src/doxagent/api_v2/{bus,bus_metrics,news_timeliness}.py` | 绑定/live状态、旧桶/新cohort、initial与cadence |
| `frontend/v2/scripts/generate-schema.mjs` | 已存在依赖直接跳过，后端ArticleBodyCohort未替换 |
| `src/doxagent/api_v2/wire_schema.json` | 后端拒绝initial；对照TS及前端wire-schema |
| `src/doxagent/message_bus_v2/collection_plane/{config,worker,observability,store,maintenance}.py` | IR/SEC例外、required=-1休眠、只读required=1、收据与48小时归档 |
| `src/doxagent/message_bus_v2/{live_repository,service}.py`、`src/doxagent/v2_read/{projectors,bus_aggregates}.py` | 首次链路、业务发布、正文结果和现有消息/尝试桶 |

本报告提供可实施边界，不表示这些新指标或刷新修复已经开发上线。
