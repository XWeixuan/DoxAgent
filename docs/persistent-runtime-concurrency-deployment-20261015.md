# 持久化运行并发修复与发布回执（2026-10-15）

最终规格：每 ticker 最多 6 个 Case，实时 W1/W2 使用每 ticker 独立 12 worker（6 W1 + 6 W2）。

## 实现与验证

- journal.CASE_CONCURRENCY_PER_TICKER=6，同一常量控制 CASE 准入与热路径池容量。第 7 个 Case 等待，其他 ticker 独立准入。
- service 按 ticker 懒创建池，短锁保护创建/双 lane 提交/关闭交接，HTTP、结果等待和 shutdown 不持该锁。
- coordinator.close 对实时 Case 等待退出，再由 service 关闭热路径池；其他后台池维持原关闭策略。
- runtime_hot_lane 日志包含 case_id、ticker、lane、status、queue_wait_ms、lane_elapsed_ms，无正文/提示词/密钥。scheduler 入口启用该模块 INFO，避免默认 WARNING 使日志无输出；HTTP/第三方日志级别不提升。
- 路由、模型、reasoning effort、重试、同日 provisional、旧冻结输入及 CLOSED sweep 波次不变。
- 十个相关测试文件回归 138 passed；随后新增 scheduler 日志入口测试，最新新测试文件 7 passed。合计覆盖 139 个不同测试用例，一处第三方 opentelemetry 弃用 warning。
- 修改的 service/coordinator/scheduler 和新测试 Ruff 通过；journal 除 main 原有 7 处 E501 长行之外通过（本次无新增长行）。git diff --check 通过。
- 方案按最终 6/12 规格修订，保留原审计时 4 Case 的历史现状说明。
- 本文件是本地运维回执，发布证据在后续步骤追加；本次仅提交相关源码、测试、方案和 changelog。

## 部署前读取

远端主检出 /home/ubuntu/doxagent 为干净 main，初始提交 618fd9c5；scheduler 初始镜像 doxagent-v2:main-618fd9c5。正式运行使用 main 下三份受管 compose 与 .env.v2。本次只发布 scheduler 所需应用镜像，不重新部署 Site Access/Message Bus/Worker。

## 提交与正式发布结果

- 本地 main、GitHub origin/main、远端 /home/ubuntu/doxagent 均同步修复提交 `0d1791ef915cdd03cb75ba0e0ccf8bc91c45e8c7`。
- 使用已提交的标准 Dockerfile.v2 完整构建，依赖安装、Playwright 安装、uv pip check 均通过，未以临时 COPY 补丁覆盖生产。
- 镜像 `doxagent-v2:main-0d1791ef`，镜像 ID `sha256:5be2d73453e8edfda3c01e70f08ff178c27bcb3d9fa4b95d6068c0541ef6afa5`。
- Git archive SHA256 `cb1b158dd8bea5138ee6a1d96753f712586f2ece0abba8564e0bef37629b8345`。运行容器四份修改源码的 SHA256 与提交 Git blob 完全一致，细项见 eval/runtime_concurrency_20261015/expected_source_hashes.json。
- 预发布测试在无网络、无生产卷和认证挂载的独立镜像容器执行，实际方法和临时 SQLite 验证两个 ticker 各 6 W1+6 W2、第 7 Case 阻挡。未提交业务消息或调用探针模型。
- 使用 MAIN 三份受管 Compose，仅 up --no-deps --no-build v2-scheduler；前后容器 ID 对照只有 scheduler 改变。
- 等待旧 scheduler 退出期间，用户明确允许中断。北京时间 2026-10-15 05:22:30 按已核对的旧容器不可变 ID 发送 SIGKILL；05:22:31 新容器启动。保留原数据库、冻结输入和回执，由原有租约机制接续，没有手工重置业务记录或批量重跑历史。
- .env.v2 仅更新应用镜像、构建提交、源码 hash 三个发布变量，保持原权限与 owner。原配置备份在远端受限目录保留，没有导出密钥。
- 远端运维证据：/var/lib/doxagent/runtime-concurrency-20261015，配置备份 env.before 为 0600。公开回执保存到本地 eval/runtime_concurrency_20261015/production_receipts.json。

## 实际运行验收

- 北京时间 2026-10-15 05:24:08，新 scheduler running、重启次数 0，采样日志无 traceback/429；新进程已有 MU W1 两轮、W2 一轮 OK 回执。
- Case `case_0428d8242b294cf0b9ecf721ed586e09` 的真实 W1/W2 内部池等待分别 28 ms/38 ms；两条 lane 均 SUCCEEDED，执行时间分别 39.560 秒/17.539 秒。确认实际入口日志可用、模型链路正常。
- 6/12 满载容量由本地 FakeResponses 并发测试与实际镜像隔离探针共同验证；不声称生产自然流量采样已达到所有 ticker 各 6 Case 满载。
- 此处等待是内部池排队，不等同准入到模型的全部时间，也不保证旧冻结大输入或外部慢请求立即全部变快。
- 本轮相关实现、测试、方案与 changelog 已提交。发布期间其他会话新增的 Document2 prompt 和 Site Access 修改保留，未纳入本次提交或镜像。
