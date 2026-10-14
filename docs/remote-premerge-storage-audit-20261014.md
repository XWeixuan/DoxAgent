# Git MAIN 合并前远端可重建资源审计

时间：2026-10-14，Asia/Shanghai；主要快照 22:20。依据 [远端发布与 MAIN 收敛报告](remote-release-reconciliation-20261014.md)，重新检查实际容量、51 个容器、79 个镜像的层与依赖、BuildKit 缓存、Git worktree、主机目录、备份入口及服务引用。本次未删除远端资源、停止业务、执行 merge 或修改部署。

## 结论与优先级

根盘共 176.54 GiB，可用约 **16.38 GiB**，使用率 91%。本次最值得处理的是可重建的构建缓存和未使用旧应用镜像，不需要为了 merge 整理业务数据库。Git merge 本身只增加少量源码/对象；主要空间峰值来自随后重新构建镜像和发布。

| 对象 | 实测规模 | 是否可删除/前提 |
| --- | ---: | --- |
| BuildKit 可回收、非共享缓存 | **约 9.14 GiB** | 首选候选；不影响已运行镜像，代价为后续重新下载/安装/构建。执行前重看是否仍 Reclaimable，不处理正在使用的记录 |
| 57 个无容器引用的旧已命名镜像候选 | 去重专属解压层 **约 8.99 GiB** | 已排除本次识别的动态 agent、approved 构建依赖和通用 backend 默认基线；可定向退役旧应用、Web、Site、磁盘/消息源修复版本。完整 ID/全部标签见证据 JSON；不用 force 或全局 prune |
| 6 个旧代码 worktree | **约 2.11 GiB** | 确认差异吸收/可从 Git 恢复，保留 secret 引用和未跟踪有用文件后，用 Git worktree 流程退出；不是当前服务挂载路径 |
| 26 个 Exited 容器 | 当前主日志合计不足 1 MiB | 可按明确测试/验收身份收尾，主要收益是解除旧镜像和目录引用；不把低日志收益夸大为大空间。保留当前迁移收据/退出状态，四个活跃研究 Worker 不随之停止 |
| 已验证旧 UI dist、覆盖构建目录 | 多数每处 0.2–7 MiB | 可在修复吸收后退出，不值得作为主要腾空间动作；旧 smoke 目录约 23 MiB，先解除三容器挂载 |
| `/home/ubuntu/.cache` | 约 150 MiB | 可重建缓存，低优先级；不扩大到 `.config`、SDK 身份或浏览器 Profile |

**9.14 GiB 与 8.99 GiB 不构成 df 可立即释放 18.13 GiB 的保证。** 前者是 BuildKit 的可回收非共享记录估算，后者是候选镜像 RootFS DiffID 与保留镜像层集合差、结合历史层字节的去重结果；BuildKit、containerd 的内容/快照引用与共享层会影响最终释放。清理顺序应为缓存、精确旧镜像、必要 worktree，逐步用 df 验证，不把镜像显示大小相加。

## 最有价值的可重建缓存

390 条缓存记录约 13.48 GiB，其中可回收非共享约 9.14 GiB。较大的记录包括：

| 内容 | 显示容量 | 缓存 ID |
| --- | ---: | --- |
| Playwright/Chromium 安装中间层 | 1.46 GB | `35cqu1tugk37836gb5v9tuf1w` |
| Chrome 构建中的 `/app` 复制中间层 | 1.354 GB | `he82odorg25bvg7jruuc6mwpi` |
| uv 安装依赖中间层 | 1.12 GB | `bbyck6ifiy60cmys1gnjyhbsp` |
| 另一 Playwright 安装中间层 | 1.022 GB | `liyrqmbhi75m9ryhel93b0cbz` |
| uv 下载缓存 mount | 928.5 MB | `s3r1wm7elaxmnxvf5nj8linql` |
| 另一 uv 依赖中间层 | 839.1 MB | `ykyacl3w65t8ys08irxm0ztnd` |
| 两份 pnpm 安装中间层 | 各约 557 MB | `sourx50vb79uj4a6pbbbhoit7`、`qc5131zxotatw78iis5w0k2f5` |

