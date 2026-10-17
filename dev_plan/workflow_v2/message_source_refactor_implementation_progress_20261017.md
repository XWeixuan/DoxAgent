# 消息源隔离、主备与维护重构实施记录

更新：2026-10-18。依据同目录 `message_source_isolation_failover_maintenance_refactor_implementation_plan_20261017.md`。软件验收、生产迁移、真实网站认证分开记录。

## 已实施

- source/publisher scope、CAS revision、Identity 唯一归属；scheduler/Crawler Plane/正文补全/IPC 回调传递来源。跨站正文解析最终 publisher 独立身份，ticker 不增加 Chrome。reader-first 协议兼容，旧客户端不收到新增字段。
- 两种 Runtime 创建页前都持久化 allocation intent/marker，绑定 instance/target。Challenge 原子交接 Task，释放全局与身份两层业务容量；普通登录在 ACK 前持久化 exact target。跨连接只回收明确归属的 BUSINESS 页，不猜测关闭未知 blank 或人工页。
- 每 Identity 独立 Python/Playwright Node、有界 IPC、epoch/PID/start-time 核验；子进程不持 Registry 写连接、不继承管理/模型秘密。控制取消失败只退休对应控制，不能确认旧进程结束时拒绝第二写者。External Chrome 与连接解耦。
- 一个主、最多一个冷备：同 runtime/release/环境/access，真实 IP/地区、正式 parser/正文 canary、单飞预算、sticky。付费备用需当前认证及两篇真实订阅全文，不能凭 200 空壳或公开全文声称可接替。
- Challenge 与常规 Login 独立；Later 释放 Viewer 不解除阻断；原页失去准确 TARGET_LOST，显式重开创建关联任务。异步 operation 有阶段/deadline/取消/终态。Verify 不导航原页，另开临时页验证；Challenge/auth/crawler/body 分别提交，仅显式登录提交一次 credential epoch。
- 英文 Tk 双标签、主线程 mailbox、异常解除 busy、单次缓存 inventory、1s/3s 状态轮询；Viewer 校验 session generation。root bridge 固定动作/短锁，兼容 CLI 不持锁等网站，秘密不出桌面。无 Docker 组、密码保存、自动 MFA。
- Supervisor 持久 manifest 与 PID/CDP UUID/Profile/环境核验；Python 进程重启能接管同一 Chrome。整容器重建仍会丢活页。max_instances16、Managed8、Supervisor8/9GiB/2304PID；冷备 idle120s、低频 publisher600s，实际活动主角色常驻。
- 有界 UTC retention/小时汇总/结构化脱敏失败形状；冷缓存清理受 flock/进程/FD保护。快照流式 hash、原子归档、缓存排除、保留 Cookie/必要存储、每批一份 Registry、两个普通快照及保护恢复集。
- 标准 Dockerfile 发布标识后移稳定依赖层；依赖清单完全相同时可从批准镜像复用全部依赖 DiffID，构建不可变源码镜像，而非运行时 overlay。迁移默认只读 preview、显式 revision/CAS/一致备份、Profile 唯一继承、不复制 Cookie。

## 验收

- 最终扩大本地回归 **288 passed / 1 Windows symlink skip**，93秒；另有 curl_cffi Windows Proactor 提示。Ruff及本轮diff空白检查通过。
- 隔离 Linux 原生 Chrome153/CfT、本地不转发代理、临时独立 Profile 实测：Challenge 交接释放容量、控制重建保留人工原页、createTarget 丢响应通过 marker 找回、跨连接精确清理、独立正文验证、Supervisor 仅进程重启接管同 PID/UUID、正常冷启动 Cookie 保留、双轨独立驱动。
- 真实测试暴露并修复：空 controller owner 被误拒；恢复 Challenge 错误要求 login_url；raw-CDP 清理后残留 disposed Page GUID 破坏恢复；恢复人工页错误占用业务 lease。
- 合成页面验收不等于真实账号/订阅正文验收；生产主备状态需逐个报告，未登录备用不得列 READY。

## 授权与生产检查点

用户已授权清理未使用 Docker build cache / 7天前 journal，不删镜像、容器、业务库、代码、Profile。实际释放缓存2.755GB、journal0，清理后根盘约3.6GiB，不适合完整重装依赖构建；只允许清单一致的有界依赖复用发布。
用户已授权冻结原 instance/target/Task 后升级 Supervisor 容器，保留 Profile/Cookie、丢页待办准确 TARGET_LOST。
本记录检查点尚未完成正式提交、镜像发布、scope迁移和逐源自然周期验收，后续以发布收据补充，不冒充全部生产完成。

## 操作与长期验收

遵循主方案 reader-first；短暂停相关采集/补全完成 scope 迁移再恢复，不重置文章时间/检查点。保留升级前原页清单、Registry一致备份、preview/apply/重复KEEP、commit/image/source hash与依赖DiffID、人工待办。
各源覆盖启用ticker/L1、10自然目标周期、最终publisher两篇正文；就绪备用需真实冷启及接替。缺登录/订阅/Challenge/独立同地区出口列待办。24–48h窗口及7天资源净增长待期限观察。
不修改并行Jev/初始化/SDK/MCP脏改动；Source Maintenance Codex Worker不部署且禁用。保留60s polling、30min freshness、closed sweep、去重和Taiwan付费排除。
