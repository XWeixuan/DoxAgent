# 消息源反复失败：修复落地与生产验收

日期：2026-10-04。实施依据：`dev_plan/workflow_v2/message_source_recurring_failure_root_cause_and_repair_plan_20261004.md`。

## 1. 结论与完成边界

已实施并部署访问驱动恢复、Reuters 分类与轮询健康语义、共享身份竞争治理等修复。维护 Codex SDK Worker **未开发、未部署**。

| 来源 | 真实采集验收 | 正文验收 | 当前判断 |
| --- | --- | --- | --- |
| Barron’s ticker / Other Dow Jones | MU 8、BE 9、INTC 13、RKLB 11 条 | MU 两篇分别 5,256 / 4,747 字符 | 列表与订阅正文恢复；保留原登录身份 |
| InvestorsHub ticker | MU 9、INTC 8、BE 13、RKLB 9 条 | BE 两篇分别 4,231 / 488 字符 | 列表及正文能力恢复；短篇表单通知不冒充长篇新闻 |
| Investing ticker | 四个 ticker 各 10 条 | INTC 两篇分别 3,148 / 7,487 字符 | 列表与采样正文恢复，不代表全部 Pro 订阅文章可用 |
| Reuters Site Search | 主备代理各曾取得 20 条搜索结果 | 两路均曾取得公开文章正文 1,868 字符 | 正常轮询再次遭遇 403/401 challenge；尚未稳定恢复，不能以单次成功验收 |
| DIGITIMES Semiconductors | 11 条真实栏目新闻 | 两篇订阅文章均 `subscription_required` | 抓取恢复；付费全文仍需人工登录/订阅权限核验 |

验收经实际生产 Site Access owner API 和 `body_v2.2` 抽取器进行，而非另外启动绕过生产链路的浏览器。所有历史样本仅作诊断，没有调用消息发布或历史回填。

Reuters Micron 最近列表的文章早于当前适用日期过滤范围，因此常规适配器返回 **0 条、真实成功**；另以同一 API 的原始搜索 recipe 核验 20 条结果及公开正文。没有将旧文章强行放入实时 Message Bus。

### 尚未完成的验收

- 06:00 UTC 新闭市周期已启动：MU/BE/RKLB 的 Barron's 自然轮询成功。最后一次部署与周期重叠，八个 Barron's/InvestorsHub/Investing 子任务记录内部 RPC ConnectError 后耗尽重试；06:15 UTC 经现有 RuntimeJournal.resume 窄恢复这八个 FAILED SOURCE_SWEEP，保留原 cutoff、窗口、cursor 和去重，不修改 poll_states 成功时间。06:16:16 UTC 后核验，四 ticker × 三源的全部 12 个 SOURCE_SWEEP 均 SUCCEEDED/poll_done=true；MU Investing 的 3 个消息处理 job 也已随该子任务完成。零条的其余任务仅证明当日窗口采集完成，不冒充有新文章发布。
- Reuters 的 `reuters-1` 多次 UNKNOWN 403，`reuters-2` 先成功后于 06:02 UTC 返回 ACCESS_CHALLENGE/401；随后组合暂不可用。此时 External 控制驱动 READY，不能归因于本轮已修复的驱动死亡，也不能假报已恢复。主路观察 IP 104.28.219.140、备路 205.198.126.113；不凭节点名称断定真实出口地区。
- 没有在本轮几分钟验收基础上声称已完成 24–48 小时稳定性观察，也未创建用户未授权的监控自动任务。
- DIGITIMES 登录态 Registry 中仍记为此前的 VALID，但最新文章实际返回付费墙。必须以当前全文权限为准，不把旧 VALID 或列表 200 当作全文成功。

## 2. 根因与本次修改

### External 控制驱动失效

此前生产日志证实：2026-10-02 16:35:56 UTC，External Playwright 的 Node 驱动在约 2 GiB JavaScript heap 上限处退出。Chrome、CDP endpoint 和 Python API 仍活着；缓存的 `_playwright` 对象阻止了重启，旧 readiness 又只检测 Managed 轨，导致四源持续失败而容器健康。

`external_runtime.py` 的修复：

- 针对锁定版本 Playwright 1.63.0，读取它实际 transport 子进程的存活、PID、RSS；记录驱动状态、epoch、年龄、附件数、活动页面和最近恢复原因。
- 启动/恢复单飞，失败有限退避；不再把“Python 对象存在”当成驱动活着。
- 每轮维护使用 Playwright 侧 browser CDP session 执行 `Browser.getVersion`；session 在 finally 有限 detach，不靠页面 JavaScript 或独立 Chrome HTTP 200 判断驱动健康。
- 默认驱动年龄 4 小时或连续 3 次维护采样 RSS 超过 1,200 MiB 时，在安全空闲点轮换控制驱动。若维护采样正好总与业务请求重叠，挂起轮换意图，最后一个业务页面释放时再尝试，避免永远错过空闲窗口。
- 人工维护期间暂停计划轮换；关闭时禁止新驱动复活。驱动死亡后的恢复保留真实 Chrome/Profile，不调用 attached `browser.close()`、`context.close()` 或 Supervisor stop。
- 老 lease 始终只减自己的老 entry、只归还一次许可；新旧 attachment 由 epoch 检查隔离，绝不通过重置 semaphore 掩盖额度泄漏。失效恢复对 raw CDP 中登记的自有业务 target 做有限清理，不关闭不归本操作所有的人工页面。
- `readyz` 暴露分轨状态：External 不健康会显示 degraded，但 Managed/HTTP 能力仍可提供服务；liveness 与业务 readiness 分离。

