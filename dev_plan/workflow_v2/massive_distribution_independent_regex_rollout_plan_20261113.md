# 五个 Newswire Massive 源独立正则与正式订阅上线方案

日期：2026-11-13，北京时间。本轮只读勘察和制定方案；仅新增本文，未改产品代码、词库、源配置、订阅或生产环境，未触发抓取、登录、模型请求、构建或发布。

## 1. 结论与完成度

建议复用现有 Collector → Live 正文/去重 → 分发 → 正式 Bus 链路，增加一个 `massive_l2` 配置字段、一个 Massive 专用判定入口，并在原统一监测词页面复用规则编辑器。无需新增消息源 ID、采集器、队列、数据库词库表或模型服务。

范围固定为五源：`businesswire_massive`、`prnewswire_massive`、`globenewswire_massive`、`access_newswire_massive`、`tmx_newsfile_massive`；九 ticker：INTC、MU、BE、COHR、NBIS、CEG、RKLB、SNOW、ONDS。最终目标为 **45 个真实正式绑定、9 套独立 Massive 正则**。Bloomberg 和 IONQ 不在本次正式化范围，保留其现状。

现状是“采集及测试底座已有、正式接入未完成”，不能把测试投递算作上线完成：

|验收维度|当前完成度|剩余工作|
|---|---|---|
|源定义、列表发现、共享采集|5/5 已存在且启用，均有近期新文章|保留现有物理端点和主备配置，正式切换检查点与调度|
|正文处理和质量门槛|已有；近24小时仅3/5持续取得完整正文|Business Wire、TMX Newsfile 有现实可用性缺口|
|离线候选与回放证据|9/9 有可选候选|合并两轮选版；区分样本成绩和上线验收|
|Massive 独立配置与分流|0/9，仍共用普通 L2|新增独立字段、校验和路由，禁止回落普通 L2|
|正式订阅|0/45|测试转正式后通过既有服务建立真实绑定|
|统一监测词页面 Massive 栏目|0/1|复用编辑、校验、CAS 保存及消费者展示|
|五源正式发布闭环|0/5；当前正式 raw/standard 均为0|新消息完整正文、独立正则、正式发布、前端及下游逐层验证|

上述是可核实的分项完成度，不用任意加权百分比掩盖“底座已存在、正式链路尚未验收”的区别。

**两项需直接说明的风险：**最优候选不代表九套都达到80%准召；此外，源转正式不会自动修复正文不可用。不能在改完词库和订阅后宣称五源均已完成业务验收。

## 2. 勘察基线与远端证据

### 2.1 发布基线必须先统一

- 本工作区：`C:/Users/WEIXUANXIE/Desktop/DoxAgent`，HEAD `0d2b7b5fa8f1b17f9cab9010fb45baa8e4c3c2ea`，存在其他会话改动。
- 用户提供的规则位于另一工作树 `C:/DoxMetrics`，分支 `codex/source-catalog-refinement`，HEAD `5dad67937f5e381b598eceb6535fa52a2a1a06b5`，亦有未提交改动。两者共享 Git common dir，不能把它们当两个互不相关的产品，也不能覆盖另一工作树。
- `source_catalog.json` 及目录实现存在于 DoxMetrics 工作树，当前 DoxAgent 工作区缺少该文件；统一监测词页面与控制层则两边均存在，尚无 Massive 专用字段。
- 远端宿主 checkout HEAD 为 `0d2b7b5f…`，但运行的 API 镜像 revision 标签为 `edf2159fab7a33349fdcfd5275fbcf27117c81d5`，API 容器内已有分类目录文件。**宿主 HEAD 不能代表全部运行镜像。**
- 后续实施先从已发布功能的实际基线整合目录/退役源相关变更，再在隔离的 `codex/` 工作分支开发。不得从缺少目录功能的旧工作区直接重建 API/Web，造成目录回退；不得擅自重置、提交或覆盖其他会话草稿。

### 2.2 生产只读快照

