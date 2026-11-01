# IB Gateway Live / Paper 最终接入与只读验收

日期：2026-11-02，北京时间。用户明确本轮为周末接入与只读验收，不进行 Live 或 Paper 下单。

## 结果

两套 Gateway 已接入。Live 已登记为 **CASH**，DoxAgent 独立策略本金 **950 美元**，且已取得正式 Executor 的应用回执。实际读取的 USD `SettledCash` 为 **959.07 美元**。Paper 继续使用原 MARGIN 配置、4,000 美元本金和既有自身盈亏账本。

**没有把任何现有 ticker 自动切为 Live。** 10 个 ticker 的 desired / active 配置、全局默认执行绑定均保持原值；用户可在标的交易配置中选择 Live，已有交易仍按创建时账户退出。

本轮新增委托 **0**、新增成交 **0**、Live 策略持仓 **0**。个人原有股票数量未改变，也未导入 DoxAgent 资金或持仓账本。

## 实际账户、连接与额度

| 项目 | Live | Paper |
|---|---|---|
| 用户提供的登录名 | weixuanxieagent | weixuanxiemock |
| API 实际账户，遮蔽 | U1***370 | DU***665 |
| Gateway 原端口 | 4001 | 4002 |
| Docker 私网入口 | host.docker.internal:7497 | host.docker.internal:7496 |
| DoxAgent 账户属性 | CASH | MARGIN |
| 正式交易 clientId | 82 | 81，保持原值 |
| 独立策略本金 | $950.00 | $4,000.00 |
| 当前策略权益 / 现金 | $950.00 | $3,987.519931 |
| 当前策略已实现净盈亏 | $0 | −$12.480069 |
| Gateway 登录/API 握手及读请求 | 通过 | 通过 |

登录名是用户提供的识别信息；实际保险绑定使用 Gateway 返回的完整账户 ID，不以登录名、端口或另一个账户代替。完整账户号及回退快照仅留在服务器受限目录。

Live profile revision 为 `ep_69fe18f1c528fd9ca3644519`，Paper 保留 `ep_7bf502b633753cde64a06b7a`。未激活全局 Live 默认，也未修改 Paper profile、资金或 ticker 配置。

Live 额度通过既有 `execution_capital_desired` 交给正式 Executor 应用；回执身份 `live-onboarding-950-20261102`，应用时间 **2026-11-02 04:30:09 北京时间**。本金、交易权益、现金均为 950，费用及已实现盈亏为 0，非 provisional。

CASH 两个名额各自预算上限 **$475**；主仓、对冲及费用仍受既有组预算和实际已结算现金围栏约束。未占用名额不会转给另一个名额。使用实际读取的现金作为输入，在可丢弃的独立数据库中验证了 475 / 475 / 第三个 ticker 为 0、缺少 USD SettledCash 拒绝新开仓；没有在生产占用测试名额或创建测试交易。

