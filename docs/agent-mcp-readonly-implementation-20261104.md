# Agent 只读 MCP：实现与部署手册

状态：本地开发与验证。没有部署到腾讯云、创建真实 Tunnel、调用生产业务 API 或操作交易。方案依据：[开发方案](../dev_plan/agent_mcp_readonly_plan_20261104.md)。分支为 `codex/agent-readonly-mcp`，开发基线 `e5df0ae1`；原有 changelog 内容保留。

## 已实现

- `src/doxagent/agent_mcp/`：独立 stdio 服务，仅依赖 MCP SDK、httpx 和输入校验；没有数据库、完整 API、研究或交易模块依赖。
- 四工具：`doxagent_get_overview`、`doxagent_list_cases`、`doxagent_search_cases`、`doxagent_get_case_detail`，输入闭合，标注只读，返回一份结构化 JSON。
- API 可选内部只读凭证；父请求和 query worker 都检查读取范围。普通 Supabase 用户登录保持原行为，不增加权限表或 OAuth 服务。
- `/api/doxagent/v2/overview/operations`：共享原 Overview ticker 选择/指标函数与 Runtime 指标函数，批量预热 Runtime 桶；复用 Bus dashboard。只读 API 聚合，未增加数据库、索引或后台任务。
- Bus summary 不构造单源消息耗时/正文卡片明细；sources 页仅为当前有界源页构造 recent 数据，全局 counts/coverage 保持完整绑定集合的原分母。
- sources 页跳过未返回的周期文章cohort、周期消息计数和周期E2E查询；即使显式选择ALL，也不为读取源状态扫描全部文章历史。
- Case List/Search 复用原标题搜索、结果/来源/周期筛选和游标。每页最多20条，不读取详情或正文。
- Detail 按 part 读取；reasoning、正文、PIN 和长内联字段通过句柄与UTF-8块续读。内容 API 校验 Case/view 关联，不接受任意 research content。
- 单进程一个在途查询、最多两个等待；输出128 KiB；续页/内容状态只存在有界内存（4 MiB/128项、最多15分钟且受原view到期约束），进程重启或淘汰后明确要求重新打开。

聚合API在220000字节行数据预算前提前keyset分页，为原256 KiB响应外层留空间；MCP再按128 KiB投影结果预算处理续页。已用20个ticker验证不会因原始DTO偏大而直接失败或漏行。

API 原有 `views/cursors` 技术状态写入继续使用；“只读”禁止业务、配置和交易变更，并不承诺底层绝对零 SQLite 写入。独立收益审计的主动快照/假设计算、独立研究/事件库和下载没有接入。

## 调用方式

示例参数直接作为工具输入，不包含 URL、SQL、命令或凭证：

```json
{"period":"TRADING_DAYS_7"}
```

上述为 `doxagent_get_overview` 的默认 summary。返回全局已有KPI及ticker页，行内包含Runtime和Bus的已有顶部指标。`has_more=true` 时用同一参数加 `cursor=next_cursor` 续读。字段名沿用原API；未展示的旧值、控制参数和DTO扩展字段不自动外泄。

同一个概览工具可按需下钻：

```json
{"section":"sources","ticker":"MU"}
{"section":"cost","scope":"API","period":"TRADING_DAYS_7","ticker_selector":"ALL"}
{"section":"policy","ticker":"MU","shell_id":"ALL"}
```

List / Search：

```json
{"ticker":"MU","period":"CURRENT_TRADING_DAY","result":"FAILURE"}
{"ticker":"MU","period":"TRADING_DAYS_7","q":"HBM","source_id":"已知来源ID"}
```

Search仅对标题做Unicode字面包含匹配。分页保留原period、result、source_id、q和limit，不静默扩大范围。

Detail：

```json
{"ticker":"MU","case_id":"从列表取得的ID","view_id":"从列表取得的view_id"}
{"ticker":"MU","case_id":"同一个ID","view_id":"同一个view_id","part":"attempts","node":"W3"}
{"ticker":"MU","case_id":"同一个ID","part":"content","content_handle":"详情返回的句柄"}
```

子页包括 `attempts/messages/candidates/executions`，正文part为 `content`。嵌入的第一页 attempts/messages 的 next_cursor 也可以用于对应part，node和默认20条大小保持一致。正文续读同时保留content_handle和next_cursor。没有提供view时服务内部创建RUNTIME view，不要求Agent手工调用底层read-context。

固定Case快照缺失时保持原错误，不以当前研究版本补齐。Bus为当前head，Gateway为即时只读探测，余额使用原300秒缓存；这些不是与Case同一原子快照。Bus区块的 `meta.read_at` / 聚合响应读取时间属于技术时效元数据，不能解释为采集成功时间。

## 本地运行

给现有 API 配置 `DOXAGENT_AGENT_READ_TOKEN_FILE`，文件内容为至少32字符的随机内部token；未配置时该认证入口关闭。不要使用Supabase service-role、浏览器短期access token或OpenAI管理员key替代。

Linux部署时，两个凭证文件需允许容器UID 10001读取（例如owner 10001、mode 0400）；本机API若以root运行仍可读取同一内部token文件。Compose的本地文件secret挂载不会替你改变宿主文件所有者。

MCP环境只需：

```text
DOXAGENT_AGENT_API_URL=http://现有API私网地址:8000
DOXAGENT_AGENT_READ_TOKEN_FILE=/run/secrets/agent_read_token
```

