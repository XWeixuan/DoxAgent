# 消息源反复失败修复：实施与生产验收记录

日期：2026-10-12。依据 `dev_plan/workflow_v2/message_source_recurring_failure_root_cause_and_repair_plan_20261012.md` 实施。本文区分代码修复、生产发布、采集可用、全文权限与长期稳定性，不宣称全部来源恢复。

## 1. 结果摘要

两个失效固定出口已恢复，消息总线 Runtime SQLite 锁竞争不再直接导致主循环退出；独立 HTTP 后备不再被浏览器排队延期提前截断。只窄发布 Site Access、Message Bus、Content Enrichment 兼容客户端，保留原生产补丁链。没有重启 Chrome Supervisor，没有重建 Profile，没有启用维护 Codex Worker。

最新只读证据见 `eval/message_source_repair_20261012/polls-final.json`（13:00:03 UTC）、`postcheck-final.json`、`release-verified.json`。各采样不是原子快照。

| 来源 | 采集情况 | 正文/剩余限制 |
| --- | --- | --- |
| Barron's ticker news | MU/INTC/BE/RKLB 均恢复自然轮询成功 | 两篇实际 publisher 文章经现有正文管线返回 FULL；第二篇通用质量检查仍含 marker 提示，见下文，不将所有文章权限一概保证 |
| InvestorsHub | 四个 ticker 均有新的成功轮询 | 实际生产已有 FULL；额外现有文章检查见验收脚本 |
| TrendForce News / Press Releases | 三个订阅 ticker 均成功 | 各两篇正文 FULL；News 样本含历史置顶文章，不计作实时新消息 |
| DIGITIMES English | 共享列表恢复、实际解析到 11 条 | 两篇当前付费文章仍 subscription_required，列表成功不代表订阅正文可用 |
| DIGITIMES Taiwan RSS | 原 feed 德国主路/直连均 200、有效 XML、40 条 | 两篇既有免费 DIGITIMES Today 正文 FULL；原免费收录限制未放宽 |
| HuggingNews | 共享采集恢复 | 发布后实际正文记录 FULL 5 条；另有一次 PARTIAL 风险组合耗尽，非每次保证成功 |
| Yahoo Finance | 四个 ticker 新成功时间 12:44:50 UTC | RSS 后备恢复；旧 browser/NCP endpoint 仍 404，浏览器采集本身尚未修复，不能称双路验收通过 |
| Reuters | 四 ticker 仍失败 | 真实 manual_attention_required；Managed 对照仍有 challenge，需要人工维护实际 Reuters 搜索入口 |
| Investing | 四 ticker 仍失败 | 原真实 challenge 人工门控保留，需要维护 public-markets-nl 的 Investing 行 |
| GlobeNewswire Semiconductors RSS | 普通 HTTP 仍 403 | 同一官方 feed 经现有 Managed 返回 200，解出浏览器文本包装后为有效 XML/20 条；尚未接入生产 adapter fallback，不能称已恢复 |

失败期间历史缺口未强行补发；30 分钟实时准入和原闭市 sweep cutoff、租约与去重保持不变。60 秒为目标轮询间隔，不将排队/延期期间伪装成每分钟已完成抓取。

## 2. 已落地的内部修复

### P0-A：固定出口恢复

- 有限对照确认原荷兰候选不可用、现有德国节点可用后，将 `us-standard-5:18081` 与 `nl-standard-2:18082` 分别固定到德国 standard 1 / 2。
- 观察 IP 分别为 `205.198.126.113`、`205.198.126.114`，Registry egress generation 为 6 / 7。保留原 ID 和 listener，不为换节点新建 Chrome/Profile。
- 原 Mihomo 配置备份在 `/opt/doxagent-egress-clash/config.pre-20261012.yaml`。验证配置后原位写入并 reload，Site Access 协调短暂停止；没有重启 Clash 容器或 Supervisor。
- 出口不可用时独立限量恢复探测：最多 2 个并行，120/240/480/900 秒退避，成功探测间隔 30 分钟；记录 attempt/next probe/error/耗时，不由每个业务请求无限重复测速。
- 上游订阅中存在近期到期提示，但没有把标签当作续费状态的确定证据；长期可用仍取决于代理供应商。

### P0-B：候选可解释，避免虚构网络尝试

