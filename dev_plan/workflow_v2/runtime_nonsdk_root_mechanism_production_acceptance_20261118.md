# Runtime 非 SDK 延时生产验收

2026 年 11 月 18 日，北京时间。**发布和业务验收通过，完整 Runtime 性能验收未通过。** 修复版完整 Runtime 非 SDK P50/P95 为 **5.15s / 10.61s**，P95 超出 10 秒目标 **0.61s**。106 例全部收尾，无失败、无计时缺失，其中 6 例超过 10 秒。

## 版本、窗口和统计口径

- Scheduler、Codex Worker、Transport Maintenance 实际运行 `b99d0dbea20ad7e927f25b340ebe45176dfa72f9`，镜像 `sha256:7920f5e9e4c33bad326ff76868616ee679fe3b4f2845b0b196363aa53b934a7f`。三个容器均未重启或 OOM。
- 连续入组窗口为 07:06:54.733–09:07:00.889，对应美东 11 月 17 日 18:06–20:07。窗口内 134 个实时 Case，106 个进入 W3，28 个未进入 W3。首次取样 105 个 W3 Case 完成，09:08:48 复核同一批次后为 106/106。窗口未截断。
- 延续上一轮区间并集口径。完整 Runtime 从持久准入到 Operations owner 返回，扣除关键路径上的模型／SDK 区间并集，将已观测的 DoxAgent 本地工具调用计回。同一 Case 的 A/B 并行区间只扣一次，辅助 W1R3 不抵扣 W3 内部等待。SQLite 锁等待、Effect 退避、启动和排队均计入。窗口内无独立记录的 Provider Pause，本轮没有把无归因间隙从内部耗时扣掉。P95 采用原验收脚本的 nearest rank。
- 样本包含突发，滚动 60 秒内最高 13 个 Case、26 次分支领取。覆盖的是盘后运行，常规开盘高峰仍待验证。

## 性能结果

| 非 SDK 耗时 | 旧版 34 例 P50 / P95 | 新版 106 例 P50 / P95 | 新版最大值 |
| --- | ---: | ---: | ---: |
| W3 Trade | 4.51s / 19.98s | 2.72s / 6.78s | 9.39s |
| W3 Operations | 1.95s / 5.87s | 1.22s / 2.34s | 3.00s |
| 完整 W3 | 6.12s / 21.50s | 3.80s / 8.46s | 10.91s |
| 完整 Runtime | 8.42s / 24.81s | **5.15s / 10.61s** | **13.34s** |

旧版使用最近一轮同口径 34 例样本，两组 Case 和流量不同，表中变化是生产观测值。旧版记录见 [上一轮专项排查](runtime_nonsdk_remaining_critical_path_audit_and_plan_20261118.md)。

全批 212 个 Trade 分支及 106 个 Operations Job 均成功。逐例比对确认 A/B 物理线程不同，Operations 使用已提交的 Trade 分支线程。

## 剩余长尾

- **缺货仍是主要放大项。** 212 次领取中，168 HIT／44 MISS，分支命中率 **79.2%**。77 例双命中，14 例单缺货，15 例双缺货。双命中组完整 Runtime P50/P95 为 4.70s/8.57s。6 个超标 Case 中 5 个至少一分支 MISS。44 次缺货从绑定到 READY 的 P50/P95/最大值为 **2.66s/5.94s/6.36s**，operation 到 Fork 派发 P95 4.43s，Fork 派发到 slot READY P95 1.66s。最慢 NBIS（`…54c70a2a`）双缺货，Trade 首个 SDK 派发前花 8.99s，两份冷准备分别为 5.94s、6.05s，均在同一段墙钟内。
- **READY 命中仍可能遇到研究材料固定退避。** INTC（`…dc4e358d`）双命中，Worker 两次 READY 领取只花约 17ms、13ms，但 Trade 首个 SDK 派发前花了 **8.51s**。第一轮 owner 在 `context.prepare` 得到 `W3Error` 并返回，距下一轮 owner 启动 **5.11s**。同期回执和代码符合 `prepare_case` 在精确 D1/D2 pin 后台准备时抛出 `w3_prepare_pending`，随后 Scheduler 按 5 秒退避重领。全窗日志只有 1 个该位置的 `W3Error`。这段等待应由已有准备 Future 完成时唤醒，延续原 Effect 所有权及冻结版本。
- **提交长尾仍偶发。** INTC（`…8db6b73`）末个 Trade SDK 结果到 owner 返回花 **3.10s**，最终 owner 本体 **2.58s**。两笔独立记录的分支保存及汇总提交，其写等待分别约 264ms、318ms。其他重叠 span 不能逐项相加。全批 Trade SDK 后内部 P95 **1.59s**，最终 owner P95 **1.08s**；Operations 对应 **0.90s／0.66s**。424 个结果交接 span P95 **142ms**。
- **W3 前置仍占用端到端预算。** 入组到 W3 路由的非模型耗时 P50/P95 **1.43s／2.70s**。MU（`…9cbc26b1`）该段为 4.44s，同时看到前置事务有写 gate 等待和重试。6 个超标样本中同时存在冷准备、Effect 退避和提交争用，扩大库存只能覆盖其中一部分。

## 运行资源与观测边界

- 09:11 快照有 4 份私有 READY，cold gate 无 owner 或等待者，Worker dispatcher 已恢复、无待结算积压，内存保护级别 NORMAL。可用内存约 13.3 GiB，根盘剩余约 5.0 GiB；Swap 存量接近满，`sar` 在窗口内仍记录了实际换页。
- Transport Maintenance 已取得 476 轮回执，全轮墙钟 P50/P95 **0.53s／0.98s**，来源处理均未上报错误。该数值包含扫描等后台工作，writer 实际持锁 P95 本轮无独立完整数据。
- Scheduler 记录 45 次可选时间锚点不可用，Case 仍正常推进。Worker 有 8 条后台 ASGI 异常标记，完整样本的 W3 Job 全部成功。该窗未观测到 `sdk.thread_resume` span。抽查生产 Native 会话确认 Trade 和 Operations 均使用 Luna max，Native 会话记录未导出有效 service tier，供应商的实际 tier 仍缺独立证据。
- 优先验证精确研究材料就绪后的即时续接，再用同样口径跟踪缺货冷准备和写入等待。当前结论依据已实际超标的 106 例，继续扩大样本也不能将此次验收记作通过。

本地证据位于 `eval/runtime_root_repair_acceptance_20261118/`。其中 `acceptance.json` 是固定队列的计时结果，`breakdown.json` 包含按领取结果分组和超标 Case 的关键时间区间，`health.json`、`sar.txt` 记录资源状态。生产侧只读采样，无模型补跑或配置变更。
