# 消息效率统一发布状态（2026-11-17）

**Git 同步完成，生产发布未完成。**

产品候选固定提交：`e06cece02f1637824c9f843893df03a89739fb98`。已推送 origin main，随后通过 `git ls-remote origin refs/heads/main` 独立核对一致。

提交包含当前全部已跟踪开发改动、两个新增产品模块、测试、依赖与 lockfile、消息效率文档，以及另一份全局性能勘察文档；同时纳入本轮消息效率评估脚本和验收摘要。提交后未发现遗漏的 src/tests/scripts/deploy/frontend 产品改动。历史原始审计数据、导出、缓存继续保留本地，没有将其混入镜像或产品提交。

生产操作前连接检查失败：`doxagent-sg`、`doxagent-sg-rdp`、新加坡直连及 `doxagent-hk` 备用入口均未完成 SSH banner 握手。未改变 Clash 节点或区域。未在远端执行 Git 更新、镜像构建、依赖安装、容器重启或生产配置修改；不能将 Git 推送视为上线验收。

恢复访问后需先核对实际生产容器、发布台账及在途任务，再使用现有 `storage_release.py` 的固定提交构建流程。新增 RapidFuzz 必须进入依赖层，不能仅基于旧依赖镜像 COPY 源码；严格 SourceDefinition 的实际消费者需同步更新。保留当时生产环境和回滚镜像，按既有排空／切换流程发布，并核验运行提交、依赖导入、源开关（IBKR 关闭）、健康状态和自然消息发布。

开发及已知基线测试问题详见 [开发记录](message-efficiency-implementation-20261117.md)。本文件仅记录发布阻塞，不代表已构建或已部署。
