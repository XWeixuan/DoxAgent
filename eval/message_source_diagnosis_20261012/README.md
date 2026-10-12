# 2026-10-12 消息源只读诊断证据

生产采样约 11:22–11:34 UTC（北京时间 19:22–19:34）。脚本只执行 SQLite 只读查询、现有管理 GET、有限 HTTPS GET 和主机只读命令；未导航浏览器、调用 probe/open/close/activate、重启服务或发布历史消息。

- `bus.json`：当前 PollState 与队列表结构。
- `site.json`：策略、组合健康、就绪与最近事件。`body_48h` 使用文本时间比较，统计请以 `details-site.json` 的时区归一化统计为准。
- `details-site.json`：最近真实访问事件、Identity 绑定、使用 `julianday` 归一化的精确近 48 小时正文结果。
- `details-bus.json`：生产采集入口参数、非终态 runs 和 outbox 汇总。聚合扫描较慢，不建议作为常规健康探针。
- `network-confirm.json`：第二轮出口对照，以实际 Registry listener 映射为准；18080 是 jp-standard-6，18084 是 server-direct。
- `network.json`：首轮原始记录保留；其中误写的 JP-slot 实际测试 18084/direct，不可用作日本/德国第二出口证据。正确对照见 network-confirm.json。
- `host.json`：脱敏代理配置、容量/内存/容器采样、Bus 与 Control SQLite 锁异常、代理连接失败日志。不包含节点密码、管理令牌或订阅 URL。
- `storage.json`：现有只读 storage_report 输出；业务库与保留备份的文件大小。
- `yahoo.json`：Yahoo recipe 当前 CONTENT_ERROR/http_404 与站点健康；NCP/RSS 全局代理配置和连接失败另由本次命令核查。

采样非原子，跨文件数分钟的计数变化正常。状态计数不是 HTTP 请求数，正文 outcome 统计不是完整的消息覆盖率。未对网站进行新登录/挑战实验；网页拒绝证据取自生产原 owner 的持久记录。

`details.py` 初次使用了不存在的 browser_identities 表，未写入数据库；改为真实 revisions/heads 联结后重新采样成功。首次旧 snapshot 聚合输出被截断，不作为保存的完整证据；本目录仅将确认完整的工具输出落盘。
