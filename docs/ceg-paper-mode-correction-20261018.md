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
