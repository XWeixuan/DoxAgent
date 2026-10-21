# 成本审计修复实施记录

日期：2026-10-21。实施依据：`dev_plan/workflow_v2/cost_audit_repair_execution_plan_20261021.md`。

## 已完成代码

- 全局 `/audit/cost` 与一次桶读取的 `/api/doxagent/v2/audit/cost/report`；原 ticker URL 保留预选兼容，标的导航进入全局默认。
- node 保持 W1/W2，增加 round，逐轮／逐 provider／model 分行；KPI、趋势、占比、行页共享筛选及冻结水位。
- 已观测尝试、有效 usage、未知 usage、已报费用、估价、未计价和历史归一化进度分别表达；缓存比率用同批成对已知样本；已知零与未知分开。
- 版本化 Decimal 计价，固定汇率6.8，原币独立保存；OpenRouter 已报总费用优先、不与详情相加；补 DeepSeek v4.1 Flash，保留旧规则；Codex 费用 N/A。
- 原始缓存创建／读取、TTL 和价格依据进入归一化 usage；Vertex 原生输入正确加读写，OpenRouter 不重复加。回填完整替换贡献（含撤销空值和旧 round），原有机制已能正确处理 None tombstone，无需重写通用贡献框架。
- 回填使用短事务、keyset、断点、单进程限速、源只读及目标版本检查；不重放业务副作用，不复制 serving 库。
- 增加全局成本桶部分索引；DDL 为显式发布步骤，不在 GET 中运行，不调用 serving_schema.install 重建生产库。
- Vertex 原生 streaming/ADC、GLM Chat Completions/父轮显式上下文已前置适配，profile 默认为 current，按显式配置才加载。正式模型不切换。

## 来源接入矩阵

| 业务 | 现有证据源／入口 | 身份与准入 | 本轮处理 |
| --- | --- | --- | --- |
| W1/W2 各轮 API | runtime_v2_turns → DomainProjectors.turn_records | turn_id，已准入 Case 关联 | 恢复 round、缓存写入、费用回执和归一化 |
| W3／维护 Codex | runtime_values.worker_invocations → worker_records/worker_usage | codex:job_id；Case provenance 或 control_epoch | 保留既有去重，归一化重算；不套API价格 |
| C4/C1/C3/C5、O0/O1、O2/O3/O4 等初始化 API | usage_capture/spool → v2_model_invocations → FormalProjectors.api_usage | invocation_id；control_operation_id 必须存在 | 统一计价和字段投影；不移除生产准入 |
| 初始化各节点 Codex | initialization_nodes.receipt.worker_invocations → FormalProjectors.worker_usage | codex:job_id；准入 initialization_run | 同一账本层级去重，原有精确节点保留 |
| CDECR／共享研究实际执行 | 上述已准入 API 回执／worker 回执；codex_runtime_records 为研究工件源 | 原始业务所有权与稳定调用身份 | 不根据工件数推算调用数，不随意分摊 GLOBAL |
| Pilot／无法证明来源的记录 | 相同源库中的非准入记录 | 无生产证明 | 仍排除，不为补数放开 guard |

当前审查样本没有发现2,121个已持久化 Runtime API身份漏投影；本轮不新增另一套并行总账或全库定时扫描。

## 本地验证

- `eval/cost_audit_implementation_20261021/golden.json`：2,121 调用，2,030 有完整 usage，91 未知；input/output/read 与原始样本完全一致。
- 已知成本 USD 2.534239477647058823529411767；W1R2 原币 CNY 3.1885021，W2R1 创建1,769,343 token。
- 自动化覆盖旧价格／新渠道、已报费用优先、缓存TTL／100k边界、部分usage、全局／历史ticker／GLOBAL、整页筛选、同ID贡献替换、MVCC、回填并发版本拒绝、API权限范围。
- 1262px、1559px 浏览器交互检查通过：表头列边界一致、无整页横溢；每次初开只有 context+report，标的与节点切换均影响KPI／图表／行。截图位于 frontend/v2/test-results/cost-audit-*.png。
- Vertex/GLM 模拟HTTP/SSE通过；未调用真实模型。完整测试结果见 eval/cost_audit_implementation_20261021/tests.xml。

