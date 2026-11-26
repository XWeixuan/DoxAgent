# 四路投影停更与消息源目录读取修复回执

## 四路投影巡检

2026-11-23 排查生产 Singapore。初始 MCP 实测四路 `checked_at` 均停在
`2026-11-21T11:29:29Z`（美东 11/21 06:29），健康表内 head 与 checkpoint 相等。
这只是最后一次成功巡检的水位，不代表上游继续发布或没有采集积压。

根盘初始可用约 342 MiB、使用率 100%，inode 使用率 42%。Projector 日志显示
`sqlite3.OperationalError: disk I/O error` 后退出；容器随后因
`no space left on device` 启动失败。自动恢复条件要求至少 2 GiB 空闲，故一直没有恢复。
`docker ps` 的 Up 文本与 `docker inspect` 不一致；后者的 exited/PID=0 和宿主进程不存在
共同确认 Scheduler、Bus 等也已停机。没有仅凭 Up 认定运行正常。

清理构建缓存返回 0 B；一份无引用的过期临时镜像删除后没有可观的物理收益。
其余镜像通过现有发布 owner 的保护规则检查，保留运行、回滚、依赖和模板引用。
用户追加授权旧诊断归档后，流式传输到 D 盘，逐份回读校验 SHA-256，再核对远端
尺寸、mtime、SHA 和打开文件后移除原件：

| 批次 | 内容 | 文件数 | 原分配字节 | 归档位置 |
| --- | --- | ---: | ---: | --- |
| 诊断 | 结束的操作 JSON 导出、旧诊断、旧评估输出 | 5,540 | 1,040,302,080 | `D:/DoxAgent-Cold/20261123-projection-recovery` |
| 构建诊断 | 11/20 构建的 `symbols/codex.debug`，构建文档明确排除于运行镜像 | 1 | 1,103,372,288 | `D:/DoxAgent-Cold/20261123-projection-recovery-symbols` |

两处均保留 `manifest.json`、`diagnostics.tar.gz` 和 `retirement.json`。
归档成员保留原绝对路径去除首个 `/` 后的名字。恢复时取出目标成员到暂存目录，按
manifest 校验 SHA，再核对当前引用和同名文件，避免直接覆盖生产。正式数据库、交易记录、
当前工作区、SDK 运行二进制、初始化控制库和 native 正式产物均未由该归档脚本删除。

空闲恢复至约 2.3 GiB 后，在共享发布锁下按现有 current 镜像核对，依次启动因
ENOSPC 失败的 Projector、Message Bus、Content Enrichment、Initialization、CDECR、
Control、Maintenance、Guardian、Scheduler 共 9 个服务。保留已经运行的服务及正常结束的
一次性 migrate 容器，没有启动历史测试容器。没有重置 checkpoint、head、源 epoch 或缺口。

最终记录时间 `2026-11-23T10:15:30Z`（美东 05:15，北京 18:15）：

| 投影 | 当轮 head | checkpoint | checked_at (UTC) | error |
| --- | ---: | ---: | --- | --- |
| research | 141345 | 141345 | 10:15:30.276231 | null |
| initialization | 166258 | 166258 | 10:15:30.287408 | null |
| bus | 12779304 | 12779304 | 10:15:30.303870 | null |
| runtime | 5212880 | 5212880 | 10:15:30.317842 | null |

初始化当时没有新业务事件，其 event 时间仍旧；巡检时间持续更新。其余三路水位推进。
20 个常驻 Compose 服务实际 running、有宿主 PID；已配置健康检查者均 healthy。

## 消息源目录

独立复现：API 首次目录请求返回 202，MCP 的 `ApiClient` 仅接受 200，因而立即报
`UPSTREAM_READ_FAILED`；用同一只读凭据 GET `result_path` 又返回 403。
后台完成后目录本身能够返回 200。这与此前文章抽样空时间锚点的错误不同。

修复只涉及现有读取链路：

- MCP 等待已有异步读取结果，轮询总预算 40 秒，只接受精确的同源 `/queries/<32位hex>`
  路径，保留最终错误和 live-read 元数据。
- API 只读凭据允许 GET 精确查询结果路径，仍由 DeferredQueries 校验 owner 和 tier；
  未开放操作、独立研究或任意内容访问。
- 线上首验发现外层 MCP 15 秒预算仍会截断合法后台查询，故仅首次全局目录读取改为
  45 秒；目录后续分页和其他工具保留 15 秒。

发布版本：API overlay 为 `fadd1e86b463892e0b26dd6c21393c058f10793c`，
MCP 最终 overlay 为 `0fc8bc0547749619108e1ed658504eb202581bea`。
沿用 canonical 发布锁、Compose 配置和回滚记录；只覆盖三个相关源文件。
构建及部署后逐份 SHA 检查通过，API/MCP 健康检查通过。

验证：36 项相关测试通过，相关产品源码与测试 lint 通过。覆盖异步结果、总超时、错误透传、
恶意/跨域路径拒绝、跨用户结果拒绝、只读权限和分页。真实 MCP 目录 5 页、85 个源、
85 个唯一 ID；独立强制新查询 URL 的实际 AgentReads 调用在约 6.17 秒返回成功。
本次没有登录浏览器页面验收。

## 未消除的边界

- 根盘最终可用 **2,316,812,288 B（约 2.16 GiB）**，仍是严重低余量；这是应急恢复，
  没有完成长期容量治理，也不能承诺不会再次耗尽。源数据、数据库和工作区是后续容量核查对象，
  不能继续假定镜像/构建缓存能够提供足够空间。
- Runtime 的 **17,216** 条历史缺口与修复前相同，没有在本次修复中消除或隐藏。
- 采集接收队列初见 423 条 PENDING，最终仍有 **395** 条；队列同时有新捕获进入，
  不能用当轮投影追平来宣称全链路无积压。没有强制确认、丢弃或重放这些采集数据。
- 约 46 小时故障期的外部信息覆盖没有完整核算，不声称已补齐全部遗漏。

机器证据位于 `eval/projection_source_recovery_20261123/`：恢复动作、首次与最终状态、
MCP 分页、冷查询和部署回执。私有发布环境及凭据未复制到该目录。
