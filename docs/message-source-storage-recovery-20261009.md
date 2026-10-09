# 消息源集中失败：容量恢复、窄修复与生产验收

日期：2026-10-09。下文生产采样时间为 UTC，北京时间加 8 小时。

## 1. 结论

本轮已执行用户授权的远端空间清理，并发布存储错误隔离、搜索健康契约和正文基础设施延期修复。**并非所有来源已恢复：网站拒绝、人工 challenge 与订阅权限问题仍明确保留，不强行清健康状态或伪造成功。**

- 清理前根分区可用空间为 0；删除两份明确指定的历史备份后，立即恢复 37,486,850,048 字节可用空间，约 34.9 GiB。实际释放约 37.2 GiB；构建、发布后仍约 34.8 GiB 可用。
- TrendForce News / Press Releases、InvestorsHub、DIGITIMES English 列表、GlobeNewswire Search 自然轮询恢复。前四类恢复不依赖删除 Cookie、重建 Chrome 或清空失败计数。
- TrendForce 两入口、InvestorsHub、GlobeNewswire Search 均取得真实列表和两篇正文样本。DIGITIMES 订阅正文仍不具备可用权限；Barron's、Reuters、Investing 未完成稳定恢复。
- 正文结果 outbox 的 PENDING 已通过原机制自行消化，发布后采样为 0；没有删除队列或强行置为 CONFIRMED。
- 只更新 Site Access、Message Bus、Content Enrichment。Chrome Supervisor、Clash、Scheduler、API、Web 及业务 Worker 未由本轮重启；六个正式 Chrome 的 instance_id、PID 和 generation 均不变。
- Source Maintenance Codex Worker 未部署、未启用。没有提交、推送整个脏 checkout。

依据：[原诊断与修复方案](../dev_plan/workflow_v2/message_source_recurring_failure_root_cause_and_repair_plan_20261009.md)。生产证据目录：`eval/message_source_repair_20261009/`。

## 2. 清理对象、保留项与不可逆边界

删除对象均位于 `/var/lib/docker/volumes/doxagent-v2_v2-data/_data/backups/`，执行前核对了实际路径、挂载及内容归属：

| 精确目录 | 文件大小合计 | 处理 |
| --- | ---: | --- |
| `20260927T184853Z-verified-current` | 19,449,281,123 bytes | 删除 |
| `trade-execution-20260929T122500Z` | 20,288,586,166 bytes | 删除 |

这两个历史完整备份点**没有外移，删除后无法从本机恢复该时间点的完整副本**。用户本轮明确授权激进清理，覆盖了原计划仅允许异地迁移后的删除边界；执行前也已告知备份损失。

保留全部当前业务库、WAL/SHM、交易与研究状态、所有 Profile / Cookie、当前及必要回滚镜像。保留 `event-maintenance-20261006` 约 4.08 GB 及其他小型专项备份，**它们不是被删全库备份的等价替代品**。没有执行广泛 Docker prune、删除卷、手删 containerd 层、在线 VACUUM 大库或截断活动日志。

清理脚本为 `scripts/ops/recover_space_20261009.py`，固定两个目录白名单，默认只报告，必须显式 `--apply`。初次执行输出递归文件清单过大，被工具截断；保存的 `cleanup-receipt.json` 明确记录该证据限制，不将截断输出声称为完整归档 manifest。脚本现已改为紧凑统计和顶层 manifest。

另提供只读 `scripts/ops/storage_report.py --data-root <实际数据卷根目录>`，报告空间、主要数据库和备份占用，不自动删除数据、不发送外部提醒。

## 3. 已落地并发布的修复

### 3.1 存储健康与故障隔离

`site_strategy/storage.py、repository.py、service.py、api.py、client.py`：

