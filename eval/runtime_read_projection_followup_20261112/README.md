# 投影延迟复发专项只读取证

2026-11-12。报告：[复查与修复方案](../../docs/runtime-read-projection-followup-diagnosis-and-repair-plan-20261112.md)。仅诊断，无生产修复或重启。

`samples.jsonl`为19:21:43–19:36:43的31个点；`resources.jsonl`为同一时段61个资源点。复算：

```powershell
python eval/runtime_read_projection_followup_20261112/analyze.py
python eval/runtime_read_projection_followup_20261112/resource_analysis.py
```

`remote.py`只是SSH输入包装器，不构成强制只读沙箱，重跑任何输入必须审查。本轮数据库探针均使用只读连接；mapper探针禁止文件发布，内容登记只计算引用，不调用ingest。LibraryIndexer和PublishedEventLibraryReader访问的是只读Native repository。

`map-probe.json`中的单个policy-head interrupted来自2秒诊断查询预算，不是生产gap。`catalog_counts`中未完成的COUNT保持interrupted，不填猜测数。资源按cgroup/全机差分，原init PID的io/stat不代表Python进程；内存current含缓存和探针。

`reuse_experiment.py`仅在独立Python进程内临时重定义Reference映射函数，生产服务进程及数据库不改变。实验依据当前Read的既有库行与Native出生身份，检查了省略记录的载荷和保留派生结果，未覆盖完整metadata、水位、迁移、故障恢复；不可当作发布代码。最初只返回空indexed列表的失败另记于`failed-prototype.json`。

性能日志合计24份报告、约1808秒tick，窗口与15分钟采样不同；profile为40秒49Hz非阻塞墙钟采样，不等于CPU时占比。原型/cProfile具有测量扰动。部分较早消费时间桶没有返回记录，不能把缺失解释为停机。

Py-spy二进制从先前已核验的官方PyPI wheel在内存中提取，来源/哈希见`profiler-provenance.json`，指定临时目录及文件已按哈希核验清理，见`cleanup-receipt.json`。
