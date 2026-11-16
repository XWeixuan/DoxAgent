# W3 v6 Ready 身份不一致事故与紧急修复

## 已证实根因

2026-11-16 10:47 UTC的生产只读统计：373个Case的Trade主阶段均为FAILED，错误完全相同：`trade_decision/PREPARE: public Template has not configured this profile`。A/B各373条分支记录均为PENDING；失败发生在Ready领取、模型派发之前。失败Effect已尝试3次，重试不可能解决确定性的身份不一致。

模板配置使用`hash(parent_thread_id, profile, w3_entry_version)`，v6模板因此进入v6 scope。Scheduler的领取请求没有传递冻结的入口版本，Worker领取仍写死`ENTRY=w3-entry-v5`，查找另一个scope。捕获`w3_ready_unconfigured`后重新配置v6模板，再次按v5查找，最终耗尽重试。

引入点为`dcded5e6f`（11月15日v6隔离/Operations重构）：配置端支持v6而领取端未同步。`c401cdc3b`正式启用v6标记后触发；第一条失败始于11月16日07:08:19 UTC。此前发布窗口没有自然新Case，Worker健康、模板READY和SDK参数探针均未执行真实v6领取，未能发现此集成缺陷。这是发布验收覆盖遗漏，不能归因于Provider、模型、并发翻倍或资源压力。

## 修复范围

修复提交`cbff3269b89f8f8b0b1faea1e074a3a2e662f421`：

1. Scheduler在领取请求中传递Case冻结的`w3_entry_version`。
2. Worker按请求版本计算scope；旧请求未提供版本时继续使用v5。
3. 保留原槽位、线程、Job、幂等键与公共配置生命周期。不重建旧绑定，不改Prompt/Schema/模型/交易规则，不关闭双任务。
4. 48项相关回归通过，包含v5/v6实际Ready槽位并发领取、重复领取幂等性和Scheduler v6版本传播。原测试只覆盖v5或用替身直接返回线程，新增覆盖完整版本边界。

## 发布及恢复边界

正式release owner构建后端镜像，仅替换Worker和Scheduler。其余服务不因这两行协议修复重新部署。不能为了切换而把未完成Job当作不存在；共享Worker的非W3在途任务需自然完成，或经用户明确选择后通过正式接口取消并保留回执。

失败历史Case保持可追溯，不统一改为成功、不直接重放数小时前的Trade。若需补跑历史研判，应通过隔离回测；本次首先恢复新Case实时链路。新Case的模式仍冻结为`dual_consensus`。

部署后必须检查实际Worker/Scheduler代码哈希、镜像revision、v6冻结入口、模式开关，并通过真实HTTP领取两份独立线程和重复领取原绑定的零模型探针。还需观察新自然Case是否从Ready推进到Worker Job及最终Trade；未观察到自然结果前，不能只凭容器健康宣称端到端恢复。

## 生产发布与实际验收

11:04 UTC仍有同类失败，累计已确认397条，均无已受理Worker Job。按本会话既有“持续在途允许中断”的授权，进行一次短发布窗口：先停止Scheduler新派发，正式取消剩余两个`runtime-maintain-*` Job，获得确定的`cancelled`回执后替换Worker/Scheduler；没有取消W3模型任务。不是将网络超时或派发未知当作未受理。

此前自然完成了其余维护Job；发布期间另将58个未领取后台任务暂置HELD并记录准确名单，11:07全部恢复。名单之外原有HELD任务未改动。期间曾按容器名称误暂停`v2-maintenance`，核对发现其实际是Read GC而非O2/O3派发者，已立即恢复；最终没有任何容器遗留paused状态。O2/O3实际由Scheduler派发。

11:06 UTC修复版本生效：

- Worker/Scheduler镜像均为`sha256:870718ba2b48075e2103fff8f2bdd3fdec92d80fb7b5c13d1648f0107f888e23`，revision为`cbff3269b`；实际文件哈希与该提交一致。未改其他组件镜像。
- `dual_consensus`仍开启；Luna/max、Trade fast、Operations default及全部冻结提示词保持原配置。
- 11:07真实HTTP探针：两份v6 Ready领取均COMPLETED、线程不同，重复领取返回原slot。零模型调用，无业务Case或交易；完成后通过原接口释放并删除探针线程。
- 11:09已有11个新自然Case进入W3，其中5个已提交Trade且Operations已提交；其他正在执行，无新阶段错误。之后的持续观测与最新计数保存在[业务回执](../eval/w3_emergency_20261116/business.json)。
- 11:11:42 UTC更新：18个Case已进入，14个Trade及对应14个Operations均COMMITTED，另外4个Trade仍执行中，发布后阶段错误为0。不是将PENDING计为成功。
- 抽查3个真实已提交Case的完整持久化读取：Native解码成功、双任务摘要存在、A/B线程不同、Operations线程等于选中分支线程。不是仅检查容器健康。
- 被取消的NBIS、INTC两个维护任务已由既有恢复机制重新推进，原Job仍保留cancelled回执；未把旧Job改写为成功或静默删除。
- 扩大回归另83项通过；两项旧断言失败在未修改的`384f0687a`源码上再次复现：后台共享槽位旧预期8、当前为7；旧计时诊断预期本地文件、当前写日志。不是本次Ready修复引入，未借紧急修复扩大范围改旧测试。
- 发布后采样可用RAM最低约11.7GiB；Worker约5.6GiB、Scheduler约1.1GiB。原有Swap仍接近2GiB，11:07:45—11:11:39新增换出1197页，约4.7MiB，不能描述成“零换出”。没有以清空Swap、放宽超时、限制W3并发或隐藏重试来达成恢复。

本次已验证真实自然Case的Trade和Operations恢复。该小窗口不构成高峰容量或非SDK P95验收；本次也不重新评价提示词业务准确率。历史失败记录保持原样，不能用清零失败数冒充恢复。
