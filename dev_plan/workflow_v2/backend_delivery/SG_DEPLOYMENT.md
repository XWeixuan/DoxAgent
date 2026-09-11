# DoxAgent V2 新加坡部署记录

更新：2026-09-10。新加坡部署已启动，已完成必要基础设施、鉴权页面、投影、真实 Codex SDK 和 IBKR 只读验收；不等同于完整业务研究或交易成交验收。

## 目标与部署入口

- 新加坡：`doxagent-sg` / `43.163.67.97`，用户 `ubuntu`，目录 `/home/ubuntu/doxagent`。
- 公网：`https://agent.doxatlas.com`。Cloudflare A 记录已改为新加坡，DNS only。
- 香港 DoxAgent V2 已停止并取消容器自动重启；香港 Gateway 自动启动已关闭。
- DoxAtlas 留在香港，根域及 www 的 DNS 未改动。不部署 V1。
- SSH 配置见 `docs/ssh-connection-guide.md`。密钥及密码不纳入版本管理。

## 已完成及证据

1. 远端仓库 git pull 基线：`dffbf64c11298fc3e2d346685ecba5ceac3d9db8`，另同步本次必要部署补丁；仅比较 Git HEAD 不代表镜像包含全部补丁。
2. 香港 `.env`、`.env.v2` 已复制，权限 600；远端监测别名及目录改为新加坡。未变更业务环境/账户绑定语义。
3. 香港停止写入后备份完整 V2 volume，恢复至 `doxagent-v2_v2-data`，保留 `/data` 工件路径、数据库及 Codex 会话。
   备份 SHA-256：`74c648ac4fc8f4bae9ff83d9c06e60ab8cd864afb9b684f978dd67aefdb563d1`，目标复核一致。
4. 八个数据库迁移成功；备份目录 `/data/backups/20260910T053607491743Z`。
5. 后端、前端生产镜像构建通过；后端 IBKR 层 `doxagent-v2:server` 含官方 ibapi 10.49.2。
6. v2-api、v2-web、codex-worker 启动健康。Gateway 鉴权后其余八个常驻服务均已启动，共 11 个常驻容器；迁移容器成功退出。
7. 新加坡真实 Codex SDK 最小请求返回 `TurnStatus.completed` / `SG_OK`。证据 `/home/ubuntu/doxagent-stage/sdk-probe.log`，未调用交易工具。
8. 切 DNS 前定向新加坡的真实 TLS 检查：页面 HTTP 200、匿名 Auth API 401，证书验证通过。DNS 切换后 Google DNS 返回 43.163.67.97，公网 HTTPS 200。
9. 本地针对监测界面默认值的必要测试 6 passed，远端 TypeScript/Vite 生产构建通过。没有运行广泛回归。

## Compose 运维

```bash
cd /home/ubuntu/doxagent
sudo docker compose -p doxagent-v2 -f docker-compose.v2-production.yml -f deploy/docker-compose.server.yml ps
# 完成 Gateway Paper 登录和只读账户核验后启动全拓扑
sudo docker compose -p doxagent-v2 -f docker-compose.v2-production.yml -f deploy/docker-compose.server.yml up -d --no-build
```

服务器覆盖使用已恢复的 external volume，不要运行 down -v。生产镜像和前端镜像分别为 `doxagent-v2:server`、`doxagent-v2-web:production`。后端修改后需重建基础镜像及 IBKR 层；仅 git pull 不会更新已运行镜像。

## 桌面与 IB Gateway

已安装 XFCE、xRDP、官方独立版 Gateway。用户 `doxagent-desktop` 沿用既有桌面凭据。
Gateway 的 `jtsConfigDir` 明确指向用户可写的 `/home/doxagent-desktop/Jts`。

