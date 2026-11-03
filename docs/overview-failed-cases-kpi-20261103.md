# Overview 下游 Case 判定失败数

日期：2026-11-03。

替换原“非例行维护触发数”卡片，返回字段改为 `failed_cases`。统计 Overview 全部未移除 ticker 在所选交易周期内、运行状态页结果为 `FAILURE` 的唯一 Case 数，按 Case 原语义交易日归属；不受标的表格运行/健康筛选影响。同一 Case 多个节点、尝试失败不重复计数，重试成功但最终未标记 FAILURE 的 Case 不计入。后置处理失败、交易未执行不直接等同于判定失败。

直接读取原有 `graph_cases`、`dimensions={"node":"FAILURE"}` 聚合桶，与运行状态图 FAILURE 节点 Case 数同源。当前/对比交易日一次批量读取全部 ticker，沿用读视图 seq 及已有捕获完整性证明。已有 Case 修订会替换桶贡献；不写新计数、不回填、不重算历史、无新表/索引/后台任务，也不读取 Case/attempt/contributions 明细。SQL 使用现有 metric/ticker/day 前缀，只有所选两个周期的小桶；额外完整性读取是现有 runtime 捕获证明的主键查找。

前端继续使用 Overview 原 Metrics 请求、周期选择、手动刷新和视图续期，没有新增 HTTP 请求或定时器。零值、未知完整性与环比沿用 Metric 结构。窄桌面为标题保留两行高度，让各卡数值对齐；不降低字体。

验证：15项后端定向测试覆盖真实 HTTP/DTO、跨 ticker 汇总、正常零值、节点失败不重复计数、周期、恢复减计及冻结视图；69项前端单测通过；1262/1559px 浏览器检查周期切换、数值对齐及无水平溢出通过。浏览器使用合成业务 fixture，生产版本/数据读取在发布后记录。前后端 OverviewMetrics schema 同步，保留其他端点兼容定义。变更已记入 changelog。

发布仅需 API/Web，无数据迁移或 Projector/采集/执行角色重启。生产 API 实际 bab0739d 镜像与旧发布登记存在差异；已核对实际镜像没有容器内源文件补丁，且 bab0739d 是本轮最新 origin/main 基线460fda18的祖先，本次构建包含这些已部署修改。
