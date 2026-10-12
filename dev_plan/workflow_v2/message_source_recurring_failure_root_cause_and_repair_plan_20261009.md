# 消息源再次集中失败：根因诊断与修复方案

日期：2026-10-09。生产只读排查主要窗口：北京时间 19:34–19:45。本文时间默认北京时间，原始证据中的时间为 UTC。

本轮交付诊断与实施方案，**未执行生产修复、清理、Registry 修改、部署或重启**。已保存只读采样脚本与证据，不能把本文的拟实施项目理解为已经落地。

## 1. 结论先行

这次不是多个网站恰好同时失效，而是三类故障叠加：

1. **当前集中失败的直接共因是系统盘可用空间耗尽，Site Access SQLite 写入失败。** Barron's、InvestorsHub、DIGITIMES English、TrendForce 两个入口、Reuters 及 GlobeNewswire 搜索在 10/09 05:29–05:31 前后集中转坏。网页已经成功返回，也可能在后续健康状态或诊断事件提交时变成内部 HTTP 500。
2. **Investing 是更早发生的独立站点拒绝 + 不可恢复配置状态。** 10/06 晚间再次收到 challenge，经过自动恢复探测短暂成功后再次被拒，最终进入人工关注；crawler/body override 又只允许这一组合。它不是磁盘耗尽才坏，释放空间也不会自动解决这个站点。
3. **GlobeNewswire Search 有健康结果契约缺口。** Adapter 未输出共享总线用于判断“全部查询失败”的计数，因此本次全查询报错仍记为 PARTIAL，连续失败为 0、成功时间持续刷新。程序缺陷掩盖了真实故障，不等于该站还有部分成功。

历史 10/04 的 External Playwright driver 死亡恢复修复目前仍在运行；本次 driver 正常、真实 Chrome 进程仍在，**不能重做一次浏览器重启就宣称根治**。当前最大优先级是先恢复可写存储，再解决错误隔离与站点恢复闭环。

## 2. 当前生产事实与逐源诊断

### 2.1 部署基线

| 服务 | 本次核实的生产镜像 / 状态 | 意义 |
| --- | --- | --- |
| Site Access | `doxagent-site-access:source-recovery-20261006-r3`，运行约 3 天、Docker healthy | 已包含 10/04 恢复与 10/06 探测修复；healthy 不代表数据库可写 |
| Message Bus | `doxagent-v2:sweep-priority-20261006`，运行约 3 天 | 不等于本地当前未提交源码 |
| Scheduler | `doxagent-v2:event-maintenance-20261006` | 保留既有实时 / 闭市 sweep 逻辑 |
| Content Enrichment | `doxagent-v2:market-news-enrich-3ef317a4`，运行约 3 小时 | `outcomes:batch` 当前同样收到 500；重启没有解决存储问题 |
| Chrome Supervisor | `doxagent-chrome-supervisor:server`，运行约 2 周 | 六个正式 Chrome 仍运行；不能删除 Profile 或重建登录身份 |

远端 checkout HEAD 为 `b0f1caf`，但生产是逐服务镜像及修复 overlay，不能用该 HEAD 直接解释所有运行代码。已核对生产 `service.py` SHA256 为 `14a0c7115f8a5df03afc1b01b6aacf7743099101ff7f10bd6574e6760ad7bd4d`，与 10/06 发布记录一致。

### 2.2 消息源状态

下表为采样时的生产 PollState / Site Access 证据，失败计数持续变化，不当成固定业务指标。

