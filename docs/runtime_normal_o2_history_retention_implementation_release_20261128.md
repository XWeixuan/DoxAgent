# O2 正常窗口、Provisional 边界与历史依赖修复实施回执

日期：2026-11-28（北京时间）。实施依据：`dev_plan/workflow_v2/runtime_history_retired_precise_retention_repair_plan_20261127.md`。

## 1. 实施范围与因果链

原实现把迟到 Case／CLOSED Sweep 的候选持续派生成 SUPPLEMENTAL O2。大量完整维护占用后台资源、反复发布 Library；历史默认保留收紧到七天内最多三版后，遗漏的实际 W1 日基线引用暴露出来。同时 Operations 的本地候选提交只需历史 Event 编号水位，却被强制经过正文版本保留校验，造成成功模型结果不能提交。

本轮保留既有存储治理方向，处理来源、可见性与真正的正文依赖，不恢复逐 Run Clone或全部历史版本。Prompt、Skill、W3 输出 Schema、SDK、模型/档位、交易规则及单/双任务配置不变。

## 2. 已落地的执行逻辑

- 删除 dirty_case → 补充维护的周期扫描与生产端，不再随 Effect 完成写无消费者通知；动态 drain／supplemental Sweep 继续处理消息，但不能派生 O2。正常 DAILY、日历 Sweep 和它们的必要原身份恢复保留。
- 新正常 O2 在原 Task 租约下采样实际 F、高水位及关闭窗口标记；读取同 ticker 合法 PENDING，不再限制原 Case 日或 Sweep。F 同时作为真实输入 as_of，原调度 C 不变。冻结后新增行不能混入原重试集合。
- 整个冻结集合先按原 signature 分组，再以每页最多128个代表事实顺序执行。原 Task 保存页进度与真实页 base；已完成页不重跑，后续页不补入新事实，最终仍走原 Control CAS／激活流程。历史已有冻结/付费单页保持原 Run／Job 身份。
- 确认仅限 Native 真正 RESOLVED 的身份及同签名代表覆盖的身份；unresolved 继续 PENDING。最终确认每100项释放 writer，不按 Case 整批清空事实，也不借 O2 清理未完成 O3 义务。
- 已在旧 Native 发布中结算、但 Runtime 尚未确认的原子，按当前分支继承的 `runtime_atomic_mappings` 确认；必须同时匹配 scope、原身份、版本、完整 Native 签名和已结算 disposition。KEEP_PENDING、异版本、异内容不确认，不为恢复确认而重跑旧模型。
- 大帧 checkpoint 保留原 Native 引用，只编码新进度；签名覆盖与最终确认使用分组/集合，避免全 ticker 积压下重复展开、重复落盘和平方级比较。
- Provisional 首次取得 writer 后确定唯一可见日；允许实际日切后一日首次迟到、或正常关闭窗口已冻结后的边界顺延。D+2以上只留原日 backlog，既有行不挪日，重试不改身份或 created_at。保留原 Case／消息／发生时间。
- W1 仍按单一语义日查询；同日 PROCESSED 事实继续可见。顺延行仅在目标日冻结 Library 已包含同内容时去重，不将维护失败变成逐日滚动。
- 在 Case 原保存事务登记实际 W1 日 pin，状态变更不再覆盖成 activation pin。W1R2 真正冻结后在原事务释放 v6 正文依赖，重试复用已冻结详情。后台用小页补旧日基线及 Sweep 的小型引用 header。
- 历史编号水位独立校验已发布证明及 Event birth identity；允许已发布后退休版本的精确水位，仍拒绝未知/未来/未发布版本，正文 API 继续拒读退休版本。新 Case 冻结内部水位，Operations 本地提交不必再读取历史正文。
- 原保存 Operations 结果按 Stage.worker_job_id 精确定位 paid invocation；核对原 Case、Thread、阶段、成功状态及输出合同，以 CAS 恢复原 Effect/Stage，保留原失败审计、失败次数和业务幂等。结果后的本地异常标为 COMMIT。
- 旧 Case 的 RUNNING/PENDING_W3 标记本身不等于真实正文消费者。一次性迁移检查原 inbox Task 与实际 W3 Effect：原任务已失败且没有 active W3 的旧 Case 释放正文依赖；旧合同 Case 即使 Trade/Case 已完成，真实 Operations 还活跃时仍受保护。不改写历史 Case 的业务状态，不恢复已删除的旧 clone。

关键文件：`normal_o2.py`、`coordinator.py`、`maintenance.py`、`repository.py`、`daily_index.py`、`resource_refs.py`、`history_exit.py`、`resource_gc.py`、`journal.py`、`w3_execution.py`。发布空闲判断同步移除了已删除补充执行器字段。

