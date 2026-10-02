# Codex SDK 与 GPT-6 迁移验收（2026-10-02）

## 结果与范围

本地项目 `openai-codex`、SDK 内置 `openai-codex-cli-bin`、独立 npm CLI 均升级到本次核查的最新版本 **0.159.3**。桌面应用自带的 CLI 仍由应用管理，为 0.159.2；应用注入的 PATH 可能优先选中它。独立 CLI 路径为 `C:\Users\WEIXUANXIE\AppData\Roaming\npm\codex.cmd`。

远端 `doxagent-sg`（`VM-0-15-ubuntu`）的 Worker、初始化、O4、调度器、Guardian、修复代理镜像及 Guardian 的初始化模板均升级到 SDK/CLI 0.159.3。本次只有合成 marker 请求，没有执行真实业务测试；成功调用不等于业务效果验收。

模型转换：`gpt-5.6-luna → gpt-6-luna`；`gpt-5.6-sol → gpt-6.1-sol`；已有 `gpt-6-sol` 默认值也统一到 `gpt-6.1-sol`。所有节点保留原推理强度。

## 节点覆盖

| 调用路径 | 迁移后的默认模型 | 原推理强度 |
|---|---|---|
| Document 1 / Global Research：C4 pre-scan、C1、C3、C5、C4 enrichment/finalization，以及兼容旧图的 Codex 节点 | gpt-6-luna | max / 原配置 |
| Market Situation Research：C2 与研究图中的 O4 | gpt-6-luna | max / 原配置 |
| Document 2：全部 O0 candidate、synthesis、review、finalization；全部 O1 state、realization、gaps、finalization | gpt-6-luna | max / 原配置 |
| Event Library：O2 maintain，本地/远端 runner | gpt-6-luna | max / 原配置 |
| Document 3：O3 initialize、trigger calibration、policy compile、final review | gpt-6.1-sol | medium / 原初始化配置 |
| Document 3：O3 maintain | gpt-6-luna | max / 原配置 |
| Persistent Runtime：W3、覆盖检查、维护执行 override | gpt-6-luna | max / 原配置 |
| 独立监控 O4：configure、deliver、repair | gpt-6.1-sol | high |
| Initialization Repair Agent | gpt-6.1-sol | medium |
| Pilot/监控任务配置模板 | gpt-6.1-sol | high |

程序性的 collection、assemble、publish、validate 等步骤没有额外模型调用。

Worker 的统一执行转换覆盖旧冻结输入、已持久化请求和恢复线程；不重写原请求、冻结 Case、request hash 或幂等身份。每次实际执行写入 `attempts/<attempt>/audit/model_selection.json`，同时记录 requested/execution model 与 effort。新 Runtime 调用记录也保存请求模型和实际模型。显式非 OpenAI provider 的模型名保持原值。

## 真实调用证据

证据位于 `eval/codex_sdk_gpt6_migration_20261002/`：

- `local.json`：本地 SDK，6-luna/max、6.1-sol/medium、high、max，四次全部 completed/OK。
- `remote-predeployment.json`：生产 Worker 登录状态下的隔离 SDK，同四组全部成功。
- `worker-postdeployment.json`：升级后实际 HTTP Worker 接口，5.6-luna/max 与 5.6-sol/medium 请求全部 succeeded/OK；执行审计分别为 6-luna/max 与 6.1-sol/medium。
- `agent-postdeployment.json`：实际修复代理镜像与 Guardian 登录，6.1-sol/medium completed/OK。
- `deployment.json`、`images.json`、`template-deployment.json`、`final-verification.json`：镜像、源码版本和服务状态。

修复代理独立登录文件最初返回 workspace routing 401。核对 token 内的账户和用户身份摘要与 Worker 一致后，备份原文件并同步 Worker 当前有效登录文件，再验证成功。凭据未进入仓库、镜像或上述报告。

## 部署方式与源码身份

各服务此前运行不同业务源码版本，因此分别保留其运行容器的 `/app/src`、`/app/prompts` 基线，只叠加 SDK 升级和模型迁移。SDK 安装使用 `--no-deps`，保留其余已安装依赖。全部最终镜像确认从 `/app/src` 加载 editable 项目。

| 镜像 | 远端源码 revision |
|---|---|
| doxagent-v2:codex-gpt6-worker-0.159.3 | b70c54e1769d8f875f48754bfae52858d2fcb1f5 |
| doxagent-v2:codex-gpt6-research-0.159.3 | bd94b34ab0a2f53620389051304f857b69bab15c |
| doxagent-v2:codex-gpt6-scheduler-0.159.3 | 481af01b397134ab8f689e431d040511b9b74484 |
| doxagent-v2:codex-gpt6-guardian-0.159.3 | 2b67dcc8d0a9e4a4e517266625357c05310d19ae |
| doxagent-initialization-repair-agent:codex-gpt6-0.159.3 | 415cd623ba8e9438c2f02b3c196d62eb276f0d31 |
| doxagent-v2:codex-gpt6-template-0.159.3 | 5f9e4a9e63792897b7ef0306cf70365fa8a4994b |

源码 refs 为远端 `codex/gpt6-<worker|research|scheduler|guardian|agent|template>-20261002`，镜像标签含对应 revision 与 Git archive SHA-256。未提交或推送本地/远端已有的其他工作区修改。

运行服务的 Compose 配置追加 `/home/ubuntu/doxagent/deploy/docker-compose.codex-gpt6.yml`。再次部署时必须保留该 overlay，使用各服务原 Compose 文件（见 baseline.json），并显式设置：

```text
DOXAGENT_V2_ENV_FILE=/home/ubuntu/doxagent/.env.v2
DOXAGENT_INITIALIZATION_REPAIR_PRODUCTION_CONTAINER=doxagent-v2-initialization-repair-template
DOXAGENT_INITIALIZATION_REPAIR_SOURCE_REPOSITORY=/home/ubuntu/doxagent
docker compose --env-file /home/ubuntu/doxagent/.env -p doxagent-v2 -f <原配置...> -f /home/ubuntu/doxagent/deploy/docker-compose.codex-gpt6.yml ...
```

部署预检比较实际运行环境与 Compose，除指定模型字段和修复代理镜像字段外没有其他环境差异。替换前 Worker active/queued 均为零；先暂停调用方，再更新 Worker，健康检查通过后恢复调用方。最终五个服务运行且重启次数为零，Worker healthy。

Guardian 模板仍为 created 状态，不运行业务任务。旧模板保留为 `doxagent-v2-initialization-repair-template-before-gpt6-20261002`；旧代理镜像保留为 `doxagent-initialization-repair-agent:before-gpt6-20261002`。构建上下文、各基线源码归档和部署脚本保留在 `/home/ubuntu/.cache/doxagent-codex-upgrade-20261002`；其中受限权限的模板 inspect 备份含环境数据，不应加入仓库或分享。

## 回归与验收边界

相关离线回归：**215 passed，2 failed**。两项失败均在未修改 HEAD 的独立源码下复现：

1. `test_configure_without_new_crawler_starts_default_monitoring`：原默认 crawler 列表已包含 Barron's，测试期望未跟进。
2. `test_failed_worker_turn_persists_bounded_turn_summary`：原失败路径缺少 turn_summary。

模型转换覆盖五种推理强度、新线程/恢复线程及显式自定义 provider 的合同测试已通过。定向 Ruff 与 diff whitespace 检查通过。此次验收确认模型与 SDK 调用兼容，没有对真实研究、消息、交易或策略生成结果作出业务验收结论。
