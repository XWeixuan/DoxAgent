# main 全量生产同步与构建资源清退回执

日期：2026-10-19。用户授权发布完整 main，并清退临时镜像/构建缓存；不进行深度存量清理。

## 代码与发布

- 产品发布提交：`3e6852540d57dff083bef6569bd675b57722e8a9`。本地 main 已推送 origin/main，生产检出 `/home/ubuntu/doxagent` 已 fast-forward 到该提交；后续回执提交只更新文档/日志，不改变已构建产品代码。
- 使用既有 `scripts/ops/storage_release.py` 稳定依赖构建与逐组件 deploy/health/rollback 机制，构建 backend、site、supervisor、web、guardian、repair-agent 六种镜像，更新 18 个生产服务。
- 校准了被既往旁路发布落下的 release ledger：以真正运行的容器镜像及其模板引用作为退出前的 current，再由正式 deploy 推进为有效 previous。避免旧 ledger 错保过时版本或漏保实际回滚镜像。
- 保留生产模型设置、自定义命令、所有数据库/资料/Profile 卷与鉴权挂载；更新正式 `.env.v2` 镜像默认值与 canonical override，包括空闲 migrate 服务及 Guardian 后续修复 Agent 引用。
- 先停止生产任务入口，再等待在途模型任务与调度器已提交批次退出；没有取消模型任务。Supervisor 平滑停止以落盘 Profile，浏览器原生页面会因进程重启失效；发布前没有 OPEN Viewer 或进行中的维护操作，Challenge 通过现有 owner 机制核对，不伪造原页可恢复。发布后原 5 个 WAITING_HUMAN 记录已转为 TARGET_LOST（总数 13）；859 个 Profile 及 184 个 identity heads 保留。
- 宿主机发布 owner 脚本同步到本次代码；宿主机维护 UI 已与 main 一致。

## 验证

- 每个 Python 生产容器核对 700 个 src/prompts/API contract 文件 SHA-256，与目标提交源码一致；备用修复 Agent 也已核对。
- W3 实际 readiness=true，gpt-6-luna 预备 2、gpt-6-sol 预备 1，生产新任务已恢复运行。Read V4 active alias 及 generation 原样保留，四源 checkpoints/heads 已另存现场回执；业务持续写入，本轮不宣称所有 CDC 尾部始终零积压。
- 镜像 ID、发布 ledger、构建提交和源码 archive identity 一致；所有容器运行、带健康检查的服务 healthy，重启次数均为 0。
- 后端 healthz/readyz、Worker health/readiness/resources、Site Access health、Web 首页与反向代理 auth/config 通过；Read 继续读取现有 active generation。
- 既有本地合并验证：599 passed/1 skipped/1 个原 main 时钟测试单独排除；远端 SDK 修复合入后追加 20 passed，前端 66 passed 和类型检查通过。本次不额外触发付费业务研究来制造验收数据。
- 日志异常类型与 traceback 摘要见机器回执；本报告不把健康检查等同于所有股票业务/第三方来源逐项端到端验收。

| 服务 | 镜像 ID 前缀 | 状态 | 重启次数 |
| --- | --- | --- | --- |
| v2-scheduler | `027a61ab9637` | RUNNING | 0 |
| v2-o4 | `027a61ab9637` | RUNNING | 0 |
| v2-cdecr-executor | `027a61ab9637` | healthy | 0 |
| v2-initialization | `027a61ab9637` | RUNNING | 0 |
| initialization-guardian | `fb04a73c1e44` | RUNNING | 0 |
| v2-control | `027a61ab9637` | RUNNING | 0 |
| v2-delivery | `027a61ab9637` | RUNNING | 0 |
| v2-executor | `027a61ab9637` | RUNNING | 0 |
| v2-message-bus | `027a61ab9637` | RUNNING | 0 |
| v2-content-enrichment | `027a61ab9637` | RUNNING | 0 |
| v2-web | `b266c6f54b88` | healthy | 0 |
| v2-maintenance | `027a61ab9637` | RUNNING | 0 |
| v2-transport-maintenance | `027a61ab9637` | RUNNING | 0 |
| v2-api | `027a61ab9637` | healthy | 0 |
| v2-projector | `027a61ab9637` | RUNNING | 0 |
| v2-site-access | `b1f877bf46bd` | healthy | 0 |
| v2-chrome-supervisor | `4aebf2ad2a7a` | healthy | 0 |
| codex-worker | `027a61ab9637` | healthy | 0 |

## 资源清退

- 完成生产验收后，按真实容器、当前配置、current/previous、活动维修、动态模板、稳定依赖及批准输入引用再次划定保护集合；成功发布关闭不再使用的构建候选，再退出无引用的临时/旧发布镜像。
- 显式收尾阶段额外删除 44 个无引用镜像，当前运行和有效回滚/模板/输入镜像全部保留；正式 deploy 的自动 reconcile 另有独立退出明细，避免把共享逻辑镜像大小相加成释放收益。
- 已清理当时所有可回收 BuildKit 构建缓存；稳定依赖镜像保留，普通提交继续复用真实依赖基线。
- BuildKit 报告本次回收 20.52 GB；显式镜像与缓存收尾阶段主机可用空间合计净增约 26.05 GiB，不能再叠加两者的共享逻辑大小。
- 显式收尾前可用 28.60 GiB，收尾后 54.65 GiB；本轮同步开始时可用约 34.99 GiB。以上为主机现场余量，期间正常业务持续写入，镜像共享层不重复计收益。
- 不运行业务存量清理命令，不批量删除 Read 历史、研究资产、数据库、会话或旧研究目录；已有产品 owner 的正常生命周期治理保持运行。

## 留痕

服务器：`/var/lib/doxagent/releases/sync-3e685254-20261019/`，发布状态：`/var/lib/doxagent/releases/state.json`。本地验收、清退及构建/排空回执：`eval/production_sync_20261019/`。含生产配置的完整快照仅放服务器 root 私有目录，不写入 Git。
