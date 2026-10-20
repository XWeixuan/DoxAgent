# 企业 IR / Press Release 开发与生产回执

日期：2026-10-20，北京时间。执行依据：[已批准开发方案](corporate-ir-press-release-development-plan-20261020.md)。

## 实现

外部来源 `corporate_ir_press_releases`，`builtin:corporate_ir@1`，API / by_ticker。九 ticker 对应十二内部入口：INTC 1、MU 2、BE 1、COHR 2、NBIS 1、CEG 1、RKLB 2、SNOW 1、ONDS 1。正常路径五 RSS、一 JSON、六 SSR HTML；无新增 Profile、身份或常驻页面。

`message_bus_v2/corporate_ir/` 实现 typed catalog、独立endpoint状态、公开transport/parser、7×24 coordinator、有界历史分页、公开正文与惰性双Context池。HTTP六并发/每host二并发；每入口单inflight。浏览器错误先确认控制器是否失活，不因单页超时重启健康sibling，停止时只操作本pool的进程句柄。

内容入队、endpoint cursor/validator及binding聚合在同一Owner事务内提交；嵌套既有repository操作借用该事务，存储失败整笔回滚。取消仍遵守StorageLane admitted commit收尾。精确时间/DATE、过期准入、bootstrap和既有去重保留。一个endpoint失败不覆盖另一个endpoint状态。

原始feed全文按受控recipe和既有质量合同验证，其他详情通过公开HTTP完成，不经Site Access的自动publisher身份分配；正文作业冻结endpoint spec/revision。CEG正文仅取文章列，去除告警订阅表单。NBIS模板午夜和RKLB坏JSON-LD不产生伪精确时间。

endpoint状态以独立复合ID纳入CDC及快照重建，UI从同事务更新的poll_state读取内部状态。沿用当前V4 Read有界history及source transport/native retention；没有另建无界成功日志或重复业务历史。前端在原消息源配置页显示各内部入口，复用Table组件。

## 配置与迁移

`scripts/ops/configure_corporate_ir_sources.py` 默认SQLite mode=ro dry-run；`--apply` 通过既有Bus Owner service、一次写事务完成注册/绑定/精确重叠迁移。重复执行不提升不必要revision。

迁移前已读取生产九ticker，均为running。旧BE绑定有PR/presentation/SEC三路，保留后两路；旧INTC仅移除PR并保留SEC Atom；ONDS为唯一旧PR入口，停用其确切旧绑定并保留满足rss_urls minItems合同的原参数。ONDS旧`/press-releases/rss`和新`/rss`逐项比较了20篇编号/标题/时间，旧路径作为已证实别名迁移。其他ticker现有非同URL官方PR的通用RSS保持。

容器内应用命令使用服务器已同步的精确提交文件，经stdin运行，避免假定运维脚本已打进应用镜像：

```sh
sudo docker exec -i doxagent-v2-v2-message-bus-1 python - --apply < scripts/ops/configure_corporate_ir_sources.py
```

## 验证

- 首次合并定向后端回归：156 passed，覆盖IR、Bus、admission、存储、正文、去重、orchestration及API配置/新鲜度。
- Read CDC/独立ID/快照映射/技术history回收及现有Read生命周期回归另有定向结果，最终结果记录于下方发布验收。
- 前端typecheck、正式build通过；2项消息展示单测通过；1262px与1559px两项Playwright浏览器检查通过，包含失败与成功并列、无整页横向溢出、展开/收起和外链。
- 新加坡生产Enrichment镜像内十二入口的代表性公开正文均达到FULL；ONDS/RKLB的RSS原生全文无需二次详情请求；CEG改用正文列后仍FULL且不含订阅表单。
- 相关结果与生产快照放在`eval/corporate_ir_implementation_20261020/`。不相加重复运行的测试数量。

## 发布与自然验收