| 来源 | 当前状态与时间线 | 根因判断 / 修复边界 |
| --- | --- | --- |
| Barron's ticker / Other Dow Jones | 四 ticker FAILED，约 745–748 次连续失败；最后成功在 10/09 05:27–05:29。Site Access 最后真实访问 05:30:53 仍为 200/SUCCESS | 本次集中中断首先是内部存储失败，没有证据要求重新登录或切出口 |
| InvestorsHub ticker | 四 ticker FAILED，约 795–796 次；05:27–05:29 后转坏。NL 组合最后实际成功到 05:30:46 | 与 Investing 共用 `public-markets-nl`，但 InvestorsHub 健康状态仍 READY；同身份不意味着同站点同时被封 |
| DIGITIMES English More News | 三 ticker FAILED，约 816 次；05:30 左右最后成功 | 列表此前持续成功，当前内部 500；会员正文是否有效需存储恢复后另测，旧认证状态不作证明 |
| TrendForce News / Press Releases | 各三 ticker FAILED，约 819–823 次；05:30 左右最后成功 | Managed 轨也受影响，反证不是仅 External driver 死亡；两入口共享访问服务的持久化故障 |
| Reuters Site Search | 四 ticker FAILED；05:28–05:29 后集中失败，单轮约 65–179 秒 | 原 NL Managed 路线 05:30:51 仍真实搜索成功；当前主要是内部 500，另有 DE 正式 Chrome 的历史 challenge 未解除 |
| Investing.com ticker | 四 ticker FAILED，约 4,128–4,130 次；10/06 22:34–22:37 开始失败 | 先是 challenge / 人工关注 / 单候选用途 override，之后叠加存储 500；不能只按最新 HTTPStatusError 解释三天故障 |
| GlobeNewswire Search | 四 ticker PARTIAL，带 `search_query_failed/HTTPStatusError`；成功时间仍刷新，degraded 计数约数百次 | 当前访问受存储故障影响；此外 Adapter 的结果聚合契约确有缺口 |
| BE `newswire_rss` | BE FAILED、约 19,656 次；自 9/22 03:57 起未记录成功。错误 URL 为 `https://investor.bloomenergy.com/rss/pressrelease.aspx`，HTTP 403 | 长期独立 IR RSS 问题，不是本轮集中中断；403 的具体 WAF/路径/地区原因本轮未作新页面验证 |
| Benzinga、Finnhub、IBKR、Google News RSS、HuggingNews、Tom's、The Elec、ETNews、DIGITIMES TW、GlobeNewswire 半导体 RSS | 当前已订阅 binding 的轮询状态成功；INTC `newswire_rss` 也成功 | 只能证明相应采集路径未一同停摆，不能据此宣称全部正文补全 / 相关性 / 发布已验收 |
| Yahoo Finance | 四 ticker 整体轮询成功 | 存在爬虫 → NCP → RSS fallback；整体成功不证明浏览器支路成功。不能因总线显示成功而忽略 Site Access 故障 |

当前 Google News Search RSS 表中只有 BE / INTC 两个 binding；本轮不擅自新增订阅。三 ticker 的 distribution 失败来自一次共享抓取，不应把三份 PollState 当成三次独立网站拒绝。

## 3. 根因链一：共享磁盘耗尽把正常网页变成内部失败

### 3.1 已确认不是单个 SQLite 自身超过上限

系统盘 `/dev/vda3`：总量 `189,559,676,928` bytes，约 176.5 GiB；已用 `184,054,333,440` bytes；**Available=0，Use%=100%**。inode 使用率仅 15%，不是 inode 耗尽。

`statvfs` 还显示约 5.2 GiB 含保留块的 free，但 `f_bavail=0`，不能把这部分当成服务可用余量，更不应通过改 root、权限或降低文件系统保留比例掩盖容量不足。

Site Access 数据库：

- 主文件 `440,176,640` bytes，约 420 MiB；WAL 约 4 MiB。
- page_count=107491、page_size=4096、freelist_count=0、max_page_count=1073741823。
- 明显远未达到 SQLite 页数上限，WAL 也不是几十 GiB 失控增长。
- `access_events` 共 656,969 条，最后记录停在 UTC `2026-10-08T21:30:53.821018`，与多源集中失效时间吻合。

只读目录统计（`du -h` 近似值，目录有包含关系，勿重复相加）：