## 生产发布与回填

待 origin/main 同步后填写实际提交、镜像、回填规模、磁盘增量与查询基准。本段不能作为已上线证明。

## 已知边界

- 真实缺 usage 和缺原始回执不会被充零或回填成猜测值；回填无法找回的缓存字段保持未知。
- 生产既有 runtime capture proof 的 closed_end_at 停留2026-09-29，complete=false，即使checkpoint=head。全局 source gap 与模型回执缺失分别报告，不把历史来源缺口刷成 COMPLETE。
- GLM 官方已确认原厂工作空间 endpoint、JSON object 和原生 low/high/max，不能用 medium 或关闭思考。可选 DOXAGENT_BAILIAN_GLM_API_KEY 支持独立授权密钥；不从其他对话复制任何密钥。
- Vertex project、ADC权限和真实缓存命中需未来模型切换窗口核验；跨项目缓存不能继承。


### 未来切换配置（本次不设置）

`DOXAGENT_PERSISTENT_RUNTIME_V2_API_PROFILE=current` 是默认值；未来可选 vertex_recall、bailian_glm、vertex_glm。
Vertex 需要 DOXAGENT_VERTEX_PROJECT 与 ADC secret；GLM 需要 DOXAGENT_BAILIAN_GLM_ENDPOINT（北京工作空间兼容端点），可独立设置 DOXAGENT_BAILIAN_GLM_API_KEY。
profile 只影响新 Case 的 model manifest；Vertex project 同样冻结到 manifest。旧 Case 路由不变，保留其仍需要的原渠道凭据直至在途工作收尾。


### 首次生产试跑后的回填优化

通用 ingest 逐 metric/entity 查旧值，试跑25条约1.3秒，单向缩批退化到1条并固定休眠0.5秒，不适合49,324条保留调用。
改为只对带 usage 版本保护的 cost_backfill 路径预取旧贡献、executemany 批量移动与插入；桶结算和 MVCC 语义不变，在线普通投影路径不变。
自适应缩批最低8条（显式更小配置仍被尊重），批间延时由运维明确配置，不再自动抬至0.5秒；输出实际write/CPU时间用于确认资源影响。


### 控制派生成本数据的长期体积

试跑确认旧方案一条调用展开几十条 contributions_current 并产生逐字段 history／watermark，不能按此方式扩展全部保留历史。
成本调用现在在既有 usage 对象中保存一份紧凑 `_cost_reduction` 精确贡献快照；同 ID 更新从旧快照减、新快照加到原 metric_buckets，不再为成本单独新增几十条 SQL 贡献和水位行。
首次迁移仍把旧逐调用贡献按 MVCC 移到原 contributions_history，旧 usage 与 bucket 版本也保留；原始调用账本完全不改。Policy／graph 身份贡献保持原存储与算法。
代码确认成本和 Overview 读 metric_buckets，未依赖逐调用 cost contributions 列表；非成本 distinct 仍读原贡献表。
回退 API／Web 可以使用兼容的桶读取版本；**迁移后投影器须保留紧凑成本贡献实现，不能直接回退到旧逐行贡献写入镜像**。不需要恢复或复制整库。


### 保留历史的 Embedding 计价补齐

全账本模型核查发现1,670次 qwen3.7-text-embedding 调用，usage为 input=total、output/cache=None。
其协议没有文本输出和上下文缓存，按输入专属模型处理，而不是把 input 也作废；缓存比率排除该类调用。
北京在线输入公开价 CNY0.5/百万token，Batch价格不套在线请求；依据 https://help.aliyun.com/zh/model-studio/qwen3-7-text-embedding 。
规则版本提升为 cost-audit-20261021-v2；其它既有价格未改，黄金样本仍完全一致。


### 生产预算验证后的响应精简

