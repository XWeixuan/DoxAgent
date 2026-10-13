# DoxAgent v2.1 前端可读性与策略 KPI 优化方案

日期：2026-10-14。状态：完成代码与生产只读勘察，待实施。本轮没有修改应用代码、数据库结构、服务配置或进行部署。

## 1. 目标与实施边界

本次一次性完成三个用户目标：统一紧凑引用标记；重新组织 D2 v2.1 的阅读 UI；修复策略 KPI 查询并完善 Policy 正文、方向状态与关联预期展示。沿用旧版 V2 的表格、单列内容、细分隔和少量语义色，让新字段服从业务层次。

- 使用现有 React / shadcn / Lucide 与 `business.css`、`refinement.css` 的设计基础，不换 UI 框架，不重新设计导航和整体品牌。
- 正文 14–15px，标题 16–20px，字段标签 14px；通过结构提高密度，不缩小文字塞内容。
- 保留 v2.0 历史 D2、旧 Policy 的可读性，以及当前的固定版本、历史选择、下载、索引定位和局部错误反馈。
- 不改变 D2/D3 的 producer schema、研究内容、Condition 的 OR 语义、W2/W3 规则、Policy×方向消费和重新授权逻辑。
- 不执行整库重投影、数据清理或历史回填。引用显示是渲染转换，不修改原始 JSON / Markdown 或导出内容。
- 桌面验收范围为 1262px 和 1559px；保留必要的局部表格横向滚动，不允许页面整体横向溢出。

依据：用户本轮需求；`frontend/v2/AGENTS.md`；`dev_plan/v2_design.md` §1.9–1.10；前端 PRD Part1 §7–8。旧 PRD 中与 v2.1 相冲突的字段定义以当前正式 v2.1 合同为准。

## 2. 勘察结论与生产证据

### 2.1 引用标记没有统一渲染层

- `components/document.tsx` 的 ReactMarkdown 只定制表格、链接和图片，没有处理 `【cite:O1】`，因此 D1、通用结构化正文等仍显示原始标记。
- `pages/expectations.tsx:UnitV21` 的 `refs()` 直接 `values.join(" · ")`，参数和 State value 引用也直接输出。这就是截图中大量引用撑高 Unit、State 和卡片的原因。
- 旧版 `components/citations.tsx` 提供“引用 N”详情展开，但不是内联引用标记转换组件。应复用其已有详情能力，不能把所有数字圆标做成新的查询入口。
- 引用也可能出现在段落内部，不只存在于独立 `ref[]`。只替换数组或全局 CSS 无法统一解决。

### 2.2 D2 v2.1 是结构适配不足，不是缺少业务数据

真实 BE 页面已复现用户截图：State 每个参数一个 `.condition`，其下堆多个 `<dl>`；Baseline / Factor / Gap 也都使用同一个 `.condition` 样式。

具体原因：

1. `UnitV21` 没有沿用 `LegacyUnit` 已有的状态表格。
2. 每个参数都执行一次 `values.filter()`，且没有显示 State value 自身的 `name`，不同来源/期间的观测口径不易区分。
3. 四类章节缺少旧版的图标和明确的组件结构，字段标签与长正文的视觉权重接近。
4. Gap 的 `possibility_space` 被渲染成连续 `<p><strong>name</strong> · implication</p>`，既没有组名，也没有条目分隔。
5. Unit 的 scope 和大量 ref 裸放在 header 后，缺少与正文一致的内边距。

数据合同已经完整提供 `parameters`、`values`、`expectation_baseline`、`realization_factors`、`potential_gaps[].possibility_space`。无需修改研究 Workflow 来解决上述 UI 问题。

### 2.3 策略 KPI 超时的根因在 API 查询

2026-10-14 生产状态：

