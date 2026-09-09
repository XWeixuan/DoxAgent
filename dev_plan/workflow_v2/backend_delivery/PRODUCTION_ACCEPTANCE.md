# V2 生产接线验收记录

日期：2026-09-09（日志使用 UTC）。状态：**生产代码和部署资产已交付，本地拓扑验收通过；正式用户登录后的 API / SSE 及断线重连验收已通过；不等同于完整研究流程或券商成交业务验收。**

## 本次交付

- `docker-compose.v2-production.yml`：独立 12 服务拓扑，包含显式迁移任务，不依赖 V1 overlay。
- `Dockerfile.v2` 与 `frontend/v2/Dockerfile`：正式后端/Worker 及前端静态构建；依赖锁定，镜像不包含旧 dashboard 或 integration Python 模块。
- `.env.v2.example`、`production_v2` 的 check / migrate / bind-profile 命令、`PRODUCTION_RUNBOOK.md`、`scripts/verify_v2_production.py`。
- 前端生产入口固定 `createAuth()`，无 demo/mock/录制分支、测试 token 或 LocalAuth；启动表单按目标 ticker 查询正式执行能力。初始化 CLI 删除自定义 adapter-factory 注入入口。
- Runtime 的受管装配使用独立 delivery；executor/delivery 心跳参与 Paper/Live capabilities，控制/消费与旧 intent pin 规则保留。
- Policy 首次激活证据、完整消费覆盖与捕获追平后的 ACTIVE 重算保留；没有把新生产空库填成录制样本。
- 读库迁移修复为事务内幂等操作，重复迁移不重置 generation 或数据；部署迁移与所有 managed writer 互斥。

## 实际已通过

| 项目 | 证据与边界 |
| --- | --- |
| Compose | `config --quiet` 通过；独立项目 `doxagent-v2-acceptance` |
| 前端构建 | Docker 内 frozen pnpm install、schema、TypeScript、Vite production build 均通过 |
| 后端镜像 | 从源码构建成功，生产容器导入检查确认 `doxagent.integration_v2`、`doxagent.dashboard_api` 不存在 |
| 迁移/重启 | 首次建库、已有 v2 读库迁移以及完整 stop/up 均完成；v2-migrate exit 0，11 个常驻服务运行 |
| HTTP | 8082 静态主页与 healthz 200，正式 Supabase config 200，未登录 auth/me 401；未使用 LocalAuth |
| 浏览器 | 正式登录页在 1262px / 1559px 可见，无横向溢出；已使用正式账户验证 Overview 与 MU 消息总线页面 |
| Codex | 正式账户 readiness 200、authenticated=true；当前所需 gpt-5.6-luna / gpt-5.6-sol 均可用，没有调用模型生成研究 |
| 投影 | research / initialization / bus / runtime 的 head=checkpoint，无 source error，gap=0 |
| 唯一执行器 | 第二个同库 WriterLock 被 EXECUTOR_ALREADY_RUNNING 拒绝；control、delivery、executor 心跳存在 |
| 无订单 | 新持久卷中 te_jobs、te_executions、te_profiles、Policy 消费均为 0；本轮没有向 broker 发 Live 或 Paper 订单 |
| 聚焦回归 | 16 个必要测试通过：Policy 覆盖 9、API/鉴权 3、executor/Live 隔离 2、迁移幂等 1、创建前 Paper/Live 能力 1；未跑全套回归/压力测试 |

当前运行页：`http://127.0.0.1:8082`。旧 5177/8097 与 5178/8098 的已识别模拟/录制服务均已停止。

## 正式登录与 SSE 补充验收（2026-09-09 08:30–08:35 UTC）

1. 用户自行登录正式 Supabase 账户。auth/me、capabilities、Overview read-context、metrics、status、tickers 均返回 200，页面正常；未提取或保存用户密码、Bearer token。
2. 暂停初始化 worker 后，通过正式 UI 创建 MU / MESSAGE_MONITORING，POST 返回 202；MessageBaseline 返回 200，前端携带基线 Last-Event-ID 建立 SSE。Nginx 首次流连接返回 200、90 字节（持续心跳的 HTTP chunk 数据）。
3. 中断并重启 API 后，前端经历预期的短暂 502，自动对同一个 view_id 重连；重启前后 SSE 均返回 200、18 字节（一个心跳 chunk），没有重新伪造基线或身份。当前为空消息范围，此验收证明连接、心跳与持久游标重连，未声称验证带业务 delta 的无重无漏。
4. 通过正式 DELETE 移除本轮 MU 生命周期，返回 202，Overview 恢复“暂无监控标的”；审计记录保留，随后恢复初始化 worker。没有执行模型研究。运行库 te_jobs、te_executions、te_profiles 均为 0。
5. 首次常规 API 重启等待现有 SSE 结束；关闭页面流后完成。随后使用 2 秒停止期限做故障恢复验收。部署时仍按 Compose 的 60 秒停止宽限执行，不将本次故障注入参数改成生产默认。

## 生产业务验收边界

1. 本地卷为真实生产服务的冷启动卷，无录制数据、无交易 profile。没有执行完整新 ticker 模型研究、真实来源获取、Paper 成交或 Live 放行；部署资产可用于远端配置与迁移，这些外部业务验收仍需按运行手册独立完成。
2. 远端正式业务库/工件和 broker 的具体地址、账户绑定、权限、TLS 证书须由部署环境配置；本轮没有改动远端服务或下单。

## 证据与回退

本机 `.tmp/v2-production-acceptance/` 保存 `release-final-build.log`、`release-startup.log`、`services.jsonl`、`images.txt`、`topology.json`、`http.json`。`runtime.env` 为私密运行配置，不得上传/提交。Codex auth.json 仅放在本地持久卷，不在镜像中。

首次重复迁移暴露旧 ReadStore 的版本行冲突，已修复。故障后仅恢复派生读库，来源为 `/data/backups/20260908T182539833848Z/read.sqlite3`，故障现场保存在 `/data/backups/read-before-idempotence-repair.sqlite3`；业务源库未回退。后续重启迁移成功。

旧演示/录制入口的批量删除被自动审批拒绝后，改为归档至 `.tmp/retired-v2-entrypoints-20260909/`；它们已退出源码树、生产镜像和运行服务，历史验收资料保留。以前的联调说明属于历史记录，不能继续用其旧命令启动当前版本。

本轮补充证据：`.tmp/v2-production-acceptance/authenticated-sse.log`、`authenticated-http.log`、`http.json`。不含认证请求头或密码。
