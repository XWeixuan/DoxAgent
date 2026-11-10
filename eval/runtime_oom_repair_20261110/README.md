# Runtime 2026-11-10 修复证据

- `final-release-regression.xml`：最终代码 129 项相关回归通过；无真实交易回放。
- `completed-result-priority.xml`：扩展旧 W3 v4 检查，43 通过、4 项固定历史消息触发过期保护；`completed-result-priority-baseline.xml` 为关闭本次优先级标记后的旧 W3 v4 对照，同四项失败。
- `lateclaim-incomplete-observation.jsonl` / `lateclaim-incomplete-business-end.json`：09:07—09:17 CST 的完整服务窗口。资源未 OOM，但结果提交延迟不合格，**不作为最终通过窗口**。
- `failed-release-*`、`resource-only-c52-*`、`pressure-798-*`：更早失败/条件隔离的现场，保留而不混入最终验收。
- `scalar-read-benchmark.json`：生产独立只读子进程的 20 次相同 miss-key 对照；不是业务端到端时延。
- `writer-*`：不含 locals 的特定窗口锁/栈与回归证据；不能据单个瞬时栈量化全部事故归因。
- 最终窗口使用 Case rowid 小集合、Case/Job 主键与既有状态/owner 索引，不读全历史正文。`business-end.json` 中 `new_jobs` 指 Job 创建于窗口内，不必然代表新消息；`new_case_jobs` 另行区分。

这里只保存安全的资源和业务身份元数据。密钥、环境文件、Compose 私有备份、原始日志及完整 Prompt 不纳入此目录。远端详细运行资料位于权限 0700 的 release 审计目录。

最终通过窗口为 `observation.jsonl`（09:22—09:32 CST），摘要 `observation-summary.json`；`business-end.json` 与 `completion-crosscheck.json` 区分提交统计和已结束未提交检查；`source-and-service-verification.json` 保存实际源文件 hash 与镜像身份。
