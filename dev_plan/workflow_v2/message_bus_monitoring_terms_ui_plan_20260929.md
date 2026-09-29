# 消息总线统一监测词人工管理开发方案

日期：2026-09-29
范围：V2 前端 + V2 API + Message Bus 监测词服务。本文为待执行方案，本次未实施业务代码、未修改线上词表。
依据：当前工作区代码、V2 PRD Part 2 §5.8、用户提供的 `message_pipeline_site_strategy_governance_guide.md`。速查文档的线上状态是历史记录，本方案不将其当作当前线上事实。

## 1. 目标与已确定的边界

在 `/ticker/:ticker/message-bus?mode=CONFIG` 现有逐源配置上方增加三个紧凑模块：

1. **搜索监测词（By Keyword）**：编辑 `l1_concepts`，实际后端模式仍叫 `by_search`，不引入 `by_keyword` 新枚举。
2. **分发匹配规则（By Distribution）**：编辑 `l2`，支持“规则视图 / 原始 JSON”。
3. **Jev 相关性定义（By Distribution）**：编辑 `definition.relevant` 和 `definition.irrelevant`。

“全局”指**当前 Ticker 跨消息源共享**，不是全部 Ticker 共用。页面区域标题为“{ticker} 统一监测词”，避免使用无范围的“全局配置”。保留下方逐源运行设置和参数配置。

三个模块共享一份草稿和一个“应用更新”按钮，提交完整配置形成一个 revision；不是三个互相覆盖的独立写请求。每个模块均可人工调整，任一模块修改都可随完整配置独立于单源设置应用。没有变更时禁用应用。

本次不增加自动生成词表、LLM 正则翻译、历史回放、词表审批、版本管理页面、全站消息源管理、Jev 模型/密钥/总开关管理。已有 CLI history 继续可用。无需重启 Bus、重新初始化 Ticker 或触发 O4。

## 2. 当前实现与需要补齐的部分

| 代码入口 | 已有事实 | 本次处理 |
| --- | --- | --- |
| `frontend/v2/src/pages/source-settings.tsx` | BindingSettings 当前只有逐源工作区；Editor 的表单/JSON共用参数草稿、带幂等键和 If-Match | 在工作区上方挂统一监测词编辑器；补充旧搜索参数接管提示 |
| `src/doxagent/message_bus_v2/monitoring_terms.py` | TickerMonitoringTerms、不可变 revisions、head、actor、CAS apply/get/history 已存在 | 复用存储；提取无写副作用的读取和共用校验；补齐语法检查 |
| `search_plan.py` | 按 source.content_language 渲染 L1，separate 每概念一条、or 合一条，query_key 含 revision | 后端生成预览；前端不自行拼 query |
| `relevance.py` | NFC/空白归一化；源语言与 en 取并集；组间 OR；any/all/none；regex 每次匹配20ms超时 | 原样保留判定语义；保存前编译检查，不另造匹配引擎 |
| `jev.py`、`distribution.py` | 相关/不相关定义组成每个 ticker 的独立问题；L2/Jev 任一命中可投递 | 编辑定义，不改变判定阈值、重试和开关 |
| `scheduler.py` | 普通 by_search poll 读取当前 terms；distribution 目标记录 terms_revision | 新任务自然消费新 revision |
| `persistent_runtime_v2/bus_orchestration.py` | SOURCE_SWEEP 固定 query_plan，后续读取 frozen plan | 已开始窗口不被人工更新改写 |
| `v2_control/bindings.py`、`api_v2/bindings.py` | 原生 Bus SQLite 同事务控制写、独立配置GET、鉴权、CAS、幂等回执 | 复用这种模式，不向 read model 写配置 |

目前未找到面向 V2 前端的 monitoring-terms 管理 API。CLI 已有 validate/apply/show/history/preview/test，不能让浏览器调用 CLI，也不能再建一份前端专用词表库。

重要现状：

