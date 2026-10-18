# Site Login Maintenance：独立登录会话与 Challenge 验证修复

## 真实故障顺序与根因

本次读取生产操作记录、SQLite 会话/任务、原生 Chrome 精确 target 及渲染后的 DOM。不是全节点代理失效，也不是上轮网络命名空间错绑复发；Site/Supervisor 当前网络 namespace 相同。

1. 2026-10-18 20:10:50 UTC，Reuters `OPEN_LOGIN` 返回 `ProfileUnavailableError`。`prepare_login` 发现未结清的 Challenge Task 就主动拒绝普通登录。这是独立错误，早于 Viewer 冲突；用户反馈的“先登录失败，再尝试 Challenge”与记录一致。
2. 20:11:26，Reuters `OPEN_CHALLENGE` 成功取得 Viewer；20:12:05 的 `VERIFY_CHALLENGE` 却在 `CHECKING_BODY` 抛异常，整个操作失败，原页已解除挑战的结果没有提交。
3. 20:12:59，`CLOSE_LOGIN` 因当前会话是 Challenge 而拒绝；20:13:20 的 Barron’s `OPEN_LOGIN` 在新建 OPEN 会话时触发全局 `maintenance_viewer_seat` 唯一索引。单 Viewer 约束有必要，但原实现没有友好准入和明确切换流程。
4. 精确读取 Reuters 主备原 target：均为正常 Search Results，有 main、搜索标题与 40 个日期文章链接，没有浏览器网络错误。通用分类器只认识 `/search/`，不认识 Reuters `/site-search/`，因此归类为 unknown。没有理由强行再制造一个 CAPTCHA。
5. Profile 上的单一维护 token/Challenge 字段被当成所有会话的权威，导致普通登录、Challenge 恢复、重启后绑定互相干扰。应以持久化 MaintenanceSession/Task/target 所有权为权威，Profile 只投影当前维护操作。

## 修复边界

- 保留单一 Viewer 及其 SQLite 唯一约束；在事务内先检查占用，返回可操作的提示，不再将预期冲突暴露成 IntegrityError。
- 桌面打开另一身份前明确提示释放当前 Viewer；确认后执行 RELEASE_VIEWER，再打开目标。释放不关闭旧浏览器页面，也不结清 Challenge。
- 同一 LOGIN 再次 Open 复用精确会话，不反复分配窗口。Challenge 状态下 Close Login 提示释放 Viewer，不隐式丢弃原页。
- 普通 LOGIN 可在同 Identity 有待处理 Challenge 时创建独立页面。仅释放 Profile 兼容投影；保留原 Task、原 target 和 RELEASED Challenge 会话。关闭 LOGIN 后若原 Task 仍待办，恢复其 Profile 阻塞投影，不能放行后台采集。
- 重连与 Worker token 绑定优先采用持久化会话中的 site/identity/target/instance，而非 Profile 当前指针；历史/被替代的 RELEASED 会话不得在重启时夺回身份。
- 新 LOGIN 验证不能结清另一个原页的 Challenge；只有匹配 Task 和精确页面的验证才能结清该 Task。
- Reuters 必须同时满足真实 main 与搜索结果正文，才将 `/site-search/` 识别为正常 listing；空壳、未知页面和明确挑战仍不能通过。
- Verify 分别返回原页 Challenge、认证、采集、正文状态和有界错误原因。采集或正文 canary 抛异常时保存 CONTENT_ERROR，而不是丢弃已取得的原页结果；不据此伪造 READY、FULL 或付费订阅有效。
- 可选认证消息源在真实采集/正文证明访问可用时可结束普通维护；认证仍显示 UNKNOWN 时不声称账号已登录。付费必需源保持 VALID/订阅正文证明要求不变。
- Reopen Lost Page 重试原失败 URL，而非无条件改成登录落地页；保留旧 Task 与替代 Task 的链接，不声称找回了已丢失现场。

## 发布与验收

只从已提交代码构建窄 Site Access 镜像，覆盖 site_strategy 与本次正文分类文件，保留实际生产镜像的其余业务与依赖。更新 root 所有的桌面 GUI；不更换 Supervisor 镜像、不重建 Chrome、不复制账号或 Cookie。验证仍由人工完成 CAPTCHA/登录，本轮不自动求解。

回归与生产验收记录在发布后追加；真实验收必须覆盖有未结清 Challenge 时新建普通登录、释放后跨源打开、原页引用保留，以及正常 Reuters 原页 Verify 的结构化结果，不能仅凭 VNC/RFB 连通宣称功能可用。

### 第一轮生产验收及继续定位

- Site 窄发布 `675ba1c5`，Supervisor 与其他服务容器 ID/镜像均不变。桌面 GUI root:root/0755；提交后版本经语法编译验证。
- Reuters 与 Barron’s 原 Challenge 待办仍在时，新建独立 LOGIN 均成功，原 target 存在且与新登录 target 不同；WSJ 普通登录成功。首次约 6–9 秒，复用会话约 0.3–0.7 秒，API ACK 约 4–34ms；只关闭本轮验收新建的登录页。
- Reuters 原 Challenge Verify 返回 DONE、CLEARED、原页 listing、旧会话 CLOSED，未再报笼统操作失败。认证 UNKNOWN，采集 CONTENT_ERROR/TimeoutError、正文 NOT_TESTED，如实保留业务未恢复事实。
- 对本次 canary 进一步核实：新搜索页已有 459KB 正常 listing DOM，却因等待 DCL 超时被判失败。因此继续将 Reuters 搜索抓取改为 commit + 原生结果卡片/明确零结果就绪判定；Reuters 正文采用 commit + 真实正文段落/挑战文档就绪。资源照常加载，不拦截、不禁 cache/SW，不用空页面或 HTTP200 代替业务验收。
- 辅助发布脚本曾因证据脚本命名为 inspect.py 而遮蔽 Python 标准库，首次发布在实际替换前中止；已改名并修正后成功发布，不隐瞒该部署工具问题。
