# Runtime Case 分轮与引用展示本地交付

## 修改范围

- W1/W2 尝试按 R1/R2 分组，保留已有 R3 与实际失败、重试记录。W2 空召回的 R2 明确未执行；历史 R1 未知不推断空召回，固定水位分页截断不推断零尝试。
- 展开判断依据同时展示原有 reasoning 和精确引用：Event 名称、实际归因 Fact 命题；provisional 的真实候选命题；W2 R1 候选 Policy 与最终命中 Policy 的名称分别列出。未形成最终判定不显示“无命中”。
- 只改读取与展示，不改 W1/W2 prompt、输出模型 schema、业务编排、O1/O2/O3 skill、历史业务数据。
- API 契约 Markdown、公共 TS、Python 与前端生成 JSON Schema 已同步。新字段为兼容增量；尝试续页支持可选 view_id，新客户端传固定 Runtime view。

## 后端查询边界

| 读取 | 范围与返回 |
| --- | --- |
| 原生 Case / W3 route | kind+ticker+Case ID+MVCC 点取；SQL 只选 pin、结果、状态及 W3 mode。不选择完整 payload，不打开 frozen_inputs 或消息正文。选中外部字段最多 64 KiB，超限保留未记录。 |
| 轮次与耗时 | 只聚合当前 Case 的 attempt 标量字段；返回最多节点×轮次的有限分组，不将全部 attempt payload 放入 Python。 |
| 尝试分页 / 失败摘要 | 默认每节点 20 条，游标与 view 同水位，现有分页最大100条。失败摘要仅最近20条尝试失败及一个终态；全部尝试仍可续页查看。 |
| Event / Fact | 固定 library snapshot+Event ID 点取名称；只对实际 fact_attributions 的 F# 按固定 Event parent+Fact ID 索引取命题，不拉整套事实。 |
| Policy | 固定 policy artifact+Policy ID 索引点取 title/revision，不加载 criterion、Document2 或 Policy 正文。 |
| provisional | ticker+Event ID+语义日等值、冻结 snapshot 上界，索引取最新一条候选；不加载全日或全历史候选集合。 |
| 尚无 Case R1 结果 | 从当前 Case 的 W2/R1 成功 attempt 限定取一个 turn ID，再点取该 turn 的结构化 output；不查询 raw_output/prompt。 |

无 JOIN、无历史 Case 列表或整个 Event/Policy 库载入。新增三个局部表达式索引：case_fact_evidence、case_policy_evidence、case_provisional_evidence；首次迁移由 SQLite 建索引，不通过 Python 读出全表数据。保留现有单请求查询截止与中断机制。

## 必要验证（本地）

- `tests/v2_backend/test_formal.py` + `test_case_evidence.py`：9项通过，包含正式快照名称/命题、局部缺失、R2失败无最终判定、R1 output 回读、旧 view 隔离、大块历史输入不读取、超限外部字段不打开、26条尝试完整计数及三个精确查找 EXPLAIN 索引命中。
- 前端 `runtime-case.test.ts`：4项通过；TypeScript、Vite生产构建通过。既有大 bundle 提示未扩展处理。
- Playwright 实际浏览器运行真实 CaseDetails 组件，合成 API/Content 数据：1262px、1559px 展开后无详情组件横向溢出，reasoning、事实命题、候选/最终 Policy 名称及 R2重试可见；空召回、跳过、历史未知三个分支展开/收起验证通过。
- 浏览器是组件级合成数据验证，不是远端 Runtime 页面业务验收；未重新查询生产个案、调用模型、重放消息、改业务DB、提交订单或部署重启服务。
- 公共 schema 生成器原先会遗漏手工登记的 gateway-status 与异步 query routes；本次保留原有两条登记，不删旧路由、不扩大生成器修改范围。

## 交付状态

已完成本地修复；未提交、push 或部署。既有其他对话修改与 exports 保留。现有 Pending/失败 Case 的业务执行未被本轮改变。
