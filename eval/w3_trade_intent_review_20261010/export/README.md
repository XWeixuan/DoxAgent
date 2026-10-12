# W3 交易意图导出清单

逐案交易结果和复盘见 [review.md](../review.md)。

- 截止 UTC：2026-10-10T12:53:20.738930+00:00
- 起点 UTC：2026-09-19T12:53:20.738930+00:00（向前 21 天）
- 口径：正式 `trade_intents` 的 `released_at` 落在窗口内，且内含 W3 判定结果；包括重复、过期、投递失败和未成交意图。
- 正式意图：77；Case：77；实际建仓 Case：12。
- W3 判定产生但未释放为正式意图的相邻记录：{'candidates:NOT_SELECTED': 3, 'candidates:DUPLICATE_REALTIME_OUTPUT': 1}。这些未混入交付 JSON。
- 买卖点采用归属于该笔 lot 的真实成交加权均价；盈亏采用读投影中的已实现净盈亏，未实现部分留空。

## 分组

- BE: 15 条
- INTC: 9 条
- MU: 49 条
- RKLB: 4 条

## 文件

- [BE_01.json](BE_01.json)：5 条；SHA-256 `3c1f9b941b3be19246cb143f70b636484a766e2e26a5b07d9ed0382ef610079b`
- [BE_02.json](BE_02.json)：5 条；SHA-256 `09fde1d2fba08011228546d3a9402a87dfaf5fda080e63aed4c9ae99d9a31286`
- [BE_03.json](BE_03.json)：5 条；SHA-256 `fd2dbcc79b25dfe328d2fe8de011c1146003195d36971cce3deb27829c640269`
- [INTC_01.json](INTC_01.json)：5 条；SHA-256 `fcdf850bbd801411db6de425d32088671535a5a5f9178d927f371d627b3ce08d`
- [INTC_02.json](INTC_02.json)：4 条；SHA-256 `8a154ed2cf9c1f5619c41859a9226eeb2869f7d73c55715d4b22c6f33ade2df8`
- [MU_01.json](MU_01.json)：5 条；SHA-256 `b0b2d29d24b1729cea4267ade76d41d509e3d6f7695f2061632fa7d6cb38f8a5`
- [MU_02.json](MU_02.json)：5 条；SHA-256 `9c9d08e1854eb6419cc4f59e4e70c7dfd0fafea8ba20df6cfd2f8f860c94b20a`
- [MU_03.json](MU_03.json)：5 条；SHA-256 `0582b8ba1c0dbdf982440c2aabbcd053ab8b8e7be1ae4b1801d83571443e64dc`
- [MU_04.json](MU_04.json)：5 条；SHA-256 `55aee20590e8731852ea20e7953929c0b6c94e12e7cdcbbc69555cdcd5da1db8`
- [MU_05.json](MU_05.json)：5 条；SHA-256 `7d6ea0f13c1cd6a58fee0f170b1ce458313e444c56f546fb7ce1c9ee93a3670d`
- [MU_06.json](MU_06.json)：5 条；SHA-256 `6896c6c0d3a8ec9eaec288176a70e3ebf0f47c82ec4f3977bd0d8f96965c22fe`
- [MU_07.json](MU_07.json)：5 条；SHA-256 `64386544a5e2c777ea8366b1c888d97e9fd7d7e746657c09855e3aeca72d54be`
- [MU_08.json](MU_08.json)：5 条；SHA-256 `6a24b0492fb0f327d37efbcd7d8de38525d04ca496a0a1a46405579aa376366f`
- [MU_09.json](MU_09.json)：5 条；SHA-256 `b115679c74481f534baefb1578673319fd52f4b3b31a31cda608d68d558c1f5d`
- [MU_10.json](MU_10.json)：4 条；SHA-256 `e22c09ae3806f57338fbd51195cf62db3d862ccd605e83ca3e92cb4f5a17a8ce`
- [RKLB_01.json](RKLB_01.json)：4 条；SHA-256 `350321a98eab691b6c60e3ce5090896b81898aa9283d8515127e826abe335edd`
