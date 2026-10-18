# 远端再次接近满盘：可重建资源扫描

> 后续用户已授权清理两类对象并要求深入追溯：清理与完整占比、额外候选及 46.48 GiB 回收后的复发分析见 [深查报告](remote-disk-regrowth-and-cleanup-20261017.md)。下文保留最初只读扫描时点的状态。

扫描时间：2026-10-17 北京时间约 04:20 起，证据时间使用 UTC。主机 `doxagent-sg`。本轮只读扫描，未删除资源、重启服务、修改 Dockerfile 或部署配置。上次仅清理构建缓存的授权不扩展到本轮镜像/业务数据删除。

## 当前结论

根盘 176.54 GiB，扫描开始可用 **1.03 GiB**，后续采样 **0.99 GiB**，`df` 显示 100%。最有价值的候选是 **31 条非共享可回收 BuildKit 缓存（约 6.01 GiB）** 与 **12 个无引用旧 MAIN 镜像（去重专属层约 8.24 GiB）**。

两个数字属于不同元数据口径，不能保证合计释放 14.25 GiB。BuildKit/containerd 的内容、快照和共享引用会影响实际物理释放；应按精确 ID 分步处理并验证 df。

当前只有 31 个镜像、25 个容器（19 running、6 exited）。旧 `/home/ubuntu/doxagent-release-*`、多处临时工作树和四个长期研究 Worker 已退出；Git worktree 列表只剩主检出。不能再使用 10/14 的 57 镜像/6 工作树删除清单。

## 建议的清理顺序

| 优先级 | 对象 | 规模 | 判断与影响 |
| --- | --- | ---: | --- |
| 1 | BuildKit `Reclaimable=true && Shared=false` | **31 条，约 6.01 GiB** | 可重建过程数据；按当前元数据与精确 ID 清理。后续构建需要重新下载/安装 |
| 2 | 无容器、动态 image 环境引用及已识别构建依赖的旧 MAIN 镜像 | **12 个，专属解压层约 8.24 GiB** | 12 个镜像的 revision 标签均对应当前 MAIN 可达 Git 提交，可从已有源码恢复；当前生产、动态 agent 和 approved SDK/Chrome 依赖保留 |
| 3 | 6 个已退出 W2 模型比较/low 测试容器 | 日志合计约 58 KiB | 可在保留需要的测试回执后收尾；本次未统计 writable layer，不能承诺大收益。它们固定 `main-e929134d`，但该标签同时仍出现在运行容器的配置环境，不能只删容器就默认删该镜像 |
| 低优先 | 主机下载缓存、系统日志、旧小型施工资料 | 主机 `.cache` 约 150 MiB；整个 `/var/log` 约 595 MiB；`main-convergence` 约 319 MiB | 有容量，但远小于前两项；不为这点收益截断活动日志、删除凭据或丢弃发布回执 |

BuildKit 共 137 条记录、约 19.17 GiB；其中约 13.16 GiB 标记共享，不能把整个 19.17 GiB 当成此次非共享清理收益。大私有记录包括 Playwright 安装 1.46 GB、uv 安装 1.12 GB、uv 下载缓存约 840 MB、pnpm 安装约 558 MB，以及多个约 537–557 MB 的构建上下文记录。

## 12 个旧镜像的精确范围

| 标签 | 镜像 ID 前缀 |
| --- | --- |
| `doxagent-v2:main-21b9b7bd` | `5d1f9859bf7a` |
| `doxagent-v2:main-483d4771` | `18edf21f7e30` |
| `doxagent-v2:main-fcc4f70a` | `8cff27095032` |
| `doxagent-v2:main-0d1791ef` | `5be2d73453e8` |
| `doxagent-v2:main-a3713de3` | `a4fce72c0d63` |
| `doxagent-site-access:main-21b9b7bd` | `1bc4a745c580` |
| `doxagent-site-access:main-483d4771` | `93408e5a7cac` |
| `doxagent-site-access:main-fcc4f70a` | `17a6aaf270e1` |
| `doxagent-v2-web:main-21b9b7bd` | `ab69e624304f` |
| `doxagent-chrome-supervisor:main-21b9b7bd` | `928845676419` |
| `doxagent-initialization-guardian:main-21b9b7bd` | `cc53c598dc72` |
| `doxagent-initialization-repair-agent:main-21b9b7bd` | `82a9749c0e8f` |

完整 ID 见 `eval/storage_rescan_20261017/image-candidates.json`。Git 可恢复性及镜像 revision 标签证据在 `image-recoverability.json`；逐个核对均通过。删除前仍需重验引用，不能使用全局 image prune 或 force 越过引用。

