# DoxAgent Codex SDK Agent Framework 与 Document 1 v2 重构开发方案

> **历史基线提醒（2026-08-20）：** 本文记录 `codex_d1_v2` 的原始迁移设计，继续用于
> legacy reader、历史 run 与兼容测试，不再是新研究编排的命名/DAG 权威入口。当前新架构
> 见 [`global_market_research_lane_rearchitecture_plan_20260820.md`](./global_market_research_lane_rearchitecture_plan_20260820.md)：
> Global Research 为 `C4 pre-scan → C1/C3 → C5 → C4 enrichment`；独立 Market
> Situation 为 `C2/O4`。事件库强隔离本轮明确延期。

> 状态：需求确认后的可执行开发方案  
> 日期：2026-08-08  
> 目标版本：`workflow_version=codex_d1_v2`  
> 本文件只定义开发方案；当前轮次不实施代码、配置、数据库或远端部署变更。

---

# 一、已经冻结的决策

本方案以下列已确认决策为前提，后续开发不得重新模糊这些边界。

1. **Codex SDK 直连是固定技术目标。** 不将主方案替换为 Agents SDK，也不把旧 ReAct Runtime 嵌入 Codex Thread。
2. **本轮只详细设计和实施 Document 1 v2。** Document 2 只冻结接入接口和非回归约束，待 Document 1 v2 稳定后另写迁移方案。
3. **Legacy Framework 保持可独立运行。** 旧 Run Type、旧 Workflow、旧 API 行为和已有前端兼容性不变；允许新增 v2 表、字段、Adapter、API payload 和前端分支。
4. **运行时使用 Python `AsyncCodex`。** SDK、其 pinned Codex CLI runtime、生产 `CODEX_HOME` 和开发 Codex App 环境相互隔离。
5. **Thread 只属于一个明确的 Run 和逻辑 Agent。** 唯一键为：

   ```text
   workflow_version + ticker + run_id + agent_role
   ```

   同一 Run 内可以从 Document 1 延续到未来 Document 2；不得跨 Run 或跨 Ticker 继承隐含 Thread History。
6. **业务数据不足可以降级，执行边界损坏必须阻塞。** 指标缺失、可选研究不足和 Sub-agent 失败属于软降级；身份错配、越界写入、正式输出契约失败和持久化失败属于硬阻塞。
7. **优先采用独立 `codex-worker` 服务。** 只有远端可行性门槛无法满足时，才允许回退到同容器运行并用 Hook、操作系统权限和运行后文件审计共同约束 Workspace 外写操作。
8. **开发、研究质量评测和运行表现评测是三个独立工作流。** 开发任务只要求功能、契约、恢复和远端运行路径成立；研究质量与成本/延迟评测在 Workflow 跑通后分别开展。

---

# 二、目标与非目标

## 1. 本轮目标

在完整保留旧 DoxAgent 的前提下，新增一条可以显式选择的 Codex SDK Document 1 v2 路径：

```text
DoxAgent
├── Legacy Agent Framework
│   ├── Custom ReAct Runtime
│   ├── Legacy Document 1 / 2 Workflow
│   └── 原有生产路径继续可用
│
└── Codex Agent Framework
    ├── Codex D1 Orchestrator
    ├── Independent codex-worker
    ├── Python AsyncCodex + pinned Codex CLI
    ├── Versioned Agent Bundle
    ├── Attempt-scoped Context Workspace
    ├── Data MCP + Source Capture MCP
    ├── Document 1 v2 Workflow
    └── v2 Result / API / Frontend Adapter
```

Document 1 v2 必须能够完整执行：

```text
Run 初始化
→ C4 实体地图与前置未来节点
→ PROGRAM Horizontal Collection
→ C1 / C2 / C3 / O4-B
→ AGENT Observation 归一
→ C4 研究后未来节点补充
→ O4-A
→ Document 1 v2 统合与发布
```

## 2. 本轮非目标

本轮不做：

- 替换、删除或逐步侵入旧 ReAct Runtime；
- 修改 Legacy Workflow 的节点顺序或输出语义；
- 实施 Document 2 v2；
- 将 v2 自动设为默认生产路径；
- 让前端直接读取 Codex Workspace、Thread 或 MCP 内部状态；
- 为 Codex 重写已有 provider connector、semantic tool 或数据标准化逻辑；
- 把全部研究正文塞入单个大 JSON；
- 把 Thread History、Citation、Blackboard 和正式 Workflow State 混成同一对象；
- 在基础开发任务中同时追求研究质量优化和运行成本优化。

---

# 三、必须正视的技术边界

## 1. Codex SDK 的产品定位偏差

OpenAI 当前把 Codex SDK 主要定位为 coding-focused Codex threads；本方案把它用于金融研究 Agent，属于有意偏离官方主要使用路径。开发时必须以真实 spike 和端到端契约验证为依据，不能只因 SDK 可以启动 Thread 就宣称架构可用。

官方参考：

- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
- [Codex Python SDK API](https://github.com/openai/codex/blob/main/sdk/python/docs/api-reference.md)

## 2. Python SDK 仍是 beta

必须：

- 精确 pin `openai-codex` 版本；
- 使用该版本发布包自带的 pinned CLI runtime；
- 在运行审计中记录 SDK、CLI、模型和配置版本；
- 升级 SDK/CLI 时先运行兼容矩阵，不允许镜像构建时自动漂移到新的 beta 版本。

## 3. DeepSeek V4 Flash 可作为 Codex custom provider fallback

DeepSeek 官方已确认 `deepseek-v4-flash` 支持 Codex，并原生支持 Codex 使用的 Responses API 格式。它可以通过独立 `CODEX_HOME` 中的 custom provider 配置直接接入：`base_url = "https://api.deepseek.com/"`、`wire_api = "responses"`，无需建设 Responses compatibility bridge。

该能力只作为 fallback：OpenAI 始终是 P0 主路径并优先尝试；仅当 OpenAI 模型不可用、鉴权不稳定、条款不适用或额度受限时，才为新的 attempt 启用 DeepSeek profile。Phase 0 仍须针对精确 pin 的 Codex CLI/SDK 版本验证模型目录、流式响应、structured output、tool/MCP 与多 Turn 行为。

官方参考：

- [DeepSeek：在 Codex 中使用 DeepSeek V4](https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/codex)
- [Codex custom model providers](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers)

## 4. Docker 内层 sandbox 不是天然可用

Codex 在 Linux 上依赖 `bwrap` 与 `seccomp`。Docker 容器可能阻止 user namespace、setuid `bwrap` 或 seccomp 操作。因此独立 worker 必须先运行 sandbox preflight；不得假定 `Sandbox.workspace_write` 已经形成真实隔离。

若内层 sandbox 无法启用，优先让独立 worker 的外层容器成为隔离边界，并确保它不挂载源码、其他 Run 或宿主机宽泛目录。只有独立 worker 仍不能满足文件和恢复契约时，才进入本方案定义的回退路径。

---

# 四、总体组件边界

## 1. Codex D1 Orchestrator

Orchestrator 是 DoxAgent 内部的新业务编排层，负责：

- v2 Run 和 checkpoint 生命周期；
- Document 1 v2 DAG；
- 节点依赖与并行 join；
- Thread Registry；
- Context Snapshot 选择；
- Node Attempt 创建、重试、取消和恢复；
- Workspace 准备与正式 Artifact 发布；
- MCP capability 生成；
- Structured Completion 校验；
- Citation Manifest 派生和非阻塞审计；
- 结果持久化和 v2 Adapter；
- API/SSE 状态同步。

Orchestrator 不负责：

- Plan/Action/ReAct 循环；
- 决定 Agent 下一步工具调用；
- 拼接大段动态 Prompt；
- 模拟 Codex 内部 Sub-agent；
- 直接替 Agent 生成研究正文。

## 2. codex-worker

`codex-worker` 是独立的内部服务，负责：

- 管理 `AsyncCodex` app-server 生命周期；
- 使用指定 `CODEX_HOME` 启动和恢复 Thread；
- 为每个 Turn 设置 `cwd`、model、effort、sandbox 和 output schema；
- 流式转发 SDK event；
- 执行 interrupt；
- 管理 Workspace 文件；
- 运行本地 stdio Data MCP / Source Capture MCP；
- 返回 TurnResult、usage、items、artifact inventory 和错误分类。

worker 不拥有正式 Workflow State，不决定下一个业务节点，也不能直接把产物写入 Legacy Blackboard stable document bucket。

## 3. Workspace Control Plane

Workspace Control Plane 是 worker 对 Orchestrator 暴露的内部接口。它解决独立容器和远端部署中的文件生命周期，不要求主 API 直接访问 worker 临时文件系统。

它包含：

- `WorkspaceStore` 抽象；
- 内部 Workspace HTTP API；
- 同机 Docker named volume 实现；
- 后续对象存储实现扩展点；
- path containment、checksum、atomic publish 和 retention cleanup。

## 4. Data MCP

Data MCP 只负责把已有数据工具安全暴露给 Codex：

```text
Codex Agent
→ Data MCP
→ Existing semantic tools
→ Existing provider clients
→ Existing normalization / result assembly
```

它不允许模型获得 provider secret，不允许模型自行选择未授权 provider，也不允许模型直接写 StateValue。

## 5. Source Capture MCP

Source Capture MCP 只负责将 Data MCP 之外的网页、文件或外部材料纳入 Observation 体系并返回当前 attempt 的 `O#`。它不负责登记 Citation、发布 Artifact、维护 Memory 或阻塞 Workflow。

最小请求只包含 `url`、简短 `source` 和 `note`；标题、时间、checksum 等由系统补充。成功返回 `{"alias":"O17"}`，失败返回 `{"alias":null,"warning":"source_capture_failed"}`，Agent 继续执行。

## 6. Result Adapter

Result Adapter 将 v2 Artifact Bundle 转为：

- v2 数据库存储；
- v2 API payload；
- 旧前端仍可理解的兼容投影；
- 新前端 v2 展示分支。

兼容投影不能把缺失字段伪造为旧版结果，也不能把 O4-A 混入旧 `market_trace_report` 后冒充 Legacy Document 1。

---

# 五、独立 codex-worker 的远端可行方案

## 1. 推荐部署拓扑

远端 `doxagent-hk` 仍以 `/root/doxagent` 为部署入口，在 Compose 中新增内部服务：

```text
dashboard / runtime-scheduler
        │
        │ internal Docker network
        ▼
codex-worker:8790
        ├── AsyncCodex
        ├── isolated CODEX_HOME volume
        ├── workspace volume
        ├── Data MCP stdio process
        └── Source Capture MCP stdio process
```

要求：

- worker 端口不映射到公网；
- worker final image 使用 multi-stage build，只安装 wheel、运行依赖、版本化 Agent Bundle 和 MCP package，不复制可编辑的 DoxAgent 源码 checkout；
- `CODEX_HOME` 与 Workspace 使用两个独立持久化 volume；
- worker 不挂载 `/root/doxagent`、Docker socket、SSH key、开发 Codex home 或其他项目目录；
- Orchestrator 通过内部鉴权调用 worker；
- 重建 worker image 不删除 auth/session/workspace volume；
- 正式发布 Artifact 进入 DoxAgent 持久化层后，Workspace 才按 retention 清理。

## 2. Worker Job API

建议的内部控制接口：

```text
POST   /internal/v1/jobs
GET    /internal/v1/jobs/{job_id}
GET    /internal/v1/jobs/{job_id}/events       # SSE
POST   /internal/v1/jobs/{job_id}/interrupt
POST   /internal/v1/threads/{thread_id}/resume
GET    /internal/v1/health
GET    /internal/v1/capabilities
```

`POST /jobs` 至少接收：

```yaml
run_id: string
ticker: string
workflow_version: codex_d1_v2
node_id: string
attempt_id: string
agent_role: string
thread_id: string | null
workspace_id: string
task_text: string
output_schema: object
model_profile: string
timeout_seconds: integer
capability_token: string
```

API 只接受 Artifact/Workspace ID，不接受任意宿主机绝对路径。

## 3. Workspace File API

为保证远端可维护性，worker 必须提供：

```text
POST   /internal/v1/workspaces
POST   /internal/v1/workspaces/{id}/attempts
GET    /internal/v1/workspaces/{id}/files
GET    /internal/v1/workspaces/{id}/files/{relative_path}
PUT    /internal/v1/workspaces/{id}/files/{relative_path}
DELETE /internal/v1/workspaces/{id}/attempts/{attempt_id}
POST   /internal/v1/workspaces/{id}/publish
GET    /internal/v1/workspaces/{id}/inventory
POST   /internal/v1/workspaces/{id}/export
```

实现要求：

- 所有路径必须是规范化相对路径；拒绝绝对路径、`..`、symlink escape、junction 和设备路径；
- 写入采用临时文件、`fsync`、checksum 和原子 rename；
- `GET` 支持流式读取和 ETag；
- `export` 支持 manifest + 压缩包，避免大量小文件逐项传输；
- 删除只允许 scratch、未发布 attempt 或已过 retention 的对象；
- published artifact 不能通过普通 DELETE 删除；
- 同一 artifact 的重复 publish 使用 idempotency key 返回同一结果；
- Worker API、Orchestrator 和数据库保存一致的 artifact checksum。

在当前单机远端部署中，API 是控制面，共享 named volume 是高效数据面；未来 worker 与 API 分离到不同主机时，只替换 `WorkspaceStore` 为对象存储实现，不改变 Orchestrator 契约。

## 4. 远端可行性硬门槛

独立 worker 只有同时通过以下检查才视为可用：

1. worker 重启后 `CODEX_HOME`、Thread Registry 和已发布文件仍可恢复；
2. API 能完成 create/write/read/list/export/delete 全链路；
3. 10 个并发 attempt 不发生路径串写；
4. Codex 能在 attempt `cwd` 中创建、修改和删除允许文件；
5. Agent 无法写 Workspace 外、其他 Run 或 published 目录；
6. Orchestrator 重启后能够重新连接 job、恢复 checkpoint 或将失联 job 标为可审计失败；
7. worker image 重建后 auth/session volume 不丢失；
8. API/SSE 能返回可定位的 node、attempt、thread 和 turn 标识；
9. 远端 Compose healthcheck 与现有 dashboard/runtime-scheduler 不冲突。

## 5. 回退条件与回退形态

只有以下任一条件在 spike 后仍无法解决时，才允许回退：

- 远端容器无法稳定启动 Codex app-server；
- Workspace API 无法满足原子文件操作或恢复要求；
- 独立 worker 的认证缓存无法安全持久化；
- 独立进程/容器间 event、interrupt 或 resume 无法可靠关联；
- 外层容器与内层 sandbox 都无法阻止跨 Workspace 写入。

回退形态为“同容器、逻辑独立”的 `CodexWorkerRuntime`，并强制：

- 使用独立 `CODEX_HOME`；
- Codex `cwd` 只能是 attempt root；
- DoxAgent 源码和其他运行目录对 worker 用户只读；
- `PreToolUse` Hook 拒绝解析后目标位于 Workspace 外的写入、删除、移动和权限变更；
- `PostToolUse` Hook 记录命令、解析路径和文件差异；
- `Stop` Hook 执行 inventory/checksum 校验；
- Turn 结束后比较基线文件清单，发现越界立即隔离 Thread；
- 回退不改变 WorkerClient、WorkspaceStore 或 Orchestrator 接口，以便后续重新拆分服务。

Hook 是补充防线，不是替代 path containment、操作系统权限和发布校验的唯一安全机制。

Codex 当前正式支持 `PreToolUse`、`PostToolUse` 和 `Stop` lifecycle hooks；实施时仍须用所 pin 的 CLI 版本验证 Bash、`apply_patch`、MCP 与 code-mode nested tool call 的覆盖范围。`PostToolUse` 不能撤销已发生的副作用，因此发布前硬校验必须继续由 Orchestrator 执行。参考：[Codex Hooks](https://learn.chatgpt.com/docs/hooks)。

---

# 六、认证与模型 Provider 策略

## 1. OpenAI / ChatGPT Pro 路径

当前官方能力允许 Codex CLI 使用 ChatGPT 登录，远端/无头环境可使用 device-code 登录；Python SDK 可以复用该 `CODEX_HOME` 中的 Codex 认证。

OpenAI 是本方案的 P0 默认且最高优先级 provider。每个新 Run/attempt 均优先使用已验证的 OpenAI profile；DeepSeek 不参与常态路由，也不因单次节点错误自动抢占 OpenAI 路径。

因此，同一个人的 ChatGPT Pro 身份可以分别登录 Codex App 和远端 Codex CLI/SDK，但：

- 两边不共享 Thread、Session、Workspace 或配置；
- worker 使用自己的持久化 `CODEX_HOME`；
- 不直接挂载开发机或 Codex App 的整个 `.codex` 目录；
- 首选在 worker 内运行 `codex login --device-auth`；
- 只在必要时通过 secret channel 导入 `auth.json`，并把它视为高敏感凭证；
- 使用 `codex login status`、SDK `account()` 和 `models()` 做启动检查；
- 登出、凭证过期、模型不可见和额度受限必须形成明确 health/audit 状态。

官方同时建议程序化、CI/CD 或无人值守自动化优先使用 API key；个人 Pro 条款也限制账号共享、自动化数据提取和以订阅驱动第三方服务。因此本方案将 ChatGPT Pro 登录定义为：

> **开发、个人控制下的远端 smoke 和早期 Workflow 跑通路径；是否作为长期无人值守生产凭证，必须在上线前单独确认条款适用性和运行稳定性。**

本轮测试默认模型：

```text
model = gpt-5.6-luna
reasoning_effort = max
```

使用显式模型 ID，不使用会漂移的 alias。若 SDK `models()` 不返回该模型或不支持 `max`，该 OpenAI profile 判定为不满足本轮要求，不静默换模型。

## 2. OpenAI 路径的 Phase 0 验证

必须验证：

1. 本地独立 `CODEX_HOME` 登录；
2. 远端 worker device-code 登录；
3. worker 重启后的 token refresh；
4. `gpt-5.6-luna` + `max` 可启动、流式执行和 structured output；
5. 同一 Pro 身份在开发 Codex App 与远端 worker 同时存在时不会互相清除凭证；
6. 额度耗尽、登录失效和限流能够分类并停止启动新节点；
7. 当前使用方式符合账号与产品条款。

## 3. DeepSeek fallback 路径

当 OpenAI 路径因模型不可用、鉴权不稳定、条款不适用或额度原因不可作为当前测试后端时，使用 DeepSeek 官方支持的 `deepseek-v4-flash` custom provider profile。该 profile 直接使用 Responses wire protocol，不新增协议桥接服务。

DeepSeek 路径不能复用 CDECR 的 `CDECR_M2/M3/M4` 变量作为 Codex 配置真相；新增独立配置命名空间，例如：

```text
DOXAGENT_CODEX_MODEL_PROVIDER=deepseek
DOXAGENT_CODEX_MODEL=deepseek-v4-flash
DOXAGENT_CODEX_DEEPSEEK_BASE_URL=https://api.deepseek.com/
DEEPSEEK_API_KEY=<secret>
```

worker 在独立 `CODEX_HOME` 中生成对应 provider 配置和 `models.json`，密钥只通过 secret 注入，不写入仓库。精确 pin 的 Codex CLI/SDK 未通过模型识别、stream、structured output、tool/MCP、多 Turn、取消与恢复 smoke 前，该 fallback profile 不得启用。

## 4. Provider 不得在 Thread 中途切换

一个 Thread 创建后固定：

```text
model_provider
model
reasoning_effort
SDK/CLI version
Agent Bundle version
```

若 Provider 失效，当前 attempt 失败。切换 Provider 必须创建新 attempt 和新 Thread，使用显式 Workspace Context 重建状态，避免把不同模型的隐含历史拼接到同一正式 Thread。

---

# 七、Thread Registry 与生命周期

## 1. 逻辑 Agent

Document 1 v2 使用：

```text
C4_CONTEXT_INDEX
C1_FUNDAMENTAL_RESEARCH
C2_MACRO_RESEARCH
C3_INDUSTRY_RESEARCH
O4_MARKET_RESEARCH
```

`C4_CONTEXT_INDEX` 是实体地图与未来节点 Agent。不得继续使用可能与未来 Document 2 市场角色冲突的模糊 `C4` 名称。

## 2. Thread 复用

- C4 的实体地图、前置节点、研究后补充使用同一 C4 Thread 的三个 Turn；
- O4-B 和 O4-A 使用同一 O4 Thread 的两个 Turn；
- C1/C2/C3 各自使用独立 Thread；
- 同一节点的可恢复 transient retry 可以复用 Thread；
- Schema、安全、身份或越界失败后隔离原 Thread，重试时创建新 Thread；
- Thread ID 必须持久化，不能只保留在 Python 对象中。

## 3. Thread 不是正式 Workflow State

恢复依据依次为：

```text
checkpoint
→ published artifacts
→ context snapshot manifest
→ attempt audit
→ thread registry
```

Thread 丢失时可以创建新 Thread 继续；不得因 Thread 丢失而无法解释正式 D1 当前状态。

## 4. 建议的 Thread Registry 字段

```yaml
thread_record_id: string
run_id: string
ticker: string
workflow_version: codex_d1_v2
agent_role: string
codex_thread_id: string
provider: string
model: string
reasoning_effort: string
sdk_version: string
cli_version: string
agent_bundle_version: string
status: ACTIVE | QUARANTINED | ARCHIVED | LOST
created_at: datetime
updated_at: datetime
quarantine_reason: string | null
```

---

# 八、Context Workspace 与原子发布协议

## 1. Workspace 根目录

概念布局：

```text
/srv/doxagent-codex/
├── codex-home/
└── workspaces/
    └── codex_d1_v2/
        └── MU/
            └── run_xxx/
                ├── run_manifest.json
                ├── published/
                └── attempts/
                    └── <node_id>/
                        └── <attempt_id>/
```

Agent 不直接把 `run_xxx` 作为可写 `cwd`。每个 Turn 的 `cwd` 必须是独立 attempt root。

## 2. Attempt 目录

```text
attempt_xxx/
├── AGENTS.md
├── task.json
├── context/
│   ├── context_manifest.json
│   └── ...只读输入快照
├── scratch/
├── artifacts/
├── completion/
└── audit/
```

规则：

- `context/` 是节点启动时物化的不可变快照；
- `scratch/` 和 `artifacts/` 是 Agent 可写目录；
- `completion/` 由 worker 保存最终 structured response；
- `audit/` 由 worker/Orchestrator 写入 inventory、diff 和 event summary；
- 不通过 symlink 暴露 shared parent；
- 每个 attempt 可初始化为最小 Git repo，用于文件变化审计，但它不是 DoxAgent 源码 repo。

## 3. 三层上下文

### 静态层

静态层来自版本化 Agent Bundle：

- C1/C2/C3/C4/O4 角色说明；
- internal skills；
- Citation 规则；
- MCP 规则；
- 文件操作和输出契约；
- Sub-agent role 定义。

静态层随 worker image 发布并记录 bundle version，不依赖开发者全局 Codex 配置。

### 运行层

通过 `context/` 文件暴露：

- ticker/run metadata；
- cutoff time 和 timezone；
- 实体地图；
- 前置/最终未来节点；
- Horizontal Collection 输出；
- C1/C2/C3/O4 已发布报告；
- 事件数据；
- 必要 Blackboard snapshot；
- Unknowns 和 degradation summary。

每个节点只获得白名单内的 Context Snapshot，不把整个 Workflow History 复制进去。

### 任务层

`thread.run()` 的文本只描述：

- 当前节点和 task mode；
- 需要读取的文件；
- 允许写入的位置；
- 正式 artifact；
- 完成条件和硬边界。

## 4. Attempt 发布

```text
Agent 持续编辑 attempt/artifacts
→ thread.run(output_schema=...)
→ worker 保存 completion
→ Orchestrator 执行硬校验
→ artifact checksum/inventory 校验
→ 系统尽力生成 Citation Manifest；失败只记录 warning
→ 原子 publish
→ v2 persistence / API adapter
```

失败 attempt 可以保留审计，但不能进入：

- `published/`；
- v2 stable Document 1；
- Legacy Blackboard stable documents；
- 后续节点 Context；
- 前端正式展示。

---

# 九、Codex Agent 输出协议

## 1. 两类结果

每个研究节点完成后统一收集：

```text
Structured Completion
+ Markdown / lightweight Artifact
```

## 2. Structured Completion

最终 `output_schema` 只包含机器必须读取的内容：

```yaml
node_id: string
attempt_id: string
status: COMPLETED | DEGRADED | FAILED
artifact_refs:
  - relative_path: string
    artifact_kind: string
    checksum: string
agent_observation_candidates: []
unknowns: []
degradation_reasons: []
completion_summary: string
```

完整研究正文不进入该 JSON。

## 3. Markdown Artifact

C1、C2、C3、O4-B、O4-A 的报告以 Markdown 为正式正文。Agent 在 Loop 内逐步编辑，而不是在最后一次响应中重新输出整篇报告。

## 4. Horizontal Agent Observation

`AGENT` target 的候选 Observation 通过 Structured Completion 提交，至少包含：

```yaml
collection_target_id: string
item_key: string | null
value: any
as_of: datetime
source_refs: []
unit: string | null
published_at: datetime | null
quality_flags: []
```

随后由确定性 normalization/compiler 检查并决定是否提升。Agent 不直接生成或写入 `StateValue`。

## 5. ArtifactEnvelope

所有正式产物使用统一平台 envelope：

```yaml
artifact_id: string
artifact_kind: string
schema_version: string
workflow_version: codex_d1_v2
run_id: string
ticker: string
node_id: string
attempt_id: string
agent_role: string | SYSTEM
relative_path: string | null
content_uri: string | null
checksum: string
created_at: datetime
published_at: datetime | null
citation_manifest_ref: string | null
metadata: {}
```

业务 payload 保持各自最小契约；技术 ID、checksum、版本和审计信息放在 envelope 中。

---

# 十、Data MCP 设计

> Data MCP 的冻结决策、详细契约、工具指引、Observation Pack 与开发顺序见 [`data_mcp_development.md`](./data_mcp_development.md)。本节保留上位架构边界；若两者发生冲突，以本文件的 Workflow/Worker 总体边界和专项方案中版本化的 Data MCP 接口共同校验，不得静默选择其一。

## 1. 复用边界

继续复用：

- provider clients；
- semantic tools；
- tool registry 和 agent allowlist；
- provider fallback；
- cache；
- normalization；
- result assembly；
- provider capability / entitlement 状态；
- Observation 分段与可回查 locator。

不复用：

- Legacy Agent Tool Registry 调度循环；
- ReAct observation memory；
- Submit Tool；
- Legacy Plan/Action 状态机。

## 2. MCP 工具可见性

可见工具按 `agent_role + node_id + workflow_version` 计算，并由两层控制：

1. Codex profile 中的 MCP enabled-tools allowlist；
2. Data MCP 服务端 capability token 校验。

Prompt 中的文字限制不能替代服务端授权。

## 3. 返回协议

Data MCP 返回：

- 可供 Agent 阅读的 bounded result；
- observation/source locator；
- retrieved/published/as-of time；
- provider/method version；
- 可生成 Observation/O# 的 source ref；
- 明确的 EMPTY/FAILED/UNAVAILABLE 状态。

不得返回 provider key、数据库凭证或无法回查的裸字符串 ID。

## 4. Horizontal Collection 当前基线

`d1_horizontal_indicators_collection.md` 的前三步已经落地：

- 核心契约已冻结；
- Metric / Collection Target Registry 已建立；
- 规划内非派生 semantic tools 已完成首轮接线与真实验收。

Codex SDK D1 实施不得重新做一套 Registry。后续从该方案第四步继续：

```text
确定性 batch collector
→ 稳定性和跨标的检查
→ AGENT 补充边界
→ Codex D1 Workflow / 报告接线
```

只有 `PRODUCTION_READY` target 进入 PROGRAM batch；`DOCUMENTED`、entitlement 缺失或真实验收失败的路径不能因迁移而自动升级。

---

# 十一、Source Capture 与 Citation

## 1. 外部信源捕获

Agent 对 Data MCP 之外的材料调用 Source Capture MCP：

```json
{"url":"https://example.com/article","source":"Company release","note":"全年收入指引"}
```

系统抓取并切分来源后返回 `O#`；任何抓取、解析或持久化失败都只产生 warning，不改变节点或 Workflow 状态。

## 2. Citation Manifest

Agent 只在 Markdown 中写 `【cite:O#】`。系统解析最终 Artifact 并生成最小 Manifest：

```yaml
citations:
  - anchor: c1
    source_ref: src_123
```

Manifest 由系统派生，不要求 Agent 另行输出。无效 alias、Manifest 生成失败或持久化失败均软降级，正文继续发布。

## 3. 与现有 annotation 系统的关系

复用现有 Observation/O# 和文本 annotation 的确定性链路；v2 只补充 Artifact 绑定、最小 Manifest 与前端来源链接，不另建独立引用账本。

---

# 十二、Sub-agent 边界

首版只对以下主 Agent 开放：

```text
C1
C3
O4-A
```

规则：

- 单个主 Agent 同时最多 2 个 Sub-agent；
- 按完整研究问题拆分，不按单个 Tool 拆分；
- Sub-agent 使用 read-only 文件权限；
- 只读取当前 Context Snapshot；
- 可以使用受限 Data MCP；
- 可在 memo 中以 `O#` 引用来源，不写共享引用记录；
- 不编辑主报告、published artifact 或 shared context；
- 返回压缩 memo，由主 Agent负责综合；
- 失败只降级对应研究分支；
- C4、C2、O4-B 首版不开启，后续由独立评测决定。

Sub-agent model 继承当前 provider profile；OpenAI 早期跑通阶段同样使用 `gpt-5.6-luna`，不在功能开发阶段提前引入多模型路由。

---

# 十三、Document 1 v2 Workflow DAG

## 1. 正式节点

建议新增独立枚举和 Orchestrator，不扩写 Legacy `WorkflowNode`：

```text
PrepareCodexD1Run
BuildOrRefreshEntityMap
ScanDirectFutureNodes
RunProgramHorizontalCollection
RunC1Research
RunC2Research
RunC3Research
RunO4BResearch
NormalizeAgentObservations
EnrichFutureNodes
RunO4AResearch
AssembleDocument1V2
PublishDocument1V2
```

## 2. 执行图

```text
PrepareCodexD1Run
        │
        ├── C4 Turn 1: BuildOrRefreshEntityMap
        │            ↓
        │      C4 Turn 2: ScanDirectFutureNodes
        │
        └── RunProgramHorizontalCollection
                         │
                         ▼ join
       ┌─────────────────┼─────────────────┬──────────────────┐
       ▼                 ▼                 ▼                  ▼
RunC1Research      RunC2Research      RunC3Research      RunO4BResearch
       └─────────────────┼─────────────────┴──────────────────┘
                         ▼
              NormalizeAgentObservations
                         ▼
             C4 Turn 3: EnrichFutureNodes
                         ▼
               O4 Turn 2: RunO4AResearch
                         ▼
                AssembleDocument1V2
                         ▼
                 PublishDocument1V2
```

C4 前置工作与 PROGRAM Collection 可以物理并行，但研究节点必须等待它们的必要输入快照完成。

## 3. C4_CONTEXT_INDEX

C4 同一 Thread 执行三种 task mode：

```text
BUILD_OR_REFRESH_ENTITY_MAP
SCAN_DIRECT_FUTURE_NODES
ENRICH_FUTURE_NODES
```

治理来源是 `d1_new_research_section.md`。当前 `prompts/agents/c4.md` 和 `entity-map-and-future-nodes.md` 中超出治理文件的公开字段必须在实施时收敛。

公开业务产物只包含：

### Entity Map

```text
关系主体
关系对象
关系类型
关系说明
关联业务或产品
```

### Future Node

```text
时间
未来事项
与目标公司的关系
来源
来源发布日期
```

不得公开增加：

- 节点 ID；
- 节点类型枚举；
- 时间精度枚举；
- 可靠性等级；
- 重要性评分；
- 预期影响；
- Unit/Gap 绑定；
- priced-in 状态。

平台 envelope 可以保存内部 artifact ID、source refs、checksum 和去重审计，但不能把这些技术元数据重新定义为业务字段。

## 4. PROGRAM Horizontal Collection

该节点不使用 Codex Thread，负责：

1. 按 target registry 实例化适用 targets；
2. 执行 `PRODUCTION_READY + PROGRAM` targets；
3. 生成 `CollectionObservation`；
4. 执行单位、期间、身份、source role 和质量检查；
5. 生成 target-level `HorizontalCollectionManifest`；
6. 符合条件时提升 StateParameter/StateValue；
7. 将程序结果物化为各研究 Agent 的 Context Snapshot。

单个 target 的 EMPTY/FAILED/UNAVAILABLE 不阻塞研究；collector 自身 schema、身份或持久化失败阻塞该节点。

## 5. C1/C2/C3/O4-B 并行研究

四个主 Agent 读取：

- C4 前置产物；
- PROGRAM collection；
- 本角色 Data MCP tool allowlist；
- 事件与必要历史上下文；
- 当前时间、cutoff 和研究边界。

它们在同一 Turn 内完成研究、渐进编辑 Markdown、以 `【cite:O#】` 标注来源，并在最终 structured completion 中提交 AGENT Observation candidates。

Agent 不重复搜索已由 PROGRAM target 提供的相同 `collection_target_id + item scope`。

## 6. NormalizeAgentObservations

该节点是确定性节点：

- 合并四个 Agent 的 Observation candidates；
- 校验 target identity、source role、time scope、unit 和 refs；
- 去除同 target 的无审计竞争值；
- 通过 compiler 提升合格结果；
- 更新 Manifest；
- 不合格项记录 reason 后继续；
- 不让 Agent 直接决定 CURRENT 覆盖。

## 7. C4 研究后补充

C4 第三个 Turn 只读取：

- 已发布 Entity Map；
- 前置 Future Nodes；
- C1 和 C3 的核心研究产物；
- 必要的、有明确时间属性的 C2 证据。

它只补充外部可观察的具体未来事项，不重新解释 C1/C3，也不构造 Document 2 对象。

## 8. O4-B / O4-A

同一 O4 Thread 顺序执行：

```text
Turn 1: O4-B
宏观市场状态与可交易性研究

Turn 2: O4-A
市场隐含预期与定价结构研究
```

O4-A 必须等待：

- C1；
- C3；
- O4-B；
- 必要 C2；
- 基础 Market Measurements；
- 与候选驱动相关的事件时间线。

两份报告必须分别发布：

```text
o4_b_report.md
o4_a_report.md
```

O4-A 不生成 Expectation Unit、PotentialGap、交易建议或 Gap Activation。

## 9. Document 1 v2 Assembly

Assembly 是确定性节点，负责建立索引和引用，不再次调用模型重写所有报告。

正式 Bundle 至少包含：

```yaml
document1_v2_id: string
run_id: string
ticker: string
workflow_version: codex_d1_v2
cutoff_at: datetime
entity_map_artifact: ArtifactRef
future_nodes_pre_artifact: ArtifactRef
future_nodes_final_artifact: ArtifactRef
horizontal_collection_artifact: ArtifactRef
c1_report_artifact: ArtifactRef
c2_report_artifact: ArtifactRef
c3_report_artifact: ArtifactRef
o4_b_report_artifact: ArtifactRef
o4_a_report_artifact: ArtifactRef
citation_manifest_refs: []
unknowns: []
degradation_summary: []
created_at: datetime
```

Document 1 v2 不依赖 Document 2 完成后再生成 O1 narrative；当前 Legacy `GenerateGlobalNarrativeReport` 不进入该 DAG。

---

# 十四、失败、重试、取消和超时

## 1. 硬阻塞

- Ticker、Run、Thread、attempt 或租户错配；
- Workspace 外写入或修改 published/context；
- 必需 structured completion 无法解析；
- 必需正式 artifact 缺失或 checksum 不一致；
- MCP capability 失配；
- 未授权写操作；
- checkpoint 或 artifact 持久化失败；
- Required Agent 两次 attempt 都失败；
- Provider bridge 丢弃不支持的协议项。

## 2. 软降级

- 单个指标 EMPTY/PARTIAL/FAILED/UNAVAILABLE；
- 可选指标未采集；
- 部分 Observation 未提升；
- Source Capture、Citation 解析或 Citation 持久化失败；
- Sub-agent 失败；
- 报告覆盖率或质量警告；
- C4 没有可靠未来节点并返回合法空列表。

## 3. 超时默认值

| 执行单元 | Hard timeout |
|---|---:|
| PROGRAM collection batch | 15 分钟 |
| C4 单个 Turn | 30 分钟 |
| C1/C2/C3/O4-B 单个 Turn | 60 分钟 |
| O4-A | 90 分钟 |
| 完整 Document 1 v2 | 180 分钟 |

## 4. 重试

- SDK overload/短暂连接错误：指数退避，最多 3 次，不创建新业务 attempt；
- 节点级失败：最多 1 个新 attempt；
- 可恢复网络、overload、timeout：允许复用 Thread；
- Schema、安全、身份、Workspace 越界：隔离 Thread，新 attempt 使用新 Thread；
- Provider 切换：新 attempt + 新 Thread；
- 超过预算时停止可选研究和新 Sub-agent，不发布半成品。

## 5. 取消

用户取消时：

1. Orchestrator 标记 cancellation requested；
2. worker 调用 `TurnHandle.interrupt()`；
3. attempt 标记 CANCELLED；
4. 不发布 artifact；
5. 保留 14 天供审计。

---

# 十五、持久化与旧系统兼容

## 1. 独立 v2 存储

为避免污染 Legacy schema，建议新增：

```text
codex_workflow_checkpoints
codex_thread_registry
codex_node_attempts
codex_artifacts
codex_sources
codex_citation_manifests
```

Horizontal Collection Manifest 可以继续通过现有 append-only Blackboard working-memory artifact repository 持久化，但外层 envelope 必须包含 workflow/node/attempt 关联。

## 2. 不修改 Legacy Orchestrator

新增：

```text
CodexDocument1Workflow
CodexWorkerClient
CodexWorkspaceStore
CodexThreadRegistry
CodexArtifactRepository
CodexSourceRepository
CodexCitationManifestRepository
CodexDocument1ResultAdapter
```

不要继续向 `BlackboardInitializationWorkflow` 主类增加 Codex mixin。旧类只通过更上层的 run-type router 与新类并列。

## 3. Run 路由

```text
legacy
→ BlackboardInitializationWorkflow

codex_d1_v2
→ CodexDocument1Workflow
```

需要显式 feature flag：

```text
DOXAGENT_CODEX_D1_ENABLED=false
```

关闭时 API 拒绝创建 v2 Run，但 Legacy Run 不受影响。

## 4. API 与前端

建议新增 v2 payload，而不是改变旧 payload 的字段含义：

```text
POST /api/runs { workflow_version: "codex_d1_v2" }
GET  /api/runs/{run_id}
GET  /api/runs/{run_id}/artifacts
GET  /api/runs/{run_id}/document1-v2
GET  /api/runs/{run_id}/events
POST /api/runs/{run_id}/cancel
```

前端新增 v2 展示分支，分别展示：

- Entity Map；
- Future Nodes；
- Horizontal Collection/Manifest；
- C1/C2/C3；
- O4-B；
- O4-A；
- Citation/来源链接；
- degraded/unknown 状态。

旧页面继续读取旧接口。前端不读取 Workspace 文件路径，只读取经过 Adapter 授权的 artifact 内容或签名 URL。

---

# 十六、Document 2 冻结接口与非回归约束

本轮不实现 Document 2，只发布一个版本化 handoff：

```yaml
document1_handoff_version: d1-handoff-v1
run_id: string
ticker: string
document1_v2_ref: ArtifactRef
entity_map_ref: ArtifactRef
future_nodes_final_ref: ArtifactRef
horizontal_state_refs: []
c1_ref: ArtifactRef
c2_ref: ArtifactRef
c3_ref: ArtifactRef
o4_a_ref: ArtifactRef
citation_manifest_refs: []
thread_refs_by_role: {}
cutoff_at: datetime
```

冻结规则：

- D1 Markdown 必须能作为显式 Context Snapshot 注入；
- Structured Output 必须能机器读取；
- 已生成的 Citation/source ref 必须能继续引用；
- 未来 D2 可以在同一 Run 恢复对应 C1/C2/C3/O4 Thread；
- D2 不得依赖 Thread 中未发布的隐含信息；
- 本轮不创建 Expectation Unit、不修改 D2 Schema、不运行 D2 promotion；
- Legacy D2 行为和 eval 入口不受影响。

---

# 十七、可观测性、保留和安全审计

## 1. 统一关联字段

所有 log、trace、usage、MCP call、artifact 和 error 必须携带：

```text
workflow_version
run_id
ticker
node_id
attempt_id
agent_role
thread_id
turn_id
worker_job_id
provider
model
```

## 2. 事件流

worker 原始 SDK event 不直接全部推给前端。Orchestrator 转换为稳定业务事件：

```text
NODE_STARTED
TURN_STARTED
TOOL_ACTIVITY
ARTIFACT_UPDATED
SUBAGENT_STARTED
NODE_DEGRADED
NODE_RETRYING
NODE_COMPLETED
NODE_FAILED
RUN_CANCELLED
```

不得向前端或日志泄漏 auth token、provider key、完整敏感环境变量或原始 reasoning。

## 3. 默认保留期限

- 正式 Document 1 v2、结构化产物和已生成的 Citation/source：沿用 DoxAgent 正式数据策略；
- published Workspace 和 Codex Session：30 天；
- failed/cancelled attempt：14 天；
- 不含完整原文的运行审计、usage、版本和失败码：180 天；
- provider raw data/cache：遵守 provider entitlement 与现有 TTL；
- 删除 Workspace 前保存 artifact inventory、checksum 和恢复清单。

---

# 十八、建议代码结构

```text
src/doxagent/
├── codex_runtime/
│   ├── client.py
│   ├── config.py
│   ├── thread_registry.py
│   ├── attempts.py
│   ├── artifacts.py
│   ├── citations.py
│   ├── events.py
│   └── errors.py
├── codex_worker/
│   ├── app.py
│   ├── jobs.py
│   ├── sdk_runtime.py
│   ├── workspace_api.py
│   ├── workspace_store.py
│   └── health.py
├── mcp/
│   ├── data_server.py
│   ├── source_capture_server.py
│   ├── capabilities.py
│   └── audit.py
├── workflows/
│   └── codex_document1/
│       ├── orchestrator.py
│       ├── schema.py
│       ├── nodes.py
│       ├── context.py
│       ├── validators.py
│       ├── assembler.py
│       └── adapter.py
└── dashboard_api/
    └── codex_document1.py

prompts/codex_v2/
├── AGENTS.md
├── agents/
├── skills/
└── schemas/

tests/codex_runtime/
tests/codex_worker/
tests/codex_document1/
```

具体模块名可在实现前调整，但运行时、worker、MCP、Workflow 和 Adapter 的责任边界不得重新揉成一个大模块。

---

# 十九、分阶段开发顺序

## Phase 0：可行性与硬门槛 Spike

目标：证明固定技术目标在本地与远端成立，不建设完整 D1。

任务：

1. pin Python SDK/CLI；
2. 创建独立 `CODEX_HOME`；
3. 验证本地/远端 ChatGPT device auth；
4. 验证 `gpt-5.6-luna + max`；
5. 验证 start/run/stream/interrupt/resume/output_schema；
6. 验证 MCP 和 Sub-agent 基础能力；
7. 实现最小 worker + Workspace CRUD；
8. 验证 Docker sandbox；
9. 若 OpenAI 路径不可用，验证 DeepSeek 官方 Codex custom provider fallback profile；
10. 形成 Go/No-Go 报告，明确使用独立 worker 还是批准回退。

退出条件：至少一个 provider profile 能通过 structured output、tool/MCP、多 Turn、文件 CRUD 和恢复测试。

## Phase 1：冻结 v2 核心契约

任务：

- v2 Workflow nodes/checkpoint；
- Thread Registry；
- Attempt/ArtifactEnvelope；
- WorkspaceStore；
- Structured Completion；
- Source Capture 与最小 Citation Manifest；
- Document1V2Bundle；
- D1 handoff v1；
- 硬/软失败枚举；
- API versioning。

退出条件：契约测试通过，Legacy schema/test 无回归。

## Phase 2：生产化 worker 与 Workspace Control Plane

任务：

- 独立 worker image；
- Job API/SSE/interrupt；
- Workspace API；
- named volumes；
- worker auth/profile；
- remote Compose；
- healthcheck；
- restart/recovery；
- path containment 和 atomic publish。

退出条件：远端完成文件 CRUD、并发隔离、重启恢复和 Workspace 外写入拒绝。

## Phase 3：Data MCP、Source Capture 与 Citation

任务：

- 现有 semantic tools MCP adapter；
- per-role/node allowlist；
- capability token；
- bounded ToolResult；
- 轻量 Source Capture；
- Observation/O#；
- 系统派生 Citation Manifest；
- annotation/API 投影。

退出条件：真实 read-only tools 可调用，凭证不暴露；任何 Source/Citation 失败均只降级、不阻塞 Workflow。

## Phase 4：Horizontal Collection 后四步

承接现有方案第四至第七步：

- PROGRAM batch collector；
- Observation/compiler；
- 跨标的稳定性测试；
- AGENT target 边界；
- Manifest/StateValue 接线；
- Codex Context Artifact；
- D1 Workflow 接线。

退出条件：PROGRAM 与 AGENT targets 不竞争，单 target 失败可降级，Manifest 可审计。

## Phase 5：C4 与 Context Snapshot

任务：

- 重命名为 `C4_CONTEXT_INDEX`；
- 收敛现有 C4 prompt/skill；
- 三个 task mode；
- minimal public payload；
- context snapshot compiler；
- C4 Thread 三 Turn 恢复；
- 空结果与去重测试。

退出条件：C4 不泄漏 Document 2、Gap、定价或重要性语义。

## Phase 6：C1/C2/C3/O4 与完整 DAG

任务：

- Agent Bundle；
- C1/C2/C3/O4 Thread；
- 并行 join；
- Agent Observations；
- Sub-agent 受限角色；
- O4-B/O4-A 双 Turn；
- timeout/retry/cancel；
- provider profile 固定。

退出条件：完整 DAG 能在 fixture 与 fake worker 下确定性完成。

## Phase 7：Assembly、持久化、API 与前端

任务：

- Document1V2Bundle；
- v2 repositories；
- Result Adapter；
- API/SSE；
- 前端 v2 分支；
- Legacy compatibility projection；
- retention cleanup。

退出条件：Legacy 与 v2 可并存展示，前端不读取 Workspace，旧 Run 行为不变。

## Phase 8：本地与远端功能验收

本阶段只验证开发完成度：

- contract/unit/integration；
- provider smoke；
- remote Compose；
- Workflow 跑通；
- restart/resume；
- cancel；
- idempotent publish；
- failed attempt isolation；
- Legacy regression。

研究质量和运行表现不混入本阶段的开发任务。

---

# 二十、验收分层

## 1. 开发与契约验收

必须随开发完成：

- Schema、身份、Workspace、权限、幂等和恢复硬门槛 100% 通过；
- 完整 Document 1 v2 至少成功跑通；
- 失败 attempt 不发布 Artifact；Citation 异常不阻塞成功 Artifact；
- Legacy 测试不回归；
- 远端 worker 文件 create/read/write/delete/publish/restart 全链路可用。

## 2. 研究质量验收

在 Workflow 跑通后另开独立任务：

- C1/C2/C3/O4-B 与 Legacy 重叠部分比较；
- Entity Map、Future Nodes、Horizontal Collection、O4-A 使用新版 rubric；
- 事实、来源、时间和 Ticker 混淆作为关键失败；
- 引用解析率和报告完整性单独评估。

该工作不与基础开发任务捆绑执行。

## 3. 运行表现验收

在功能稳定后另开独立任务：

- node/run P50/P95；
- tokens/cost；
- MCP calls；
- Sub-agent 使用；
- retry/timeout；
- worker concurrency；
- Workspace 存储增长。

先建立真实基线，再确定 SLA、预算和模型降级策略。

## 4. 启用边界

- v2 始终通过 `workflow_version=codex_d1_v2` 显式启动；
- 本方案完成后仍只作为可选 beta 路径；
- 不自动切换默认生产 Workflow；
- 默认切换需单独批准；
- D2 迁移需单独方案。

---

# 二十一、主要风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Codex SDK 非金融研究主路径 | 行为或工具能力不稳定 | Phase 0 真实 spike；固定 SDK/CLI；端到端契约测试 |
| Python SDK beta 漂移 | API/事件结构变化 | 精确 pin；升级兼容矩阵；审计版本 |
| Pro 登录不适合无人值守生产 | 凭证、条款或额度不稳定 | 仅用于受控 smoke；生产前单独确认；准备 provider fallback |
| DeepSeek Codex 集成的版本或行为差异 | fallback profile 在特定 CLI/SDK 版本下失效 | 使用官方配置；精确 pin CLI/SDK；运行 provider smoke 矩阵；不静默降级 |
| Docker 内层 sandbox 失败 | Agent 越界访问 | sandbox preflight；outer-container isolation；不挂载源码；Hook 回退 |
| worker/Workspace 分离后文件难维护 | 半成品、孤儿文件、跨容器读写困难 | Workspace API + named volume + atomic publish + retention |
| Thread 成为隐式正式状态 | 无法恢复和审计 | checkpoint/artifact 为真相；Thread 可丢失重建 |
| Citation 解析或持久化失败 | 来源链接缺失 | 正文照常发布；warning；异步重试 |
| Sub-agent 并行写污染 | 报告内容冲突 | read-only Sub-agent；主 Agent 汇总；并发上限 2 |
| 新旧数据模型串写 | Legacy 回归 | 独立 v2 tables/repositories；run router；显式 workflow_version |
| O4-A 与 O4-B 混合 | D2 接收技术交易内容 | 同 Thread 双 Turn、双 artifact、独立契约 |
| C4 公开 schema 超出治理文件 | D1 语义膨胀 | minimal public payload；技术元数据放 ArtifactEnvelope |

---

# 二十二、Definition of Done

本轮 Codex SDK Document 1 v2 开发只有满足以下条件才算完成：

1. Legacy Framework 无行为回归并可独立运行；
2. `codex_d1_v2` 通过独立路由启动；
3. 至少一个经过验证的模型 provider 能完成全 Workflow；
4. 独立 worker 或经批准的 Hook 回退路径能在远端运行；
5. Workspace 文件 CRUD、原子发布、删除和保留机制可用；
6. C4、PROGRAM Collection、C1/C2/C3/O4-B、Normalization、C4 enrichment、O4-A、Assembly 全部接通；
7. Markdown 和 Structured Completion 两类 Agent 结果可统一收集，Citation Manifest 由系统派生；
8. Citation/Source Capture 的任何失败都不会阻塞 Artifact 或 Workflow；
9. Thread、checkpoint、artifact 和 usage 可追踪；
10. D1 handoff v1 已冻结，但没有实施 Document 2；
11. API/前端能区分 Legacy 与 v2；
12. 本地与远端功能验收通过；
13. 研究质量和运行表现评测已被明确拆分为后续独立工作，而非伪装成当前开发已完成。
