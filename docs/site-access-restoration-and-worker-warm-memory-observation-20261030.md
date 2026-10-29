# Site/Chrome恢复后10分钟资源与Worker预热内存实测

## 结论

本轮没有重现此前持续的内存回收/I/O失控。Worker在观察窗口由3.36GiB降至2.41GiB，没有再次扩张至9.9GiB；可用内存最低2.34GiB、结束3.88GiB。CPU仍较忙、恢复初期出现短暂换页，不能把10分钟结果当长期容量证明。

**此前9.9GiB不能作为正常时段预热需求。** 本轮在途任务0–3个，W3已确认READY预热3组，PSS约519–610MiB；另有1组未映射到当前job或ready slot的环境，结合通用warm target=1，视为通用预热候选，约123–232MiB。两者合计约720–797MiB，即**约0.7–0.8GiB**；其中通用环境归属为推断，确定的READY部分单列。没有证据支持将此前整个Worker峰值归因于预热，维持现有策略，不做削减。

## 操作与观察范围

- 用户明确要求忽略此前非正常生产状态下的定时探测，先恢复完整服务后观察。本轮独立记录，不把该探测作为正常生产基线。
- 2026-10-30 **03:36:08北京时间**启动既有正式Chrome Supervisor、Site Access容器；未重建镜像或改配置，Profile、登录和持久化数据保留。
- 资源窗口：**03:37:23.654–03:47:23.654北京时间**，11个一分钟采样点，完整600秒。启动时点、恢复前快照、结束后的功能核查分别记录。
- 没有改动W1/W2/W3执行能力、并发、模型、SDK预热、消息周期、身份与正文路径；未制造测试新闻或额外模型调用。仅观察自然负载。

## 资源数据

组件内存为cgroup memory.current，包括匿名、文件页和内核；是采样峰值，不是连续真实峰值。

|组件|窗口最低|采样最高|结束|
|---|---:|---:|---:|
|Worker|2.41GiB|3.36GiB|2.41GiB|
|Site Access|2.85GiB|3.58GiB|3.03GiB|
|Chrome Supervisor|2.88GiB|4.10GiB|3.80GiB|
|Scheduler|1.90GiB|2.78GiB|2.29GiB|
|整机可用内存|2.34GiB|4.38GiB|3.88GiB|

- CPU busy均值69.33%，iowait均值1.90%；平均读15.32MiB/s、写7.40MiB/s、约1539 IOPS，分钟区间await均值约0.88ms、磁盘util均值21.10%。不是此前持续高I/O等待状态。
- 内存PSI some按累计计数差计算的窗口停顿占比0.514%，后5分钟约0.092%；后5分钟iowait约1.54%。没有持续的85%级内存停顿。
- Swap占用1.26–1.37GiB，结束1.32GiB；窗口换入146MiB、换出178MiB，主要压力出现在恢复前段。残留Swap不等于正在发生持续抖动，也不是零换页。
- Worker匿名内存结束约1.84GiB，其余为文件页/内核等；结束无执行中或排队job，READY保持3组。窗口任务0–3个，包括实际W3决策与O2维护，不是此前集中消化积压时的20个左右并行job/大量准备环境。

## 预热实际占用

通过活进程的DOXAGENT_CAPSULE_ID关联SQLite中的READY槽、当前job及USED/BOUND状态；读取同组Python/Codex/MCP子进程的smaps_rollup，按PSS累计，避免直接相加RSS重复计算共享页。共5次归属快照；读取仅输出归属和内存，不留存环境凭证。

|分类|实测驻留PSS|说明|
|---|---:|---|
|确定W3 READY预热，3组|519–610MiB|未绑定执行中的job；READY物理进程已验证|
|通用预热候选，1组|123–232MiB|未映射到job/slot，配置通用warm target=1；归属保留不确定性|
|上述合计|720–797MiB|约0.7–0.8GiB，不含已使用环境和控制器|
|USED后保留环境|430–623MiB|另列，不能统称未使用预热；不据此断言泄漏|
|生命周期控制器|48–57MiB|独立公共控制资源|
|正在执行的job|0–665MiB|随任务完成变化，不归入预热|

PSS是当前物理驻留份额，会随共享页和换页变化；不代表新开一组环境的严格边际成本。Worker自身服务/研究缓存、未映射资源及文件缓存不全属于预热，不能拿Worker容器总额替代预热成本。当前观测支持“积压集中消化明显放大峰值”；尚未逐一拆出旧峰值中每个阶段的占比，因此不声称已精确证明全部历史9.9GiB的来源。

## 功能核查与限制

- Site Access ready，Site/Supervisor/Worker均持续运行并健康，无本轮重启或OOM记录。结束10个ticker的consumer pending均为0。
- 结束附近5分钟Site既有访问结果有375次SUCCESS，同时仍有SERVICE_UNAVAILABLE 9、AUTH_REQUIRED 3、CONTENT_ERROR和ACCESS_EXHAUSTED各1。服务恢复和资源缓解不等于每个来源访问能力全部恢复；本轮没有用这些数据宣称全部采集/正文指标达标。
- 当前浏览器组件合计仍约6.83GiB，CPU也有实际负载，资源余量不是无限。此窗口只覆盖较少在途模型任务下的自然生产运行，不能证明再次集中消化数百条积压或20 ticker极限负载也有同样余量。
- 当前保留服务运行和原预热策略，不以这次实测为由关闭预热、降低覆盖或引入W门禁。

证据：`eval/browser-restore-10m-20261030/`下的restoration.txt、before-host.json、resources.jsonl、analysis.json、methodology.md、site-ready-start/end.json、runtime-streams-start/end.json及end-host.json。观察程序均为临时只读宿主探针，没有启动临时容器或修改应用代码。
