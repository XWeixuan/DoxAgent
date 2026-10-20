# W3 Policy RELATED / HIT：6.1 Sol medium 发布验收

## 已发布行为

新冻结的 W3 执行 profile 按 W2 的实际结果选择：`policy_hit` 或 `policy_related` 为真，均使用 **gpt-6.1-sol / medium**；普通 OPEN 继续 **gpt-6-luna / max**。公开 Template、Case Fork、交易研判与后续操作沿用同一个冻结 profile。旧 Case 的既有冻结 profile 不改写，不补跑已完成任务。

W3 的 SDK 配置显式指定 `service_tier=default`，暂不开 Fast。同步对齐公共预热的 Sol 型号、Worker 就绪检查及 Sol 库存容量；兼容旧冻结 gpt-6-sol profile 使用原 Sol 容量，避免误归入 Luna 库存。本轮没有修改 Prompt/Skill。

## Git、部署与验证

- 代码提交：`160199b7a2009ad59c2b80b5d5941e9a96d66383`，已推送 `origin/main`。
- 使用远端精确提交构建：`doxagent-v2:storage-160199b7a200`，镜像 `sha256:8a72916bedc0c9210e784d341d24ed9632c588be90cbff5f80c16e8fb91192ef`。
- 通过现有 canonical release owner 仅替换 `codex-worker` 和 `v2-scheduler`，两者 release/image 一致。切换于北京时间 **2026-10-20 21:22:07** 完成，Worker Docker health 为 healthy。
- 切换前临时停止模型任务受理方，等待现有 Worker 队列为空及受理方退出，再关闭 Worker。未强制取消模型 turn；临时停止的其他生产服务已恢复，其容器 ID 未改变。
- 定向回归 **84 passed**：44 项覆盖模型冻结、三阶段模型一致性、Sol 库存、权限及离线评估，40 项覆盖 W3 编排、生命周期、Writer 交接和持久化执行。
- 生产五个相关源码文件 SHA256 与已提交内容全部相符；生产函数烟测确认 HIT/RELATED→6.1 Sol medium、OPEN→Luna max。
- 独立 SDK 空 Thread 检查确认 `service_tier=default` 被实际 SDK 接受，未发起模型 turn。

## 自然流量确认

自然到达的 NBIS Case `case_a15841b16f7948f89c518fa2cddeb878`，W2 为 RELATED（非 HIT），冻结 profile 为 `gpt-6.1-sol/medium`。

- 实际交易研判 job：`7333a9d6ec32c14a748d8e02db332c272671e5b68e08312b16fa64b7cc0248c1`。
- Thread：`01a15981-e9ad-7bb1-b126-3aeb70413650`。
- Turn：`01a15986-e1df-7b62-8b5a-837d9b5cfa1d`。
- 原生 `turn_context` 确认 **gpt-6.1-sol / medium**，同 Thread 的原生 settings 确认 **service_tier=default**。该实际 turn 于北京时间约 **21:35:48** 启动，验收时仍在正常研判，未等待或预设其交易结果。

这证明新路由贯通到真实生产 SDK 调用，不仅是静态配置检查。没有人工派发测试消息或为验证重放历史 Case。HIT 的选择分支由生产函数烟测与回归确认，本次未声称采得新的 HIT 自然 turn。

## 上线期间发现的预热可靠性问题

**路由发布已确认生效，但不能据此宣称整个 W3 预热已健康或时效达标。** 发布后的冷启动出现一次公共 Sol Template `CODEX_START_TIMEOUT`，以及 Fork 的 `FORK_SLOW/RECONCILING` 重试。后续 Sol 库存自动恢复到 READY，并被上述自然 Case 成功使用。

有两条自然 Case 在进入模型前准备失败，已保留原始失败，没有为美化验收而重放或修改状态：

| Case | 新冻结 profile | 失败 |
|---|---|---|
| case_c9378c7d671c41c5b0a3ada8f2262806 | 6.1 Sol medium / RELATED | trade_decision/PREPARE: ready-slot preparation failed: TimeoutError |
| case_9d792e29998d4290ae7bc112cb63da12 | Luna max / OPEN | 同上 |

就绪采样时 `/v1/w3/readiness` 仍为 false：Sol 有一份 READY 库存，Luna 为0且仍在准备；之后 Sol 槽被真实 Case 占用，Luna 仍有补充/接回操作。该问题同时涉及两种模型，现有证据指向 SDK/Fork 准备与恢复，不能归因为 Sol 推理，也不能仅凭本轮记录断言已经查清所有根因。代码路由变更没有包含一轮额外的 Fork 可靠性修复；本轮不修改交易业务逻辑、不改写失败 Case。

## 证据

原始记录位于 `eval/w3_policy_sol_release_20261020/`：`before.json`、`build.json`、`deploy.jsonl`、`acceptance-first.json`、`acceptance.json`、`sdk-config-acceptance.json`、`warm-status-final.json`、`case-acceptance.json` 和 `verification.json`。`verification.json` 将路由确认与全局预热就绪状态分别记录，避免把容器 healthy 等同于业务全就绪。
