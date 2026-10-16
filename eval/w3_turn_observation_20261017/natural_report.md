# W3 SDK 交易 turn 自然流量观测

业务分类来自工具参数和公开 commentary 的表面证据，不包含隐藏推理。
分类可重叠，时间不可跨组相加；response 间隔包含模型、服务、传输等混合时间。

| Case / turn | 类型 | 状态 | response 数 | 工具批次 | 输出 KB | 观测秒 |
|---|---|---|---:|---:|---:|---:|
| case_f8c91d46d736446db835000eced42c9b | OPEN_EVENT_EVALUATION | completed | 8 | 7 | 135.5 | 71.65 |
| case_2b3cde144d9a45129fc5e8f83f855490 | OPEN_EVENT_EVALUATION | completed | 11 | 10 | 283.5 | 103.11 |
| case_e8712e9003b44bd4aff973ebb34f0fd3 | OPEN_EVENT_EVALUATION | completed | 10 | 9 | 214.4 | 125.20 |
| case_890995f581f04e8384749dce4a464741 | OPEN_EVENT_EVALUATION | completed | 13 | 12 | 282.3 | 125.43 |
| case_9d52460dac224be88de14e65465eb520 | OPEN_EVENT_EVALUATION | completed | 10 | 9 | 230.2 | 99.93 |

## case_f8c91d46d736446db835000eced42c9b

Turn: `01a145e2-5d8e-7383-b328-3b5eb79840c9`；覆盖：`{"turn_start_found": true, "native_rollout_available": true, "native_bytes_scanned": 2698234, "native_scan_truncated": false, "invalid_native_lines": 0}`

| 业务组 | 批次 | 工具区间并集 ms | 输出 KB |
|---|---:|---:|---:|
| CONTROL_CONTRACT | 4 | 1152.0 | 99.7 |
| RESEARCH_NAVIGATION | 1 | 276.0 | 42.4 |
| EVENT_HISTORY | 3 | 785.0 | 76.5 |
| D3_POLICY | 1 | 318.0 | 33.4 |
| WEB_RESEARCH | 2 | 7351.0 | 35.4 |
| OTHER_TOOL | 1 | 318.0 | 0.4 |

| 工具批次 | 动作及业务标签 | 资源 / 来源域名 | 返回 KB | 区间 ms | 线索 |
|---|---|---|---:|---:|---|
| call_5b9b672834ff46cc9f5085d28206f95f | exec_command / CONTROL_CONTRACT, EVENT_HISTORY, RESEARCH_NAVIGATION |  | 42.4 | 276.0 | repeat=1,  |
| call_eee621c961fe46f4a9bffc5b31ff9703 | exec_command / CONTROL_CONTRACT, D3_POLICY, EVENT_HISTORY |  | 33.4 | 318.0 | repeat=1,  |
| call_0c1251eb3877444ea63a5d51ae609ffe | exec_command / CONTROL_CONTRACT |  | 23.2 | 367.0 | repeat=1,  |
| call_a812b8a54c284dd6972d1f4652ebd9b9 | web__run / WEB_RESEARCH |  | 18.9 | 4288.0 | repeat=1,  |
| call_8308563c9ce24fb1965587ed5d8b08d7 | exec_command / CONTROL_CONTRACT, EVENT_HISTORY |  | 0.8 | 191.0 | repeat=1,  |
| call_c5f9bc6035944d959b19e47b61117177 | exec_command / OTHER_TOOL | consent.google.com | 0.4 | 318.0 | repeat=1,  |
| call_773e9a1a291d4830b277132161fe00a5 | web__run / WEB_RESEARCH | news.google.com | 16.5 | 3063.0 | repeat=1,  |

