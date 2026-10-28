# Message Bus 精益化实施记录

执行依据：[全链路方案](message-bus-end-to-end-lean-optimization-plan-20261028.md)。基线main：7e7e3538；其它会话已有10个修改文件已保存差异与哈希，不覆盖或丢弃。

G3按本次“严格按照方案”授权采用：首24小时30分钟条件重查，之后保留provider修订/再次进入列表/TAIL触发更新；已存在recheck积压仍各执行一次，不直接标完成。极旧无信号静默修改检测的覆盖边界按方案明确记录。

|包|状态|验证/说明|
|---|---|---|
|W0 契约/基线|基线已固化，持续核验|81项基线回归；原工作区差异与哈希已保存|
|W1 故障域止损|代码及故障回归完成|publisher分池、curl异常归还、坏池代际恢复、失败checkpoint、许可异常安全、PSI硬门禁关闭|
|W2 写路径基础|代码及必要回归完成|标量claim/renew、Native单遍读取与有界缓存、维护锁外准备；20ticker大正文writer内禁止Native I/O|
|W3 实时收缩|代码与迁移/契约回归完成|三类薄事实、直接交接、原子发布；旧实时relay不再创建；ARTICLE_ONCE、跨源旧发布接回、sweep续作及投影已回归|
|W4 正文/收尾|代码及必要质量/崩溃回归完成|最多两条可执行路线、5秒普通重试、结果spool；正文与本地交付分离、wrapper在途合并、公开正文复用|
|W5 Site/Chrome|主体实现，未做生产容量实验|公开HTTP薄入口、独立只读连接、catalog缓存、取消与anchor回收；综合回归78项通过|
|W6 生命周期/健康|代码与96小时等效保留期验证完成|48小时原始证明/7天小时摘要、失败分母保留、输入与receipt到期、过期checkpoint代际回收；真实列表进度健康、薄BODY cohort及按需展示状态|
|W7 全量验收|本地组合回归及受控成本试验进行中，生产待上线|本地与生产、自然24h/72h增长分别记录；不以合成replay代替真实容量|
|最终review|本地逐包review已执行，组合与上线复核继续|修复Native正文重复写、旧文章伪新建/重复发布/版本递增、旧结果覆盖新owner、语言推断、无效Reader拒绝、HTTP Timeout对象兼容；相关源码通过F821检查|

执行证据：`eval/message-bus-lean-implementation-20261028/`。未完成项不标为已实现；本地测试不替代生产时效和持续稳定性验收。

### 本地验证与当前边界

- 基线81项；综合管线/Site78项；最新采集/开关/防务68项；正文质量58项；迁移与投影2项针对复测通过；后续组合测试仍在推进，不将重复运行相加成独立用例总数。
- 20 ticker共享一次正文、正文实际更新不增加stream、提交失败接回spool、Native缓存变更检测、curl真实socket发送、旧任务一次性转交、业务wire/正文cohort已有必要回归。
- `test_message_bus_v2_deduplication::test_prepare_failure_does_not_claim_or_mutate_pending_task`暴露基线RuntimeJournal的先claim后prepare语义与旧测试不一致；该RuntimeJournal文件本轮未改，单列，不为通过消息管线测试去改W1/W2/W3行为。
- HTTP起始角色配额明确拆到实际进程/loop：Collector直连5、Yahoo专用loop1、Bus sweep直连1、其Yahoo loop1、Site CRAWLER HTTP4，共12；BODY直取2、Site BODY/Reader2，共4。普通publisher2；不把每进程各自的容量误称全局容量。此分配须经生产容量数据复核。
- 生产仍未切换本轮代码；已读核对远端角色版本和健康，W7生产自然窗口及长期增长尚未验收。

### 2026-10-29 整体review与发布准备

最终组合回归：558 passed、1 skipped（`acceptance.xml`）；SEC/词表/Decision Model/旧去重语义72 passed、1个已确认基线claim测试未纳入该组（`semantic-final.xml`）。最后正文更新/别名/复用针对复测23 passed（`material-final.xml`）。标题更正与正文变化均修订原消息，单纯复查不制造修订。未将多组重叠用例相加宣称独立总数。

