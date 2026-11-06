# 2026-11-06 全部本地开发整合与生产发布

本地产品代码、测试、配置示例、开发文档及冻结回测输入已合入并推送 `origin/main`。最终产品源码为 `942c393660e3a69e97eee5f5bdfe4ad4f3f11f6d`。验收时间：2026-11-06 08:12:34 CST。

## 实际生效范围

- 现有本地 W3 AGENTS.md/skill.md 调整、时间感知 Harness 与回测适配、code-mode companion 配套打包。
- W1 每日 Known Event Index、固定前缀及新增事实桥接。
- O2 Reference Serving 修复、API/MCP/前端契约，以及本地交易恢复提交。
- 合并 origin/main 已有的 Sail、消息决策过期、MCP Tunnel、Reuters 等开发；保留生产有效模型、账号与交易参数。

重建 Backend 和 Web。15 个后端角色使用同一个最终 Backend：codex-worker、API、Control、Initialization、CDECR Executor、O4、Scheduler、Projector、Delivery、Maintenance、Transport Maintenance、Message Collector、Content Enrichment、Message Bus、Trade Executor；Web 更新到本次整合后的前端镜像。

Site Access、Chrome Supervisor、Initialization Guardian 和 MCP Tunnel 的相关改动此前已部署，本次未重启这些独立角色。Web 源码自 `be4be0fe` 构建后未再变化，后续修复只涉及后端，无需再次构建 Web。

| 镜像 | 实际 Image ID |
|---|---|
| Backend | `sha256:2267bdbdb478a75c67a49eab945e4783f4f621b4354cf957f284944880861670` |
| Web | `sha256:625317f956252102dcdf76e1e60cb8b077707eb74089e1c0dba1b0fa4be9ac9a` |

原始 eval/artifacts 审计、导出、运行缓存及调试符号保留本地；凭据不进入 Git。原生运行时通过独立校验产物交付，没有提交二进制。

## 发布中发现并解决的问题

1. 原有生产快速构建路径不复制 W3 原生运行时。补齐 `Dockerfile.source-release`，构建时核对提交中的补丁摘要、主二进制及同版 code-mode host 摘要，仅复制必要产物。
2. 真实激活库均为精简分支，最初 W1 实现依赖本地完整版本历史，10 个库预检均失败。暂停后续切换并保留旧 Scheduler，修复沿现有分支 sidecar 读取历史的兼容性。早期已退役的时钟保留为未知值；只在时间上界证明已过窗口时排除事件，重叠不确定性继续保留。没有伪造时间、恢复大历史库或改写 Event/Fact/激活指针。最终 10 个真实激活库全部通过，合计约 3.4 秒。

每次更新先让模型生产者正常退出、等待 Worker 活动任务归零，再逐项替换服务。更新前以实际容器校正发布台账、私有环境及回滚镜像，防止旧覆盖项退回生产配置。Maintenance 唯一额外继承的凭据为现有 env_file 的 X token，与 Collector 的有效凭据一致；未改变交易账号或模型参数。

## 验证结果

- 16 个目标角色正常运行、restart_count=0；有健康检查的角色均 healthy。
- 15 个后端各核对 98 个变动源码/Prompt文件，共 1470 次摘要比较全部一致；有效构建版本、W3 开关和原生路径一致。
- 认证、Read Context、Operations Overview 三个实际 API 均 HTTP 200。
- 综合回归 550 passed、3 skipped、8 failed；8 项在发布前 `21c8fcb2` 基线上逐项同样失败。没有把该套件描述为全绿。
- 修复后 W1/Runtime/Sail/精简分支/保留版本专项 113 passed；最终原生 SDK/code-mode/时间档位/阶段隔离 3 passed；前端类型检查及 69 项测试通过，生产 Web 构建通过。不同选择集有重叠，不相加为独立测试总数。
- 生产自然流量已形成 COHR、INTC、IONQ、NBIS 的每日冻结记录。
- 两条新 Trade Decision 均 succeeded，原生记录分别出现 `Elapsed: 3s`、`Elapsed: 12s`。
- 三条发布后 Operations 均 succeeded，新增原生时间片段均为 0；尚未退役请求也已核对无时间上下文。已完成请求会按既有机制退役 request，因此原生日志是本次成功 Trade Decision 的直接时间注入证据。

生产配置已启用 `DOXAGENT_W3_TIME_AWARENESS_ENABLED=true`，路径为 `/opt/doxagent/codex-runtime/codex`，同目录包含同版 `codex-code-mode-host`。**Post-Decision Operations 不注入时间提醒。** 无新增硬超时、额外模型调用或交易门槛。

短窗口证明发布、真实注入及阶段隔离；不等于整体耗时改善或长期投研质量验收。未发起测试订单或历史 Case 重放。收据保留发布前旧失败请求的原始日期，不将它们计作此次新发布失败，也未声称历史问题已清零。

公开校验收据：`artifacts/audits/full_main_release_20261106/`。远端私有发布状态与回滚环境：`/var/lib/doxagent/releases/full-main-20261106-r3/`，不提交有效环境。

本报告及收据提交只补充发布记录；运行镜像固定上述产品源码提交。后续如关闭时间能力，先关闭开关，并为已有时间记录的 Threads 保留补丁二进制的历史过滤能力。
