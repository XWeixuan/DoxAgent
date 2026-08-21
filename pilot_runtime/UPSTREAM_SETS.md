# 双 Research Lane Pilot 人工上游说明

每个 Pilot case 都是不可变输入。更换上游文件后必须生成新 case ID，不能覆盖旧 case。
上游中的原 attempt `【cite:O#】` 只作为线索，进入当前产物前必须重新核验并使用当前
attempt 的引用。

## Global Research 顺序

`C4 pre-scan -> C1/C3 并行 -> C5 -> C4 enrichment`

| 节点 | `--upstream-dir` 可识别文件 |
| --- | --- |
| `c4_pre_scan` | 无 |
| `c1` | `c4_pre_scan.json` |
| `c3` | `c4_pre_scan.json` |
| `c5` | `c1.md`, `c3.md` |
| `c4_enrichment` | `c4_pre_scan.json`, `c1.md`, `c3.md`, `c5.md` |

C1/C3 只会收到 `c4_pre_scan.json` 中的 entity relations；future nodes 不会投影给它们。
C4 enrichment 是最终 C4 turn，输出必须是完整合并快照，不再存在 finalization Pilot。

示例：

```powershell
python D:\DoxAgentPilot\runtime\prepare_case.py `
  --lane global_research `
  --source-run <run-id> `
  --node c5 `
  --case-id <new-case-id> `
  --profile quality `
  --upstream-dir D:\DoxAgentPilot\upstream_sets\<set-id>
```

## Market Situation Research

C2 与 O4 独立并行，不接收 Global Research 上游，也互不依赖：

```powershell
python D:\DoxAgentPilot\runtime\prepare_case.py `
  --lane market_situation_research `
  --source-run <run-id> `
  --node o4 `
  --case-id <new-case-id> `
  --profile quality
```

历史 `codex_d1_v2` case 仍可用 `--lane legacy_document1` 复现，但不得把历史
任何 legacy 节点文件改名后冒充新 lane 产物。