上线边界追加：启动不再读取已有Collector绑定的旧Native轮询历史；旧未发布正文recheck与新稿发布分离，实际检查一次并保持原first_seen和原发布门禁；迁移时停止所选旧API/Projector，防止旧投影器消费新契约。针对回归15 passed（`launch-final.xml`）。本地未提交交易/退出策略/UI与远端原compose差异均保留，不混入本轮镜像提交。

最终减法复核：过期、从未接纳且无旧发布的候选在Native发布前退出，持久化列表/delta与ACK足以续作，不再写一套永远不会发布的文章/交付。旧发布更新及旧recheck保留；过期大正文Native写0、旧recheck实际执行等针对验证通过。后续仅做发布必需问题修复，不再扩大范围。

### 生产上线与必要修正（10/29）

- `976dafa7`已推送origin/main并正式重建/上线Collector、Bus、BODY、Site、API、Projector；显式迁移退出0，六角色运行，Collector/Site/API健康。Chrome Supervisor与Worker沿用原容器/版本，未改Profile或W1/W2/W3容量。
- 初检Read位置与Bus head均为9325819，最新5个Owner已发布项均在Read可查；链路已实际发布。停服前旧主机故障按用户要求不扩展排查。
- 17:07:38—17:09:38 UTC快速自然片段：792个应执行周期、624个有有效新鲜列表证明（78.79%）、15个missing、21个pending；仍有IBKR超时、个别RSS/Calcalist403、浏览器频道失败，未达到99%。仅1篇新canonical文章FULL，不能据此宣布真实正文率>80%。该文章5个交付尚未完成，最大等待119秒，不能宣布≤10秒。
- 由此发现并修正计划内快路径缺口：旧迁移/复查的本地分发与新稿共用按due排序的32项批次。正文领取及交付改为新稿/实质更新优先、同优先级新近文章优先；每8轮保留一次最老工作机会；实际生产pump每250ms交还调度，不让32个旧准备任务连续占住快路径。旧Raw已有经质量验证的完整正文直接复用，并沿用更早观察时间。
- 新旧40项排队、原完整正文禁止再次下载、250ms切片等针对验证通过；相关管线组合34项通过，最后针对3项通过。仅重建受影响Bus/BODY，协议与schema不变，其它四个976dafa7角色保持兼容。
- 初检资源：Collector约116MiB、Bus189MiB、BODY192MiB、Site4.11GiB、API475MiB、Projector541MiB；全机MemAvailable约2.4GiB，CPU/IO/内存PSI仍高。此为启动和历史工作衔接期快照，不能声称持续稳定或性能目标已达。
- 后续自然24h/完整交易峰值/72h资源增长、全部必需频道独立对照和FULL最终publisher抽查仍未完成。用户要求即时上线，本轮不等待这些窗口；失败及未完成样本全部保留，不用小样本或统计修补代替性能提升。

后续短检查与发布必需修复：

- `df46c7f3` Bus/BODY已正式重建完成，退出0。17:29:49—17:31:49 UTC片段：792 expected、636 fresh（80.30%）、16 missing、29 pending；3个应交付项均完成，平均5.541秒、最大10.005秒，2/3严格小于等于10秒。3篇eligible中2篇程序标FULL（66.67%）。样本仍不足且目标未全达，不能取整或仅保留成功项。
- 此时Read位置与Bus head同为9329028，最新5个发布项均可读；capture PENDING已为0、result spool为0，本地旧分发PENDING/L2_DUE已消化。仍有真实站点403、IBKR超时和BODY访问失败。
- 检查发现BODY转交旧By Distribution任务时，binding_json为`$snapshot`，直接当完整binding读取触发`KeyError: binding_id`并导致容器重启。已补锁外展开和单项失败隔离：5秒标量重试不重写完整job，不结束整个BODY。真实旧snapshot夹具与故障隔离4项回归通过，仅需重建BODY。
- 已按运行容器、正式compose、模板/repair引用及当前/回退版本核对，保护35个镜像依赖，清退4个明确无引用旧镜像，错误0；未删volume/Profile或运行/回退镜像。未触发全局builder prune。