主快照 UTC `2026-11-12 19:30:45`，即北京时间 **11月13日03:30:45**；正文与端点补查约03:31–03:34。使用容器内 SQLite `mode=ro` 和 `query_only`，读取标量、配置白名单及必要错误码。未调用业务 Repository 写入口。数字是保留窗口快照，不是永久累计值或生产准确率。

五源均 `enabled=true`、`acquisition_mode=massive_distribution`、`delivery_mode=test`、`jev_enabled=false`、`require_complete_body=true`，目标间隔配置30秒；虚拟测试目标均为十 ticker（含IONQ）。五源真实绑定全部为空，正式 raw/standard 消息全部为0。

|源|当前源版本|保留文章数|FULL/SHORT_FULL|测试发布记录|近24h新文章 / 已取得完整正文|
|---|---:|---:|---:|---:|---:|
|Business Wire|2|5,743|14|4|688 / 1|
|PR Newswire|2|6,795|4,544|2,436|1,163 / 703|
|GlobeNewswire|2|7,906|7,674|4,844|1,114 / 1,071|
|ACCESS Newswire|2|1,187|1,141|601|189 / 167|
|TMX Newsfile|3|1,409|1,117|357|190 / 0|

测试发布记录含内容/词库版本，不能直接除以文章数计算通过率；近24h分母含仍待处理或不可用文章，不是已完成请求的成功率。

- Business Wire 近24h的主要正文错误为 `http_401`（补查时490篇）；另有 `deadline_exceeded`、`transport_timeout` 和183篇 `test_publisher_cooldown` 等待。最近一次完整正文检查时间为北京时间11月12日05:59:57。
- TMX Newsfile 近24h的190篇均为 `UNAVAILABLE/render_required`；最近完整正文检查时间为北京时间11月11日17:10:57。历史1,117篇 FULL 不能证明今天仍可正常取正文。
- 当前活跃端点：BW/PRN/ACCESS 为 HTTP listing；GlobeNewswire 为 HTTP listing **与 RSS all 并行**；TMX 为 RSS all。`required=-1` 的历史退休单位不算当前请求。每单位虽有关联十个测试目标，采集仍按共享物理工作合并，不能解释为十份下载。
- 正式化后使用真实绑定、ticker运行状态和既有交易日/运行时段；测试模式的日历豁免不能顺带移植到生产。当前九 ticker 均为 `running`。

## 3. 九个 ticker 的拟采用版本

将两轮全部完整成对结果合并，按既有 SOP 的 `(调试P+调试R+验证P+验证R)/4` 选择最高版本；平手比较四项最小值，再取较早版本。此次用户指定“最优的那一版”，不机械采用最后一版。第二轮简化版中，只有 CEG 取代首轮入选版。

路径缩写：

- A：`C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/massive_distribution_regex_tuning_20261112`
- B：`C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/massive_distribution_regex_round2_20261112`

候选均位于 `根目录/TICKER/regex/版本/candidate.json`。下列短哈希是原候选文件 SHA-256 前12位，实施时记录全哈希。

|Ticker|根 / 版本|调试 P / R|旧验证 P / R|四项均分|原候选短哈希|
|---|---|---:|---:|---:|---|
|INTC|A / attempt-02-overfit|86.67 / 86.67|89.66 / 86.67|87.41%|`0c05ce0b406c`|
|MU|A / attempt-03-overfit|86.96 / 100|90.91 / 100|94.47%|`fbe34dbae186`|
|BE|A / attempt-02-overfit|92.59 / 92.59|68.75 / 91.67|86.40%|`a8a8489fd8f9`|
|COHR|A / attempt-02|100 / 100|61.54 / 80|85.38%|`e9405d9e67d5`|
|NBIS|A / attempt-03-overfit|90.91 / 86.96|100 / 72.22|87.52%|`9af0886415f2`|
|CEG|B / attempt-01|100 / 95|86.67 / 86.67|92.08%|`2869eec3ad05`|
|RKLB|A / attempt-02-overfit|96.15 / 100|93.10 / 100|97.31%|`caa6e47a9a33`|
|SNOW|A / attempt-02-overfit|83.33 / 90.91|86.67 / 92.86|88.44%|`ed6a01f648fa`|
|ONDS|A / attempt-02-overfit|92.86 / 81.25|93.75 / 93.75|90.40%|`a15dbecb0847`|