- API：`doxagent-v2:service-busy-20261013-r1`；Web：`doxagent-v2-web:v21-production-20261012-r1`，均 healthy。
- 浏览器 BE 策略页可读取 Policy 正文，但 KPI 显示 `QUERY_TIMEOUT`。
- API 日志中该路由 `queue_wait_ms≈0.1`，`execute_ms≈3000`，SQLite 为 `SQLITE_INTERRUPT`。不是浏览器骨架、排队时间或页面刷新造成的假超时。
- 容器内 `api_v2/policies.py` 与本地文件 SHA256 相同：`15028ad17128a8ff03c34fcfd3d62a190ee807da1b626bb4711d4eb0981fc33d`。

`policies.py:selection()` 为每条 catalog 记录生成 3 次 Policy change EXISTS 和 2 次 contribution EXISTS。`metrics()` 对这些结果求和，又调用 `uncertain()` 进行第二次统计。贡献查询还直接访问包含全部历史版本的 `contributions`，没有采用已有的 `metric_table(seq, "contributions")`。

EXPLAIN 确认存在多个 `CORRELATED SCALAR SUBQUERY`；变更查询会重复访问 current/history 分支，贡献查询只能先按 metric/ticker/day 找候选，再解析 dimensions 中的 business_key。这里不能笼统称为“每次都扫描整库”：部分入口确实用了索引，问题是重复扫描同一候选范围和大量历史版本，而非索引完全不存在。

生产 catalog 的 current 记录包含有效的退役历史：

| Ticker | catalog 记录 | 唯一 policy_id | ACTIVE 记录 | RETIRED 记录 |
|---|---:|---:|---:|---:|
| BE | 423 | 264 | 124 | 299 |
| MU | 624 | 310 | 264 | 360 |
| INTC | 5672 | 274 | 173 | 5499 |

不能简单删除退役记录或只保留当前 ACTIVE 来让查询变快，否则周期失效、历史命中和修改会丢失。

只读原型结果：

| 方案 | 当天范围 | ALL 范围 | 结论 |
|---|---|---|---|
| 原查询主要聚合，6 个样本 | 1.5s 人为诊断上限内未完成 | 未作无限等待 | 与生产 3s 超时日志相符 |
| 按 Policy 一次聚合再关联，仍读原 contributions | BE 36.4ms / MU 43.7ms / INTC 117.8ms | 三者超过 1.5s | 消除相关子查询有效，但不够 |
| 同时采用现有 contribution current/history 关系 | BE 261.2ms / MU 481.9ms / INTC 846.2ms | BE 943.4ms，MU/INTC 超过 1.5s | 历史水位和 ALL 必须单独优化 |

这些是不同只读快照上的 SQL 原型，不是完整 API 的最终性能，不代表已修复。全部探测设置 `mode=ro`、`query_only=ON` 和 1.5s 中断上限，未在生产建立索引。

### 2.4 Policy 关联预期当前实际上是原始 ref 列表

`PolicyDocument` 对旧 Policy 使用结构化 `source_refs` 构造链接；对 v3 Policy 则将 `policy.ref` 逐条渲染为 span。截图中的文件路径并不是真正可用的预期链接。

生产 v3 ref 抽样确认至少包含：

- `context/document3/v21/d2/shell-0001.json#/units/0`；
- `context/document3/v21/shared/document2.json#/shells/9/units/0`；
- 整个 Shell 文件引用；
- D1 报告路径、Discovery JSONL 行号、Research 工作文件、HTTP(S) 来源和 cite 标记。

v2.1 Unit 在读投影中已经有 `ordinal`、合成 `unit-0000` ID 和原始 `view_id`，因此可确定性映射名称与跳转位置。必须以该 Policy 的 `source_document2` 为准，不能把路径中的索引套到当前最新 D2。

### 2.5 层级和方向资格必须保留真实语义

- `trigger_layer` 是触发事实的粗位置，不是优先级，也不是执行顺序。
- `OUTER`：改变需求、供给或既有假设的驱动；`MIDDLE`：行业、采用、配置、供给等传导状态；`INNER`：目标公司的经营结果或明确修订。
- “方向可执行”来自 `executable_directions`，表示方向资格，不表示订单必然成功、Gateway 正常或已经执行。
- 一个 Policy 可同时包含 LONG/SHORT，不能为了让比例看起来相加等于 100% 而擅自修改分母或恢复旧版单方向模型。

## 3. 引用标记统一设计

