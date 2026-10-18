# CEG 延时初始化启动回执

用户授权约两小时后启动 CEG。一次性 automation `ceg` 创建时间为
2026-10-18 00:13:39.795 UTC，最早允许启动时间 02:13:39.795 UTC（北京时间 10:13:39.795）。
调度于约 02:09 UTC 提前唤醒，先只读核验并等待；启动脚本亦显式检查 NOT_BEFORE，未提前提交。

## 启动

启动前 CEG 没有 Control、初始化、操作或 Guardian incident。
02:13:57.032592 UTC 通过生产 ControlRepository 正式提交 START：

- actor `operator:ceg-delayed-start-20261018`
- 幂等键 `ceg-initialization-delayed-20261018`
- 模式 `MESSAGE_MONITORING`，沿用正式默认值；没有开启 PAPER / LIVE 交易。
- 操作 ID `4d35f026d5104e688e130819c39ae003`
- Control 02:13:57.193423 UTC 完成，状态 SUCCEEDED / INITIALIZATION_QUEUED。
- run `init-ceg-d0d787cd5f6846e7925032e0f51e0f99`
- research cutoff `2026-10-18T02:13:57.176197Z`

没有 FORCE_INITIALIZE / reinitialize，没有绕过 admission、租约或激活检查。

## 启动验收

02:14:46 UTC：run RUNNING / UPSTREAM、state_seq=6，三个节点 RUNNING，无失败。
租约同一 owner/token=1，已续期。首个 C4 pre-scan Worker：
`c0b4590867d4b46c185e833bfd263436f2c71f8582577291b28049ef0273eb97`，
execution_phase=RUNNING，thread / turn 均已建立，error_code/error_message 均为空。
前端 read-model 已同步 CEG INITIALIZING / revision 2。

这份回执确认启动及实际派发成功，不代表整轮初始化完成。
一次性 automation `ceg` 已暂停；长期每小时 automation `ticker` 继续负责故障巡检和原节点续跑。

## 留痕

远端 `/data/initialization/operator-audits/ceg-delayed-20261018/start.json` 保存正式操作回执。
主工作区 `eval/ceg_delayed_initialization_20261018/` 保存带时间门槛及重复检查的 start.py、
只读 probe.py、before.json、start.json 和 after_01/02.json。脚本通过 py_compile 检查。
生产代码、镜像及其他 ticker 的执行没有更改。
