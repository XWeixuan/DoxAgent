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

生产部署、真实单次发送及个人微信实收确认结果将在下文追加；不能以接口受理代替实收验收。
