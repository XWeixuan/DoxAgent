# 远端 MAIN 收敛执行回执

日期：2026-10-15，Asia/Shanghai。依据：`remote-main-convergence-repair-plan-v2-20261014.md`。用户已授权执行，并另外明确批准关闭 Reuters 人工登录维护会话、下载整批执行回执。

## 1. 已完成的目标

有价值的遗漏修复已进入 GitHub `main`，生产已切换；临时发布代码及旧容器已退出。最终运行核对时间为 **2026-10-15 02:13:54 +08:00**。

| 项目 | 最终结果 |
| --- | --- |
| GitHub MAIN、远端主检出 | `21b9b7bd689643d39506892f0576b90d53ad497b`，一致 |
| 正式代码/部署入口 | `/home/ubuntu/doxagent`，分支 `main`，工作树干净 |
| 正式应用 | 18 个服务均运行同一 MAIN 源码；6 个配置健康检查的服务全部 healthy，其余 running；重启计数均 0 |
| 应用镜像 | 后端、Web、Site Access、Chrome Supervisor、Guardian、Repair Agent 共 6 类，标签均 `main-21b9b7bd` |
| 构建来源 | 镜像 revision 与源码归档 SHA256 均核对通过；754 个应用/提示词文件比对通过 |
| 运行引用 | 31 个已删除代码目录和已归档 stage 共 32 条路径：容器 mounts/env/labels/command、systemd、进程 cmdline/maps/cwd/exe/fd 均无旧路径引用 |
| Git worktree | 远端仅剩 MAIN 主检出 |
| 宿主资源保护进程 | active，入口 `/home/ubuntu/doxagent/scripts/resource_safety_controller.py` |

源码归档 SHA256：`120e8660d39b63dd784fa7c5fae64003ec206994a343ea3005e9994ddf348cec`。

专用镜像按角色分别构建；基础出口设施 `egress-clash` 保持原配置。业务卷、凭据、身份和恢复资料仍在 Git 外，不应把“MAIN 下运行”理解成把数据库和 secrets 迁入 Git。

## 2. 合入与修复内容

调查确认了三种遗漏来源：发布分支未合入 MAIN、生产覆盖层补丁未提交、部署配置未同步。并非所有旧目录差异都有价值。

- 原发布分支相对旧 MAIN 的 28 个独有提交已合入，并保留 MAIN 的双语 README。MAIN 从 `83d030a5` 普通快进推送到 `21b9b7bd`，没有强推。
- maintenance 分支缺失的修复按当前合同移植：Event Library 局部错误降级、分阶段失败预算、终态超时有限恢复、成功 O3 旧合同工件恢复、维护错误归因与成功收据保留、source_snapshot 限定、FAILED/HELD parent 的 dirty 保护、固定 Case 语义日读取。
- O3 恢复没有套用到 v2.1 TechnicalReceipt；保留现行 SWEEP 优先顺序、异步 SQLite、新 Policy 查询与 W3 task_phase/source_case_id 身份规则。
- W3 生产补丁进入源码：生命周期 RPC/关闭共锁、fork 后释放客户端 writer、W3 Data MCP 启动预算 90 秒；维护 O2/O3 预算 60 秒，其他旧节点仍 20 秒。
- Barron's 无包装卡片同样执行精确发布时间规则，补入无日期回归用例；保留现行正文延期重试等后续修复。
- Yahoo DE/TR 路由进入正式 server Compose，并核对真实 listener、批准节点映射及出口探针。
- 将已批准 IBKR/Chrome 依赖构建路径纳入正式模板：IBKR 10.49.2、protobuf 5.29.5、SDK 0.159.3；Chrome 复用批准系统层并替换成 MAIN 应用。Repair Agent 保留必要额外依赖。
- Docker 构建上下文排除 eval 和敏感状态；重要代码变更已追加 changelog。

未混入原本地主工作区中其他未提交的回测等工作。整合在独立 worktree 完成：`C:\Users\WEIXUANXIE\.codex\worktrees\main-convergence\DoxAgent`。

## 3. 四项高风险保护的实际结果

| 风险 | 实施与验证 |
| --- | --- |
| Git 脏树/生产补丁 | 保存基准提交、staged/unstaged 差异及有价值的 untracked/ignored 文件；12 组补丁恢复检出通过。唯一 Luna 提交 `88291b93` 有异机 bundle 和 rescue 引用。可写层候选、额外归档内独有内容另行提取并验证，之后才清对应对象 |
| 生产数据库/WAL | 原生产卷原路径复用；用 SQLite Backup API 制作一致快照，不裸复制活跃主库、不删除 WAL。恢复集包含 489 个数据库、562,808 个基线工件文件及 677 个增量文件。全部 489 个库隔离恢复打开，重建字节、schema SQL 和语义锚点匹配；代表性外置工件引用核对通过 |
| Volume/可写层 | 固定原有 named volume 名称；研究 Worker、测试容器逐个检查可写层与队列，保留唯一产物后退出。容器删除不带 `-v`。两个无引用匿名卷经完整文件清单/哈希、空配置、标记重复性及运行引用复查后精确删除 |
| Secrets/身份/Profile | 原秘密配置及权限保留，受限加密恢复包已有异机副本；四个 Profile 经现有维护快照流程保留，与 Site Registry/映射配套。Site 可写层 NSS 检查确认无自定义证书/私钥。保留三个仍有订阅域 Cookie/本地存储的旧 Profile 卷 |