| 轮 | 输入 token | 增量 | 缓存 token | 前序返回 KB | usage 间隔 ms | 工具外剩余 ms | 返回后间隔 ms | 本轮动作 / 前序业务 |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 46008 | None | 0 | 0.0 | 7499.0 | 7499.0 | 7499.0 | CONTROL_CONTRACT, EVENT_HISTORY, RESEARCH_NAVIGATION |
| 2 | 59230 | 13222 | 45824 | 42.4 | 8436.0 | 8245.0 | 8245.0 | CONTROL_CONTRACT, D3_POLICY, EVENT_HISTORY |
| 3 | 69971 | 10741 | 59008 | 33.4 | 5114.0 | 4834.0 | 4834.0 | CONTROL_CONTRACT |
| 4 | 76401 | 6430 | 69760 | 23.2 | 4527.0 | 4192.0 | 4192.0 | WEB_RESEARCH |
| 5 | 80808 | 4407 | 76288 | 18.9 | 6468.0 | 2575.0 | 2575.0 | CONTROL_CONTRACT, EVENT_HISTORY |
| 6 | 81230 | 422 | 80640 | 0.8 | 7428.0 | 7265.0 | 7265.0 | OTHER_TOOL |
| 7 | 82307 | 1077 | 81024 | 0.4 | 3725.0 | 3417.0 | 3417.0 | WEB_RESEARCH |
| 8 | 86834 | 4527 | 82176 | 16.5 | 28413.0 | 25389.0 | 25389.0 | WEB_RESEARCH |

完全重复调用候选：``；需结合业务复核其必要性。

## case_2b3cde144d9a45129fc5e8f83f855490

Turn: `01a145e4-10db-7ee2-9277-7e35697d4b14`；覆盖：`{"turn_start_found": true, "native_rollout_available": true, "native_bytes_scanned": 2040375, "native_scan_truncated": false, "invalid_native_lines": 0}`

| 业务组 | 批次 | 工具区间并集 ms | 输出 KB |
|---|---:|---:|---:|
| CONTROL_CONTRACT | 2 | 1376.0 | 61.0 |
| RESEARCH_NAVIGATION | 3 | 2314.0 | 70.4 |
| EVENT_HISTORY | 1 | 946.0 | 20.2 |
| WEB_RESEARCH | 6 | 24621.0 | 207.4 |
| D1_RESEARCH | 1 | 375.0 | 5.7 |

| 工具批次 | 动作及业务标签 | 资源 / 来源域名 | 返回 KB | 区间 ms | 线索 |
|---|---|---|---:|---:|---|
| call_8a2ea936bab143a6b6dca62030285194 | exec_command / CONTROL_CONTRACT, RESEARCH_NAVIGATION |  | 40.8 | 430.0 | repeat=1,  |
| call_4afe5804ba304d4a9a784e19b7dd27a4 | exec_command / CONTROL_CONTRACT, EVENT_HISTORY, RESEARCH_NAVIGATION |  | 20.2 | 946.0 | repeat=1,  |
| call_67a69f93068442c590b5e96a32a6a43d | web__run / WEB_RESEARCH |  | 35.9 | 2999.0 | repeat=1,  |
| call_b21d90ca6eaa47e69139d1a072b0d500 | exec_command / RESEARCH_NAVIGATION |  | 9.4 | 938.0 | repeat=1,  |
| call_dbc3f859f407404098ddb8199f20e9f5 | web__run / WEB_RESEARCH |  | 34.7 | 2940.0 | repeat=1,  |
| call_f07a7172b4c543fe9672ccfa5cb9f537 | web__run / WEB_RESEARCH |  | 36.7 | 6363.0 | repeat=1,  |
| call_94f53e54161241d8844ccd8807b89f04 | web__run / WEB_RESEARCH |  | 37.4 | 2991.0 | repeat=1,  |
| call_ca4b49e4478b4146a8983a63b965cfb9 | web__run / WEB_RESEARCH |  | 37.1 | 5547.0 | repeat=1,  |
| call_76eb0a38aa664c059cc17b0efba2aab7 | exec_command / D1_RESEARCH | d1/001_INTC, d1/017_一、当前市场定价基线.md, d1/018_二、近期重定价与主要定价驱动.md | 5.7 | 375.0 | repeat=1,  |
| call_d47a75f4cc3e4f6592541c6d515a6912 | web__run / WEB_RESEARCH |  | 25.5 | 3781.0 | repeat=1,  |