- 正常链路为：物理采集并持久化delta → Bridge锁外准备 → 一次薄文章/交付接纳 → BODY共享结果 → 本地相关性/发布。慢L2不会阻塞直接命中，BODY提交失败接回spool，不重新下载。新实时工作不再产生旧PollJournal/DistributionRun/EnrichmentJob副本。
- 同篇旧文章按强URL别名跨发现来源接回已有发布；原Raw/Standard/Stream ID、first_seen、源归属及控制origin保持。未变化只关联交付，不新增业务修订；真实变化更新原文，已消费过的下游不会再次触发。原发布更新无需再做一次新稿L1/L2准入。
- Source/binding纯配置版本和roster变化不重抓旧正文；新增共享订阅触发一次当前列表刷新，只关联新目标。旧wrapper与已运行canonical工作合并，保留已有owner。BODY/公开复用仍检查语言及真实访问scope。
- Raw及Standard的大正文均使用共享不可变内容；发布引用以现有artifact_ref保护，避免技术CDC过期后误清正文。未变化recheck复用原result_ref，按需读UI状态，不重写20份ticker全文。新旧BODY cohort通过薄alias合并。
- 100轮未变列表：真实adapter执行101次（含首轮），首轮后新增业务事实/Native文件为0。20ticker约十余万字正文：一次获取、20条stream；真实更新仍20条stream、40份内容修订。69次本地writer事务max/P99约32ms，writer内Native读/写为0；此数值不冒充8核生产性能。
- 96小时等效输入：20ticker、30s周期，原始逻辑轮次维持48h×120×20=115200条；48小时过期失败轮次以960份小时摘要保留，fresh仍为0。最终SQLite约75.3MB；测试验证文件复用而非线性随运行时长增加。物理证明一份、逻辑关联仍有存储成本，本次未宣称达到方案768B/物理操作成本预算，生产需实测复核。
- 整体回归曾遇本机C盘低于1GiB，既有磁盘保护正确阻断Site；迁至D盘后Site/浏览器62项通过，不降低保护阈值。系统临时目录清理被自动审批阻止，未执行删除。
- 已单列的基线不一致：旧source清单断言未覆盖已存在科技/能源源；Globe旧聚合query测试预期失败后继续第二query，而当前HEAD行为是结束该轮、独立unit下周期恢复；RuntimeJournal旧测试预期prepare前不claim，当前HEAD已经先claim。相关源清单、Globe和RuntimeJournal实现均非本轮修改，不为刷绿改动其业务语义。
- 生产在上线本轮代码之前曾发生主机故障并由用户重启；按10/29追加授权，已使用正式compose停止Collector、Bus、BODY、Site四服务，数据/配置/Chrome身份保留，W1/W2/W3未新增限制。用户要求本轮不扩展排查旧主机故障，完成计划后即时重建上线。
- 发布入口：`scripts/ops/storage_release.py release --commit <已提交版本> --collection-migration --component v2-message-collector --component v2-message-bus --component v2-content-enrichment --component v2-site-access --component v2-api --component v2-projector`。只执行小表/索引/CDC增量迁移，无全库Native展开/ANALYZE/源epoch重置；保留旧数据与一个可核验回退版本。Worker/Scheduler未改变调用契约，不为统一hash打断W1/W2/W3。
- 新只读验收脚本：`python /app/scripts/ops/message_bus_lean_acceptance.py --minutes 30`；包含所有应执行周期、真实wire/列表证明、未完成/失败正文、逐article×ticker发布时间。自然样本、FULL最终publisher抽查和T2单独验证，不能用本地样本填生产分子。用户要求即时上线，本轮不为等待长时间验收延误发布，未达到的自然24h/72h窗口如实保留为待验证。
