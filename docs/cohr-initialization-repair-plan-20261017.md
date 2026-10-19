# COHR 原节点恢复方案

日期：2026-10-17。用户已授权实施、部署并从阻塞处续跑，禁止新建初始化。

## 业务判断

- 初始化输入、cutoff、成功的 D1/CDECR/O2/D2 和成功 Worker 输出是可复用资产，不能因为守护失败而重跑。
- D3 Shell 并发是合理的；一项失败时提前关闭兄弟仍在使用的 HTTP client 不合理。阶段等待全部已启动项结算后传出原始失败。
- MCP 注册工具不应初始化尚未调用的全部 HTTP/TLS 客户端。生产实测 128 个工具注册创建 59 个 HTTP client/SSL context，registry 2579ms，其中 SSL 2508ms；先消除这部分重复冷启动。
- 20 秒是应用设置的 required MCP 启动预算，不是业务质量条件；D3 调为 60 秒，同时记录所有节点的模块导入/应用构建/总启动时间。W3 保留 90 秒。
- 外部模型登录的短期 access token 与内网互信是两件事。不能修改发行方的 token 期限。长期复制同一 refresh token 到闲置 repair 模板和各 incident，会失去单一刷新所有者，属于不合理的认证生命周期。
- 生产已有持久登录所有者 `/data/codex-home`。Guardian 从其当前认证发布仅含 access token/account id 的快照给隔离 Repair Agent，后者采用 SDK 支持的 external-token 登录模式；不向 Agent 分发 refresh token，不增加共享 CODEX_HOME 或 Worker MCP 权限。
- 人工恢复基础设施故障应继续同一 repair round，并记录原因和原失败；不能消耗新业务修复预算或假造成功。

## 实施与验证

1. 延迟 HTTP client 构建，回归注入 client、首次请求及并发首次访问。
2. D3 所有并发聚合等待兄弟结算；回归一个基础设施失败、其他项延迟成功及保留原始异常。
3. 加 MCP 启动诊断及 D3 60 秒预算；生产同镜像 stdio initialize/list_tools 前后测量，不调用外部数据接口。
4. 认证快照只由 Guardian 发布，SDK external login 使用当前 access token；回归禁止 refresh token 分发、快照更新与同账号约束。
5. Guardian 基础设施重试耗尽同步 round 状态、保留各次日志并记 issue；提供受身份/状态约束的同轮 retry-agent。
6. 仅将上述代码部署到对应服务；新镜像保留现有每服务实际基线，不把本地其他未提交工作带入生产。部署前快照相关 SQLite；不重启其他业务服务。
7. 对账 S0001/S0002/S0004 原 job，并明确重新开放失败 S0003 原执行预算；沿用原 run、owner workspace、冻结请求及成功快照。在原 initialization 上续跑。
8. 验证 D3、O4、activation、bus/runtime ready 和 incident 终态；保存 before/after 及实际问题，不把离线测试视为生产成功。

官方认证依据：https://learn.chatgpt.com/docs/auth 和 https://learn.chatgpt.com/docs/auth/ci-cd-auth 。
已读取已安装 SDK 0.159.3 的 chatgptAuthTokens 登录协议；使用 SDK/app-server，不自行调用 OAuth token endpoint。
