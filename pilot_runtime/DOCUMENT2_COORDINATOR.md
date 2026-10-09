# Document2 SDK Pilot

当前推荐入口是 `document2_sdk.py`，由 Codex 助手直接驱动本地 SDK，用户无需在 Codex App
逐个打开 case。完整说明见仓库 `docs/document2-sdk-pilot-20261006.md`。

安装 runtime 后，从已发布 Global Research 启动：

```powershell
python document2_sdk.py start --source-global-run-id <global_run_id> --coordinator-id <pilot_id>
```

已有 D2 运行可使用 `--source-d2-run-id <d2_run_id>`；v2 源加
`--document-schema-version document2.v2`，默认是 v2.1。也支持显式 `--checkpoint <json_path>`。
配置由 runtime 的 `.env.local` 提供，源输入、签名权限和上游冻结沿用既有机制。

继续 / 选择 O0 生成的一个 Shell / 每次执行一个节点：

```powershell
python document2_sdk.py continue --coordinator-id <pilot_id> --shell-key <shell_id> --max-nodes 1
```

每个节点执行一次正式研究，再在同一 thread 执行 audit-only 复盘，记录 bug、质量风险、
理解歧义、不太会写的地方、执行困难和证据不足。同一 Shell O1 阶段共享 SDK thread；
Open Discovery 的 Scan→Selection 仍是一次研究 Turn，复盘不算新的业务阶段。

失败后查看报告，确认需重试时增加 `--retry`。研究成功但复盘失败只重试复盘，已有完整
成功节点直接复用。不重复启动尚未结束的 SDK Turn。

结果在 `<cases_root>/document2/_coordinators/<pilot_id>/`：
`PILOT_REPORT.md`、`pilot_delivery.zip`、`pilot_delivery_manifest.json`。
助手应阅读并总结可观察过程、Pilot issues、未解决事项，再附产物交付。公开 reasoning
summary 没有提供时明确注明，不能声称获取了完整思维链。

```powershell
python document2_sdk.py report --coordinator-root <coordinator_root>
python document2_sdk.py case --case-root <prepared_case_root>
```

`document2_coordinator.py` 仅保留作历史 case 的准备、状态和上游替换工具；它本身不调用
模型，其旧 JSON-only 推进不代表 SDK Pilot 已通过研究与复盘。不要同时驱动两种入口。
历史 replacement handoff 仍须在 O1 case 创建前提供，并保留原产物与 SHA 来源记录。