| 轮 | 输入 token | 增量 | 缓存 token | 前序返回 KB | usage 间隔 ms | 工具外剩余 ms | 返回后间隔 ms | 本轮动作 / 前序业务 |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 46146 | None | 0 | 0.0 | 5292.0 | 5292.0 | 5292.0 | CONTROL_CONTRACT, RESEARCH_NAVIGATION |
| 2 | 56672 | 10526 | 45952 | 40.8 | 9267.0 | 8884.0 | 8884.0 | CONTROL_CONTRACT, EVENT_HISTORY, RESEARCH_NAVIGATION |
| 3 | 64438 | 7766 | 56448 | 20.2 | 4347.0 | 3547.0 | 3547.0 | WEB_RESEARCH |
| 4 | 72874 | 8436 | 64256 | 35.9 | 10801.0 | 7843.0 | 7843.0 | RESEARCH_NAVIGATION |
| 5 | 76364 | 3490 | 72704 | 9.4 | 4125.0 | 3268.0 | 3268.0 | WEB_RESEARCH |
| 6 | 85939 | 9575 | 76160 | 34.7 | 7027.0 | 4120.0 | 4120.0 | WEB_RESEARCH |
| 7 | 94522 | 8583 | 85760 | 36.7 | 10142.0 | 3821.0 | 3821.0 | WEB_RESEARCH |
| 8 | 102849 | 8327 | 94336 | 37.4 | 5437.0 | 2465.0 | 2465.0 | WEB_RESEARCH |
| 9 | 111099 | 8250 | 102656 | 37.1 | 10648.0 | 5156.0 | 5156.0 | D1_RESEARCH |
| 10 | 113388 | 2289 | 110976 | 5.7 | 8207.0 | 7929.0 | 7929.0 | WEB_RESEARCH |
| 11 | 120266 | 6878 | 113280 | 25.5 | 27570.0 | 23904.0 | 23904.0 | WEB_RESEARCH |

完全重复调用候选：``；需结合业务复核其必要性。

## case_e8712e9003b44bd4aff973ebb34f0fd3

Turn: `01a145e6-cc22-7332-afa8-a04c99b3be81`；覆盖：`{"turn_start_found": true, "native_rollout_available": true, "native_bytes_scanned": 1209688, "native_scan_truncated": false, "invalid_native_lines": 0}`

| 业务组 | 批次 | 工具区间并集 ms | 输出 KB |
|---|---:|---:|---:|
| CONTROL_CONTRACT | 1 | 580.0 | 41.0 |
| RESEARCH_NAVIGATION | 3 | 1340.0 | 90.6 |
| EVENT_HISTORY | 4 | 1060.0 | 87.7 |
| WEB_RESEARCH | 4 | 12686.0 | 85.7 |
| D1_RESEARCH | 1 | 214.0 | 28.6 |

| 工具批次 | 动作及业务标签 | 资源 / 来源域名 | 返回 KB | 区间 ms | 线索 |
|---|---|---|---:|---:|---|
| call_aefb5efc17534b23aa67eac64d93e0fa | exec_command / CONTROL_CONTRACT, RESEARCH_NAVIGATION |  | 41.0 | 580.0 | repeat=1,  |
| call_2ce5606a7094460dae050d3b5bacc9d1 | exec_command / EVENT_HISTORY, RESEARCH_NAVIGATION |  | 31.8 | 570.0 | repeat=1,  |
| call_c2f94589f109422f8f14a54d7470688a | exec_command / EVENT_HISTORY, RESEARCH_NAVIGATION |  | 17.8 | 190.0 | repeat=1,  |
| call_2dd0a6b2a148467c9d990ece7af63c92 | web__run / WEB_RESEARCH |  | 13.1 | 2863.0 | repeat=1,  |
| call_66bd77d7d1814dbf9bc41d7d6d604e7f | exec_command / D1_RESEARCH, EVENT_HISTORY | d1/$f | 28.6 | 214.0 | repeat=1,  |
| call_5454d3632f744e799b156e2dc015d044 | exec_command / EVENT_HISTORY |  | 9.5 | 86.0 | repeat=1,  |
| call_e84d34136c93413a9c335f075a86d960 | web__run / WEB_RESEARCH |  | 30.8 | 3890.0 | repeat=1,  |
| call_cf20f45a3b384c55b7aec7943c36ad45 | web__run / WEB_RESEARCH | huggingnews.com | 20.6 | 3087.0 | repeat=1,  |
| call_5b1eb1557b0a42218ac15ed0b9a4068c | web__run / WEB_RESEARCH |  | 21.2 | 2846.0 | repeat=1,  |

