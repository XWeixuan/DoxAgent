# COHR / NBIS 初始化完成后前端仍显示进行中

2026-10-18 北京时间 06:34 排查，业务真相与页面数据不一致：

- Initialization：COHR / NBIS 均 SUCCEEDED；成功节点 550 / 973。
- Runtime Control：均 RUNNING，activation_id 正确，initialization_incomplete=false，revision 9 / 6。
- Read model ticker：仍 INITIALIZING，revision 8 / 5。
- Read model initialization：COHR 仍停在 state_seq 1391 / FAILED；NBIS 仍停在早期 D3。

## 根因

前端 Overview 从固定 read-context 快照读取投影，而不是直接读 Control SQLite。
Initialization 投影 source checkpoint 117678，head 118059，尚在处理 20:34 UTC 的事件，
落后约两小时。Runtime source 已到 head，但两个最终 Control 事件
1885579（NBIS）、1885595（COHR）被正式投影器以
`admitted activation must be indexed before effective pointer` 拒绝并进入 gaps 重试。
其依赖的 activation_revision 尚在 Initialization source 积压中。

`v2_read.initialization.progress()` 每次节点/事件更新均取出同一初始化所有历史节点和事件，
NativeContent row factory 因而反复校验、解压完整 Worker 回执、模型输出及上下文。
COHR / NBIS 当时共有 1510 个已投影节点、3560 个事件，其中 284 个节点 result 外置。
这使每次页面进度聚合都承担大量与页面无关的内容读取，阻碍后续激活记录传播。

## 修复

只修改 `v2_read/initialization.py`：SQL 先投影 stage/status/key、时间、事件 node 和质量标记，
不读取历史 receipt。若实际需要的 result / event payload 外置，仍保留其引用并走原生完整性校验。
未放宽激活依赖、未直接改页面状态、未跳过 outbox 或清空 gaps。
相同生产库中节点所需字段读取约 0.22 秒、事件字段约 0.02 秒。

新增回归覆盖：历史 receipt 不可读不影响进度聚合、外置 result 质量标记、
外置 event 时间结算，以及被实际使用的 result 缺失仍报错。
初始化 / 正式投影回归 15 passed，Ruff passed。

本地提交 `b11e9034`。生产基于 projector 原 revision
`3c30365a0586959f632d1a62041d2b6ccdd7ea0c` 仅叠加上述文件，得到
`00983e64daf5dc430682a6f20f653b10b11121fa`，镜像
`doxagent-cohr-recovery:projector-00983e64`。
仅重建 projector，保留原环境、源注册、数据库与检查点；初始化和研究未重跑。

## 验收留痕

远端目录 `/var/lib/doxagent/cohr-nbis-projection-recovery-20261018/` 保存发布前
投影 / 检查点快照、镜像来源及 Compose overlay。
主工作区 `eval/cohr_nbis_projection_recovery_20261018/` 保存窄补丁、部署脚本、状态快照、
API 路由验收脚本。API 脚本在隔离的 TestClient 中使用测试身份读取真实生产数据，
不读取用户浏览器凭据，也不更改生产鉴权设置。
06:43:38 最终验收：Initialization checkpoint=head=118059；Runtime
checkpoint=head=1885608，research / bus 也均追平。
顺序投影已将两个 activation_revision 正常入库；短暂停止 projector 单写者后，
以原始 receipt 和正常 FormalProjectors / ReadStore.ingest 路径立即重放
1885579 / 1885595 两个延迟事件（避免再等退避计时），恢复 projector。
Runtime checkpoint 未变，两条对应 gap 已消失。

部署中 API 代码的 Overview 路由返回 HTTP 200：
COHR / NBIS 均 `run_state=RUNNING`、`initialization_incomplete=false`、
`initialization=null`，read-model revision 分别为 9 / 6，与 Control 一致。
初始化进度对象也均为 SUCCEEDED（state_seq 1409 / 2231）。
Projector 正常运行、RestartCount=0。

页面已有 read-context 是固定快照，保留已打开快照属于原契约；页面“刷新”会创建新
read-context 并读取上述正确结果。没有修改前端状态文字或篡改旧快照。
