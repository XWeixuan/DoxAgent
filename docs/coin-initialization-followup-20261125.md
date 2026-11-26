# COIN 初始化后续检查与原 run 恢复 — 2026-11-25

## 已执行

- 02:52（北京时间）首次检查：O2 第一次 1800 秒超时后已自动重试，第二次仍有 SDK 检索进展。Read 四路追平。
- 03:04 加查：两次 O2 均 CODEX_TURN_TIMEOUT，原 run 因重试耗尽 FAILED，Guardian 已接管但自身工具沙箱无法启动。
- 修复 Guardian 临时 Agent 的 namespace/mount 策略，完整 bubblewrap 探针通过。Agent 在原轮次内产出候选，没有新建业务初始化。
- 候选将原先一次处理 850 条 Delta 的 O2 survey 拆为 15 个带独立回执的有界调查分段，再进行全局综合和完整覆盖校验。单段最大估计 17,827 tokens。两次超时与未分段工作负载存在不匹配；没有证据将精确 SDK 等待位置认定为死锁。
- 独立复现并修复 resume 误重置已成功父节点的问题：历史失败子节点若已有成功的 manager，其 FAILED 诊断保留，不参与恢复选择、不重新派发父节点，也不计作新的修复预算消耗。旧 repair round 的原始 targets 证据保留，执行与记账按实际待修目标过滤。
- 修复了 Guardian Git status 首行前导空格被 strip() 去除，造成 changelog 被误识别为 hangelog 的问题。随后用户明确要求无需 Guardian，本轮停止使用它。

## Guardian 已停用

生产容器 doxagent-v2-initialization-guardian-1 已停止，Docker restart=no；Compose current overlay 及仓库配置设置 manual-initialization-guardian profile，默认 up 不再启用它。保留 incident、worktree、候选 commit 1559536b 和全部诊断记录。未停止 Codex Worker、采集或正常调度服务。

通过 RepairRepository.release() 将 incident repair-fdba07bc50324fdba2b9bde2d1b95e55 的自动恢复占用释放为 CANCELLED，原因明确记录为用户关闭 Guardian、操作员接管。这不会取消原初始化 run。

## 正式断点续跑

03:48:23 经 ControlRepository.submit() 使用 revision CAS 与独立幂等 key 提交 RESUME_INITIALIZATION，operation=b6d30748d1bf4f82848f97b937c0c972。原 run 为 init-coin-0a7ab32b3e9d4b2db5dae780a52dfe03，原 cutoff=2026-11-24T14:35:02.927898Z。

03:48:29 已进入 o2.o2-survey-part-001；原先 3109 个 SUCCEEDED 节点的完整 payload SHA-256 全部相同，D1/CDECR 未重跑，cutoff 不变。历史 FIELD ImmutableRecordConflict 仍作为 FAILED 子节点保留，未伪造成功。

03:49:55 核验 Worker job=8ef314e2cd0cf3a63d19ad83506fd88b6286b0e37adc0444e1a4a8b5e1b46b74，RUNNING、无 wait_reason，SDK 8 条事件，最近更新时间 03:49:51。四路 Read head/checkpoint 相等、error=null。完整初始化及激活仍在进行中，不以外层 RUNNING 代替完成。

实际拥有恢复执行逻辑的 Control 和 Initialization 均已部署补丁：doxagent-control:coin-preserve-20261125、doxagent-init:coin-bounded-20261125。只在没有其他 RUNNING/QUEUED 初始化时切换角色，未重启 Worker。Guardian 与初始化基础镜像存在模块版本差异，Guardian 临时部署阶段已回到自身原模块仅应用必要补丁，未继续依赖该服务；运行中的 Control/Initialization 保留各自依赖环境。Compose overlay 保留前端投影修复及其他服务设置。

## 验证与证据

- Guardian 独立验证容器：111 passed（非 Agent 工具沙箱，未使用其 selector polling workaround）。
- 本地合并后的关键回归：39 passed，另 Git status 路径回归 1 passed。
- 原成功节点哈希基线、恢复验收、Agent 报告和独立测试日志：../artifacts/audits/coin_hourly_20261125/。
- 远端回滚配置与代码备份：/home/ubuntu/coin-monitor-repair-20261125/；运行镜像选择持久化于 /var/lib/doxagent/releases/compose.current.json。