### 3.1 圆标规格

新增共享 `CitationBadge`、`CitationText`、`ReferenceMarks`，所有业务页使用同一实现：

- 外观只有一个数字，不带 `cite:`、O 前缀、方括号、点分隔或链接下划线。
- 使用浅中性背景、细边框、13px 等宽数字；一/两位数字约 20px 圆形，三位 26px，更多位按文字宽度增大圆径，不裁掉数字。
- 段落内作为 inline 元素稍抬高；独立引用数组用 flex-wrap、4px 间距，不再输出 ` · ` 分隔符。
- 元素为 span，没有 click、tabIndex、button role 或 pointer cursor，不新增弹窗、Tooltip 或网络读取。
- 保留 `aria-label="引用 O28"` 等完整原始身份，视觉只显示 `28`。

编号规则：正式 `O28` / `【cite:O28】` 直接显示 `28`，不重新给现有 O 编号。其他非标准别名按当前文档的稳定来源顺序分配数字，避开已使用的 O 数字；映射作用域为固定文档/Content，不跨 ticker、run 或不同内容混用。D2 的编号上下文来自当前已加载完整 Shell；D1 来自当前完整报告，分块读取继续沿用同一映射。不得按某段局部出现次序反复从 1 编号。

`ref[]` 中的 URL、文件路径和 JSON Pointer 不是 cite 标记，不得全变成数字圆标或被删除；应归入资料引用区。

### 3.2 接入方式

1. 新增纯解析工具 `core/citation-markers.ts`，识别完整引用标记、别名与数组去重，保留原始字符串。
2. Markdown 使用一个小型 remark AST 插件，在普通 text 节点中生成受控 span；ReactMarkdown 映射到共享圆标。跳过 code、inlineCode、链接地址等，不开启 raw HTML，不用字符串拼 HTML。
3. D2、Policy 等直接 JSX 字符串使用 `CitationText`；ref 数组使用 `ReferenceMarks`。
4. `Document`、`SectionedMarkdown`、`StructuredDocument` 共用编号上下文，防止一个报告被多个组件切开后编号冲突。
5. 扫描 D1 报告/未来节点、D2 两版本、Policy 正文、Case reasoning、Event/消息正文等文本入口，凡正常正文出现完整 cite 标记均采用共享渲染。
6. 旧 `Citations` 详情展开可保留，数字圆标本身不绑定其交互；资料引用折叠是独立操作。
7. 下载、Case JSON 导出、复制原始业务 JSON、Citation API 和源内容 SHA256 不变。

## 4. D2 预期研究页面整体调整

### 4.1 阅读层级

保留 Shell 切换与紧凑 Shell 概况，正文按以下顺序：

```text
Unit 名称                                      时间范围
范围正文 · 紧凑引用圆标

[趋势图标] 当前状态                 对齐的状态表
[标尺图标] 预期基线                 预期判断 + 正常推进 / 开放前沿
[分支图标] 实现因素                 单列机制卡片
[分叉图标] 潜在差异                 修订逻辑 + 分项可能路径
```

四个章节使用不同结构；不再复用交易条件的 `.condition`。Unit 概述加与正文一致的 20px 左右内边距，scope 正常阅读宽度，horizon 作为次要信息。章节间距 18–20px，卡片间距 10–12px。

### 4.2 当前状态恢复为分组对照表

列为：**参数 / 来源口径 / 当前值 / 先前值 / 时间范围·截至 / 有效性 / 引用**。