- L1 要求1–3个概念，各语言表达非空且不含 `|` 或独立 `OR` 语法；概念有稳定 concept_id。
- `required_languages()` 当前取**所有已注册 search/distribution source** 的 content_language，包含未绑定/禁用源；`validate_languages()` 另要求 en，且 L1/L2 都要满足。此次保留这个统一治理规则，API明确返回必需语言及原因，不偷偷改成当前绑定语言；显示当前实际使用源与配置必需语言是两回事。
- `MonitoringTermsService.get()` 返回的配置内部 expected_revision 是当初提交时的旧基线；UI/API必须使用返回元组的当前 revision，不能把历史 expected_revision 原样提交。
- 现有模型只检查 literal/regex二选一，没有检查regex能否编译；运行时错误或超时按未命中处理。需增加服务层共用校验，CLI与Web行为一致。
- MonitoringTermsService 构造器当前执行建表。新GET不得调用会写DDL的构造路径；迁移应在服务初始化完成。

## 3. 前端布局与编辑行为

### 3.1 整体布局

统一监测词位于消息源工作区之前，不放在某个 source 卡片内部，不随左侧消息源选择而切换。三个模块纵向排列，模块标题和操作紧凑；L1用表格，L2按语言/规则组折叠，Jev两个文本框。初始展示已有词条，L2默认展示当前语言各组规则摘要，可展开编辑，不以巨型JSON占满首屏。

区域底部提供“应用更新”“放弃修改”；应用按钮仅控制统一监测词。单源配置仍使用自身“保存配置”。应用期间锁定统一词表编辑/重复提交，不冻结下方源列表。

数据按首次进入配置、配置模式手动刷新及应用成功后更新。不要绑定消息流SSE、KPI分钟刷新或逐源选择。统一词表与源配置分别加载/报错，一方失败不阻塞另一方。无binding时也必须能创建词表，否则会与“先有词表才能建立search/distribution binding”的准入形成死循环。

### 3.2 搜索监测词

- 概念为行，语言为列；必需语言固定显示，同时保留现有额外语言，并支持增加/删除非必需语言。
- 支持新增/删除概念（1–3个），自动生成新稳定concept_id；已有ID保留。UI不要求用户日常手填ID，原配置数据不丢失。
- 不把一行字符串拆成无限关键字；每个语言单元格是一个概念表达。保留服务端现有OR限制和引用/转义规则。
- 模块内折叠“实际搜索语句”：通过校验/预览接口显示当前绑定search源的语言、separate/or模式、实际queries。预览包括禁用但未删除的binding，并注明启停；不发起外部搜索。
- 未提交词表时显示“尚未配置”，提供空白草稿及必需语言结构。不得把某源的旧复杂search_terms自动压缩为3个概念或当成已生效统一词表。

### 3.3 分发匹配规则：两种无损视图

**规则视图（默认）**直接编辑结构，不尝试将任意正则反编译为自然语言：

- 语言页签：en及所需/额外语言，明确源语言与英语共同参与匹配。
- 规则组：稳定id、新增/删除、折叠摘要；组间“满足任一组”。
- 每组分为“至少命中一项（any）”“必须全部命中（all）”“排除任一项（none）”。any和all均有内容时必须同时满足，none只否决本组，不能误画成全局排除。
- 每个条件：类型“文本 / 正则”、值、范围“标题 / 摘要 / 正文 / 全部”、区分大小写；文本类型支持“整词匹配”。正则类型不展示可操作的whole_word开关，因为当前引擎只对literal应用该标志。
- 高级regex值始终按原文可见和可编辑，例如 `(?i:HBM\\d*)`，只解释条件结构和flags，不编造表达式含义。
- 摘要示例：“标题中至少命中任一：Micron、HBM；正文必须命中：产能；本组排除：招聘”。literal/regex以小型类型标识区分。

**原始 JSON**仅编辑l2对象，严格对应后端结构；不是JavaScript正则串，也不编辑revision/ticker。两种模式使用同一规范化AST草稿。JSON未解析成功时保留原文并阻止应用及切回结构编辑，显示具体错误。切换视图无数据变更不标脏。

不得删除隐藏字段、未知语言或未打开分组；严格模型拒绝未知结构字段，并在错误处显示原因。既有regex上的whole_word=true在往返时保留，并提示“此选项仅对文本条件生效”，不静默重写。新增regex默认whole_word=false。

### 3.4 Jev定义

两个多行文本框：“相关条件”“不相关条件”，原样对应relevant/irrelevant。支持中文/多语言长文本，不自动翻译或变成任意答案选项列表。

