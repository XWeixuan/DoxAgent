# 2026-10-13 远端磁盘根因审计证据

目标 doxagent-sg / VM-0-15-ubuntu。采样为北京时间 02:35 起；数据库和目录查询跨分钟，非原子快照。

本目录是只读排查输出，不包含可直接执行的清理程序。主报告：`docs/disk-rootcause-and-repair-plan-20261013.md`。

| 文件 | 内容 |
|---|---|
| host-scan.txt | df 之外的主目录 du 实际分配空间、journal、截取的 lsof |
| data-top.txt / data-deep.txt | 应用卷拆解、SDK sessions、Runtime 文件、浏览器目录 |
| backup-and-browser-detail.txt | 分散备份目录、两个 Profile 的 Cache/Code Cache、最终 df |
| runtime-schema.jsonl | 容器镜像/mount/log 配置；数据库只读 page/freelist/schema 信息；配置文件是否存在 |
| docker-inventory.txt / docker-summary.txt | 镜像/容器/卷/缓存与系统 timers；Docker reclaimable 出现负数，不作为回收承诺 |
| image-history.txt | 指定镜像的大层来源与实际 builder GC policies |
| gc-and-home.txt | 容器清单没有 GC、home 目录清单；其中探测假定容器名失败，已用全量实际清单与远端 Compose 补证 |
| production-code-proof.jsonl | 实际 Read 容器文件 SHA 与 gap 全局阻塞关键行；Guardian 动态依赖 |
| distribution-code-proof.jsonl | 实际 Bus 容器代码 SHA、轮询 work_key 与 run-scoped delivery 身份 |
| retention-samples.jsonl | source ACK/pin/archive/floor，Read gaps/checkpoint，末尾对象版本样本 |
| bus-stratified-sample.jsonl | 六个历史序列区间各 1,000 条 CDC 抽样；不是总体字节估计 |
| table-populations.jsonl | 各表末尾 rowid（不是计数）；完整 outbox_by_table 聚合与 poll-state 历史/当前准确 COUNT |
| distribution-counts.jsonl | 原始消息、共享文章、runs/observations/deliveries 的准确 COUNT；末尾精简投递样本 |
| distribution-states.jsonl | 按 state 准确聚合；与前项时间稍晚，总数差异为持续写入 |
| admission-distribution-samples.jsonl | 表定义与真实末尾样本，证明过期文章被重新建立拒绝投递、完整配置重复留存 |
| payload-samples-retry.jsonl | 主表末尾 100 条 payload 长度样本，不代表全库分布 |
| scan-progress.jsonl | 本轮扫描 PID/I/O；SDK 文件仅文件名/大小，未读取 auth/config 内容 |
| dbstat.jsonl | 未完成的长查询，仅有 start；不能当作表体量统计，已终止其专属审计 PID |
| payload-samples.jsonl | 首次样本查询遇到短暂 locked 的记录；以 retry 文件为准 |

数据库连接使用 mode=ro 与 PRAGMA query_only=ON。部分全表查询设置进度回调并返回 interrupted；没有用不完整聚合替代完整计数。lsof 输出有截断，不能全面排除所有已删除打开文件。

关键查询：

```sql
SELECT count(*) FROM distribution_articles;
SELECT count(*) FROM distribution_runs;
SELECT count(*) FROM distribution_observations;
SELECT count(*) FROM distribution_deliveries;
SELECT state,count(*) FROM distribution_deliveries GROUP BY state;
SELECT count(*) FROM message_admission_results;
SELECT table_name,count(*) FROM v2_source_outbox GROUP BY table_name;
SELECT count(*),min(valid_from),max(valid_from) FROM objects WHERE kind='native:poll_states';
SELECT count(*) FROM object_current WHERE kind='native:poll_states';
SELECT * FROM gaps;
SELECT * FROM read_meta;
SELECT * FROM v2_source_state;
SELECT * FROM v2_source_consumers;
SELECT * FROM v2_source_pins;
SELECT * FROM v2_receipt_archive LIMIT 10;
PRAGMA page_count;
PRAGMA page_size;
PRAGMA freelist_count;
PRAGMA auto_vacuum;
```

特别注意 message_admission_results 使用 INSERT OR REPLACE；最大 rowid 约 1,317 万但实际只有 9,725 行。objects/object_current 的最大 rowid 同样不能当作行数。

历史凭证在主报告第 5 节列出。本轮只在本地新增报告/证据及 changelog 记录；没有清理或修改服务器业务。