P/R栏单位均为%。六套两集四项严格>80%；BE、COHR、NBIS仍未达到全部门槛。尤其COHR验证精度61.54%，不能包装成高准确上线方案。按本次要求把这些作为“历史最优的初始生产候选”，同步保留未达标状态；不擅自用最窄但严重漏召的简化版替代。

原评测来自旧正则阳性池并做过标题富集，召回是原池内相关消息保留率，精度不是自然生产精度；旧验证已用于选版，不能恢复成独立盲测。

**INTC预发布勘误：**入选原稿 `intel_manufacturing_fact` 的一个分支缺少 Intel 后的词边界，已实际误命中 `Artificial Intelligence`。不能带着这个明确缺陷直接发布。实施时仅做词边界勘误，保留原稿、差异、新哈希和两集回放；不能沿用原哈希或把勘误版冒充原已评分文件。本轮不重新开展按验证坏例追分的调词。

导入时仅取候选 `l2` 写入新 `massive_l2`，**不能 PUT 整份旧候选**，以免覆盖现在的普通L2、L1、definition、X账号或使用过时的 `expected_revision`。当前生产词库revision为：INTC5、MU7、BE4、COHR3、NBIS3、CEG3、RKLB4、SNOW3、ONDS5；正式执行重新读取最新值。

## 4. 最小配置和判定模块改动

### 4.1 配置：同一 ticker 词库内分开存储

扩展 `TickerMonitoringTerms` 和 `MonitoringTermsValue`：

```text
l1_concepts       现有搜索词
l2                现有普通分发正则
definition        现有 Decision Model 定义
x_accounts        现有官方账号
massive_l2        新增；language → 现有 L2Language/groups 结构
```

- `massive_l2` 对旧记录缺省为空，兼容历史JSON；继续复用 `ticker_monitoring_terms`、版本修订表、64KiB限制、CAS、ETag、幂等和CLI校验。无需SQL新表或另起词库服务。
- Massive规则缺失、为空或语言不足时进入 `CONFIG_INCOMPLETE`，给出 `MASSIVE_TERMS_MISSING` 等明确原因；禁止自动使用 `l2`，禁止把配置缺失写成新闻不相关。
- 普通来源继续只使用 `l2/definition`；普通配置不因 Massive 存在而强制填写 Massive规则。创建/启用 Massive绑定时校验其 `massive_l2`，仅有普通词库头不够。
- 分开计算普通与Massive绑定的语言要求。Massive不生成L1搜索词，也不要求固定三语。暴露 `massive_required_languages` / `massive_language_requirements`，现有普通语言字段只反映普通消费者；无订阅时允许先预配置规则。
- 新字段加到控制层读取白名单，否则 `_snapshot()` 会丢失它。旧客户端省略新字段时保留已存值；显式清空是另一操作，仍有启用Massive绑定时拒绝清空，防止普通词编辑误擦除新规则。
- 继续使用统一配置revision，无需另建scope版本表。只改某栏也会推进总体revision，现有配置缓存会刷新，但普通表达式、搜索词和模型定义的内容及判定保持不变。

### 4.2 独立入口复用原生匹配器

建议 `relevance.py` 提取接受规则map的薄函数，保留当前 `regex_relevant()` 普通入口；新增 `massive_relevance.py` 仅调用同一匹配原语并取 `massive_l2`。保留 NFC/空白归一化、字段匹配、20ms超时、group OR、组内any/all/none与语言＋en并集。