本地建立 SSH 隧道后打开远程桌面：

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:14389:127.0.0.1:3389 doxagent-sg
mstsc /v:127.0.0.1:14389
```

Session 选 Xorg。Gateway 使用 Paper 登录，API 4002；初始保留 Read-Only API。本机 `.tmp/sg-deploy/DoxAgent-SG.rdp` 是连接文件，不会自动维持 SSH 隧道。

容器通过 `host.docker.internal:7496` 的私有 bridge relay 访问 Gateway 4002。xRDP 只监听远端 loopback 3389；4001/4002 对非 loopback 的入站连接被主机规则阻断。相关单元：`xrdp`、`doxagent-ibkr-paper-relay`、`doxagent-broker-firewall`。

迁移保留历史 profile `hk-paper` / revision `ep_e1d7c65e0656af2e6c4923b1`；该名字不代表连接香港，网络目标使用当前容器宿主机。不得通过改名破坏不可变引用。Live 不做下单验收。

## TLS 与诊断

只迁移 agent.doxatlas.com 的证书、续期配置和对应 ACME 账户。Nginx 配置 `/etc/nginx/conf.d/doxagent.conf`，上游 loopback 8082，沿用 SSE 配置。

```bash
sudo nginx -t
sudo systemctl status xrdp doxagent-ibkr-paper-relay certbot.timer --no-pager
sudo ss -lntp
```

管理日志 `/var/backups/doxagent-sg/`，迁移日志 `/var/tmp/doxagent-sg-migration.log`。配置和备份 staging 含机密，不得提交或通过公共 Web 暴露。

## 最终验收与业务边界

2026-09-10 14:05（北京时间）验收：

- SSH 链路重试恢复，本地 14389 隧道收到有效 RDP 协议响应，用户已完成新加坡 Gateway 登录。可使用 `deploy/connect-sg-desktop.ps1` 保持隧道并在断线后重试。
- 唯一 executor 只读 probe：PASS，Paper 账户匹配 DU***665，持仓同步 complete，MU 合约与实时报价正常，orders_submitted=0。证据 `/data/operator/sg-ibkr-executor-probe.json`。
- IBKR Data MCP 真实 stdio 握手及 `market_daily_ohlcv`、`market_quote_snapshot` 均 is_error=false。证据 `/data/operator/ibkr-acceptance/run-sg/acceptance.json`。
- 四个投影源 head/checkpoint 分别为 research 281/281、initialization 49428/49428、bus 26/26、runtime 13/13；gaps=[]，freshness=FRESH。位置是验收瞬时值，后续允许正常增长。
- control、delivery、executor 的数据库心跳均为当前时间；11 个常驻容器运行，配置健康检查的 API/前端/Codex Worker 均 healthy。交易 jobs/executions/lots 均为 0。
- 用户已登录页面的 Auth、capabilities、read-context、Overview 三个聚合 API 均 HTTP 200。刷新后所有“数据源同步延迟”横幅消失；重新加载后消息监测和 Paper 入口可用，Live 仍禁用。
- 此次延迟由部署阶段 projector 尚未运行导致。ReadStore.freshness 要求所有数据源无错误、checkpoint 追平、检查时间小于 30 秒且无 gaps；浏览器刷新不能代替投影进程。Overview 按设计不建立 SSE 连接，所以这次提示不是 SSE 故障。
- Nginx SSE 禁用缓冲和缓存、读取超时 3600 秒已配置；本轮没有创建活跃 ticker 来做增量 SSE 业务事件验收，不宣称已完成新加坡全研究生命周期验收。
- MU 保留香港旧初始化失败状态：最新 run `init-mu-1f3e9130ae304b01a7fdf2991a35e028` 在 2026-09-09 18:16 UTC 耗尽 D1 重试，节点记录为 Codex 403 Forbidden（HKG）。新加坡真实 SDK 最小请求已通过，但未自动重试该业务运行；页面“重试失败步骤”供用户显式发起。
- 历史统计“覆盖未知/未记录”与实时源新鲜度是不同维度；未补造历史消费、消息或收益数据。
- 仅 Paper Gateway 已配置；Live 未启用，未发出任何订单。只读核验不证明 Paper 下单/成交能力，Gateway Read-Only 开关需按正式交易启用流程处理。

部署记录、changelog 及服务器覆盖文件同步到远端仓库；本次补丁尚未提交或推送 Git。后续更新注意保留或先提交这些补丁。
