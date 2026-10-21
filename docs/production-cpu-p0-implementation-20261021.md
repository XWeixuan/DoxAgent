# 生产 CPU 三项 P0 开发与验证记录

2026-10-21，Asia/Shanghai。按 [CPU 复审与容量决策](production-cpu-capacity-decision-20261021.md) 的 6.1、6.2、6.3 完成开发。状态：本地实现与回归验证；未发布生产，未执行扩容。

## 改动

| P0 | 实现 | 保留边界 |
| --- | --- | --- |
| Scheduler 稳定准入与详情构造 | 消费者记住本进程已完整准入的 activation/epoch/mode；稳定轮次核对当前状态、Bus/Runtime ACK、授权 head 和授权 receipt 后跳过重复全量加载。生产 CLI 明确 `include_detail=False`，后台 tick 不再构造完整展示详情 | 首次/重启、新版本、epoch/mode 变化、缺 ACK/授权证明仍走完整准入；每轮控制检查、新 Case 冻结输入、Policy/rearm/fencing、状态计数与事件消费不变。显式 tick 和 API 默认仍返回详情 |
| Calendar 与 schedule | 每个 Calendar 实例只解析一次安装包版本；使用同版本持久化 session/bounds 缓存；bounds 缓存缺失时惰性加载库。稳定 schedule 内容相同不再调用 set | 每次先读取 override，变更下一次调用生效；跨日和包版本重启更新缓存；保留原有离线故障兜底、语义日和 CLOSED 规则；不降低 durability |
| Bus 单轮批量状态与连接复用 | 一个同步 storage work unit 内读取 roster/poll 状态、初始化 due、计算 capacity；连接只在本工作单元内借用，poll states 返回值供本轮判断。约束计算复用本轮 sources。IR roster 复用一个 Bus 连接、一次 Runtime pause 快照 | 读操作仍 autocommit，写操作仍各自短事务；连接在回到异步/网络前关闭；不跨线程共享。配置下一轮重读，请求限流与 CPU-06 的请求开始持久化顺序不变 |

Bus 先收取并移除已完成的 in-flight，再读取本轮 due 快照。稍后才完成的任务仍留在 in-flight 中，避免刚完成的来源因为旧 due 时间被重复派发。CLOSED sweep 仍有原来的任务领取、租约、暂停和共享分发排他逻辑。

没有修改业务并发、采集频率、重试上限、CPU 配额、模型路由、Yahoo 读取顺序、W3 研究资料/Context 机制、数据保留策略。未引入全局连接池、外部缓存或新的治理框架。

## 验证

正式针对性回归 **127 项通过**，另补充 storage lane 与交易执行/恢复 **28 项通过**，合计 **155 项通过**。结果分别见 `eval/cpu_p0_implementation_20261021/regression.xml` 与 `storage-and-trade.xml`。覆盖新优化与初始化激活/恢复、Runtime 并发、日历/编排、Bus、IR、请求起始事务及 Policy 消费，并验证存储线程串行、取消时提交排空和日历的交易侧调用。

新增专项回归包括：

- 稳定准入不重复加载且继续刷新 ACK；DEGRADED 不被准入重置；activation、epoch、mode、metadata、ACK、授权 head/receipt、进程重启触发完整准入；失败重试、停止/恢复。
- 后台和显式 tick 对相同输入都消费消息、提交 stream offset、保留业务计数；只在请求详情时构造展示数据。
- 日历热缓存不重算/写入；跨日、版本变化、旧 bounds、重启惰性加载；override 关闭、提早收市、删除覆盖即时生效；原有故障兜底。
- 单轮一个物理 Bus 连接；嵌套借用与短事务；atomic intake 回滚、异常关闭和 scope 复位；禁用配置下一轮生效；组内更严格约束保留；已完成采集的 due 时序；IR pause 恢复。

新修改文件的针对性 Ruff 检查、Python 编译和 `git diff --check` 通过。测试使用本地 fixture/fake，不触发模型、券商或生产任务。

## 离线收益对照

`eval/cpu_p0_implementation_20261021/benchmark.py` 从修改前 Git HEAD 加载旧 Scheduler 方法，在相同本地 SQLite fixture 上交替运行旧逐点准备路径和新批量准备路径，稳定状态各三次。fixture 为 **9 个 ticker、81 个 binding**，不是生产的 153 个 binding；不把这项实验当成生产压测。

| 单轮指标，中位数 | 旧路径 | 新路径 |
| --- | ---: | ---: |
| 物理 SQLite 连接数 | 195 | 1 |
| 进程 CPU 时间 | 703.125ms | 15.625ms |
| 墙钟时间 | 748.973ms | 14.044ms |

三次的 eligible、roster 和 due 集合均一致。Windows 进程 CPU 计时粒度较粗；连接数和一致性结果比具体微秒数字更适合作为验收依据。该对照只覆盖 Bus 轮次准备，不包含真实网页、网络、消息解析或 Runtime 工作，**不能据此声称整机 CPU 下降 98% 或已节省某个固定核数**。

Scheduler 和日历的收益由行为/写入计数验证，实际整机降幅仍需部署后在正常业务量下对照。原报告的 0.3–0.6 核是开发前工程估算，未替换成尚未取得的生产实测值。

## 后续上线范围

如部署本次改动，应更新 Scheduler、Message Bus，以及共享 `MarketCalendar` 的 Trade Executor；其他同样使用日历的进程按实际运行入口核对。只做本地开发验证没有让这些优化进入正在运行的生产容器。

生产验收应核对实际源文件身份、ACK 与授权一致性、同等业务量的核时/PSI、采集 due 与吞吐、Read 追平、Executor 工作。保持现有并发和频率，不通过少运行任务降低 CPU 指标。

重要变更已追加根目录 `changelog`。原有未跟踪 eval/export 产物未纳入产品修改。