```mermaid
flowchart LR
  A[现有 Collector / Live 完整正文] --> B{acquisition_mode}
  B -->|massive_distribution| C[massive_l2 · 专用正则入口]
  B -->|by_distribution| D[l2 + 现有 Decision Model]
  C --> E[既有正式发布 / 去重 / Message Bus]
  D --> E
```

关键改动：

1. `LiveDeliveryWorker.process()` 在任何缓存正例放行之前确定所用scope和配置完整性。Massive只调用专用入口，阴性直接IRRELEVANT，不调用模型。
2. 旧 `DistributionWorker` 的兼容处理路径同步分流，并在Massive下硬性禁用模型，不能只依赖 `jev_enabled=false`。防止重启恢复/批处理路径绕回普通词库。
3. `decision_digest + terms_revision` 的正例缓存必须属于正确scope。首次独立化提升ticker词库revision，使旧统一L2判定失效；记录判定scope与词库revision于既有决策/消息metadata，便于对账，不另建逐次遥测系统。
4. Massive在正式和测试模式均按 `message.metadata.language → source.content_language → en` 取入口语言，再执行en回退；普通正式来源保留原有语言行为。当前测试转正式会从文章语言切为源语言，需避免这一差异改变离线回放语义。
5. `raw_id`、已发布去重等快路径不能把普通来源的命中当成Massive的首次相关性通过；重复消息继续按原发布规则去重，不重复触发下游。
6. `refresh_decisions()`、`decision()`、发布前revision检查继续使用原有机制；规则修复只恢复必要的未完成决定，不自动重放所有历史终态、不重新下载BODY。

## 5. 五源测试转正式与45个订阅

### 5.1 复用源身份和绑定服务

保持五个source_id和 `massive_distribution` 类型，不创建“正式版”重复源，也不改成普通By Distribution。

现有 `MessageBusV2Service.configure_binding()` 已支持Massive，但明确拒绝对test源建立真实绑定；V2可添加入口又仅接受 `kind=api`，HTTP listing源多为crawler。因此首次45绑定由受控上线脚本复用原服务创建；不要直接INSERT数据库，也无需为上线临时开发一个新的订阅后台。

配置展示的 `monitoring_terms_usage` 和统一词消费者列表必须识别Massive。现有契约的acquisition_mode枚举已经含Massive，缺的是控制层筛选/状态逻辑，不能仅改TypeScript枚举交差。若本期要允许用户后续自行新增Massive绑定，现有添加入口只窄幅放行已登记、启用、正式的Massive源，不开放任意crawler或test源；不新增重复API。已有真实绑定的编辑/暂停继续复用现有页面。

### 5.2 切换顺序与隔离

1. 备份五源当前配置及版本、九ticker词库头/JSON/哈希、真实绑定清单、测试/正式计数与采集单位头；记录 `activation_at`。备份用于回滚，不复制数据库遗漏native-files后声称完整备份。
2. 先发布兼容代码，使所有实际读取新词库JSON的角色支持 `massive_l2`；随后CAS导入9套规则，普通字段逐项校验不变。缺Massive配置时失败关闭。
3. 对五源进行短暂源级暂停，清楚区分测试单位 `TEST:WIRE:*` 与正式 `WIRE:*`；保留现有独立端点重试、健康和共享工作合并。不要复制测试checkpoint为正式bootstrap已完成，也不要按9个ticker重复抓9份列表。
4. 在暂停状态将source更新为production，建立九个真实绑定并验证规则语言，再启用源；每源9个，合计45个。test_tickers清空仅限这五源，IONQ不产生真实绑定。不要通过SYSTEM身份绕过校验后直接视为订阅成功。
5. 默认只处理正式激活后新发现且符合既有admission窗口的消息。首次正式bootstrap检查近期窗口、已有checkpoint和资源量，防止全历史重抓/发布；回补历史如有需要应另开明确任务。
6. 旧outbox payload、`test_`文章键和文章持久化delivery_mode保持test；不能批量改成production或移动旧test_publications。当前实现按行冻结投递模式，保留并测试这一隔离。已在途旧测试任务按原模式收尾或因版本失效取消，不形成正式消息。
7. 先用PR Newswire/GlobeNewswire/ACCESS验证真实正式闭环，再处理BW/TMX的正文缺口；最终仍以五源45绑定为交付目标。正式模式下文章不完整就NO_PUBLIC_BODY，不退回标题筛选。