- 按 parameters 原始顺序分组，参数名称与定义只显示一次；组内按 values 原始顺序展示全部观测，不把不同来源或年份擅自合并。
- 预先建立 `Map<parameterName, values[]>`，不在每个参数渲染时重复 filter。
- 参数定义较长时用参数单元格中的原生 details“定义”查看，默认收起；不是截断或丢弃字段。
- `value.name` 在观测值旁作为轻量口径名展示，避免当前实现丢掉这项信息。
- NUMBER/RANGE 数值右对齐；STAGE/DIRECTION/EVIDENCE 和长文本左对齐；沿用 `stateValue()`，不把所有类型强转数字。
- previous_value 独立列；没有则为 `—`。as_of 与 time_scope 放同一列两层，不生成多行“时间范围/有效性/来源”重复标签。
- CURRENT / SUPERSEDED / DISPUTED / RETRACTED 用短中文标签；已替代历史保留可读，不删除、不默认隐藏、不以整行划线影响比较。
- 参数引用放参数定义下，观测引用放最后一列；只在同一数组内去重，不跨业务条目删引用。
- 无 value 的参数保留一行“未提供”；异常孤立 value 保留在明确的未匹配分组，不静默丢失。
- 使用原生 table、明确 th scope、轻表头与参数组分隔，避免每个观测都有厚重外框。仅表格自身允许滚动，长时间范围自动换行。
- 参数组保留 `d2-state-{index}` 定位点，索引点击后滚动到对应参数，不变成另一套结果页。

### 4.3 预期基线：突出市场原有判断

每项采用白底/浅中性底的基线面板，结构为：名称 + 时间范围；“当前预期”主正文；下方两条对齐字段“正常推进”“开放前沿”；最后紧凑引用。可以用细横线分隔补充字段，不使用与 Factor/Gap 相同的蓝灰条件卡。

字段映射：`baseline → 当前预期`、`ordinary_progress → 正常推进`、`open_frontier → 开放前沿`、`time_scope → 时间范围`。不把 baseline 误当实际状态，也不删掉长内容。

### 4.4 实现因素：单列机制卡片

沿用旧版 Factor 的细蓝色左边线，白色或极浅底，完整单列。标题下按对齐的字段行展示：

| 原字段 | 前端字段名 |
|---|---|
| mechanism | 作用机制 |
| current_status | 当前进展 |
| materiality_context | 影响分量 |
| scope_boundary | 适用边界 |

标签约 84–100px 宽，正文独占其余空间；标签使用中性深色和适度字重，不再与次要灰字混淆。长正文自然换行。v2.1 没有旧 structural_role，不伪造“必要条件/阻碍因素”标签。

### 4.5 潜在差异：把可能路径组织出来

沿用旧版 Gap 的细玫红左边线；每张 Gap 卡显示名称、“仍开放的原因”“预期修订逻辑”，再显示一个有组名的 **可能演化路径** 区域。

`possibility_space` 每项作为独立条目：

```text
可能演化路径
  1  平台把后台代理捆绑进已有订阅
     预期影响  ……
  ───────────────────
  2  企业将代理纳入生产流程
     预期影响  ……
```

name 用正常标题字重，implication 前明确标“预期影响”；条目之间 10–12px 间隔或细分隔线，全部单列，默认展开完整内容。可能路径编号只是阅读序号，不暗示概率、权重、优先级或互斥关系。空数组不输出空组名，也不编造路径。

### 4.6 索引与兼容

- 当前 v2.1 索引的 State/Baseline/Factors/Gaps 四组保留，图标与正文一致；BASELINE 必须加入统一 mode/类型定义，避免只靠零散字符串分支。
- 保留 `run/shell/unit/content/item` URL 定位语义及 `d2-baseline-*`、`d2-factors-*`、`d2-gaps-*` 锚点。
- 当前 Shell 自动完整读取和当前选中 Unit 展示不变；不为重做 UI 预取其他 Shell。
- `LegacyUnit` 保留自身数据映射，可共享状态表、字段标签和引用组件，不能强行补成 v2.1 DTO。

## 5. 策略 KPI 查询重构

### 5.1 采用集合聚合，不延长超时

新增 `api_v2/policy_queries.py` 集中构建读查询，替换 `policies.py` 中重复的 selection/uncertain。所有 SQL 继续参数化，使用同一 view seq、ticker、Shell、周期。

一次请求在一个短只读事务内完成：

