# 升级失败后的生产恢复与版本收敛

日期：2026-10-24，北京时间。主机 doxagent-sg。

## 同步范围

本地工作目录原在消息采集优化分支，代码与 origin/main 一致；本地 main 指针落后 19 个提交。已将其他会话未提交的四组研究报告、复算代码、行情证据及诊断/方案文档一并提交，然后将本地 main 快进并推送 origin/main，远端仓库同步至 f2f0bcd66a13b469073392cd93fbdbecca2e56d2。未把不属于当前开发项目的上万条历史运行临时文件盲目纳入 Git，也未删除它们。

此次整合不更改生产交易策略。四组研究项目的三组测试共 20 项通过；生产采集、恢复、维护和发布相关回归 56 项通过。没有重新计算或修改研究数据内容；自动生成的 CSV/JSONL/SVG 不做无意义的尾空白重写。

## 恢复方式

服务器仍为 4 核、约 15.2GiB 内存；继续使用现有 4 核资源配置。开机时 Docker 已启动原容器，但 O4 等消费者仍在 3d0ae94c 旧代码，不能解析新消息源 SchedulerConstraints.capacity_mode 字段，出现验证错误重启。因此本次将全套注册生产组件统一到同一精确提交，而非只重启旧镜像。

标准 storage_release 工具构建 backend/site/supervisor/web/guardian/repair-agent 六类镜像。原 secrets、挂载、角色命令和各组件前一版保留；生产数据库、Chrome profile、身份和出口数据不清空。先停 Guardian/任务生产者，再停止 Site Access，有序切换 Supervisor 与 Site Access，随后更新消费者、执行者、消息与调度组件，最后恢复 Guardian。

## 最终运行与清理

18 个注册生产组件均已切换至 f2f0bcd6，最终快照全部 running，重启计数 0；配置健康检查的组件均 healthy。Site Access 与 Supervisor 的实际网络 namespace 一致，网络守护检查 READY。新 API 对 49 个生产 SourceDefinition 全部解析成功；新 O4 已正常启动，没有复现旧镜像的 capacity_mode 验证错误。

12 个 IR 内部入口在北京时间 18:48 均产生新 succeeded 记录，证明开机后确实发生采集，并非仅复用旧健康状态。持久接纳队列仍有 17,732 个 pending capture、685,470 个待处理列表候选；这些包含重复和待过滤项，不代表唯一文章数。此次只验证恢复和接线，不宣称历史积压清零、轮询时效或正文成功率目标已达成。

已删除发布前就退出的 7 个旧 eval 容器；生产组件旧容器由 Compose 重建替换。标准发布台账回收无引用旧镜像，并执行旧构建缓存清理/6GB 上限策略，最终额外清退 1 个无引用镜像引用，错误列表为空；保留当前和前一版本、稳定依赖输入，未做 volume prune。最终根盘可用约 22.10GiB（df 显示 23G）。最终内存 available 约 8.5GiB、swap 使用约 324MiB；只是短期恢复快照。

Docker、资源控制、网络守护、发布资源 timer、券商中继服务均 active。**IB Gateway 尚未恢复**：宿主机没有 Java 进程，4002 未监听，只有中继 7496。Gateway 为桌面用户登录自动启动方式，需人工恢复远程桌面及 Gateway 登录，不能将中继 active 或容器 healthy 当成交易/API 就绪。未读取或修改券商凭证，也未擅自完成登录。

快照与测试见 [最终组件状态](../eval/upgrade-recovery-20261024/final-public.json)、[清理回执](../eval/upgrade-recovery-20261024/cleanup-public.json)、[真实采集与队列](../eval/upgrade-recovery-20261024/data-check.json)、[56 项生产相关回归](../eval/upgrade-recovery-20261024/regression.xml)。

远端私有运行回执：/var/lib/doxagent/upgrade-recovery-20261024/，包含发布前 state/Compose、构建与部署日志；含运行配置的原始回执不提交 Git。清理范围为已退出的旧 eval 容器，以及发布台账确认不再引用的应用镜像和可回收构建缓存；保留当前/上一版镜像、批准的依赖输入、生产卷及研究原始文件。