**证据限制：**确切哪一类 Playwright 分配造成 heap 长期增长仍未被 heap snapshot 证明。本次解决的是无限生命周期、驱动死亡不恢复及假健康的确定性缺陷，并通过有限代际控制防止再次无限积累；不虚构具体内存泄漏代码行。

### Reuters 与消息总线的假成功

- `capture_reuters_search` 的 HTTP 拒绝异常保留 HTML、headers、最终 URL；服务先按真实响应分类，识别 challenge/login/subscription，而非只分类异常类名。
- 未分类 crawler 403 不直接使登录失效：按 Site / Combination / Purpose 记录 15 分钟内独立 operation，单次请求继续尝试备路；累计 3 个不同 operation 后临时排除该目的 5 分钟。重复重试不重复计数，首页 PROBE 成功不能清掉 CRAWLER 搜索拒绝历史。
- Reuters PollResult 记录 query attempt/success/failure/deferred 数量，保留 operation、query key、disposition、category、reason。
- 全查询失败时状态为 FAILED，保留最后真实成功时间并累计连续失败；有真实成功的部分失败为 PARTIAL；全部延期/无新 I/O 不刷新成功时间，也不清除失败记录。
- 连续降级计数放在既有自由 JSON checkpoint 的 `_health_consecutive_degraded` 中；没有向严格 PollState 增加会让尚未重启的 API/Scheduler 读取失败的新字段。闭市 sweep 仍保留原业务 cursor，仅更新独立健康键。
- `network_ms` 改为各真实 attempt 服务耗时之和，排队保持在 `queue_wait_ms`，不再把端到端总耗时冒充网络耗时。

### 共享身份饥饿与配置

- 爬虫老化阈值从不可达的 30 秒改为 `min(5 秒, queue timeout / 2)`；继续保留 LOGIN 优先、BODY FIFO 和仅在可执行 waiter 中竞争的既有跨 Identity 修复。
- 新建 `public-markets-nl`，供 InvestorsHub 与 Investing 共用一个 External Chrome / 新独立 Profile，保持荷兰出口和固定环境；不复制 DIGITIMES Cookie、不继承它的认证状态。
- DIGITIMES 保留 `digitimes-nl-1`；Dow Jones 保留 `dowjones-main`。原主备组合、账号、域名解析、最终 publisher 正文归属均未重构。
- 始终单并发，轮询频率仍为 60 秒，访问间隔仍由原 Identity 策略控制。没有修改 L1 监测词、实时 30 分钟准入或闭市 sweep 规则。
- 预热只有实例/运行状态真正改变时才更新 identity runtime generation，消除每分钟假递增；驱动 epoch 不等于 Chrome generation，也不等于 Registry revision。

## 3. 生产变更与证据

仅更新两项服务：

- Site Access：`doxagent-site-access:recurring-repair-20261004-r5`，image ID `sha256:5dca9377ebddc63f6ad217f7e28c1441c632f9b7d63301172cec4b63835137ab`。
- Message Bus：`doxagent-v2:recurring-repair-bus-20261004-r4`，image ID `sha256:a5c5d2b59c48345daec5844621f16b923d6a0c970a8966bd96f4524fe11c2a77`。

以各自**原运行镜像**构建窄覆盖层，没有重建依赖、升级 Codex SDK、整库 git pull、重启业务 Scheduler/API、正文补全服务或 Chrome Supervisor。远端其他业务工作和本地并行 V1 清理均不在此次包中。

Supervisor 仍自 2026-09-24 20:18:25 UTC 运行。保留的实例 ID：

- `dowjones-main`：`0854db06758b49a68cdce663ce78e922`。
- `digitimes-nl-1`：`03259f271d684527b235137c3e6acc33`。
- 新 `public-markets-nl`：`2e256ab3aa5a4bc18c20fd370ff585ff`。

Supervisor 当前约 2.32 GiB / 5 GiB；Site Access 约 306 MiB / 6 GiB。公共身份新增后达到现有 6 个实例上限，**不能再把额外冷启动身份视为无条件可用**；本轮没有盲目提高上限、停掉其他持久身份或重启 Supervisor。公共两站相互竞争仍可能有限延期，但不再占用付费 DIGITIMES 身份。

VNC 宿主机监听仍为 `127.0.0.1:5900`。没有 TLS MITM、stealth、随机 UA、Profile 迁移或账号密码自动填写。

