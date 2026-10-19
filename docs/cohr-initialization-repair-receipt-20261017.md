# COHR 修复与原节点续跑回执

日期：2026-10-17。北京时间 19:00 后已确认原阻塞节点成功，D3 转入 OPEN_DISCOVERY。

## 结果与边界

- 原初始化仍为 `init-cohr-7ba3295fc06b4093a3251b8bf024d3ab`。
- 原 cutoff 仍为 `2026-10-16T22:02:56.508969Z`，创建时间不变。
- 原来 412 个 SUCCEEDED 节点的持久 payload 逐字比对全部未变。
- S0001/S0002/S0004 原成功 job 的请求、创建时间和结果 payload 全部未变；没有重新执行模型研究。
- S0003 原 job `fac614d1cfbfdc05e068e7f1238aeb2c7831f5dc7f0b82758944613a7ba5de73`
  保留原请求、attempt id、owner workspace、idempotency identity，经有审计的启动预算恢复后
  于北京时间 **19:00:29 成功完成**，infra_recovery_count=0，error_code=null。
- 父初始化已接纳四个 Shell 结果，state_seq 从 884 推进至 899；当前 RUNNING / D3，
  D3 阶段 OPEN_DISCOVERY，OPEN_EVENT 和 OPEN_RESEARCH 正在正常执行。
- 本回执确认故障修复部署、原阻塞节点成功和父流程续跑；**不代表整轮初始化已完成**。
  后续 Planning/Build/Integration、O4 和 activation 尚未在本回执时验收。

## Data MCP 为什么会超过 20 秒

静态阅读发现 Data MCP 在启动时构建完整工具注册表；原 BaseRealToolClient 构造函数
为尚未调用的每个 HTTP provider 提前建立 HTTPX client，重复创建 SSL context/加载 CA。
远端单进程剖析：128 个注册工具，59 个 HTTP client，59 次 SSL context 构建；
registry 2579ms，其中 SSL 2508ms。仅注册工具不需要这些连接资源。

同一远端四路真实 stdio initialize/list_tools 测量：

| 测量 | 四路握手耗时 | 暴露工具数 |
|---|---|---|
| 原生产代码 | 23.06 / 22.13 / 20.88 / 22.34 秒 | 每路 87 |
| 延迟 HTTP client 构建后 | 15.47 / 15.28 / 14.52 / 13.56 秒 | 每路 87 |

修复后平均约 14.71 秒，原平均约 22.10 秒，改善约 33%。并发启动下重现了原 20 秒预算不足，
并通过去掉无用连接初始化获得改善。历史当时的全部 CPU/IO 争用细节仍无法倒推，
这里没有将当前实验伪称为历史逐指令测量。

冷启动仍需加载 Python 模块、工具类与 MCP 协议栈，因此保留并发、优化无用开销，
同时把 D3 required Data MCP 启动预算从 20 秒调整至 60 秒。单工具期限、模型执行期限和
required/工具权限条件保持原契约。W3 仍为 90 秒。
新增按 attempt 落盘的启动诊断：module_import_ms/application_ms/server_build_ms/bootstrap_ms。
本次 S0003 实际 bootstrap 为 **13171.6ms**，其中模块导入 7151.2ms、应用构建 5997.8ms；
capsule 总冷启动约 30618ms，不能将它与 MCP 握手本身混为一谈。

HTTP client 现在仅在 provider 首次真正发请求时创建，并发首次访问由 provider 自身的锁保证
只创建一次；原注入 client 和后续连接复用保持不变。不新增 ticker 并发限制。

## token 的期限是否合理

需要区分三个层次：

1. 本次 401 来自访问外部模型服务的 ChatGPT-managed 登录。access token 是发行方的短期身份凭据；
   内部机器是否可信，不能改变外部服务的认证、账户与授权规则。取消本地检查也不会让过期 token 被发行方接受。
2. Worker HTTP 服务使用内部配置的 bearer secret，本次未发现它过期；它不是这次 routing 401 的来源。
3. Data MCP capability 则绑定 run/node/attempt/ticker/cutoff/工具范围，主要保证任务归属和结果可消费性。
   它的任务期限制与这次外部登录 401 是独立问题，本次没有删除或放宽这些边界。

不合理之处在生命周期管理：旧系统向静态模板和每个 incident 分别复制登录 bundle，
罕用 Repair Agent 继续拿过期副本，生产 Worker 登录已刷新也救不了它；复制 refresh token
还会产生多个潜在刷新所有者。三次重试同一失效凭据不会修复该问题。

