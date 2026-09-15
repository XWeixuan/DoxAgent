# Yahoo NCP 浏览器 transport 最小验证（本地，2026-09-15）

## 修改范围

- Yahoo 新闻 adapter 使用 `curl_cffi.requests.AsyncSession(impersonate="chrome")`，不改新闻字段解析、正文补全或消息去重。
- 每个进程仅一个 Yahoo transport/session/cookie jar，所有 ticker 复用。Session 固定在专用事件循环线程，兼容 Runtime 的短生命周期调用 loop；退出进程时关闭，不随某 ticker/poll 关闭。
- NCP、Query1/Query2 和既有 Reader fallback 共用单请求并发限制；请求启动间隔至少 1.5 秒，额外随机 stagger 0–0.5 秒。
- 普通 polling 默认 20 条，旧配置限定在 10–20 条；首次 bootstrap / 有明确窗口的 gap recovery 为 100 条。调度器通过 PollContext 显式传入模式，不用 poll 次数或 payload 噪声推断恢复。
- 任一上述请求 HTTP 429 打开进程级 circuit，立即失败并保留真实失败状态，不重试、不再尝试其他 endpoint。后续 poll 在 circuit 内不发 HTTP 请求。
- 退避为 5min → 15min → 30min，30min 封顶；支持 Retry-After 秒数与 HTTP-date，取至少本地退避时长、不早于服务端指定时刻。过期后单并发试探，HTTP 成功重置退避。
- NCP 仅在 HTTP 404/405/410 或不可识别 JSON/schema 时进入 Search fallback；401/403、429、5xx、网络/超时错误不使用 Search 绕过。可识别的空新闻列表仍是有效 NCP 响应。

## 验证结果

- 本地定向测试覆盖 Session/连接配置、cookie 跨调用 loop 复用、多 ticker 串行/stagger、Retry-After 两种形式、阶梯退避与半开成功恢复、禁止限流 fallback、endpoint/schema fallback、普通/bootstrap/recovery 条数。
- 26 项定向 provider/transport 测试通过，Ruff 检查通过。
- 同一本地出口、单次 NCP 20 条请求：原 httpx 路径 HTTP 200（约 0.28s），Chrome impersonation HTTP 200（约 0.48s），新路径解析出 20 条新闻。
- 因旧路径本地也成功，本次不能归因线上 429 为 TLS 指纹，不能宣称该补丁已修复线上；线上 IP/出口、频率、cookie、地区差异仍需后续受控验证。
- robots.txt 返回 200，列有 `Disallow: /xhr`。这是自动化使用限制信号，已停止进一步真实请求；该接口为非公开接口，不能把技术可用当作允许自动化使用或稳定服务承诺。

## 上线与覆盖边界

- 仅本地改动及必要测试，未 commit/push、远端同步、build/restart 或 DB 清理；不纳入主对话正在修改的内存治理/维护代码。
- 进程级 circuit 在进程重启后重置；不同进程或主机之间不共享。本次不新增跨进程协调服务，也不修改独立正文补全 worker 的 Yahoo transport。
- 20 条新闻的正常 polling 不等于完整增量覆盖；已知 gap 的 100 条同样不是分页完整性证明。不能将 Search/Reader 的 PARTIAL 或历史 coverage=UNKNOWN 伪装 COMPLETE。
