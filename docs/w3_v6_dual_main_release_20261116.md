# W3 v6、双任务与本地 main 整合发布记录

日期：2026-11-16。本文记录本轮审查、修复和正式生产发布；离线测试通过不等于生产业务质量验收。

## 发布范围与版本

- 整合提交 `c401cdc3b74597534e6186919b527cfdf132976e`：审查并提交本地待提交代码、测试、提示词、相关方案及必要回测资料，共482个路径。包括W3 v6按Case隔离、Operations提示词分阶段注入、统一模型和双任务门控、回测上下文对齐、W2R2引用排除及前端健康检查。
- 发布前补充修复 `27d8c0ab7e60949d73073374faa9081167ffdbd8`：双任务聚合结果使用RuntimeCase根字段保存，避免破坏大上下文的Native引用。
- 原始审计归档和临时抓取结果未作为源码整体入库；未删除这些本地资料。远端独有changelog尾部经备份、合并保留；未覆盖其他生产热修补丁。
- `contract.json`明确启用`persistent-runtime.w3.v6`，入口`w3-entry-v6`，资产布局`agents-skill-operations.v1`。AGENTS、Skill、Operations及契约一起版本冻结。

## 生产行为

所有新Case统一采用`gpt-6-luna / max`，不再根据Policy HIT/RELATED选择Sol。Trade开启fast，Operations关闭fast。同Case继续继承自己的线程，跨Case不复用对话历史。

本轮开启`dual_consensus`：两份Trade任务保留独立记录，仅在聚合后提交一次业务结果。两份方向相同才保留该方向；不同则NO_TRADE。存在NO_TRADE时选该回执作为正统来源；LONG/SHORT冲突时由编排形成明确标记的NO_TRADE，不伪造模型输出。一致时按耗时选择正统回执；仅发起一次Operations。已有Case保留冻结模式、Job及线程身份。

W1/W2的新旧、HIT/RELATED仍为各自判断，独立于LONG/SHORT/NO_TRADE。Operations不改判这些结果，按v6职责维护PIN/WATCH。没有降低实时W3并发、引入外部队列或扩展线程池。

## 最后边界检查发现并修复的问题

原聚合提交用`json_set(..., '$.frozen_inputs.w3_consensus', ...)`写入摘要。大于16KiB的冻结上下文在数据库中是Native引用对象，增加该子字段会破坏标记格式，后续读取报`ContentUnavailable`。此前小上下文测试未覆盖。

将冻结输入放大至30KB后，三个聚合提交用例均复现。修复改用可空的RuntimeCase根字段`w3_consensus`，保持Native引用完整；API仍通过原有`receipt.trade_consensus`展示。不改变Agent输出Schema，不反复编码大上下文，也不新增数据库列。修复后119项相关测试通过，补充的三个原始Native标记保全断言也通过。

发现问题时，尚无新自然W3 Case进入。暂时将新Case模式切回single，完成修复镜像发布后再开启双任务；未通过中断原Job或重放交易解决。

## 验证方法与结果

- 整合回归245项通过；排除一项已在原始基线复现的历史时钟断言失败：`test_replay_uses_production_two_stages_with_isolated_evidence`期望READY而历史时间产生RUNTIME_DELAY_EXCEEDED。
- Native修复相关回归119项通过；另三项Native保全断言通过。Ruff F821检查通过。
- 前端schema/typecheck/Vite构建通过，仅有包体积提示。
- 使用正式host release owner、发布锁、已构建候选镜像、健康检查及回滚/镜像生命周期流程。切换前同时核验Worker Job与Scheduler W3 Effect空闲。
- Worker/Scheduler实际源码及AGENTS、Skill、Operations、contract哈希与发布提交逐项匹配；所有16个本轮受影响组件核验运行镜像及revision。未更换无关浏览器基础容器。
- 零模型调用探针验证实际SDK组装：Trade为Luna/fast，Operations为Luna/default。该探针使用测试SDK替身，仅验证配置，不冒充真实模型成功调用。

最终生产镜像、开关及资源观测见[发布证据](../eval/w3_main_release_20261116/verification.json)。

## 最终生产核验

2026-11-16 01:59 UTC（北京时间09:59）完成修复版本核验：

| 项目 | 已确认状态 |
| --- | --- |
| 镜像代码revision | `27d8c0ab7e60949d73073374faa9081167ffdbd8` |
| 后端镜像 | `sha256:81ae63ee5c588d5eb5950257296582489ac87835656f9fea91408ba8324fc4ab` |
| 前端镜像 | `sha256:5581686b531dd508d3b46350e3b4cf11935e5205ee6d2eb072dfee2c37a7046d` |
| 组件 | Worker、API、Projector、Scheduler、Maintenance、Web、Initialization、CDECR、O4、Executor、Delivery、Transport、Control、Content Enrichment、Message Bus、Message Collector，共16个；有健康检查的均healthy，其余running |
| 双任务模式 | `dual_consensus`，只作用于新W3 Effect；旧任务不追溯加副本 |
| 入口 | BE/CEG/COHR/INTC/IONQ/MU/NBIS/ONDS/RKLB/SNOW十个ticker均为v6入口Bundle `9b5c25264a1e0e751aa3e14bb3161bc227f4275440217c66abfa00b232c03bc9` |
| Ready | 2个Luna/max/v6 READY slot；另外旧slot处于退休生命周期中 |
| 资源 | 01:31:44—01:59:52 UTC共8次低开销采样，可用内存最低21.0GiB，累计新增swap-out为0；最终磁盘可用约67.3GiB |
| 业务待办 | 最后采样无PENDING/RUNNING/PENDING_RETRY Effect；最新Case仍为11月15日，未观察到新自然Case |
| 发布生命周期整理 | errors为空；仅由既有release owner处理确认可退役镜像，无卷或Profile删除 |

代码、模板和开关已生效；尚未完成双任务自然业务与高峰验收。后续仅提交本报告、changelog和脱敏回执的文档提交无需再次构建镜像；生产镜像revision以上述代码提交为准。

## 验收边界与已知风险

最新提示词rev3已有离线结果为35/49，上一版为41/49，出现8条误交易、6条漏交易。按本轮明确要求发布最新版本；这项质量回退没有被宣称修复。相同模型、提示词及输入可能产生相关错误，不能将双任务错误概率简单相乘。

发布时处于自然消息安静窗口，没有新自然W3 Case可供端到端业务验收。因此资源观测只证明发布窗口稳定，不证明双任务高峰资源容量、方向一致率、费用可持续或非SDK P95≤5秒已达标。未人为构造会进入真实交易链路的生产Case。

另观察到历史线程清理行出现HTTP500、状态DELETING并被housekeeping再次处理。这是旧线程清理告警，不是本轮新W3业务失败；本轮未扩大范围修改其生命周期。
