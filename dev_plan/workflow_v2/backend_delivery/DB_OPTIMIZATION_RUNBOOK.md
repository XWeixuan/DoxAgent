# V2 SQLite 优化部署与运行记录

## 本次部署边界

2026-09-14：只发布代码并进行必要 schema 升级。按用户最新指示，不在远端执行存量清理、compact、VACUUM、GC、receipt archive 或影子库切换。旧 DB 文件不会因本次发布立即缩小。`maintenance` profile 保持关闭；保留运行中的 Paper/Live 业务配置，不发送验收订单。

## 发布内容与读写路径

| 层 | 实现 |
|---|---|
| 原生写入 | Journal 读事务不获取写锁；相同 runtime value、节点和 control ack 不重复写入；CASE 输入高水位使用索引。runtime task capture 排除纯 lease/updated_at 续租。 |
| 热读与历史 | schema 3：object_current、contributions_current、metric_buckets_current；历史查询分开选择当前和已关闭区间。历史近期索引避免按时间排序扫描全部旧版本。read_meta 高水位不依赖历史 commit 保留。 |
| 投影 | 事务内比较 payload 与 sort/parent/day/source/route/search 元数据；仅真实变化分配 seq。分组贡献更正使用 Decimal 差量；关闭版本进入 business_revisions。 |
| 页面 | Overview/Runtime 指标批量归约；Overview ticker 状态、初始化和来源计数批量读取；Cost node 先分页再批量汇总；Bus 消息日期与正文尝试日期分别聚合；Graph 路径以节点 Case 集合为准，最近观测批量点查。 |
| API/SSE | 默认 2 个读进程、1 个控制进程；关键词复杂指标使用独立单进程。队列/时间/流元数据都有硬限制；主事件循环的 auth/config 不读业务 SQLite。 |
| 内容 | 新 Raw/Enrichment、初始化、Runtime 和读投影大字段双格式读取；新正文块每块 128 KiB gzip，文件先于引用发布；永久业务引用保留。 |
| 保留 | 有效 view/cursor 至少 24 小时，seq-1 受保护；诊断删除只能作用于显式 DIAGNOSTIC、30 天以上、无引用/hold 的内容。UNKNOWN 和 BUSINESS 不按年龄删除。 |
| 来源恢复 | source epoch/head/floor 独立；已确认消费者 checkpoint、repair pin、至少 7 天以及一致性备份共同限制 outbox 归档；归档保留完整 receipt 可回读。 |

## 部署

服务器仓库：`/home/ubuntu/doxagent`。必须同时使用两个 Compose 文件，避免丢失 Gateway/资源配置：

```sh
cd /home/ubuntu/doxagent
git pull --ff-only
sudo docker compose -f docker-compose.v2-production.yml -f deploy/docker-compose.server.yml build v2-api v2-web
# 停止现有业务服务，释放写者锁后执行必要 schema 升级；不删除卷。
sudo docker compose -f docker-compose.v2-production.yml -f deploy/docker-compose.server.yml stop
sudo docker compose -f docker-compose.v2-production.yml -f deploy/docker-compose.server.yml run --rm --no-deps v2-migrate
sudo docker compose -f docker-compose.v2-production.yml -f deploy/docker-compose.server.yml up -d
```

升级对受影响的现有库做一致性备份并注册 release marker；普通重启跳过全库备份、全量 schema/backfill；本次补丁会幂等升级旧工件引用触发器。外部 native/content/receipt 文件必须与 DB 备份同时保存，不能仅复制 sqlite3 文件。API server 配额从 384 MiB 调整为 1536 MiB，容纳有界子进程；无额外 Uvicorn 多 worker。

验收只检查 commit、镜像、容器、healthz、auth/config、投影 checkpoint/head/gap 及有界业务请求；不执行真实订单或重跑历史研究。

## 维护工具（本轮远端不执行）

统一入口 `python -m doxagent.v2_read.cli`：