| 轮 | 输入 token | 增量 | 缓存 token | 前序返回 KB | usage 间隔 ms | 工具外剩余 ms | 返回后间隔 ms | 本轮动作 / 前序业务 |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 46262 | None | 0 | 0.0 | 5623.0 | 5623.0 | 5623.0 | CONTROL_CONTRACT, RESEARCH_NAVIGATION |
| 2 | 56947 | 10685 | 46080 | 41.0 | 9753.0 | 9458.0 | 9458.0 | EVENT_HISTORY, RESEARCH_NAVIGATION |
| 3 | 68577 | 11630 | 56832 | 31.8 | 5484.0 | 4943.0 | 4943.0 | EVENT_HISTORY, RESEARCH_NAVIGATION |
| 4 | 76466 | 7889 | 68352 | 17.8 | 2656.0 | 2504.0 | 2504.0 | WEB_RESEARCH |
| 5 | 80670 | 4204 | 76288 | 13.1 | 10123.0 | 7306.0 | 7306.0 | D1_RESEARCH, EVENT_HISTORY |
| 6 | 89154 | 8484 | 80512 | 28.6 | 2874.0 | 2698.0 | 2698.0 | EVENT_HISTORY |
| 7 | 92417 | 3263 | 88960 | 9.5 | 3679.0 | 3660.0 | 3660.0 | WEB_RESEARCH |
| 8 | 101105 | 8688 | 92288 | 30.8 | 18844.0 | 14963.0 | 14963.0 | WEB_RESEARCH |
| 9 | 108228 | 7123 | 100992 | 20.6 | 14308.0 | 11284.0 | 11284.0 | WEB_RESEARCH |
| 10 | 115370 | 7142 | 108032 | 21.2 | 51476.0 | 48683.0 | 48683.0 | WEB_RESEARCH |

完全重复调用候选：``；需结合业务复核其必要性。

## case_890995f581f04e8384749dce4a464741

Turn: `01a145ed-ae26-7741-9699-d8486a37b0c1`；覆盖：`{"turn_start_found": true, "native_rollout_available": true, "native_bytes_scanned": 2180327, "native_scan_truncated": false, "invalid_native_lines": 0}`

| 业务组 | 批次 | 工具区间并集 ms | 输出 KB |
|---|---:|---:|---:|
| CONTROL_CONTRACT | 3 | 611.0 | 92.9 |
| RESEARCH_NAVIGATION | 5 | 3728.0 | 128.4 |
| EVENT_HISTORY | 4 | 752.0 | 96.6 |
| WEB_RESEARCH | 7 | 21215.0 | 166.0 |
| D1_RESEARCH | 2 | 485.0 | 23.5 |
| D2_EXPECTATIONS | 2 | 485.0 | 23.5 |
| D3_POLICY | 1 | 166.0 | 10.4 |

