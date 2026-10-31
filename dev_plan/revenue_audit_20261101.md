# 收益审计实现与上线

页面沿用现有审计页，按 Live/Paper、时间、实际/全部/未执行筛选。默认实际成交；非默认视图首次进入时，对所选期间按每批最多 12 条意图串行完成全局假设计价；离开视图停止后续批次。一个正式 LONG/SHORT 意图只保留一条收益记录，NO_TRADE 不计入；执行中的零成交意图不提前结算假设收益。

顶部共七项 KPI，最左侧为当前策略总资产（DoxAgent 自有股票市值＋策略现金，含当前浮盈）。当前值直接复用既有资金账本的策略现金及当前自有持股，再按本次刷新报价估值；无需把旧交易投影到新页面。空头股票使用负市值，卖空所得已包含在策略现金中。该值始终按当前实际账户计算，不受期间/记录筛选或假设收益影响。

## 数据与口径

- Runtime 在既有交易输出中冻结原始 W3 时点、消息标题、计划账户。未放行的正式判断也保存审计归属，不增加执行权限。CLOSED 使用原候选 W3 时点。新数据资格按正式输出时间确认，因此上线后新入选的 CLOSED 交易可以保留更早的 W3 时间；未最终选中的候选不计正式意图。启用前已经结束的假设收益不回补。
- 现有 SourceOutbox / DomainProjectors 投影 `revenue_record`，由自身 allocations、最高修订成交、真实费用形成轻量 FIFO 摘要和成交时点余额。不依赖 IBKR 合并净持仓。
- 区间收益 = 期间已实现净收益 + 期末净浮盈 − 期初净浮盈。ENTRY 费用按已退出/仍持有数量分摊，避免重复扣费。费用未回报或成本归属不完整时金额为空。
- 账户收益率分母为独立策略的期初权益加期间配置资金流的时间加权值；资金分配不是利润，不使用个人账户净值。
- 额外的借券/融资费用仅接受确认属于 DoxAgent 的实际费用；复用 `adjust-capital --fee-kind BORROW|FINANCING|OTHER [--execution-id ...]`，不会把整个券商账户费用自动归给策略。相同 ID 保持幂等。
- 假设仅评价原 ticker / 原方向，历史 BID_ASK 按方向开平仓，复用 Sessions.exit_at。历史秒级 tick 取完整过去一秒中最新有效状态，最大间隔 60 秒；不跳过最新无效一侧去挑旧好价，不回退 LAST/分钟线。不模拟对冲、替代 ETF、费用或滑动止盈。缺少可靠 MAIN 预算只展示收益率。
- 对冲保留策略自身数量；MAIN 已平、HEDGE 尚有数量时仍展示交易组。

## 资源与刷新

`POST /audit/revenue/refresh` 在打开页面、手动刷新时生成既有 views 表中的冻结 frame。只对新功能范围内的实际持仓取报价，并按必要日期边界补少量历史估值缓存。无常驻统计进程、定时行情采集或前端轮询。

`GET /audit/revenue/report`、`GET /audit/revenue/records/{identity}` 只读 SQLite；切换账户、期间、筛选、翻页和打开详情不刷新持仓行情。历史净值缺口保持为空；曲线使用真实用户观察时点及可准确还原的已实现收益。

`POST /audit/revenue/hypotheticals` 仅由非默认视图按所选期间分批懒加载，完成后覆盖全局统计；结果及同资产/秒的历史报价缓存复用。迟到真实成交会取消假设资格。报价工作在 API 的有界请求线程中运行，不占用单线程交易控制 runner。

## 显式启用

安装新代码后，使用新镜像执行一次：

```sh
python -m doxagent.revenue_audit.cli --read-db /data/read/v2.sqlite3 --runtime-db /data/runtime/runtime.sqlite3 activate
```

以实际环境路径为准。先显式安装 Runtime SourceOutbox 捕获（包括 te_capital_events），不执行 backfill；再执行上述启用命令。该操作在一个短暂的 Runtime 写锁内确认资金边界，在 read_meta 保存不可覆盖的启用时间及现有策略资金基线；重复执行返回原值。最后启动新版 Runtime Scheduler，以此之后产生的新 W3 数据为范围。GET 与应用启动不建表、不迁移、不补历史。

新 Gateway / Live 配置后正常分配独立策略额度即可；不需要重设启用时间。API 仍使用原有 Supabase 认证。回滚使用各组件既有 release 记录，保留既有账本和新增审计记录。