这些大项最后使用时间约 18–19 小时前，符合刚完成统一发布留下的构建过程数据。本次缓存规模与 10/13 审计的约 67 MB 非共享可回收量明显不同，不能沿用旧结论“不值得清构建缓存”。不过，清掉依赖缓存会增加下一次 MAIN 构建耗时；若只需少量余量，可先删旧应用/复制中间层，保留下载缓存。

## 旧镜像候选与必须保留的基线

候选包括 `disk-repair-*`、`source-recovery-*`、`service-busy-*`、旧 `login-feed-*`、Case/Policy/下载/交易修复 Web 与应用镜像，以及已被新 agent 替代的部分旧 SDK/agent 标签。当前正式应用已使用 `release-8a8f60f2`，Worker 使用 `w3-writer-mcp-fix-20261014`，这些候选不再承担正式容器运行。

候选来自实时镜像 ID，而不是只按日期或 Containers=0 推断；多标签镜像必须核对全部标签。部分旧版本承载的修复仍需按收敛报告合入 MAIN，删除镜像不等于可以删除这些源补丁。源码/小型修复回执保留，旧二进制副本与源码整合可以分开处理。

以下必须保留：

- 当前 `release-8a8f60f2` 应用及 Web、Site、Chrome Supervisor、Guardian 专用镜像，以及当前 `w3-writer-mcp-fix-20261014`。在新 MAIN 完整验收前，它们也是回滚基线。
- `doxagent-initialization-repair-agent:release-8a8f60f2`：虽然没有独立运行容器，Guardian 环境明确按此标签动态创建 Worker。
- `doxagent-ibapi:approved-7d361219`：当前 backend Dockerfile 真实 FROM 依赖。同一 ID 还带旧 v21/base 标签；不能连 ID 一起删除。
- `doxagent-chrome:approved-cc38be0a`：当前 Chrome Dockerfile 真实 FROM 依赖；与 `doxagent-chrome-supervisor:server` 共用 ID。旧固定 Chrome 包不可依赖原 apt 来源重新取得。
- 当前默认 `doxagent-v2:production`、`doxagent-v2:server` 暂保留：旧受管 Dockerfile 仍有这些 ARG 默认来源，待 MAIN 构建入口统一后再退役。
- 四个仍运行的 MU/INTC/BE/RKLB 研究 Worker 镜像、两个 Created repair-template 所固定的镜像。业务终态和新模板切换尚未核实，不能为释放构建余量停止它们。

三个无标签镜像未纳入 57 个候选；尚未核定其构建身份，仅删除 tagless 不能保证有收益。候选镜像的专属层估算也已按保留这三个镜像计算。

## 目录退出条件

以下六处旧 worktree 合计约 2.11 GiB：

`/home/ubuntu/doxagent-barrons-egress-build`、`doxagent-case-release-d11f7fa1`、`doxagent-challenge`、`doxagent-driver-prod`、`doxagent-luna-recovery`、`doxagent-release-f3da669b`（后五项同属 `/home/ubuntu/`）。

它们都已注册在主库 worktree 列表中，多数显示约 4,462 个 tracked 变化，主要是历史 `exports/.../node_modules` 删除，不能把计数等同于 4,462 个遗漏修复。其中 Barron's 工作树还有额外修改和未跟踪源文件，Luna 工作树有 `deploy/secrets`，Case 工作树有 `prebuilt-dist`。根据收敛报告逐项保留需要的源补丁/secret 后再退出，不能用 `rm -rf` 代替来源核实。

以下本次保留：

| 路径/对象 | 实际依赖 |
| --- | --- |
| `/home/ubuntu/doxagent`，约 808 MiB | 32 项 tracked 修改；systemd resource-safety 从此启动，正式 token/secrets 仍从此 bind mount |
| `/home/ubuntu/doxagent-release-8a8f60f2`，约 900 MiB | 正式 Compose；Guardian 的 `deployment/guardian-source` 活跃 bind mount |
| `/home/ubuntu/doxagent-w3-writer-repair-20261014` | 当前 Worker Compose 引用；两处未合入修复，必须先整合并切换 |
| MU/INTC/BE/RKLB 四目录，约 1.56 GiB | 四 Worker 仍 Up；真实任务/队列终态未核实，不按“测试目录”整体删除 |
| `/home/ubuntu/doxagent-stage`，约 283 MiB | auth/SDK 状态与临时构建混存，不能整目录当缓存 |
| `event-maintenance-*`、`doxagent-maintenance-*`、Barron's/Yahoo 修复源片段 | 收敛报告确认仍有遗漏；体积小，先合入，不为省几百 KiB 提前丢失证据 |