- 按 SQLite primary code 区分 FULL、BUSY/LOCKED、IOERR、CORRUPT，不把所有 OperationalError 当成网络故障。[SQLite 官方错误码说明](https://www.sqlite.org/rescode.html)
- 事务 BEGIN / COMMIT 失败均记录脱敏错误码和时间，记录最近真实成功提交；失败事务回滚。
- 缓存两秒的文件系统可用容量采样，不在请求上执行 du 或写入探针。默认 warning 20 GiB、degraded 5 GiB、critical 1 GiB，可用 `SITE_STORAGE_WARNING_BYTES / DEGRADED_BYTES / CRITICAL_BYTES` 调整。
- FULL / IOERR / CORRUPT 后有限暂停；容量恢复或真实成功提交解除门控。未恢复但有余量时至多等待 60 秒后允许下一次有界尝试，不无限锁死。
- critical 时拒绝新增 Site Access 网页操作，返回 `SERVICE_UNAVAILABLE / RUNTIME_UNAVAILABLE / storage_unavailable` 和 60 秒 retry_not_before；不把站点记成代理离线或新增风控 strikes。
- `/healthz` 仍为进程存活；关键存储不可用时 `/readyz` 为 503，正常时包含 storage diagnostics。
- 核心状态提交失败返回基础设施延期；纯诊断事件写入失败不覆盖原业务结果，诊断告警至多每分钟一条。
- outcomes batch 的明确存储错误返回 503；客户端不确认 outbox，保留原幂等重试。
- 诊断清理从每批 outcomes 热路径移至每小时维护，每类最多 1,000 行，继续保留 30 天事件 / 90 天正文诊断。未执行文件重整，也不宣称 DELETE 自动缩小数据库。

本轮保护的是 Site Access 所依赖的数据库和当前共用根文件系统；不是全系统存储平台。独立 API/RSS 不被该门控全局停掉。

### 3.2 搜索健康契约与减少无效 RPC

`message_bus_v2/market_sources.py、news_adapters.py`：

- GlobeNewswire Search 增加 query attempted / success / failure / deferred 计数和逐 query 结果，复用现有 PollState 消费契约。
- 全部实际 query 失败不再伪装 PARTIAL 并刷新 last_success_at；有效空结果、部分成功、全延期、sweep 已完成无 I/O 仍分别处理。
- Reuters / Globe 已明确收到 storage_unavailable 时，不继续逐个请求同一轮剩余 query；剩余项记为未执行延期，保留 L1 集合及 cursor。
- 保持 60 秒目标轮询、最多三个 L1 概念、30 分钟实时准入和原闭市 sweep 窗口。未更改监测词、订阅或相关性判定。

### 3.3 正文延期不消耗内容重试预算

`content_enrichment/service.py`：storage_unavailable 时在原 deadline 内等候 60 秒再入队，退回本次 attempt_count 增量，不消费文章质量/网站风险重试机会；不延长原 deadline、不无限重试、不提前 publish、不修改已完成正文时间。

### 3.4 本地容量预检补充

`production_v2.py` 新增显式 migration 全库备份前的预检：预计备份文件大小加默认 20 GiB 余量，不足则拒绝创建；`DOXAGENT_BACKUP_RESERVE_BYTES` 可调。保留原 schema-current 快速路径，不让普通重启创建全库副本。

该公共 provisioning 文件**未混入本次源服务 overlay 发布**，需要下一次包含该文件的正式发布才对相应部署 CLI 生效；不能将本地实现声称为已在所有生产启动器启用。

## 4. 逐源真实验收与未恢复项

采集和正文样本不写入 Message Bus，历史样本不重放到实时窗口。搜索后续 canary 使用生产监测词版本（MU revision 5：Micron / memory / HBM），不使用裸 ticker 替代正式 L1。

| 来源 | 真实采集 / 正文证据 | 当前结论 |
| --- | --- | --- |
| TrendForce News | 7 条；两篇正文 3,434 / 4,917 字符 | 列表与正文恢复 |
| TrendForce Press Releases | 5 条；两篇正文 4,418 / 3,755 字符 | 列表与正文恢复 |
| InvestorsHub | MU 8 条；两篇正文 3,779 / 3,926 字符；四 ticker 自然轮询恢复 | 列表与正文恢复；本次两样本为 iHub 发布页，不冒称另验所有第三方 publisher |
| GlobeNewswire Search | 正式三个 L1 均成功，合计 30 条；两篇正文 8,649 / 21,317 字符；四 ticker 轮询恢复 | 查询健康契约和正文恢复 |
| DIGITIMES English | More News 11 条；两篇正文均 subscription_required | 仅列表恢复，正文需人工登录/权限复核 |
| Barron's | 主身份精确 ticker 页探测真实 403 | 未恢复，不能因历史 auth_state=VALID 宣称当前可用 |
| Reuters | 12:09 精确搜索页探测 200，12:10–12:14 各 ticker 曾自然成功；随后正式 MU 三 query 均 access_combinations_unavailable | 间歇放行，不是稳定恢复；正式 DE 身份仍人工关注 |
| Investing | 原 `public-markets-nl` 精确 news 入口触发 Cloudflare 403 challenge | 未恢复，需人工 challenge |
| BE IR RSS | 原 feed 与 IR 入口直连 403；feed 荷兰 / 德国代理对照也为 Cloudflare 403，非 XML | 未替换未经验证的 URL，继续明确失败；不是本次满盘导致 |

DIGITIMES 额外复核直接通过 owner API 读取文章：响应 200，但 `#content` 仅 267 字符，明确为短预览和 “The article requires paid subscription”。因此本次不是把含订阅导航的完整正文误判为付费墙；需复核当前会话/套餐，而不能单靠放宽正文识别恢复。

Barron's 的 `no_authenticated_profile_available` 有上下文陷阱：当历史已认证主组合被用途级 403 门控排除、剩余备用从未认证时，该结果不证明主 Profile Cookie 被删除。本轮没有清 Cookie，主 Profile 和对应站点认证记录仍为 VALID / session revision 9；但真实访问为 403。仍需人工验证，不重置认证状态伪装恢复。

12:13–12:15 个别 RSS / InvestorsHub 在网络或服务切换时出现短暂 ConnectTimeout / ConnectError，后续自然恢复；未将这类瞬态与持续拒绝混为一个根因。

## 5. 人工维护交接

仍通过 xRDP 桌面的“消息源登录维护”完成，工具可用、viewer ready，采样时无正在进行的维护会话：

1. Barron's → `dowjones-main`：先确认真实拒绝页并人工处理 challenge，必要时登录，最后验证订阅文章。
2. Reuters → `digitimes-de-1`（Reuters 行）：完成该站 challenge，验证实际搜索；不要换掉共享 DIGITIMES 身份的出口。
3. Investing → `public-markets-nl`：完成 Cloudflare challenge 后验证真实 news 列表。
4. DIGITIMES English → `digitimes-nl-1`：复核登录及订阅权限，验证订阅文章。

未自动处理 CAPTCHA/MFA、复制 Cookie、重建身份或启用未经验证的备用。BE IR RSS 的 Cloudflare 拒绝也不能承诺通过桌面登录就能修复 HTTP RSS，需后续确认官方可用 feed/API 入口。

## 6. 发布、基线与回滚

镜像严格基于实际运行版本构建，不从整个本地脏树重建：

| 服务 | 保留基线 | 本次运行镜像 / digest |
| --- | --- | --- |
| Site Access | `source-recovery-20261006-r3` | `doxagent-site-access:source-storage-20261009-r1` / `sha256:e12498a10e668db405b0b0142bc8834935ffbd9b09669a0b702ff365465e789b` |
| Message Bus | `sweep-priority-20261006` | `doxagent-v2:source-storage-bus-20261009-r1` / `sha256:d9ee1338c54cbd591e64f3e1ad297db60dc94add8385d2b03551dd1a234e8261` |
| Content Enrichment | `market-news-enrich-3ef317a4` | `doxagent-v2:source-storage-enrich-20261009-r1` / `sha256:5e9ac95b4c874dfcf8a175209b07fd1581358df5de97d4b7d16006d0bb16e18c` |

服务器 staging：`/home/ubuntu/source-repair-20261009`。`build.sh` 校验原镜像 digest 与至少 20 GiB 余量；`deploy.sh` 保留既有 Compose 链并最后叠加本次覆盖，只 `up --no-deps --no-build` 三个目标服务。

可直接回滚镜像：

```bash
ssh doxagent-sg 'bash /home/ubuntu/source-repair-20261009/rollback.sh'
```

该回滚不恢复已删除备份，也不回滚业务数据、登录状态或 Registry。保留原源修复、Barron's 和 10/06 sweep 补丁；Docker orphan 提示没有被用于删除初始化守护容器。

发布前后六个正式 Chrome 的 instance_id、PID / generation 完全一致，证据见 `pre-deploy-site.json` 和 `post-deploy-1-site.json`。生产文件 SHA256 与 staging 对照见 `overlay-hashes.json`；没有另接 CDP 控制器。

## 7. 测试与剩余边界

- 精确待发布文件独立加载回归：77 passed，包括真实临时 SQLite max_page_count 触发 FULL / 恢复、readiness、outcomes 503、诊断失败隔离、query 健康四分类及正文延期保留 deadline / attempt。
- 本地补充回归：35 passed，覆盖本轮、既有反复失败修复与正文 Hub。
- 定向 Ruff、git diff --check 通过；三份实际构建镜像在无网络、无业务卷容器内 import 验证通过。
- 生产自然轮询持续恢复、无 PENDING outbox；实际网站拒绝仍保留失败状态。没有以容器 healthy 或单次 200 代替所有来源正文验收。
- 本轮未批量重放或删除历史 PENDING distribution runs，未裁剪研究/交易/消息历史，也未新增 EXPIRED 状态迁移。约 12,600 条历史 PENDING 保留审计；它们不因此被判成完成。历史实时 run 终态整理不是当前存储恢复的前提，需后续单独实现并核对消费者。
- 仅提供本轮多周期验收，**24–48 小时、下一闭市 sweep 与实时窗口的长期验收未完成**；未创建未经要求的定时监控或自动清理。
- 真实业务库仍在增长：本次采样 read 约 28.59 GB、Bus 约 18.68 GB、runtime 约 4.26 GB。35 GiB 不是永久解决容量问题；后续需异地备份/扩容和经确认的数据保留规则。本轮不擅自删除业务历史。
- 生产仍为有记录的窄 overlay；本地相关修复已保留，但未合并/推送全仓改动。未来标准镜像发布必须包含这些文件或明确回滚，避免覆盖补丁。

技能应用：按 web-scraping 的内容有效性检查分别核验列表、拒绝页、付费预览与完整正文；未采用 stealth、随机身份或自动挑战处理。
