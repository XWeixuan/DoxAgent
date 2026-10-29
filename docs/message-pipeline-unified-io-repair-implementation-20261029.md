# 统一I/O与业务性能修复执行记录

执行依据：[统一方案](message-pipeline-unified-io-and-business-performance-repair-plan-20261029.md)。本文件逐批记录实际实现、测试、正式发布与90分钟观测，不将开发测试等同业务达标。

## 实施进度

|批次|已实施的正常路径减法|验证状态|
|---|---|---|
|A|Worker薄索引及合并READY/BUILDING；Site非终态索引；删除匿名页Registry登记及逐publisher失效收尾；旧类别维护退出及重复backlog删除|Worker回归通过，Site/大历史成本回归进行中|
|B|Read先限量退出ID/游标；DELETING声明后锁外文件工作；禁止Native复活删除中对象；复用时间floor；Acquisition RO连接/引用索引/有限schedule；Native不可变缓存取消逐chunk复核|32项首轮Read/采集/Site回归通过；追加并发/恢复测试中|
|C|独立控制watch和维护；无5秒年龄撤销；HTTP联合容量及独立客户端关闭；Browser外层重复许可删除；Finnhub默认容量更新|113项采集/身份相关回归通过，2项匿名立即退出旧断言按新2秒burst合同更新|
|D|独立native CDP证据，控制故障不直接标坏/销毁健康Chrome；按版本发送worker配置；freeze失败保留暖页；VNC按需；非业务后台关闭|真实Chrome及来源业务验证待正式发布|
|E|正文获取轮次、provider延期、首次结果字段；304复用digest；同篇多ticker一次物化；结果spool/ARTICLE_ONCE保留|正文/交付回归进行中|
|F|Owner首次结果/decision与CDC、Projector、API可选initial指标契约；组合Review；必需TAIL纳入周期，checkpoint变化触发续页计划|契约及组合测试通过，正式容器发布及实测待完成|

## 迁移与业务变化

- 现有SQLite表增加索引、首次结果和获取轮次字段，不增加任务库、全局门禁或恢复协调器。旧首态缺证据保留NULL/UNKNOWN。
- 临时开关仍只覆盖Message Bus；控制读取失败沿用最近明确授权，已知期限本地生效。手动关闭以applied revision确认，控制不可达时不虚报生效。
- 匿名公开publisher保留隔离Context，空闲最多2秒退出；已验证受保护身份、Profile、Chrome启动路径和Reuters不冻结约束保留。
- 普通首次失败最多一次短重试；真实provider延期保留not_before，不拿20秒执行预算裁掉60秒能力等待。完整正文更新仍30分钟/24小时，未变结果不重复分发。
- W1/W2/W3容量及模型不变；其它对话交易、退出策略、提示词和前端工作先核对再一并集成，不覆盖或丢弃。

## 生产基线

北京时间21:27只读核对：8核16GB，uptime约3小时34分钟，load约1.5–1.9，可用内存约11.6GiB、Swap存量约593MiB。Collector、Bus、BODY、Site、Supervisor保持停止；Worker、Scheduler、交易、API/Projector/Maintenance在运行。此低负载状态不能当作全负载收益。

## 发布与观测

待记录实际commit、镜像ID/组件版本、迁移readback、启动次序、90分钟完整窗口、周期/新文章/正文分母、资源差分和未达项。

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
