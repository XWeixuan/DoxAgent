# 香港 V2 部署记录与操作

部署源：`517dce51`，远端 `/root/doxagent` 已执行 `git pull --ff-only`。本轮部署附加补丁是 IBKR 数据客户端允许 `host.docker.internal`，以及 `deploy/` 下的香港 overlay/镜像层；正式版本应将这些补丁提交后再同步，不能以 Git HEAD 代替运行源码证据。

## 配置与拓扑

- `.env` 按用户要求从本机同步，远端权限 0600；实际 V2 使用 `.env.v2`，保留业务 provider 配置、正式 Auth 项目，移除演示环境变量并覆盖 Linux `/data` 目录。秘密不写入本文件。
- Compose 项目 `doxagent-v2`；基础文件 `docker-compose.v2-production.yml` 加 `deploy/docker-compose.hk.yml`；运行镜像 `doxagent-v2:hk`。启动脚本 `deploy/hk-cutover.sh`，不得混用 V1 Compose。
- `deploy/Dockerfile.ibkr` 基于正式镜像安装用户已安装的官方 Python API 10.49.2 源包并执行依赖检查。私有构建上下文需包含 `pythonclient/setup.py` 和 `pythonclient/ibapi`，源包不得提交到仓库。
- 旧 V1 的 dashboard/runtime-scheduler/monitoring-poller/revenue-auditor 停止且 restart=no；DoxAtlas 项目不改动。
- 旧配置及停写数据备份目录 `/root/doxagent-deploy-backup`，V2 使用独立 `doxagent-v2_v2-data` 卷。

## XFCE / xRDP / IB Gateway

