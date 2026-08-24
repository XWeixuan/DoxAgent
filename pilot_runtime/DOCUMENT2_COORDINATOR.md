# Document2 Pilot Coordinator

该工具只编排本地 Formal Pilot case，不调用或改写真实 Document2 runtime。已有成功 D2 run
时，它可以从本地 SQLite checkpoint 解析各节点对应的 Worker workspace；第一次 Pilot 尚无
D2 run 时，它可以直接从已发布 Global Research / Document1 bootstrap。两种模式都只创建
当前节点；当前 case 的 `completion.json` 成为合法 JSON 后，才创建下一个 case。

后继 case 会在 `context/pilot_upstream/` 封存其直接依赖 case 的完整 `output/`，并由
`PILOT_TASK.md` 明确要求优先使用这些 Pilot 产出。Candidate 之间不互相注入；Synthesis
接收全部可用 Candidate；O0 Finalization 接收 Synthesis 与三路 Review；O1 各 Turn 接收
前一 Turn 的完整输出。Narrative Candidate 在源 run 不存在时自动跳过。

## 启动

第一次 Pilot 从已发布 Document1 启动，不要求预先存在 D2 run/checkpoint：

```powershell
python document2_coordinator.py start `
  --source-global-run <global_research_run_id> `
  --coordinator-id <pilot_id>
```

bootstrap 会从私有 Published Storage 校验并读取 D1 C1/C3/C5 正文，在 Pilot 本地
`document2/_sources/` 缓存一次，后续节点不重复读取大正文。横向指标从源 Worker workspace
按 artifact hash 校验后同样只缓存一次；源 workspace 已不可用时，才按真实 D2 loader 的宽松
语义回退为空。Narrative report 在对应节点按既有七天窗口读取；不存在则跳过 Narrative
Candidate。

已有成功 D2 run 且本地 checkpoint/Worker workspace 可用时，仍可使用精确回放模式：

```powershell
python document2_coordinator.py start `
  --source-d2-run <document2_run_id> `
  --coordinator-id <pilot_id> `
  --shell <shell_key_or_shell_id>
```

如果源 run 只有一个 Shell，可省略 `--shell`。命令默认持续监听；每次创建新 case 都会打印
case root 和 `PILOT_TASK.md` 路径。把打印出的精确 case root 作为 Codex App 项目打开并执行
任务即可。当前 Codex App 接口不支持该脚本代替用户创建 App 会话。

bootstrap 启动时 Shell 尚未产生，因此无需预先指定。O0 Finalization 产生多个 Shell 时，使用
其 `shell_id` 选择本次 13 节点 Pilot 的 Shell 后继续：

```powershell
python document2_coordinator.py advance --coordinator-id <pilot_id> --shell <shell_id>
```

状态与当前 case 指针位于：

```text
<cases_root>/document2/_coordinators/<pilot_id>/coordinator_state.json
<cases_root>/document2/_coordinators/<pilot_id>/NEXT_CASE.json
```

如监听进程中断，可恢复：

```powershell
python document2_coordinator.py watch --coordinator-id <pilot_id>
```

只推进一次或查看状态：

```powershell
python document2_coordinator.py advance --coordinator-id <pilot_id>
python document2_coordinator.py status --coordinator-id <pilot_id>
```
