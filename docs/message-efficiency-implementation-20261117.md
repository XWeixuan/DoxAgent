# 消息效率优化开发记录

更新：后续已发布生产，最终版本及服务器排查见 [发布记录](server-hang-and-main-release-20261117.md)。下文保留开发阶段的原始状态与验证结果。

2026-11-17。依据 [轻量修复方案](message-efficiency-lean-repair-plan-20261117.md) 完成本地开发，未部署生产镜像。本轮没有新增生产配置操作；Google 9 个现有订阅的相关性筛选配置沿用上一轮结果。

## 已实现

- 将现有 `deduplication.py` 明确定义为基础去重，保留硬身份、版本与正文补全逻辑。
- 新增独立的 `advanced_deduplication.py`：宽松 URL、归一标题完全相同、完整正文 ratio≥95% 三个 OR gate，按廉价条件优先短路。没有增加数字变化否决门槛。
- `SourceDefinition.advanced_dedup_enabled` 默认 true；IBKR 的旧定义与初始定义缺省 false，可通过既有源更新接口修改。现有源和新增源均适用，无需逐个绑定配置。
- Live 的检查位于准备阶段、正文序列化／Native 落盘之前；比较和冷预热均不在 SQL 写事务内。并发使用已有写队列，提交前用 ticker epoch 复核；只有提交完成的正式消息进入参照池。
- 即时、缓冲、兼容 finalization、底层批量发布与测试发布均接入 gate。缓冲批次内也去重，全部被拦截时不创建空 Stream；正式拦截保存 `DEDUPLICATED`，测试拦截沿用 `TEST_DUPLICATE`。
- 不把近似关系写入基础身份 aliases；已正式发布文章继续更新原正文，不被旧标题吞掉。真实 BODY revision 可唤醒被拦截记录，旧内容不反复重试。
- QZ 定向删除固定宣传段落；DCD 按实际 DOM 推荐模块删除 `More in` 列表。JSON-LD／DOM／trafilatura／reader 候选统一清理，DCD 的通用提取器也使用清理后的 HTML。
- 304 缓存内容及 Live 旧结果复用、通用 Raw 准备入口同样清理；内容改变时取消 304 的 digest 不变捷径、清除旧正文指纹，继续走原提交及补全流程。保留技术正文、Source／论文链接，不删除历史 Case。

## 资源边界与实现取舍

12 小时 TTL、每 ticker 1,024 个代表、全局 10,000 个代表；共享归一正文池 32 MiB，元数据按字符串大小加固定对象开销估计另限 16 MiB。后者是逻辑预算，不是 RSS 精确上限。最长可比正文 65,536 个归一字符，正文比较有 20 ms 软预算，超限允许漏去重。

URL／标题用字典；正文用长度有序数组剪枝，再调用 RapidFuzz 原生算法。过期用堆，堆冗余超过有界阈值才重建；**没有每条消息全局排序／扫描全部历史正文**。正文跨 ticker 共享，索引不持有完整消息对象。池满先移除正文特征、保留 URL／标题，代表容量满再淘汰代表。

正式 Stream 冷启动按现有索引分页预热，最多加载 1,024 个成员；预热失败进入 60 秒冷却并放行。稳定期检查不增加历史 SQL 查询。关闭开关会清除相应源的参照，生产 Bus 沿用配置 revision 刷新。

使用一个短提交锁串行化最终提交与缓存记账，与现有单写 owner 对齐；相似计算、预热和网络／模型不持有它。内存索引另有短锁，复制候选引用后释放，不复制整池正文。未增加线程池、进程池、后台扫描、数据库表或数据库索引；仅新增 RapidFuzz 依赖及锁文件记录。

测试发布使用独立 mode 桶与已有测试落库流程；测试参照目前只在进程内保留，重启后仍由原有测试正文指纹机制兜底。**正式 Stream 的最近消息预热已实现并测试；测试库未另建预热索引／扫描机制。**

## 验证结果