## 3. 验证及发布前补证

- 受影响大组回归171项通过；最后签名分组、部分结算、页恢复、Native 引用复用等关键组46项通过；最终边界/独立仓储组19项通过。组间存在重叠，不累加冒充独立测试数。
- 最后精确映射、引用生命周期组31项通过；新增无效补充回执退役后，正常协调器/维护恢复/边界组39项通过。覆盖分页中断仅续跑未完成页、部分结算、同签名覆盖、未知受理不视为404、无效 paid 结果不反复恢复、Case COMPLETED 但旧 Ops 活跃仍保留真实引用。
- 另一个 CLOSED 恢复测试在开发前四个原模块下同样失败，具体为其旧夹具输入准备触发 TypeError；本轮未将它计入通过结果，也未借本次修复重写 CLOSED 调度。
- Windows 长临时路径曾触发 Native 文件 rename 失败；使用专用短路径后完整大组通过，未因此修改生产 Native 文件发布实现。
- 首次生产只读预演未匹配56份 paid receipt：内层 Job 已外置，旧 JSON thread 索引为 NULL。改为 Stage 记录的 Job 主键后，56份均通过身份/合同验证；没有放宽验证或重跑模型。
- 生产原有约千个非成功 SUPPLEMENTAL Task。发布窗口第一批行政退休914个；3个需收尾的原 Task 暂留 HELD；3个仍有有效运行 owner 的任务保留。候选 PENDING 和既有结果没有清空。

## 4. 生产发布与恢复结果

主体修复 `228b1b485` 已发布 Scheduler 与 Transport Maintenance。最终两个结算/收尾补丁也已提交 origin/main（最终代码 `be4500fef`），并于北京时间11月28日01:30完成Scheduler切换；本轮产品代码已全部应用。

采用当前已验收镜像的逐角色精确源码覆盖、文件 hash 核验和冻结的正式发布 owner；仅替换 Scheduler／Transport Maintenance。Worker、原 Job、Thread、模型与 Prompt 保留。旧治理组件在必要引用迁移和恢复核验之前保持停止。

### 4.1 已验证的业务恢复

截至北京时间 11 月 28 日 01:01：

| 验收项 | 实测结果 |
| --- | --- |
| 原 HISTORY_RETIRED Operations | 56/56 COMMITTED，MU 35、COIN 19、NBIS 2 |
| 原付费 Job 身份 | 56/56 与恢复审计一致 |
| 本地运营业务幂等标记 | 56/56 存在，重复执行恢复工具新增恢复0 |
| 旧非成功 SUPPLEMENTAL | 953个管理退役，剩余未收敛0；不伪装维护成功，不清空 PENDING |
| 首轮 Scheduler 发布后自然流量 | 26个主 Trade COMMITTED，双分支各26个 RESULT_SAVED；28个新 Operations COMMITTED |
| 同窗口新接收 W3 Stage 的 HISTORY_RETIRED | 0；这不是全历史错误计数清零 |
| 精确引用迁移 | reader-v3，complete=1；现场没有仍需保留的 Case 正文引用 |
| 当日必要日基线 | 13个 ticker 的精确 root/version 均 PUBLISHED，包括原事故10个基线 |
| 历史 PENDING 兼容预检 | 分页检查60,882行，未发现 Provisional 合同不兼容及对应源 Case 行缺失；没有将它们提前标记完成 |

上述自然 Stage 以 accepted_at 不早于首轮发布时间筛选；恢复的56个历史 Stage 另按恢复审计核对，不混作新 Case 成功量。统计是发布观察窗口，不代表整个交易日。

一个旧补充任务的最终 paid 回执结构无效，最初收尾工具误将它识别为可本地完成而再次消费，留下了一次额外本地失败记录；没有新增模型请求。最终补丁明确排除此类恢复，并在协调器将其行政退役，保留原失败审计及候选义务。最终一次修复工具输出 `retired=1,reconcile=0,recovered=0,blocked=0`。

### 4.2 历史治理已恢复

Transport Maintenance 于北京时间00:53恢复，新镜像 `sha256:ccc7f6f084d6…`（代码228b1b485）。正式构建/发布回执位于远端 `/var/lib/doxagent/releases/normal-o2-228b1b485/`，敏感私有 manifest 不复制到仓库。

自然治理记录已出现 BE 无引用旧版退休、CEG 38/39版退休；对应真实当日基线 BE83/CEG40受保护。过程退出 errors 为空，来源 expiry error 为空，没有放宽七天内最多三版。迁移时发现223个由陈旧 Case 状态形成的旧引用，其中57个根目录已在先前治理中消失；核验其原 Task/Effect 已终止后，释放这些假引用，而非恢复旧 clone。

