# Massive Distribution GOLD 2026-11-12

交付说明：`docs/massive-distribution-gold-dataset-delivery-20261112.md`。

- `final/development-gold.jsonl`：调试集。
- `final/validation-gold.jsonl`：验证集，调试候选冻结前不要读取具体标签/理由。
- `final/<TICKER>/`：逐ticker两集，完整输入及GOLD。
- `final/*-review.md`：逐条审阅表；不是调参时必须加载的上下文。
- `dataset-manifest.json` / `annotation-freeze.json` / `verification.json`：配额、来源、去重、hash与标注完整性。
- `baseline-terms/` / `frozen-context.json`：冻结当前统一词表和来源上下文。
- `complete-body-archive.jsonl` / `inventory.json` / `deduplicated-positive-pool.jsonl`：私有复现依据，含完整召回输入及原收据，标注者未读取。
- `annotation/packet-NN.jsonl` / `labels-NN.jsonl`：盲标原包和原始输出；只使用gpt-6.1-terra high。

只读取样与离线数据加工，没有生产配置写入、Decision Model调用或新规则调试。本数据仅测当前规则召回范围，不能估全市场recall；标题分层选样比例不是自然precision。
