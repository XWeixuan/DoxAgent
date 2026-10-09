# Reuters 主出口切换（2026-10-06）

按用户要求，仅切换 Reuters 的访问组合；未构建镜像或重启服务。

- 原主组合 `reuters-native-direct` → `digitimes-1`，服务器直连 `43.163.67.97`，已被风控。DIGITIMES 对该组合早已禁用，实际主身份为 `digitimes-nl-1`。
- 新主组合 `reuters-native-de` → 已有 `digitimes-de-1`，External Chrome 153 + CDP，固定 `de-standard-1`（德国标准 IEPL 专线 1）。经实际代理请求 ipify 确认出口 `205.198.126.113`。
- Reuters Registry revision 8 → 9，禁用原直连组合，新增德国组合 priority 0，并通过 CAS activate 指定为活动组合。新消息与正文继续使用同一套组合。
- 移出 revision 8 中两个已禁用且引用已禁用 Identity 的失败 canary（`reuters-de2-1`、`reuters-tr1-1`）：它们使 Registry 重新提交校验报 unknown browser identity。原历史、Profile、Identity 均未删除。
- 确认无人工维护会话后，对无启用站点引用的 `digitimes-1` 调用 owner drain，结果 stopped=true，正常退出并保留 Profile/Cookie，为德国浏览器释放第六个进程名额。其他浏览器未重启，DIGITIMES 策略未改。
- 新德国浏览器 instance `916734b760404c92b38ed856391ff95f`，PID 3517502，VNC 5923，正式 Chrome；与 DIGITIMES 已有德国备用身份共享 Identity，由身份预算隔离页面任务。

## 验收边界

生产真实搜索尝试已走新组合，但返回 `ACCESS_CHALLENGE`（10:15:37 UTC）；本次额外 PROBE 因业务并发返回 site_queue_timeout，不冒充成功。切换出口已完成，不宣称登录、challenge 或持续轮询恢复成功。用户在“消息源登录维护”刷新后选择 Reuters 的主 Profile `digitimes-de-1`，人工完成 challenge/登录及验证。无需操作原 `digitimes-1`。

## 回滚

通过 Registry CAS 提交新 revision：禁用 `reuters-native-de`，启用 `reuters-native-direct`；再通过组合 activate 选择原组合。原直连 Profile 未删除、出口绑定未修改，按需可重新启动；若六个正式浏览器均占用，须先正常 drain 无维护/业务占用的德国实例。不要直接恢复 revision 8 的两个无效 canary 引用。原直连仍有风控，回滚不代表可用。