| 位置 | 占用 | 判断 |
| --- | --- | --- |
| Docker 主数据卷 `_data` | 106 GiB | 当前最大的持久化占用 |
| 其中 `backups` | 41 GiB | 优先审查、外移的对象；不是可直接删除的垃圾 |
| 其中 `read` | 28 GiB | 当前读侧数据库 `v2.sqlite3` 约 26.6 GiB；不能当缓存直接删除 |
| 其中 `bus` | 19 GiB | 主库约 17.4 GiB，另有备份目录；不能清空历史消息 |
| 其中 `runtime` | 7.6 GiB | 业务状态，不在消息源修复中擅自裁剪 |
| Site Strategy 数据卷 | 12 GiB，其中 Profiles 约 11 GiB | 登录身份及浏览器数据，禁止以清空间为由整体删除 |
| containerd 存储 | 33 GiB | 约 25 GiB snapshotter + 8 GiB content，有共享层和运行镜像依赖 |
| `/home/ubuntu`、`/var/log` | 8.2 / 1.1 GiB | 不是本次最大的单项 |

最大的两个备份目录为 `backups/20260927T184853Z-verified-current`、`backups/trade-execution-20260929T122500Z`，各约 19 GiB；`backups/event-maintenance-20261006` 约 3.9 GiB。这些均可能是回滚依据，先核对 manifest、所有者、当前发布依赖与异地恢复副本，不能仅按日期删除。

Docker 日志总目录约 560 MiB，Site Access 已启用 `max-size=20m/max-file=3`。**本次不能把“补日志轮转”包装成主要修复**；Docker system df 的 reclaimable 甚至出现负数异常，不能直接拿它承诺能回收多少空间。

### 3.2 失败发生在网页访问之后，也发生在拒绝分类之后

生产 traceback 已确认三条路径：

```text
_execute_uncached → health.mark_success → repository.save_runtime → commit → SQLITE_FULL
_execute_uncached → health.mark_unknown_denial → save_runtime → commit → SQLITE_FULL
execute → repository.append_event → commit → SQLITE_FULL
```

因此：成功页面、拒绝页面、组合耗尽结果都可能最终成为统一内部 500。客户端 `response.raise_for_status()` 只留下 HTTPStatusError，Reuters/Globe 搜索再包装成 search_query_failed，真实的存储原因进一步消失。

正文结果上报 `/v1/outcomes:batch` 也在失败。采样时 outbox 有 5,709 CONFIRMED、113 PENDING；PENDING 从 10/09 05:31 开始，最高尝试约 105 次。**113 是诊断结果待上报量，不是未完成正文或漏发消息数量**；它有既有退避与幂等机制，不应直接删除或强行确认。

### 3.3 为什么容器仍然 healthy

当前 `/readyz` 返回 200/ready，只核对 generic Registry 可读取、浏览器驱动和 accepting。没有检查关键存储容量，也没有关联最近事务写入错误。

此时 External driver 为 READY、epoch 19，RSS 约 156 MiB，年龄约 3.3 小时，最近轮换原因 max_driver_age；队列 waiter、active page 在采样瞬间均为 0。六个正式 Chrome 都仍运行。这证明“Python / driver / Chrome 活着”与“业务事务能提交”是不同条件。

**共性机制：共享存储容量缺少持续预算与发布前检查 → 任一业务/备份增长挤占全盘 → Site Access 所有网站共用的持久化步骤失败 → 多源同时表现为源故障 → 网站方向的重试、换节点和重启无法恢复。**

## 4. 根因链二：风控、组合恢复与可用备用不是同一件事

### 4.1 Investing 的真实复发过程

UTC 10/06：

1. 14:37:26，原 NL 正式 Chrome 返回 403 / ACCESS_CHALLENGE。
2. 14:42:17，自动恢复 PROBE 返回 200，说明 10/06 的 probe_url / 列表选择器修复确实工作了。
3. 14:42:26，实际业务又收到 challenge；14:44:29 恢复探测再次 challenge。
4. 进入 risk_strikes=2、manual_attention_required=true。到期 cooldown 不会解除人工关注。
5. Registry revision 13 的 crawler/body override 只含 `investing-external-nl`。虽然表面登记三个组合，实际用途只允许一个；其余不是已验证的可用备用。