显示本Ticker已绑定distribution源的 `distribution_policy.jev_enabled` 配置情况；这只是源配置，不能宣称Worker当前能够调用Jev。当前API和Worker是不同进程，不能根据API进程的环境变量推断Worker有效状态。无可靠运行态时只显示“源配置已开启/未开启；实际运行还取决于Worker配置”，不增加本次不必要的健康探测系统，也不暴露密钥。无distribution binding时显示定义可保存但尚无使用源。

修改定义不会开启源、改变全局Jev开关、调用付费模型或重判已有文章。L2与Jev仍为OR关系。

### 3.5 草稿与冲突

- 基线保留完整config、current_revision、control_etag；草稿按ticker隔离。
- 模块编辑/切换source均保留草稿。离开配置页、切换ticker和关闭有脏草稿的标签页使用现有导航阻止能力或最小离开确认；仅草稿被丢弃时提示，不增加应用审批。
- 手动刷新有脏草稿时刷新已保存基线但不覆盖草稿，显示远端变更可用；应用使用原始基线CAS。
- 412保留草稿，提供“读取最新版本”与显式“基于最新版本应用我的草稿”；先展示三个模块哪些有差异，用户选择后才换etag；不得自动重试覆盖别人的修改。无需实现字段级合并引擎。
- 超时/503保留草稿与幂等键；相同提交重试同key，内容或etag变化生成新key。成功后以回执更新基线，重新查询配置使用方预览，清脏；请求成功与“新消息已消费新版本”分开表达。

## 4. API契约（新增）

前缀 `/api/doxagent/v2/tickers/{ticker}/message-bus/monitoring-terms`。这些是实时控制配置，不接收view_id/period，不进入历史read-model快照。沿用现有Bearer用户鉴权、ticker存在性校验、Wire响应与错误结构、QueryRunner/ControlRunner接入。

### 4.1 GET 当前配置

响应 `MonitoringTermsConfig`：

```typescript
type MonitoringTermsValue = {
  l1_concepts: L1Concept[];
  l2: Record<string, L2Language>;
  definition: { relevant: string; irrelevant: string };
};
type MonitoringTermsConfig = {
  ticker: string;
  revision: number;                   // 无配置=0
  control_etag: string;               // "monitoring-terms:MU:7"
  updated_at: string | null;
  configuration: MonitoringTermsValue | null;
  required_languages: string[];       // 排序、含en
  language_requirements: { language: string; source_ids: string[] }[];
  consumers: MonitoringTermsConsumer[];
};
```

Consumer至少含source_id/name、binding_id、acquisition_mode、content_language、source_enabled、binding_enabled、search_policy_mode|null、jev_source_enabled|null、terms_mode（UNIFIED/LEGACY/UNCONFIGURED）、实际queries（仅已配置search；无法生成时返回字段级问题）。只列当前Ticker未tombstone的search/distribution binding；必需语言来源另列，不混淆为有效订阅。`terms_mode`只说明配置路径，不是任务正在使用该revision的证明。

响应ETag和body.control_etag一致。没有词表返回200/configuration=null，不用404掩盖新建入口。GET只读Bus原生表，一次一致性读事务取得head/config/source/binding。

### 4.2 POST /validate

输入 `{ configuration: MonitoringTermsValue }`；ticker从路径取得。只做结构、业务规则、正则编译和query plan预览，不写库、不外网调用、不需要幂等键。

返回 `MonitoringTermsValidation { valid, issues, search_previews }`。issues为 `{path, code, message}` 数组，path使用JSON Pointer；正常校验不通过返回200/valid=false，JSON解析/协议错误走422。预览不保证未来应用一定成功，PUT仍需完整重验。UI可显式“检查并预览”，不在每次键入时请求。

### 4.3 PUT 完整原子更新

输入同validate；必需 `If-Match` 和 `Idempotency-Key`。首次创建也先GET拿revision0的etag。不允许请求体自带ticker/actor/revision/expected_revision以产生双重控制来源。

后端解析etag，对当前head CAS；构造 `TickerMonitoringTerms(ticker=pathTicker, expected_revision=currentBaseline, **configuration)`，通过统一服务apply。actor来自已鉴权principal。成功200返回最新MonitoringTermsConfig和ETag；一条事务更新三个模块。

