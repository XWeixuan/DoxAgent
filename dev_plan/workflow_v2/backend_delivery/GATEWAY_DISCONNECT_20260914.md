# Gateway 断开排查（2026-09-14）

时间均为服务器北京时间 UTC+8。只读排查，未尝试登录或更改 Gateway 配置。

- `Jts/launcher.20260913.log` 23:45:06.259：IB GATEWAY RESTART。
- 23:45:07.967：读取 autorestart，随后删除此重启恢复文件。
- 23:45:09：连接 IB 授权服务并尝试认证。
- 23:45:10.376：DISCONNECT_AUTHORIZATION_FAILED。
- 23:45:10.383：IBGateway connection to ccp disconnected。
- 当前 relay 服务 active，监听 172.17.0.1:7496，但 Gateway 127.0.0.1:4002 没有监听。
- relay 日志从本次探测开始明确记录 Connection refused（02:53:06 等），这是观察时间，不是实际断开时间。

结论：自动重启发生在 9月13日23:45:06 附近，重启后恢复登录的授权失败，API 未重新开放。不是此次02点后的前端/API部署造成，也不是 relay 服务停止。日志不能进一步证明二次验证、密码过期、周末强制重新认证或并发登录中哪一种导致授权失败；需要在远程桌面查看 Gateway 登录提示并由用户完成必要认证。

没有保存账户/认证信息或修改登录设置。静态状态探测没有订单接口。
