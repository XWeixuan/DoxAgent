# Runtime OOM 与有界资源预算修复交付（2026-09-15）

## 实施边界

- 不改变 O1/O2/O3 prompt、skill、Runtime 20 条 wave、业务 ID、cutoff、receipt 或 activation；不删证据、不全量重放历史、不进行订单测试。
- 修复 DAILY / SWEEP / 明确补偿 Case 集合的等待判断：只查任务/效果的标量状态；目标日之外 Pending、不相关 sweep 和无效 admission 不成为全局前置条件。
- 维护输入在标量读快照中确定 Case/source/date/版本与效果状态，持久化 `maintenance_selection`；按 ID/keyset 每批 100 条读取必要记录，不载入 Case 正文模型。恢复复用已冻结集合/已完成 frame；O3 交易候选及维护完成标记也仅读取对应集合。
- 新增目标日/sweep、效果 Case 状态、候选来源及日记录业务 ID 的 SQLite 索引。新增索引不需要修改现有业务 DTO 或回写历史 Case。

## 资源额度

| 服务 | 默认 RAM MiB | 借用峰值 RAM MiB | 额外 swap MiB |
|---|---:|---:|---:|
| Scheduler | 1024 | 2048 | 512 |
| Delivery | 256 | 384 | 128 |
| Projector | 384 | 640 | 256 |

Projector CPU 为 1.0；其余 CPU/PID 不普遍放宽。`memswap_limit` 为 RAM+swap 合计，不是额外 RAM；实现遵循 [Docker 资源限制说明](https://docs.docker.com/engine/containers/resource_constraints/)，在线调整使用 [docker update](https://docs.docker.com/reference/cli/docker/container/update/)。

- systemd `doxagent-app.slice` 为所有 V2 应用容器父 cgroup：MemoryHigh=5120 MiB、MemoryMax=5632 MiB、MemorySwapMax=512 MiB。保留宿主机/Gateway 2048 MiB 预算；单服务 swap 是借用边界，不作同时兑现承诺，不执行 swapoff。
- 宿主机 guardian 每 5 秒读取 cgroup/host MemAvailable、PSI 和 swap I/O，只有 metrics 新鲜、无压力、聚合使用+工作预约+峰值借用预算闭合且保留 1024 MiB 主机余量时批准新增工作/扩容。
- 只允许对 Scheduler/Delivery/Projector 固定容器在线改额度；其他 Docker 操作仅 inspect 固定 V2 名称，不 exec，不访问业务 API/DB/Gateway。systemd 独立网络命名空间、AF_UNIX 限制并隔离业务卷文件访问。
- 峰值 15 分钟租约；合法工作 30 秒续约。低于默认 70% 持续两分钟回收，使用高于默认时不缩容；守护进程失效不继续扩容，保留现有安全额度，新工作留在持久队列。资源预约保存在专用 `/run/doxagent-resources`，守护进程重启复用未过期预约。
- Unix socket 用内核 peer PID/cgroup 校验服务身份；应用无 Docker socket 权限。只调整三个白名单服务，申请者不能指定任意容器或无限额度。
- Codex 每个 slot 预估 1024 MiB；保留最多两 slot，第二个须通过聚合预算准入，闲置 capsule 在预算等待时回收。整台服务器同一时刻只有一个维护/初始化重批次，父流程与同一批次 O2/O3 子工作可共用批次身份而仍单独预约工作 RAM。
- 普通 Case 预约 128 MiB，维护/初始化元数据 512 MiB，单投影 worker 预约 128 MiB；资源不足不 dispatch、不扣重试，不伪造终态。守护审批与 Worker 队列遵循实时 > 每日维护 > 初始化 > 历史投影，控制、消息接入、交易交付不经过高内存队列门槛。

## 必要验证

- 不存在的 NativeContent 正文引用作为历史压力样本：标量等待与 membership 不解引用；无关日期/sweep/无效 admission 隔离；空补偿集合与指定集合优先级正确。
- 资源不足时第二 slot/其他重批次排队，白名单限制、压力拒绝及高用量缩容拒绝通过；既有 O2 receipt/O3 失败恢复测试通过。
- 一个原有 Closed Case 轮次数断言失败；使用 HEAD 的 repository/coordinator 原代码内存加载后，同一断言仍为 `5 != 3+1`，已确认是本次修改前存在的失败。本轮不修改该业务/测试口径。
- 真实 MU 维护峰值、三条既有消息自然 W1 启动、O2/O3 进展、父 cgroup/在线额度以及 Gateway/消息轮询将在部署后只读复查；不能用单次容器 healthy 代替这些验收。

## 部署结果

待部署后的实际证据追加。