1. **catalog_scope**：只读所需 catalog 标量列，保留 ACTIVE 和相关退役历史。只解析一次 lifecycle/effective/consumed/directions，维持 ACTIVE_UNKNOWN 规则。
2. **change_flags**：对同一 ticker/seq/周期/Shell 的 policy_change 一次分组，按 route 产生 added/modified/retired 布尔标志；多 Shell 副本和多条变化先去重。
3. **trade_flags**：对 `policy_ar_hits`、`policy_executed` 的正贡献按 `dimensions.business_key` 一次分组。必须沿用 `policy_id:activation_revision` 或 v3 对应 revision 的真实键，不能只按 policy_id 连接导致不同代次串联。
4. catalog LEFT JOIN 两组小结果集，得到每条 catalog 的筛选标记。aggregate 一次返回所有 KPI **以及 unknown_count**，删除第二次 uncertain 全扫描。
5. 不需要 period 事实的 ACTIVE 列表，先对 catalog 做范围与有效性过滤、游标和 LIMIT；如需全部 matched_filters，再对已返回页统一补充标记。禁止每条 Policy 发起查询。
6. HIT/EXECUTED/变更类列表从对应集合与 catalog 关联后再分页，与 KPI 共用范围和键规则。

跨表关联本身不是问题；这里选择的是过滤后的集合关联，禁止回到“每个 Policy 再扫描一次事实表”的写法。

### 5.2 正确使用 current/history 和两条针对性索引

- 对 objects 使用现有 snapshot 关系；对 contributions 使用现有 current/history 关系，不能直接无差别查询全部历史 contributions。
- 固定 view 即使落后一个提交，也必须读取该 seq 的历史版本，不能偷偷换成最新值来追求速度。
- 当前分支按 metric/ticker/day 读取；历史分支先按 metric/ticker/valid_to > seq 缩小范围，再检查 valid_from。原型已证明 ALL 仍需要优化历史候选范围。
- 新增下面两条**仅针对正的 Policy 统计贡献**的部分索引，不建新的汇总服务或大宽表：

```sql
CREATE INDEX IF NOT EXISTS policy_positive_current
ON contributions_current(
  metric,ticker,day,json_extract(dimensions,'$.business_key'),valid_from
)
WHERE metric IN ('policy_ar_hits','policy_executed') AND CAST(value AS NUMERIC)>0;

CREATE INDEX IF NOT EXISTS policy_positive_history
ON contributions(
  metric,ticker,valid_to,valid_from,day,json_extract(dimensions,'$.business_key')
)
WHERE valid_to IS NOT NULL
  AND metric IN ('policy_ar_hits','policy_executed') AND CAST(value AS NUMERIC)>0;
```

查询分支显式包含相同的固定 metric 集合及正值条件，使 SQLite 能证明部分索引适用。用 EXPLAIN 核实历史分支确实按 valid_to 范围读取；不是仅确认“建过索引”。若标准 `metric_table` 关系妨碍范围下推，为本查询增加小型 scoped 关系构造器，把 metric/ticker/day/seq 放进两条 UNION ALL 分支，复用同一规则而非全局字符串 replace。

索引由独立幂等 helper 建立，不触发全量 `ReadStore.migrate()` 的其他维护任务；先在生产读库的隔离副本测建索引耗时和空间，再安排一次受控迁移，不在每次 API 启动/请求时建索引。生产表和数据不重建、不删除。

### 5.3 指标口径和反馈

- 保持现有 KPI 字段和统计身份。ADDED/MODIFIED/RETIRED/HIT/EXECUTED 的边界、撤回和去重必须由回归固定，不能用新查询把“有历史事实”改成“当前仍 ACTIVE”。
- 生效数不重复统计同一当前 Policy；LONG/SHORT 依据旧 decision 或 v3 executable_directions，不退回“缺方向就 LONG”。
- 双向 Policy 的 LONG/SHORT 覆盖可重叠。继续展示两个百分比，字段名可改为“LONG / SHORT 可用覆盖”，不强行使其总和为 100%。
- 缺失资格/覆盖继续显示真实未知，UNKNOWN 不补零；不新增长期常驻的“优化中”等说明。
- KPI 继续独立 useRead，不阻塞 Policy 列表。局部重试只重试指标，保留正常正文和筛选。
- 维持现有 3s 查询预算与有限 GET 重试，不加高频轮询，不通过扩大超时掩盖慢 SQL。

