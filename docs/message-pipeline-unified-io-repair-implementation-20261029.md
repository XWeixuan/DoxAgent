# 统一I/O与业务性能修复执行记录

执行依据：[统一方案](message-pipeline-unified-io-and-business-performance-repair-plan-20261029.md)。本文件逐批记录实际实现、测试、正式发布与90分钟观测，不将开发测试等同业务达标。

## 实施进度

|批次|已实施的正常路径减法|验证状态|
|---|---|---|
|A|Worker薄索引及合并READY/BUILDING；Site非终态索引；删除匿名页Registry登记及逐publisher失效收尾；旧类别维护退出及重复backlog删除|Worker/Site大历史与索引回归通过；生产READY覆盖索引读取0.058ms|
|B|Read先限量退出ID/游标；DELETING声明后锁外文件工作；禁止Native复活删除中对象；复用时间floor；Acquisition RO连接/引用索引/有限schedule；Native不可变缓存取消逐chunk复核|Read/采集/GC并发、引用竞争与重启恢复回归通过|
|C|独立控制watch和维护；无5秒年龄撤销；HTTP联合容量及独立客户端关闭；Browser外层重复许可删除；Finnhub默认容量更新|113项采集/身份相关回归通过，2项匿名立即退出旧断言按新2秒burst合同更新|
|D|独立native CDP证据，控制故障不直接标坏/销毁健康Chrome；按版本发送worker配置；freeze失败保留暖页；VNC按需；非业务后台关闭|已正式发布，实测纠正Ukm启动回归及snapshot复用后的隐式串行|
|E|正文获取轮次、provider延期、首次结果字段；304复用digest；同篇多ticker一次物化；结果spool/ARTICLE_ONCE保留|正文/交付、复用/更新/首次失败/延期回归通过；真实业务观察进行中|
|F|Owner首次结果/decision与CDC、Projector、API可选initial指标契约；组合Review；必需TAIL纳入周期，checkpoint变化触发续页计划|契约及组合测试通过，正式容器已发布；90分钟完整观察进行中|

## 迁移与业务变化

- 现有SQLite表增加索引、首次结果和获取轮次字段，不增加任务库、全局门禁或恢复协调器。旧首态缺证据保留NULL/UNKNOWN。
- 临时开关仍只覆盖Message Bus；控制读取失败沿用最近明确授权，已知期限本地生效。手动关闭以applied revision确认，控制不可达时不虚报生效。
- 匿名公开publisher保留隔离Context，空闲最多2秒退出；已验证受保护身份、Profile、Chrome启动路径和Reuters不冻结约束保留。
- 普通首次失败最多一次短重试；真实provider延期保留not_before，不拿20秒执行预算裁掉60秒能力等待。完整正文更新仍30分钟/24小时，未变结果不重复分发。
- W1/W2/W3容量及模型不变；其它对话交易、退出策略、提示词和前端工作先核对再一并集成，不覆盖或丢弃。

## 生产基线

北京时间21:27只读核对：8核16GB，uptime约3小时34分钟，load约1.5–1.9，可用内存约11.6GiB、Swap存量约593MiB。Collector、Bus、BODY、Site、Supervisor保持停止；Worker、Scheduler、交易、API/Projector/Maintenance在运行。此低负载状态不能当作全负载收益。

## 发布与观测

完整观测窗口：2026-10-29 14:30:32.571819Z–16:00:32.571819Z（北京时间22:30:32–次日00:00:32）。正式Collector实际启动为起点，含冷启动、失败、修复和正式容器替换。稳态子窗口另列，不能替代整体。原始证据、测试XML及资源快照在`eval/unified-io-repair-20261029/`。

## 开发阶段整体Review

- 删除的是宽历史读取、匿名逐页写/失效扫描、每轮backlog复查、相同正文跨ticker物化、每次chunk签名复核、请求内HTTP close及配置刷新串行维护；未添加新的全局队列、门禁或恢复台账。
- GC先取得DELETING再锁外搬文件；新引用被明确挡在本地重建边界，Projector保留原坐标重试，不能导致BODY重新下载。已验证“引用先赢”和“删除先赢”、held首批越过与释放后继续回收。
- 匿名cleanup不在全局lock中等待；清理已开始则只等待原owner，未确认退出不创建第二个runtime。Protected身份仍独立driver；正常Reuters页面不freeze；冻结优化失败不强制冷建。
- 配置watch独立于完整compile和维护；明确关闭/到期仍在实际发网前判断，配置健康摘要30秒更新，避免删除高频写后旧healthcheck误判。
- 修正未变配置快路跳过checkpoint续页计划的问题。必要TAIL从新增/迁移生效边界进入分母，不倒填旧证据；旧历史仍保留原口径缺口。
- 304只有在已有同表示FULL缓存下才能复用；普通重试按获取轮次，wrapper/跳转不算不同重试；provider等待不消耗取网轮。首态不被recheck覆盖，无法还原的旧数据不填当前FULL。
- 已有20ticker共享一次BODY/ARTICLE_ONCE测试通过；这不是20ticker真实30秒浏览器容量验收。
- 本地另有未提交的交易/退出策略、前端详情及提示词改动。整合核对发现其删除ENTRY五分钟规则与现有交易测试冲突（70通过、3平台跳过、1失败）；不覆盖该工作，也不将其夹入消息系统发布。该冲突不以本轮消息优化为名修改交易语义。

