# DoxAgent MCP 微信通知

在现有 MCP/Tunnel 进程中增加可选工具 `send_wechat_notification`；四个 DoxAgent 数据查询工具保持只读。无新增进程、数据库、索引、告警判断、后台任务或业务 API 改动。

## 工具输入与固定行为

```json
{
  "title": "DoxAgent 巡检完成",
  "content": "巡检范围：全部 ticker\n结论：未发现需要处理的异常。",
  "level": "INFO"
}
```

只接受这三个字段。`level` 必须为 `INFO`、`WARNING`、`CRITICAL`；标题 1–100 字符、正文 1–10000 字符，去除首尾空白后不能为空。标题实际显示为 `[级别] 标题`，正文按纯文本和换行展示。

使用已存在的 httpx，向 PushPlus 官方 HTTPS `https://www.pushplus.plus/send` 发一次 JSON POST，固定 `channel=wechat`、`template=txt`。不传 topic、to、option、callbackUrl、pre；使用服务端保存的本人 Token，Dots 不能覆盖接收人、渠道、模板、地址或凭证。

MCP 标注：新工具 `readOnlyHint=false`、`destructiveHint=false`、`idempotentHint=false`、`openWorldHint=true`；原四查询工具的标注和查询逻辑不变。Dots 自行决定何时通知；宿主仍按其工具确认策略处理外部发送。

成功返回 `data.status=accepted`、`data.delivery=unconfirmed`、`provider_code=200` 和消息流水号 `short_code`。根据 [PushPlus 消息接口文档](https://www.pushplus.plus/doc/guide/api.html)，这仅代表异步受理，不能声称用户已经收到。拒绝返回固定错误码及提供方数字编码；超时/网络/畸形响应返回 `NOTIFICATION_DELIVERY_UNKNOWN`，不自动重试，避免重复推送。工具不回显 Token、请求正文或提供方错误原文。

## 配置与部署

新增服务端环境变量：`DOXAGENT_PUSHPLUS_TOKEN_FILE=/run/secrets/agent_pushplus_token`。没有配置时保持仅四个查询工具；配置后会发现第五个通知工具。Token 文件仅挂载给 MCP，UID 10001 可读、mode 0400，服务器实际路径 `/var/lib/doxagent/agent-mcp/secrets/pushplus-token`。凭证值不进入源码、文档、测试收据或日志。

可在现有完整 Compose 参数后追加 `deploy/docker-compose.agent-mcp-wechat.yml`，设置 `DOXAGENT_PUSHPLUS_TOKEN_PATH` 为私密文件路径，并使用包含本次代码的新 MCP 镜像。保留原 API、Tunnel home、组织/workspace 关联、资源预算和所有生产覆盖文件。本次只更新 MCP 容器；不更新 API、采集、研究或交易服务。

部署后在 ChatGPT 的 DoxAgent 插件设置中刷新工具列表，确认第五个工具出现并供 Dots 使用。示例提示：`完成巡检后，用 send_wechat_notification 将结论发到我的微信，级别 INFO；不要发送完整 Case 正文。`

## 验证记录

本地验证：通知路由/输入边界、三级消息、异步受理、失败/超时不重试、凭证不回显、真实 MCP 协议发现/分发，以及原只读 MCP/Bus 回归合计 **44 项通过**。Ruff 和 diff 检查通过；测试中的 HTTP 发送全部使用 MockTransport，不产生微信消息。

## 生产与真实微信验收

功能提交 `f87bcdfe` 已推送至 origin/main。只更新 `v2-agent-mcp-tunnel`，镜像 `sha256:c2f11b4f5b9b8331b2967720c8b811c34fbd8ea079c3a838c9659ccb06cd7a00`；保留原官方 Tunnel 二进制与所有依赖，仅覆盖 MCP 入口和新增通知模块。远端仓库其他任务的未提交 changelog 保留，使用 fetched commit 的 git archive 打包本次文件。

生产覆盖文件 `/var/lib/doxagent/agent-mcp/wechat-20261111/compose.wechat.json` 已纳入原 release 的完整 Compose 命令；使用既有发布锁和健康回滚，仅新增 secret 挂载。`healthz` / `readyz` 均为 200，控制面轮询正常，容器无重启/OOM。其他 **21 个**已有容器 ID 保持不变，包含 API、采集、研究和 Executor。空闲采样约 88.26 MiB / 384 MiB、0.05% CPU、12 PIDs，仅为一次采样，不代表长期峰值。

在部署后的实际 Linux stdio MCP 会话中，发现五个工具并确认新工具的非只读、非幂等标注。随后**只调用一次**通知工具发送测试 `WX-20261111-01`：

```json
{
  "data": {
    "status": "accepted",
    "delivery": "unconfirmed",
    "channel": "wechat",
    "level": "INFO",
    "provider_code": 200,
    "short_code": "5477e8baa786443f848e8d867c6e6f22"
  },
  "meta": {"submitted_at": "2026-11-10T17:09:50.792549+00:00"}
}
```

北京时间 **2026-11-11 01:09:50**，该工具调用耗时约 1 秒。上述为 MCP 对 PushPlus 接口响应的固定字段投影；`delivery=unconfirmed` 是工具当时的准确状态。之后用户在本轮 ChatGPT 回复 **“已收到，正文清晰”**，确认个人微信实收同一测试编号，单独记录在 `handset-confirmation.json`。没有因收到确认而篡改原接口收据。未调用 PushPlus 开放管理接口、开通 AccessKey 或新增投递状态轮询。

同一生产会话再调用原概览工具（limit=1）成功，原四查询工具保持只读标注。当前 ChatGPT 连接缓存仍可能只显示旧四工具；刷新 DoxAgent 插件的工具列表后即可让 Dots 使用新工具。本轮真实推送来自部署后 MCP stdio 验收，不冒充 Dots 已完成插件刷新后的调用。

证据位于 `artifacts/audits/agent_mcp_wechat_20261111/`：功能镜像与源码哈希、真实 MCP 响应、健康/资源/其他容器未替换检查、44 项测试收据及用户实收确认。服务器完整私密发布记录在 `/var/lib/doxagent/agent-mcp/wechat-20261111/`；其中 state/compose.before 不作为公共交付附件。

如需撤回通知能力，停用该工具或恢复 MCP 上一镜像并移除微信 secret 的覆盖即可；无需回滚业务库、API、研究或交易服务。原四工具仍可继续使用。
