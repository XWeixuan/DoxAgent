# O4_REPAIR 开发方案

## 1. 节点职责

`O4_REPAIR` 负责恢复已经批准并投入运行的 monitoring capability。

它不负责重新判断 ticker 应该监控什么，也不负责寻找新的替代 Source。

```text
Operational / Crawler Alert
        ↓
O4_REPAIR
        ↓
定位故障层
        ↓
最小必要修复
        ↓
验证恢复
        ↓
RESOLVED

如果原 Source 已不再可用
        ↓
RECONFIGURATION_REQUIRED
        ↓
O4_CONFIGURE
```

核心原则：

> **Repair restores the approved monitoring design; it does not redesign it.**

---

# 2. 输入

一次 `O4_REPAIR` request 至少应显式提供：

```text
ticker
trigger alert
source_id
binding_id
crawler_id / execution_id（如存在）
```

Agent 进入节点后再读取当前实时状态，而不是依赖旧 thread：

```text
Message Bus binding / poll state
Crawler Plane alert
Crawler execution
active crawler version
cassette / artifact / failure bundle
message_bus_telemetry
```

当前 crawler execution 已经把 `poll_run_id`、crawler execution、Message Bus telemetry 和 RawMessage lineage 串联起来，因此 crawler source 的故障应优先沿这条 lineage 排查。

---

# 3. 第一步必须先定位故障层

收到 alert 后不能直接修改 crawler。

O4 先区分：

| 故障层                 | 典型问题                                           | 主要处理                       |
| ------------------- | ---------------------------------------------- | -------------------------- |
| Message Bus config  | binding 参数错误、disabled、active window、schema 不兼容 | 修配置                        |
| Scheduler / polling | source 未被正常 dispatch、capacity 问题               | 检查运行状态；基础设施异常保留告警          |
| Crawler transport   | timeout、403、HTTP/browser failure               | 修 crawler                  |
| Crawler discovery   | HTTP 正常但持续 0 observations                      | 修 crawler                  |
| Content extraction  | body 异常短/结构漂移                                  | 修 crawler                  |
| Alert policy        | crawler 实际健康但阈值不适合该 source                     | 调整 policy                  |
| Source viability    | 页面消失、内容迁移、永久登录化、不再承担原信息职责                      | `RECONFIGURATION_REQUIRED` |

当前 Crawler Plane 已有 `crawler_execution_failure`、`crawler_discovery_anomaly`、`crawler_content_drift`、`crawler_transport_anomaly` 四类 crawler alert，并可按 crawler/source 配置 alert policy。

---

# 4. 标准排障流程

Agent 默认按以下顺序推进：

```text
Alert
  ↓
crawler_plane.get_execution
  ↓
检查：
status / error
parameters
checkpoint before/after
diagnostics
cassette
artifacts
message_bus_telemetry
  ↓
判断 failure layer
```

如果是 crawler failure：

```text
Failure execution
      ↓
必要时 add_regression
      ↓
crawler_plane.get
      ↓
create_version(latest+1, base_version=active)
      ↓
REPLAY 复现
      ↓
修改 working version
      ↓
live_probe
      ↓
certify
      ↓
promote
      ↓
等待/执行健康运行
      ↓
确认 alert resolved
```

这正是当前 Crawler Plane 已实现的标准 repair lifecycle。

---

# 5. Repair 必须优先复现

对于有 failure cassette 的稳定故障，O4 应优先：

```text
add_regression
+
REPLAY
```

再改代码。

开发复现时：

```text
commit_checkpoint = false
```

并按需使用：

```text
checkpoint_override
```

避免污染正式 checkpoint。当前 `crawler_plane.execute` 默认会 commit checkpoint，因此这一点需要写进 shared Crawler Plane Skill。

生产 FAILED / TIMED_OUT execution 会自动留下 failure bundle 和完整 cassette；符合条件的失败加入 regression 后，未来 certification 都必须通过该 failure replay。

---

# 6. 修复采用最小变更

O4_REPAIR 的目标不是顺手“优化 crawler”。

如果：

```text
selector drift
```

只修 discovery。

如果：

```text
正文 extraction drift
```

只修 extraction。

如果：

```text
source parameter 错误
```

优先改 binding/config，而不是发布 crawler 新版本。

只有 crawler implementation 本身发生变化时才：

```text
new version
→ certify
→ promote
```

---

# 7. Source parameter schema 改变是特殊情况

普通 crawler repair：

```text
v12 → v13
```

promote 后已有：

```text
crawler:<crawler_id>
```

Source 会自动执行新的 ACTIVE version，不需要重新注册。

但如果新版本改变：

```text
parameter_schema
```

则必须同步 Message Bus `SourceDefinition` 和已有 bindings。

当前实现不会自动保证 CrawlerVersion schema 与 Message Bus SourceDefinition schema 一致。

---

# 8. Alert Policy 只能在规则本身不合理时修改

例如：

```text
某官方公告正常正文只有 70–90 chars

crawler_content_drift threshold = 100
```

可以调整 threshold。

但：

```text
crawler 每次都返回 0
```

不能通过：

```text
disable crawler_discovery_anomaly
```

