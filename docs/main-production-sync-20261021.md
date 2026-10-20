# main 全量提交与生产同步：2026-10-21

用户授权范围为整个 main 工作目录，包含其他会话产生的正式代码、提示词、测试和项目文档；排除评估输出、日志、缓存、临时脚本、导出件等生成资产。不是只发布某个会话的文件。

## 本次整合

- W3 统一 LONG/SHORT/NO_TRADE、W2 归因独立、后置运营与归档、Read/API/前端/导出/回测适配。
- W3 启动修复第三版：启动进度、统一冷启动准入、压力滞回、owner/Fork 恢复、固定准备期限和交易后运营 handoff。
- 用户更新的 W3 AGENTS.md 与唯一 skills/skill.md 一并发布；保留 weekend_sweep 附加方式。
- 为新版提示词补充宿主 `contract.json`，显式启用 `persistent-runtime.w3.v5`，不增加 Agent 提示词文件。
- 修正 Compose 中误插入 depends_on 内部的 cpu_shares，最终 Worker=2048、Site Access=512、Chrome Supervisor=256。
- 回测旧恢复夹具显式冻结 v4，新增 v5 离线回放覆盖；工厂测试按当前生产提示词契约验证。

## 发布方法和边界

生产源检出 `/home/ubuntu/doxagent`，目标 Git 仓库 `origin/main`。使用现有 `scripts/ops/storage_release.py`，从精确提交构建 backend/site/supervisor/guardian/repair-agent/web 六类镜像，逐服务健康检查、登记 current/previous 和引用保护。

本次统一更新全部 18 个 DoxAgent 生产服务，保留现有 Compose 私有配置和命令、全部业务/研究数据库与卷、Profile、鉴权及网络代理。构建完成后再停止任务受理方、短暂排空并切换 Worker；保留原任务持久化身份，不人为重放历史 Case。先 Worker 后 Scheduler/client。Browser Supervisor 重启会关闭原生浏览器页面，Profile/身份文件保留。

清理仅限无引用旧发布镜像、Docker 可回收构建缓存和确认无业务用途的临时容器；保留当前镜像、有效 previous、稳定依赖、批准输入及动态修复模板引用。不执行 volume/system prune 或业务历史数据删除。

## 证据

本地 `eval/main_production_sync_20261021/` 保存全目录文件清单、排除计数和离线测试/构建日志；不纳入本次生产源码提交。

服务器私有证据目录 `/var/lib/doxagent/releases/main-sync-20261021-v5/` 保存发布前容器/镜像/配置快照、构建、切换、健康/源码一致性和清理记录。含配置和环境的原始快照只保留在服务器私有目录，不写入 Git。

## 最终提交与验收

产品代码提交为 `039d677584094fa7fd977612944e41cfbc557cf9`，74 个正式文件已整体提交并推送 origin/main；生产源检出已 fast-forward 到该提交。随后验收回执提交只更新本文和 changelog，不需要重建产品镜像。约 1.5 万个未跟踪 eval/export 生成资产留在本地，没有混入提交。

- 最终离线回归 **260 passed，3 warnings**，前端 **67 passed**；Schema 生成、TypeScript 检查、前端生产构建和 staged diff 检查通过。启用新版提示词后发现的旧契约测试默认值已修正；另一个短超时测试初轮有计时波动，单项和最终整轮均通过，未为此改变生产预算。
- 六类镜像均从该精确提交构建，18 个服务全部更新；17 个 Python 容器各核对 **845 个 src/prompts/API contract 文件 SHA256**，全部一致。所有容器标签指向该产品提交，最终全部 running，带健康检查的全部 healthy，RestartCount 均为 0。
- Worker 内部 healthz、Web 首页和认证配置通过。Worker 没有公开宿主机端口，检查走其容器内回环地址，未为验收扩大端口暴露。
- W3 生效 Bundle：`ac16b6fa812c4cff57c22bb8d8136af07f13273e19ebfea37b5513da1eb65e0d`。BE、CEG、COHR、INTC、MU、NBIS、ONDS、RKLB、SNOW 九个 ticker 的 `w3_entry_active` 均已指向 **persistent-runtime.w3.v5**。
- 一轮冻结字段为 decision、reason、prior_expectation、expectation_delta；二轮为 novelty、delta_candidates、watch_actions、pin_actions，没有旧 policy 复判字段。
- 冻结 AGENTS.md SHA256：`1b2394fb198653e1437a14fdbf5a1c40c455b1b430b778ba0ba7b27e9caf64d6`；唯一 Skill SHA256：`bf900cb76b95805da80ef429f72a3d0ebeeb6912ff4e20b20772fe08470aa649`。与本次用户更新后的提交内容相符。
- Worker 实际预算为 boot=90、prepare=180、handshake=45、handoff=90 秒；实际 CPU shares 为 Worker=2048、Site Access=512、Supervisor=256。

## 发布中处理的预热资源问题

容器更新后，新 Bundle 已发布，但旧 ticker 入口仍在等新母线程与 READY 库存。现场 CPU PSI avg10 约 75.85，触发了新版空库存预热门槛；这时不能仅凭镜像更新就宣布 W3 已切换。

为完成正常发布流程，短暂停止采集、正文补全、Read 投影以及浏览器服务，为预热留出资源，没有绕过身份/版本校验或手动改写入口。CPU PSI 降低后，正常预热得到 **Luna 2 / 6.1 Sol 1，ready=true**；恢复全部临时暂停的服务及 bus 心跳后，九个 ticker 均由正常调度切到 v5。最终所有服务已恢复。此处仅是发布窗口的就绪验收，不宣称持续高负载下的 p95 或所有 badcase 已通过。

## 资源清理结果

- 正式 release owner 退出 **10 个无引用镜像 tag**；保留当前、有效 previous、批准输入、稳定依赖及动态模板引用。
- 清理 Docker 可回收构建缓存，BuildKit 报告 **2.457 GB**。共享层和镜像 tag 大小不重复相加。
- 清理阶段没有发现符合临时/one-off 身份的退出容器，删除数为 0；最后检查无退出容器残留。
- 清理后可用空间 **48.84 GiB**，较本轮发布前净增约 **4.56 GiB**；包含期间正常业务写入的实际文件系统差值。
- 没有删除数据卷、数据库、研究资产、Profile、鉴权或 Case 历史。

全部 ticker 继续 PAPER_TRADING，没有更改实盘授权。旧在途 Case 按自身冻结契约继续执行；本轮未人工投放测试消息、重放历史 Case、恢复旧失败或运行 Read 历史回填。真实交易判定质量、下一交易时段高负载时效及历史问题恢复仍需分别验收。
