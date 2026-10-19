# CEG 模拟交易模式修正待办

2026-10-18 17:29 北京时间，用户明确 CEG 必须使用 PAPER_TRADING，允许初始化完成后调整。
17:29 实测原初始化 `init-ceg-d0d787cd5f6846e7925032e0f51e0f99` 正在 D3，
Control revision=2 / epoch=1，mode/requested_mode=MESSAGE_MONITORING；无 active revision。
全局 PAPER_TRADING binding 已配置，profile environment/account_mode 均为 PAPER。

当前正式 START 会增加 Control epoch，且无 active 时会尝试初始化；为保留原 run，
不在此时提交模式修改，也不直接写 Control 字段。

已更新现有每小时 heartbeat `ticker`：原 run 成功、active 发布且 Control 完成初始化后，
使用正式 START，body 为 monitor_mode=PAPER_TRADING / initialization=REUSE_ACTIVE，
带当前 revision 和固定幂等键 `ceg-paper-mode-after-initialization-20261018`。
然后验证操作完成、有效模式、bus/runtime ACK 与前端同步，完成后通知一次。
长期故障巡检继续，不创建重复初始化或新巡检任务。

主工作区 `eval/ceg_paper_mode_20261018/switch.py` 提供上述有条件操作，
首次在生产 Control 容器运行仅返回 `wait_for_original_initialization / RUNNING / D3`，
没有提交操作或改变原 epoch、cutoff、模式和研究进度。
用户授权已持久化到 automation prompt；模式切换尚未执行，不能宣称已经生效。

## 2026-10-18 22:33 CST 完成验收

原初始化 `init-ceg-d0d787cd5f6846e7925032e0f51e0f99` 于 22:21:42 CST 达到 SUCCEEDED / VERIFY_READY，state_seq=2476；research cutoff 仍为 `2026-10-18T02:13:57.176197Z`，原 activation 已发布，无初始化租约或活动 Guardian incident。

22:33:12 CST，守卫脚本在确认原 run 完成、activation 匹配、Control 初始化结束、既有 PAPER binding 可用且没有其他在途操作后，通过正式 START 提交 `monitor_mode=PAPER_TRADING, initialization=REUSE_ACTIVE`。使用固定 actor/idempotency key 和提交时的当前 revision。操作 `74ced0c3b994454bab2b82cd441bc24c` 于 22:33:17 CST SUCCEEDED / RUNNING。

验收：Control revision=5、epoch=2，mode/requested_mode 均为 PAPER_TRADING、analysis_allowed=true、initialization_incomplete=false；初始化 ID 和 activation 未变。bus/runtime/initializer 均已对 epoch=2 ACK PAPER_TRADING。前端当前 ticker 投影 revision=5，RUNNING / AVAILABLE / PAPER_TRADING，health=NORMAL。既有全局 binding 的 profile environment/account_mode 均为 PAPER；未改绑定、账户或策略，未提交 LIVE_TRADING，也未重新初始化。

当前八个正式 Control 所引用的初始化全部 SUCCEEDED；无失败/运行中的当前初始化。长期小时巡检继续保留；后续若 CEG 已处于 PAPER_TRADING，脚本仅核验返回 already_paper，不重复 START。

原始操作回执保存在生产 `/data/initialization/operator-audits/ceg-paper-mode-20261018/operation.json`；本轮验收在主工作区 `eval/ticker_heartbeat_20261018_1433/`。切换前全部 19 个保留容器运行，所有配置的健康检查通过，根盘可用约 3.9 GiB（此为实测，不对其他会话的空间治理操作作归因）。
