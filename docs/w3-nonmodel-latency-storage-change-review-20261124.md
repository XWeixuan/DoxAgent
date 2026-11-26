# 上轮磁盘治理对 W3 读写与非模型延时的影响梳理

日期：2026-11-24

依据：[实施与验收回执](global-disk-lifecycle-implementation-20261124.md)，以及发布代码 `9bde19577 → 66d538196` 的差异。本文件只归纳改动及潜在风险，没有重新测量远端延时，也不构成新的根因结论或修复实施方案。

## 1. 核心判断

上轮确实修改了 W3 的持久化和上下文读取路径，不能仅凭磁盘写入减少，就认定非模型延时一定改善。

最值得优先关注的两个方向是：

1. 后台治理与 Runtime/Worker 对共享 SQLite writer、磁盘和页缓存的竞争。
2. 冻结数据拆分后，在真实 Case 保存、冷读和恢复中的同步编解码及文件访问成本。

其次是 D3 共享内容的冷读取、历史 projection 重建，以及附加到 Case/Worker Job 事务中的引用检查。以下风险均表示值得核对的机制，不表示已确认它们造成生产延时回退。

## 2. 涉及 W3 非模型延时的修改清单

| 修改 | 实际改变 | 对延时的影响与风险 |
| --- | --- | --- |
| Runtime Case 冻结数据拆分 | `save_case`、`save_case_with_effects` 将 `frozen_inputs` 按字段存入已有 Native CAS，直接字段达到约 2KiB 即可外置 | 直接涉及热路径。避免每次状态更新重写整个冻结容器，但保存时仍同步执行 JSON 序列化、哈希和文件存在检查；新对象需要压缩、写文件及 fsync。拆分可能增加小对象与冷读次数 |
| Case 资源引用登记 | 新增 Case 插入、状态变化、删除触发器，维护 Event/D3 精确引用 | 直接增加原 Case 写事务工作。状态变化时执行引用删除/登记及索引维护；普通 Context 更新不额外登记。单次预计较小，但高频状态推进时可能累积 |
| W3 上下文读取精简 | `PublishedW3ContextProvider` 不再额外加载 bundle、published document 和完整策略导出，使用固定版本的 typed PolicySet | 主要减负，去掉一份完整导出的读取；没有取消全部 PolicySet 加载、解析或上下文组装 |
| D3 按 Policy 共享内容 | formal/staged 保存共享内容引用，读取时解压、解引用并解析；历史 projection 缺失时按需重建，当前热投影保留 | 减少重复存储，但冷读取可能从一次大正文读取变为多对象读取。缓存未命中、版本变化或旧版本重建时，可能增加模型调用前的准备耗时 |
| Policy 授权复用 | 相同 D3 release 的 O2-only activation 复用授权证明，避免重新读取、解析完整策略集 | 主要减负。增加少量标量查询/回执写入，减少大对象处理；首次遇到新 release 仍走完整路径 |
| O3 暂停、O2 精简 | O3 不再创建/消费任务；O2 supplement 直接查询 Case、parent、候选标识，不展开完整 Context/effects | 主要减少与 W3 的资源竞争。但 O2 仍正常执行，仍会消耗 Worker、CPU 和 I/O |
| Event 更新与读取变化 | O2 复用生效 Event 库、不再逐 run clone；相同内容不造 revision；第一获知证据迁移完成后走轻量查询 | 整体减少复制和重复查询。O2 更新与其他消费者读取同一仓库更集中；迁移未完成时仍保留旧 lineage 读取路径 |
| 后台治理移到 Transport 进程 | Runtime owner、Event 历史、旧 Case 证据迁移从 Scheduler 解释器移出；有工作时约分钟级推进，D3 过程治理约五分钟级 | 避免 Scheduler GIL 被历史治理占用，但独立进程仍共享 Runtime/Research 数据库、CPU、磁盘和页缓存，不能据此认定不会影响 W3 |
| 传输退出与 Native GC 恢复推进 | 重访游标前已到龄数据；忙时也允许少量精确退出；旧文件 inventory 改为小时级 | 减少积压与反复扫描，但相较此前延后执行的清理，生产繁忙时确实会多出一些删除、WAL 和文件 I/O |
| Worker 会话退出登记 | `JobStore.save` 接入本地 owner 检查；非 Maintenance 的显式 thread 复用做引用查询；维护任务 ACK 登记退出 | W3 没被纳入新的 Maintenance root 删除，但部分 W3 Job 保存路径可能多出索引查询。后台清理还会短暂使用同一个 Worker Jobs writer |
| Native SDK 日志与删除补丁 | DB 默认 WARN、摘要限长；日志批写事务顺带有界删除；已证明独占的维护 root 删除跳过全 rollout 扫描 | 整体应减负。残余风险是日志删除仍占用 SDK 自身 SQLite writer；维护线程退出仍调用共享 SDK 服务 |
| Read 投影减量 | 相同 D3 不重复 repaint；只展开展示所需冻结字段；fact 同步分批续跑 | 主要降低与 Runtime 的资源竞争。Read 追平速度可能受批次限制，但 Read 展示延后不能直接等同 W3 执行延后 |
| 恢复、Selection、初始化减负 | 补偿恢复改为小页元数据；Selection 先筛标量再展开；部分 ready-schema 构造跳过重复 DDL | 主要减少启动/调度中的大 JSON 处理，并不代表原有启动恢复成本全部消失 |

## 3. 可能加重 W3 延时的风险排序

### 3.1 第一优先：后台治理争用共享 writer 和磁盘

这是最可能造成周期性延时尖峰的方向。治理工作移出 Scheduler 解释器，减少了同进程 GIL 竞争，但没有隔离共享 SQLite writer、设备 I/O 和页缓存。

