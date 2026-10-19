# W3 历史回测合入与三端同步回执

日期：2026-10-15，Asia/Shanghai。状态：**完成**。

## 同步结果

| 位置 | 最终状态 |
| --- | --- |
| GitHub `origin/main` | `618fd9c5c9a5ec4e0d1242fd3a6f57d2381a7d4e`，从 `21b9b7bd` 普通快进推送 |
| 本地 `C:\Users\WEIXUANXIE\Desktop\DoxAgent` | 分支 `main`，同一提交，受跟踪文件干净，跟踪 `origin/main` |
| 远端 `/home/ubuntu/doxagent` | 分支 `main`，同一提交，工作树干净 |
| 生产 | 18 个正式服务均为此 MAIN 源码，六类镜像均使用 `main-618fd9c5`；健康检查通过，最终重启计数均 0 |

生产切换于 **02:57:00 +08:00** 完成，最终验收快照为 **02:57:39 +08:00**。源码归档 SHA256：`bf1181f669363ce52f74fc34328c5bf8d4644670ea811a1b494f9939e7ab5c6c`。

## 本次提交范围

- 独立 `python -m doxagent.w3_backtest` 实现：prepare/build/run/resume/report/compare、旧合同适配、离线证据和行情、两阶段编排、Gold 评分及工具审计。
- SDK 可选 sandbox/config 接口和显式离线执行环境；生产默认能力保持原值。保留 MAIN 的维护节点 MCP 60 秒、W3 90 秒预算及 writer 交接修复，没有用本地旧文件覆盖这些线上修复。
- 回归测试、实施方案/最终实施回执、使用指南、两个冻结数据集的输入/版本研究/Gold/时间审核，以及首轮 baseline 报告、汇总和最终审计。该提交共 1,272 个文件，绝大多数是冻结数据。
- 对冻结输入设置 `.gitattributes -text`，防止 Git 跨平台换行改变字节指纹；1,240 个数据集文件的暂存字节与本地原件一致。73 案数据指纹仍为 `84c93cd621df911193c96a8129bd27185475f2e44db22d2e184f3b4c30af159c`，Windows 与远端 Linux 均核对通过。
- 追加 changelog，并纳入上一轮 MAIN 收敛执行记录及 W3 writer 修复说明。

SDK 私有认证/运行状态、逐案运行数据库、重复远端原始导出及采集器原始回执继续留在原本机位置，未上传 GitHub。回测输入与报告在 Git 中；完整原 run 仍在 `D:\DoxAgentBacktest\runs\baseline-20261014`。没有修改冻结 Gold、原提示词或历史失败结果，也没有在生产执行回测。

原本地 9 个修改文件及完整差异先保存并核验，再同步主检出。备份位于 `eval/main_convergence_20261014/backtest-sync/pre-sync-modified-files.zip`、`pre-sync-local.patch`。无关未跟踪资料保留，没有使用 `git clean` 或强推。

## 验证

- 整合源码上 **72 项回归通过**：回测/W3 两阶段、SDK 权限、Template/Fork 生命周期、writer 交接与维护恢复。Ruff 和代码/文档差异检查通过；四份冻结研究原文的历史行尾空格保持原样。
- 拟提交数据 JSON/JSONL 均可解析；凭据模式扫描无真实命中，新闻 URL 的 `sk-…` slug 误报已分类。
- 本地主检出同步后的 CLI `--help` 正常。
- 六类生产镜像复用已批准依赖层，仅叠加 18 个新增/修改源码文件；Web 文件系统保持原样。767 个应用/提示词文件哈希、SDK 0.159.3、IBKR 10.49.2 及 Site/Chrome 非 root 导入 smoke 通过。
- 原 named volume、secrets、Profile 挂载保持一致；四个核心数据库 epoch 未变，写入水位未回退。未迁移/恢复数据库、删除卷或重放订单。既有受限恢复集继续保留。
- Guardian 从 MAIN 实际 clone/fetch、生产容器定位和源码归档 hash 匹配。
- Barron's、DIGITIMES、SeekingAlpha 原身份文章探针均 SUCCESS/HTTP 200、article、无认证/付费墙阻断。
- 新 Worker 启动后的自然任务观测：1 个 succeeded/SETTLED，2 个 RUNNING，错误码均为空；没有额外发起模型验收任务。
- 资源保护进程 active。验收时根盘可用 **6.489 GiB**；本轮没有扩展到额外容量清理。

## 切换中纠正的问题

1. 同时停止 scheduler 与 Worker 的顺序不合适：scheduler 的关闭等待需要读取 Worker 终态收据，端点先停触发长时间断连等待。恢复原 Worker 曾被自动审批拒绝；用户明确批准后短暂启动原容器，scheduler 随即正常退出，再继续切换。以后先停止调度/生产者并保留 Worker 端点，待 scheduler 排空后再停止 Worker。原 18 个受控停止退出码为 0×6、143×10、130×2（主动取消的初始化/CDECR）。
2. 首次增量构建把私有上下文的 0700/0600 源码权限带入镜像，Chrome 非 root 导入失败。已将镜像源码目录/文件分别修正为 0755/0644，并补做 Site/Chrome 非 root 导入 smoke，再重建替换。故障尝试的 Chrome 曾重启；最终 18 个正式容器全部健康/运行、重启计数 0。身份和数据库卷未变。
3. Docker 退出事件查询没有返回记录，不能将空结果当作全程无 137 的证明；上述退出码来自原容器直接检查，回执已明确区分证据边界。

执行证据：`C:\Users\WEIXUANXIE\Desktop\DoxAgent\eval\main_convergence_20261014\backtest-sync`，其中 `receipts/` 保存本次 6 份远端回执。远端材料位于 `/var/lib/doxagent/main-convergence/20261014/backtest-sync-20261015`，正式运行入口仍仅使用 MAIN 下三份受管 Compose。

本同步回执是本地执行记录，未另行提交，避免仅为完成记录再改变已验收生产提交。回测功能、冻结输入、基线报告和实施文档均已在 `origin/main`。
