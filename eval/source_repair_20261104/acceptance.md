# 三消息源验收（2026-11-04）

运行版本 35a7396e；Collector、Enrichment、Site Access 已标准发布，Supervisor/Profile 保留。自然观察 UTC19:22:45—19:31:44：Calcalist 30 次已验证物理 listing，NRC 9 次，SDxCentral 9 次（供五个 ticker 共用），均无采集失败。Calcalist 跨一次令牌续期，NRC 运营方缓存 55 项且当前 CEG 正确零条。

正式读路径 BODY 样本均 FULL：Calcalist 4012 字符、NRC 原生事件 1478、SDxCentral 主身份3405；备用经异步冷 canary 后，独立 listing 与 BODY SUCCESS/FULL3405。初次限定备用请求的 ACCESS_EXHAUSTED、BODY 的预算延期均保留；最终已恢复主身份。

证据：baseline/probe/paths/nrc-table、acceptance-first/progress/mid/late/final、body、backup-first/second/body-restore/body-final/body-third、sdx-diagnostics-final、hashes JSON。所有输出脱敏，无凭据/Cookie。NRC 非 CEG 样本未发布；正文抽样不是新消息发布量。Calcalist 历史403原始错误正文缺失，未强行归为同一种原因。199 项相关测试通过；九分钟不代表长期稳定性。