- 只读 candidate diagnostics 输出 purpose override、disabled、人工关注、auth、egress、cooldown 等阻断原因；读取不提升健康状态或解除门控。
- 没有实际网络尝试时不再生成假的 AccessAttempt；纯出口不可用返回 egress_unavailable 和明确重试时间。
- 新 AccessResult 组合诊断字段同步到 Bus/Enrichment 严格模型客户端，避免 extra_forbid 导致协议不兼容。
- 发给未重启 Supervisor 的 ProxyEgress 排除新增探测遥测字段，兼容旧服务严格 schema。
- **尚未完整实现**：cold_start_capacity 目前仅表示 not_required / requires_supervisor_check，不是实时容量结论；Managed 候选的后一文本也不够精确。不能据 selectable=true 宣布未启动备用可接替。

### P0-C：SQLite 锁竞争局部隔离

- RuntimeJournal 新增可选 busy timeout；其他调用方默认仍 30 秒，Bus 使用 50ms。
- 完整短事务在线程中创建/关闭连接，有界 3 次重试；不跨线程共享连接、不把整个 run_once 移至线程、不降低交易持久化等级。
- 只处理 BUSY/LOCKED；IOERR/FULL/CORRUPT 不吞掉。诊断含 pid、operation、wait/elapsed、SQLite code。
- 主循环保留 durable lease、下轮继续；已完成 inflight 在 finally 清理，gap 写失败不重复收割同一异常。日历读取移出事件循环。
- 符号级合入实际生产 10/06 sweep 优先级、shared_key fencing 与实时共享采集避让，不用本地较旧实现覆盖这些补丁。
- 发布后日志真实出现 sqlite_code=5 和 preserving durable leases，Bus 继续运行；不只是模拟测试。
- **证据边界**：尚未定位具体长事务持有者；lsof 的数据库使用者不能直接当作持锁进程。新耗时日志用于继续取证。没有借本轮重启旁侧 Control。

### P1-A/B：独立 HTTP 后备与入口级 feed 路由