券商 `AccountType` 返回 `INDIVIDUAL`，这是账户所有者类别，不能用它宣称券商已确认 CASH/MARGIN 权限。CASH 是按用户此前明确说明配置，并使用真实 USD SettledCash 约束；没有用 TotalCashValue、AvailableFunds 或个人账户总净值冒充策略额度。[IBKR Account Summary 文档](https://www.interactivebrokers.com/docs/tws-api/doc/accounts/summary)说明相关字段。

## 个人持仓与账户隔离

- Live 原有 MU、NBIS、LILMF 三种个人持仓，前后股数一致；DoxAgent 的 Live executions / owned lots 均为 0。
- Live 总券商净值并非 950 美元；系统仅使用用户分配的 950 策略本金，个人股票市值及其盈亏未计入。
- 真实连接 Live 时，故意使用错误的预期账户 ID，得到 `ACCOUNT_MISMATCH`，未转向 Paper。
- 两账户订单连接仍使用各自固定 profile。行情、新闻的来源切换不改变交易账户、下单 clientId 或退出归属。
- Live 新增 relay 仅监听 Docker host gateway 私网地址，并限制来源网段，转发到本机 4001；原 Paper relay 保持不变。两套 Gateway 本身未重启，未修改登录或交易设置。

## 非交易数据服务

沿用已经工作的 **paper-data 优先、live-data 备用**，两来源均登记 reference / realtime / history / news 能力。使用现有按能力选择、60 秒冷却及成功来源粘性，不增加新的路由服务或定时任务。

实际验收结果：

| 数据 / 行为 | 结果 |
|---|---|
| 两 Gateway 合约、服务器时钟 | 通过，QQQ conId 一致 |
| 两来源历史日线与历史 BID_ASK | 均有实际返回 |
| 两来源新闻及正文 | 均返回 2 条样本，DJ-N 正文 2,825 字符 |
| 断开本次 Paper 数据读取 socket | 实际切到 Live，读请求成功 |
| Paper 读取 socket 恢复 | 后续三次保持 Live，没有立即切回 |
| 已部署新闻适配器的故障切源 | Paper 正常 → 注入本次读取故障 → Live 新闻及正文成功 → 恢复后仍为 Live |
| 切源测试是否停止 Gateway / 修改交易连接 | 否 |

数据 socket 断开测试是真实关闭本次独立只读连接；新闻故障测试只在验收进程内注入读取异常，没有关闭生产 Gateway。均未写入生产新闻业务记录或交易记录。保留了早期较短 8 秒探测窗口的超时记录；最终按现有生产 20 秒超时重验通过，没有修改生产超时策略。

周末实时请求返回 market data type 1，但无有效 Bid/Ask，因此**未把缓存 Close 或零数量报价作为新鲜、可交易盘口通过**。开市后的新鲜双侧行情仍需实际观察；既有 ENTRY 盘口、方向价格和新鲜度检查保持。

Document 1/2 及 W3 所使用的 Codex Worker 已加载两来源配置，SDK 的 Data MCP 环境传递保留。未为了配置验收重新运行模型或生成投研结论；这不等同于新跑一轮 Document 1/2 全流程验收。

## 实际部署兼容缺口及最小修正

验收发现：当时运行的采集器/消息总线父镜像 `47589435e504` 不包含此前实现的 `ibkr_data_sources` 模块、Settings 字段及新闻路由包装。只设置环境变量不足以启用备用来源。

因此以两服务的**实际运行父镜像**为基础，仅补三项：

1. Settings 增加 `IBKR_DATA_SOURCES_JSON` 字段。
2. 加入现有共用 `ibkr_data_sources.py`。
3. 在原新闻 poll 外接现有路由；原实际采集、正文、去重和消息发布实现保留。

采集器与消息总线现在运行 `ae874f463dfbe5a1f6618c5bedd603208b1c0a291e3b58f882b94404903e4ca6`。三文件在两个运行容器中逐一校验；新镜像的配置解析/切源 smoke 和真实新闻切源重验通过。没有整文件覆盖本地尚未提交的新闻采集修改，也没有借此发布其他并行改动。

API、Executor 和 Codex Worker 只重载环境，运行镜像保持原值。Codex Worker 在 running / queued / starting 均为 0 时才重启。Scheduler、Web、Projector、Delivery、Control、BODY、初始化服务及其他组件未因本轮操作重启。未新增常驻应用服务；仅增加轻量 Live 私网 relay。未改订单状态机、限价、重试、退出规则或任何交易意图。

## API、资金与回归

- 实际数据库及同一 ASGI 应用返回 Live=CASH、Paper=MARGIN；交易资金接口返回 Live 已应用 950。
- 收益审计当前总资产返回 Live **950**、Paper **3987.519931**；个人股票没有进入该值。
- 生产 HTTP readyz 正常，无认证资金读取返回 401。认证报告读取使用一次性本地 ASGI 认证 fixture；生产认证没有改变，未开放测试入口。
- 当前本地代码相关配置、CASH 资金/名额、归属和订单规则回归：**53 passed**。精确部署镜像另做配置解析、路由 smoke、真实 API 和新闻读取验证；不将本地回归描述为运行镜像的全量测试。
- 核验时相关服务均运行；具有 healthcheck 的 API、采集器、Codex Worker 均 healthy，重启计数 0。

## 本轮验收边界

已完成本次授权的**双账户接入、额度应用和只读验收**。以下按用户周末只读决定没有执行，不能宣称已通过：

- Live 实际买入/卖出、API 下单权限、实际费用回报和退出成交。
- 卖出后未结算现金的实际周转及跨结算日行为。
- 正常开市时的新鲜双侧盘口、真实 Live CASH SHORT 替代与双腿成交。
- 已登录生产前端的完整点击操作，以及新一轮 Document 1/2 模型业务验收。

现有 10 个 ticker 仍配置为 Paper；切换某 ticker 前，在其齿轮配置选择 Live 并填写需要的 CASH 参数，等待执行端应用提示即可。本轮未替用户决定哪些 ticker 进入实盘。

## 留痕与回退

- [结构化证据](../eval/live-paper-onboarding-20261102/acceptance.json)
- [回归日志](../eval/live-paper-onboarding-20261102/regression.log)
- [精确兼容修正 diff](../eval/live-paper-onboarding-20261102/collector-compatibility.patch)
- [已部署新闻切源实测日志](../eval/live-paper-onboarding-20261102/news-routing.log)
- [Live relay 单元](../deploy/doxagent-ibkr-live-relay.service)

服务器受限留痕在 `/var/lib/doxagent/live-onboarding-20261102/`，包括配置/资金变更前状态、Compose/release 备份和精确发布回执；文件不含公开下载入口。组件回退使用原环境及现有 release previous；资金调整继续通过账本事件，不覆盖或回滚整个 Runtime 数据库。新增镜像已登记运行/回退引用，本次临时父镜像别名已移除，未执行全局镜像/卷清理。

重要变更已追加根目录 `changelog`。用户原有未提交文件未覆盖，本轮没有使用 sub-agent。