### 5.3 Business Wire / TMX 的有限修复边界

- Business Wire：区分列表成功与正文401/超时、测试publisher cooldown。核对现有原站HTTP、Reader及Site Access策略和预算，使用少量新鲜原文检验现有可行路径；不能以移除cooldown、提高并发或降低正文质量门槛代替修复访问问题。
- TMX：核对近期 `render_required` 是HTML/app-shell、提取器、原站路由变化还是既有渲染策略未命中，优先复用已有站点访问/补全能力。不能因为见到该错误就默认新建浏览器身份或把历史FULL当控制样本。
- 运行修复涉及真实正文策略变化时同步目录body主备标签；不重新设计列表采集，不改TMX已明确采用RSS的列表策略。
- 若现有权限/访问条件下仍不能取正文，可以完成独立模块、配置和绑定部署，但该源标记正文降级，**五源业务验收保持未完成**。记录已完成/待完成和真实阻碍，不伪造闭环或悄悄排除该源。

## 6. 前端与API调整

页面仍为“消息总线 → 配置 → 统一监测词”，与原搜索词、普通分发规则、Decision Model定义和X账号并列新增 **“Massive Distribution 监测词”**。

- 复用现有规则组编辑器、字段选择、literal/regex、any/all/none、大小写/整词开关及JSON模式；抽出一个接受scope/rules的轻量组件，避免复制一整页。
- 普通和Massive各自维护选中语言、JSON草稿及解析错误，不能共用单一 `jsonText` 后在切栏时覆盖另一份规则。表单与JSON继续编辑同一个配置草稿。
- 保留原revision/ETag、幂等、412冲突反馈、远端变化提示、草稿恢复、放弃修改。差异列表增加Massive字段；保存普通栏时Massive必须原样保留，反向亦然。
- Massive栏展示已订阅的五源消费者和所需语言；必要提示只保留“仅正则、不走Decision Model”。已有Jev区仍只反映普通分发源，不为Massive增加模型提示词或概率阈值控件。
- API保留既有GET、`/validate`、PUT监测词路由，扩展同一DTO；校验错误定位到 `/massive_l2/...`。不新增采集预览、实时全文测试或词表自动调参入口。
- 从 `dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts` 修改契约并走现有schema生成流程，核对 `frontend/v2/scripts/generate-schema.mjs` 的生成根类型、前后端wire schema和测试fixture；不要只手改生成JSON。
- 遵循 `frontend/v2/AGENTS.md` 的紧凑桌面设计，必要状态和错误可见，不增加装饰卡片、常驻环境说明或第二个全局导航。后续浏览器验收覆盖1262px与1559px。

## 7. 按用户提供的消息源规则同步维护

依据 `C:/DoxMetrics/docs/message-source-classification-maintenance.md`，含11月13日追加的目录整理。该文件是本任务选用的维护依据，不代表本轮已执行其中发布操作。

实施时同步 `src/doxagent/message_bus_v2/source_catalog.json`：