来“解决”。

判断原则：

> **改变 alert policy 必须修正错误的健康定义，而不能隐藏真实故障。**

Crawler alert 当前只有 `OPEN / RESOLVED`，没有 ACK / snooze；下一次条件仍成立时手工 resolve 的 alert 会重新打开。

---

# 9. Source 不再 viable 时停止 Repair

如果证据表明：

```text
原 publication page 已永久撤销

Source 不再发布计划要求的信息

访问永久变成必须登录/订阅

原 disclosure channel 已迁移到另一主体或平台
```

问题已经不是：

```text
crawler broken
```

而是：

```text
approved Source Need resolution 已失效
```

此时 O4_REPAIR 输出：

```text
RECONFIGURATION_REQUIRED
```

并把以下事实交回 `O4_CONFIGURE`：

```text
原 source
失效原因
当前可访问状态
相关 evidence
受影响 ticker / binding
```

REPAIR 不自行寻找替代 Source。

---

# 10. Global Crawler Asset 的并发问题

Crawler asset 是全局共享的。

例如：

```text
crawler:sec_filings
```

可能同时服务：

```text
MU
NVDA
AMD
```

因此某个 ticker 的 O4_REPAIR 修改 crawler 时，实际可能影响所有使用该 ACTIVE release 的 ticker。

开发时应加入一个 crawler-level repair coordination：

```text
repair_key =
crawler_id + active_version + failure_family
```

同一个 crawler/version 的同类故障同一时间只产生一个 active repair owner。

其他 ticker 的相同告警关联到这个 repair，而不是各自创建：

```text
v7
v8
v9
```

业务上的 O4 thread 仍保持 per-ticker；global repair coordination 属于 orchestrator/runtime state，不依赖 thread 共享。

---

# 11. Node 输出

`O4_REPAIR` 最终应产生一个简洁的 Repair Settlement，至少表达：

```text
trigger
diagnosed failure layer

action taken

crawler version before / after（如有）

configuration changed（如有）

regression / certification evidence（如有）

health verification

final status:
  RESOLVED
  STILL_FAILED
  RECONFIGURATION_REQUIRED
  HUMAN_INTERVENTION_REQUIRED
```

其中 Human Intervention 继续使用 O4_DELIVER 已定义的严格标准：

> 只有 Agent 当前能力无法创建的外部前置状态，例如必须人工注册账号、人工 MFA、采购订阅或管理员授权，才能要求人工介入。

技术失败、403、反爬、Certification FAIL、代码问题都不属于人工介入。

---

# 12. Skill 结构

`O4_REPAIR` 使用：

```text
Shared O4 Prompt

+ message-bus-operations.md
+ crawler-plane-operations.md
+ source-repair.md
```

前两个与其他节点共享。

`source-repair.md` 只负责 Node 3 特有工作方式：

```text
先诊断故障层
沿 lineage 获取证据
优先 replay
最小修复
crawler change 必须 regression + certification
不重新设计 Source portfolio
source 不再 viable → CONFIGURE
正确处理 global crawler blast radius
严格 human intervention
Repair Settlement
```

不要在这里重复 Message Bus / Crawler Plane 的完整操作手册。

---

# 13. 工具面

正常 repair 主要需要：

```text
monitoring.list_status
monitoring.get_ticker_config
monitoring.update_ticker_config
monitoring.recent_events

crawler_plane.list_alerts
crawler_plane.get_execution
crawler_plane.get
crawler_plane.create_version
crawler_plane.execute
crawler_plane.add_regression
crawler_plane.live_probe
crawler_plane.certify
crawler_plane.promote
crawler_plane.update_alert_policy
crawler_plane.resolve_alert
```

当前 Message Bus OperationalAlert 没有 O4 resolve 接口；poll 恢复成功后 failure alert 会自动清除，因此 O4 不应直接改数据库消除它。

---

# 14. 开发顺序

第一阶段先实现 `O4_REPAIR request + Repair Settlement + source-repair.md`。

第二阶段接入 crawler/message-bus alert trigger 和 per-ticker O4 thread。

第三阶段实现 crawler-level repair claim/deduplication。

第四阶段做四类 Pilot：

```text
binding config failure
crawler selector drift
content drift + regression repair
source permanently non-viable → CONFIGURE
```

---

# 15. 验收标准

一个合格的 O4_REPAIR 应做到：

```text
不会收到 crawler alert 就直接改代码

能正确区分 config / crawler / source viability

能利用 execution → cassette → artifact → telemetry lineage

crawler 修复先复现，再修改

修复版必须 live probe + certification + promote

不会通过关闭 alert 掩盖真实故障

不会自行寻找 replacement source

Source 已失效时正确转回 O4_CONFIGURE

一个 global crawler failure 不会被多个 ticker 并发重复修复
```

最终 Node 3 可以压缩成一句话：

> **O4_REPAIR 是 approved monitoring capability 的维护者：诊断实际失效点，用最小、可复现、经过 regression 保护的修改恢复它；一旦问题已经从“实现坏了”变成“Source 选错或失效”，立即退出 Repair，重新交给 O4_CONFIGURE。**
