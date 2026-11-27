# 绑定级额外正则：2026-11-27 证据

报告：[开发、规则与全历史验证](../../docs/by-distribution-binding-extra-regex-20261127.md)。

`rules-selected.json` 是最终16个实际订阅的规则对象，不自动应用。
`all-history-results.csv` 覆盖全部8,707条；`blocked-review.*` 覆盖全部184条拦截和逐条正文复核。
`protected-controls.csv` 是20条需保留的真实反例，不能当作完整保留集合的GOLD。
两组零效果候选（COHR × Tom’s Hardware/TechNews）暂不建议启用。

本地证据包 `binding-extra-relevance-evidence-20261127.zip` 包含原始`cases.json`、最终拦截正文、人工审阅输入、实验版本、截图、测试结果和报告；ZIP及原文不提交Git。完整性见 `evidence-sha256.txt`。

`export.py` 是在生产只读SQLite连接中执行的导出主体，不包含凭据。它读取当时实际启用的指定四源绑定及其留存正式消息。
`replay.py` 读取本地`cases.json`并使用生产同一清理和matcher，可传`--cases`/`--rules`；没有网络、模型或生产写入。
`review_notes.py` 记录人工阅读全文后的判断，索引对应ZIP中的冻结审阅输入；它只排版校对过的笔记，不是自动GOLD标注器。

本轮没有给第一层拒绝池或全部保留消息补GOLD，不能据减量计算全管线准召。