短窗口资源观测：Scheduler约812MiB、Worker约2.75GiB、Transport约48MiB。Worker在本轮持续保持原容器/启动时间和 `sha256:543c6014d830…`，未为了应用本修复中断付费任务。以上是瞬时值，不作为长期资源/延时达标证明。

### 4.3 证据落盘

远端 `/data/runtime/history-retired-repair-20261127/` 保存：`reference-verification-v3.json`、`pending-compatibility.json`、`final-retirement.json`、`final-business-acceptance.json`。恢复逐 Case 原错误及 Job 证明位于 Runtime 原 `history_retired_saved_result_recovery` 审计命名空间；交易决策及原 Thread 不重建。

### 4.4 最终发布完成与准确版本

| 组件/代码 | 当前状态 |
| --- | --- |
| origin/main 源码 | 最终产品代码 be4500fef；另有本回执文档提交 |
| Scheduler 正在运行 | be4500fef，镜像 sha256:f5065bbc0f7c…，北京时间01:30:09启动，restart_count=0；全部覆盖源码hash核验通过 |
| Transport Maintenance 正在运行 | 228b1b485，镜像 sha256:ccc7f6f084d6…，北京时间00:53:26启动，restart_count=0 |
| 最终 Scheduler 候选 | 已正式生效，18个覆盖文件与be4500fef构建清单逐文件hash一致 |
| Worker | 仍为原 sha256:543c6014d830…，11月27日北京时间19:12启动，容器/启动时间未变，restart_count=0 |

7111904d8的“继承精确 Native 已结算映射确认”和be4500fef的“无效旧补充回执行政退役”现已生效。最后一例存量旧补充已管理收敛，最终版本也具备阻止重复恢复的协调器分支。

正式发布入口数次执行原有 `stop_idle_scheduler`，最后一次把观察窗口延长到180秒，仍因实际在途 owner/Future 而拒绝切换，旧服务保持运行。自动审批拒绝了快照/重排全部RUNNING Task/Effect的强制切换脚本，原因是范围可能涉及W3，超出单纯O2受控操作；该次被拒操作没有执行，也没有以修改 guard 或间接方式绕过。

用户随后明确授权“重启，以后不要问了”。据此受控重启Scheduler，旧进程收到TERM后在首个观察间隔内退出；停止后的最终RUNNING Task/Effect均为0，未实际改写任何在途任务状态或租约。Worker、原Job/Thread/冻结输入保持不变，正式发布入口完成镜像替换；以后同类已授权发布所需的受控重启直接执行。

01:30:46复验：56/56历史Operations仍COMMITTED、原Job匹配56/56、业务提交标记56/56；旧补充管理退役953个、未收敛0；13个当日基线均PUBLISHED。自首轮发布起自然流量累计52个主Trade/各52个双分支结果和54个Operations完成，同一窗口新Stage无HISTORY_RETIRED；这些累计量包含此前主体版本的运行，不冒充最终镜像独有样本。最终角色核验和重启回执位于 `/var/lib/doxagent/releases/normal-o2-be4500fef/`。

启动日志确认Scheduler已恢复16个ticker的Bus轮询，无启动异常。启动恢复的 `journal.reconcile_interrupted` 有最高8.56秒的只读扫描，未见SQLite错误；这是独立的启动历史扫描观测，本轮未修改该路径，也不据此宣称非模型P95已达标。重启后瞬时Scheduler约223MiB、Worker约1.61GiB，无内存异常上涨。

## 5. 验收边界及本地工作区

本次不把 CLOSED 排队或未知 REALTIME 延时归因于 HISTORY_RETIRED，也不宣称已达到新的端到端延时 P95。正常日切、新 O2 窗口的长期接管吞吐与实际模型费用需要在后续自然窗口观察；源码和机制测试不能替代完整交易日业务验收。

必要后续观察：正常窗口能按冻结的有限集合处理跨日积压、128项页调用/Token和实际发布量；KEEP_PENDING仍保留原义务，不因本轮退休补充任务被清掉。历史 PENDING 为数万行，取消逐 Case Task 不等于本轮已将积压全部结算。没有为了验收提前发起新正常 O2 或付费模型。

主工作树保留了其他会话的未提交修改及与 origin/main 分叉的本地发布提交。没有强制 reset、stash 或把 SDK／来源修复草稿打包到本次生产镜像。实施工作树位于 `.tmp/o2-normal-boundary`；发布代码以 origin/main 已提交版本和镜像内 hash 为准。
