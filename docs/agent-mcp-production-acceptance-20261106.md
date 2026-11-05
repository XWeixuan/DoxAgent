# DoxAgent MCP 生产部署与 ChatGPT / Dots 接入

2026-11-06（北京时间）。实现已合并到 `origin/main`，功能提交 `5a816432`；新 API 和 Secure MCP Tunnel 已部署到新加坡腾讯云服务器。当前通过的是服务器与真实生产数据验收；ChatGPT 插件安装和 Dots 实际工具调用尚待完成，不能把服务端 ready 记为端到端接入完成。

## 生产版本与范围

| 项目 | 实际值 |
| --- | --- |
| 主机 / 仓库 | `doxagent-sg` / `/home/ubuntu/doxagent`，main |
| API 镜像 | `sha256:712187bbf10fdc093eabc698e05708c30bf42eba89df40afe4dc10ece3f3f091` |
| MCP + Tunnel 镜像 | `sha256:52f88941c355f2d816e7886c7758e1e666bd94ca65e4011be078f7200da4877b` |
| Tunnel client | 官方 `v0.0.19` Linux amd64，归档及二进制 SHA256 校验通过 |
| Tunnel | `tunnel_6aecfaf096dc8191a80d1e676208456b`，名称 doxagent |
| 组织 | `org-BBlVfjWjoyNRGZRBNWyLGrdN` |
| 已关联 ChatGPT workspace | `d21cbb07-93f1-4a00-9ea4-54eb4e42c696` |
| 容器 | `doxagent-v2-v2-agent-mcp-tunnel-1` |

在独立工作树 `C:/DoxAgentMCPRelease` 将本次 MCP 文件移植到最新 main，保留主工作区其他任务的未提交内容。changelog 冲突保留 main 记录并追加本次条目；前后端 schema 重新生成。没有把无关变更纳入发布。

API 在当前生产镜像上只覆盖本功能涉及的 11 个 API 源码/契约文件。发布前逐文件比对，相关旧源码与 main 相同，仅生成 schema 有差异。使用既有 `storage_release.py` 发布锁、不可变镜像、版本记录和健康回滚。只替换 API 并新增 Tunnel 服务；其他 20 个已有容器 ID 均保持不变，包括采集、研究和 Executor。

运行配置位于 `/var/lib/doxagent/agent-mcp/compose.agent-mcp.json`，已追加到既有 release 的 `compose_base_command`；`compose.current.json` 记录两个新镜像。保留原生产/server/repair/projector/余额/正文覆盖文件。没有数据库迁移、数据回放、新公网端口或 MCP 业务卷。新增容器放在原 `doxagent-app.slice`，预算 384 MiB、0.5 CPU、32 PIDs。

凭证只在服务器 `/var/lib/doxagent/agent-mcp/secrets/` 的独立文件中；运行密钥与内部 API token 分开，文件 UID 10001、mode 0400。此文及证据不包含凭证值。MCP 子进程不继承 OpenAI 运行密钥。

## 验证结果

- 最新 main 上 MCP/Bus 专项 **23 通过**，前端 **69 通过**；schema 生成、typecheck、Vite build 通过。历史扩大回归的五个基线失败仍见原实现报告，不据此宣称全仓测试通过。
- `doctor --profile doxagent-readonly --explain` 通过；OpenAI 返回真实 tunnel 元数据，组织和 workspace 关联均已确认。
- `/healthz` 返回 `200 live`，`/readyz` 返回 `200 ready`，实际控制面 poll 连续成功、HTTP 204；API healthy，Tunnel 无 OOM、无重启。
- 一次实际生产概览覆盖 **10 ticker**，响应 **72,592 字节**，约 **2.27 秒**。status / metrics / Gateway / OpenRouter 余额四块均 AVAILABLE。没有逐 ticker 额外创建持久化 view。
- sources 分页：2 条、约 0.15 秒，保留续页；Case List：2 条、约 0.07 秒；标题搜索：1 条、约 1.81 秒，并验证命中选定 Case。
- 同一 Case 的 main、W1、正文句柄读取通过；列表没有正文/reasoning。非法 command 输入被校验拒绝；内部凭证的 POST 和研究 GET 均返回 403，`can_operate=false`。
- 四个工具通过真实 Linux stdio MCP 会话发现并调用，均标注 readonly=true / destructive=false。生产探测使用独立短期 stdio 会话连接同一 API，**没有冒充 ChatGPT 经 Tunnel 发起的调用**。
- Tunnel 空闲采样约 **66.13 MiB / 384 MiB、0.03% CPU、12 PIDs**；这是单次运行采样，不代表长期容量或峰值验收。新增容器 Block I/O 为 2.5 MB read / 0 write；API 仍承担原有 SQLite 查询与技术游标写入。

