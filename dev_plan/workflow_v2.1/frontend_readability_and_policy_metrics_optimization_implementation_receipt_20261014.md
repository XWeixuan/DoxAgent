# v2.1 前端可读性与策略 KPI 优化实施回执

日期：2026-10-14。基于 `frontend_readability_and_policy_metrics_optimization_plan_20261014.md` 完成开发收尾。代码与必要功能回归完成；性能整体 p95 达到方案目标，但不能宣称每个范围的 p95 都已达标，忙时尖峰见下表。本轮未执行 Git 提交、推送或生产部署。

## 最终交付

- 共享非交互数字引用圆标，覆盖 Markdown、D2/Policy、通用结构化正文及 Event/Case 相关文本入口。保持原始研究文本、下载内容和引用身份，代码块/URL 不误替换，不启用原始 HTML。
- D2 恢复分组状态表，保留六种值类型、全部观测、先前值、有效性、空参数及孤立观测；独立的基线、单列蓝边因素、玫红差异和有名称/影响/分隔的可能路径。保持历史兼容和 Shell/Unit/章节/条目定位。
- 策略经济传导图标、中文触发层级、简短方向资格；命名关联预期与默认折叠资料。解析绑定来源 D2 的 run/artifact/SHA。D3 `shell-0001.json` 是一基源序号，映射到原始 view_id，不套用零基阅读 ID 或失败 outcome 的排序。
- 增加单条 pinned Policy 接口，保留 activation/version/artifact 绑定，不用当前版本或消费状态替代历史正文。
- KPI 与列表复用 revision/ticker/Shell/MVCC/周期集合。消除逐 Policy 的事实相关子查询，ACTIVE 先分页后补标记，unknown_count 同次聚合。
- 现场进一步发现 SQLite 对分组 CTE 的 LEFT JOIN 采用重复 SCAN。最终列表以 UNION ALL 后按业务键归并标记；KPI 直接汇总三个集合的单行结果，不携带 Policy 正文、不连接成千条中间记录。没有新增缓存/汇总系统或延长预算。
- 两条正贡献部分索引仅通过显式幂等 `policy-query-indexes` 命令创建，支持 dry-run；GET/启动不自动迁移。

## 必要验证

- 前端63项测试通过；TypeScript、改动相关 ESLint、最终生产构建通过。既有大 chunk 提醒保留，未为此扩展打包改造。
- 最终后端28项回归通过，覆盖新旧查询小库等价、当前/冻结水位、范围/分页/退役与方向状态、引用解析和固定历史版本。新增 Python 文件 Ruff 检查通过。
- 暂停前已通过 CUA 实际浏览器检查：1262/1559宽度、真实 BE 发布研究长文本、状态表/基线/因素/多路径、索引及命名 Unit 深链，无整页横向溢出；圆标无焦点/交互。浏览器 spec 与真实长度夹具已更新，未另用独立 Playwright CLI 运行，不将静态检查冒充浏览器测试。

## 隔离性能证据与边界

隔离副本保留 BE/MU/INTC 所需完整 Policy 历史：1,287,969条业务对象、479,988条贡献（含零值），另有3条合成 Shell 用于 API 范围校验。复制结果不存在超出冻结水位的 valid_to，源生产读库保持只读。显式两索引在副本建立约1.3秒，不能据此估计生产整库迁移时间。

普通测试覆盖三标的、五种周期参数形状、当前/冻结水位、ALL/具体 Shell，每范围5次，共300次 SQL 与300次 API。30天范围使用30个工作日日期；测试的是查询范围行为，不替代交易日历验收。ALL 冷读再各60次，仅对隔离测试文件调用 POSIX_FADV_DONTNEED，不清空服务器全局页缓存。

| 场景 | SQL 整体 p95 / 最大 | API 整体 p95 / 最大 |
|---|---:|---:|
| 普通范围 | 352.84ms / 569.67ms | 481.37ms / 1043.16ms |
| ALL 文件冷读 | 417.18ms / 1070.51ms | 271.51ms / 311.17ms |

全部360次 API 返回200。SQL阶段设置3秒中断，最终没有超时；API测试为实际 ASGI 路由与响应校验，使用合成认证/view，未包含生产进程池排队和公网延迟。EXPLAIN 保留受限 current/history 和正贡献索引，去掉逐 catalog 相关子查询及大集合 LEFT JOIN。

**限制：**整体 p95 达标，但5样本的部分单范围估计仍超目标，尤其冻结 INTC ALL 文件冷读；不得写成所有范围 p95 均达标。结果逐条保留于 `eval/v21_frontend_implementation_20261014/acceptance-results.json`、`cold-results.json` 和 `performance-summary.json`，没有删去尖峰或用最小值验收。

收尾期间生产 API 被本轮以外的发布流程重建：2026-10-14 02:53:59（上海时间），当前检查镜像为 `login-feed-api-20261014-r1`、healthy。容器原 `/tmp/ui-impl` 隔离目录随重建消失，测试已完整结束并保存结果，故不再为收尾复制大库。主机 `/home/ubuntu/doxagent-ui-validation-20261014` 仍有验证脚本和结果；本轮没有清理业务数据或干预交易/采集/研究服务。

## 发布边界

本地存在其他任务大量未提交修改，本轮未整体提交/部署。后续按方案窄发布 API/Web，并显式执行独立索引迁移（先评估生产写锁窗口）：

```text
python -m doxagent.v2_read.cli policy-query-indexes --read-db /data/read/v2.sqlite3 --dry-run
python -m doxagent.v2_read.cli policy-query-indexes --read-db /data/read/v2.sqlite3
```

不触发业务重投影，不在 GET 执行 DDL，不因回滚删除历史数据。生产发布后的实际端到端延迟与上述单范围尖峰仍需发布验收，不能由此次隔离 ASGI 测试替代。
