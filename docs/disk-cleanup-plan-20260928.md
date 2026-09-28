# 新加坡服务器磁盘清理审计与方案（2026-09-28）

本轮仅做只读调查，没有删除文件、镜像、容器或缓存，也没有重启服务。

## 结论

- 根文件系统 `/dev/vda3`：177 GiB，已用约 161 GiB，剩余 9.1 GiB，95%。
- `/var/lib/containerd` 两个大目录：overlayfs 约 63 GiB、content 约 13 GiB，共约 76 GiB。不是 76 GiB 全部可删。
- 可批准的首轮方案：历史备份约 33.16 GiB，加 Docker 预计约 40–50 GiB；合计约 73–83 GiB。Docker 为规划估计，最终回收以 `df` 为准，不把镜像虚拟大小与共享缓存重复相加。
- 清理不需要重启后端。当前数据库、业务产物、浏览器 Profile、所有数据卷、运行镜像和 Guardian 的动态依赖均保留。

## 历史运维备份是什么

生产数据卷根目录：`/var/lib/docker/volumes/doxagent-v2_v2-data/_data`。下文 `/data` 对应此目录。

`src/doxagent/production_v2.py:migrate` 在实际数据库迁移前调用 SQLite backup API，将研究、初始化、Message Bus、Runtime、Read 等数据库复制到 `/data/backups/<UTC时间戳>/`。旧版快照还包含 scheduler、o4、usage。当前代码识别 schema-current marker 后不会因普通启动重新做整库备份；迁移备份代码没有保留数量或容量清理策略。

带 `v2-read-fix`、`o2-authoritative-recovery`、`resume-runtime` 名字的目录，以及 `/data/incident-backups/message-freshness-20260914`，是历史专项修复/迁移恢复留下的快照或验证副本。名字说明用途，不能单独证明字节相同或恢复完整。

这不是一个经过恢复验收的每日全系统备份：19 个顶层备份目录都没有 `backup_manifest` 生成的 `manifest.json`；最新迁移快照包含五个数据库及 `native-files`、`content-files`，不能据此宣称覆盖所有发布产物、配置、凭据或跨库同一时刻状态。

### 实际空间与候选

| 项目 | 去重实际占用 | 首轮处理 | 预计回收 |
| --- | ---: | --- | ---: |
| `/data/backups`，19 个目录 | 39.393 GiB | 保留最新 `20260920T214547524194Z`（8.893 GiB），清理更老快照的大体积数据库 | 约 30.47 GiB |
| `/data/incident-backups/message-freshness-20260914` | 1.277 GiB | 删除历史 Message Freshness 调试/验证副本 | 1.277 GiB |
| `/data/bus/backups`，三份专项备份 | 1.264 GiB | 保留最新 `pre-monitoring-activation-20260924.sqlite3`（0.471 GiB），删除两份 9 月 23 日旧副本 | 0.793 GiB |
| `/data/bus/bus.sqlite3.before-bc958c68` | 0.618 GiB | 删除旧迁移前副本 | 0.618 GiB |
| 合计 | 42.553 GiB | 保留最近两个恢复点及极小的 O2 特殊证据 | 约 33.16 GiB |

为避免丢失可能只存在于专项修复快照中的 Event Library 原始证据，保留 `20260911T064923141964Z-v2-read-fix` 两个 Event Library 文件（约 11 MiB），以及 `20260911T1050Z-o2-authoritative-recovery` 内两个顶层 Event Library 文件及 `event-library-primary-raw/event_library.sqlite3`（合计约 16 MiB）；该目录内旧 Read/Runtime/Initialization 数据库仍删除。保留约 27 MiB 原始证据不值得阻挡数十 GiB 的主清理。

特别注意：两个 `20260914T141029911261Z*` 目录有四个硬链接共享文件，总共享约 5.057 GiB。它们分别显示约 6.112 GiB，但一起删除只释放约 7.167 GiB，不是 12.224 GiB。上述总数按设备号、inode 去重。

### 删除风险核对

- 当前容器配置没有引用 `/data/backups/...` 或 `/data/incident-backups/...`；当前 Read 路径是 `/data/read/v2.sqlite3`。
- 检查时 `/proc/*/fd` 未发现打开上述备份路径的文件描述符；这是时间点证据，不是永久保证，执行前复查。
- 当前 research、initialization、bus、runtime 四个源库的 `v2_receipt_archive` 和 `v2_source_pins` 都没有记录。未发现当前运输归档依赖这些旧 checkpoint 的迹象。归档代码本身要求保留 checkpoint，未来不能无条件删一切备份。
- 在部署脚本和 systemd 小型配置中未发现这些历史目录的引用。没有扫描全部业务 JSON 的每个字节，也没有对多 GiB 快照运行全库 integrity check。
- 删除旧快照会永久失去相应日期的数据库回滚/取证副本；不删除当前 MU 初始化结果、新闻、workflow 数据或当前页面内容。很小的 O2 原始证据另行保留。
- 最新迁移快照时间是 9 月 20 日 UTC / 9 月 21 日北京时间，不覆盖之后完成的 MU 初始化与新增数据。保留它是过渡措施，不能称为当前恢复点。

## Docker：哪些可清，哪些不可误删

Docker API `/system/df` 与 `docker inspect` 联合审计：