双 worker、1秒队列、3秒查询预算实测：原全历史明细响应124,474 bytes，四并发中2个排队繁忙；Overview两个模块均200、约0.26秒。
明细行移除未展示的字段和恒为N/A的上一期／变化率，只传实际使用的数值、完整性、暂估标记及计价计数，汇总与逐行实际数据不变。保留完整闭合schema校验，不扩大队列或超时。schema引用展开实验无稳定收益，未纳入产品。
最终紧凑账本回归296通过；另2项既有Overview测试因当天UTC16点固定时间造成VIEW_EXPIRED，已独立复现。精简响应后14项相关API/schema测试、67项前端测试、typecheck/build及1262/1559浏览器检查通过。


### 在线写锁退让

全历史首段完成14,859条，恢复后再完成467条时遇到一次超过2秒的在线写锁；工具保留原子断点退出，未损坏数据。补上SQLITE_BUSY/LOCKED专用有界退让（0.1秒至1秒，连续最多30秒），重新读取同批及版本，不推进失败事务的cursor。成功后清零连续退让时长，输出累计busy_retries。20项成本与存储测试通过，包含锁竞争后不重计的回归。

一次性回填写连接使用16MiB页缓存（SQLite默认约2MiB），在256MiB容器上限内减少活跃索引页反复换入换出；在线API／投影器配置、同步落盘与锁时限不改。11项成本回归通过。


### 全局桶计算归并

CPU profile定位全历史报告逐条解码约19,912个标的桶，和响应schema验证共同占用CPU；撤掉回填后四并发仍有1个队列繁忙。将跨ticker归并前移到同一SQL SELECT，连接私有Decimal聚合保持费用精确；原生内容解码不用于纯标量结果。requests桶同时提供标的候选，无新增SQL查询或服务。移除requests已归零的旧轮次行／候选和空趋势日。16项相关测试通过，含大于IEEE整数精确范围的费用、全局与单标的精确一致、候选保留和旧轮次不再显示。

报告归并采用局部64位Decimal精度，避免大量既有有限小数相加时在默认28位上下文逐项截尾；原始报价和计价规则不改。新增超过28位合计精度回归，16项相关API/schema测试通过。


### 资源预算的最终收敛

全量前半程出现临时MVCC/history增长超过500MiB及写事务超过100ms，已暂停；既有GC有正确expiry索引，瓶颈是写锁占用及回收赶不上新增历史。单条50调用试验5.35秒，大多写锁几十毫秒。改为keyset每次最多读100条，按ticker/day/round/provider/model分组，每个短事务最多8条，同桶只写一次；超过100ms缩组，低于50ms逐步恢复到最多8条，逐组让出写锁。整批全部提交才推进原断点；部分已提交后锁重试重新读取版本且不双计，已用回归验证。保留原MVCC与既有GC，不做truncate/vacuum或全库副本。

profile确认解释型schema遍历占报告约一半CPU。对CostReport使用fastjsonschema 2.22.2编译当前draft-07可表达子集，价格/数值/schema原文不变。格式仍调用原FormatChecker；关闭default注入；出现2020-12新增关键字、引用附加约束或编译校验失败时，继续原Canonical验证。普通成功响应只校验一次，失败仍保留原语义与脱敏错误。对正常夹具、负数/布尔计数、非法时间、缺字段、未知字段、换行小数及未来dependentRequired验证对照通过；schema和响应不被修改。官方机制说明：https://horejsek.github.io/python-fastjsonschema/ 。本地初测10次夹具校验CPU由0.109秒降至0.016秒，最终以生产实测为准。

原始OpenRouter envelope已过保留期时，沿用同usage事实中明确标为REPORTED的冻结已报费用；若真实raw仍在则始终优先raw。不能因为归一化usage未再带provider envelope而丢掉已证实费用。保留缺原始回执标记，反复归一化费用不变；35项成本／来源重建测试通过。

精确生产对账发现旧粗粒度桶requests归零后，有限精度历史减法仍留有约1e-28美元残差；仅隐藏空行会使全局与行／ticker小计出现这个极小差值。现在同一SQL先选requests>0的ticker/day/dimensions叶子，再做精确全局归并，所有层级采用同一调用集合。没有epsilon清零，不重写原报价，失败调用和已知零费用仍保留。新增旧轮次及同轮次不同ticker的残差回归，20项相关测试通过。
