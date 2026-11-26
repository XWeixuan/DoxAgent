# 50 GiB 根盘清理执行回执

执行依据为 [根盘全景与 50 GiB 方案](root-disk-50gib-cleanup-plan-20261118.md)。目标按 `df -B1 /` 的 Available 至少 `53,687,091,200 B` 验收。北京时间 20:25 开始，起始可用 `18,393,022,464 B`（17.13 GiB）。

## 批次

- D3 仅处理受退役 fence 保护、发布 manifest 内容逐份 SHA 校验一致的独立副本，并退派生过程视图。旧批处理有 SQLite 连接未关闭的问题，在写总回执前中止，已落盘的单对象回执和原子文件替换保持有效。使用显式关闭连接重跑，后续回执记录 260 个目录，释放 880,009,216 B 文件分配块，错误 0。前一批只单独按实际文件收据核算。
- 3 个无 Chrome 进程持有且取得 profile owner 锁的缓存目录组退出，共约 437,628,928 B。仅清 Cache、Code Cache 等已列入产品再生白名单的目录。
- Reuters 旧取证数据库和 COHR 的旧 Research 压缩恢复包迁到受限 D 盘，逐份比对远端原始 SHA。文件定位与 SHA 见 `artifacts/audits/root_disk_50g_execution_20261118/host-archive.jsonl`。浏览器当前 profile 与数据库保持在线。
- Event 历史精确版本归档到 `D:\DoxAgent-Cold\20261118-50GiB`。每批压缩包包含相对路径、版本、SHA 和原 inode/尺寸，D 盘回读对比 SHA 后才允许远端退出。首批五份 SQLite 恢复样本通过 `quick_check`，精确版本均存在。第一次现场复核用全库 data version，其他业务写入会造成误跳过。后续改为同一 owner 锁下复核精确根、活跃 activation、Case 和任务。最终 29 个包、3,511 个物理分支退出，核销 `14,107,885,568 B`（13.14 GiB）；压缩包占 D 盘约 3.48 GiB。中段有一次 SSH 传输停滞，终止了未完成的连接，从已验收批次之后续跑。
- 已退役工作区存在空目录树。按 Worker fence、complete 标记与活跃家族复核，仅对 `context` 下空目录执行底向上 `rmdir`。8,667 个目标工作区累计移除 2,066,242 个空目录，核销 `8,470,892,544 B`（7.89 GiB）。抽验的一棵树在清理前无普通文件，仅目录分配就约 2.2 MiB。另一项上下文大文件查重没有命中，收益记 0。
- BE 已正式采纳，移除了未被打开的历史完整过程导出 `control_nodes.json`（629,776,384 B 分配量），仍保留正式发布包与结算摘要。SDK 的正式 owner 接口退出 260 个已结算会话，核销 459,464,704 B，错误 0。D3 Runtime 的已消费过程退役入口尝试 4,458 个 owner，成功 2,865 个。其 Research 库内页收益在物理缩库统一结算。
- Research 走已上线的历史正文转换。首批 32 条全部校验通过。中段锁等待沿相同游标退避重试，至少 26,675 条确认转换，最终记录冲突为空。物理缩库前约有 19.83 GiB 可复用页，原文件约 45.79 GiB。
- Acquisition 维护时暂停 Collector 和 Bus，源库、目标库、关键表行数、`quick_check` 和 schema 核对后切换。首次启动后发现 `VACUUM INTO` 的结果回到了 DELETE 模式，短暂停止两个 owner 恢复 WAL，再从新启动后验收。采集配置刷新与新数据接受均确认，随后退役原件。原库分配 `20,489,244,672 B`，新库 `13,962,596,352 B`，差约 6.08 GiB。首段维护至开始恢复约 33 分钟，之后 WAL 恢复和新捕获验收约 1 分钟。期间的实时覆盖未作完整核算。
- 进入 Research 缩库前，根盘可用 `46,306,721,792 B`（43.13 GiB），仍低于目标。停 Research owner 和 Projector，Read API 继续响应现存投影，Collector 和 Bus 在运转。替换库开始前核对源表计数，缩库后候选须处于 WAL 模式，原件保留到服务和正文读取验收通过。
- Research 新库通过 `quick_check`、schema 和关键计数，保留 `codex_runtime_records` 78,085 行、D3 runs 5,580、staged sets 5,404、source receipts 47,219，重新开启 WAL。切换及重启约在维护开始 13 分 18 秒后完成。API 与 Worker 健康，12 份历史发布正文重新读取并按原 SHA 验证，然后退役旧库。物理库从 `49,171,550,208 B` 缩到 `27,809,042,432 B`（约 45.79 → 25.90 GiB）。此前已经写入 gzip 的块计入当时根盘占用，未把临时写入隐藏在整盘结果之外。

## 最终验收

Research 旧库退役后 `df -B1 /` 立即确认 `67,626,684,416 B`（62.98 GiB）可用，超过 50 GiB 目标。21:32 第一轮复验显示 23 个容器运行，含一个并行测试容器。API、Worker、Site Access 的健康探针均返回 200，生产采集在正常接收新数据，PENDING 为 0。Read 的 Research 与 Initialization 水位已追平，Bus 与 Runtime 正在消化维护期积压。已知 Runtime 历史缺口仍是 17,207。

北京时间 **21:33:48** 完成最终复验。`df` 可用 `67,477,405,696 B`（**62.84 GiB**），使用率 **84%**，相比起始净增 **45.71 GiB**。22 个生产和基础设施容器运行，采集 PENDING 为 0。Bus `12154932`、Initialization `163324`、Research `120802`、Runtime `5008881` 四路 checkpoint 与本次观测 head 一致，无源错误。两个 SQLite 目录均无本次缩库临时库或旧库残留。最终结果以根分区实测为准，已扣除期间业务新增、压缩文件与写入波动。

完整探针与水位记录见 [最终验收 JSON](../artifacts/audits/root_disk_50g_execution_20261118/final-acceptance.json)。

事件压缩包中的 `manifest.json` 记录原相对路径、所需版本和 SHA。离线恢复时，从对应包取出指定的 SQLite 文件到暂存路径，核对 SHA、`quick_check` 和 `library_versions` 后按原相对路径恢复。恢复前检查当前版本和服务引用，避免覆盖同名的活动库。两份宿主冷档也附有 SHA 和原路径回执。
