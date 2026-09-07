# Workflow V2 数据库落地核验

2026-09-07：真实目标数据库创建/核验完成；本机摘要同步已完成服务端凭据与应用 RPC 验收。

## 远端 Supabase

- 目标：DoxAgent，`etzupajqhqylonxdhcax`。不是 `.env` 中 CDECR 使用的另一个项目。
- 本轮检查时远端已登记 `20260907061350 / ticker_initialization_summaries`。仓库文件为 `20260905105810_ticker_initialization_summaries.sql`，迁移名称和实际函数定义匹配；没有重复应用或改写历史版本号。
- 表、主键、RPC 已存在；RLS 开启，anon 无 RPC 执行权限，service_role 可访问。RPC 为 security invoker、空 search_path。
- 在事务中 `SET LOCAL ROLE service_role`，先写 state_seq=2，再写旧 state_seq=1，断言保留2及原phase；成功后 ROLLBACK，无验收业务行残留。
- Security advisor 仅返回 RLS 无 policy 的 INFO；本表是 service_role 专用，符合其访问设计，不给 anon/authenticated 增加策略。

## 本地 SQLite

已按现有 repository/migration 创建真实默认库：

- `.tmp/persistent_runtime_v2.sqlite3`：Runtime V2 基础表、orchestration schema v1、Trade Executor schema v1。
- `.tmp/message_bus_v2.sqlite3`：Message Bus V2 表。
- `.tmp/ticker_initialization/control.sqlite3`：初始化控制表。

各库 `PRAGMA integrity_check=ok`、foreign_key_check 无结果；真实开启写事务、建临时验证表并插入，随后回滚，验证表未保留。表清单见 `eval/persistent_runtime_v2/db_readiness_20260907/sqlite.json`。未生成业务 Case、ticker activation、订单或启动 worker。

`.env` 已配置初始化 control path 和正确的摘要 Supabase URL；没有启用交易或添加业务 activation。数据库就绪不等于 ticker 已初始化或持久进程已启动。

## 应用摘要同步验收

本机 `.env` 已设置 `DOXAGENT_TICKER_INITIALIZATION_SUMMARY_KEY`（仅本地忽略文件，未写入本报告或日志）。现有 `SUPABASE_ACCESS_TOKEN` 不是该 RPC 凭据，未再使用该路径。

使用生产 `SupabaseSummarySink` 和临时、本地清理的控制库执行真实 `outbox -> RPC -> acknowledge`：远端返回 HTTP 200，1 条合并 outbox 被确认移除；随后以更低 `state_seq` 对同一验收摘要回写，远端状态保持 `SUCCEEDED`，验证乱序不回退。远端保留的验收摘要为 `init-syncaccept-a29dd5b00899442aab55ed8b5f649f82`，ticker/operation_kind 分别为 `SYNCACCEPT` / `SYNC_ACCEPTANCE`，不关联业务 ticker、artifact、activation、监测或交易。生产控制库 outbox 在验收前后均为 0。

此验收补足此前远端 SQL/service_role 事务验证未覆盖的应用 HTTP 凭据与 RPC 路径。云端摘要同步仍是非阻塞投影；其不可用不影响本地 Runtime/Bus 的持久化或恢复。