## 数据、备份与扫描边界

Read 当前约 **24.97 GiB**、Bus **5.90 GiB**、Runtime **4.93 GiB**。Read 虽是派生投影，也有历史 provenance 缺口、版本/token 状态和不可简单丢弃的历史读取约束，不作为本次可直接删的普通缓存。交易、研究、新闻原件、receipt/native files、Codex sessions、浏览器身份及业务 named volumes 均不列入删除清单。

六类已知备份入口的数据库/压缩文件及收据约 129 MiB（排除 immutable 内容子树）；没有重新积累出多套几十 GiB 的全量主库备份。另在 initialization 目录发现 `control.predeploy-20260921-102705.sqlite3` 约 0.432 GiB，属于待核对旧副本，不是当前 control 主库；其历史恢复作用、路径引用未专门验证，不纳入首批删除。这样的小项无需为本次合并继续展开恢复审计。

全量 du 在数据卷大量小文件处耗时，本次已终止自己的扫描；Docker `/system/df` 90 秒超时，未用其异常/缺失数字下结论。随后改用镜像 inspect/history 和 BuildKit 自身元数据，未对业务表做全表 COUNT、VACUUM 或内容哈希。主机 `/proc` cwd/exe 与 systemd 路径有检查，但未对任意业务数据中的历史路径字符串或所有打开 FD 做穷举；因此目录候选包含明确退出前提，不能当成无条件删目录授权。

16.38 GiB 比 10/13 收尾的 38.69 GiB 少约 22.3 GiB。本次看到大量新的构建缓存与统一发布产物，Read 也增约 2 GiB；这些足以说明合并发布余量应优先从可重建资源取得，但未逐字节归因全部增量，也未据此宣称应用根因修复失效。

证据：`eval/merge_storage_audit_20261014/` 中 `inventory.json`、`build-cache.json`、`cache-candidates.json`、`image-candidates.json`、`home-sizes.txt`、`backups.json`、`summary.json`。本次只有扫描与候选清单；后续若执行，应依据当时实际状态按精确 ID/路径处理并记录 df 净变化，不新增备份低价值副本。

## 后续授权执行：仅清理第一项构建缓存

2026-10-14 22:36，Asia/Shanghai，按用户“删除第一项，其他两项暂时不动”执行。重新读取 default builder 的缓存，取审计清单与当前 Reclaimable=true、Shared=false 的交集，共 177 个 ID；使用精确 ID 正则筛选调用 buildx prune。未执行 image prune、worktree remove 或业务数据清理。

本机版本的 du 布尔筛选 `shared=false` / `private=true` 没有返回预期记录，未据此执行清理；改为先核对元数据，再用已验证可用的 ID 正则筛选。筛选接口依据 [Docker 官方说明](https://docs.docker.com/reference/cli/docker/buildx/prune/)。`--all` 仅允许处理清单内的 internal/frontend 缓存，ID 范围没有扩大；`--force` 仅跳过交互确认。

- 177/177 个目标缓存记录已移除，命令 exit 0；BuildKit 报告回收 9.809 GB（约 9.14 GiB）。
- 根盘可用从 17,405,464,576 bytes（约 16.21 GiB）升至 25,964,527,616 bytes（约 24.18 GiB），现场 df 净增 8,559,063,040 bytes（约 7.97 GiB）。以实际 df 变化作为容量收益，不把工具逻辑回收量当作物理净释放量。
- 213 个共享缓存记录全部保留；前后镜像 ID/标签、51 个容器 ID/镜像/状态/启动时间一致。未重启业务、未删除旧镜像或 worktree。
- 完整命令、目标 ID、前后状态与回收输出：`eval/merge_storage_audit_20261014/cache-deletion-receipt.json`。
