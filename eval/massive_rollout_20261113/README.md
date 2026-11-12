# Massive 独立正则上线证据

见 `../../docs/massive-distribution-production-rollout-20261113.md`。

- `terms.json`：9套实际导入规则、原候选文件及原SHA-256；只导入massive_l2，没有覆盖候选里的旧普通词库。
- `historical-replay.json`：新入口与历史保存预测逐条比较，两集各50×9，共900，差异为0；包含归一化部署规则哈希。
- `activation.json`、`config-verification.json`：45个真实绑定、词库revision、普通字段未改变及消费者schema回读。
- `catalog-verification.json`：实际生产读模型目录的5×9订阅和主备配置。
- `web-verification.json`、`validation.json`：实际HTTP静态内容SHA-256、构建/单元/浏览器/原生API回归；各重叠测试组合不可直接加总。
- `audit-final.json`：启用后至少60分钟的截止快照。articles为新增BODY文章记录，deliveries按文章×ticker；NO_PUBLIC_BODY不属于正则阴性。列表状态来自当前非TEST单位，GlobeNewswire两路并行。PRN的HTTP失败/RSS备用按降级记录。
- `downstream-verification.json`：正式stream投递与Runtime admission/inbox回执。当前交易日新鲜稿可早于启用时刻发布，不伪称为启用之后才发布的新稿。
- `negative-review.json`：6个完整正文阴性的有界业务复核，非新评测集。
- `releases.json`：各次窄发布及当前角色目标源文件哈希；并行API更新后的实际镜像另见runtime-health.txt。

只读复核脚本为 `../../scripts/ops/audit_massive_regex.py`，在已有Bus容器中执行。完整配置备份和含环境的state/compose快照只保存在服务器私有发布目录，不放入本目录或Git。

本目录忽略临时构建包、逐次stdout/stderr、导出的完整文章和探测脚本。被版本化的回执不包含凭据或完整配置备份。词库旧样本的高P/R不能外推自然全市场表现，正文不可用及未出现正例的源分别保留未验收状态。
