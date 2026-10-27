# Runtime/W3 P0 修复证据

- 实施与验收：`docs/runtime-p0-repair-20261028.md`。
- `preflight_remote.py` / `preflight.json`：发布前只读 Case/task/Effect/job 清单。
- `source_files.json`：发布后逐组件源码校验清单。
- `release_remote.py`：复用 canonical storage_release 的构建、发布和回滚记录，只发布列明六个组件。
- 原排查证据保留在 `eval/runtime_p0_latency_20261028/`；本目录的 baseline 仅用于保留其他会话的未提交改动，不能作为发布输入。
- 本轮不手工重跑或重置存量 Case，不改变模型/Prompt/Skill/交易策略。正式代码来自提交，不从脏工作树打包。

- 最终生产代码：Worker/Scheduler 416fb2fa；API/Bus/Projector/Web 24a5fde5。`release-final.json` 含用户明确授权的强停范围和 34 文件摘要校验结果；`accept-final.json` 是只读基础快照。
- `release_worker_followup.py` 是收口版的正常排空发布入口；本轮最终依用户补充指令终止排空等待、强停旧两个容器，仍使用已构建镜像与 canonical deploy/state。未改变业务交易规则。