额外尝试 Responses API 跨端调用时，运行 key 返回 `401`、缺少 `api.responses.write`。未扩大该 key 权限、未生成模型回答。这不影响已经成功的 Tunnel metadata/poll，也不能证明 ChatGPT 用户侧调用已完成。ChatGPT 插件使用登录用户的工作区权限，不需要把该运行 key 填入插件。

证据：`artifacts/audits/agent_mcp_20261106/` 中的生产工具调用、运行检查、doctor、metadata 和跨端受限收据。服务器完整私密发布记录在 `/var/lib/doxagent/agent-mcp/`；不要下载/提交其中 state.before.json、compose.before.json 或 secrets。

## ChatGPT 接入

按 [OpenAI Secure MCP Tunnel 指南](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) 与 [自定义 MCP 插件指南](https://developers.openai.com/api/docs/guides/custom-mcp-server)：

1. 在目标 ChatGPT workspace 打开 **Plugins → ＋ → Add custom MCP server**。
2. Name 填 `DoxAgent`，Description 可填 `只读查询 DoxAgent 运营指标与 Case；按概览、摘要、详情渐进读取`。
3. Connection 选 **Tunnel**，选择 doxagent，或输入 `tunnel_6aecfaf096dc8191a80d1e676208456b`。
4. Authentication 选 **No authentication**：本 MCP 没有应用层 OAuth，入口访问由 OpenAI Tunnel 的组织/workspace/用户权限控制；服务器内部 GET 另用独立 token。不要填写 OpenAI 运行 key 或内部 token。
5. 检查风险说明并创建为插件，再安装到 Dots 实际使用的个人插件/工作区。在 Dots 可用的插件设置中启用 DoxAgent；实际入口名称以账户页面为准，本轮未读取或修改 Dots 的定义。
6. 确认发现四工具：`doxagent_get_overview`、`doxagent_list_cases`、`doxagent_search_cases`、`doxagent_get_case_detail`。不要求新增通用 search/fetch 工具。

最小验收提示词：

```text
仅使用 DoxAgent 插件。先调用 doxagent_get_overview（默认参数），简述全局运行状态。
再用 doxagent_list_cases 查询 MU 的 TRADING_DAYS_7、limit=2。
从一条记录的 title.value 提取标题，用 doxagent_search_cases 在相同周期搜索它。
最后仅对这个 case_id、沿用列表 meta.view_id 调用 doxagent_get_case_detail。
如需正文，只续读返回的 content_handle，不批量展开其他 Case。
```

检查真实调用结果和服务端对应请求后，才能把 ChatGPT/Dots 端到端验收标为完成。如果插件未列出 Tunnel，先核对当前 ChatGPT workspace 是否为已关联 workspace，以及操作者的 Tunnels Read + Use 权限；不要把 HTTPS `https://api.openai.com/v1/tunnel/...` 当成 Server URL。

本轮浏览器工具因无法可靠识别 Windows 当前浏览器 URL 而终止，未继续自动操作 ChatGPT；没有创建、安装插件或发送 Dots 消息。

## 运行与回滚

用现有 release state 中的完整 Compose 参数管理服务，保留新增覆盖文件，不用简化的两文件命令覆盖现有运行设置。生产健康检查与仓库新增 overlay 一致。检查可用：

```bash
sudo docker inspect doxagent-v2-v2-agent-mcp-tunnel-1 --format '{{.State.Health.Status}} {{.RestartCount}}'
sudo docker logs --tail 30 doxagent-v2-v2-agent-mcp-tunnel-1
```

回滚先停止新增 Tunnel 服务，再按 release 记录恢复 API 上一镜像及覆盖。两个凭证文件只在停止服务/确认轮换流程后处理；不恢复业务数据库、不清 WAL、不重放消息/交易。后续轮换 OpenAI 运行 key 只需替换专用 secret 文件并重建 Tunnel 容器，不改业务配置。
