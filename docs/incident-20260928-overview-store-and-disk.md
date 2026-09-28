# 2026-09-28 Singapore Overview 读取故障及系统盘核查

核查目标：`ubuntu@43.163.67.97`、`VM-0-15-ubuntu`，腾讯云实例 `lhins-akg2bdyb`。时间为北京时间 2026-09-28 01:29 起。

## 页面故障

实际已登录 Chrome 页面 `/overview` 可打开，但默认请求 `period=CURRENT_TRADING_DAY`，read-context 返回 503。API 日志记录 `child_ValueError`，失败耗时约 30–260 ms，没有本轮冷启动硬超时的证据。

远端交易日历核查：`2026-09-27T17:29Z` 对应纽约周日，`is_trading_day=false`；`PageCalendar.period(CURRENT_TRADING_DAY)` 抛出 `ValueError: NON_TRADING_DAY`，查询工作进程将普通异常泛化为 `STORE_UNAVAILABLE`。人工刷新保持同一无效周期，因此无法恢复。下游 Overview 查询依赖 context，停留在 pending，表现为持续加载。

修复前手动选择 `PREVIOUS_TRADING_DAY` 后，真实登录页面正常显示 BE/INTC/MU/RKLB 及统计。Web 日志确认 read-context、overview/status、overview/tickers、overview/metrics 均返回 200。

修复：首次没有显式周期时，前端先通过可用的前一交易日读取服务端 period_options；休市日明确选择前一交易日并同步 URL，正常交易日继续请求本交易日。显式指定的休市本交易日保留契约语义，提示用户选择可用周期，不冒用前日数据。后端返回 VALIDATION_FAILED/422；前端 context 失败时结束依赖模块的加载态。

验收：后端周末/圣诞节不可选周期、消息页连续语义日、真实 spawned worker 共 4 项通过；前端周期读取 5 项通过，类型检查、定向 ESLint、远端生产构建通过。真实登录浏览器默认入口选择前一交易日、显示四个 ticker；显式休市周期显示具体提示，所有依赖模块不再无限加载。指标及表格标题已同步为“前一交易日”。1262px 桌面核查无页面横向溢出、加载占位或 STORE_UNAVAILABLE。部署限定 API/Web；未重启 ticker、scheduler、projector 或修改业务事实。

API 镜像：`sha256:53fa909f48aa7b530054806edc2c40e11a99400879161b53455b04d718e6fedd`；Web 镜像：`sha256:b96ff3427868b8b78067e86282d70f8e6d62c90705f0655f6a533bd68278cb1c`。API 容器内 views.py 与本地修复 SHA256 完全一致。隔离诊断应用访问同一生产读取库：休市 CURRENT 返回 VALIDATION_FAILED/422，PREVIOUS 返回 200、37 ms；该诊断不代表外部认证验收，外部认证读取以真实浏览器及 Web 日志为据。

最终浏览器验收：1262px 与 1559px 均无页面横向溢出或重叠；两次快照均为 `module-loading=0`、没有 STORE_UNAVAILABLE。实际手工刷新后 read-context（MANUAL）、status、metrics、tickers 均返回 200。API/Web healthy，其他业务服务保持原容器运行时长。浏览器尺寸测试后已恢复。

## 磁盘事实与控制台差异

`lsblk -b`：系统盘 vda 为 193273528320 bytes，即 180 GiB；根分区 vda3 为 ext4、挂载 `/`。EFI 分区 vda1 挂载 `/boot/efi`，约 536 MiB。实例、公网 IP 与控制台一致。

初次 `df -h /`：根文件系统约 177 GiB，已用 169 GiB，可用 1.2 GiB，Use%=100%（显示舍入及保留块口径）。这不是 inode 耗尽，inode 使用率约 11%。

可重新生成的 Docker 构建缓存超过一天未使用的部分经 `docker buildx prune --force --filter until=24h` 清理，工具报告回收 15.94 GB。清理后 `df -B1 /`：总量 189559676928 bytes、已用 165253263360 bytes、普通用户可用 16482279424 bytes、Use%=91%。后续生产重建会重新使用部分空间，不能把清理后瞬间值当作最终剩余容量。

已直接查看用户已登录的腾讯云控制台：概要显示系统盘 2.1 GB / 180 GB、1.17%。同时读取腾讯云 barad 监控代理的实际 DiskCollector 上报日志（2026-09-28 01:41）：

| 分区 | 上报容量（MiB） | 上报使用率 |
| --- | ---: | ---: |
| vda1 / EFI | 536 | 1.17% |
| vda3 / 根文件系统 | 180778 | 93.97% |

控制台的 1.17% 与 EFI 完全一致，180 GB × 1.17% = 2.106 GB，与显示的 2.1 GB 一致。这强烈指向控制台概要分区选择/汇总错误；没有访问腾讯云内部实现，因此不能断言具体服务端代码错误。代理已采集根分区的高占用，`df` 与目录统计一致，应以根文件系统实时值判断容量。

## 空间归属

以下为清理后、重建前 `du -xhd1` 的实际文件占用，约数单位 GiB；父子项不得重复相加。

| 路径 | 占用 | 内容 |
| --- | ---: | --- |
| /var/lib/containerd | 70 | Docker 镜像、解包层及内容存储 |
| 其中 overlayfs snapshotter | 58 | 镜像及容器文件系统层 |
| 其中 content store | 12 | 镜像压缩内容 |
| /var/lib/docker/volumes | 70 | 持久化数据卷 |
| 其中 doxagent-v2_v2-data | 66 | 应用数据及历史备份 |
| v2-data/backups | 40 | 多次运维全量 SQLite 备份 |
| v2-data/read | 13 | 当前读取库；v2.sqlite3 文件 13431152640 bytes |
| v2-data/bus | 5.0 | 消息库及旧版备份 |
| v2-data/runtime | 2.3 | Runtime 数据及附属文件 |
| v2-data/workspaces | 2.0 | 工作区产物 |
| v2-data/codex-home | 1.4 | Codex 数据及日志 |
| v2-data/initialization | 1.4 | 初始化数据 |
| v2-data/incident-backups | 1.3 | 事故恢复备份 |
| doxagent-v2_v2-site-strategy | 3.8 | 其中浏览器 profiles 约 3.3 GiB |

历史备份举例：20260920T214547524194Z 约 9.0 GiB；20260914T141029911261Z-resume-runtime 约 6.2 GiB；20260914T100440657364Z 约 5.7 GiB。旧 server/site-access/chrome 镜像也存在多个版本。Docker system df 的镜像大小与 Build Cache 共享层，不能简单相加估算磁盘总量。

本次未删除业务库、工作区、历史备份或回滚镜像。磁盘高占用属于独立容量风险；当前页面故障有可复现的 NON_TRADING_DAY 异常链，不能将其归因于磁盘满。

部署完成后 01:46 再次采样：containerd 约 76 GiB；根盘已用 172007735296 bytes（约 160.2 GiB），可用 9727807488 bytes（约 9.1 GiB），Use%=95%。Docker 镜像总计 50 个、11 个 active，报告可回收 43.85 GB，构建缓存可回收 6.376 GB；这些 Docker 统计含共享层，不能相加保证最终回收量。旧版本镜像涉及回滚保留，历史备份涉及恢复能力，本次未清理它们；后续应按明确保留清单处理，容量风险仍未彻底关闭。
