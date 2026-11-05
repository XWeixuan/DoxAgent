# by_distribution 模型判定10分钟窗口

日期：2026-11-04。

`LiveDeliveryWorker` 对 acquisition_mode=`by_distribution` 的正式分发链路增加固定10分钟模型窗口。从已有首次列表发现锚点起算；delivery首次发现与article首次发现取较早者，防止来源接管、正文更新、canonical合并或403恢复重新起算。600秒整开始过期。发布规则仍须满足原完整正文、绑定启用等条件。

- 窗口内按原逻辑执行正则及decision model；正则命中直接正常分发。
- 超时后仍检查正则，正则命中可正常分发；未命中则不再派发模型，不接受迟到的正/负结果或错误重试，使用现有IRRELEVANT终态并记录`DECISION_MODEL_EXPIRED`。
- 在模型调用前、正向回调及最终错误/负向结果处理处检查窗口。已有在途请求沿原有限预算结束，迟到结果丢弃；不新建每消息取消任务。
- 已在窗口内取得、且仍匹配当前正文摘要和术语revision的有效正向缓存可继续提交。复用原`due_at`（无延迟重试的判定结果即成为可处理时点）验证缓存取得时间；失败/重试路径本就清除分数。已发布消息继续正常修订，不撤销既有Stream或Case。
- 复判/重启即使重新将delivery置PENDING，也会按不变的发现锚点过期；不需要持久化新的倒计时或过期账本。历史未发布项在原流程自然触达时生效，无历史扫描或回填。

范围仅by_distribution。by_search（包括已有Google opt-in）、by_ticker、Massive测试源的既有语义保留。正式Collector目前走LiveQueue；部署前确认旧distribution_deliveries及旧模型continuation均无PENDING/RUNNING，未扩展已退役的兼容分发路径。

资源：产品仅改`message_bus_v2/live.py`；无新字段/表/索引、SQL查询、定时器、HTTP调用或后台扫描。过期使用本来需要提交的终态UPDATE。模型窗口计算为已有行的时间比较。窗口内正则命中及已有正式消息更新不增加请求。

回归包含599/600秒边界、排队及在途过期、正/负/错误结果跨截止、及时缓存与迟到缓存、来源接管，以及真实SQLite的CONFIG_INCOMPLETE恢复/worker重启；过期正则命中仍正式发布。具体结果与生产验收在发布后追加。

14项新增回归通过；Google opt-in及跨源去重等定向复验38项通过（含新增测试，随后追加的第599秒成功用例单独通过）；更广的既有分发/恢复/Massive/去重回归91项通过、1项既有跳过，和定向复验有重叠，不重复相加。新测试文件Ruff通过。缓存检查同时保留加载当前术语前的原判定revision，避免旧版本缓存被误标为新版本后绕过过期。

发布只需重建backend并更新`v2-message-bus`，无迁移；Collector、BODY、API/Web等无需重建。本轮基线包含已上线c7697c88的缓存/源入口修复，部署前核对实际Message Bus镜像无容器内源代码补丁。

## 生产验收

产品提交`1ab887b3f31630f67ba1ccfa7224c7ca4c3f2e56`先推送origin/main，再通过标准release只构建backend、更新v2-message-bus。实际镜像与登记均为该提交，running、restart_count=0；其余组件镜像登记均未变化。live.py源码SHA256与Git归档一致。无数据库迁移；资源清理由原治理器按管线资源预留规则暂缓，无全局prune。

2026-11-04 18:58北京时间，在生产新镜像的独立进程内，用三条真实COHR/NBIS旧消息的持久化时间锚点，配合内存中的正则命中/未命中输入演练6个分支：未命中全部得到DECISION_MODEL_EXPIRED且不发布；命中全部允许分发。所有decision写入及publish操作均为内存记录器，没有修改原消息、补发、写生产库或调用模型。它证明部署代码与过期分支生效，不宣称已经获得上线后自然新消息的完整十分钟观察。

脱敏[验收回执](../eval/decision_model_expiry_20261104/acceptance.json)。历史已经正式发布的记录没有删除或撤销；原KPI中的历史长延迟样本仍保留。
