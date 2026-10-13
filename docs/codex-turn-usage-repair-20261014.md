# Codex SDK 用量统计遗漏修复

## 状态与范围

2026-10-14：本地实现和验证完成，未发布生产、未重启服务、未回填生产数据库。
本次只处理用量统计，不修改 W3 业务、prompt/skill、模型、交易决策或 Fast 配置。
当前工作区另有 W3 重构及其他改动；不能直接用整个工作区构建生产镜像。

## 根因和修复口径

SDK 的 `usage.last` 是最后一次请求；`usage.total` 是线程累计值。
二者均不能直接表示复用线程上的单次 Worker turn 消耗。

新增 `codex_worker/turn_usage.py`：从 Codex 原生 state_5.sqlite 定位 rollout，
倒序只读扫描到目标 task_started，严格按 thread_id + turn_id 筛选
token_usage_record，按 response_id 去重并累加 usage。
仅当开始边界存在、记录合法且合计与原生 turn_token_usage 相同，才确认完整。
线程累计值、其他 turn、Fork 复制的父线程请求、其他子线程均不计入。
缓存输入是 input 的子集，reasoning output 是 output 的子集，不额外相加。

SDK 流式及非流式完成路径均接入该读取器。读取在后台线程进行，原生写入可能稍晚，
最多额外重试两次，每次间隔 100ms。读失败不会触发业务失败或重跑模型。
原生统计不可用时保留已有 SDK 最后请求数值，但 `usage_complete=false`。
新增 telemetry 字段 `usage_source` 和 `usage_complete`；旧记录默认不完整。
Read 的 W3 和其他 Worker 用量记录按该标志标记 COMPLETE/PARTIAL。
成本汇总 API 既有 SOURCE_GAP 覆盖提示保持不变，不宣称实现整个账户的用量审计。

这里统计的是 Worker 自己执行的 turn。没有独立 Worker 回执的子 agent 线程
不在此次统计归属范围内，不能据此声称覆盖账户全部消耗。

## 历史回填

工具 `python -m doxagent.codex_worker.usage_repair` 默认仅预览。
在具有相同 /data 挂载、包含本次代码的容器内执行：

```sh
python -m doxagent.codex_worker.usage_repair \
  --codex-home /data/codex-home \
  --runtime-db /data/runtime/runtime.sqlite3 \
  --worker-db /data/workspaces/worker-jobs.sqlite3
```

核对预览数量后，同一命令追加 `--apply` 执行。
只为原始日志可完整核验的终态 job 补齐 token；缺失日志或 turn 的旧回执保留原数值，
显式标为不完整，并通过投影更新覆盖标记；活动任务跳过。
分别修正 runtime 的 worker_invocations、worker_receipts 和指定 Worker DB 的 jobs。
不修改 request、idempotency key、任务状态、交易输出或事件内容。
使用短事务及并发比较，数据已被其他进程更新则跳过，重复执行不累加。
Runtime 更新走既有 CDC，由 Projector 替换对应 invocation 的贡献，禁止直接累加
Read metric bucket。两库不是跨库原子事务；中断后重复执行即可收敛。
各任务历史 JSON 文件等其他审计副本不在此次回填范围内。
工具分页读取，但长线程早期 turn 的读取仍可能耗时，避免同时运行多个回填进程。

## 发布边界

新增 telemetry 字段对旧版 extra=forbid 的 WorkerJob 消费者不兼容。
需要将新增字段同步发布到对应 Worker 及使用 WorkerJob 解析回执的调用方；
Read Projector 同步发布覆盖判断，然后再回填历史数据。
应从各服务当前生产基线制作窄补丁，不能夹带本地未发布的 W3 重构。
安排已有任务完成后切换，不中断正在研判的 W3 或其他研究 Worker。
本轮侧会话没有进行跨服务生产切换。

## 验证证据

- SDK/成本兼容回归83项通过；最终采集/观测测试11项通过：复用线程、其他 turn/线程隔离、压缩、response 去重、
  冲突/缺失/截断日志、SDK 流式/非流式入口、日志不可用降级、默认只读回填、
  重复回填不累加、不修改运行中 job，以及 Read 贡献替换后调用数仍为 1。
- 远端 12 条最近成功调用只读验证全部与 turn 原生累计一致；读取耗时约19–158ms。
- 一条远端记录旧总量173,316，完整总量2,304,336 tokens。
- 未发起模型调用、未开启 Fast、未改生产配置或数据。
- 本次新模块、SDK适配器、Worker schema 和新增测试的 Ruff 检查通过；
  Read runtime.py 尚有本次改动前已存在的第31行超长行，未改动该无关行。

## 限制

旧版本或被清理的原生日志无法凭空恢复。失败/取消且无正常结束边界的调用可能
继续是 PARTIAL；不能把尚未核验的最后请求用量宣称为整次任务总量。
这次修复不会提供 token 到 Pro 周额度百分比的固定换算。
