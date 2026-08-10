# IBKR TWS 底层只读链路

本阶段只证明 `本机 TWS socket -> IBKR 官方 Python API -> 只读数据请求`，不接
ToolRegistry，不暴露 MCP，也不包含下单、账户、持仓或成交接口。只有本页的真实 smoke
全部通过后，才把现有三个 `ibkr.*` semantic tools 从 Client Portal HTTP 迁移到 TWS，随后
执行 Data MCP 验收。

## 本地验收结果（2026-08-10）

- `127.0.0.1:7496` 已由本机 TWS 监听。
- IBKR 官方 `ibapi 10.49.2` 已安装到项目 `.venv`。
- 握手成功：server version 225；服务器时间可读。
- MU 合约解析成功：`conId=9939 / SMART / NASDAQ / USD`。
- MU 一个月日线成功：22 条 OHLCV/WAP/bar_count，最新 `as_of=20260807`。
- snapshot、delayed snapshot、delayed-frozen snapshot 和 15 秒 streaming 均返回零 tick，且没有
  354 等请求级拒绝码。因此当前结论是 TWS socket 与历史行情可用，但本账户会话的报价流
  不可用；工具以 `market_data_unavailable / degraded` 返回，不使用历史收盘价伪装 snapshot。
- 实际 stdio Data MCP 已完成 initialize/list/call：只暴露 guide/read 和三个授权 IBKR 工具；
  contract/history 成功，分别生成一个表格 O# 与一个 time-series O#，snapshot 稳定降级。

## 本机准备

1. 以管理员身份运行官方 `TWS API Install 1049.02.msi`。当前 TWS 为 10.49，使用同系列
   API 可避免协议版本错配。
2. 用 DoxAgent 虚拟环境安装官方 Python client：

   ```powershell
   Set-Location 'D:\TWS API\source\pythonclient'
   C:\Users\WEIXUANXIE\Desktop\DoxAgent\.venv\Scripts\python.exe setup.py install
   ```

3. 在已登录 TWS 的 `Global Configuration -> API -> Settings` 中保存以下设置，并确认设置
   窗口关闭后端口确实监听：

   - Enable ActiveX and Socket Clients：开启
   - Socket Port：7496
   - Allow connections from localhost only
   - Read-Only API：开启

   ```powershell
   Get-NetTCPConnection -State Listen -LocalPort 7496
   ```

## 底层验收

先仅验证握手、服务器时间与 MU 合约解析：

```powershell
uv run python scripts/smoke_ibkr_tws.py --symbol MU --contract-only
```

再验证 snapshot 和一个月日线：

```powershell
uv run python scripts/smoke_ibkr_tws.py --symbol MU
```

通过标准：输出 `status=succeeded`；handshake 有 `server_version`；服务器时间可读；MU 至少
返回一个 SMART/STK/USD 合约；snapshot 有有效 tick；history 有 bars。若仅行情步骤返回 354
或空数据，应记录为“socket/合约链路通过、行情 entitlement 未通过”，不能把整个底层链路
误报为未连接。配置默认使用 market data type 3；账户有实时权限时 TWS 仍优先返回实时数据，
无实时权限时才尝试延迟数据。

## 当前启用边界

- 本机 `.env` 已启用 `IBKR_TWS_ENABLED=true`，并固定 localhost/7496/read-only 数据路径。
- 三个既有 `ibkr.*` ToolRegistry 入口已迁移为官方 TWS API，因此旧直接调用和 Data MCP
  共用同一实现；没有订单、账户、持仓或成交工具。
- 非本机或未启用环境仍把三个 IBKR contracts 标记为 unavailable，不向 agent 暴露。
- Data MCP 的 attempt capability、O4 allowlist、O# 清洗分段与 cited-only promotion 逻辑不变。