权威恢复边界为 **2026-10-15 00:25:41.211089 +08:00**。生产恢复集最终密文合计 **6,136,393,968 字节，约 5.71 GiB**；历史 468 个冻结数据库由独立小包合并压缩，并验证全部重建哈希。没有为缓存、依赖、Read 大库另做整套冗余拷贝。

恢复验证分为两层：异机验证所有加密包的哈希、解密及包内文件哈希；远端隔离恢复打开全部 489 个数据库并核对锚点。生产没有从备份恢复数据库，也没有重放订单。四个核心库的 epoch 保持原值，最终写入水位均不低于切换后首次验收。

浏览器恢复包 **125,147,504 字节，1,859 个文件**，含四个原身份的 Local State/Cookies 和一致 Site Registry；缓存、下载组件与遥测不进入异机包。现有 Site 卷内完整原生快照保留，由原快照机制管理。没有删除仍由 Registry 引用的快照。

受限恢复材料位置：

- 远端：`/var/lib/doxagent/main-convergence/20261014/protection-r2`、`authority-recovery-r2`、`browser-recovery`。
- 异机：`D:\DoxAgentPilot\backups\main-convergence-20261014\protection`、`authority-recovery`、`browser-recovery`。
- 加密密钥单独受限保存；本回执不记录密钥或凭据值。

## 4. 删除与保留清单

| 对象 | 已完成处理 |
| --- | --- |
| 旧代码/发布目录 | **31 个删除**：6 个旧 worktree、4 个隔离研究检出、19 个 overlay、2 个最后的生产发布目录 |
| stage 混合材料 | 原路径退出；整个目录同文件系统原 inode 原子改名至 `/var/lib/doxagent/main-convergence/20261014/legacy-stage-material`。约 283 MiB，旧库归档、SDK SQLite/WAL 和敏感材料保持完整，未按垃圾删除 |
| 旧容器 | **32 个退出并删除**：4 个研究 Worker、25 个已退出测试/smoke、2 个 Created 旧修复模板、1 个旧 migrator。另有 18 个正式服务正常重建替换，不计入这 32 个 |
| 旧镜像 | **57 个无引用候选精确删除**，镜像清理期间 df 实测约增加 4.62 GiB。保留当前 MAIN、必要回退及批准依赖镜像 |
| 构建缓存 | 仅定向清掉 1 个已完成 regular record，工具报约 716.9 kB，df 无可归因净收益；不计作数 GiB 回收 |
| 无引用匿名卷 | 精确删除 `99760e7d…be276`、`aedb5b14…ee01` 两个网络缓存卷；配置文件均为 0 字节，标记文件与当前活动卷相同，GeoData 可重建。活动 Clash 卷保留 |
| 本任务重复物 | 统一恢复包验证后，清掉 468 个旧独立历史库包及其他重复/中间任务材料，共 478 个精确对象，df 阶段净增加约 2.11 GiB |
| 本地测试临时物 | 仅本任务 6 个已结束测试/源码快照目录退出，释放 492,527,616 字节；测试回执保留 |

旧代码目录总计 31 个，精确名称见本地 `retired-*.json`；最终引用复核包含这些目录和旧 stage 共 32 条路径。没有全局 prune、删业务卷、清研究 workspaces、压缩数据库或删除业务历史。

明确保留的少量例外：

- `doxagent-barrons-cdp-attach-control-profile`、`doxagent-barrons-chrome-control-profile`、`doxagent-barrons-playwright-chrome-control-profile`：分别有 274、281、3 条未过期或 session Cookie，并有订阅域记录和本地存储；尚未证明可安全舍弃。此元数据不等于登录仍有效，故未伪称已验证有效登录，也没有为它们重建身份。
- `/home/ubuntu/doxagent-acceptance`、`/home/ubuntu/doxagent-backups`：历史验收/恢复资料，不是运行代码入口。
- `legacy-stage-material`：混合历史恢复资料，已从发布路径归档；没有运行引用。
- 当前 MAIN、批准 IBKR/Chrome 依赖和必要回退镜像，以及一套已验证权威恢复集。

最终根盘可用 **10.178 GiB**（02:13:54 快照）。镜像与重复包清理的阶段收益不能相加当作最终净收益：本次构建/恢复集有真实空间成本，正常生产写入也持续占用空间。本轮没有扩展到另一轮业务容量治理。

## 5. 生产验收与证据边界