|项目|本次动作|
|---|---|
|身份与描述|保留正式source_id，描述写明机构及全市场企业稿范围；五源去掉“仅测试”措辞，Bloomberg保留原状态|
|适用范围|维持GLOBAL、sector_ids为空；不因为订阅九个ticker硬写九个标签，不把GLOBAL当自动订阅十个ticker|
|分发方式|继续来自SourceDefinition的massive_distribution，不维护第二套模式字段|
|列表主备|BW/PRN为HTTP主、RSS备用；ACCESS为HTTP；TMX为RSS；GlobeNewswire现为HTTP与RSS并行，应列两者primary|
|目录现存偏差|DoxMetrics当前GlobeNewswire条目把RSS写成fallback，与活跃两路并行单位不符；本次同步修正并增加覆盖断言，不为此重新抓源|
|正文主备|按实际修复后的HTTP/Reader/渲染配置记录；不把HTTP下载RSS算两种列表能力，不按Playwright客户端库名把Chrome/CDP标成托管浏览器|
|启停/订阅|从真实配置与TickerSourceBinding读取，正式转出“测试消息源”分区；不在分类JSON手写enabled或ticker数组|
|历史与退役|保留旧测试/消息历史；不恢复retired_sources中的旧栏目或GlobeNewswire Search，也不宣称Massive已完成对其覆盖替代|
|敏感信息|仅保留公共入口，不写API key、Cookie、签名参数、CDP地址|

静态登记/分类测试同步，核对卡片方法标签、分发方式、默认分区和九个真实订阅；不增加分类编辑后台、自动分类、定时同步、逐次路径遥测或历史标签回填。产品代码修改按AGENTS追加changelog。

## 8. 实施任务顺序及文件边界

|阶段|主要文件/能力|完成条件|
|---|---|---|
|P0 基线与词库清单|两工作树及生产角色镜像；两轮eval候选|发布代码基线明确、9套候选及哈希锁定、INTC勘误独立留痕|
|P1 配置与分流|`message_bus_v2/monitoring_terms.py`、`relevance.py`、新增薄模块、`live.py`、`distribution.py`|Massive只读新规则；普通正则/模型行为不变；旧JSON兼容|
|P2 控制与界面|`v2_control/monitoring_terms.py`、必要绑定usage逻辑、原API路由/契约、前端规则编辑器|单页可独立读改查存两套规则，冲突/旧客户端不会擦除另一套|
|P3 源与正文|既有SourceDefinition/绑定服务、已有Site Access/补全路径、source_catalog|五源配置就绪，BW/TMX缺口被解决或如实保留未验收状态|
|P4 窄发布与订阅|既有部署流程与一次性可预览的幂等配置脚本|9套新字段回读一致、45个真实绑定，测试历史未转正|
|P5 真实验收|正式raw/standard、stream/下游接纳、页面及源目录|新消息在正确词库revision下形成可追踪闭环，普通源无回退|

部署至少涉及API、Web、Message Bus、Collector。还应按实际启动命令核查是否有其他运行角色使用旧 `TickerMonitoringTerms` / MessageBus Scheduler 并解析同一JSON；读取者先兼容再导入新字段。不要假定其他角色天然可忽略新字段，也不要无差别重建全部服务。Projector/Site Access/Content Enrichment仅在其实际代码或配置变更时发布，保留已上线目录、采集修复及其他并行变更。

## 9. 必需验证、验收与回滚

### 9.1 离线/接口/浏览器

1. 模式隔离：同一文章让普通L2与Massive正则给出不同结果，证明两条路径各自使用正确词库；Massive缺配置不回落普通L2，误开jev_enabled也不能调用模型。覆盖Live及旧批处理恢复入口。
2. 配置兼容：旧JSON、动态语言、英文fallback、错误正则、空规则、64KiB上限、CAS/幂等、scope缓存失效和publish前revision变更；旧客户端省略字段时新规则保留。
3. 真实输入回放：九套入选版每套两集都跑新入口，除有记录的勘误外与旧回放逐条一致。另跑冻结的普通By Distribution/搜索对照，证明L1、普通L2、definition、X账号未变。不是新一轮按旧验证调参。
4. 生命周期：测试及生产文章键隔离、旧test payload晚到、禁止旧测试补发、仅9目标、重复/修订去重、禁用源/绑定、按交易日/窗口接纳、bootstrap不触发全历史洪峰。
5. 现有测试扩展：`tests/test_massive_distribution.py`、`test_message_bus_monitoring_terms.py`、`tests/v2_backend/test_monitoring_terms.py` / `test_bindings.py`；源目录/退役源测试、前端监测词unit与browser测试。按差异补回归，不额外搭测试harness。
6. 前端验证两种桌面宽度，检查普通/Massive栏切换、语言/JSON互不覆盖、保存回读、刷新恢复、412冲突和目录真实订阅标签；构建通过不等于页面验收。