后续大部分失败为本地 `access_combinations_unavailable`，不是每分钟真实访问网站被拒。直到磁盘耗尽后，连这个耗尽结果的诊断落盘也报 500。

这里的 challenge 是真实外部拒绝；两次挑战后需要人工关注是当前策略，不是 driver 坏了。需要修的是**可观察、可维护、可验证的恢复闭环以及真实可用的备用**，不是把 risk_strikes 每分钟清零。

### 4.2 Reuters 不能一直按 10/06 的出口结论判断

- Registry 仍为 revision 9；德国正式 Chrome `digitimes-de-1` 在 UTC 10/07 19:28 曾成功，19:31 再次 challenge，当前人工关注。
- 随后活动组合已 fallback 为 NL Managed `reuters-1`；UTC 10/08 21:30 仍有真实搜索 200。
- 最近 24 小时窗口中，Reuters 有 385 条 FULL 正文结果、17 条 subscription_required PARTIAL 等记录。它并非从上次换出口开始就完全无法访问。

由此不能把上次“NL 被拒”当成永久事实，也不能把正式 Chrome + CDP 当成必然免风控方案。当前先恢复存储，再按实际搜索路径和正文权限分别验收；德国身份的 challenge 需要人工维护，不能影响目前仍可能可用的 NL 路线。

## 5. 根因链三：健康结果契约和诊断信息不完整

### 5.1 GlobeNewswire 的确认缺陷

生产 `GlobeNewswireSearchAdapter.poll()` 捕获各 query 异常并返回 failures，但 acquisition_metadata 仅有 provider、terms_revision、queries，没有 query_attempt_count / query_success_count / query_failure_count。

`MessageBusV2Service.accept_poll_result()` 依靠这些计数识别 all_queries_failed。缺省 failure_count=0，使 Globe 全查询失败落入 PARTIAL 分支，刷新 last_success_at 并归零 consecutive_failures。本次 production checkpoint 的连续 degraded 计数已达数百次，是明确 bad case。

Reuters 已输出这些计数，因此显示 FAILED；这是同类源间契约不一致，而不是 Globe 的运行质量更好。Google RSS 当前正常，本轮没有证据证明它存在同样全失败假成功；其逐条 URL/领域过滤错误也不能粗暴当作整个搜索请求失败。

### 5.2 HTTPStatusError 掩盖了失败层级

内部 RPC 500、目标网站 403、代理连接失败、资源延期最终都可能在 UI 显示同一大类错误。尤其当 Site Access 自身无法落盘时，不能再依赖它的 access_events 去证明后续访问情况；需要保留调用方的 operation ID、内部 endpoint、稳定错误原因和最近一条脱敏服务端异常。

Reuters 单轮采样 65–179 秒，与多个 query / 分页的逐请求预算累计有关；本轮未做逐请求耗时分解，不把所有长耗时都归因于固定 timeout 缺陷。明确可修的是：已知共享基础设施不可用时，不必继续把同一轮所有 query 逐一打进失效服务。

### 5.3 PENDING 不是自动修复成功，也不是可以全删的队列

近期 distribution_runs 中，TrendForce 两入口和 DIGITIMES 有大量 PENDING。代码中 release_run 会把失败/释放的 run 重新置为 PENDING，正常实时下一 slot 又创建新 run；这些记录不能证明旧窗口仍会自动重抓。

不要恢复时批量重放这些历史 run，也不要全删 PENDING。实时准入仍是 30 分钟，闭市 sweep 仍按原冻结窗口；分别处理过期实时 run、有效 sweep cursor 与正文/分发中的 durable work。

## 6. 与过去修复的对照