- Yahoo HTTP 使用明确固定代理 `DOXAGENT_MESSAGE_BUS_V2_YAHOO_HTTP_PROXY_URL=http://doxagent-egress-clash:18083`，不继承 Chrome Cookie、不纳入浏览器身份登录治理。
- browser→NCP→RSS 优先级不变，保留各分支失败链及阶段/status/脱敏 URL；browser 排队/出口延期允许继续独立 HTTP，storage_unavailable 仍延期。所有后备都失败时恢复原浏览器延期语义。
- 有限实测 NCP 404、RSS 200/20 条，含 EXACT pubDate。四 ticker 自然 PollState 恢复，不是仅脚本 HTTP 200。
- **未完成**：旧 Yahoo browser recipe 精确替代接口仍未找出，公开 loader 静态资源 HTTP 对照 429；未猜测新 API、未从相对“43m”伪造精确时间。
- SharedFeedAdapter 新增入口级批准路由，最多 primary + 1 fallback、总 deadline 30 秒；仅传输错误切换，不因 403 扫描多出口，不将 HTML 拒绝页视为空 feed。
- Taiwan SourceDefinition 通过原服务审计更新 version 5→6，绑定同步：`feed_proxy_url=...:18083`、`feed_http_routes=[de-standard-1,direct]`。保留原 URL、免费规则、时效和订阅。
- [GlobeNewswire 官方 RSS 列表](https://www.globenewswire.com/Rss/List) 的 Semiconductors 链接仍是现有入口，不能用 Globe Search 成功代替 RSS 成功。额外实测发现普通 HTTP403，而同一 URL 经原 Managed owner 返回200；XML被包在HTML文本中，解析包装文本可得到有效RSS20条，证据见 globe-browser-feed.json。该可行路径尚未接入生产 adapter，feed内两篇正文配对验收也未完成，仍列剩余项，不能拿此前 Search 正文 FULL 代替。
- BE IR 独立入口本轮未取得可确认的 source ID/URL，因此不伪称已复测，也未猜测替换或取消订阅。

### P1-C / P2：控制层、积压与实验 Jev

- 自有 CDP task done callback 收尾异常，有限聚合告警；close 取消/等待自有任务，非受治理 target resume 有界重试并记录，不关闭所有人工页面。
- External 控制探测一次慢响应先 degraded，连续两次或真正断联才恢复；计划轮换 idle-only，保留维护保护与单飞恢复，不停止正式 Chrome。
- 过期 realtime distribution run 每分钟最多 200 个标为 EXPIRED：超过 3 天且无有效 lease/未完成有效交付，保留原因与审计。有效 sweep/cutoff 不改，不删除记录、不批量重放。
- 12:54 快照 EXPIRED=4000，13:00 为5000；这是终态治理，**不是释放 5000 条数据库空间**。
- Jev 402 增加 5 分钟 provider circuit、单次半开；正则独立正常工作。真实 provider 仍 402，未自动充值、换 provider 或把未知判相关。

## 3. 授权清理与容量

按用户进一步授权，自主清理低价值对象，不再逐项询问。执行前核对路径、引用、挂载与使用者：

- 未使用 Docker 构建缓存；输出累计 reclaim 数不直接当根分区净释放量。
- journal 保留最近 7 天并 vacuum 至约 300MB；apt 下载缓存。
- 两个无容器引用的旧无标签镜像：`33418fc8597c602a5e7cda1f8ab6394bb48c6b0eb57faa315005761bca2bcf0e`、`c567b3d2ffae32fbde5b64fa9282170403b78f9da89ef562efa39ffaeea5cfe1`。共享层导致实际释放小于显示大小。
- `.cache/doxagent-codex-upgrade-20261002/` 下旧 agent/guardian/template/worker/research/scheduler 构建副本；保留根目录原验收收据和旧 tar 备份。
- 六处旧 checkout/export 中可重新安装的 `exports/mu_enrichment_20260915_support/node_modules`；以及 `/home/ubuntu/doxagent-stage/ibgateway-linux.sh` 旧安装包。精确名单和安全核对逻辑见 `cleanup_low_value.py`，该脚本是实际删除程序，**不要当只读检查重跑**。

未删除业务数据库、WAL/SHM、Profile、有效部署代码、在用镜像或回滚基线；未截断现有研究 Worker/Clash 活跃日志。被删除的缓存/安装器可重建下载；旧日志不可恢复。

首轮构建前剩余 `21,685,891,072` bytes，约 20.2GiB，满足 20GiB 发布门槛。业务继续运行后降至约 19GiB；随后两次小型缓存 overlay 修正重建发生于低于 20GiB 时，不能声称全过程一直满足门槛。后续完整构建应重新检查；清理不是长期容量规划，数据库增长仍需容量预算/扩容。

## 4. 精确发布谱系与回滚

真实基线、全部 25 处 COPY 文件前后 SHA256、运行容器及暂存文件一致性已保存 `release-verified.json`，all_match=true。不是只核对本地源码。

| 服务 | 基线 image ID | 发布后 image ID |
| --- | --- | --- |
| Site Access | e12498a10e668db405b0b0142bc8834935ffbd9b09669a0b702ff365465e789b | d12fb2a9bd3e65e8966d2221f4d67247e5d0f4eeace49fee9c1296f764d4574d |
| Message Bus | d9ee1338c54cbd591e64f3e1ad297db60dc94add8385d2b03551dd1a234e8261 | fd811880b1da9b21db477b39cce9068f8a3be1cd0c86e3af33e81d6fb7e391f7 |
| Enrichment | 5e9ac95b4c874dfcf8a175209b07fd1581358df5de97d4b7d16006d0bb16e18c | 31a24e36e06acc5e7cbc2ca05635da967642ad79387c97f0742753ff30bfab9c |

远端发布目录 `/home/ubuntu/source-repair-20261012/`，只重建三服务，graceful stop 100 秒，up --no-deps --no-build。Enrichment 只更新严格协议 schema/client，并未替换正文抽取业务实现。

Compose 原 chain：production、server、09/30 Barron's、10/04、10/06、10/09；末尾追加本次 override。原基线镜像保留。代码回滚应沿同一 chain 明确选择原三服务镜像及 Yahoo 原环境配置，只重建目标服务；不能删除旧 override 后用当前脏树整仓重建。节点和 Taiwan source 属于独立配置变化，回滚代码不自动回滚配置；原荷兰节点仍不可用，不建议机械恢复失效出口。

发布途中发现本地并发开发的 scheduler 维护 hook 被带入 stage，但旧镜像没有 source_maintenance 包，12:30–12:36 有 ModuleNotFoundError；已移除无关 hook、重建并恢复。最终 Yahoo 独立 fallback 修正后 Bus 启动于 12:44:41 UTC，RestartCount=0。不能隐去这段发布过程问题，也不能把旧镜像源码与脏本地整文件视作等价。

Supervisor container/image/start time 不变（仍 09/24 启动）。五个原 Chrome instance/PID/generation 不变；public-markets-nl 的实例在 12:12:36 已发生变化，早于本次代码发布，确切退出/自动生命周期原因未证实。没有发出重建该 Profile 的命令，不宣称六个进程全程不变。

本轮未 git commit/push；工作区存在其他并行开发，未一并发布。部分初始修复已被其他会话纳入当前 HEAD，正式下一次发布仍需核对本次 staged 与 main 的生产基线差异，不能覆盖 sweep 等历史 overlay。

## 5. 测试、验收边界与人工操作

最终组合：97 passed / 1 skipped / 1 Windows curl_cffi 事件循环 warning；生产修改目标 Ruff 通过，git diff --check 通过。测试覆盖真实双 SQLite 连接锁竞争、事件循环可继续、BUSY 与 IOERR 区分、readonly candidate、egress 延期、CDP task 异常、一次/两次 probe、Jev402、expired run 保留有效 sweep、403 不切路和 Yahoo 排队→RSS fallback。

最终 Bus 自12:44:41运行至13:00检查未重启，RestartCount=0。观察中仍出现个别共享源 ReadError/ConnectTimeout/TimeoutError；13:00快照 TrendForce两路、DIGITIMES、Taiwan、HuggingNews均在12:59有新成功、连续失败清零。这说明瞬时失败可在后续自然轮询恢复，不代表网络抖动消失，也不能用此短窗口保证长期稳定。

正文检查通过现有 owner API/ArticlePipeline，未建立第二 CDP 控制器、未向消息总线发布验收文章。首轮脚本错误使用合成标题，被 publisher_identity_mismatch 正确拒绝；已改用原真实标题重新验收，首轮拒绝不能当作网站故障。

- Barron's `memory-chip-stocks-drop-cxmt-threat-51baf6e0`：FULL、3584 字符；`stock-market-things-to-know-today-earnings-oil-b765556b`：管线 FULL、7366 字符，但通用 extracted_quality 为 truncated_or_paywall_marker。保留该矛盾原始结果，第二条不能独立证明全文末尾完整；未为通过验收放宽识别规则。
- Taiwan 两篇既有免费 DIGITIMES Today：FULL、2030 / 2052 字符。包含历史样本，只作访问/抽取验证，不绕过时效发布。
- InvestorsHub 两篇既有文章：FULL、1674 / 8057 字符。六篇定点结果保存在 body-acceptance.json；其中 BE IR 公告存在未来发布时间字段，只作正文诊断，不用此样本证明实时准入。
- TrendForce News 两篇 FULL：3434 / 4917 字符；Press 两篇 FULL：4418 / 3755 字符。
- DIGITIMES English 当前两篇（Samsung HBM / Nanya DRAM）均 subscription_required，明确未达到付费全文验收。

仍须用户处理：

1. xRDP 消息源登录维护中 **Reuters 的 digitimes-de-1 行**，验证真实搜索，不是 Reuters 首页或 DIGITIMES 行；仍有真实 challenge。
2. **Investing 的 public-markets-nl 行**，完成 challenge 后验证 ticker news；不因共享 Identity 同步解除其他 Site 健康门控。
3. **DIGITIMES English 原订阅 Profile**，确认账号订阅/当前文章权限；德国备用当前不能宣称已认证。Barron's 主路此次无需预先要求重登，未认证备用仍不能算登录 fallback。
4. Jev 仍是 OpenRouter402，需要额度恢复；正则轨没有因此关闭。

长期边界：截至最终自然快照仅完成有限轮询样本，不宣称所有源已经各自完成 10 个自然成功周期；24–48 小时与下一次闭市 sweep 尚未经历。Yahoo browser recipe、Globe RSS 浏览器 fallback 接入/成对正文验收、实际冷启动容量诊断、BE IR 定点复测及长持锁者定位尚未完成，属于后续明确剩余项，而不是无声标成全部落地。

本轮遵循 project-discovery 的真实生产基线核对及 web-scraping 的内容/拒绝页区分；未采用 stealth、随机身份、Cookie 复制或自动验证码方案。