错误：缺前置条件428，revision过期412/REVISION_CONFLICT，同key异载荷409/IDEMPOTENCY_CONFLICT，字段错误422/VALIDATION_FAILED（携带结构化issues），总请求超64KiB沿用413，未配置Bus503，ticker不存在404。保留API现有大小预算，UI显示超限不能伪报成功。

幂等回执与revision必须位于**同一个Bus SQLite事务**：新增窄表 `v2_monitoring_terms_commands(scope,key_hash,body_hash,receipt)`；scope含actor、ticker、PUT路由，hash覆盖配置和If-Match。先查回执，命中同载荷即返回原回执，再执行CAS；保证网络丢回执后重试不重复增加revision。不要跨control DB/Bus DB分两次提交。

不新增PATCH、删除词表、历史恢复API；减少首次开发分支。首次创建要求三个模块满足同一个原生配置约束。

## 5. 后端具体实现

1. `monitoring_terms.py` 提取schema初始化函数和只读读取路径；保留现有调用兼容，初始化/写路径负责迁移，新GET不能隐式DDL。把纯校验集中为函数，由apply、Web validate和CLI validate共用。
2. 校验现有约束、必需语言、非空白definitions/literal/regex/ids、concept_id唯一、同语言group.id唯一；regex使用运行时同一Python `regex` 包编译及相同flags，返回精确字段路径。保留20ms运行时超时，不声称编译成功即可证明任意输入均无超时；不增加武断regex黑名单或浏览器JS编译校验。
3. 保留当前literal归一化/whole_word/regex语义。定义与pattern原文保存；字段非空检查不能擅自改变regex空格语义。现有非法配置可GET/展示，应用前要求修复并定位错误，不在读取时导致整页失败。
4. 新增 `v2_control/monitoring_terms.py`：组合现有Bus事务模式（复用TransactionRepository或抽取极小公共事务工具），完成get/validate/mutate。避免复制整个Bindings类或建立通用配置平台。apply和幂等receipt使用同连接；不要嵌套独立事务。
5. 新增 `api_v2/monitoring_terms.py` 路由；在app.py注入同一个 `DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH` 配置。检查QueryRunner/ControlRunner子进程工厂也能建立服务，不能只测无runner的TestClient。
6. 在TypeScript contract定义精确L1/L2/definition及3组Wire operation，更新contract Markdown、examples、API route contract和前后端wire schema；使用现有生成脚本，不手改生成JSON。
7. 不增加read-model投影/轮询词表同步。控制GET读原库，Bus原消费者继续读同一head，避免保存后等投影才能看到新值。

## 6. 单源配置与统一词表的衔接

统一词表存在时，by_search实际query来自L1，不应继续诱导用户修改被忽略的旧search_terms：

- 为BindingConfig增加向后兼容的监测词使用描述（mode、terms_mode、managed_parameter_paths；未涉及源为空）。路径由后端根据真实adapter消费逻辑明确列举，不按字段名猜测；实施时逐一核对Reuters/Google等当前by_search adapter。
- 统一词表已接管的搜索参数在表单中只读并显示“由上方搜索监测词控制”；JSON视图保留原值但标注受管路径。服务端拒绝对受管路径的变更，允许原样带回；错误提示跳转统一词表模块。其余启停、间隔和参数可照常保存。
- 无词表的既有LEGACY binding继续允许原路径编辑，明确为旧源级配置；创建统一词表后刷新这些binding的控制元数据。不要删除原参数，以免制造不可逆迁移。
- 服务端保存binding时重新读取terms head，防止编辑过程中另一个窗口启用统一词表后，旧界面仍写入已无效的参数；无需更改source版本或批量重写binding。
- by_ticker参数不受影响；distribution共享抓取参数仍属于source，页面不能把L2当成某个binding的抓取参数。

## 7. 生效边界

- 成功提交即原库head可读，普通by_search下一次构造QueryPlan使用新revision；正在执行poll不强制取消或换词。
- 已冻结SOURCE_SWEEP继续用旧plan直到该窗口完成；后续新窗口采用新revision。source/query checkpoint按既有query_key语义运行，不手工清游标。
- 已创建distribution target/decision按原terms_revision继续执行；后续创建的新目标引用新head。不给同一旧目标套新定义，不把“下一次poll”承诺成重判旧文章。
- 新词表不自动绑定源、启用源/Jev、改变Ticker运行状态、不重新发布旧消息。
- 保存提示为“已应用，后续任务使用新配置；已开始任务保持原版本”。无需展示常驻大段操作说明。