| 时间 / 修复 | 当时解决的机制 | 本次复核结论 |
| --- | --- | --- |
| 10/01 有限等待、请求清理、队列热路径优化 | timeout、取消、调度吞吐被放大 | 本次不能拿更大 timeout 治 SQLITE_FULL；保留既有预算边界 |
| 10/04 External driver OOM 恢复、epoch fencing、定期轮换 | Chrome 活着但 Playwright driver 死亡不恢复 | 当前 driver 已到 epoch 19 且正常；旧修复并非失效，不能再次大规模重构 runtime |
| 10/04 公共身份与付费 DIGITIMES 分离 | 不同网站挤占一个 Identity | 已生效；InvestorsHub 在 Investing 风控期间仍成功，不能污染整个共享 Identity 健康 |
| 10/06 Investing probe_url、PROBE 页面类型、联合预算 | 一次 challenge cooldown 因缺探测而停摆一天 | 14:42 的真实恢复探测证明缺口已修；随后实际再次 challenge 是新的外部拒绝 |
| 10/06 Reuters 临时用途拒绝治理、固定组合切换 | 未知 403 不受治理、出口瞬时可用 | NL 后来再次成功，DE 后来再次 challenge；需当前证据而非永久硬编码好/坏出口 |
| 历史服务器清理脚本 | 某次事故的明确白名单对象清理 | 不是持续容量治理；旧 allowlist 不能直接用于删除这次新增回滚备份 |

## 7. 修复方案与实施边界

### P0：先恢复可写容量，不先 build、不先重启 Chrome

