# Ticker 初始化故障修复统一复核与合并 — 2026-10-19

本轮范围为本对话产生的 COHR/NBIS/CEG/ONDS 初始化故障修复及其运行、守护、投影链；只将这些修复并入 origin/main。主工作区其他会话尚未提交的 W3、消息源及存储改动不作为本轮提交来源。合并基线 origin/main=d8a7fee9；合并前已在独立工作树整合该基线，changelog 冲突保留双方记录。

## 逐项复核

| 故障/修复 | 复核判断与本轮处理 |
| --- | --- |
| Data MCP 20 秒握手超时 | 注册 128 工具时重复创建 HTTP/TLS 客户端的实测证据成立。保留首次使用时惰性构建与并发锁，注入 client 合同不变；D3 60 秒/W3 90 秒是基础设施预算，不放宽 required/tool/capability 约束。分阶段启动诊断保留，未无依据继续延长超时。 |
| D3 首个失败使 HTTP client 提前关闭 | 三处并发阶段统一等待已启动兄弟结算后传播原异常，成功 Worker 结果保持可消费；不以重跑整轮恢复。保留该修复。 |
| Repair Agent 认证陈旧与首次非例行维护失败 | 内部部署不改变外部模型发行方 access token 的有效期。Guardian 只分发当前 access/account 快照、保留唯一 refresh-token 所有者和隔离 CODEX_HOME；不撤销身份边界。真实认证 turn 与原初始化续跑已完成验收。外部登录被撤销仍需重新登录，不能宣称永久免维护。 |
| Agent 第 1 轮失败后没有第 2 轮 | 基础设施失败进入 HUMAN_REQUIRED 符合避免重复消耗预算的设计；原修复补上 FAILED round、issue 和同轮 retry-agent。复核发现人工恢复后仍会覆盖旧重试日志/去重掉新 issue，本轮以 recovery 序号隔离日志与 issue ID，不新增业务修复轮或重置节点预算。 |
| NBIS Guardian 获取不到部署源码 | 原生产 bare mirror fetch refspec 调整仅为现场配置。本轮将显式全分支 fetch 写入 GitWorkspaceManager，放在 refs/remotes/origin 命名空间，prune 不碰 incident 自有分支。真实 Git 回归覆盖镜像创建后才出现的新部署分支、源分支退役及 incident worktree 保留。 |
| COHR Supplement 租约失效 | 定向读取单节点、顶层调度过滤、动态计划只读 NodeSpec 三项共同避免反复展开历史 Worker receipt。保留内容完整性、冻结 plan 校验与 fencing；新增节点不绕过依赖图检查。现有回归包含不可用历史 child content 与外置 inputs。 |
| COHR/NBIS O4 缺 Site Access token | 保留初始化容器的 worker secret 挂载。Site Access 全局健康不应成为 D1/D2 启动门槛，本轮将 initializer 的 Compose 依赖改为 service_started；实际 O4 调用仍受服务自身 readiness/鉴权约束，未绕过阶段验收。其他直接使用 Site Access 的服务仍依赖 healthy。 |
| 已完成但前端仍 INITIALIZING | progress 聚合只读所需状态/时间/质量字段，实际需要的外置字段继续校验；当前生产 CEG 控制与前端一致。保留修复，未直接篡改旧页面快照。 |
| ENOSPC 与回执丢失 | 之前仅对已确认终态 session/schema 的 CEG turn 补结算，未重跑未知 turn。磁盘清理/无损压缩与预留调整均保留验收记录；这些是应急措施，长期治理由另一会话实施，本轮不重复更改其部署/数据。 |
| CEG 模式修正 | 原初始化完成后正式 START/REUSE_ACTIVE 成功；当前 PAPER_TRADING、analysis_allowed=true、bus/runtime ACK 和前端有效模式一致。本轮不重复 START。 |

## 当前生产与临时分支边界