- 50 个镜像记录；39 个未被容器引用。
- 其中 `doxagent-initialization-repair-agent:server` 虽然 Containers=0，仍是 Guardian 配置中的动态启动镜像，必须保留。因此当前未引用镜像中 38 个是清理候选。
- 4 个历史测试/迁移容器可以删除，再释放它们各自引用的旧镜像；总计 42 个镜像记录为候选。
- 42 个候选的 `Size - SharedSize` 合计 **37.553 GB / 34.97 GiB**。这是 Docker 镜像独占尺寸统计，不是实际删除后的磁盘承诺；组内共享层与压缩内容另计。
- 183 个 BuildKit 缓存记录，InUse 全为 false。显示总大小 36.332 GB，其中真正 non-shared 仅 **6.376 GB / 5.94 GiB**；不能将 36.332 GB 再加到镜像回收额上。
- Docker 报告镜像层去重总量 80.164 GB，未引用镜像 reclaimable 约 43.85 GB。删除目标与这个统计不完全一致：本方案额外释放四个停止容器的镜像，但保留动态修复镜像。
- 容器可写层本身只有约 18 MB，不是 76 GiB 的主因。数据卷另计，不能混在镜像垃圾里删除。

### 可以删除

未被运行或动态启动配置引用的 Yahoo driver/fix/recycle/headline、Barron's challenge/diagnostic/control、旧 freshness、旧 db-release、旧 Guardian 修复候选、pre-O2、pre-rebase、dangling 镜像，以及无引用的 curl/uv 工具镜像；删除前按完整镜像 ID 生成清单并再次比对引用。

四个容器只删除容器对象，不加 `-v`：

1. `doxagent-v2-v2-migrate-1`：已 Exited(0) 的旧迁移任务。
2. `doxagent-barrons-cdp-attach-control`。
3. `doxagent-barrons-playwright-chrome-control`。
4. `doxagent-barrons-chrome-control`。

三个浏览器测试容器的 Profile 数据卷继续保留，不因容器删除而抹掉人工登录状态。

### 必须保留

- 所有 17 个运行容器，以及它们的实际 `.Image` ID。不能只按标签或仓库名判断；部分运行服务使用不同的子镜像/历史镜像 ID。
- 当前 web、API、Site Access、Chrome Supervisor、egress 与 `doxagent-v2:production` 镜像及其运行子镜像。
- `doxagent-initialization-repair-agent:server`（`3cc03c11fd9d...`）。
- `doxagent-v2-initialization-repair-template` 容器及 `doxagent-v2:init-repair-source-1b74f2e` 镜像（`0522bf4e87bd...`）。Guardian 的 `DOXAGENT_INITIALIZATION_REPAIR_PRODUCTION_CONTAINER` 明确指向这个 Created 模板。Created 不等于废弃。
- 全部业务数据卷和浏览器 Profile。本轮不做 volume prune。

不能直接执行 `docker image prune -a` 或 `docker system prune -a`：它们可能清掉当前零容器引用的动态修复代理，后者还可能删除 Created 修复模板。不能直接删除 `/var/lib/containerd` 下的目录。

## 执行顺序与验收

1. 记录磁盘、完整镜像 ID、容器 ID/状态、mounts、Guardian 两项动态引用和清理白名单。立即重查是否新启动了修复/构建任务。
2. 删除上述四个指定旧容器，不删除任何数据卷。删除白名单内 42 个镜像记录，使用 Docker CLI，不强删被引用镜像。若执行期间出现新的引用，跳过该 ID。
3. 没有构建运行时，执行 `docker buildx prune --all --force` 清掉未使用构建缓存；不重启 Docker。代价是下次构建失去缓存、重新下载/构建更慢，并可能有数 GiB 的临时空间需求。
4. 删除历史备份白名单，解析每个绝对路径，确认落在上述已审计目录内；保留最新迁移快照、最新 Bus 快照和约 27 MiB O2 特殊证据。不要用未限定的通配符扫整卷。
5. 每批后比较 `df`、Docker 占用；核对 17 个运行容器 ID 未变化、Guardian 模板/代理镜像还在，验证 API 健康及实际 Overview 读接口。空间收益以 `df` 为准。
6. 获得空间后做一次当前版本的在线 SQLite backup + 必需的发布产物/immutable files 备份，优先导出到服务器外；不要为备份重启/停止后台任务，也不要直接 `cp` 活跃 SQLite 主文件忽略 WAL。当前五个主库合计约 17.5 GiB，产物另计。
7. 新备份带完整 manifest 并验证后，替换旧的 8.893 GiB 迁移备份和 0.471 GiB Bus 过渡备份。验证应包括独立恢复演练；仅有文件或 header 正常不等于可恢复。

首轮清理后预计根盘从约 161 GiB 已用降至约 78–88 GiB，剩余约 82–92 GiB。若再在本机保留一份新的完整备份，稳定占用会回升；服务器外保留则不会侵占系统盘。

## 防止重新堆满

- 本机仅保留一个已验证近期恢复集；重大迁移的临时回滚集在验收后到期删除。较长历史放到服务器外，建议保留最近两份及有限周备份，而不是在系统盘无限累积。
- 每次调试/部署结束登记并删除无运行或动态依赖的临时镜像。维护运行镜像与 Guardian 动态镜像/模板保护名单，不用全局 prune 代替依赖识别。
- 给 BuildKit 设置实际版本支持的 GC/缓存容量上限，建议 5–8 GiB；定期清缓存不需要重启业务。
- 根盘按 `/` 真实可用字节告警：剩余低于 30 GiB 预警、15 GiB 紧急；不要依赖已证实与根盘不一致的云控制台百分比。
- Read 主库目前 12.51 GiB、Bus 2.65 GiB，属于活跃数据，首轮不删。后续单独治理历史保留/归档和增长，不能把删活跃库当作垃圾清理，也不在高负载期间跑全库 VACUUM。

本方案未实施定时任务、配置或代码变更；这些属于后续明确授权的实施范围。