保留当前所有运行镜像、退出容器固定的镜像、环境配置仍引用的版本，以及 `doxagent-initialization-repair-agent:main-618fd9c5` 动态 agent。`doxagent-ibapi:approved-7d361219` 和 `doxagent-chrome:approved-cc38be0a` 仍是标准 Dockerfile 的实际构建依赖，不能因没有运行容器而删除。三个无标签镜像本次未确认构建身份，不计入上述候选。

## 这次为什么又很快满

10/14 清理后根盘可用约 24.18 GiB；本次仅约 1 GiB。现在可以明确证明一条新的重复增长机制：**MAIN 标准构建把每次变化的发布身份放在大依赖安装之前，导致依赖层失去跨发布复用。**

远端 `Dockerfile.v2` 在 apt/uv/Playwright 安装之前声明 `DOXAGENT_BUILD_COMMIT`、`DOXAGENT_SOURCE_SHA256`，且将 commit 写进早期 ENV/LABEL。实际 `main-00acd74a`、`main-3c30365a` 的镜像 history 显示，这两个不同发布参数进入了 apt、uv、Playwright 的 RUN 环境。

两版本分别生成约 839 MB 的 uv 依赖层和约 1,022 MB 的 Playwright 层，而不是只生成源码差异层。按当前 MAIN 后端镜像 RootFS 去重，约 1,022 MB 的 Playwright 层有 **10 个不同 DiffID，合计约 9.52 GiB 解压层**。这是整个现存 MAIN 后端版本集合的数据，不是可直接额外回收的量，也不与前文候选/缓存重复相加。

这与 [Docker 官方 ARG 缓存语义](https://docs.docker.com/reference/dockerfile/#impact-on-build-caching) 一致：RUN 会隐式使用此前 ARG 的值；只把 ENV 后移、却保留发布 ARG 在依赖安装之前，不能完整解决。

建议的最小后续修复：把发布 commit/source SHA 的 ARG 声明及对应 ENV/LABEL 放到稳定依赖安装之后；保留版本追溯字段。版本发布验收后，定向退役本次替换掉且已无引用的旧版本和私有中间缓存。无需另造 CI 平台、配额框架或高频清理任务。**本轮仅发现并提出修复，尚未实施或重新构建验证。**

另有自然数据增长：Read 主库从 10/14 约 24.97 GiB 增至 **27.99 GiB**，research 从约 0.94 增至 **1.48 GiB**；Bus **5.90 GiB**、Runtime **4.93 GiB** 基本不变。本次没有逐字节解释全部约 23 GiB 的净消耗，不将上述机制宣称为唯一原因。

## 数据与恢复副本的边界

- Read GC 与运输归档均在运行。现场 GC 实际每批回收 34/44 行、无错误、仍是 `BUS_POLL_ONLY`，保护 runtime；Bus 运输归档每批推进 30/24 行，Runtime 为 0。不是此前服务完全未启用的情况。
- Bus 约 2.13 GiB、Runtime 约 1.02 GiB 是 SQLite 内部空闲页，可被后续写入复用，当前不等于 df 已释放。Read 空闲页仅 2 页。此时为回收这些页再制作大 shadow，收益和施工成本不如先处理缓存/旧镜像。
- 不直接删除 Read 主库、新闻/交易/研究数据、native/receipt files、Codex sessions、浏览器身份或业务 named volume。
- 六类旧备份入口未发现新增大规模全量库备份；`incident-backups` 为空。`initialization/control.predeploy-20260921-102705.sqlite3` 约 0.432 GiB，仍属于需要专门核对恢复作用的旧副本，不列入首批。
- MU/INTC/BE/RKLB 的原研究数据目录仍存在，MU 还有 `research-before-*`/`research-wrong-model-*` 库；生产 validation 含 adoptions。虽然研究 Worker 已退出，也不能据此认定研究原件可由 Git 重建或正式采用引用已解除。本次没有将它们计为可直接回收空间。
- `/data/prebuilt/cdecr` 约 183 MiB，可能属于运行任务所需构建资源，未判为可直接删。`/tmp` 全部约 65 MiB，主机只剩主检出，没有上次 2.11 GiB 旧 worktree 候选。没有发现占用根盘的大型 deleted-open 文件；三个 pulseaudio memfd 属于内存文件。

## 取证与限制

证据目录：`eval/storage_rescan_20261017/`，包括镜像/容器/挂载/Git 清单、私有缓存 ID、镜像候选/源码可恢复性、远端 Dockerfile 与实际 image history、目录大小、SQLite 页数与维护日志。

主机小目录统计完成。数据卷完整 du 40 秒超时，仅取得 codex-home 约 9.90 GiB、Read 目录约 32.51 GiB 等部分结果；另对旧测试目录和 workspaces 采用每项 8 秒预算，超时部分明确未得总量。没有将部分扫描结果写成整个数据卷总占用；未做业务表全表扫描、完整恢复演练或满盘条件下的大库复制。未改动同一工作区中其他正在进行的 Worker/prompt 修改。
