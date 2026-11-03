# Overview 下游 Case 判定失败数

日期：2026-11-03。

替换原“非例行维护触发数”卡片，返回字段改为 `failed_cases`。统计 Overview 全部未移除 ticker 在所选交易周期内、运行状态页结果为 `FAILURE` 的唯一 Case 数，按 Case 原语义交易日归属；不受标的表格运行/健康筛选影响。同一 Case 多个节点、尝试失败不重复计数，重试成功但最终未标记 FAILURE 的 Case 不计入。后置处理失败、交易未执行不直接等同于判定失败。

直接读取原有 `graph_cases`、`dimensions={"node":"FAILURE"}` 聚合桶，与运行状态图 FAILURE 节点 Case 数同源。当前/对比交易日一次批量读取全部 ticker，沿用读视图 seq 及已有捕获完整性证明。已有 Case 修订会替换桶贡献；不写新计数、不回填、不重算历史、无新表/索引/后台任务，也不读取 Case/attempt/contributions 明细。SQL 使用现有 metric/ticker/day 前缀，只有所选两个周期的小桶；额外完整性读取是现有 runtime 捕获证明的主键查找。

前端继续使用 Overview 原 Metrics 请求、周期选择、手动刷新和视图续期，没有新增 HTTP 请求或定时器。零值、未知完整性与环比沿用 Metric 结构。窄桌面为标题保留两行高度，让各卡数值对齐；不降低字体。

验证：15项后端定向测试覆盖真实 HTTP/DTO、跨 ticker 汇总、正常零值、节点失败不重复计数、周期、恢复减计及冻结视图；69项前端单测通过；1262/1559px 浏览器检查周期切换、数值对齐及无水平溢出通过。浏览器使用合成业务 fixture，生产版本/数据读取在发布后记录。前后端 OverviewMetrics schema 同步，保留其他端点兼容定义。变更已记入 changelog。

发布仅需 API/Web，无数据迁移或 Projector/采集/执行角色重启。生产 API 实际 bab0739d 镜像与旧发布登记存在差异；已核对实际镜像没有容器内源文件补丁，且 bab0739d 是本轮最新 origin/main 基线460fda18的祖先，本次构建包含这些已部署修改。

## 生产验收

功能提交 `b824879d133c9810b6876862188dfe296f08071a` 已先推送origin/main，再按标准release仅重建/发布API、Web。两个实际镜像revision与登记一致且healthy；其余角色镜像均未变化，余额只读secret挂载保留。生产静态包已含新卡片文案，旧文案移除。

2026-11-03 20:18北京时间，真实生产库10个未移除ticker聚合读数：本交易日135，前一交易日594，近7交易日1042，近30交易日1710；与已有SOURCE/FAILURE结果桶独立对照一致。每次新增指标计算约0.02–0.04秒。EXPLAIN确认当前桶走metric/ticker/day/dimensions索引，冻结视图历史分支走metric/ticker/day/valid_to范围索引，未扫描Case明细。原捕获证明尚未确认完整，因此保留未知完整性提示及“不适用”环比，不把已有读数宣称为已证明无遗漏。

本次生产验收为真实镜像、静态资源、数据与查询计划复核；布局/交互由真实浏览器配合合成fixture验证，未扩大声称为登录生产浏览器验收。脱敏[验收回执](../eval/overview_failed_cases_20261103/acceptance.json)。