## 8. 文件级工作清单与执行顺序

| 顺序 | 文件 | 交付 |
| --- | --- | --- |
| 1 | `message_bus_v2/monitoring_terms.py`、`cli.py` | 可复用校验/只读访问与原子apply保持一致 |
| 2 | `v2_control/monitoring_terms.py`、必要的事务公共工具 | 原生get/validate/PUT幂等CAS；数据库迁移 |
| 3 | `api_v2/monitoring_terms.py`、`app.py`、runner工厂 | 路由与实际部署进程装配 |
| 4 | `api_contract/doxagent-v2-api.types.ts`、examples、API Contract | 新DTO/operations与BindingConfig使用描述；生成schema |
| 5 | `v2_control/bindings.py` | 受管参数显示/写保护，保留legacy路径 |
| 6 | 新 `frontend/v2/src/pages/monitoring-terms-settings.tsx` | 三模块、共同草稿、JSON无损往返、校验/应用/冲突 |
| 7 | 新 `frontend/v2/src/core/monitoring-terms.ts` | 纯草稿转换、摘要、字段问题映射，不复制后端匹配器 |
| 8 | `source-settings.tsx`、`core/api.ts`、现有schema注册入口、`refinement.css` | 页面嵌入、独立加载与缓存失效、旧参数提示 |
| 9 | tests、PRD Part2 §5.8、运维文档、changelog | 测试与边界同步；记录真实验收结果 |

沿用现有组件、字体与配色，普通标签/内容14px，不另建编辑器框架、不新增富文本/代码编辑器依赖。现有未提交的Runtime功能改动属于前一项任务，实施时不覆盖或混入其语义修改。

## 9. 必要验证与验收标准

### 服务层与HTTP

- 空配置GET、revision0首次创建、再次修改；配置读回与CLI show一致。
- 一次修改任意模块保留其余模块，完整事务仅增加一个revision；CAS竞争只允许一个成功。
- 相同key重试返回同回执；同key异body/etag冲突；模拟写入失败后head/revision/回执一起回滚。
- 非法regex、空白定义、重复group id、缺语言、L1超3概念、OR表达式返回对应路径；既有非法配置GET不崩。
- en/ko/zh-Hant、regex转义、literal整词与不同字段；不改relevance引擎结果。
- GET不执行DDL；真实runner配置下GET/validate/PUT可用；未鉴权、Ticker不匹配、413及Bus不可用错误可理解。
- 搜索预览与build_query_plan一致；新poll用新revision、已有frozen计划和distribution目标保持旧revision（已有测试扩展，不测试实现的字面复制）。
- binding旧词被接管时不能被误改；未接管和by_ticker照旧。

建议扩展 `tests/test_message_bus_monitoring_terms.py`、`tests/test_message_bus_search_plan.py`、`tests/test_message_bus_distribution.py`、`tests/v2_backend/test_bindings.py`，新增 `tests/v2_backend/test_monitoring_terms.py`。

### 前端与浏览器

- 规则视图/JSON多次往返无数据丢失，复杂regex不误翻译，非法JSON不覆盖有效AST。
- 无binding也能创建；三个模块都能编辑并应用；保存后重新进入读取新版本。
- 任一请求失败只影响本模块区域；切source不丢词表草稿；刷新/冲突/失败重试不丢用户内容。
- 在1262px、1559px实测三个模块及既有源设置，无页面横向溢出、控件误触、字体缩小；可验证滚动定位到统一词表。
- 本地隔离Bus数据库接真实API做浏览器读—改—保存—重读，核对库中revision及实际QueryPlan，不能只用UI fixture声明功能验收。
- 不需要真实付费Jev请求或在线采集才能验收编辑功能；如另外进行线上应用，明确记录目标Ticker、原/新revision及新任务证据，不能把配置保存成功写成召回效果已验证。

运行聚焦pytest/Vitest/浏览器交互测试和前端build，更新changelog。只改方案文档阶段不运行无关业务回归。本次交付方案；后续实施完成后，再按用户部署指令进行提交/发布，线上现有词表不因部署被seed覆盖。