实时取证文件位于主工作区 `eval/initialization_final_review_20261019/production-before.json`。COHR/NBIS/CEG/ONDS 及其他已完成正式初始化均 SUCCEEDED；SNOW 原初始化 `init-snow-b14da3f619f84766995e3ec0a6862928` 在 D3 推进，实际模型活动、租约正常，无新的活动 repair incident。保留所有成功节点、cutoff、在途 Worker 和模式设置。

本轮只处理本对话创建的远端 cohr 临时分支及两个已结束 incident 分支，其他会话的 storage/site/W3/GPT6 分支不属于本轮。SNOW 的持久化执行版本仍引用 f142e55c，投影服务环境仍声明 00983e64；相应 `codex/cohr-initialization-lease-recovery`、`codex/cohr-nbis-projection-recovery` 保留。只退役确认不再被运行版本、在途节点或 Guardian incident 使用的其他本轮临时分支。删除前保存精确 ref->commit 与 Git bundle，不删除业务数据、镜像或在途工作树。

## 代码审查和验证

代码审查已完成；main 合并及逐分支删除回执在后文补录。新增优化首先进入主线；本轮不替换 SNOW 正在使用的初始化/Worker/投影容器。生产镜像已叠加另一会话的存储修复，不能用旧基线整体重建替换它们。

代码审查覆盖本分支相对 origin/main 的全部生产差异：请求/作业身份和恢复边界、并发失败与取消传播、认证快照刷新与账号一致性、Guardian 轮次/预算、原生内容完整性与租约 fencing、Compose 鉴权挂载和前端最终状态。发现的 fetch/重试日志问题已修复；没有遗留的高优先级合并阻碍。4 处既有长行仅换行，使本次涉及的 Python 文件 Ruff 全部通过。首次定向组 41 passed / 1 平台 skip，SDK/资源预算组 93 passed；更大范围结果见最终验收补录。

生产特征复核：当前 initializer 已有三项定向读取及 settle_parallel；Worker 已有惰性 client 与 D3 启动预算；Guardian 已有 external_auth_source，并由后续部署包含显式全分支 fetch。本分支现在也持久包含该 fetch 修复，避免只靠生产配置。新的 recovery 日志命名改进尚未部署，本轮不为审计优化重启 SNOW 相关服务，后续从合并后的主线发布生效。


扩大回归的首次结果为 124 passed / 3 failed：两项旧 fixture 导入在干净 origin/main 上同样失败，已改为从实际生产模块导入；D1 旧断言还把当前 7 节点 DAG 当作 5 节点，已更新为七个节点各一次，并保持 assemble=1/publish=2 的恢复验收。修正后的 D1 用例已通过，D3 旧入口恢复用例也通过。另一次 MemoryError 的 D3 managed publication 用例独立复测通过（27.45 秒）；未据此修改生产业务逻辑。最终结果见下。

部署追溯边界：后续存储发布的镜像标签与部分保留下来的 DOXAGENT_BUILD_COMMIT/DOXAGENT_SOURCE_SHA256 环境声明不同，已分别采集，未将任一旧环境字段冒充当前容器完整代码身份。Guardian 获取当前部署源仍按容器镜像标签和源 hash 校验；SNOW 历史节点 f142e55c 与后续 fe4a768b 的执行记录均保留。分支退役取镜像声明/环境声明/在途持久记录的保护并集，因此保守保留两个仍有运行链引用的本轮 refs。其他会话的部署身份整合不在本轮整体重建范围内。

认证兼容性补齐：复核旧 incident 发现，在配置 external login 后，先前复制进 codex-home 的 auth.json 仍可能含 refresh token。新 `_start_agent` 在挂载 Agent 之前仅将这类旧文件原子移到 incident/retired-auth（Agent 挂载之外，私有权限），保留原件、不动 /data/codex-home 的登录所有者；不隔离当前 SDK 无 refresh token 的 external-token 状态。新增旧事件、原件保留、挂载边界和重复调用回归，认证/Guardian/Agent 组 17 passed，Ruff 通过。此兼容性改进随主线下次发布生效，不重新开启已 CANCELLED 的事件。