独立无 sudo 桌面用户 `doxagent-desktop`。xRDP 仅监听 `127.0.0.1:3389`。本地以以下 SSH 隧道连接：

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:13389:127.0.0.1:3389 doxagent-hk
mstsc /v:127.0.0.1:13389
```

本轮已建立隐藏 SSH 隧道；不要再启动相同端口的第二个隧道。RDP 账户密码只在本机 `.tmp/hk-deploy/desktop-credentials.txt`，不在文档中。用户自行处理桌面证书提示及登录验证。

官方 Gateway 安装于 `/opt/ibgateway`，XFCE 登录自动启动，也可双击桌面 IB Gateway。首次先登录 Paper，API 端口 4002，保留 Read-Only API 完成无订单验收。桌面断开连接不会退出 XFCE；不要选择注销或关闭 Gateway，否则 API 会断开。重启服务器后仍需人工登录和二次验证，未配置保存 IBKR 密码或绕过鉴权。

`doxagent-ibkr-paper-relay.service` 将 Docker 私网 `172.17.0.1:7496` 转发到宿主 `127.0.0.1:4002`，只允许 172.16.0.0/12 来源；没有将 API 暴露公网。Data MCP 使用 host.docker.internal:7496 / client_id=71；交易 profile 应使用不同 client_id，且固定真实账户 ID。Data MCP 不暴露下单工具。

Gateway 官方安装入口：https://www.interactivebrokers.com/en/trading/ibgateway-latest.php
官方 API 设置说明：https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/

## 上线验收边界

远端 IBKR 握手、账户发现、MCP 行情和执行器只读探测必须在用户登录后完成。不得以端口监听或包导入成功代替；不得为验收创建订单。Paper/Live profile 在账户身份核实前不导入或绑定，Read-Only API 关闭和真实交易放行属于后续显式操作。

本文件将在本轮部署结束时追加实际运行结果。

## 已完成的线上结果（2026-09-09）

- 11 个 V2 常驻服务已启动，API/web/Codex Worker healthy；迁移任务成功退出。公网 https://agent.doxatlas.com 已切换为 V2，主页/healthz/auth-config 为 200，匿名 auth/me 为 401。
- Codex Worker readiness：ready=true、authenticated=true。未运行新的模型研究。
- read projection 四个来源 head/checkpoint 一致、gap=0。实际容器 IBKR 源文件 SHA256 与本地补丁一致；官方 ibapi 10.49.2 已安装，protobuf 5.29.5 依赖检查通过。
- runtime 的 te_jobs、te_executions、te_profiles 均为 0；没有下单。旧 V1 四服务均 exited + restart=no。
- 停写 V1 数据压缩备份约 58 MB；本地原始 .env、远端原始 .env、Nginx 原始配置均按上述位置保留。本机 RDP 隧道监听 127.0.0.1:13389，服务端 RDP 只监听 localhost。
- Gateway 已安装并设置桌面自动启动；截至本条记录，用户尚未完成 Gateway 登录，4002 未监听。IBKR 的实际 API/账户/行情验收及 profile 绑定仍待完成，不能声明交易链路已就绪。
- 本轮仅运行一个直接相关的 host 地址边界回归测试，通过；未扩展回归或发起订单。

升级必须设置 `DOXAGENT_V2_IMAGE=doxagent-v2:hk`，先构建基础镜像及 IBKR 层，再使用同一项目和两个 Compose 文件停止写入、迁移、启动。远端 `/root/doxagent-deploy-backup/build.log`、`cutover.log` 为本轮构建和迁移日志；本机 `.tmp/hk-deploy/public-http.json`、`projection.json` 为不含密钥的验收证据。

2026-09-09 修复香港 IB Gateway 启动权限：root 安装保留了 /root/Jts，桌面用户无法创建 launcher.log。vmoptions 显式设置 -DjtsConfigDir=/home/doxagent-desktop/Jts，目录归桌面用户且权限 0700；重启后 launcher.log 正常创建，日志系统初始化成功。

## Gateway 登录后的最终验收

2026-09-09：真实 Paper 账户 DU***665，Gateway API server_version=225、ibapi=10.49.2。Docker 私网经回环 relay 握手、服务器时间、MU conId=9939 均通过。

正式 stdio Data MCP 以隔离的运维 observation 目录运行，initialize/list/call 通过，未绕过节点工具授权或生成模型研究。market_daily_ohlcv 由 IBKR primary 返回 21 条日线，无 provider fallback；market_quote_snapshot 返回实时 market_data_type=1 的 bid/ask，观察证据在 `/data/operator/ibkr-acceptance/run-hk/acceptance.json`。

执行配置 `hk-paper`，不可变 revision `ep_e1d7c65e0656af2e6c4923b1`，端点 host.docker.internal:7496，client_id=81（Data MCP 基础 client_id=71）。该 profile 来自真实账户发现，没有伪造 Live 账户。未绑定 ticker、未启动研究或交易生命周期。创建标的前，可按正式运行手册将该 revision 绑定到目标 ticker 的 PAPER_TRADING；不使用全局 activate-profile。

唯一执行器只读 probe：PASS，持仓/会话成交同步 complete=true，当前持仓为空，SMART 与 OVERNIGHT 合约、market rules 和实时 bid/ask 均正常。证据 `/data/operator/ibkr-executor-probe.json`，orders_submitted=0。探测期间暂停空闲执行器，结束后已恢复。

Gateway 实际监听 *:4002，因此新增持久 systemd 防火墙 `doxagent-broker-firewall.service`，IPv4/IPv6 均拒绝非 lo 对 4001/4002 的直接访问，Docker 仍通过已限定私网的 relay 连接。xRDP 仍仅绑定回环地址。

**上线状态：V2 应用与真实 Paper 数据/执行 API 部署验收完成。没有执行成交验收，也未放行交易。Live 会话及 Live 精确账户 profile 尚未配置；不能将 Paper 验收当作 Live 验收。** 如需 Live，必须完成真实 Live 登录和账户类型核验，单独配置私网端点及 LIVE profile，再绑定指定 ticker；不从 DU 账号推导 U 账号。

香港 overlay 现对所有 backend 服务固定 `doxagent-v2:hk`，防止运维时遗漏环境变量而退回未安装 ibapi 的基础镜像。所有重要变更已追加 changelog。