代码/镜像/hash 清单：`eval/message_source_repair_20261004/manifest.json`。六个 Site Access 执行所有者文件及四个 Bus 执行所有者文件 SHA256 与运行容器逐项一致。规范服务 override 已更新 `/home/ubuntu/barrons_ticker_fix_20260930/compose.override.yml`；原 override 留于 `/home/ubuntu/source-repair-20261004/compose.override.before.yml`。

### 测试

- 最终定向集合：**70 passed / 1 skipped**；新测试覆盖驱动单飞恢复、探测 session 释放、空闲轮换、老 lease 精确释放、维护期间保留人工窗口、队列老化、目的级拒绝和全查询失败/延期健康状态等。
- Ruff 对本轮修改和新增验收脚本通过。
- 真机隔离驱动故障：最终 r5 测试 Node PID `62 → 75 → 88`，epoch `1 → 2 → 3`，分别验证死亡恢复、强制达到年龄阈值后的安全轮换；两次都重新 attach 同一 Chrome，Chrome 实例/PID 未变。只终止诊断进程自己的 Node，未终止生产驱动。Playwright 对人为断连输出一条 Future connection-closed warning，断言及后续控制通过；不是新的生产崩溃。
- 扩展旧 Message Bus 集合运行时另有 3 个 V1 Scheduler 兼容夹具失败（旧构造参数/禁用 V2/缺 journal），对应本轮之外的并行 V1 退休工作；未为绿灯去修改那些业务代码或测试。不要将定向通过误称全库测试通过。

## 4. 人工维护

DIGITIMES English 付费正文需处理：xRDP 的消息源登录维护中选择 **DIGITIMES / digitimes-nl-1**，确认同一付费账号在当前浏览器中可读取最新付费文章，再“登录完成并验证”。最新文章页 `#content` 实测只有一段约 242 字符预览，并明确显示 `The article requires paid subscription. / Subscribe Now`。

Reuters 仍需后续维护：先处理 `reuters-2` 的人工 challenge，再验证实际搜索页，而非只访问首页。06:18 UTC 主路仍是未分类 403，备路保持 manual_attention_required；返回给适配器的汇总 `EGRESS_UNAVAILABLE` 不证明代理链路断开，本次应以逐组合拒绝证据解释为站点访问失败。未为规避挑战强行清除健康状态或迁移到未经验收的共享 External 身份。

不能确定是登录会话失效、会员产品范围还是最新文章权限限制；不要用 Registry 的旧 VALID 推断账号当前有效，也不要无凭据地认定 Cookie 已丢失。没有改用无订阅预览来冒充全文。Barron’s 不需本轮重新登录；Reuters 主备又触发风控，需进一步人工挑战/出口验证，当前不宣布稳定恢复。

此外自然运行日志出现 Jev HTTP 402，属于模型提供商额度/计费问题，未自动充值、换 provider 或扩展为本轮五源访问修复；可能影响实验模型补充判定，既有正则轨保留。

## 5. 回滚

基底镜像保留：Site `doxagent-site-access:repair-baseline-20261004`；Bus `doxagent-v2:repair-baseline-20261004`。原 image IDs 见 manifest。

1. 用部署目录 `acceptance.py --rollback-public-identity` 通过管理 API / revision CAS，将公共两站组合映射回 `digitimes-nl-1`；不删除新 Profile，不复制 Cookie。
2. 恢复 `compose.override.before.yml` 中原 Site/Bus 镜像；Compose 的最后一个 override 也必须指向旧镜像，不能残留本轮 repair override 覆盖回滚值。
3. 只执行 `up -d --no-deps --no-build v2-site-access v2-message-bus`。不回滚数据、不重启 Supervisor、不强杀 Chrome。
4. 原 Site 版本严格拒绝新增的 CombinationRuntime 目的字段。因此已用原基底 + 仅兼容 schema 构建 `recurring-repair-rollback-compatible-20261004`，供回退运行逻辑时直接读取新状态；**不需手工修改数据库**。部署目录提供 `rollback.override.yml`，必须最后加载该文件。Bus 未新增严格 PollState 字段，无此兼容问题。

实际回滚命令（仅在决定回滚时执行，本轮未执行）：

```bash
sudo docker exec doxagent-v2-v2-site-access-1 python /tmp/source_repair_acceptance.py --rollback-public-identity
cd /home/ubuntu/doxagent
sudo docker compose --env-file .env.v2 \
  -f docker-compose.v2-production.yml -f deploy/docker-compose.server.yml \
  -f /home/ubuntu/barrons_ticker_fix_20260930/compose.override.yml \
  -f /home/ubuntu/source-repair-20261004/eval/message_source_repair_20261004/rollback.override.yml \
  up -d --no-deps --no-build v2-site-access v2-message-bus
```

长期观察重点：驱动 RSS/epoch 与成功恢复、Owned target/队列归零、下一正常周期四 ticker 的真实成功及告警恢复、DIGITIMES 人工核验后的 FULL 正文。维护 Worker 仍完全留待后续。
