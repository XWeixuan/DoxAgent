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

具体提交号、最终测试和生产验收结果在完成后追加；不以容器 healthy 代替自然流量时效或历史失败恢复验收。