### 9.2 生产业务验收

- 45/45绑定正确、启用、非删除，9/9 `massive_l2`归一化回读与拟部署值一致；五源production、禁用模型；IONQ和Bloomberg不被改动。
- 正式启用后先观察一个有新稿且在正常采集时段内的60分钟窗口，逐源记录列表新鲜度、完整正文、候选数、正则正/负和正式发布。窗口无相关新稿时记录“尚无真实样本”，不能用旧测试消息补数或制造发布。
- 每源至少有一条新鲜、完整且按新规则相关的真实消息完成该源的正式闭环，才能通过该源业务验收；不要求每个ticker×源都立即产生正例。未观察到合格样本则该维度保持待验证。
- 核对 raw/standard、发布流及下游接纳/去重证据，不能只看test_publications增长、HTTP200或容器healthy。按既有模块职责做质量/幂等，不借上线触发初始化或历史重研。
- BW/TMX若仍401/render_required，仅配置/订阅完成，正文与全链路验收未完成。保持NO_PUBLIC_BODY；交付时列清未完成、原因和下一步，不写“五源全部上线验收通过”。
- 正式发布后对实际正例与负例各做有限抽查，观察自然流量误召/漏召和负荷。这是生产观察，不将原阳性富集样本的P/R外推为真实全市场指标。

### 9.3 回滚

出现串用普通词库、测试历史误入正式、重复发布、正文门槛失效或资源明显异常时，先停受影响Massive源/绑定的后续处理，保留记录；原普通来源继续运行。恢复词库使用新修订回写前值，先验证其余普通字段未被并发更新，不回退整个旧候选配置或数据库快照。

代码回滚前处理新字段兼容；不能把含 `massive_l2` 的JSON交给不兼容的旧Reader。必要时仅撤销Massive启用，保留支持新字段的兼容代码。测试模式恢复也应先暂停真实绑定，避免恢复十ticker虚拟目标或掩盖已发生正式投递。已发出的正式消息保留审计，不批量删除或倒退发布offset。

## 10. 本轮交付与后续执行边界

本文完成前置勘察与执行方案。本轮没有重新调词、修改GOLD、构造新数据、改UI或开启45订阅。后续按本方案实施时交付实际代码/契约、9套导入清单、45绑定回执、正文缺口结果、前端检查、正式闭环及回滚证据；工程部署和业务验收分别报告。

参考资料：

- [用户提供的维护规则](C:/DoxMetrics/docs/message-source-classification-maintenance.md)
- [Massive业务与当前链路说明](../../docs/massive-distribution-business-and-regex-debugging-guide-20261112.md)
- [首轮九ticker报告](../../docs/massive-distribution-regex-tuning-report-20261112.md)
- [四ticker简化重调报告](../../docs/massive-distribution-regex-simple-round2-20261112.md)
- [统一监测词SOP](../../docs/by-distribution-relevance-debug-sop.md)

关键实现定位：`live.py:453/501/540`（词库/缓存/分流）、`monitoring_terms.py:25/95/173`（语言/模型/版本）、`v2_control/monitoring_terms.py:50`（读取白名单/消费者）、`v2_control/bindings.py:265/298`（订阅API入口限制）、`service.py:640`（真实绑定服务）、`body_results.py:13`（test文章身份）、`newswire_sources.py:40/51`（源默认测试配置/端点）、`frontend/v2/src/pages/monitoring-terms-settings.tsx:140`（现有普通规则编辑区）。行号以本次DoxAgent工作区为准，实施以合并后的实际基线复核。
