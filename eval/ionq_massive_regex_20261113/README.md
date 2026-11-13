# IONQ Massive 50/50 GOLD 与正则

主报告：../../docs/ionq-massive-distribution-regex-tuning-report-20261113.md。

完整数据在本地压缩包ionq-massive-gold-and-regex-20261113.zip；解压后development-gold.jsonl和validation-gold.jsonl各50条，完整title/summary/body与gold字段均保留。不要运行build_dataset.py重新抽样覆盖本次冻结集合。初次建集漏了一个平台公告语言变体，身份修复说明、原集合及旧成绩均留存。

复现评分（仓库根目录，Python路径按环境）：

    python scripts/distribution_relevance_debug.py evaluate --track regex --rule-scope massive_l2 --ticker IONQ --dataset eval/ionq_massive_regex_20261113/development-gold.jsonl --terms eval/ionq_massive_regex_20261113/best.json --context eval/ionq_massive_regex_20261113/context.json --output 新的空输出目录

验证替换dataset为validation-gold.jsonl。两候选文件及词库在身份修复前后均未改动；修复后结果仍属于已暴露的选择参考，不能继续以此反复调词。不同律师通稿可涉及同一事件，不把文章去重误称为独立事件隔离。旧召回池内召回不等于全市场召回。

最终部署只改massive_l2，保留当前普通L1/L2/definition/X账号；生产六个Massive订阅均启用。完整配置备份只在服务器私有数据目录，不在此包。
