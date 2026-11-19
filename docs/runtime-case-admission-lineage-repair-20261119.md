# Runtime Case 创建中断：Event 发布历史依赖被冷归档

## 生产证据与根因

2026-11-19 的深入检查发现，这不是“本日没有新闻”，也不是投影未追平。INTC 的新消息已正式进入 Message Bus，控制状态允许分析，消费高水位已推进；但 `inbox:INTC:std_live_aa7a785995b74fd9717a48ad13c6c2ec` 等任务在创建 Case 前失败。

任务对应的 gap 为 `FileNotFoundError`，指向 `/data/runtime/runtime_artifacts/runtime-maintain-eb4b9e217aae6bbe3907d21c/events/US/INTC/event_library.sqlite3`。原生 Runtime 与 Read 对最新 INTC Case 的时间一致，均停在 06:56 UTC，本交易日索引尚未建立。IONQ 存在同类证据。

11 月 18 日的空间清理将历史 Event SQLite 文件冷归档，保留了分支 sidecar，却没有识别 W1 已知事实的“首次发布”历史依赖。W1 每个新交易日首次生成索引时，需要沿 `.branch.json` 追溯这些记录；直接 Case 已结束并不意味着这项依赖结束。

当日索引缺失的 ticker 为 INTC、BE、CEG、COHR、IONQ、MU、NBIS；ONDS、RKLB、SNOW 已有当日索引，因此此前只测试后面几个 ticker 得到的成功结论不能覆盖全部 ticker。此前的前端视图续期修复解决了另一个真实问题，但没有解决本次 Case 创建中断。

## 修复范围

1. `_admission_history` 根据 sidecar 的 `base_version` 和当前追溯上限，跳过没有本地新增发布的快照数据库。仍读取其 sidecar 继续追溯；真正承载发布的祖先必须存在，不能以空结果、当前时间或最新快照代替首次发布时间。
2. Runtime 资源清理针对一个候选根，检查其小型 `library_versions` 表。存在 `version > base_version` 的正式发布就保留该分支，避免未来日索引失去历史。无新发布的重复副本仍可按原规则处理，不增加历史事实扫描或常驻维护任务。
3. 根据 10 个 ticker 当前有效依赖链制定精确恢复清单。从本地冷归档恢复 130 个缺失的必要文件，共 486,141,952 字节（约 464 MiB），而非恢复全部 3,511 个历史数据库。初始估算约 565 MiB 来自退休回执的磁盘分配口径，恢复包按原始文件长度和 SHA-256 校验。
4. 一次性恢复脚本按原始 SHA-256 校验，验证 SQLite 完整性与所需发布版本，在原路径原子发布，拒绝覆盖不同内容的现存文件；与既有资源退休使用同一 OS 锁。脚本不改 Case、交易记录或任务状态，不批量重放旧失败消息。

## 验证

- W1 日索引、最小分支与保留版本回归：19 项通过，包括缺失无变化分支仍保持原首次发布时间、缺失真实发布分支仍明确报错。
- 资源退出与存储生命周期回归：25 项通过，包括无直接 Case 引用的真实发布分支受到保护、无变化分支不被额外保护。
- 扩展旧 OOM 回归中有两项非本次路径测试失败：后台并发数量的瞬时断言及旧 timing 文件落地断言；本次未修改这两条逻辑，不将其计入通过数。
- 生产自然业务验收见下节；不能仅凭容器启动、投影追平或历史查询返回 200 宣称新 Case 已恢复。

## 发布与生产验收

- 修复提交 `db3cd17357442eee622c36dcb3126fabd3dfef8f` 已先推送 `origin/main`，再通过正式 release owner 发布。仅构建后端源码层并替换 `v2-scheduler`，复用依赖层；没有整体重建、修改其他会话工作区或重启其他业务服务。
- 生产 Scheduler 容器 `52e7410f1a9f`，源归档 SHA-256 `bdfaa6704c681b31420d4e227c2ef7d5f7a0ee5bdcbe86182896ec98994abc43`，运行版本与上述提交一致，重启计数 0。
- 冷恢复回执：`restored=130, already_present=0`；130 个文件均与原归档 SHA-256 一致，并通过 SQLite quick_check 与所需正式发布版本检查。恢复包 110,048,882 字节，SHA-256 `4d45d40e29f395cfb90aed8c060bcd5ec1b8ef5ade943794d7d33ae2903ff625`。
- 原冷归档保留于 `D:/DoxAgent-Cold/20261118-50GiB`；精确依赖清单、恢复包和回执保留于 `D:/DoxAgent-Cold/recovery-20261119`。服务器上传副本与容器临时恢复包已清理，原位恢复文件保留。
- 直接运行正式 `known_index(..., day=2026-11-19)`，10 个 ticker 全部成功，单 ticker 0.235–1.111 秒。此探测没有借用已经冻结的日索引缓存，也没有写入或重建 Runtime 业务记录。
- 对实际生产数据库，通过应用原有 GET 路由检查 10 个 ticker × 当天/7天 × 最近记录/运行图/SOURCE 节点/W3 Case，共 80 个接口响应均为 200。这是容器内隔离只读探测进程的路由验收，不冒充公网连接验收。
- 另通过公网 DoxAgent 连接器确认 INTC 当天新 Case 已能读出；通过真实已登录 Chrome 验证 INTC、BE 的当天最近记录、SOURCE/W3 节点列表均恢复。INTC 实测已自然产生 5 个新 Case，其中 4 个完成 W3；窗口跨过分钟更新，记录继续可读。验收截图：工作树 `.test-tmp/intc-runtime-recovered.jpg`。
- 自然业务记录确认原先中断的 INTC、BE、CEG、COHR、MU、NBIS 均已重新创建 Case；例如 INTC `case_47b333905efe4728871ddeebbe3e1b22` 于 12:54 UTC 创建并正常完成。IONQ 的直接索引读取已验证成功，但该次验收时还没有修复后的自然新 Case，保留这一验收边界。
- 用真实生产候选检查 GC：承载新增发布的 `runtime-maintain-94d0c8ba33f532fbba61bafe/events` 返回 protected；无新增发布、仅需 sidecar 继续追溯的 `runtime-maintain-eb4b9e217aae6bbe3907d21c/events` 不额外保护。避免把所有重复数据库重新永久保留。

## 历史影响边界

故障期间已经终态失败、且尚未形成 Case 的 inbox 任务不能靠投影重建出现。恢复依赖会让新入站消息重新正常创建 Case，但不会自动把旧新闻重新送入当前交易执行。需将自然恢复与历史缺口分别说明。
