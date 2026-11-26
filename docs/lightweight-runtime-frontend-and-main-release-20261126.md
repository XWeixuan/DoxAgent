# 轻量运行前端适配、主线整合与发布

日期：2026-11-26。状态：前端、本地主线整合、兼容迁移及远端应用发布完成。固定产品源码 `f429dd4f1c96364b3baa03fbc5ab85501b3230f2`；发布 Owner 于北京时间 21:57:22 完成最后切换/资源协调，随后完成服务和认证读取检查。

## 实际修改

- 启动模块：原“运行模式”改为“交易模式”；原“研究运行方式”改为“运行模式”，两个选项为“普通运行”“轻量运行”。只在强制初始化时提交目标模式，复用当前文档时继承已有模式；没有增加前端转换入口。
- Overview：状态为 RUNNING 时，原位置的“运行中”改为当前“普通运行”或“轻量运行”；暂停、停止、初始化及健康状态保持原义。移除监测模式栏下方此前额外增加的模式文字，也移除 Case 来源行此前额外增加的模式文字；没有新增用户未要求的标签或产品组件。
- 轻量 ticker 的预期研究、交易策略导航禁用，不带 href；直接打开这两个地址回到基础投研，不发起 D2/D3 内容请求。模式与页面复用同一 ReadContext 查询，不增加独立模式端点、轮询或全 ticker 预取。
- 轻量链路隐藏 W2、一轮判定交易、policy 相关事件三个节点及其边，即使周期中有旧历史计数也不显示这些节点；W1 居中，其余业务节点保留。普通运行图与真实历史 Case 数据不改写。

代码落点：`frontend/v2/src/pages/overview/start-form.tsx,monitor-row.tsx`、`components/state.tsx,ticker-navigation.tsx`、`pages/ticker-workspace.tsx,runtime.tsx,runtime-case.tsx` 及原样式文件。

## 全部本地开发改动整合

以本地 `434711945a93df0f520ea871087c38df14cc94b0` 保存产品开发快照，再合并当时最新 origin/main `fcb3dd9bc` 的 47 个提交。产品源码、测试、部署配置和关联文档纳入提交；原始 eval/artifacts/exports、缓存、凭据和本地私有备份不打包。已被远端版本化且与本地冲突的 7 个收据先原样备份到 D: 测试目录，其中两份 COIN 收据内容不同，其余五份与远端相同；备份没有覆盖或删除。

审查涵盖轻量运行、初始化断点/Guardian 修复、O2 有界 survey、CDECR OpenRouter、交易归属与 EXIT 恢复、W3 本地评估/回测、Read 投影及前端契约。合并保留 origin/main 的全局 O3 关闭控制、W3 隐藏 W2 输入选项和 Selection 的有界查询，未覆盖生产已有账户、模型或交易配置。W3 提示词相对 origin/main 无差异。

新增两个必要的资源兼容点：轻量 effect 完成的既有 dirty 通知携带内部模式值，在原查询/事务内产生，不增加一次读取或通知写入；Scheduler 据此复用 O2-only 补充维护，不为未维护的 Policy 记录加载完整 W3 Context。历史 Event 回收在无 Policy 版本时跳过 Policy 数据库读取。没有新增 Runtime、Worker、线程池或常驻服务。

远端 `/home/ubuntu/doxagent` 有未提交修复与大量历史 eval 删除痕迹。发布将从固定提交的 Git 对象构建，保留该工作区现场，不能用 reset/clean 消除这些差异。Control/CDECR 的实际镜像与旧发布台账不一致，需要先以真实容器保存私有回滚配置，避免发布时退回过时环境。

## 验证

| 检查 | 实际结果 |
|---|---|
| 合并后的整合大组 | `artifacts/lightweight-main-integration-20261126.xml`：603 passed / 15 failed。4 项为夹具与当前契约不符：三项两 ticker 退出用例仍使用单 ticker 预算，另一个 W3 mock 缺少冻结模式。修正夹具，没有放宽产品交易约束或删除断言。 |
| 最终相关回归 | `artifacts/lightweight-main-final-targeted-20261126.xml`：116 passed，覆盖轻量、资源回收/补充维护、原交易/退出及 W3 回放输入。 |
| 现役功能兼容专项 | `artifacts/lightweight-production-compatibility-20261126.xml`：23 passed，覆盖全局 O3 关闭、O3 切换栅栏、W3 隐藏 W2、冻结存储、资源归属、人工平仓财务与固定资金槽位、发布保留策略。另修正一个旧 SimpleNamespace fixture 的 FULL 模式字段，不改变产品判断。 |
| 前端 | `artifacts/lightweight-ui-release-tests-20261126.log`：78 passed / 17 files；schema/typecheck/build 通过，现有大 chunk 提示保留。 |
| 桌面浏览器 | `artifacts/lightweight-ui-release-browser-20261126.log`：10 passed，1262/1559px；普通/轻量状态、两个标题与选项、无额外模式副标题、复用禁用、轻量 Case/链路、禁用页面直接访问、普通链路及原 Overview 指标。截图在 `frontend/v2/test-results/lightweight-*.png`。导航测试确认无 D2/D3 请求，两个 ReadContext 分别属于被拒绝页和跳转后的基础投研，基础投研自身复用已有查询。 |
| 静态检查 | 相关 Python compileall、新模块/专项测试 Ruff、Git whitespace 检查；没有 W3 提示词改动。 |