最终测试验收：扩大组 270 passed / 2 failed / 1 skipped。两项失败分别为该批已加载的旧 D1 节点计数断言、以及既存 startup 集成 fixture 的 D2 provenance 不一致（D2 绑定 d1-mu，却激活 d1-new）；后一项也已在干净 origin/main 复现。修正测试导入/计数和 fixture 引用，不放宽生产 provenance 校验。最后针对两项失败及所有新增代码运行的收尾组为 **39 passed / 1 平台 skip**；SDK/Worker 资源预算组 **93 passed**，所有涉及 Python 文件 Ruff 与 git diff --check 通过。此前 MemoryError 在单例和第二次扩大组均未复现。上述测试组有重叠，不将它们相加冒充独立用例数。

最终代码审查没有遗留的合并阻碍。新增生产改动仅为 Guardian 源码获取、重试审计、旧认证副本隔离和 initializer 的启动依赖；没有改变成功节点、研究质量门槛、cutoff、预算、交易绑定或激活 provenance 合同。

## 合并与退役验收（2026-10-19 04:43–04:45 CST）

本分支在整合其他会话新提交后已于 `fe87e94e` 合入并推送 origin/main，随后 main 的 `9de580c9` 已确认包含该提交。基础代码审查、测试修正和新增优化均已进入主线；追加整合后的 Site Access/O4 联合回归 21 passed / 1 平台 skip。新增认证隔离/审计命名改进未借机替换生产容器，后续从主线发布；SNOW 在途初始化保持不动。

主工作区只对 21 个有重叠的未提交路径做了有记录的临时保存和恢复；SDK 中未提交的 W3 改动经规范化 hash 核对与合并前一致，原有 changelog 所有段落保留，Git index 恢复原未暂存状态，无未解决冲突。其余会话继续写入的文件保留现场；可恢复 stash OID 与原始补丁保存在主工作区 eval/initialization_final_review_20261019，不把他人未提交内容纳入本轮 main 提交。

生产 /home/ubuntu/doxagent 的 main 已快进同步。已删除本对话构造且无在途引用的 5 个部署分支：

- codex/cohr-initialization-recovery
- codex/cohr-recovery-agent
- codex/cohr-recovery-guardian
- codex/cohr-recovery-initialization
- codex/cohr-recovery-worker

另外删除 Guardian bare repository 中两个已 CANCELLED incident 的 codex/init-repair/repair-b0356020858e4d9ab620508917be81fd 与 codex/init-repair/repair-c814d8dc0f6a405c989b4e49f8dea1a4 分支；仅通过 Git 原子解除工作树 HEAD 的符号引用，完整保留取证文件及原索引状态，未执行 reset/目录删除。此前创建分支的精确 refs/commit 和经 git bundle verify 的 deployment.bundle / incident.bundle 保存在生产 /var/lib/doxagent/initialization-final-review-20261019（分别约 8 KB / 3 KB），足以恢复专有提交，不制造完整仓库副本。

保留 codex/cohr-initialization-lease-recovery 和 codex/cohr-nbis-projection-recovery：SNOW 在途执行链仍引用 f142e55c，运行中投影服务仍声明 00983e64。其他会话的 storage/site/GPT6 等分支未动。GitHub origin 上本来没有本对话的 cohr 临时分支；清理对象为生产 checkout 与 Guardian bare repository 中的 refs。

最终实测 SNOW 同一个 run/cutoff 继续 RUNNING/D3，state_seq 从 1122 推进到 1134，Worker 模型会话持续写入至 04:43:34 CST；COHR/NBIS/CEG/ONDS 保持 SUCCEEDED，CEG PAPER_TRADING。没有新建初始化、重置预算或重启服务。完整 before/after、删除 CAS 与 bundle hash 回执均在主工作区 eval/initialization_final_review_20261019。