测试证据：`eval/unified-io-repair-20261029/`。已完成采集/交付148项、正文/Read/API契约114项、Site/Worker/GC66项、专项成本与故障10项、前端TypeScript及67项单元测试；首轮失败及修正保留在执行历史。旧“必须后台探针解锁”的测试已按当前真实业务半开语义修正，没有改生产health逻辑来迎合旧断言。

发布前追加核对：纯已提交归档运行56项关键回归通过，确认未依赖其它会话的交易工作树。另补采集统计的范围分支读取和薄schedule部分索引，避免OR条件把48小时周期历史拖入30分钟UI窗口；探针只取窗口内周期与生效schedule，并输出首态。专用IR/SEC等读取也计获取轮，失败后的既定30分钟内容更新仍保留，不把有限首次重试变成永久取消更新。

Finnhub实际只读探针HTTP200，响应声明`x-ratelimit-limit=60`、remaining=59，6237字节。迁移只解除“上一响应结束才允许下一请求”，保留原最小发网间隔；20ticker/30秒该端点为40次/分钟，其它同账号用量仍需纳入余量，不能从一次探针保证全账号持续容量。

## 生产观察中的启动回归修复

首轮正式5fc746a6已于14:30:32Z启动Collector；14:37–14:39Z追踪ensure失败至Chrome自身退出。同镜像、同UID、临时隔离目录与Xvfb对照：只禁用domain-reliability存活、只禁用Ukm返回-11、只禁用Segmentation/Optimization存活；移除Ukm禁用参数，External及Managed一致修正。未清理任何真实身份目录。UKM后台I/O治理不能据此视为已完成，后续以真实I/O差分判断其剩余影响。首轮失败及修复停顿保留整个90分钟窗口。

后续实测发现HTTP联合许可在多人排队时会反复转让而无请求进入；8c964891修正为唤醒后立即保留该许可检查其余容量，不持许可await其它容量，40并发及100请求/6publisher/4source均能完成且容量归零恢复。Registry惰性publisher/profile写入不得进入只读scope，e41694a2补齐所有相关入口。Compose旧ENV覆盖镜像commit已在正式release契约修正，旧ENV不能作为实际版本依据。进一步删除legacy迁移完成后的旧BODY JSON检查；Bloom两个非press RSS同出口curl200/RSS有效、httpx403，固定低成本curl路线，明确配置出口不变。

## 生产阶段整体Review（进行中）

- 182项冻结提交组合回归通过；同身份双命令实际重叠13项、市场/SEC68项另行通过。开发阶段暴露的NameError/旧测试断言等均在本地修正，没有带入对应发布。
- 发现并处理了三项本轮优化引入的回归：Ukm禁用使批准版Chrome SIGSEGV；联合许可转让形成无进展循环；snapshot不再每命令发送后，worker读取并发默认1使实际串行。均保留生产失败记录，补故障合同测试。
- 发现并修复前轮减法遗留的FormalProjectors serving_skipped契约遗漏（c770409d于10月29日07:10引入SourceOutbox瘦化），约700个KeyError缺口经原有repair归零；54个缺provenance的历史问题没有宽松放行。
- HTTP仍有真实provider慢请求，不能把7.76秒实测响应归为DB问题，也不盲换curl（同端点curl8秒仍超时）。修正的是排队提前烧掉网络预算，整体45秒与网络15秒都没有增加。
- Bloom非press两RSS的curl路线已经实测200/RSS有效；补实际完成证据后才能计有效取网。Benzinga双查询用解析器绑定实际ID，未给无请求轮伪造时间。
- Globe HEAD的内部head_at周期与Collector相位冲突导致空转；正式HEAD改由Collector单一节奏触发，TAIL义务保留。SEC正式HEAD去掉2秒结果TTL捷径，继续条件请求，不变更申报/表格/正文质量合同。
- 30秒eBPF窗口（15:07:52Z起）只统计实际提交BIO并按首vector归文件：Site约0.23MiB、Supervisor约7.25MiB、Worker约0.20MiB、Collector约1.17MiB、Maintenance约0.38MiB；主要有swrast_dri.so、acquisition.sqlite3、site_strategy.sqlite3与SDK日志WAL。该短样本不是90分钟峰值，也不能与容器累计字节重复相加。完整cgroup/磁盘差分另行报告。
- W1/W2/W3容量/模型/业务入口保持原配置，Worker运行作业及3个READY已有实测；未以暂停这些能力换消息指标。