- 出版商、技术／能源源、Google 文档及新 gate 测试组：**158 passed**。
- 测试源 massive 分发与新 gate 测试组：**27 passed，1 skipped**。
- 新 gate 与现有 publication lookup 成本测试组：**14 passed**。
- 较宽的基础／跨源／IBKR／Google 去重回归：**86 passed，1 failed**。失败为 `test_prepare_failure_does_not_claim_or_mutate_pending_task`，涉及 RuntimeJournal 输入准备异常后状态，见下文；本轮未修改该模块。
- 新增模块及测试 Ruff 检查通过；修改范围 `git diff --check` 通过。

上述测试组有重叠，不相加作为独立测试总数。最终 Google／查询成本与新 gate 补充组 **19 passed**。现有 Live 循环每分钟输出一次聚合命中与缓存占用，不增加数据库指标写入或后台任务。

RuntimeJournal 失败已用 **git HEAD 的 journal.py 独立加载复现**：`prepare_inputs` 抛出 LookupError 后实际状态为 RUNNING、token=1，而测试预期 PENDING、token=0。证明 HEAD 原实现也存在此问题，不是本轮消息去重改动引入；没有为通过回归去修改该测试或扩张修复 RuntimeJournal。证据：[baseline 回执](../eval/message_efficiency_plan_20261117/runtime-journal-baseline.json)。

## 实际模块离线开销

以正式实现回放冻结输入，启用 20 ms 预算、实际 scope 分桶及缓存淘汰，含归一／查找／记账／过期。本地结果：参与检查 2,011 条，候选拦截 44 条；算法 P95 **2.22 ms**、P99 **5.63 ms**、最大 **11.09 ms**；峰值 1,400 个代表、共享正文约 **7.42 MiB**、元数据估计约 **1.11 MiB**，未触发计算预算降级。

这不是服务器 SLA，也不是最终生产节省量。冻结导出没有完整 publisher 元数据，正式代码不会像早期原型那样无条件删除 Google 标题最后一段来源文本；同时实际 scope 比早期原型更严格，因此 **44 与早期 110 的候选数不能直接等同**。未使用新增模型调用或生产回放证明收益；95%／同标题误拦风险仍按方案保留。

复现脚本：[benchmark_implementation.py](../eval/message_efficiency_plan_20261117/benchmark_implementation.py)，[结果](../eval/message_efficiency_plan_20261117/implementation-benchmark.json)。完整输入保留于原有忽略目录，不提交正文副本。

## 发布边界

本轮只完成开发与本地验证。部署时需同步更新会读取严格 SourceDefinition 的实际消费者；优先用源开关回退，IBKR 保持关闭。仍需在真实服务器确认 loop lag、提交延时、冷启动读取和自然流量效果。Google 本轮没有重新执行配置，也不把上一轮配置读回视为本轮自然流量验收。

## 吞吐专项复查补充（2026-11-17）

对待发布能力做并行复查后，c2b0f6509 修正：已预热 ticker 不再被其他 ticker 的冷预热锁阻塞；兼容 finalization 的证据读取与 Native 准备移到提交锁外，并按实际保留/提交的消息及 standard ID 检查和记账。486931723 进一步将预热完成标志移至代表真正加载后，保留同 ticker 冷启动互斥，避免快路径把正在加载误当已完成。既有 epoch、CAS、scope 和业务 gate 保留。

本轮组合回归 56 passed、1 个已知 RuntimeJournal 基线失败用例 deselected；最终进阶组 16 passed，包含同 ticker 冷预热并发回归。组间有重叠。新进阶能力没有参与 UTC12–13 点旧 cd57c3cf3 的生产积压，不能将其视为该旧问题根因，也不能把其上线当作全局吞吐已经恢复。主性能方案本轮未实施；并行发布与版本切换单列。

完整补充取证、容量回放及修复顺序：[吞吐守恒与长尾最终方案](../dev_plan/workflow_v2/message_pipeline_throughput_tail_repair_plan_20261117.md)。