启动 `python -m doxagent.agent_mcp`。stdout只承载MCP协议，日志在stderr。MCP只使用固定GET路径。真实内部token不应出现在聊天、命令参数、日志或提交文件中。

## Linux 镜像与 Tunnel

打包入口：`deploy/Dockerfile.agent-mcp`，覆盖配置：`deploy/docker-compose.agent-mcp.yml`。仅新增一个Tunnel容器，stdio子进程为MCP，无host ports、无业务数据卷、无业务env_file、无启动迁移。

最小依赖使用 `deploy/agent-mcp.lock`，由 Linux amd64 / Python3.11 平台解析并固定哈希。更新方式：

```bash
uv pip compile deploy/agent-mcp.in --generate-hashes --python-version 3.11 \
  --python-platform x86_64-unknown-linux-gnu --output-file deploy/agent-mcp.lock
```

从[官方Tunnel指南](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)指向的正式release下载目标Linux架构的包，先核对SHA256SUMS，提取二进制到仓库之外的独立目录。不要提交二进制或secret。本轮验证了 `v0.0.19` Linux amd64发行包，归档哈希与二进制哈希见 `artifacts/audits/agent_mcp_20261104/tunnel-binary.json`；后续先确认版本，再固定部署，不自动升级。

设置以下**文件路径/非秘密构建参数**：

```text
DOXAGENT_TUNNEL_BINARY_DIR=包含tunnel-client文件的绝对目录
DOXAGENT_TUNNEL_CLIENT_SHA256=已核实的二进制SHA256
DOXAGENT_AGENT_READ_TOKEN_PATH=内部API token文件的绝对路径
DOXAGENT_TUNNEL_KEY_PATH=OpenAI Tunnel运行key文件的绝对路径
```

使用实际生产的原Compose文件/覆盖顺序，最后追加 `-f deploy/docker-compose.agent-mcp.yml`。不要省略既有server等覆盖。先运行 `docker compose ... config` 检查合并结果，再build新增服务；只更新必要API服务，不重启采集或Executor。

API也必须通过既有后端镜像流程打包本次源码和生成的wire schema；仅更新v2-api到该新不可变镜像，不能让新增MCP连接仍未包含只读入口的旧API。没有新数据库迁移，不需要为本功能重新启动其他后端角色。

以下 `docker compose ...` 中的省略号表示**已有生产Compose参数与上述最后一个覆盖文件**，不是可直接复制的Shell字面参数：

```bash
docker compose ... build v2-agent-mcp-tunnel
docker compose ... run --rm v2-agent-mcp-tunnel help quickstart
docker compose ... run --rm v2-agent-mcp-tunnel init \
  --sample sample_mcp_stdio_local --profile doxagent-readonly \
  --tunnel-id <真实tunnel_id> --mcp-command /app/mcp-server.sh
docker compose ... run --rm v2-agent-mcp-tunnel doctor --profile doxagent-readonly --explain
docker compose ... up -d --no-deps v2-agent-mcp-tunnel
```

profile持久化在专用home卷，只保存环境secret引用。入口读取OpenAI运行密钥到 `CONTROL_PLANE_API_KEY`，MCP子进程启动前删除该环境变量；不配置或使用管理员key。健康/UI保持容器内loopback，无端口发布。初始容器预算为384 MiB、0.5CPU、32 PIDs；真实Tunnel常驻资源仍需联调观察。

真实接入需：目标组织/工作区关联、操作人员Tunnels Read+Manage（创建）、运行key及调用人员Read+Use（使用）。Dots宿主必须支持该Tunnel目标；Responses API用tunnel_id。完成doctor/ready/polling后，必须从实际Dots调用overview和单Case详情，才能标记端到端通过。缺少账号关联、tunnel_id或运行key时不要使用公网代理替代。

## 验证与边界

本地验证收据在 `artifacts/audits/agent_mcp_20261104/`：

- `focused.xml`：58项相关回归通过；`final-focused.xml`：包含补充容量场景的36项检查通过；`mcp-final.xml`：最终MCP/Bus的23项检查通过。选择集互有重叠，不相加为独立测试总数。
- `unit.xml`：扩大后的V2后端回归；5项失败同时在修改前HEAD复现，详见 `baseline-failures.xml/.txt`。不把该套件报告为全绿，也没有为修复无关基线而改业务逻辑。
- `container-smoke.json`：真实Linux容器stdio初始化、四工具发现和非法输入拒绝；约52 MiB、采样空闲CPU0%、3 PIDs。只代表该MCP进程的本地采样，不是Tunnel+生产负载容量验收。
- `tunnel-binary.json`：正式发行包及二进制校验；CLI quickstart/init帮助和临时profile创建已实际验证，未连接真实Tunnel。

前端schema生成、typecheck、构建及69项测试通过。没有修改页面组件，因此没有新增浏览器布局验收要求。新增测试覆盖真实只读API、标题搜索、隐藏字段剔除、嵌入游标、超限页不漏行、正文关联、UTF-8分块、队列取消和实际stdio调用。

上线前仍需按原方案核验生产版本、自然负载SQLite I/O与锁等待，并完成真实Dots/Tunnel联调。回滚仅停止新增服务、移除/轮换内部token及必要时恢复旧API镜像；不恢复业务库、不清WAL、不重放消息或处理持仓。
