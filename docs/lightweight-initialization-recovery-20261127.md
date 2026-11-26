# PRAX / XENE 轻量初始化修复与恢复

## 已确认原因

- **PRAX**：原 D1、监测配置成功；CDECR 的两篇文档在 Dreamer 与零候选恢复调用全部失败（Sail Research 上游 429）时，被错误持久化为 `SUCCEEDED / mentions=[]`。随后 Bulk Epoch 在 `PARENT_RESOLVE` 停止，外层重试反复复用这两条无效成功缓存。
- **XENE**：原历史抓取阶段取得 11 条 Finnhub 候选，但旧独立正文获取链路全部失败。系统把“全文不可用”误判为“没有历史消息”，生成 `FINALIZED_NOOP`，O2 未执行，空 Event Library v1 正常激活。因此问题不在前端显示。对同一原始 Finnhub 包装链接使用现有 Shared BODY / Site Access，实际取得约 17 KB 的 Yahoo 正文。

## 最小修改

1. 初始化 CDECR 在已启用 Site Access 时复用现有 `SharedContentExtractor / body_v2.2`，沿用现有服务、凭据、并发限制与关闭机制；不另建浏览器、Worker 或资源池。未启用时保留原路径。
2. 有历史候选但全部因正文不可用被拒绝时明确失败，保留断点；真正没有候选仍允许既有 NOOP，不伪造事件，不把摘要当全文。
3. Dreamer 初始与零候选恢复均无任何成功调用时，文档记为失败并允许原机制重试；保留有效空结果和原有部分分块成功行为。
4. 选定节点/块续跑继承原 `running_mode`，避免轻量 ticker 被默认 FULL 拒绝。
5. 按用户明确授权，同一 DeepSeek 模型保留 Sail Research 首选，仅允许 `ambient/fp4` fallback。真实小探针已返回 Ambient 成功结果。
6. 为生产 CDECR Executor 补齐已有 Site Access worker secret 挂载。第一次恢复暴露该服务原来仅有 token 文件路径、没有实际挂载，已保留这次失败记录并在配置修复后继续同一断点。

代码提交：`7d199fff7`；Compose 配置：`4d24119d1`。只替换 Initialization / CDECR Executor；不重启 Runtime、交易执行器或 Codex Worker，不修改 W3 提示词。

## 恢复边界与留痕

- PRAX 原 run：`init-prax-99ee3bd20640445599dd1f4917ac1385`；原 cutoff：`2026-11-26T14:33:50.673294Z`。通过 Control 的 revision CAS、幂等 key 和 `RESUME_INITIALIZATION` 恢复。
- 仅将已证实全部 Dreamer 调用失败的两条成功缓存更正为 FAILED，原结果和模型调用记录保留；归档并清理其零输出、未 FINALIZED Epoch 的依赖缓存，避免空 FIELD/Atomic 结果在新文档处理后继续复用。不修改其他 ticker 或有效研究结果。
- XENE 原成功 run：`init-xene-6e3785c8d1764f95b2a960bf5ada87fd`；原 cutoff：`2026-11-26T14:34:04.633659Z`。原成功记录不改写。仅 CDECR → O2 的补跑为 `init-xene-d03c24c1ce7544e59483377e22c1f609`，冻结复用原 D1。
- 为满足本轮结束后继续自动完成的要求，在现有 CDECR 租约交接点，通过正常 `claim → expand → release_lease` 显式将原有四个激活/ACK 节点加入同一补跑 DAG，依赖真实 O2 结果，不预填未来版本或伪造回执。保留原监测配置引用及 base revision CAS，随后恢复 Initialization 调度。没有抢占执行器租约，也没有新增后台跟踪器。
- 原成功节点的完整模型序列化哈希已建立基线，恢复过程中核验保持一致。
- 远端恢复基线、无效缓存原始证据：`/data/initialization/operator-recovery/light-init-20261127/`，限定目录/文件权限，不包含新增凭据。
- 生产发布使用现有 immutable overlay owner；仓库服务器工作区未 reset/clean。CDECR secret 的生产差异通过同一 release 目录内 Compose overlay 持久化，由 release state 保存引用。

## 验证

- 历史全文、失败/有效空结果、文档续跑、轻量模式、初始化/O2/恢复相关：91 项通过。
- CDECR Dispatch、子进程、预构建与隔离兼容：24 项通过。
- 发布镜像源文件 SHA 校验通过；CDECR Executor 已能读取已有 worker token。
- 原成功节点哈希全部保持一致；XENE 19 个 live binding、监测词配置和配置 revision 与交接前基线逐项相同，摘要 `edefd6a45e2f3dc98ee12c2398db80e66ccbe0376050b63b96744451fe126a0e`。

## 按用户要求提前结束时的状态

**本节是自动续跑交接状态，不是全部初始化成功验收。** 用户明确允许在相关 ticker 可以正常继续执行时结束本轮。

| Ticker | 实际状态 | 自动后续 |
| --- | --- | --- |
| PRAX | 原 run 已恢复 RUNNING / CDECR_RUNNING，原两篇全文已开始新的文档处理；两条错误缓存仍作为 FAILED 历史记录保留 | 原 O2 → 激活 → Bus ACK → Runtime ACK DAG 保持启用 |
| XENE | 重新获取的 14 条候选中，4 篇全文合格且全部处理成功，产生 40 条事件提及；Epoch `bulk-epoch:c2cfd869dcec26686f2bb58e` 已 FINALIZED；O2 和 `o2.o2-survey` 已实际 RUNNING | 同一补跑 DAG 已持久化 `activation.prepare → activation.commit → bus.ready → runtime.ready`，不再依赖本聊天手工激活 |

- O2 尚未发布，不能宣称 XENE 新 Event Library 已完成或已经非空激活；原 v1 会保留到真实新版本发布并激活。
- Initialization 已恢复运行；CDECR Executor 健康。交易执行器、Runtime 调度器、消息总线与 Codex Worker 未因本轮修复重启。
- 一次性租约交接程序已正常退出，不留新增常驻任务。后续失败仍按原有预算、断点和人工恢复机制处理，不能保证外部模型/来源永不失败。
- **来源限制仍存在**：Benzinga 历史接口实际返回 401；XENE 其余 10 条候选未达到全文标准。本次没有降低准入、补造事件或将部分覆盖包装成完整历史研究。PRAX 保留原已接受的两篇来源队列，没有重做其已完成的历史筛选。

交接证据：`artifacts/light-init-recovery-handoff-20261127.txt`、`artifacts/light-init-attach-boundary-20261127.log`、两组测试日志；远端同一恢复目录保存原始基线、缓存归档及显式追加的 DAG。