1. 重新采样磁盘、最大目录、运行镜像与回滚镜像，列出精确路径/大小/归属/保留理由。当前快照不能代替执行时检查。
2. 优先把已确认不再需要在线回滚的两份大型历史备份迁移到用户批准的异地存储，核对 manifest/哈希及可恢复性，再删除批准的本机副本。**本文件不是删除授权**。如无合适异地位置或备份仍必须在线，先扩容磁盘，不冒险删业务状态。
3. 恢复至少 20 GiB 可用空间；稳定目标至少 30 GiB，并覆盖后续构建、备份及业务增长。数值是初始工程预算，不是测出的长期增长模型。
4. 不在满盘上创建另一份全量备份、不直接 VACUUM 28 GiB 读库或 19 GiB Bus 库、不手删 SQLite WAL/SHM、containerd 层或 Chrome Profile。VACUUM 可能需要原库两倍额外空间，删除记录通常也不直接缩小文件。[SQLite 官方说明](https://www.sqlite.org/lang_vacuum.html)
5. 不执行广泛 `docker system prune -a --volumes`；运行与回滚镜像、共享层、匿名/命名卷必须分别确认。Docker 对不同对象有不同 prune 语义，不能视为一键安全清理。[Docker 官方说明](https://docs.docker.com/engine/manage-resources/pruning/)
6. 空间恢复后先观察现有下一轮自然轮询与事务能否自行恢复。若已有连接持续报错，才正常关闭/重开该服务 DB 连接，必要时仅重启 Site Access；不先停 Supervisor，不碰现有登录身份。

### P1：补最小可用的容量治理，避免几天后再次耗尽

- 基于现有 ops 工具增加只读容量报告和显式 apply 清单；按数据卷、备份、读库、Bus、Profiles、镜像分类，不写自动删库功能。
- 备份创建前检查预计备份大小 + 目标余量；同盘备份必须计算额外峰值。复用 `production_v2.py` 的 schema marker/显式 migration 逻辑，**不让普通重启重复创建全库备份**。当前代码已有该保护，本次未证明“每次重启都复制备份”，不要重复发明修复。
- 备份分为当前发布回滚、最近验证恢复点、历史归档，保留 manifest 和责任归属；历史归档外移后才释放本机。初期人工确认清单即可，不引入通用存储平台。
- 读库、Bus 和 runtime 是真实持久化数据，其增长原因与保留规则另作表级核查；本轮不擅自裁剪研究、交易、消息历史及 active/pending 工作。
- Site Access 已有 30 天事件 / 90 天正文诊断保留，但清理挂在 save_outcomes，且 DELETE 不保证释放文件。将小批量诊断清理移到独立低频维护、保留可配置天数；不能指望它解决主数据卷 106 GiB。文件重整只在有余量、低峰和可恢复条件下执行。
- 暴露容量/增长信息到现有诊断与运维状态；**不新增未经要求的外部通知、定时提醒或维护 Agent**。

### P2：把存储故障变成准确、有限、隔离的基础设施错误

涉及 `site_strategy/repository.py、service.py、api.py、client.py` 和正文 outbox 调用路径。

1. 记录事务成功时间和 SQLite primary error code；区分 SQLITE_FULL、BUSY、IOERR、CORRUPT，不能只匹配所有 OperationalError 并一概重试。SQLITE_FULL 本质是写入无法完成，需结合实际文件系统确认原因。[SQLite 错误码](https://www.sqlite.org/rescode.html)
2. 增加轻量缓存的 storage diagnostics：关键挂载的 available bytes、最近持久化错误、最近真实成功提交时间。容量采样不在每个请求上执行 du，不为每次 readyz 都写数据库。
3. 初始容量阈值：不足 20 GiB 为警告；不足 5 GiB 为明显降级；已出现 SQLITE_FULL 或可用空间不足 1 GiB 时，暂停该存储依赖的新增网页操作。健康轨道和不依赖该库的 API/RSS 不全局阻塞。阈值可配置，不当成永久定律。
4. `/healthz` 继续表示进程活着；关键持久化不可用时 `/readyz` 返回 503 和脱敏 storage 原因，External/Managed 分轨状态继续保留。Docker unhealthy 不等于自动恢复，不能用重启循环代替容量恢复。
5. API 将明确存储不可用转换为稳定的 `SERVICE_UNAVAILABLE/RUNTIME_UNAVAILABLE` 与 `storage_unavailable` reason、有限 retry_not_before；client 按结构化响应解析，兼容旧非结构化 5xx。不要伪造远端网站 status_code，不累计网站风控 strikes，不把它报成代理离线。
6. 核心 Profile / 锁 / 健康状态事务仍须可靠提交；提交失败则有界退让，不能吞异常继续发布“成功”。纯诊断 append_event 采用有界 best-effort、脱敏聚合日志，不能因日志写失败覆盖已经形成的业务失败原因。容量临界时先拒绝新操作，不靠无限内存队列缓存诊断。
7. 若失败发生在网页成功之后且核心状态未持久化，返回资源延期，不把已访问网页当未访问并无限重试；保持 operation ID、短期幂等和原窗口。恢复后以新真实请求确认健康。
8. outbox 保留当前幂等上报与有限退避；只有服务端提交成功才 CONFIRMED。不要删除 113 条 PENDING，也不要强行刷新正文成功时间。存储异常不应消耗文章内容质量/网站风险的重试次数。

### P3：修正搜索健康契约，防止全失败假成功

涉及 `message_bus_v2/market_sources.py、news_adapters.py、service.py`。

- 先补 Globe 的 query_attempt_count / query_success_count / query_failure_count / query_deferred_count，以及每 query 的 operation、disposition、category、reason。沿用 Reuters 已有元数据形状，不另建复杂查询框架或重构所有 Adapter。
- 全部实际 query 失败：FAILED，保留 last_success_at，累计连续失败；确有部分查询成功：PARTIAL；真实空结果：成功；全部延期 / 无新 I/O：不假刷成功、不当网站拒绝。
- 区分查询 I/O、分页覆盖、逐条解析/过滤错误。消息数为零不等于搜索失败，coverage PARTIAL 也不自动变 FAILED。
- 对已经确认的共同 `storage_unavailable`，结束当前轮后续重复 RPC，将未执行 query 标记为 deferred，保留原 L1 集合和 sweep cursor；不改成减少关键词或漏掉某个 query。
- 保持 60 秒目标调度、最多三个 L1 概念、30 分钟实时准入与原闭市 sweep。慢轮询可延期，但不能并发堆叠同 ticker/source 的未结束轮次。
- 前端以持久化正确状态为准，区分最后尝试、最后真实成功、最后完整成功、持续 degraded；已有字段能表达的先复用，不为状态修复扩成前端重构。

### P4：站点恢复闭环与逐源窄修复

| 来源 | 存储恢复后的必要动作 |
| --- | --- |
| Barron's | 保留 `dowjones-main`；核验 ticker 双栏目、精确发布时间、排除 IBD；用既有权限正文验收，不因内部 500 要求重登 |
| InvestorsHub | 复验四 ticker 列表和 iHub/ADVFN 最终 publisher 正文；确认没有被 Investing 的站点健康状态牵连 |
| DIGITIMES English | More News 与至少一篇订阅全文分开验证；只有真实登录/权限失败才交给人工维护 |
| TrendForce 两入口 | 分别真实解析列表、抽样完整正文；在 DB 恢复后才判断仍有无 ReadTimeout/ReadError，避免修错层级 |
| Reuters | 用当前 NL 路线验证各 ticker L1 的实际搜索；正文另测。DE 正式身份保留人工关注，维护时不要全局换共享 Profile 的出口 |
| Investing | 在 `public-markets-nl` 上人工完成该站 challenge 并验证实际新闻列表/正文；否则保持明确“需人工验证”，不伪装可用。只将经过实测可用的备用加入用途 override |
| GlobeNewswire Search | 先修正确失败状态，再验证各 ticker 搜索真结果/真空结果和文章正文；不拿 RSS 成功代替搜索成功 |
| BE IR RSS | 对现 URL 做一次目标路径/当前代理与直连对照，确认 403 的内容与重定向；如原 feed 已失效，采用该公司官方可验证公开入口作窄替换。未确认前不虚构 URL，不悄悄停订阅 |

补充边界：

- “登记了备用”不等于该用途能选到、也不等于备用健康/有权限。诊断应同时列出登记组合、用途候选、实际可选、人工关注及原因。
- 人工关注在站点/组合范围内，不冻结同 Identity 下其他正常站点。维护工具必须显示 challenge 状态与所需操作，不能仅显示 auth_state=UNKNOWN/VALID。
- PROBE 200 只说明被探测入口当时放行；需要实际 crawler recipe / 正文权限验证，不能用首页正常清除全站历史拒绝证据。沿用现有人工验证成功释放健康门控，不自动解 challenge、不注入 Cookie、不清空身份。
- 当前 Supervisor 采样约 4.80/5 GiB，已运行六个 Chrome。没有证据证明本次 OOM，但不应把“每站再新建正式浏览器”当默认修复；如确需新身份，另做容量验证和固定出口/Profile 绑定。

### P5：小范围发布、自然恢复与长期验收

1. **先容量恢复，后代码发布。** 存储恢复阶段可能不需要任何构建；不要在零余量机器上再次 build。
2. 以各服务实际镜像及 SHA256 为基线构建窄 overlay，保留 Barron's、正文补全及 10/04/10/06 的已有补丁。记录镜像 digest、文件哈希、Registry revision、Compose 最终覆盖顺序与可直接使用的回滚入口。
3. 代码阶段原则上仅更新 Site Access、Message Bus；只有确认正文 client/outbox 需要变更时才更新 Content Enrichment。Scheduler、API、Chrome Supervisor 默认不动；不能直接从当前脏 checkout 整体发布。
4. 容量恢复后对既有 FAILED sweep 逐一核对冻结窗口、cursor、是否已经过期或发布过，必要时用原恢复入口续跑；不批量清状态、不重发过期实时新闻，不通过改成功时间“修复 UI”。
5. 过期实时 distribution run 标明过期/不可重放原因；如现模型没有终态，增加最小明确终态并核对消费者，不把失败 run 强置 DONE/COMPLETE。有效工作继续按原 lease/去重处理，不删除所有 PENDING。
6. 跨 24–48 小时、至少一个闭市 sweep 与下一实时窗口验证。此处是后续验收要求，本轮未创建自动观测任务，也未宣称已完成长时间验证。

## 8. 开发回归与生产完成标准

### 必须新增 / 保留的测试

- 注入 SQLITE_FULL：健康成功写、风险状态写、diagnostic append、outcomes batch 各路径都有准确分类；不会把网站标成被封/账号失效。
- 低空间 / 恢复后 readyz 状态正确；不依赖大量写探针，不误杀健康 HTTP/RSS 与浏览器轨道。
- 诊断写失败不覆盖核心原因；核心事务失败不得伪报成功；取消后 lease / 并发预算仍释放。
- Globe 全查询失败、部分成功、真空结果、全延期、分页覆盖不足、逐条无效数据分别回归；最后成功时间语义正确。
- 同 Identity 的 Investing challenge 不污染 InvestorsHub；用途 override 与实际候选诊断一致；人工维护排他、Cookie/instance 保留。
- outbox 上报失败保留待重试、重试幂等、确认只在提交成功后发生；不把诊断结果延迟计作正文漏发。
- 备份容量预检、错误目标拒绝、明确删除清单、回滚资源保护；不得执行 broad prune 或删除 active work。

### 生产验收按四层分别给出

1. **基础设施：**可用空间达到预算；实际业务提交成功；没有新增 SQLITE_FULL；readyz 与真实持久化一致。
2. **采集：**每源自然轮询至少三个连续周期，实际 query/列表成功或真实空结果；失败状态正确、不靠历史补发凑消息数量。
3. **正文 / 发布：**每个需正文的源至少两个适用样本，记录 final publisher、FULL/SHORT_FULL/权限失败及发布去重；RSS 采集成功不能替代正文验收。
4. **持续性：**24–48 小时容量、driver epoch、队列延期、真实失败、人工关注、outbox 积压分别报告；保留 Profile 与 Chrome instance 对照，明确仍需人工处理的站点。

此方案能消除已确认的容量失守、假健康、全失败假成功与错误层级混淆；**不能承诺目标站永不风控**，也不能以一次 HTTP 200 作为长期恢复证明。

## 9. 证据、文件与未完成项

本轮证据位于 `eval/message_source_diagnosis_20261009/`：

- `read_only_audit.py`：只读 SQLite URI + query_only，以及现有 GET 管理诊断；未实例化会迁移库的 Repository。
- `site.json`：当前策略、组合健康、driver diagnostics、最后访问、Investing 复发事件、正文结果与 DB/文件系统信息。
- `bus.json`：当前各 binding PollState、近 24h 共享 run 分布及 outbox 样本；不是不可变 poll 历史全集。
- `host-and-storage-error.txt`：系统盘 / inode、部署清单、日志轮转配置、带时间戳的 SQLITE_FULL traceback。

目录大小、outbox 分组及六个 Chrome 存活另经只读 shell / SQLite 核实，正文所列为该时点近似容量。没有采集网站密码、Cookie、管理令牌或代理订阅秘密。

历史对照：`docs/message-source-reliability-repair-20261001.md`、`docs/message-source-recurring-repair-20261004.md`、`docs/reuters-investing-repair-20261006.md`、`docs/reuters-egress-switch-20261006.md`，以及 10/04 原诊断方案与 changelog。

证据限制：没有重新运行网站 canary、登录或 challenge；在满盘状态下，生产 owner API 的诊断/健康写同样可能失败，此时追加网页测试不能可靠区分网站与服务层。未对所有大库做全量表级增长分析或完整性扫描，没有确认精确哪个写入耗尽最后空间；已确认主盘不可用、主要占用分布、错误路径及集中失效时间。既有备份不是已证明失效的垃圾，清理/迁移需要后续明确执行范围。

**下一轮建议严格依序实施 P0 → P2/P3 → P4，再补 P1 持续治理与 P5 长验收；Source Maintenance Codex Worker 仍不启用、不部署。**