生产现在由持久 Worker 登录缓存作为认证来源，Guardian 向每个独立 incident 发布当前
**access_token + account_id** 快照，并在 Agent 运行期间更新。Agent 通过 SDK 0.159.3
的 typed `chatgptAuthTokens` 外部登录协议和 refresh callback 读取快照；不分发 refresh token，
不挂载 Worker CODEX_HOME、MCP 配置或业务数据给 Repair Agent。
已有独立认证模板模式保留给明确单独登录的环境，生产 overlay 使用新的外部凭据来源。

使用新 Agent 镜像进行了真实 thread/turn 验证，模型返回 `OK`；没有使用伪 token 或绕过发行方认证。
外部登录如果被撤销仍可能需要重新登录，系统不能保证永久有效；这次修复消除了静态旧副本和额外刷新所有者。

官方依据：
https://learn.chatgpt.com/docs/auth
https://learn.chatgpt.com/docs/auth/ci-cd-auth

## 其他业务逻辑修复

- D3 Shell Discovery、Open Discovery 和 owner queue 的并发聚合等待全部已启动兄弟结算后再抛原始异常；
  外层适配器随后才关闭 HTTP client，避免一个失败把健康兄弟变成 client closed。
- Repair Agent 基础设施重试耗尽时同步 round 为 FAILED、记录 issue，并按重试次数保存日志；
  不再让已停止 Agent 永远显示 AGENT_RUNNING。
- 新 `retry-agent` 操作只恢复尚未进入候选执行的基础设施失败事件；沿用原 round/thread 和节点预算，
  留存旧重试次数及恢复原因，不把基础设施重试计成新业务 repair round。
- 新 Worker 离线 `recover_startup` 操作用 Worker 自己的 writer lock，只允许已结算、没有 thread/turn 的
  START_THREAD 基础设施失败。原请求不变，旧失败完整落 event，重新开放该原 job 的启动预算。
  不对完成状态未知的模型 turn 重发。

当前 COHR 三个迟到结果经原有受保护输入/快照校验后接受，其 D3 task 保留了旧 transport error，
所以状态为 PARTIAL；已有文件作为 Discovery 输入被正常消费，未被作为 unavailable 丢弃。
这是保留下来的历史故障标记，不是这次又执行失败。S0003 新结算为 COMPLETED。

## 部署与恢复路径

本次分别从每个服务实际部署版本应用窄补丁，不把本地其他未提交修改带入生产：

| 服务 | 原基线 | 新镜像 |
|---|---|---|
| Worker | 3c30365a | doxagent-cohr-recovery:worker-58078285 |
| Initialization | 28053cde | doxagent-cohr-recovery:initialization-7d9f5ac6 |
| Guardian | 618fd9c5 | doxagent-cohr-recovery:guardian-5c4b7dd5 |
| Repair Agent 模板 | 618fd9c5 | doxagent-cohr-recovery:agent-7709d64a |

新镜像 revision/source hash 对应远端 `codex/cohr-recovery-*` 提交，可供后续 Guardian 追溯。
仅在确认无活动初始化/排队或运行 Worker job 后重建前三个服务。其他服务未重启。
保留初始化、Worker jobs、research 三库的一致性备份并完成 quick_check。
远端安全保存的部署 overlay 逐项保留服务原有效环境，只变更镜像/构建身份和 Guardian 认证配置。
该 overlay 含环境秘密，不复制进本地报告或仓库。

本次已由人工完成部署，不再让旧自动 Agent 对同一问题重复编码；旧事件通过正式 release 交还控制权，
round 保留真实 FAILED，incident 为 CANCELLED，并新增 operator_recovery issue，未伪造自动修复成功。
随后提交正式 `RESUME_INITIALIZATION` 操作 `7009d0c9da694ab7b59cfc47c0c54e02`，
由 Control/Initialization Worker 继续原 run，未调用 submit/reinitialize/rerun-block。

## 验证

- 原初始化/Guardian/D3/Data MCP 关联测试：96 passed。
- 新增及认证/修复回归组（有重叠）：24 passed。
- provider、IBKR、Worker 资源、W3 与维护启动组：89 passed，1 项基线失败。
- 基线失败为 `test_real_registry_exposes_strong_tool_descriptors`：既存 `crawler_plane.list`
  descriptor 的 input_fields 为空。已使用修改前 BaseRealToolClient 重现同一失败，未扩展本次修复范围。
- 修改代码的 Ruff 检查通过；真实四路 MCP 握手、实际认证模型 turn、原 job 成功与父阶段推进均有生产证据。

## 本地证据

`eval/cohr_initialization_audit_20261017/` 中包含：
`handshake_before.json`、`handshake_after.json`、`auth_probe_result.json`、`release.json`、
`deployment.json`、`preservation.json`、`resume_status_01.json` 至 `resume_status_05.json`，
以及仅用于本次的采集/构建/部署脚本。原失败证据继续保留。
源码提交位于独立工作树的 `codex/cohr-initialization-recovery`；changelog 已追加记录。