- 定向回归 **81 passed**；合同回归 **66 passed**，两套有重叠，不应合计称 147 个独立测试。相关 Ruff 与 diff 检查通过。
- 六类镜像构建、依赖导入和最终标签后的 smoke 通过；镜像 RootFS 未因补充来源标签改变。
- MAIN 切换后的自然 W3 `trade_decision`、`post_decision_operations` 已成功 SETTLED。最终追加观察的最近 15 个任务中，14 个 succeeded/SETTLED、1 个正在正常 RUNNING，错误码均为空。没有为验收额外发起模型任务。
- Barron's、DIGITIMES、SeekingAlpha 切换前后使用相同身份探测已配置文章，均 SUCCESS/HTTP 200、page_kind=article、无认证/付费墙阻断。Managed Chrome 版本、HOME 和 UID 保持一致。
- Reuters 维护会话已按用户批准关闭并完成浏览器切换；原 Profile 保留。原认证状态 UNKNOWN，不将其计入“三站身份通过”。
- Yahoo 路由 DE/TR 在正式配置启用，分别指向 listener 18083/18085；实际批准节点映射、READY 状态、IP 出口探针通过，未额外声称独立地理位置鉴定或全 ticker 完整采集验收。
- Guardian 已验证从 MAIN 实际 clone/fetch、计算生产源码 hash、读取当前初始化容器和修复模板。持久受限 system Git 配置解决只读 Ubuntu-owned MAIN 仓库的 safe.directory 问题。
- 未强行发起 MAINTAIN/O3 全流程实跑；这些改动的本轮证据是定向/合同测试与部署来源验证。当前通过不代表所有后续业务任务永不失败。

## 6. 执行中发现并纠正的问题

以下记录实际偏差，不将“最终成功”写成过程零错误：

1. 首轮 scheduler 120 秒停止超时导致 exit 137，检查为非 OOM；立即中止该轮、恢复旧服务，未删除原库。后续按实际排空改用无固定短截止的正常停止；旧 Python PID1 的 transport 另用 SIGINT 退出。最终正式切换的受控退出为 0/143，初始化/CDECR 的主动取消为 130。
2. 初始恢复检查误把 SQLite 内部 schema_cookie 当作必须原样保留的业务字段；改为 schema SQL、epoch、水位及业务锚点。备份控制器中 SQLite 连接未及时关闭导致 171 库后文件描述符耗尽；保留同一冻结边界，修正 closing 后续完成剩余 318 库，再恢复服务；最终 489 库全部重建验证通过。
3. **删除 INTC 独立检出前漏查 BE 对其共享 Git 元数据的依赖，导致 BE 的 `.git` 指针失效，这是可避免的操作失误。** BE 业务文件未删除。随后核对其基准已在 MAIN、staged/unstaged 与保护包一致、受保护文件哈希一致，再用 no-checkout/read-tree/repair 重新关联主仓库；验证无业务文件改动后才退役 BE。证据 `be-git-association-recovered.json`。
4. 批量 overlay 清理曾被自动审批拒绝，原因是额外归档和 SQLite 状态保护不足；转为逐文件/归档成员检查，补保护 239 个额外文件及 42 个独有内容并异机验证后，才按精确对象删除。该阻塞已解决。
5. Guardian 的源码 hash 标签与 root 访问只读 Git 的信任配置问题在最终退出旧发布目录前捕获并纠正；最终实际 clone/fetch/hash 校验通过。
6. 整批回执下载曾被自动审批以“可能包含私有路径/身份元数据、未有该批导出的明确批准”拒绝；用户随后明确批准重试，**35 份白名单回执下载成功**，另保存 1 份最小 Worker 活动回执。密钥、Cookie 正文、备份数据包不在该批回执下载中；没有剩余审批阻塞。

## 7. 后续正常发布入口

正式部署入口固定为 MAIN 下三份受管 Compose 与原外部环境文件：

```bash
docker compose -p doxagent-v2 \
  --project-directory /home/ubuntu/doxagent \
  --env-file /home/ubuntu/doxagent/.env.v2 \
  -f /home/ubuntu/doxagent/docker-compose.v2-production.yml \
  -f /home/ubuntu/doxagent/deploy/docker-compose.server.yml \
  -f /home/ubuntu/doxagent/deploy/docker-compose.initialization-repair.yml \
  ps
```

以后发布新 MAIN 时，应构建对应角色镜像并同步持久环境中的镜像标签、提交与源码 hash，再用此正式入口切换；仅 push 不会自动完成部署。不要恢复临时 Compose overlay。代码回退复用当前业务卷，不自动回滚切换后产生的数据库事实。

本地证据目录：[执行证据](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/main_convergence_20261014)，其中 `receipts/` 保存获批下载的原始回执；`progress.json` 为最终汇总。远端原始回执保留在 `/var/lib/doxagent/main-convergence/20261014`。`main-cutover-receipt.json` 的 pending 字段是切换瞬间历史阶段，完成状态以 `final-runtime-audit.json`、本回执及最终 progress 为准。

本回执是执行记录，尚未单独提交 Git；生产代码保持已验收的 `21b9b7bd`，本地主工作区其他未提交工作保持原状。