首次生产HTTP canary识别了真实客户端差异：Micron IR/Bloom以及部分Rocket Lab详情在httpx下返回Cloudflare `cf-mitigated: challenge`，相同URL/出口/固定UA用标准库公共HTTP返回200；移除Accept-Language并未解决。按实测给四个endpoint recipe增加精确`urllib_hosts`（Micron newsroom只对最终IR host生效），不引入Chrome、代理轮换或Cookie。同步请求在有限HTTP槽中offload，取消等待真实线程结束，重复取消不提前归还permit。Owner工具仅升级原始revision1且参数为空的已知recipe，保留人工配置。

生产已通过Owner事务应用source revision2、九个binding revision1及十二入口。首次apply触发旧RSS参数minItems约束，整笔回滚；修复为对迁移后无剩余feed的确切旧binding只停用并保留原参数（生产为ONDS，INTC有SEC Atom保留），补测后应用成功。

SourceOutbox生产迁移显式安装新表CDC并backfill十二行；仅更新代码或重启Projector不自动安装源库捕获trigger。自然运行发现httpx把304列入is_redirect，修正为只对301/302/303/307/308读取Location；新增真实MockTransport(304)回归，最终IR定向36 passed。修复发布后重新起算自然观察，前一短窗不计入最终统计。用户随后明确要求提前结束观察，本次不声称完成30分钟验收。

发布组件仅限实际消费者：Message Bus、Content Enrichment、API、Projector、Web；保持Chrome Supervisor、Site Access、既有Profile/原页、维护Worker开关。使用canonical storage_release工具并保留current/previous。

下方分别记录部署、配置、轮询与业务证据边界；没有用历史公告补造实时业务验收。


### 已核对的发布边界

| 组件 | 代码提交 | 生效镜像 ID |
| --- | --- | --- |
| Message Bus / Content Enrichment | `2cace1e7e68a5be8265858a3a8d353d05a630634` | `sha256:e52635de0522dad0f342ecb6901085b36782a4d788521d440605dc3ca371b5fd` |
| API / Projector | `34ba6ce6b871875d945d8de26bcc59e773541786` | `sha256:449f22abc2e61ceba879a94c3319461a77ccb6241a66d3dec5e1b19d4fa9d77c` |
| Web | `34ba6ce6b871875d945d8de26bcc59e773541786` | `sha256:845719e7d76d0068700b4fc8ec466fc3f79d29b43b2f333794051640e1eca2ac` |

三次canonical release均完成并返回`errors: []`。HTTP补丁仅重建Bus/正文服务；其他消费者保持其已验证版本。十二个相关运行文件的SHA-256已与对应git blob逐一核对，全部一致。最终文档/测试格式提交无需重建镜像。

Site Access和Chrome Supervisor的container ID/image/StartedAt在发布前后完全相同。Bus与Enrichment的/proc进程名快照均无Chromium/Chrome/Node；这是本次快照及默认全部HTTP recipe的证据，不表示已测试Linux浏览器长时资源。全服务瞬时内存样本Bus约241MiB、正文约115MiB，包含其余来源业务，不能归因为IR独占内存或节省比例。发布后磁盘可用约48.5GiB；没有手动prune或删除生产数据。

生产Read使用实际SourceStatus builder读取九binding、十二内部入口，native endpoint复合ID也确认为十二条。前端浏览器检查是在本地真实组件与fixture状态完成；未另行登录生产UI做交互验收。

瞬时TLS超时只影响`rklb.updates`，由自然退避重试恢复，其余入口持续成功。共同调度延迟与共享Bus慢存储同时出现：日志包含`ingest`约10秒、`save_poll_state`约23.8秒、`_accept_poll_result`约11.6秒。保留既有单Owner事务及durable入队责任，本次不改全局存储调度。30秒为target而非硬性上界，实际分位数见最终自然观察表；不把HTTP响应时间冒充完整启动延迟。


### 最终自然运行回执