## 6. Policy 正文与关联预期

### 6.1 经济传导和条件标签

- “经济传导”使用现有 Lucide `Workflow` 图标，与匹配范围 Crosshair、激活条件 GitPullRequestArrow、关联预期 Link2 保持 18px 同一线宽和主色。
- transmission / match_scope / criterion / calibration 中出现 cite 时都使用共享引用渲染。
- 条件第一行用紧凑横向 flex、6–8px gap：条件序号、LONG/SHORT、层级、方向状态；不再使用 `· MIDDLE该方向可执行` 的串接。
- LONG/SHORT 保留轻绿/轻红语义。层级使用中性描边小标签：`外部驱动` / `传导环节` / `经营结果`，aria-label 保留 OUTER/MIDDLE/INNER 原值；不渲染为高/中/低优先级。
- 方向状态用图标+短文字：`可用` / `已消费` / `未确认`，来自 executable_directions / consumed_directions，不因标签改名重算资格。“可用”只表达该方向资格；未提供 eligibility 的历史固定版本不显示伪造的可用状态。
- criterion 仍是主要条件正文，但字重由整段重粗调整为约 500；参考状态/触发边界以对齐标签+正常字重正文承接，避免整屏黑粗文本。

### 6.2 将预期关联与资料 ref 分开

**默认显示关联预期名称与链接；原始资料 ref 默认收起。** 不把文件路径直接当作可访问页面，也不把所有 HTTP URL、D1 报告当作关联预期。

新增 `api_v2/expectation_refs.py`，只做已索引、固定 D2 的确定性映射：

1. 权威输入是 PolicyDetail.source_document2 的 run_id/artifact_id/content_sha256。先核对读库 document_ref，不使用当前 activation 的其他 D2 替代。
2. 白名单解析两种生产已确认的地址：`context/document3/v21/d2/{shell_id}.json#/units/{index}` 和 `context/document3/v21/shared/document2.json#/shells/{shellIndex}/units/{index}`。可识别其明确的 State/Baseline/Factor/Gap 子路径。
3. 整 Shell 文件或 `#/shells/{index}` 只形成 Shell 级名称链接；没有 Unit 下标就不编造关联 Unit。
4. shellIndex 映射固定来源 D2 中的真实 Shell ordinal；unitIndex 为零基，映射 `unit-{index:04d}`。名称读取已索引 Shell/Unit；校验索引存在、所属 run 和 Shell 一致。
5. 批量查所需 ID，复用 `ReadStore.batch_get()`；同一详情最多一个 Shell 目录批次和一个 Unit 批次，不为每个 ref 下载 D2、读取工作区文件或发前端请求。
6. 同一 Unit 被多个 ref 引用时合并链接；更细条目的准确名称/位置保留。无法解析的引用进入资料区，不能凭名称相似度猜 Gap。
7. 不允许 `..`、file://、任意本地路径或外部 URL 被当成 D2 定位输入；不访问网络解析来源。只解析受控模式和固定 JSON Pointer。
8. source_document2 缺失或不一致时，Policy 其余内容仍可看。若确实存在 D2 定位 ref，保留原 ref 并显示简短“预期关联未解析”；完全没有预期定位 ref 时不添加无用途的空关联标题。不悄悄链接到最新 D2。

扩展读 DTO（原始 PolicyV3.ref 不变）：

```ts
interface RelatedExpectation {
  run_id: string;
  shell_id: string;
  shell_name: string;
  unit_id: string | null;
  unit_name: string | null;
  content: "ALL" | "STATE" | "BASELINE" | "FACTORS" | "GAPS";
  item: string | null;
  item_name: string | null;
  refs: string[];
}
// PolicyDetailLegacy / PolicyDetailV3 均增加可选 related_expectations?: RelatedExpectation[]
```

普通 Policy detail 在只读响应组装时补充该字段，兼容既有已经发布的 v3 Policy，不要求重投影历史 artifact。前端接收结果，不自行扫描所有 D2 Shell。旧 Policy 的结构化 source_refs 同样解析为真实名称，不能继续只显示三个 ID。

