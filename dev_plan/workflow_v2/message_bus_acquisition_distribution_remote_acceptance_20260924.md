# Message Bus 采集模式与共享分发远端部署验收（2026-09-24）

## 范围与版本

- 本地 `main` 全部变更已提交并推送为 `a7889a34`；远端 `/home/ubuntu/doxagent` 快进拉取到同一提交。
- 按用户后续明确边界，仅重建并重启 V2 业务后端、Web 和 Initialization Guardian。**没有重建或重启** Site Access、Chrome Supervisor，也没有运行登录桌面安装程序或修改在线 Site Strategy Registry。
- Site Access 容器 ID 前后均为 `1adfbe97606e95fa0292a71595cee0fe0ee643c6aa44784734e7f01e637e78cf`；Chrome Supervisor 前后均为 `5aa49999899bf3da7eefd3790826a1cbfcebb733754ebd8952fcafb730c6d4e3`。Clash 容器亦保持运行。

## 发布与迁移

- 远端实际使用基础 Compose + `deploy/docker-compose.server.yml`，Guardian 另叠加 `deploy/docker-compose.initialization-repair.yml`。业务镜像 `doxagent-v2:server` 和 Web 镜像 `doxagent-v2-web:production` 均完成构建；`v2-migrate` 返回 `schema_current=true`。
- Bus SQLite 迁移前在线备份为 `/data/bus/backups/bus.sqlite3.pre-a7889a34-20260923T175212Z.bak`，完整性检查 `ok`；采集模式迁移另生成 `/data/bus/backups/bus.sqlite3.pre-acquisition-20260923T180335233986Z.bak`。迁移后再次 preview 显示 `source_changes=[]`。
- Reuters Site Search、Google News Search RSS 已是 `by_search` 且启用；工商时报半导体入口 `ctee_semiconductor` 为繁中 `by_distribution` 且**禁用**。未自动创建订阅或监测词。现有 BE、INTC、MU、RKLB 的搜索绑定仍标记 `LEGACY_TERMS`，须经人工提交版本化词集后才能完成新 L1/L2 治理；未将旧搜索串臆测为新的投资定义。
- OpenRouter key 仅注入远端受限 `.env` 和 Message Bus 容器环境，未写入仓库或验收输出；`DOXAGENT_MESSAGE_BUS_JEV_ENABLED=false`，实验性 Jev 判定目前未启用，也未触发付费调用。

## 结果

- 远端 Message Bus `status`：`enabled=true`、11 个 source、25 个 ticker-source binding、1983 条 raw message、1817 条标准消息；`ctee_semiconductor` 的共享采集/分类/投递计数均为 0，符合禁用且未订阅状态。
- API `/healthz` 返回 HTTP 200，Web 首页返回 HTTP 200；API、Web、Codex Worker、CDECR Executor 以及保留的 Site Access/Chrome Supervisor 健康检查通过。Message Bus、Content Enrichment、Scheduler、O4、Initialization、Projector、Delivery、Control、Executor、Guardian 持续运行；短时日志未见新的 traceback。
- 本地相关测试 261 passed，受影响模块 Ruff、定向 mypy 和 Compose config 通过。工商时报的真实抓取/正文/分发与 Jev 实际判定**未做生产验收**：入口禁用、无订阅和已提交词集，Jev 开关关闭；这不是零故障生产覆盖的证明。

## 部署过程偏差与恢复

1. 最初对全部服务执行构建时，Chrome Supervisor 历史锁定的 Google Chrome apt 包不可从当前索引解析，构建失败；运行中的浏览器服务未受影响。用户随后明确要求不要动消息源策略治理，因此取消了相关构建尝试，未提交浏览器构建修订。
2. Bus 数据迁移先于正在运行的旧版 Bus/Content Enrichment 镜像切换，旧 Pydantic 模型不认识新增 source 字段，两个旧容器短暂重启循环。立即用已构建的 `doxagent-v2:server` 重建 Bus 和 Content Enrichment，恢复运行；此过程未回滚数据库、未影响浏览器 Profile。后续同类升级应先暂停这两个消费者、执行备份/迁移并立即切换新镜像，避免新旧模型并存窗口。
3. 批量业务重建时 API 启动耗时超过 Compose 初次依赖等待，导致该命令返回非零；API 随后自行完成初始化并转为 healthy，单独启动 Web 后 healthy。Guardian 所需的两个既有环境变量按旧容器配置显式传入后完成重建。

## 后续人工启用边界

只有在用户确认目标 ticker、三概念多语言监测词、相关/不相关定义及工商时报订阅后，才启用该 distribution 入口并做真实文章抓取、正文归属、分类/投递与实时/sweep 时效验收。Site Strategy 与浏览器服务在本次交付中保持现状；其新代码/静态配置不会因为本次部署而自动变更在线 Registry。
