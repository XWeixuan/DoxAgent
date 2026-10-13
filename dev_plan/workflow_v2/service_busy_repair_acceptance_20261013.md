# SERVICE_BUSY 修复实施与验收（2026-10-13）

范围：按 `service_busy_chrome_io_root_cause_and_repair_plan_20261013.md` 修订后的方案实施。时间为北京时间，生产发布及验收约03:03–03:25。未新增内存/磁盘保护、并发门控、后台清理任务或持久化target台账；未执行全机、Supervisor、消息总线或研究Worker重启。

## 1. 实际代码修复

- **45秒预算失效的机制已复现**：线上Playwright 1.63.0的 `Channel._inner_send()` 在取消后调用 `Connection._abort()`，后者仍等待原命令回复或transport错误，无独立退出条件。浏览器没有回应时，外层asyncio timeout已取消任务，却仍卡在取消确认。新增测试直接调用安装SDK的真实代码，证实超时后任务仍未完成；解除假transport阻塞后才以TimeoutError退出。
- **External页面创建不再经过该newPage取消路径**：复用当前raw-CDP执行 `Target.createTarget`，以唯一 `about:blank#doxagent-<uuid>` 标记匹配Playwright本地页面状态，先取得target ID再等Page初始化。原请求deadline覆盖创建/绑定；permit在异常/取消时归还，申请计数进入诊断。没有新增控制器或正常路径的逐页面数据库写入。
- **自有页面完整收尾**：创建后立即在当前CDP对象内保存target ID，成功绑定的Page也保存ID。关闭通过原raw-CDP完成；创建失败关闭暂时失败时保留当前连接内的所有权，detach时再次收尾。人工接管 `abandon()` 时删除业务所有权，不关闭人工页。集合只随当前连接存在，不是持久化/跨epoch治理系统。
- **detach/socket/task清理**：单个target关闭异常不能跳过raw-CDP关闭。socket和reader/task收尾进入独立finally，连接清理后释放entry引用。
- **读池恢复去掉Bus启动写锁**：API父进程受控启动时一次初始化监测词schema；`query_worker=True` 的读取、stream及控制worker构造MonitoringTermsControl时使用 `initialize=False`。控制写命令的原事务不变，缺schema不返回伪造空数据。不改请求预算、池大小或替补机制。

本地源码与生产消息源staging分别应用同一小补丁，未用脏工作区整包覆盖生产。API基于原V2.1 r2，Site Access基于消息源修复r1。

## 2. 发布、现场恢复与真实失败

| 对象 | 最终生产版本 | 结果 |
|---|---|---|
| API | `doxagent-v2:service-busy-20261013-r1`，image `86150631d2f6` | healthy；read 2/2、stream 1/1、control 1/1，全部空闲时available齐全、waiting=0 |
| Site Access | `doxagent-site-access:lifecycle-20261013-r2`，image `6ba737865e73` | healthy；READY、5 attachments，正常并行采集3页，acquiring=0、stalled=false |
| Chrome Supervisor | 原容器 `88b79b5b3d0e...` | 容器未重建/重启；仅Yahoo实例做一次恢复 |
| 其他运行容器 | 按两次发布前后ID检查 | 未被本次发布重建，研究任务及消息总线保留 |

第一次发布后API和Site Access均healthy，但历史Chrome工作集仍使磁盘读取约156MiB/s、wa=86%，CDP查询多实例超时；前端一次刷新仍出现SERVICE_BUSY。**没有将第一次healthy检查误记为完整修复成功。**

Yahoo仍UNHEALTHY、预热附着失败；核实 `yahoo-main` 无maintenance session、无进行中业务请求后，通过现有Site Access drain接口请求正常停止。接口20秒等待返回 `chrome_unclean_shutdown`；没有强杀进程、删除锁或重建Profile。Chrome随后完成退出，原Supervisor自动用同一Profile/出口启动generation=2，runtime恢复AVAILABLE。原instance `c19bf9032f274015aa1b4a901ba4c104` → `d3b0110cdb2f43cf84d424548ae15f98`。cookies/登录数据、身份配置保留；历史Yahoo标签页被该次实例恢复释放，没有活动人工维护会话。

历史拥塞期间首次新代码也遇到target关闭无响应，留下11个带唯一业务分配标记的空白target。现场证据驱动补齐“当前连接保留失败关闭的所有权”，通过回归后仅补发Site Access r2。一次性清理操作先捕获精确ID/URL，至少超过60秒后重新核实ID及标记完全一致、相关Profile无人工维护会话，再关闭这11个明确过期的业务target。未按about:blank批量删页；其余无归属证明的历史空白页保留。页面仅为空白分配，没有删除业务正文、数据库或Profile。

发布脚本第一次结束时有一处校验断言：Docker重新排序了Site Access挂载数组。已逐项核实destination/source/RW完全相同，校验改为按destination排序，补全发布receipt；没有因这个校验问题再次重建API。R2再次核实挂载、环境变量不变。

## 3. 验证证据

### 自动回归

```
uv run --no-sync pytest -q tests/test_service_busy_lifecycle_repair.py tests/test_site_strategy_lifecycle.py tests/test_message_source_recurring_repair.py tests/v2_backend/test_monitoring_terms.py tests/v2_backend/test_api.py
```