链接形态：`/ticker/{ticker}/expectations?run={source_run}&shell={shell_id}&unit={unit_id}&content={content}&item={item}`，不存在的参数不输出；显示 Unit/条目名称，以 Shell 名为次要上下文。使用原生 a/Link，支持键盘和新标签打开，不截断业务名称为技术路径。

资料引用使用原生 details：`资料引用（N）`，默认关闭。展开后完整显示未转成预期关联的原始 ref；HTTP(S) 来源使用安全外链，文件/JSONL ref 是可换行文本，cite ref 是数字圆标。需要复核已映射 ref 时，可在此区的单独“预期定位来源”分组查看，默认不重复占正文空间。

### 6.3 固定历史 Policy 的路径一并覆盖

当前 `PinnedPolicy` 会下载完整 PolicySet 再找一个 Policy，拿不到解析后的关联名称。此次增加一个小型只读接口：

`GET /tickers/{ticker}/policy-sets/{version}/policies/{policy_id}?runtime_activation_id=...`

- 复用现有 PolicySet download 的 activation/version/artifact 绑定校验；从固定 artifact 的已索引 policy_detail 读取目标 Policy，parent 限定该 artifact，再按 policy_id 过滤，LIMIT 1。
- 返回 PolicyDetail 和相同的 related_expectations；不存在固定 artifact 时保持 PINNED_ARTIFACT_MISSING，不从现行版本替代。
- 这是已固定的历史正文，不能把当前 catalog 消费资格覆盖到它上面；历史资格没有可靠证据就不显示。
- `PinnedPolicy` 改用该接口，既有完整下载按钮和原 JSON 下载接口保持不变。
- 不改变当前 v3 默认 ALL Shell、旧版本 Shell 筛选行为。派生引用关系用于阅读导航，不自动把一个资料引用升级为 Policy 的归属或 Runtime 匹配条件。

## 7. 文件级实施任务

| 顺序 | 文件/模块 | 具体交付 |
|---|---|---|
| 1 | `core/citation-markers.ts`、`components/citation-text.tsx`（新增） | 安全拆分、稳定编号、圆标、数组引用 |
| 2 | `components/document.tsx`、`page-kit.tsx`、`citations.tsx` | Markdown AST 接入和上下文贯通，保留既有详情能力 |
| 3 | `pages/expectations.tsx` | 抽出 StateTable / BaselinePanel / FactorCard / GapCard，四类章节映射、索引不丢失 |
| 4 | `api_v2/policy_queries.py`（新增）、`policies.py` | 集合聚合、共用范围/筛选、一次 unknown_count、去除相关子查询 |
| 5 | `v2_read/repository.py` | scoped MVCC 关系/两条部分索引的独立幂等 helper，仅增加所需代码 |
| 6 | `api_v2/expectation_refs.py`（新增）、`libraries.py` | 固定 D2 引用批量解析、当前与历史 Policy detail 组装、小型 pinned 接口 |
| 7 | 共享 TS contract、两份生成 wire schema、`core/schema-source.ts` / `api.ts` | 只增加读 DTO 字段和新接口客户端类型；同步生成，不手改生成 JSON |
| 8 | `pages/strategy.tsx` | 经济传导图标、层级/资格样式、命名预期链接、折叠资料、PinnedPolicy 最小读取 |
| 9 | `business.css` / `refinement.css` | 使用 d2-* / policy-* 局部选择器；不修改全局 `.condition` 造成其他页面串样式 |
| 10 | 对应回归与 `changelog` | 必要测试、生产/静态证据分别记录，追加变更日志 |

本地已有其他任务的未提交修改（包括 contract、wire schema、Runtime、投影等）。实施前核对新基线，保留其他任务改动；提交只包含本方案对应变更，不整体覆盖 API/read 或把工作区全量打包部署。

## 8. 必要测试与验收

### 8.1 自动化

