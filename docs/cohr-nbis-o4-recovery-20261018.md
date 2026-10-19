# COHR / NBIS O4 部署故障修复（2026-10-18）

06:13:46 北京时间探测：两者 FAILED / O4_CONFIGURE，唯一失败节点均为
`o4.configure`，错误 `Site Access is enabled but the worker token is unavailable`。
COHR D3 已结束，544 个成功节点；NBIS D3 已结束，967 个成功节点。

## 原因与修复

初始化服务在进程内调用 `build_monitoring_o4_runtime()`，由 Crawler Plane
读取 Site Access worker token。生产环境启用了 Site Access，并配置
`/run/secrets/site_access_worker_token`，但初始化服务没有声明该 secret，实际容器也没有挂载。
独立 `v2-o4` 服务有挂载，不能替代初始化进程自己的运行依赖。本次不是 token 过期。

修正基础生产 Compose：初始化服务声明 worker secret 并依赖 Site Access healthy。
扩展现有部署契约测试覆盖初始化服务；完整相关测试 12 passed、1 skipped。
生产仅通过附加 Compose overlay 补挂载，原镜像 ID 与全部环境设置核对一致。
在初始化容器内通过现有 token 调用 Site Access resolve，HTTP 200；未输出凭据。

NBIS Guardian 已打开 incident，但停在源码准备阶段，生产 revision
`f142e55c226898d7bf9cbf66dda3d73685030d88` 不在维护 bare repository。
该 bare clone 没有 `remote.origin.fetch`，默认 fetch 只刷新 HEAD，遗漏后续发布分支。
持久配置 `+refs/heads/*:refs/remotes/origin/*`，fetch 后确认生产 revision 可解析；
远端 incident 本地分支不受该 remote-tracking refspec 覆盖。
维护首轮没有启动 agent / executor / Control 操作，按失败如实关闭并记录 operator_recovery，
release 维护所有权后使用正常 Control resume。没有伪造自动维护成功，也没有重置维护预算。
Guardian 已恢复运行。

## 恢复与验收

通过正式 `RESUME_INITIALIZATION` 恢复原 run：

- COHR `init-cohr-7ba3295fc06b4093a3251b8bf024d3ab`，Control 操作
  `a25951a1140e431ba98befda8bb4fd17`。
- NBIS `init-nbis-1e711329cd9c43b89fdbeea06c16ddbc`，Control 操作
  `c312e00c2b164a6caaa37cb2e8c7dca8`。

两个操作均 SUCCEEDED / INITIALIZATION_RESUMED；原 O4 节点 generation 2、ordinal 1。
06:19 检查两者均 RUNNING / O4_CONFIGURE，Worker 已取得各自 thread / turn，
execution_phase=RUNNING，无 error_code/error_message。冷启动约 14 秒。
COHR 原 544 个、NBIS 原 967 个成功节点的原始 payload SHA-256 全部保持一致，
原 run ID、research_cutoff_at 保持一致。初始化容器 RestartCount=0，租约正常。

验收范围为缺失凭据挂载已修复、原阻塞节点已恢复实际执行；整轮初始化尚未完成。

远端留痕 `/var/lib/doxagent/cohr-nbis-o4-recovery-20261018/`：
before.json（原 run / 失败节点 / 成功节点哈希）、release.json、after.json、部署 overlay。
本地执行脚本与验收快照位于主工作区 `eval/cohr_nbis_o4_recovery_20261018/`。
生产源码及镜像未改动；基础 Compose 修复在独立工作树 `codex/cohr-initialization-recovery` 留存。
