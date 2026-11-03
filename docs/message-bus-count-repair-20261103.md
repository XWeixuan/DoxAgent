# Message Bus 缺口恢复后的计数漏计修复

日期：2026-11-03。仅处理计数漏计，不调整来源、搜索词、相关性、去重或推理资源治理。

## 根因与最小修复

生产全量正式消息核对发现30条缺少 `bus_messages` contribution：INTC 29条、SNOW 1条，原首次发布业务日为10月24/25日。30条当前消息均由 `bus` 的旧 receipt `:repair:1` 恢复；各消息对应计数水位已经由更晚的原始消息关联或正文事件推进。

`ReadStore.ingest` 原本既按对象身份拦截旧源事件，又按 contribution 身份拦截旧计数。Bus计数实际在当前写事务中从 `object_current` 重算，使用的是当前事实；将其再与输入receipt旧position比较，会跳过正确的计数。结果是正式消息已经恢复，而来源桶和顶部Bus数仍缺失；类似顺序也可能造成路由变更或消息删除后维度未更新。

修复只让这批在事务内生成的Bus派生贡献不再受输入receipt水位阻挡；对象身份水位、来源checkpoint和其他直接贡献水位保持原行为。每个贡献仍按同一metric/ticker/entity替换，差量更新既有日桶及ALL桶，保留历史MVCC视图。

`ingest` 增加可选 `refresh_bus_members`，用于显式定点重算既有消息的派生贡献。它在原writer事务内读取当前消息、正文尝试和关联，复用既有差量路径，不改消息、Case、CDC checkpoint或模型结果；常规调用没有额外历史扫描。没有新表、索引、定时任务或API取数时回填。

## 验证

新增回归在修改前先复现两项失败：旧缺口在较新raw关联后恢复仍为0；旧消息路由修正被较新BODY水位阻挡。修复后覆盖恢复、已有旧水位、路由替换、删除、旧源事件不可覆盖新对象、历史视图、定点补齐及重复补齐不增加序号/计数。

本地44项定向回归通过（ReadStore、投影、指标、streaming、dashboard及新增乱序/定点补齐测试），新增测试文件Ruff通过；最后加强当前对象不回退断言后3项新增用例复验通过。

## 生产发布与历史补齐

功能提交 `81ec32e90f7084722199f4c6050e5ef20982f31a` 已推送origin/main，通过既有host-owned `storage_release.py` 仅更新 `v2-projector`；实际镜像commit、源码archive和repository.py SHA验证一致，Projector running/restart_count=0。发布登记中其余角色保持原镜像；无DDL迁移、前端、采集或交易执行变更。

北京时间18:58执行定点补齐，修复revision为12647715：INTC29条、SNOW1条。API容器独立全量回读：缺失贡献0，正式消息与Bus逐ticker/日期/来源桶差异0；概览 `messages` 同样与正式消息一致。真实API计数函数及来源卡片归并复核：INTC 10月24日16、10月25日103，SNOW 10月25日3（其中Yahoo1），顶部Bus数与来源加总一致。

重复补齐没有分配新commit；30条目标消息完整行未变；修复revision没有消息changes行，全部来源checkpoint无回退。没有重放消息或模型，没有新增Case。本轮保持来源配置、相关性及去重/资源治理边界，不扩展到并行会话的工作。

脱敏证据在 `eval/bus_count_repair_20261103/`：before.json、dry-run.json、applied.json、independent-api-readback.json、api-functions.json、deployed.json、regression.xml、dashboard.xml及release.log。修复采用现有writer/MVCC差量桶，不重建历史数据库，不改旧view在其原seq看到的历史计数；后续新seq自然读取修复值。本次验证为真实生产库/容器和API计数函数，未新增浏览器UI验收声明。