| 工具批次 | 动作及业务标签 | 资源 / 来源域名 | 返回 KB | 区间 ms | 线索 |
|---|---|---|---:|---:|---|
| call_962024c41795439085c5c68487bf739b | exec_command / CONTROL_CONTRACT, EVENT_HISTORY, RESEARCH_NAVIGATION |  | 42.5 | 306.0 | repeat=1,  |
| call_2138c5c693e3484880f342e63dbea515 | exec_command / CONTROL_CONTRACT, EVENT_HISTORY, RESEARCH_NAVIGATION |  | 40.0 | 139.0 | repeat=1,  |
| call_e487dc9c8a5444afaf4ef6f7cd99704e | web__run / WEB_RESEARCH |  | 31.6 | 3111.0 | repeat=1,  |
| call_32aec7d57bb749e889cb07f49b488397 | exec_command / D1_RESEARCH, D2_EXPECTATIONS, RESEARCH_NAVIGATION | d1/005_三、核心基本面驱动因子.md, d1/017_一、当前市场定价基线.md, d1/018_二、近期重定价与主要定价驱动.md, d1/020_四、市场锚点与定价问题.md | 19.8 | 344.0 | repeat=1,  |
| call_52c5b9c831c04ecc973cb9fb565cb7f3 | web__run / WEB_RESEARCH |  | 31.2 | 3007.0 | repeat=1,  |
| call_8fda1b53e1e64bf38b6e585e09d0bc14 | web__run / WEB_RESEARCH | arxiv.org | 38.0 | 3264.0 | repeat=1,  |
| call_ec7d47860bff4c3195709c76a1967a37 | web__run / WEB_RESEARCH |  | 7.9 | 3445.0 | repeat=1,  |
| call_52127f6d8b314c6faf28b0b21307091a | exec_command / D1_RESEARCH, D2_EXPECTATIONS, EVENT_HISTORY, RESEARCH_NAVIGATION | d1/*.md, originals/document1.md, originals/document2.json | 3.7 | 141.0 | repeat=1,  |
| call_626789ffff4847ef99b45f58face5572 | web__run / WEB_RESEARCH | huggingnews.com | 0.5 | 2836.0 | repeat=1,  |
| call_ee181f659c114c40bcfda3730bc61b54 | web__run / WEB_RESEARCH |  | 34.2 | 2754.0 | repeat=1,  |
| call_419f1bf29b014347849cfb34d23c50e0 | web__run / RESEARCH_NAVIGATION, WEB_RESEARCH |  | 22.5 | 2798.0 | repeat=1,  |
| call_c39cddf34eba42f6b7cdc54d8d8fae87 | exec_command / CONTROL_CONTRACT, D3_POLICY, EVENT_HISTORY |  | 10.4 | 166.0 | repeat=1,  |

| 轮 | 输入 token | 增量 | 缓存 token | 前序返回 KB | usage 间隔 ms | 工具外剩余 ms | 返回后间隔 ms | 本轮动作 / 前序业务 |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 46214 | None | 0 | 0.0 | 5107.0 | 5107.0 | 5107.0 | CONTROL_CONTRACT, EVENT_HISTORY, RESEARCH_NAVIGATION |
| 2 | 58639 | 12425 | 46080 | 42.5 | 6779.0 | 6492.0 | 6492.0 | CONTROL_CONTRACT, EVENT_HISTORY, RESEARCH_NAVIGATION |
| 3 | 70226 | 11587 | 58496 | 40.0 | 4944.0 | 4858.0 | 4858.0 | WEB_RESEARCH |
| 4 | 78617 | 8391 | 70016 | 31.6 | 8147.0 | 5080.0 | 5080.0 | D1_RESEARCH, D2_EXPECTATIONS, RESEARCH_NAVIGATION |
| 5 | 86219 | 7602 | 78464 | 19.8 | 4150.0 | 3872.0 | 3872.0 | WEB_RESEARCH |
| 6 | 94647 | 8428 | 86016 | 31.2 | 4925.0 | 2070.0 | 2070.0 | WEB_RESEARCH |
| 7 | 102924 | 8277 | 94464 | 38.0 | 4861.0 | 1643.0 | 1643.0 | WEB_RESEARCH |
| 8 | 105677 | 2753 | 102784 | 7.9 | 16498.0 | 13300.0 | 13300.0 | D1_RESEARCH, D2_EXPECTATIONS, EVENT_HISTORY, RESEARCH_NAVIGATION |
| 9 | 108583 | 2906 | 105472 | 3.7 | 3997.0 | 3895.0 | 3895.0 | WEB_RESEARCH |
| 10 | 109155 | 572 | 108416 | 0.5 | 14848.0 | 12028.0 | 12028.0 | WEB_RESEARCH |
| 11 | 119491 | 10336 | 108928 | 34.2 | 4347.0 | 1654.0 | 1654.0 | RESEARCH_NAVIGATION, WEB_RESEARCH |
| 12 | 126361 | 6870 | 119296 | 22.5 | 24424.0 | 21640.0 | 21640.0 | CONTROL_CONTRACT, D3_POLICY, EVENT_HISTORY |
| 13 | 132019 | 5658 | 126208 | 10.4 | 22333.0 | 22206.0 | 22206.0 | CONTROL_CONTRACT, D3_POLICY, EVENT_HISTORY |

完全重复调用候选：``；需结合业务复核其必要性。

## case_9d52460dac224be88de14e65465eb520

Turn: `01a145f3-1dc1-7d73-a0a9-e44a7a5016f2`；覆盖：`{"turn_start_found": true, "native_rollout_available": true, "native_bytes_scanned": 1262613, "native_scan_truncated": false, "invalid_native_lines": 0}`

| 业务组 | 批次 | 工具区间并集 ms | 输出 KB |
|---|---:|---:|---:|
| CONTROL_CONTRACT | 2 | 366.0 | 81.3 |
| RESEARCH_NAVIGATION | 2 | 267.0 | 53.2 |
| EVENT_HISTORY | 2 | 366.0 | 81.3 |
| D3_POLICY | 1 | 180.0 | 40.1 |
| MARKET_PRICE | 1 | 180.0 | 40.1 |
| WEB_RESEARCH | 5 | 13900.0 | 127.4 |
| SOURCE_VERIFICATION | 2 | 3183.0 | 30.7 |
| D1_RESEARCH | 1 | 137.0 | 9.4 |

| 工具批次 | 动作及业务标签 | 资源 / 来源域名 | 返回 KB | 区间 ms | 线索 |
|---|---|---|---:|---:|---|
| call_4357c378232d405eb62c08502e518aae | exec_command / CONTROL_CONTRACT, EVENT_HISTORY, RESEARCH_NAVIGATION |  | 41.2 | 186.0 | repeat=1,  |
| call_e58eb257c7b74edea473389d7b3ae13b | exec_command / CONTROL_CONTRACT, D3_POLICY, EVENT_HISTORY, MARKET_PRICE |  | 40.1 | 180.0 | repeat=1,  |
| call_f6b120f8f83246d4a28f529c134de4d0 | web__run / WEB_RESEARCH |  | 36.1 | 3211.0 | repeat=1,  |
| call_9eef9a91eaeb42a4b012312ee4c03202 | web__run / SOURCE_VERIFICATION, WEB_RESEARCH |  | 0.2 | 109.0 | repeat=1, syntaxerror |
| call_606e99f27ce34b4dba2e6cec56895642 | web__run / SOURCE_VERIFICATION, WEB_RESEARCH |  | 30.5 | 3074.0 | repeat=1,  |
| call_a9fbb706cdf641f4999809f0b2d92661 | web__run / WEB_RESEARCH |  | 28.1 | 4416.0 | repeat=1,  |
| call_11997d6823a74bdcae171b1e2d24da59 | web__run / WEB_RESEARCH |  | 32.6 | 3090.0 | repeat=1,  |
| call_fac6a5e787b84b179723f39e6e25fd12 | exec_command / RESEARCH_NAVIGATION |  | 12.0 | 81.0 | repeat=1,  |
| call_103da17fdb4e40b8868fe8d06cc957ed | exec_command / D1_RESEARCH | d1/017_一、当前市场定价基线.md, d1/018_二、近期重定价与主要定价驱动.md, d1/019_三、市场隐含的业务、财务与持续期条件.md | 9.4 | 137.0 | repeat=1,  |

| 轮 | 输入 token | 增量 | 缓存 token | 前序返回 KB | usage 间隔 ms | 工具外剩余 ms | 返回后间隔 ms | 本轮动作 / 前序业务 |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 47779 | None | 0 | 0.0 | 9210.0 | 9210.0 | 9210.0 | CONTROL_CONTRACT, EVENT_HISTORY, RESEARCH_NAVIGATION |
| 2 | 59131 | 11352 | 47616 | 41.2 | 6373.0 | 6214.0 | 6214.0 | CONTROL_CONTRACT, D3_POLICY, EVENT_HISTORY, MARKET_PRICE |
| 3 | 69842 | 10711 | 59008 | 40.1 | 3994.0 | 3858.0 | 3858.0 | WEB_RESEARCH |
| 4 | 78100 | 8258 | 69632 | 36.1 | 6783.0 | 3607.0 | 3607.0 | SOURCE_VERIFICATION, WEB_RESEARCH |
| 5 | 78411 | 311 | 77952 | 0.2 | 2818.0 | 2749.0 | 2749.0 | SOURCE_VERIFICATION, WEB_RESEARCH |
| 6 | 86706 | 8295 | 78208 | 30.5 | 8746.0 | 5761.0 | 5761.0 | WEB_RESEARCH |
| 7 | 94413 | 7707 | 86528 | 28.1 | 18288.0 | 13965.0 | 13965.0 | WEB_RESEARCH |
| 8 | 105910 | 11497 | 94208 | 32.6 | 7103.0 | 4181.0 | 4181.0 | RESEARCH_NAVIGATION |
| 9 | 111506 | 5596 | 105728 | 12.0 | 12946.0 | 12906.0 | 12906.0 | D1_RESEARCH |
| 10 | 115780 | 4274 | 111360 | 9.4 | 23412.0 | 23320.0 | 23320.0 | D1_RESEARCH |

完全重复调用候选：``；需结合业务复核其必要性。

