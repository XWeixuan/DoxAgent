# 本次架构更新：首轮长时观察与Runtime失效调查

观察任务2已由PAUSED恢复ACTIVE，每两小时一次；新基线2026-10-30 01:18:19北京时间，阶段汇总不早于10月31日01:18:19，之后继续直到用户要求结束。原起点/停机/发布历史保留。用户随后优先授权Runtime紧急修复，定时任务仍只读；正式修复版本另记生效时间，必须覆盖该版本至少24小时，不把此前混合版本算成最终版本通过。

本次业务窗口16:00:32.571819Z–17:18:19Z，衔接上轮90分钟结算；宿主快照17:19:23Z、Runtime定点调查17:20–17:27Z，分别标记，不冒充精确同一截止。未修改生产数据/配置或修复应用；后续修复独立见[修复方案](../../../docs/runtime-stream-consumption-repair-plan-20261030.md)。

## P0：Runtime确定性消费失败

6个ticker队首正文以NativeContent引用存储，SQL别名绕过自动解码，直接Pydantic验证导致body需要字符串而实际dict。所有6条显式解码后完整正文校验通过，文件可读。顺序流一直卡住，527条等待；该缺陷当前main仍存在，不能靠仅升级旧Scheduler解决。NativeContent16KiB阈值使小正文能走、首次长正文堵住整个ticker。起因和代码历史、精益补丁/发布验证在修复方案中详述。

|ticker|原游标|积压|队首发布UTC|
|---|---:|---:|---|
|BE|2062|47|2026-10-29T00:28:52.736840+00:00|
|CEG|143|23|2026-10-29T11:21:23.241764+00:00|
|COHR|300|82|2026-10-28T17:14:21.972587+00:00|
|INTC|5004|171|2026-10-29T00:28:53.682953+00:00|
|MU|5101|127|2026-10-28T17:11:44.901378+00:00|
|NBIS|856|77|2026-10-29T00:28:54.697869+00:00|

SNOW当日11条case可经生产ReadStore.page返回（约69ms）；INTC、BE当日0、ALL各20条，历史未清空。Read runtime checkpoint=head，无当前source错误；54个旧provenance缺口仍留存。前端默认CURRENT_TRADING_DAY解释相关ticker空列表，但不能据此断言用户浏览器所有空白都已解释；未读取用户当前浏览器/认证筛选，存在独立Graph/API查询超时。

**验收纠偏**：之前PUBLISHED/Owner T1并不证明真实Runtime消费者能解析、claim并建case。本轮下表交付数字仅为Owner发布代理，不计为最终“Runtime可消费”目标通过。修复后必须同时验游标、具体case及UI；不新增正常路径重复校验/宽读。

## 消息管线指标（保留全部应执行）

- 应执行33,265，新鲜28,898（86.87%）；完全缺轮615、在途3，失败未从分母排除。详细source/ticker/必需unit见unit-metrics.csv和business.json。
- 真实新canonical66，首次FULL/SHORT_FULL57（86.36%），当前58（87.88%）；未独立抽检全部66篇，不以标签证明全量质量。
- Owner发布代理：应交付114，发布113，记录≤10秒102（89.47%），已发布mean5.907秒/p95 17.724/max31.839。66/66篇已关联唯一实际物理响应T0；**受Runtime故障影响，最终下游可消费率不可由该列替代**。
- 新鲜缺口主要Reuters2488应执行仅6新鲜，IBKR778/0，Yahoo3409/2964，DIGITIMES390/10，Calcalist233/64。RSS、bootstrap及旧结果不替代必需频道。
- DIGITIMES有http_500；截取到的BODY导航ERR_HTTP_RESPONSE_CODE_FAILURE已被Site归为TRANSIENT_TRANSPORT，不能把这个独立BODY样本直接认定为全部CRAWLER http_500的根因。Reuters/IBKR/Calcalist沿既有待查事实继续观察，不操作身份/网关。

## 编排非Agent/LLM跨度

3个真实新case（RKLB1、SNOW2），6个W3 effect持久化receipt，6个Worker作业。只报有明确边界的本地跨度，不把case生命周期/Worker roundtrip或phase.materialize_and_model当本地耗时。

|跨度|n|mean ms|max/p95 ms|口径|
|---|---:|---:|---:|---|
|effect_claim_elapsed_ms|6|3646.409|8011.332|按receipt或作业时间戳；不跨嵌套跨度相加|
|effect_thread_start_ms|6|0.429|1.284|按receipt或作业时间戳；不跨嵌套跨度相加|
|thread.ready_claim:per-attempt-total-ms|3|853.291|1207.532|按receipt或作业时间戳；不跨嵌套跨度相加|
|case.thread_prepare:per-attempt-total-ms|3|1090.577|1453.338|按receipt或作业时间戳；不跨嵌套跨度相加|
|context.prepare:per-attempt-total-ms|6|105.402|199.077|按receipt或作业时间戳；不跨嵌套跨度相加|
|case.context_bind:per-attempt-total-ms|6|153.639|647.146|按receipt或作业时间戳；不跨嵌套跨度相加|
|job_created_to_started_ms|6|29.867|33.456|按receipt或作业时间戳；不跨嵌套跨度相加|

claim最慢8.011秒，生产EXPLAIN显示SCAN runtime_v2_effects与临时排序；RUNNING的OR分支不能利用现有仅PENDING部分索引，且查询发生于BEGIN IMMEDIATE内，具备扫描/锁放大机制。约17,532 max(rowid)不等于精确行数。claim耗时还包含本地锁、SQLite及解码，当前不能把8秒全部归给单个SQL；独立后续处理，不混进紧急消息读取补丁。

case创建仅有created_at，缺少创建开始，耗时不可测；W1/W2未有独立本地/模型边界，不用总时长替代。目录只枚举5001项触及预算（不完整），41个时窗文件中含progress重复；不据此声称完整fork RPC覆盖。thread_prepare含READY取得，不能称fork请求执行。旧w3-slot-fork:09da...仍CREATING/RECONCILING、最后更新时间10月19日，记录旧非终态，不当新失败或擅自接回。

## 宿主及资源

同boot、同容器epoch差分16:11:46–17:19:23（67分38秒）：CPU busy52.81%、iowait1.52%，vda读5.02/写7.11MiB/s、约895IOPS、await0.671ms、util13.61%。可用内存3.95GiB、匿名9.83GiB、Swap存量1312MiB，区间swap-in211/out199MiB，文件refault约360万。sysstat.txt保留跨日后00:00–01:10十分钟粒度，不能当真实瞬时峰值。

Site匿名内存增加669MiB（至3439.8MiB），Supervisor增加90MiB（至3284.1MiB）。Site进程61→63（Python12→13、Node/MainThread10→11、Chrome仍30）；Supervisor155→174、Chrome117→131、Xvfb7→8，说明增加一套浏览器及一个控制worker、另有现有进程内部增长，不能仅凭RSS断言泄漏或持续收敛。Worker匿名下降30.5MiB，容量没有被新增硬限制；现阶段没有证据支持重新进入TB级饱和，但工作集增长和换页必须后续续记。

## 水位、缺测与边界

业务与新case cohort成功覆盖至17:18:19Z，SAR至17:10:01Z，cgroup文件取17:19:23Z；原旧观察缺口保留，未倒填。目录抽样/精确fork/W1W2本地阶段/UI T2不可测，分别保留，不推进为整体成功。仅写本地观察文档/证据和定时任务，没有生产部署/清数据/操作Chrome或限制W1W2W3。
