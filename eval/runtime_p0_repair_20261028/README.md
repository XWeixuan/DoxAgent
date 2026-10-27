# Runtime/W3 P0 修复证据

- 实施与验收：`docs/runtime-p0-repair-20261028.md`。
- `preflight_remote.py` / `preflight.json`：发布前只读 Case/task/Effect/job 清单。
- `source_files.json`：发布后逐组件源码校验清单。
- `release_remote.py`：复用 canonical storage_release 的构建、发布和回滚记录，只发布列明六个组件。
- 原排查证据保留在 `eval/runtime_p0_latency_20261028/`；本目录的 baseline 仅用于保留其他会话的未提交改动，不能作为发布输入。
- 本轮不手工重跑或重置存量 Case，不改变模型/Prompt/Skill/交易策略。正式代码来自提交，不从脏工作树打包。