- `diagnose --read-db PATH`：高水位、schema、source lag/gap、DB/WAL/free page、pin。
- `backup --source runtime=PATH --source bus=PATH --backup-dir NEW_DIR`：一致性备份与 SHA-256 manifest；可显式追加其他源及 immutable artifact roots。逐库在线 backup 不等于跨库原子快照。
- `compact --read-db OLD --target NEW`：离线影子副本外置旧正文/大字段、保留 generation/seq/tokens；不修改源文件。磁盘不足拒绝开始。
- `verify --read-db SHADOW --source ...`：检查完整性、gap 与 source 水位；`switch --read-db SHADOW --alias ALIAS --source ...` 切换读库指针。切换前必须停止写者/API/投影并闭合全部源水位；不能将业务执行库回滚到旧备份。
- `gc --read-db PATH --limit 100` 或 opt-in `gc-worker`：短事务处理到期读历史；关闭的旧业务版本先进入业务目录；有 gap 时不越过保护边界。
- `inventory --read-db PATH --limit 100`：分轮登记文件，未分类材料保持 UNKNOWN。
- `diagnostics-gc --read-db PATH --limit 10`：显式诊断标记、隔离、删除可恢复协议；活跃材料必须登记 ref 或 hold。
- `archive --source runtime=PATH --read-db READ --manifest VERIFIED_MANIFEST --limit 100`：仅归档满足 checkpoint/pin/7 天约束的 receipt。归档写入/校验失败时禁止删除原 receipt。

必须在独立副本完成恢复与引用核对后，才启用生产清理。本次不声称存量压缩、GC 吞吐或线上 1 小时容量稳态已验收。保留旧镜像和备份；外部引用格式写入后，回退必须使用支持双读的版本，不能直接运行不认识引用的旧代码。

## 必要验证记录

已针对去重、元数据变化、source 高水位/归档回读、NativeContent roundtrip/backup、Policy 覆盖、Bus 日期/筛选更正、Cost、SSE、OpenAPI 和 Graph 节点路径做定向检查；发现的 SSE 方法绑定及 Graph 直接投影聚合缺失已修复。前端 schema/typecheck/production build 已通过。最终部署结果在完成远端检查后追加。

## 2026-09-14 远端发布记录

已将代码推送 main 并在新加坡 `/home/ubuntu/doxagent` 拉取，使用两个生产 Compose 文件构建共享后端镜像并更新服务。schema 3 已生效；一致性备份位于 `/data/backups/20260914T141029911261Z-resume-runtime`。发布中的验证采用停止写者后的 schema/页数/水位检查，未对远端大库运行清理或压缩；完整恢复验证仍需离线副本。

必要检查还发现并修复了三项真实运行问题：

- 老正文补全 audit 缺少 started_at，产生大量 KeyError gap。新记录保存真实起止时间，旧记录保留时间未知，不能加入有限日期窗口冒充已知发生时间。
- 投影先读取有限事件坐标，再逐项加载真正需要处理的 payload，避免每轮预读后丢弃整批大对象；旧 task 的纯租约变化也不新增投影版本。
- 工件触发器采用显式 UPSERT 冲突处理，避免外层业务 UPSERT 导致 artifact.id 重复报错；来源确认遇到短暂写锁只延迟确认，旧归档保护水位仍有效。

本地 Runtime 实际投影与 receipt 归档回读 3 个定向用例通过，旧正文时间未知用例通过；此前的前端生产构建及针对快照、指标、Policy、SSE 的定向检查已通过。不扩展无关回归。

在线抽样（14:46 UTC）中，Ticker/消息/Case/Policy 的 20 行有界查询为 1–22 ms，消息汇总约 11 ms；启动完成后的 auth/config 返回 200，约 2.6 ms。这些是单次样本，不是 p95、冷缓存或长期容量验收。

部署前备份已有 7,394 条 Bus KeyError gap；服务更新前继续产生的记录也已纳入自动 repair。修复后 Bus checkpoint 可追平 head，gap 数开始下降；Runtime 仍有历史 receipt 待处理。积压及 gap 消失前保留 PARTIAL/同步延迟，不能把服务健康误报为业务覆盖完整。正常投影回放只更新读模型，不重新执行研究或发送订单。

本轮未执行远端历史清理、GC、receipt 归档、影子切换或存量压缩；maintenance profile 关闭，旧文件大小不会立即缩小。未发出验收订单。远端带 Supabase 登录的业务 HTTP/SSE 没有重测，不将本地 SSE 检查当作线上登录态验收。

最终服务检查：11 个后端容器均运行同一镜像 `sha256:f1f0838128833ab52351b0d2de1dc9d870144f096b46b14e1ab465a849760732`，加上 Web 共 12 个服务；检查时 restart=0、OOM=false。通过 Nginx 的首页、healthz、Supabase auth/config 和未登录 401 拦截检查通过。14:49 UTC 的第二轮有界业务读取为 1.6–5.3 ms；Bus 25972/25972、Research 943/943、Initialization 54839/54839，Runtime 827265/852016，gap 从修复启动后的 8599 降至 7839，仍需正常追赶。