北京时间 **2026-10-20 20:23:34—20:44:18，共20分44秒**。按用户“可以了，不用观察那么久”的明确指示结束；未完成原计划30分钟及后续24小时趋势观察。

以源库CDC回执按endpoint_id+last_attempt_at去重：**490次完成轮询，488次成功，2次失败**。两次均为Rocket Lab Updates（一次TLS握手超时、一次读取超时），均自然重试恢复。观测末尾12入口全部成功，最终Read查询9个ticker聚合状态全部succeeded。不存在把上轮错误在报告里删除或只取最后成功覆盖历史。

| 内部入口 | 成功/次数 | 304次数 | 间隔中位秒 | 间隔p95秒 | 最大间隔秒 | 启动延迟p95秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| be.press | 41/41 | 41 | 31.04 | 44.49 | 63.38 | 21.67 |
| ceg.press | 41/41 | 0 | 31.55 | 43.16 | 60.64 | 22.81 |
| cohr.corporate | 41/41 | 34 | 31.59 | 41.44 | 60.64 | 21.80 |
| cohr.financial | 41/41 | 34 | 31.69 | 43.16 | 60.64 | 21.81 |
| intc.press | 41/41 | 0 | 31.55 | 43.16 | 60.64 | 22.81 |
| mu.ir | 41/41 | 41 | 31.55 | 43.16 | 60.64 | 22.81 |
| mu.newsroom | 41/41 | 0 | 31.90 | 43.16 | 60.64 | 21.81 |
| nbis.press | 41/41 | 37 | 31.59 | 41.44 | 60.64 | 21.80 |
| onds.press | 41/41 | 0 | 31.90 | 43.16 | 60.64 | 21.81 |
| rklb.ir | 41/41 | 39 | 29.88 | 44.49 | 63.38 | 21.67 |
| rklb.updates | 37/39 | 0 | 33.40 | 60.64 | 60.77 | 40.04 |
| snow.press | 41/41 | 7 | 30.38 | 43.16 | 60.64 | 22.81 |

间隔按相邻完成回执对应的last_attempt_at计算；启动延迟相对前次持久next_due_at计算，包含故障退避和共享存储排队。样本没有外推成长期SLA；30秒target已配置，实测中位约30—33秒，但p95约41—61秒，不能宣称严格每30秒。响应实体bytes仅对成功请求求和，非wire压缩带宽，也不含TLS/headers开销。

首次bootstrap未倾倒旧公告：本源最终standard_messages=0、content_enrichment_jobs=0。12入口的非入流公开能力canary全部列表HTTP200、代表性正文FULL；共十一条不同文章URL（MU两入口同篇），正文1338—10503字符。ONDS/RKLB IR原生RSS全文，其余公共详情；CEG不含订阅表单。这证明当前抓取/正文能力，不是新公告端到端业务样本。自然窗没有合时效新公告，first_seen→FULL→dedup→stream→生产UI的新公告实例仍待出现后验证；没有合成公告、回灌旧公告或新建自动监控任务。

最终定向IR测试36 passed；既有首批156项后端回归、24项Read生命周期回归、前端typecheck/build与2项浏览器检查的结果分别保留，不相加重复运行。最终ruff与diff whitespace检查通过。

持久证据：同目录`eval/corporate_ir_implementation_20261020/`中的`natural-summary.json`、`public-body-canary-final.json`、`read-canary-final.json`、`deployed-source-fingerprints.json`及`legacy-feeds-after.json`随回执提交。更细的CDC采样、XML和release日志保留本地评估目录，未把每次HTML/高频日志存入仓库。

回退时先通过Owner关闭本IR源以停止新任务，保留待处理正文责任和新表；按canonical current/previous选定兼容消费者版本，再仅恢复本次移出的INTC/BE/ONDS确切PR feed与原enabled状态。现有SEC/presentation及其他ticker feed不用重建；不全量双开、不删历史或outbox。当前previous为HTTP304修复前版本，回退时应明确其304缺陷，不能把镜像回退等同于业务恢复。