本轮没有创建后台每小时自动化。当前检查与必要恢复在本聊天内执行。

## 05:13 唤醒检查与 Survey 验收修复

按一小时 timer 休眠后检查，发现 O2 于 04:35 停止；两次 part-001 Worker 均成功，业务节点却被 DateResolutionLedgerEntry 的正式 Event/Fact ID 校验拒收。调查阶段尚未分配这些 ID，属于阶段契约误用。

修复只改变 Survey 草稿验收：保留字段类型、日期语义与完整 Delta 覆盖校验，正式发布仍使用原强校验。增加对旧成功 Worker dispatch 的准确 lookup（Job ID/request SHA/run/attempt/cutoff）及冻结 projection/现有产物重验，验收成功后接续 durable 节点，不再重复调查。

18 项针对性测试通过。只读挂载生产数据验证：原 part-001 的 63 条日期记录通过调查阶段校验；匹配到已有成功 job d2346c12a86f1d8356f5a3984c1afa485bc20c840a80f12bc646d9ec57f3d380，冻结 projection 一致、可恢复。

仅部署 Initialization 镜像 doxagent-init:coin-survey-validation-20261125（sha256:4f2850ac6fc45e28c69027190b1481732b79224ef040b5237bd0bcd4d9f5be89），通过 CAS/幂等 Control operation 42a93e14badc4c1bbc5ab03a74e3a509 恢复同一 run。05:20:38 part-001 已验收成功，05:20:39 进入 part-002；part-001 Worker 调用总数仍为原来的 2，没有新增调用，原 3109 个成功节点哈希均未变。Guardian 持续关闭。

验收：artifacts/audits/coin_hourly_20261125/survey-date-repair-acceptance.txt；只读验证程序：validate_survey_recovery.py。远端备份 /home/ubuntu/coin-survey-validation-20261125/。继续每次检查后 timer 休眠一小时，中间不探测远端。

## 15:27 里程碑复查

O2 的 15 个调查分段、15 个重建批次及全局协调均已完成，业务节点 SUCCEEDED，产物 Event Library v1。独立只读核验 events/US/COIN/event_library.sqlite3：library_heads.published_version=1 且 library_versions 的 COIN/v1 记录存在。D2 已启动，O0 候选成功并进入 synthesis，真实 Worker run d2ws-12aae09fcbfa930fbc4ab10a5ed5c27d 有近期 SDK 活动。原 3109 个成功节点 payload SHA 仍无变化。继续按小时 timer 复查，Guardian 不启用。

## 最终终态核验与监控结束

北京时间 2026-11-25 16:30:03，原 run 为 SUCCEEDED / VERIFY_READY；COIN 已激活，Control 为 RUNNING / LIVE_TRADING，initialization_incomplete=false、initialization_failed=false，Bus/Runtime 均确认相同 activation revision。原 3109 个成功节点 payload SHA 全部未变，cutoff 不变；全程没有重新创建整轮初始化。最终验收见 artifacts/audits/coin_hourly_20261125/final-acceptance.json。

**必须区分执行完成与研究质量完整。** D2 发布为 PARTIAL（含 62 项质量标注），D3 当前发布策略数为 0。D3 阶段观测到多次 gpt-6.1-sol HTTP 429，系统随后通过既有降级路径完成；不能据此称全部研究正常或无降级。账户查询未显示普通使用被禁止，使用率约 49%；按生产二进制/同模型/medium 发起的单次最小只读探针返回 {"ok":true}。这仅证明探针当时可用，未确定 429 的精确上游原因，也没有补齐 D2/D3 研究质量。

本次具体初始化已终态，当前聊天的一小时 timer 循环结束。没有创建后台自动化；Guardian 仍停止、restart=no、manual-only profile。未为消除质量标记而修改已经发布的研究或整轮重跑。