最终 **40 passed**。涵盖：SDK真实取消确认挂起、创建回复丢失/绑定超时、重复故障后target和permit收尾、并行创建正确绑定、target关闭失败仍关闭连接/driver、socket关闭失败仍回收task、首次关闭失败保留所有权并在detach重试。

测试Bus库持续持有真实BEGIN IMMEDIATE写锁时，真实spawn的QueryRunner worker成功启动；主动回收该测试worker后替补仍能启动。未对生产worker注入故障。

真实隔离Chrome使用临时独立Profile、不访问网络页面：并行创建/关闭6个页面；模拟“真实target已创建、Page初始化尚不可见”后约0.109秒按测试预算退出，target集合回到原始集合，初始页面保留。测试结束关闭隔离浏览器，临时Profile正常移除。

针对新增文件Ruff检查通过，修改文件 `git diff --check` 通过。未宣称全仓所有测试通过。

### 生产

- 生产文件SHA256与本次验收版本逐一相同：
  - app.py：`f5bae15683b4d8eb70c4a0f22f8354f2122e137efcaed83937456d52113b4409`
  - monitoring_terms.py：`28a4e5777e0f28588508fece72efda587905849d51190ab3fbdd52055ea9d469`
  - external_runtime.py：`8b66935d14d5d2e2b796ed6c61fce363c8e907b054abf30e2655fba58399f958`
  - runtime.py：`8af3d7ce4a15bdebada46889c67b5b6654a1e1f3eff96ce4bc6d57ce71746311`
- 浏览器沿原有登录会话实际打开Overview、MU基础投研正文、MU消息流。恢复阶段记录过一次SERVICE_BUSY；最终Overview和MU消息流刷新完成，MU研究正文已加载，未显示SERVICE_BUSY/STORE_UNAVAILABLE；不是仅检查匿名healthz。
- 03:18:50–03:19:11另有read-context短时STORE_BUSY日志（SQLITE_BUSY、broken=False，未回收worker）；03:24核查此后近5分钟未再记录该错误。将其保留为恢复期间的真实锁竞争，不将其冒称为原替补worker启动迁移失败，也不声称整个验收窗口零错误。
- 既有Message Bus的真实自然轮询恢复：Yahoo四ticker成功；Barron's MU/BE/INTC/RKLB、InvestorsHub多ticker在恢复后有成功时间戳。补发Site Access期间存在短暂ConnectError/ConnectTimeout，不把单条失败或旧last_success冒充全源稳定。
- 既有Site Access owner完成真实Barron's最终发布者正文提取：HTTP200，`browser_article_dom`，`FULL`，约27.2秒，content_length=3810，47句，最终URL `https://www.barrons.com/articles/micron-stock-memory-chip-cxmt-threat-51baf6e0`。测试没有发布诊断消息，也没有注入认证。
- 03:20:47后快照：六实例CDP列表均可读取，page数7/1/2/5/18/1；唯一业务分配标记的空白target均为0。并行中的3个正常页面已导航，活动请求均小于45秒，stalled=false；不把正常活动页当泄漏。
- 同段vmstat两次瞬时采样：磁盘读约980/11824 KiB/s、wa=0/0、blocked=0/1。Chrome约3.30GiB/5GiB，未更改5GiB配置；根盘约5.4GiB可用。该变化支持残留Yahoo工作集对I/O拥塞的重要影响，但不证明所有历史峰值I/O都来自它。

## 4. 边界、镜像登记和回滚

- 本轮不新增内存/磁盘保护、资源包络、固定余量门槛、定时告警、自动浏览器重启或研究并发限制；正常并发保持。没有运行数据清理或数据库压缩。
- 正文订阅权限错误（如DIGITIMES subscription_required）未伪装为成功；本轮不扩展成所有来源登录/订阅修复。Overview另有IB Gateway“断开”和既有统计完整性提示，本轮未修改IB或统计语义。
- 有限验收窗口可以证明当前可用、已发生的失败路径得到修复，不能证明长期所有泄漏完全排除。其余历史空白页没有所有权证明，不删除；后续容量应按健康工作集评估，而非新增准入保护。
- 本轮构建镜像登记：API `service-busy-20261013-r1` 正在使用；Site Access `lifecycle-20261013-r2` 正在使用；Site Access `lifecycle-20261013-r1` 是r2构建父镜像和显式回滚点，保留。没有本轮创建且无运行/构建/回滚依赖的临时镜像待删除。其他对话/运维创建的disk-repair镜像不属于本轮，不擅自处理。
- 远端receipt：`/home/ubuntu/service-busy-repair-20261013/release.json`，只保存镜像/容器身份与Compose文件路径，不保存token/env值。API原Compose链加 `v2-api.yaml`，Site Access原链加 `v2-site-access.yaml`、`v2-site-access-r2.yaml`；回滚只需对应服务恢复原image overlay，沿相同链 `up -d --no-deps --no-build`。不回滚数据库、不重启Supervisor。
- `changelog` 已追加本次实现和现场恢复记录。未提交或覆盖工作区其他既有变更。
