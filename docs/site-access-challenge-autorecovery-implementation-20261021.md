# 消息源安全验证分流与自主恢复：实施记录

依据：`dev_plan/workflow_v2/site_access_challenge_classification_autorecovery_implementation_plan_20261021.md`。

## 实现边界

不解验证码、不改变浏览器身份/出口，不增加发布入口，不改变正常 24 小时/60 秒轮询、30 分钟新闻窗口、去重与分发。恢复探测只提交能力证明，不发布消息、不推进业务 checkpoint、不调用 Jev。

### 已实现

|方案范围|实现|
|---|---|
|证据分类|`challenge_detection.py`：AUTO_CHECK / INTERACTIVE / ACCESS_BLOCK / UNKNOWN；供应商识别不冒充交互识别；只读取已加载且可见的供应商 iframe；隐藏控件/脚本不作为人工题；UNKNOWN 保留原始事实，再执行站点策略|
|持久恢复|`BrowserRecovery`、`browser_recoveries`：每身份一个当前事件，租约、generation、配置指纹、实例检查、受影响能力上下文；自动项不创建人工 Task|
|页面交接|RECOVERY owner；精确 target/instance/allocation；close_claim 原子检查，旧 BUSINESS finally 无权关闭已交接页；原页确实丢失后，新探测可留下新的真实人工现场|
|恢复协调|原页轻量观察；到期隔离业务探测；AUTO 60/120/300/600 秒退避；人工待办也可自然恢复；全局最多两个动作、每身份一个；OPEN 人工会话禁止新导航，RELEASED 不阻断后台|
|能力独立|同身份真实 CRAWLER/BODY 分别证明、分别解除门禁；成功列表不证明订阅正文，登录表单消失不证明认证；不存在的旧页永不伪造 LIVE|
|维护|Verify 外层 105 秒、内部独立阶段；丢原页可执行同身份真实业务验证；清理失败不回滚已提交健康，也不永久占 Viewer；ViewerReady 前核实实际 target|
|上游|RECOVERY_PENDING；协议 3 返回新字段，旧协议降级成 BUDGET_DEFERRED；采集延期保留原失败计数；正文恢复延期不消耗失败尝试次数，但仍受原业务过期时间约束|
|认证时间|UNKNOWN 不刷新最后明确认证时间；单独保留 last_check_at/result/reason；维护 inventory 分别提供认证确认、认证检查、恢复真实探测时间|
|资源|Managed 恢复页保护 context；External 控制连接回收不等于关闭 Chrome；原页 TTL 与整个身份寿命分开；有界截图及一次低细节补拍；恢复行 7 天、截图既有 14 天/256 MiB；Supervisor 最近退出摘要及结构化停止原因|
|迁移/回滚|显式 dry-run/apply；保留 Profile/Egress；OPEN 用户延期移交、释放后跟进；持久迁移标志不受日志保留删除影响；rollback-export 只输出旧 schema 投影，不直接改生产库|
|全源契约补齐|CTEE 使用实际分类 API 验证而非栏目首页；复用实际 JSON 列表解析器，兼容 Chrome JSON `<pre>` 包装；Reuters/Yahoo 保留既有 recipe，其他列表使用各自实际解析器|

新增主要模块只保留 `challenge_detection.py`、`recovery.py`；其余沿用现有 Registry、Owner、Worker、Supervisor、维护 UI 与正文队列。

### Reuters 普通自动登录（可选）

`AuthPolicy.auto_login` 默认 `false`。已实现明确 REAUTH_REQUIRED 后的普通表单恢复路径：沿用当前身份/Profile、受保护 CredentialStore、短命 Worker 管道传递，拒绝非 Reuters 域及外域 form action；同一 credential 主备共享 30 分钟尝试间隔；遇到 Challenge/MFA 停止。提交表单后再由独立业务探测证明恢复，不据“提交成功”更新 VALID。

目前只在已有恢复事件明确检测到掉号时适用，不是持续扫描所有 Cookie 的自动登录平台。账号密码不写 Git、审计输出、截图元数据或普通配置。未实测普通表单成功前，不宣称自动登录生产可用；也不把它当成 DataDome 滑块解决能力。

## 验证与发布记录

- 已有离线回归组：203 passed；双轨/维护补充组：73 passed、1 skipped。分组有重叠，不能相加当成独立测试总量。最终候选还需执行增量测试。
- 已检查当前源码未定义变量/未使用导入：`ruff --select F,E9`。
- 生产发布前重新读取：根盘约 51 GiB 可用；7 个未结束历史 Challenge；79 个未终结 target 记录（记录数不等同于实际活页数）。无 OPEN 人工维护会话。
- 已在 root 私有目录 `/var/lib/doxagent/challenge-recovery-20261021/before/` 保存 Registry 在线备份和既有截图。脱敏清单：`eval/challenge_recovery_implementation_20261021/pre-release.json`。
- 用户明确允许重建 Supervisor，保留 Profile 并将实际失去的旧页标记 TARGET_LOST。重建必须同时更新 Site Access 的共享网络命名空间，不能把它留在旧 namespace。
- 本文件后续追加精确发布 commit/镜像、迁移回执、真实业务验收。当前不能把离线测试或容器健康当成全源恢复。

## 验收限制

1. 只有运行至少 24 小时的自然轮询，才能完成方案的长期验收；观察窗无自然验证码不能宣称所有真实验证码分支已验证。
2. UNKNOWN 是证据不足，不是准确识别了题型；Reuters UNKNOWN 按人工策略，其余首批按自动恢复策略。页面拒绝持续存在时保持退避，不保证第三方放行。
3. 首帧保持不可变；小 PNG 只能标记低细节疑似，补拍不能保证消除 GPU/CPU 导致的白图。
4. 自动恢复与账号/订阅/解析/网络错误分开。授权不足、正文非 FULL、非正常解析不会被“没有验证码”掩盖。
5. 回滚必须停止新 claim、等待旧 lease，验证数据库副本的旧 schema 投影，并统一回退消费者；不能直接让旧镜像读取新 JSON。