后台设置的 **10ms busy timeout 是它等待锁的限制，不是它拿到锁后持锁时间的保证**。后台已经拿到 writer 时，W3 仍可能排队。文件删除的约 50ms 预算也不是整轮治理耗时上限，更不能直接解释为 W3 最多只受影响 50ms。

需要区分正常保留期退出与一次性旧数据迁移：后者可能在迁移阶段带来额外资源消耗，不能直接推断为长期常态。

### 3.2 第二优先：冻结字段拆分的同步成本

这是最直接改到 W3 热路径的地方。Case 保存前仍会进行序列化、哈希和文件操作；业务读取时还需要重新拼装冻结内容。

隔离回放中，12 次状态变更的 Native 新增字节减少 91.69%，编码总耗时从 1.233s 降至 0.573s，说明该样本总体受益。但它没有覆盖生产冷缓存、多 Case 并发及 P95/P99，不能排除某些场景因小文件访问、反复序列化和解压而变慢，也不能把这组编码耗时当作 W3 全链路耗时。

### 3.3 第三优先：D3 冷读取与 projection 重建

当前热投影保留，因此主要风险集中在首次加载、版本切换、缓存失效，以及读取未保留 projection 的旧版。

若这些操作实际落在 W3 启动或 Context 准备过程中，就可能直接增加非模型耗时。共享内容减少存储副本，并不自动保证每次读取更快。

### 3.4 第四优先：业务事务中的新增引用检查

Case 状态触发器及 Worker Job 保存中的检查，都附加到了原有业务事务中。它们比全库扫描轻很多，但不能视为零成本；在频繁状态推进或 writer 已紧张时，可能产生累积影响。

### 3.5 第五优先：SDK 后台退出与正常请求竞争

日志过滤和消除全 rollout 扫描的方向总体减负，优先级低于上述四项。但日志删除、维护线程退出与真实 W3 使用同一套 SDK/存储资源，仍存在共享资源竞争的可能。

## 4. 主要减负项

以下改动从方向上主要减少非模型负担，目前没有依据把它们列为首要延时嫌疑：

- 暂停 O3，减少模型任务、Context 物化及发布过程与 W3 的竞争。
- O2 不再逐 run 复制 Event SQLite，相同内容不制造新 revision。
- 相同 D3 release 复用授权证明，减少重复读取和解析。
- W3 上下文不再额外读取完整策略展示导出。
- O2 supplement、补偿恢复、Selection 减少无关大对象展开。
- Read 避免同 D3 的重复投影，减少共享设备负载。
- SDK 默认不再将 TRACE/流式正文大量写入日志数据库。
- 已证明独占的维护 root 退出不再遍历全部历史 rollout。

这些方向上的减负，并不能代替真实性能验收。

## 5. 发布期间影响与持续运行风险的区别

- 上轮存在约 **82 秒发布切换窗口**，属于发布期间影响，不能解释发布后持续发生的延时。
- Read 缺表、Transport 调用不存在方法两处问题已在上轮修正。当时的重启可能造成瞬时干扰，不能直接认定为当前持续根因。
- 没有把新增治理 RPC 设成 W3 启动的前置条件，也没有修改原 W3 timing patch；但整个 Worker/SDK 的其他代码和存储行为发生了变化，不能据此声称非模型耗时完全不变。
- 回执中的真实 W3 成功、Read 追平是功能证据，**不是 W3 非模型延时没有回退的性能证据**。
- 本文件没有复查当前生产是否仍处于迁移阶段，也没有确认任何一种风险已经导致生产回退。

## 6. 主要代码定位

以下路径相对于仓库根目录，描述的是上轮发布版本，不代表本地未提交修改或后续提交：

| 范围 | 代码 |
| --- | --- |
| Case 保存及冻结字段拆分 | `src/doxagent/persistent_runtime_v2/repository.py`、`src/doxagent/v2_read/native_content.py`、`src/doxagent/v2_read/content_files.py` |
| Case 引用触发器 | `src/doxagent/persistent_runtime_v2/resource_refs.py` |
| W3 Context 读取精简 | `src/doxagent/persistent_runtime_v2/w3.py` |
| 授权复用、O2 supplement、恢复 | `src/doxagent/persistent_runtime_v2/policy_consumption.py`、`coordinator.py`、`maintenance_recovery.py` |
| Runtime writer 与后台退出 | `src/doxagent/persistent_runtime_v2/journal.py`、`storage_maintenance.py`、`owner_exit.py`、`history_exit.py` |
| Transport、Read 与 Native GC | `src/doxagent/v2_read/transport_archive.py`、`source_lifecycle.py`、`source_native_gc.py`、`formal.py`、`serving_gc.py` |
| D3 共享与历史按需恢复 | `src/doxagent/workflows/codex_document3/policy_storage.py`、`repository.py`、`state_v21.py`、`history_exit.py`、`process_exit.py` |
| Event 更新及第一获知证据 | `src/doxagent/event_library/repository.py`、`admission_evidence.py`、`lifecycle.py` |
| Worker 保存、会话与 Workspace 退出 | `src/doxagent/codex_worker/job_store.py`、`local_sessions.py`、`resource_maintenance.py`、`workspace_retention.py` |
| Native SDK 日志与删除补丁 | `deploy/codex-patches/storage-log-policy-0.159.3.patch` |

## 7. 后续判断顺序

如后续继续验证，应首先对齐 W3 延时发生时点与后台治理批次，再区分 Case 保存、冻结数据读取/解码、D3 冷读、SQLite writer 等待和 SDK 本地操作。优先验证“后台共享资源竞争”和“冻结数据拆分后的真实热路径耗时”，其次验证 D3 冷读及新增事务检查。

本文件没有授权或实施新增扫描、监控、性能测试、代码修复或部署。
