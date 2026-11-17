# Runtime 全局非 SDK 延时修复：本地实现与验证

日期：2026-11-18。依据 `runtime_global_nonsdk_latency_repair_plan_20261118.md` 实施。当前为**本地代码完成并通过所列回归，尚未发布或验收生产性能**。没有修改模型、Prompt/Skill、模型输出 Schema、交易规则或双任务选择规则。

## 已实现

### 可执行库存与资源保护

- 当前 Luna 的基础私有 READY 默认目标改为4。配置、fill和PRESSURE保留使用同一scope目标，旧模板同名模型不占当前scope保护名额。
- 基础补货独立于投机CPU/I/O滞回，仍使用原单后台冷启动owner；真实Case继续bypass。实际主机/aggregate内存短缺时停止基础补货并允许回收闲置库存，CRITICAL及busy/handoff保护保留。
- 可选投机PSI触发/恢复参数改为CPU 80/60、I/O 30/15。它们不是实时Case的准入门槛。
- W3不再额外维持一份通用warm库存；保留底层干净Capsule借用能力。未开启执行过Case的物理client跨Case回收复用。
- 后台维护先发起已有即时fill，不等待research reconcile完成；认领/build完成仍复用原即时补货机制。
- fill先查当前库存，足量直接返回；最近成功查询使用scope/ready_at索引。readiness改为只读，只统计实际存活、当前活动profile的READY，不再要求闲置Sol。

### 准备、通知和短写

- BOOTING导入微阶段不再反复更新operation，保留本地boot receipt及启动次数、Fork前状态、child身份等恢复屏障。
- child registry与operation COMPLETED在同事务提交；带owner的更新检查CAS结果，所有权丢失不继续派发。
- 同进程准备等待使用现有task的完成唤醒；恢复路径仍按持久operation校验。原100ms检查在task存在时改为最多250ms有界join，未删除恢复期间的状态读取。
- Research MCP就绪改用通知等待，正常真实MCP无需50ms轮询；保留兼容测试/嵌入适配器的降级读取。
- 禁用data能力的W3不再提前导入和构造Data MCP契约/权限注册器；原能力启用时仍准确创建。
- 同内容model selection等派生文件免重写。成功ACK后的resident release标记删除并入ACK提交。
- Case源回执忽略纯技术`updated_at`变化，包括JSON内同名字段；实际业务/终态变化仍生成原版本回执，未改成读取最新行替代事件。

### 版本发布与事实可见性

- Bridge直接对比当前固定版本可见内容、当天冻结基线和已有provisional，不调用Fact首次入库历史追溯。
- 复用原PROCESSED provisional；候选与head完成标记同事务提交，重启后的已处理head可直接跳过。日内内容身份确定性去重，避免不同head并发加入同一事实。
- 维护激活完成后准备可见性；Case仍有一次性版本处理兜底，覆盖其他激活入口与提交缝隙。未激活候选不进入全局provisional。
- 激活时预编译当日/次日14/7筛选结果，存在现有Runtime元数据/内容存储中；首Case仍按当时实际版本冻结，预编译不提前冻结当天索引。
- `ActivatedRuntimeInputs.public_pin`仅取产物身份，公共Template准备不再加载完整事件索引和Policy projection。research pin及Worker重新配置能力保留。

### GC与上下文保护

- 在原asset表增加`retained_until`，context绑定/延期先提升对应资产的保护高水位；COLD衍生物从原不可变source在writer外恢复。
- 旧NULL水位在有界context回填完成前不回收，回填每轮有时间预算并按token游标推进。
- GC使用有限元数据候选；manifest/source读取、hash、递归删除不在写事务内。仅目录原子rename与退休CAS保留为必要的短原子屏障。
- 删除前重查保护水位，hash期间新增引用不会被删除。删除失败的tombstone后续可清理，且不清理尚未提交退休的目录。
- 同phase重复绑定不重写已存在的不可变manifest；原token/hash及不允许修改冻结上下文的校验保留。

## 验证

新增 `tests/test_runtime_global_latency.py`，覆盖基础补货/严重内存保护、当前scope四份库存、MCP通知与清理、重复绑定免写、未提前日冻结、Bridge持久幂等、不查首次入库历史、纯技术更新时间免源回执、GC与新引用并发。

实际通过的组合：

- 88项：Lifecycle、原身份恢复、READY、研究MCP、日冻结、资源GC、维护恢复等。
- 74项：v6、双任务一致性、投影性能、维护恢复及新增保护测试。
- 最后修改后26项：GC/可见性/冻结边界回归。
- Ruff F821与`git diff --check`通过。三组有重叠，不相加声称唯一用例数。

旧测试仍预期为Luna和Sol创建两个公共模板，已改为当前统一模型的一份模板及原模板复用断言；没有通过恢复Sol调用来满足旧预期。GC夹具显式模拟过期的资产保护水位与更新时间，适配新高水位保留语义。

日志与JUnit：`eval/runtime_global_latency_design_20261118/implementation*.txt/xml`。没有声称全仓测试全绿，也没有发起付费模型调用。

## 发布与剩余验收边界

本轮没有提交、推送或生产发布；并行对话的Message Bus、Site及其changelog改动保持原样，未混入本轮验证结论。本轮变更已追加`changelog`。

完整Runtime P95≤10秒尚待正式发布后的至少100个连续自然Case和开盘高峰验证。4份真实空闲子树PSS、补货吞吐、缺货比例、CPU/I/O及写入归因也仍需实测；本地fake SDK测试不能代替这些结论。

方案要求依据实测再决定的部分未盲目启用：执行过Case的物理client reset复用、将4提升到6、修改Native SDK内部状态库、放宽SQLite同步/timeout、迁移canonical首次内容入库属性。当前采用日索引发布预编译路径；历史原始入库记录与14/7规则仍保留。

仍保留的必要等待包括未知派发恢复、有限的恢复进度检查、GC原子rename，以及缺货时真实的Native Fork。它们全部应计入后续内部耗时，不能以后台/SDK辅助步骤的名称排除。尚未定位到具体文件的Worker高写入，不列为已经消除的收益。
