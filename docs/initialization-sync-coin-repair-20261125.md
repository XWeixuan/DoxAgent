# 2026-11-25 初始化投影延迟与 COIN 核查回执

## 前端同步修复

北京时间 02:04:53，Initialization head=193038/checkpoint=188485，落后 4553 条，最早未投影事件为 01:49:15。Research/Bus/Runtime 当时均追平。COIN 有 3120 个初始化节点，其中 CDECR 3100 个。旧 progress() 每条 run/node/event 事件均重新扫描并反序列化该 run 的全部节点和事件；三类账本更新重复计算，生产日志的初始化映射耗时约 0.1–0.3 秒/事件，造成投影吞吐不足。不是浏览器刷新失效。

修复在单个 ProjectionWorker tick 内复用精简的进度元数据，按事件增量替换节点；保留每条事件的进度投影与提交。异常时清空缓存，历史 gap repair 每次重读，tick 结束释放缓存。没有跳过 source receipt、伪造 checkpoint 或清除历史 gaps。

只读生产数据对比：原始 progress 0.167 秒，缓存首读 0.131 秒，后续 50 次平均 0.020 秒；结果完全相同。02:09:24 仅重建启动 v2-projector。镜像 doxagent-read:init-progress-20261125，原镜像保留为 doxagent-read:before-init-progress-20261125。Compose current overlay 仅修改该服务 image，备份 /home/ubuntu/read-init-progress-20261125/compose.current.before.json；两份 host source 在旧哈希一致后同步，保留 .before。生产 repository.py 与本地 HEAD 不同，未覆盖。

02:10:54 首次追平，02:11:51 独立复查四路 head/checkpoint 全相等，source error 均 null，Initialization oldest_pending_at=null。没有完成登录态浏览器验收；验收针对前端所读取的 Read 数据及其 clock。Runtime 原有 PROVENANCE_UNVERIFIED/ValueError 历史 gaps 保留，本次不声称历史完整。

## COIN 初始化

原 run init-coin-0a7ab32b3e9d4b2db5dae780a52dfe03，cutoff 2026-11-24T14:35:02.927898Z。本次接入时 CDECR 已在原 run 内完成，dispatch SUCCEEDED、原生 bulk epoch FINALIZED，终结时间 2026-11-24T18:01:12Z；父节点于 18:01:43 成功，O2 于 18:01:46 启动。D1 成功；D2/D3 等后继尚未启动。

CDECR FIELD 阶段耗时约 5697 秒（95 分钟）。其中子任务 44bfa9d6b2b435d472c9ad22f3880ab20c1c44e0fe060a88fb2c5df0e686ae71 留有 FAILED_TERMINAL / ImmutableRecordConflict，其他 CDECR 节点 3099 成功。该失败没有阻断原生批次 FINALIZED 或后继 O2；它是保留的局部失败证据，不能据此声称整个初始化停住。现有日志只记录异常类型，尚未确定具体冲突记录，未擅自改写已冻结的原生终态或把失败标成成功。

O2 实际 Worker job=31bd075e6272e6fdfe9050bba059cc851eb44ca9fa75a5f47f89d21c93dead11，execution_phase=RUNNING，wait_reason/error 均空；capsule 已启动。SDK 审计有 21 条事件，最近 shell 调用完成于 18:10:07Z，证明已实际读取事件库，而非仅外层 RUNNING。

本次未发手工 resume、未新建 run、未重复成功节点：该 run 在接入前已自动继续，重复恢复会干扰在途 O2。当前为继续执行 O2，尚未完成全初始化或生产激活。

## 验证与证据

完整两份针对性测试：14 passed / 3 failed；三个失败均用未修改 HEAD 重现（legacy native content 缺失检测、CLI 新库 fixture、ACK 旧方法 fixture）。排除已有失败及同参数组后 13 passed。新增缓存与无缓存一致性/生命周期回归通过；compileall 和 git diff --check 通过。

证据：../artifacts/audits/initialization_sync_coin_20261125/acceptance.json 与 coin-worker.json。其时间均为 UTC；本回执正文时间按北京时间。