1. 引用解析：连续引用、段落内引用、重复/三位编号、其他别名、Markdown 表格/粗体、跨章节一致性；code/URL 不误改；没有 HTML 注入；原 JSON 下载不变。
2. D2 组件：六种 State 值类型、同参数多观测、先前值、历史有效性、空参数、四类章节和 Gap 多路径均有字段名/间隔；v2.0/v2.1 都可读；索引定位和 BASELINE 模式有效。
3. KPI：扩展现有 `tests/v2_backend/test_policy_queries.py`，覆盖 current/frozen、五种周期、ALL/具体 Shell、多次变更去重、退役历史、HIT 撤回、同 Policy 不同 revision、方向消费、ACTIVE_UNKNOWN；验证列表筛选与 KPI 使用相同集合。
4. 解析链接：两种真实 ref 形式、零基索引、整 Shell、条目定位、重复关系、坏指针、缺失/不匹配来源 D2、恶意路径、历史版本和旧 source_refs；普通详情不发生逐 ref HTTP。
5. pinned 接口：ticker/activation/version/policy/artifact 绑定正确，不能用其他版本内容兜底；只读不会改变任何消费资格。
6. 更新 `tests/browser/downstream-v21.fixture.tsx` / `.spec.ts`，增加真实长度的 State、多条可能路径和 30+ 引用场景。构建/TypeScript/相关 ESLint 通过；不为纯 CSS 写镜像实现的单元测试。

### 8.2 生产同规模性能验收

- 在隔离读库副本建立索引，运行 BE/MU/INTC 的当天、7天、30天、ALL；同时覆盖新鲜和冻结 view、具体 Shell 与 ALL。
- 用合成小库验证新旧结果等价，用生产规模副本测计划和耗时；原接口会超时时不能把“旧结果没有返回”当正确性证据。
- 目标：指标 SQL p95 ≤500ms、对应 API p95 ≤1s，无 3s deadline；ALL 冷读也必须完成且不依靠重试。每个关键范围至少 5 个样本，记录冷/热差别，不用单次最小值验收。
- EXPLAIN 不再出现对每条 catalog 重复执行的变更/贡献相关子查询；历史分支使用受限索引。不要求消除对已经过滤的小型结果集正常的 SCAN / GROUP BY。
- 不增加查询预算和高频缓存预热。若 ALL 仍达不到目标，继续修正索引选择/范围下推，不先引入新的长期汇总系统。

### 8.3 浏览器验收与发布边界

按 D2 → 策略页 → D1/其他引用文本入口逐页验收；在 1262/1559px 检查密度、字体、滚动和键盘，重点核对真实长文本，不只看短 fixture。

- 数字圆标无点击要求、无意外焦点；State 不再是连续纵向大卡片。
- Baseline/Factor/Gap 一眼可区分，Gap 每条路径清楚显示名称和影响。
- 经济传导有图标；条件层级/资格短标签不串字、不混淆执行结果。
- 关联预期显示业务名称，可跳到固定 D2 的正确 Unit/条目；资料默认收起，坏引用不导致整卡失败。
- KPI 正常返回，局部错误/重试不闪整页；打开详情/导出/历史查看保持可用。

未来部署以 API/Web 和本次独立索引迁移为边界，按当时真实镜像和 Compose 配置窄覆盖。索引建立不触发业务重投影；若建索引耗时会长期占用写锁，安排短投影维护窗口，不重启交易、采集或投研任务。发布前保存 API/Web 原镜像与 overlay；回滚只恢复 API/Web，新增读索引可以保留，不为回滚删除历史数据。

## 9. 本轮证据位置与明确未完成项

证据在 `eval/v21_frontend_audit_20261014/`：

- `probe.py` / `production-sql-audit.json`：生产查询计划、原型超时和 catalog 规模；
- `set_query_probe.py` / `set-query-audit.json`：一次分组关联的只读试算；
- `set_query_current_probe.py` / `set-query-current-audit.json`：current/history 关系原型、不同周期耗时和 v3 原始 ref 抽样。

浏览器真实查看 BE 策略/预期页面，确认用户描述与现行实现一致。此次没有应用上述改造、建立索引、跑业务回归、Git 提交或部署；上面的性能数值只支撑选型与诊断，不能作为最终上线验收。
