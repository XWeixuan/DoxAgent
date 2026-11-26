# 消息源精简与 Quantum Zeitgeist 修复（2026-11-27）

## 变更范围

- Quantum Zeitgeist 继续只供 IONQ 使用，改为 `quantum-computing-news`、`quantum-computing-business-news`、`quantum-companies`、`quantum-funding`、`quantum-policy` 五个栏目。每个栏目独立检查点，仍归属一个消息源，继续共享既有文章身份、去重、正文与相关性链路。
- 用栏目 RSS 的只读 HTTP POST 替代持续 403 的全站 WordPress API。实测 GET 带有一天的缓存指令；POST 的 CDN 回执为 BYPASS，保留 RSS 精确发布时间。停止全站 RSS 回退，避免从栏目外召回文章。每栏沿用 60 秒周期，单并发、请求最小间隔 2 秒，不增加即时重试、浏览器或数据库表。
- DCD 行业源只保留 NBIS、BE；其另一个能源公司入口也解除 CEG，仅保留 BE。同步收窄种子订阅范围。保留旧栏目定位以兼容已有正文任务。
- Google Search 解除 INTC、MU（MU 生产原本已无订阅）；现有及未来新增 Google 订阅默认 `require_relevance=true`，包含传入自定义搜索参数的新增订阅。搜索召回和原有相关性判断定义不变，继续使用现有 Regex/Decision Model；不影响其他消息源。
- 通过正式 hard-delete 服务删除 Stocktwits Messages API、可观测性 / 官方发布、Newswire RSS 的消息源及订阅，删除目录展示项，并加入退役集合，阻止 bootstrap 或重新注册复活。保留历史文章、消息和审计记录；删除时不冲刷待发布消息。
- Reuters 只解除 ONDS 的订阅。

## 验证与轻量约束

- 193 项源目录、五栏目采集、正文、Google 相关性及默认参数、DCD 配置和迁移测试通过；轮询配置另 5 项通过。
- 五栏目均已获取有效 RSS，分别解析 200 / 200 / 200 / 200 / 59 条列表项；这是有限历史窗口，不是日新增量。各栏最新文章正文均提取为 FULL，2020–4997 字符；测试不写消息、不开模型请求。
- 栏目交叉文章保留相同 article key；重复轮询不重复输出未变化文章。单栏目失败不会回退全站或影响其他栏目。源端间歇性 429 仍可能发生，交由既有下轮轮询与失败退避处理，不能据此承诺零限流。
- 全站 WordPress 旧双页用例改为实际五栏目契约；保留通用解析功能和已有历史文章身份。未放宽实时消息 30 分钟准入期限。
- 扩展回归中六项旧失败已在原始 HEAD 的隔离模块基线上复现：旧源目录静态预期、已退役 TikHub 窗口用例、Runtime 测试桩缺 `running_mode`、Yahoo 槽位旧间隔预期，以及 CPU/intake 两项重检预期。本轮未修改对应机制。
- 无新增服务、定时任务、表、索引或逐消息数据库查询；Google 默认值判断为本地字典读取。栏目检查点复用既有每入口最多 1024 项上限。

## 发布方式与验收

使用现有带锁的逐角色不可变镜像发布机制，仅覆盖本轮修改的九个消息源模块/目录文件与一个候选配置安装模块；发布 API、Collector、Bus、BODY、O4，保留各角色当前镜像中的其他修复。Initialization 正在执行 PRAX/XENE/RGNX/CGEM 四项研究，因此保留原进程。真正将初始化配置安装到 live Bus 的路径由新 Bus 代码负责，旧候选快照和回滚中的退役源也会被剔除。发布脚本编译实际覆盖路径并核对文件 SHA-256。

退役检查也覆盖候选配置安装与回滚，防止先前冻结的初始化快照把已删除源恢复。检查为本地集合读取，不增加数据库访问。

生产配置脚本默认只预览；正式应用时保存权限 0600 的控制面备份，在一个短事务中调用既有服务完成变更。解除订阅前确认缓冲为零，不新增订阅、不清理历史消息。

## 生产发布回执

北京时间 2026-11-27 05:19 完成生产配置应用，运行代码为 `6cf859aceda1f49ed23ca8b97bbd2843d4df252a`（包含前序 `6513d0fd7`）。五角色实际镜像及全部十个覆盖文件 SHA-256 均匹配发布清单。API、Collector 的 Docker 健康检查通过；Bus、BODY、O4 没有配置 Docker Healthcheck，已验证运行及启动后日志无 ERROR/Traceback。

| 角色 | 运行镜像 SHA-256 前缀 |
| --- | --- |
| API | df4edc07b190 |
| Collector | ac633fab2eed |
| Bus | 8cbf034b4d14 |
| BODY | 0b8a0e956a6a |
| O4 | 8f12ef925a2a |

- DCD 行业源：`BE, NBIS`；DCD 能源公司源：`BE`。共解除五条 DCD 订阅。
- Google：`BE, CEG, CGEM, COHR, COIN, IONQ, NBIS, ONDS, PRAX, RGNX, RKLB, SMMT, SNOW, XENE`，14 个绑定均显式为 true，源默认值也为 true。INTC 已解除，MU 维持无订阅。其余自定义搜索参数保留。
- Reuters：ONDS 已解除，其余 12 个订阅保留。
- 三个退役源及其 15 条旧订阅已通过正式 hard-delete 删除，Read 投影的三个当前源记录均为零；配置复核脚本再次预览时无待删除源或待解除订阅。
- Quantum Zeitgeist：IONQ 唯一订阅；实际部署代码的五栏目只读采集均成功，每次一条有效物理请求证明。BODY 首轮四个 FULL，一个 429；对该文章进行一次有限复核后为 HTTP 200/FULL、4997 字符。所有五栏最新文章均有完整正文成功证据；不承诺源端永不限流。
- Initialization 镜像与进程保持原样；Worker、Scheduler 的启动时间均早于本次切换，未重启。候选安装的防复活规则由本次新 Bus 执行。
- 私有配置备份保存在生产 `/data/bus/message-efficiency-20261127.before.private.json`，权限 0600。镜像发布/回退记录由 `/var/lib/doxagent/releases/dynamic-ownership-6cf859aceda1/` 现有发布 owner 持有。
- 初始化配置补充回归通过：候选/激活/迁移 18 项，以及加入 Google 默认值后的候选配置 12 项；退役回滚新增用例也已单独复核通过。

当前交易日历为 CLOSED_SLEEP：旧全站采集单元已标记退役，五栏目计划已生成，新的持久采集单元将在正常窗口调度时落地。上述成功是部署、配置及真实网络/正文验收，不代表已经测得下个采集窗口的自然发布量或当日成本下降。