剩余 11 项整合失败均属于前次已在原 HEAD 复现的 Read/API fixture 问题：binding advanced_dedup、JSON subtype 五项、Capabilities 的过期 account_mode 字段、Projection freshness 两项、Projection recovery 与 Overview 无 Control fixture 各一项。本次没有删断言或声称整合大组全绿。此前两个不稳定的 WAL/分页用例未算作本次修复，仍见前次实施记录。

一次浏览器复跑未设置专用 fixture base URL，访问了默认旧地址而六项失败；改用明确的 8196 地址后最终十项通过。没有据错误地址的测试结果修改产品行为。

## 数据库迁移

已核对生产 Scheduler 的数据库 project 与连接器一致，为 `etzupajqhqylonxdhcax`。使用 Supabase migration 工具应用 `lightweight_terminal_policy_nullable`，返回成功；回读确认 Policy 列允许 NULL、新模式一致性 CHECK 已安装、RLS 仍开启。没有回填或改写历史业务行。

仓库文件改为与远端迁移台账相同的 `supabase/migrations/20261126134550_lightweight_terminal_policy_nullable.sql`，替代先前尚未部署的本地时间戳文件，避免后续 CLI 重复执行。增加 3 秒 DDL 锁等待上限；CHECK 为 NOT VALID，不扫描历史全表。

## 应用发布与生产验收

发布沿用 `scripts/ops/storage_release.py` 的构建、部署锁、切换、源码检查和回滚 Owner，没有新建发布服务或运行体系。一次性准备脚本只在私有发布目录为现有 Owner 生成 manifest；没有把生产环境值、令牌或密码提交到 Git。

| 产物 | 实际 Image ID |
|---|---|
| 共用 Backend | `sha256:32245f577e6812ec6393b96ab50a361092ec669432e28caa9b674e8ac802cccd` |
| Web | `sha256:72c0a91ac61e666d83d68b717bf66bf982d2daae36051e6ac641a51f8433c050` |

更新 15 个后端角色：API、Projector、Control、Initialization、CDECR Executor、O4、Maintenance、Delivery、Transport Maintenance、Collector、Content Enrichment、Message Bus、Trade Executor、Codex Worker、Scheduler；以及 Web，共 16 个角色。Site Access、Chrome Supervisor、MCP Tunnel 和现有代理不属于此次变更角色；Guardian 保持停用，没有借发布重新启用。相关开发源码/配置已进入主线，停用 Guardian 的旧运行容器没有作为验收对象启动。

采用现役容器的有效 environment/command/entrypoint 生成候选，只有构建提交和源码摘要元数据更新；私有快照与上一镜像均由原 Owner 留存。Control/CDECR 两项旧台账已按真实运行镜像校正。Worker/Scheduler 使用原 idle fence、协作停止和恢复路径；没有强行中断模型任务。Collector 使用原发布流程正常停止、安装既有 acquisition 成功过期索引并恢复。发布窗口检查未结算策略订单为 0，没有撤单、修改持仓或提交测试订单。

实际验证：

- 16 个角色全部运行；有健康检查的全部 healthy，restart count 全部为 0，现役镜像和发布台账一致。
- 每个后端角色校验 167 个变动源码、契约及 W3 提示词文件，15 × 167 项一致；Web 完成静态资源与 API auth/config 健康检查。
- 发布前有效环境与候选/发布后环境逐项一致（仅上述构建元数据更新），全局 O3 开关仍为 False。
- 11 个已激活 ticker 均仍为 FULL：BE、CEG、COHR、COIN、INTC、IONQ、MU、NBIS、ONDS、RKLB、SNOW；未新增初始化任务或提交模式转换操作。
- 生产 HTTP：`/healthz`、首页、`/api/doxagent/v2/auth/config` 均 200，匿名 `auth/me` 仍拒绝 401。
- 通过已连接 DoxAgent MCP 的正常认证路径读取 Overview、MU 一页 Case 和选中 Case 的 main 详情均成功，确认旧 FULL Case 的 W1/W2/W3 资源可读；未批量读正文或模型推理。
- 核验时四个投影 head=checkpoint、error=null，Bus head 自然推进。保留真实 `STALE` 标记和已有 Runtime 历史 gaps（17,218），不将历史存量问题描述为清零。首次显式选择 CURRENT_TRADING_DAY 被感恩节休市周期校验拒绝；使用服务默认可选的 PREVIOUS_TRADING_DAY 正常读取，没有因此修改日历或绕过校验。
- 结束时主机约 21 GiB available memory、根盘约 36 GiB 可用；这是短窗口快照，不是长期性能或延迟改善结论。原 Owner 资源协调 errors 为空，没有删除业务数据库或更换代理地区。

公开收据：[角色/环境/HTTP 核验](../artifacts/lightweight-release-20261126/verified.public.json)、[认证读取核验](../artifacts/lightweight-release-20261126/read-probe.public.json)。私有 rollback/state/compose 与 manifest 留在 `/var/lib/doxagent/releases/lightweight-main-20261126`，有效值不输出、不提交。

轻量 UI、路由、O2、ACK、故障恢复在本地隔离 fixture 中验证；生产保持全部 FULL，因此没有宣称已完成真实轻量初始化、真实轻量模型闭环或 FULL→LIGHT 周末转换的生产验收。首次启用仍须由用户明确提交实际业务操作。本轮没有测试订单或历史 Case 重放。
