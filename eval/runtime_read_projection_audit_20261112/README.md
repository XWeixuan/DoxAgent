# Runtime → Read 投影性能只读取证

2026-11-12，北京时间。结论及后续执行方案见 [报告](../../docs/runtime-read-projection-performance-diagnosis-and-repair-plan-20261112.md)。未实施生产修复、调度/优先级变更、重放、迁移或清理业务数据。

## 本地复算

```powershell
python eval/runtime_read_projection_audit_20261112/analyze.py
python eval/runtime_read_projection_audit_20261112/resource_analysis.py
```

分别生成 `summary.json`、`resource-summary.json`。分钟负载取完整 UTC 2026-11-11 00:00–21:00；最后不完整消费半小时标记 `complete=false`，容量速率不填值。延迟抽样先按 Read seq 去重，仍不是总体事件分位数。

## 生产探针边界

- `remote.py` 只是 SSH 包装器，任意脚本都能传入，**不具有强制只读沙箱**。重跑必须审查脚本；本次只运行本目录列出的只读输入。
- `sample.py`：31 次、间隔30秒；metadata/有界最近提交，`mode=ro`，无跨采样事务。
- `resource_sample.py`：61 次、间隔15秒；读 `/proc` 与 cgroup。容器 init 单 PID 的 stat/io 不能代替 Python 进程工作量，资源结论使用 cgroup/全机差分，RSS另见 final-host。
- `details.py` / `rates.py`：有界真实源头、SQL计划、分钟负载和落地锚点。`interrupted` 是主动取消预算，不能当查询完成。
- `map_probe.py`：真实生产只读依赖、禁止内容文件发布，content registration 仅内存算引用，**不执行 ingest**。因此不能用其映射时间冒充含写入的全链路吞吐。
- `payload_probe.py`：只读统计 Native digest、尺寸与展开成本，不保存 Case 正文。
- `repair_read_probe.py`：60个真实 gap，同输入四轮事件读取，调用现有 `event()` / `event_reader()`，不调用 `ProjectionWorker.repair()`。
- `inventory.py` / `source_inventory.py` / `final_snapshot.py`：读取角色身份、所属源码和资源；`production/` 是现网代码冻结副本，不是候选补丁。
- `gap_census.py`：全部17,204个 gap 与 hot 头按坐标查询；not_hot 不等于证据不存在，需考虑 archive / pack / exception。

`live-profile-*.raw` 为两次30秒49Hz非阻塞外部栈采样，包含空闲/等待；不等于纯CPU耗时。`live-syscalls.txt` 为8秒受扰动strace统计；timeout退出124是预定结束。

py-spy 0.4.3 的官方PyPI wheel经SHA-256核验，只短暂放置于远端 `/tmp/doxagent-projector-audit-20261112/py-spy`；采样后按二进制哈希核验清理。来源及哈希见 `tools/provenance.json`，清理见 `cleanup-receipt.json`，二进制和wheel不保留在仓库。

## 证据检查

本次13份容器所属源码与本地源码规范化换行后相同；原始字节哈希因Windows CRLF可能不同。摘要中的资源是15秒桶/15分钟窗口统计，不是单次I/O请求分位数。检测到research一次短暂SOURCE_UNAVAILABLE后消失，未推测未记录的原始原因。

Python语法通过AST解析检查；无应用代码修改，未将功能测试通过与性能恢复混为一谈。应用修复/发布和存量恢复需按报告后续阶段执行。
