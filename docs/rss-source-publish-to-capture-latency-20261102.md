# DoxAgent RSS 消息采集延迟报告：2026-11-02

生成日期：2026-11-03；生产证据导出时间：11-03 23:03:23（北京时间）。只读调查，未修改生产服务、配置或数据。

## 1. 结论

北京时间昨天一天，生产持久记录中有 **30 个 RSS 来源、634 条来源 × 文章首次发现记录**；其中 464 条满足同来源、时间版本一致、精确发布时间及非负差值的计算条件。跨来源同篇文章分别衡量各来源延迟，同来源多 ticker 共用的采集只计一次。

- **实时采集可验证样本 252 条**：平均 5.93 分钟，P50 3.71 分钟，P95 21.59 分钟；超过 5 分钟 97/252 (38.5%)，超过 10 分钟 46/252 (18.3%)。
- **历史补扫可验证样本 212 条**：平均 660.76 分钟，P50 689.12 分钟，P95 1253.93 分钟。这是历史文章被补扫发现的年龄，不能直接当成实时轮询变慢。
- 数据可以衡量“文章标记发布 → DoxAgent 首次发现”的总差距；不能单凭它拆出 RSS 上游何时上架、CDN 缓存、DoxAgent 调度或网络分别贡献多少。
- **这不是全部 RSS 返回条目的无损全量审计**：采集回执接纳后清空 payload，部分过期且从未接纳的条目不进入 live_origins；首次发现记录也不能表示当天旧文章的全部更新次数。下文覆盖全部有可识别新发现记录的 RSS 来源，并明确无法精确量化的部分。

## 2. 日期、时间与统计口径

- 自然日按用户时区 Asia/Shanghai：`2026-11-02 00:00:00 ≤ 首次抓取 < 2026-11-03 00:00:00`，等价 UTC `[2026-11-01T16:00:00Z, 2026-11-02T16:00:00Z)`。按抓取日入样，不强制文章也在当天发布。
- 发布时间取持久文章输入的 `published_at`，只把 `publication_time_basis=EXACT` 纳入精确统计。DATE、RELATIVE、UNKNOWN_FIRST_SEEN 不伪造成精确时间。
- 抓取时间取 `live_origins.first_seen_at`，代码中由采集 `observed_at` 写入；不是 raw_messages.collected_at、正文完成时间、Bus 发布时间或 Runtime 接纳时间。
- 差值秒数 = `first_seen_at - published_at`；表格统一显示分钟。P50/P90/P95 使用 nearest-rank（向上取整秩），平均值按消息加权；阈值为严格大于。
- 以 `source_id × article_key` 去重，取该来源全部 origin 的最早 first_seen_at；同文章同来源的多个 wrapper/URL 别名只算一次，昨天以前已经发现、昨天新增别名的不算新消息。不乘 ticker 数，不跨来源去重，不把后续内容更新算作新消息。`REALTIME` 与 `CLOSED_SWEEP` 依据同版本输入的 admission_context 分开；不能只看 SHARED/HEAD 名称判断是否补扫。
- 为避免当前输入覆盖历史，跨源合并后 input_source_id 不同，或 material_observed_at 与首次抓取不同的记录，不进入主延迟统计。被排除不表示没有抓到，也不表示其延迟为零；逐条明细保留原始时间和排除原因。这个保守子集可能存在选择偏差，不能代表被排除样本。
- RSS/Atom 依据生产已部署 parser、adapter 和单条采集格式识别；不能以 SourceKind.API 或 source_id 后缀直接代替。Yahoo 仅纳入可证明来自 legacy_headline_rss 的输入，GraphQL/浏览器不混入。Corporate IR 只纳入实际 RSS 端点。

### 2.1 全天新发现分布（北京时间）

0 表示没有找到该小时的新 origin，不代表已证明该小时服务停机、轮询失败或没有 RSS 返回。补扫较多的时段会影响混合统计。

| 小时 | 首次发现 | 可比实时 | 可比补扫 | 不可精确比较 |
| --- | --- | --- | --- | --- |
| 00:00–00:59 | 0 | 0 | 0 | 0 |
| 01:00–01:59 | 0 | 0 | 0 | 0 |
| 02:00–02:59 | 0 | 0 | 0 | 0 |
| 03:00–03:59 | 0 | 0 | 0 | 0 |
| 04:00–04:59 | 0 | 0 | 0 | 0 |
| 05:00–05:59 | 0 | 0 | 0 | 0 |
| 06:00–06:59 | 0 | 0 | 0 | 0 |
| 07:00–07:59 | 0 | 0 | 0 | 0 |
| 08:00–08:59 | 0 | 0 | 0 | 0 |
| 09:00–09:59 | 0 | 0 | 0 | 0 |
| 10:00–10:59 | 0 | 0 | 0 | 0 |
| 11:00–11:59 | 0 | 0 | 0 | 0 |
| 12:00–12:59 | 0 | 0 | 0 | 0 |
| 13:00–13:59 | 0 | 0 | 0 | 0 |
| 14:00–14:59 | 0 | 0 | 0 | 0 |
| 15:00–15:59 | 356 | 19 | 212 | 125 |
| 16:00–16:59 | 23 | 13 | 0 | 10 |
| 17:00–17:59 | 20 | 17 | 0 | 3 |
| 18:00–18:59 | 31 | 29 | 0 | 2 |
| 19:00–19:59 | 33 | 28 | 0 | 5 |
| 20:00–20:59 | 41 | 33 | 0 | 8 |
| 21:00–21:59 | 44 | 36 | 0 | 8 |
| 22:00–22:59 | 49 | 45 | 0 | 4 |
| 23:00–23:59 | 37 | 32 | 0 | 5 |

## 3. 所有有新发现的 RSS 来源汇总

按可验证样本的 P95 从高到低排序。“发现数”含后续排除记录；统计列只用精确可比样本，包含实时和补扫，必须结合下一节解读。单位：分钟。

| 来源 source_id | 发现数 | 可计算 | 平均 | P50 | P95 | 最大 | >5分钟 | >30分钟 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| etnews_rss | 12 | 12 | 501.49 | 146.29 | 1441.61 | 1441.61 | 11/12 (91.7%) | 11/12 (91.7%) |
| wccftech_hardware | 18 | 17 | 364.24 | 0.58 | 1310.42 | 1310.42 | 6/17 (35.3%) | 6/17 (35.3%) |
| quantum_zeitgeist | 40 | 40 | 806.29 | 689.30 | 1254.00 | 1254.57 | 35/40 (87.5%) | 35/40 (87.5%) |
| tomshardware_rss | 33 | 22 | 810.13 | 961.64 | 1171.64 | 1201.64 | 19/22 (86.4%) | 18/22 (81.8%) |
| yahoo_finance_news | 48 | 42 | 466.42 | 281.42 | 1151.01 | 1196.13 | 40/42 (95.2%) | 26/42 (61.9%) |
| siliconangle_software | 6 | 5 | 399.94 | 0.89 | 998.88 | 998.88 | 2/5 (40.0%) | 2/5 (40.0%) |
| google_news_search_rss | 270 | 128 | 102.50 | 5.61 | 990.92 | 1391.25 | 77/128 (60.2%) | 15/128 (11.7%) |
| dcd_infrastructure_rss | 28 | 28 | 80.92 | 3.50 | 960.39 | 1200.12 | 4/28 (14.3%) | 2/28 (7.1%) |
| huggingnews_rss | 43 | 43 | 269.44 | 24.48 | 938.36 | 999.76 | 31/43 (72.1%) | 21/43 (48.8%) |
| digitimes_tw_rss | 19 | 18 | 746.39 | 752.82 | 778.79 | 778.79 | 18/18 (100.0%) | 18/18 (100.0%) |
| venturebeat_software | 2 | 2 | 331.13 | 1.42 | 660.83 | 660.83 | 1/2 (50.0%) | 1/2 (50.0%) |
| newswire_rss | 2 | 1 | 547.30 | 547.30 | 547.30 | 547.30 | 1/1 (100.0%) | 1/1 (100.0%) |
| technews_hardware | 50 | 50 | 308.49 | 274.92 | 480.82 | 1439.69 | 49/50 (98.0%) | 47/50 (94.0%) |
| thelec_semiconductors_rss | 6 | 5 | 39.70 | 5.49 | 181.62 | 181.62 | 3/5 (60.0%) | 1/5 (20.0%) |
| register_software_cloud_security | 7 | 7 | 21.50 | 1.14 | 144.69 | 144.69 | 1/7 (14.3%) | 1/7 (14.3%) |
| enterprise_apps_official_rss | 3 | 3 | 37.22 | 3.70 | 106.58 | 106.58 | 1/3 (33.3%) | 1/3 (33.3%) |
| world_nuclear_news | 1 | 1 | 23.49 | 23.49 | 23.49 | 23.49 | 1/1 (100.0%) | 0/1 (0.0%) |
| aviationweek_rss | 12 | 10 | 10.19 | 7.69 | 19.71 | 19.71 | 6/10 (60.0%) | 0/10 (0.0%) |
| canary_media | 1 | 1 | 16.07 | 16.07 | 16.07 | 16.07 | 1/1 (100.0%) | 0/1 (0.0%) |
| crn_cloud_security_datacenter | 1 | 1 | 13.21 | 13.21 | 13.21 | 13.21 | 1/1 (100.0%) | 0/1 (0.0%) |
| dod_releases | 1 | 1 | 11.58 | 11.58 | 11.58 | 11.58 | 1/1 (100.0%) | 0/1 (0.0%) |
| unmannedairspace_counteruas | 4 | 3 | 2.29 | 1.52 | 3.85 | 3.85 | 0/3 (0.0%) | 0/3 (0.0%) |
| globenewswire_semiconductors_rss | 5 | 4 | 2.61 | 2.36 | 3.35 | 3.35 | 0/4 (0.0%) | 0/4 (0.0%) |
| utility_dive | 2 | 2 | 1.73 | 1.73 | 1.73 | 1.73 | 0/2 (0.0%) | 0/2 (0.0%) |
| spacenews_rss | 1 | 1 | 1.55 | 1.55 | 1.55 | 1.55 | 0/1 (0.0%) | 0/1 (0.0%) |
| nbis_official_actor_rss | 2 | 2 | 1.33 | 1.28 | 1.37 | 1.37 | 0/2 (0.0%) | 0/2 (0.0%) |
| siliconangle_ai | 3 | 3 | 0.87 | 0.85 | 0.98 | 0.98 | 0/3 (0.0%) | 0/3 (0.0%) |
| quantum_insider | 11 | 10 | 0.49 | 0.45 | 0.93 | 0.93 | 0/10 (0.0%) | 0/10 (0.0%) |
| ans_nuclear_newswire | 2 | 2 | 0.43 | 0.14 | 0.72 | 0.72 | 0/2 (0.0%) | 0/2 (0.0%) |
| spacecom_launches | 1 | 0 | — | — | — | — | — | — |

## 4. 分开看实时采集与历史补扫

### 4.1 实时采集

| 来源 | 样本数 | 平均(分) | P50(分) | P95(分) | 最大(分) | >1分钟 | >5分钟 | >10分钟 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| etnews_rss | 1 | 4.07 | 4.07 | 4.07 | 4.07 | 1/1 (100.0%) | 0/1 (0.0%) | 0/1 (0.0%) |
| wccftech_hardware | 11 | 0.52 | 0.42 | 1.36 | 1.36 | 1/11 (9.1%) | 0/11 (0.0%) | 0/11 (0.0%) |
| quantum_zeitgeist | 5 | 3.46 | 3.51 | 3.81 | 3.81 | 5/5 (100.0%) | 0/5 (0.0%) | 0/5 (0.0%) |
| tomshardware_rss | 4 | 4.32 | 4.40 | 6.20 | 6.20 | 4/4 (100.0%) | 1/4 (25.0%) | 0/4 (0.0%) |
| yahoo_finance_news | 16 | 10.87 | 9.13 | 20.80 | 20.80 | 16/16 (100.0%) | 14/16 (87.5%) | 8/16 (50.0%) |
| siliconangle_software | 3 | 0.73 | 0.68 | 0.89 | 0.89 | 0/3 (0.0%) | 0/3 (0.0%) | 0/3 (0.0%) |
| google_news_search_rss | 113 | 7.66 | 5.26 | 25.21 | 29.61 | 105/113 (92.9%) | 62/113 (54.9%) | 27/113 (23.9%) |
| dcd_infrastructure_rss | 26 | 4.05 | 3.18 | 5.09 | 27.77 | 23/26 (88.5%) | 2/26 (7.7%) | 1/26 (3.8%) |
| huggingnews_rss | 18 | 4.07 | 3.60 | 10.84 | 10.84 | 14/18 (77.8%) | 6/18 (33.3%) | 1/18 (5.6%) |
| digitimes_tw_rss | 0 | — | — | — | — | — | — | — |
| venturebeat_software | 1 | 1.42 | 1.42 | 1.42 | 1.42 | 1/1 (100.0%) | 0/1 (0.0%) | 0/1 (0.0%) |
| newswire_rss | 0 | — | — | — | — | — | — | — |
| technews_hardware | 1 | 1.16 | 1.16 | 1.16 | 1.16 | 1/1 (100.0%) | 0/1 (0.0%) | 0/1 (0.0%) |
| thelec_semiconductors_rss | 4 | 4.22 | 2.86 | 7.19 | 7.19 | 4/4 (100.0%) | 2/4 (50.0%) | 0/4 (0.0%) |
| register_software_cloud_security | 6 | 0.97 | 0.81 | 1.14 | 1.14 | 3/6 (50.0%) | 0/6 (0.0%) | 0/6 (0.0%) |
| enterprise_apps_official_rss | 2 | 2.54 | 1.37 | 3.70 | 3.70 | 2/2 (100.0%) | 0/2 (0.0%) | 0/2 (0.0%) |
| world_nuclear_news | 1 | 23.49 | 23.49 | 23.49 | 23.49 | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| aviationweek_rss | 10 | 10.19 | 7.69 | 19.71 | 19.71 | 10/10 (100.0%) | 6/10 (60.0%) | 5/10 (50.0%) |
| canary_media | 1 | 16.07 | 16.07 | 16.07 | 16.07 | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| crn_cloud_security_datacenter | 1 | 13.21 | 13.21 | 13.21 | 13.21 | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| dod_releases | 1 | 11.58 | 11.58 | 11.58 | 11.58 | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| unmannedairspace_counteruas | 3 | 2.29 | 1.52 | 3.85 | 3.85 | 3/3 (100.0%) | 0/3 (0.0%) | 0/3 (0.0%) |
| globenewswire_semiconductors_rss | 4 | 2.61 | 2.36 | 3.35 | 3.35 | 4/4 (100.0%) | 0/4 (0.0%) | 0/4 (0.0%) |
| utility_dive | 2 | 1.73 | 1.73 | 1.73 | 1.73 | 2/2 (100.0%) | 0/2 (0.0%) | 0/2 (0.0%) |
| spacenews_rss | 1 | 1.55 | 1.55 | 1.55 | 1.55 | 1/1 (100.0%) | 0/1 (0.0%) | 0/1 (0.0%) |
| nbis_official_actor_rss | 2 | 1.33 | 1.28 | 1.37 | 1.37 | 2/2 (100.0%) | 0/2 (0.0%) | 0/2 (0.0%) |
| siliconangle_ai | 3 | 0.87 | 0.85 | 0.98 | 0.98 | 0/3 (0.0%) | 0/3 (0.0%) | 0/3 (0.0%) |
| quantum_insider | 10 | 0.49 | 0.45 | 0.93 | 0.93 | 0/10 (0.0%) | 0/10 (0.0%) | 0/10 (0.0%) |
| ans_nuclear_newswire | 2 | 0.43 | 0.14 | 0.72 | 0.72 | 0/2 (0.0%) | 0/2 (0.0%) | 0/2 (0.0%) |
| spacecom_launches | 0 | — | — | — | — | — | — | — |

### 4.2 历史补扫

| 来源 | 样本数 | 平均(分) | P50(分) | P95(分) | 最大(分) | >1分钟 | >5分钟 | >10分钟 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| etnews_rss | 11 | 546.71 | 358.93 | 1441.61 | 1441.61 | 11/11 (100.0%) | 11/11 (100.0%) | 11/11 (100.0%) |
| wccftech_hardware | 6 | 1031.05 | 941.27 | 1310.42 | 1310.42 | 6/6 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) |
| quantum_zeitgeist | 35 | 920.98 | 689.45 | 1254.47 | 1254.57 | 35/35 (100.0%) | 35/35 (100.0%) | 35/35 (100.0%) |
| tomshardware_rss | 18 | 989.20 | 1000.26 | 1201.64 | 1201.64 | 18/18 (100.0%) | 18/18 (100.0%) | 18/18 (100.0%) |
| yahoo_finance_news | 26 | 746.77 | 870.57 | 1169.34 | 1196.13 | 26/26 (100.0%) | 26/26 (100.0%) | 26/26 (100.0%) |
| siliconangle_software | 2 | 998.76 | 998.63 | 998.88 | 998.88 | 2/2 (100.0%) | 2/2 (100.0%) | 2/2 (100.0%) |
| google_news_search_rss | 15 | 817.00 | 788.85 | 1391.25 | 1391.25 | 15/15 (100.0%) | 15/15 (100.0%) | 15/15 (100.0%) |
| dcd_infrastructure_rss | 2 | 1080.26 | 960.39 | 1200.12 | 1200.12 | 2/2 (100.0%) | 2/2 (100.0%) | 2/2 (100.0%) |
| huggingnews_rss | 25 | 460.50 | 474.09 | 969.01 | 999.76 | 25/25 (100.0%) | 25/25 (100.0%) | 24/25 (96.0%) |
| digitimes_tw_rss | 18 | 746.39 | 752.82 | 778.79 | 778.79 | 18/18 (100.0%) | 18/18 (100.0%) | 18/18 (100.0%) |
| venturebeat_software | 1 | 660.83 | 660.83 | 660.83 | 660.83 | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| newswire_rss | 1 | 547.30 | 547.30 | 547.30 | 547.30 | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| technews_hardware | 49 | 314.76 | 279.98 | 480.82 | 1439.69 | 49/49 (100.0%) | 49/49 (100.0%) | 49/49 (100.0%) |
| thelec_semiconductors_rss | 1 | 181.62 | 181.62 | 181.62 | 181.62 | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| register_software_cloud_security | 1 | 144.69 | 144.69 | 144.69 | 144.69 | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| enterprise_apps_official_rss | 1 | 106.58 | 106.58 | 106.58 | 106.58 | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| world_nuclear_news | 0 | — | — | — | — | — | — | — |
| aviationweek_rss | 0 | — | — | — | — | — | — | — |
| canary_media | 0 | — | — | — | — | — | — | — |
| crn_cloud_security_datacenter | 0 | — | — | — | — | — | — | — |
| dod_releases | 0 | — | — | — | — | — | — | — |
| unmannedairspace_counteruas | 0 | — | — | — | — | — | — | — |
| globenewswire_semiconductors_rss | 0 | — | — | — | — | — | — | — |
| utility_dive | 0 | — | — | — | — | — | — | — |
| spacenews_rss | 0 | — | — | — | — | — | — | — |
| nbis_official_actor_rss | 0 | — | — | — | — | — | — | — |
| siliconangle_ai | 0 | — | — | — | — | — | — | — |
| quantum_insider | 0 | — | — | — | — | — | — | — |
| ans_nuclear_newswire | 0 | — | — | — | — | — | — | — |
| spacecom_launches | 0 | — | — | — | — | — | — | — |

### 4.3 实时采集长尾案例

下面列出延迟最大的 15 条可验证实时输入；时间均为北京时间。每条同时给出发布时间和首次抓取时间，可与明细直接核对。

| 来源 | 标题 | 发布时间 | 首次抓取 | 差值(分) |
| --- | --- | --- | --- | --- |
| google_news_search_rss | [Illinois Nuclear Plant Upgrades: $5bn Federal Loan Targets Six Sites to Strengthen Grid - Construction Review](https://news.google.com/rss/articles/CBMiwAFBVV95cUxOaExzUERkcmFPMkZkWUZDbFhRaVdxUUxfejV4TG5BRFY1Ty1RMFpHSy01d2tSTl93eG5xbG5HUHNiOTVmZHQ0UHpvMktCMTBoV1l5TzQwRFd0WkJSMjhKMXlVX2tmMkc3Y21qNUlEdDU1UWxIejd4VjJTNDBrVjB4Zk5nYWtwT3hHb0pYQnRjNnVFdjR3bzJtcDdpdUVic3duVHB5Nm1ObmQ5Xy05Y2NEN3BXRTJUTElIX2V6MUZrNWo?oc=5) | 11-02 18:46:05 | 11-02 19:15:41 | 29.61 |
| google_news_search_rss | [Intel: CPU Story Collapses Before Memory Bubble (NASDAQ:INTC) - Seeking Alpha](https://news.google.com/rss/articles/CBMikAFBVV95cUxPRmlBSGZOV0RSbE1Fb2tZLWlNa0s4TTRaLXpab1pzbVA1RDVTYl93V0h4Q1U4aUtpaWpraWNxYmdnV3luSmowcmstR2lLeS00TlBJSzJXWUVsMmNzcDdFRl9FTG42SzZqbTNXYnhuRHhLQnlLajBIRzZVT3g3SmF1SjdWbERnUUtsUzRFX0xmTzI?oc=5) | 11-02 20:46:39 | 11-02 21:15:55 | 29.27 |
| google_news_search_rss | [Intel Stock Plunges Nearly 29% After Weak Forecast, Its Worst Day Since 1974 - Technology Org](https://news.google.com/rss/articles/CBMilAFBVV95cUxOS0lQXzBqMk9CZGgxdzlrMVgxMFZZRXZraDJ1Y0g2OVBoUGV0REpnbUFuZEQwazdsWWs0OFFoQVpSemQxMW9TTExjMTZ4UjYxLVREcVg3VVBkUENnb2NSemo0TXZNMmc5SnJOMU0zX0syT0xqUl9WSVRNTHRhV0JWVGFHbHFlbVU0bllDSUQ5VFBZbHBM?oc=5) | 11-02 15:34:00 | 11-02 16:01:55 | 27.92 |
| dcd_infrastructure_rss | [Sponsored: Huawei Cloud unveils four strategies to accelerate AI adoption in Brazil](https://www.datacenterdynamics.com/en/product-news/huawei-cloud-unveils-four-strategies-to-accelerate-ai-adoption-in-brazil/) | 11-02 22:00:00 | 11-02 22:27:45 | 27.77 |
| google_news_search_rss | [Time to change your tyres: rules in Luxembourg and the Greater Region explained - Luxembourg Times](https://news.google.com/rss/articles/CBMixwFBVV95cUxNOWlRNEJxaFhlZmF2TXU1S3lyb21TNkFuZGxZM0xWT2RucWNFdlhDLTJwNGFFUGhFTWRzMHhlZjNXdFR0YndmX0JWQU1OQ29sd2o0Mk9SR3drSmQyRERFVnNvdms0ZWZOXzlEU2tWTUFoemdZWThJWnhxVFB0LTJXU1pkcDEzUGI1LTlSeF9UbkFkVTJMMjBNa0R5RTRZRThFUFJpek1kMmo2b0dsYUFZbFY1QjF5MGlrLXp0R25wUTVLZDRtY3VR?oc=5) | 11-02 19:13:51 | 11-02 19:41:15 | 27.40 |
| google_news_search_rss | [Apple And Palantir To Kick Off Tech Earnings In Another Crucial Week, AMD To Settle Chip Debate After Intel’s Warning - TradingView](https://news.google.com/rss/articles/CBMiiAJBVV95cUxNTmVOdnlCMGgtTzd5dUJVZnNkM3NwWVVtZlBvTlc0VXktZUVKd25xbnAzS2VhUjdLekt2UF94bU5IdE41eE1MOExLUjF2RG5RMXZiOHR3anNRTkZXV2FfbVRXYzZmTlAzcWpkald3Q1JldzdLQjdZaVhiUXNQdDlXY1hoUG1HbXpJNVVZMzFYbG4xaWM0VGxBLVRQV1gzOURBajEtNzFBV0hNSTE2akVrbmZKWGZ6Ny1LTUd3eE9ZdWJjbnR5VERYQVF4cFFELS1ZR090OUdoeEVVUDAzNUUwX1lOZE9TcUdjZlo0Slp5RUJBbnF2OUd6Z3ppbXE1ZUxWbzc1MmVHM0g?oc=5) | 11-02 16:40:08 | 11-02 17:05:55 | 25.79 |
| google_news_search_rss | [Radar Satellite That Sees Through Clouds and Night Set to Launch on Electron Rocket - Around Prague](https://news.google.com/rss/articles/CBMitwFBVV95cUxPejdNMkN3ZDAyTUROLTZsU2EwSWdORm0yUEpZYWVHRW92SjVDZHcza3dTUF9hTDBZUUwxNzlqTkV1MTF3X0xtMjFQRWJreHd5TUM2TldFRU5TTFgwc2c5M0x5dGNpUy1iV3JvMDhqd1VROEREUy1jNnd3SG9zX2hIeXB6VzBkeFpfQzdpLUp4c0J6ZGZ0Q0N1d3pWZXJ6ZEpoQjVEblhlOURkY3FyYTZEbjFjcDl0bnM?oc=5) | 11-02 16:45:49 | 11-02 17:11:01 | 25.21 |
| google_news_search_rss | [AH Realty Trust completes property sales and debt restructuring - StreetInsider](https://news.google.com/rss/articles/CBMivwFBVV95cUxOMUFCQkFXSHY1RHdlQ0tOWWxYR2xodlk3ZDl1QlU4TjZpOFRBeEVicXpXbXFRRnJkTGFoTFl4TkNEYXRwZDBLNWwtbW9MV2RnRVZHbzBNSnY3c1JMdGt2V05ER3ctdEtNV0pET01uci0wNGY4TzhvU01NSy1DdVVpRFZGdTlyaFM1S2MyV25YWWUxSWVfT3pBUVBrem92OXNKa3c1VHJzaGM5M28xT00wZy1lT19KMnQ0QVdjNTFoYw?oc=5) | 11-02 19:03:50 | 11-02 19:27:41 | 23.86 |
| google_news_search_rss | [Intel, ZKH Launch AI Model to Verify Industrial Supplies - www.marketscreener.com](https://news.google.com/rss/articles/CBMirAFBVV95cUxQdTVlcmhhcVprTHUyaGlfTkhCLXVLTTc5SDNYTGhjWGVlVTU2eV91QUVrYzBlZC1VQnB6NHdGejMzNFhZdnVHT1JwaFVETmtDeV9aZ2RfNVRiNV85QnJFM3ZQSUk5MGJDc282YVBoY1dUME1TS2gwX0pyU0pjSDdFaG8yendPQTV6ZE42aWpjRzZYTUtvUERwMk50SUFTOUNsWEdyQzh5S2M0aDVr?oc=5) | 11-02 18:57:04 | 11-02 19:20:55 | 23.85 |
| world_nuclear_news | [First concrete poured for unit 5 at Haiyang plant](https://www.world-nuclear-news.org/articles/first-concrete-poured-unit-5-haiyang-plant) | 11-02 22:37:39 | 11-02 23:01:08 | 23.49 |
| google_news_search_rss | [Stonepeak completes acquisition of 65% stake in Castrol from BP - TipRanks](https://news.google.com/rss/articles/CBMisgFBVV95cUxQRGxZMUQzTVhHU0Z3VFAzM0ZnQjUzXzVwOFI3NFdKRl9YMFgtOC1UekVrLUJMcjRzNEdUbFNFZEFWZ0N0UVpscUxOTWdjTGt0Q1ZXbXREUk8tSHlIX282Z25keUFNVjhYMWYwTngtTm5kd1VRamdNcF9kUkswbGR3MDV1N0VzcVFSeHVEWWJDSUp4M0Rzc3JHTUhyUHdleU5MT04zVHBHVmc4VHZPWEc0RlRn?oc=5) | 11-02 20:15:31 | 11-02 20:38:35 | 23.07 |
| google_news_search_rss | [Made in New Jersey: Papa’s Tomato Pies keeps a 114-year-old family tradition alive - News12 \\| New Jersey](https://news.google.com/rss/articles/CBMirgFBVV95cUxQMTVnUUhkZ2t4UFlZb3hrdUtVLWYxU3ZHTUUxNkY5SncyVklzcV82aGE0M2pRanJJREZLTHduMzM5bWNJWU1mVTVoZEg2dFFzenRFTkF6RXN5Q0FoOFBQV0V4VTZQU1NhYm9RSG1lWXpqZ2FrTFBFYXdJSUN3d1NGUUx0TFA4ZnF4T1VNVlN0OTNZaUY3Z1ZYYmxBeG9CeGlYeW1Kd2VrT1JUa2lkSGc?oc=5) | 11-02 21:08:36 | 11-02 21:31:15 | 22.66 |
| google_news_search_rss | [Quantum Cryptography Market Heads Toward US$ 15.28 Billion - openPR.com](https://news.google.com/rss/articles/CBMimAFBVV95cUxQN0NXc2k1dVN5dUNDb184RTEzNEFUZC04TklldEM2Y3dyMWJIcEhMeGJxSnpvM0ViT2hJcDNMYjB3OUJxMHduUlJnTjNFcnd6SWhoaUtGc2ZvenFNT3F1Wjk5UER0NDRWZ1BZMXFoYzJhUlpBVjRVc2FkS3BuZVFxSm9FWDRFMHY5TFFKb1oybHhwdlhUQll6dg?oc=5) | 11-02 17:52:00 | 11-02 18:13:35 | 21.59 |
| yahoo_finance_news | [Super Micro Stock Has Given Back Nearly All of Its October 21 Rally. Here’s Where the Stock Could Go](https://www.tikr.com/blog/super-micro-stock-has-given-back-nearly-all-of-its-october-21-rally-heres-where-the-stock-could-go?.tsrc=rss&ref=yahoofinance&tlnk=yf-261102-rv24gc9f) | 11-02 21:05:20 | 11-02 21:26:07 | 20.80 |
| aviationweek_rss | [De Havilland Canada Set To Double DHC-515 Production Capacity](https://aviationweek.com/defense/aircraft-propulsion/de-havilland-canada-set-double-dhc-515-production-capacity) | 11-02 18:48:11 | 11-02 19:07:53 | 19.71 |

## 5. 覆盖质量与异常

| 来源 | 发现数 | 可计算 | 跨源输入 | 输入版本已变 | 非精确时间 | 负时间/缺失 |
| --- | --- | --- | --- | --- | --- | --- |
| ans_nuclear_newswire | 2 | 2 | 0 | 0 | 0 | 0 |
| aviationweek_rss | 12 | 10 | 0 | 1 | 0 | 1 |
| canary_media | 1 | 1 | 0 | 0 | 0 | 0 |
| crn_cloud_security_datacenter | 1 | 1 | 0 | 0 | 0 | 0 |
| dcd_infrastructure_rss | 28 | 28 | 0 | 0 | 0 | 0 |
| digitimes_tw_rss | 19 | 18 | 0 | 1 | 0 | 0 |
| dod_releases | 1 | 1 | 0 | 0 | 0 | 0 |
| enterprise_apps_official_rss | 3 | 3 | 0 | 0 | 0 | 0 |
| etnews_rss | 12 | 12 | 0 | 0 | 0 | 0 |
| globenewswire_semiconductors_rss | 5 | 4 | 1 | 0 | 0 | 0 |
| google_news_search_rss | 270 | 128 | 32 | 110 | 0 | 0 |
| huggingnews_rss | 43 | 43 | 0 | 0 | 0 | 0 |
| nbis_official_actor_rss | 2 | 2 | 0 | 0 | 0 | 0 |
| newswire_rss | 2 | 1 | 0 | 1 | 0 | 0 |
| quantum_insider | 11 | 10 | 1 | 0 | 0 | 0 |
| quantum_zeitgeist | 40 | 40 | 0 | 0 | 0 | 0 |
| register_software_cloud_security | 7 | 7 | 0 | 0 | 0 | 0 |
| siliconangle_ai | 3 | 3 | 0 | 0 | 0 | 0 |
| siliconangle_software | 6 | 5 | 1 | 0 | 0 | 0 |
| spacecom_launches | 1 | 0 | 1 | 0 | 0 | 0 |
| spacenews_rss | 1 | 1 | 0 | 0 | 0 | 0 |
| technews_hardware | 50 | 50 | 0 | 0 | 0 | 0 |
| thelec_semiconductors_rss | 6 | 5 | 0 | 1 | 0 | 0 |
| tomshardware_rss | 33 | 22 | 0 | 11 | 0 | 0 |
| unmannedairspace_counteruas | 4 | 3 | 0 | 1 | 0 | 0 |
| utility_dive | 2 | 2 | 0 | 0 | 0 | 0 |
| venturebeat_software | 2 | 2 | 0 | 0 | 0 | 0 |
| wccftech_hardware | 18 | 17 | 0 | 1 | 0 | 0 |
| world_nuclear_news | 1 | 1 | 0 | 0 | 0 | 0 |
| yahoo_finance_news | 48 | 42 | 0 | 6 | 0 | 0 |

主样本排除原因互斥计数：`{"VALID": 464, "INPUT_VERSION_CHANGED": 133, "NEGATIVE_TIME": 1, "CROSS_SOURCE_INPUT": 36}`。解码失败 0 条。

导出全来源共 1179 条当日 origin：先剔除 17 条此前已发现文章的新增别名，再折叠 205 条当日同来源同文章重复别名，然后识别 RSS 路由。别名剔除计数为全来源导出范围，不能直接与 RSS 表分母相加。

一个实际陷阱：newswire_rss 的 Nebius 状态页在首次抓到后更新了 pubDate。当前记录若直接相减会得到负延迟；本报告将其列为 INPUT_VERSION_CHANGED，不把它解释成抓取早于发布。跨源合并时，同样不使用另一个来源的当前发布时间冒充该 RSS 最初提供的时间。

采集库在本次导出时，所选日期仍有 1061 条 capture，时间范围 11-02 15:01:06 至 11-02 23:59:50，保留非空 payload 为 0 条。它们不能重建整个自然日的原始 RSS 列表。本报告改用持久的 live_origins 与文章输入取证；不声称被过期准入过滤掉的所有候选都已计入。

当前 REALTIME 精确时间准入通常以 30 分钟为上限，可能天然漏掉首次出现就已经很旧的文章；CLOSED_SWEEP 则按其固定历史窗口判断。因此不能用本报告的实时 P95 证明全部 RSS 上游都能在该时限内到达。

### 5.1 混合来源与其他边界

- Yahoo 当日有 153 条原始 origin 记录；经来源 × 文章去重、排除既往文章新别名、识别采集路由后，纳入 48 条 RSS 首次发现。GraphQL/浏览器或跨源输入已失去原始路由证据的记录不算 RSS；即使 source 名称相同也不混算。
- Corporate IR 当日 5 条首次发现记录，其中 0 条来自 RSS，其他是 HTML 端点。
- Massive 测试来源若有记录会保留 delivery_mode 并单独标识。本日导出没有这类来源的新 origin，不能据此断言它们全天无返回或当时已启用。
- 下表按当前来源清单列出未找到当日新 RSS origin 的来源；当前 enabled/绑定状态不能反推昨天完整配置，也不等价昨天轮询成功且无新闻。

| 未找到当日新 RSS origin 的来源 | 当前启用 | 当前 delivery_mode |
| --- | --- | --- |
| optical_systems_official_rss | True | production |
| aws_platform_marketplace_rss | True | production |
| lakehouse_platform_official_rss | True | production |
| observability_vendors_official_rss | True | production |
| corporate_ir_press_releases | True | production |
| breakingdefense_space | True | production |
| defensescoop_space | True | production |
| airspaceforces_space | True | production |
| dod_contracts | True | production |
| defensescoop_drones | True | production |
| shephard_uncrewed | True | production |
| army_news_rss | True | production |
| quantum_computing_report | True | production |
| quantum_insider_business | False | production |
| quantum_insider_national | False | production |
| quantum_insider_capital_markets | False | production |
| register_software | False | production |
| register_cloud | False | production |
| register_security | False | production |
| venturebeat_orchestration | False | production |
| venturebeat_data | False | production |
| venturebeat_security | False | production |
| siliconangle_big_data | False | production |
| siliconangle_cloud | False | production |
| siliconangle_security | False | production |
| crn_cloud | False | production |
| crn_security | False | production |
| crn_data_center | False | production |
| darpa_news | True | production |
| venturebeat_ai | True | production |
| wccftech_ai | False | production |
| wccftech_industry | False | production |
| technews_semiconductor | False | production |
| technews_ai | False | production |
| nist_news | True | production |
| nsf_news | True | production |
| power_magazine_energy | True | production |
| power_engineering_energy | True | production |
| data_center_frontier | True | production |
| ionq_nautilus_rss | True | production |
| ionq_quantum_actors_rss | True | production |
| businesswire_massive | True | test |
| prnewswire_massive | True | test |
| globenewswire_massive | True | test |
| tmx_newsfile_massive | True | test |

## 6. 如何解读与后续排查顺序

1. 判断用户实时体验时优先使用 4.1，按实时 P95、超过 5/10 分钟比例及样本数定位来源；只有补扫样本的来源不能判断其实时表现。小样本来源的 P95 很容易等于最大值。
2. 4.2 中小时级延迟说明这些条目在补扫时才成为持久新发现，不能单独证明昨天实时轮询发生小时级卡顿；需要对照源站上架证据、此前轮询列表和故障窗口。
3. 对实时长尾做具体文章取证：文章标记发布、上游 RSS 首次出现、DoxAgent 轮询 due/dispatch/response、正文/Bus 发布分段核对；本轮只完成前后时间差统计，没有修复或部署。
4. 若需要未来严格全量分布，应保存每条 RSS 候选最初的 source、pubDate、observed_at、准入结果及实际路由，包含过期丢弃项，并保留输入版本；不能从已清空列表中补造昨天缺失证据。

## 7. 证据与复算

- 原始导出：[production-evidence.json](../eval/rss_latency_20261102/production-evidence.json)。
- 机器汇总：[summary.json](../eval/rss_latency_20261102/summary.json)。
- 全部纳入来源的逐条记录，含被排除项：[message-details.csv](../eval/rss_latency_20261102/message-details.csv)。
- 只读生产导出脚本：[export_production.py](../eval/rss_latency_20261102/export_production.py)；报告生成脚本：[analyze.py](../eval/rss_latency_20261102/analyze.py)。
- 生产取证入口：doxagent-sg，实际运行的 v2-message-bus 容器；数据库 `/data/bus/bus.sqlite3` 与 `/data/bus/acquisition.sqlite3`，SQLite `mode=ro`、`query_only=ON`，各库独立只读事务；读取 native content 文件，不构造可写 Repository。
- 冻结导出 SHA-256：`c0e11112673138c62d9998245457de9a1c275c1d8aa4b48269dce47eb85b9c05`。本地复算：`python eval/rss_latency_20261102/analyze.py`。

## 附录：全部可计算消息的时间对照

以下逐条列出精确可比记录；被排除记录仍在 CSV，未静默删除。全部时间为北京时间，差值为分钟。

### ans_nuclear_newswire — ANS / Nuclear Newswire

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 20:49:55 | 11-02 20:50:03 | 0.14 | [NN announces the 2026 40 Under 40 honorees](https://www.ans.org/news/2026-11-02/article-8544/inni-announces-the-2026-40-under-40-honorees/) |
| 实时 | 11-02 23:34:16 | 11-02 23:34:59 | 0.72 | [UW-Madison breaks ground on new radioisotope cyclotron facility](https://www.ans.org/news/2026-11-02/article-8547/uwmadison-breaks-ground-on-new-radioisotope-cyclotron-facility/) |

### aviationweek_rss — Aviation Week

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 15:00:00 | 11-02 15:01:49 | 1.83 | [Airport In Focus: Toulouse-Blagnac Airport](https://aviationweek.com/air-transport/airports-networks/airport-focus-toulouse-blagnac-airport) |
| 实时 | 11-02 17:42:08 | 11-02 17:49:49 | 7.69 | [Hypersonica To Accelerate Testing Pace With Funding Boost](https://aviationweek.com/defense/missile-defense-weapons/hypersonica-accelerate-testing-pace-funding-boost) |
| 实时 | 11-02 18:00:00 | 11-02 18:03:49 | 3.83 | [50 New Routes Launching In November 2026](https://aviationweek.com/air-transport/airports-networks/50-new-routes-launching-november-2026) |
| 实时 | 11-02 18:00:00 | 11-02 18:16:49 | 16.83 | [Podcast: Live From Amsterdam—Sovereignty, Components & Concerns](https://aviationweek.com/podcasts/mro-podcast/podcast-live-amsterdam-sovereignty-components-concerns) |
| 实时 | 11-02 18:30:00 | 11-02 18:31:48 | 1.82 | [Northwood Space Raises $375M In Series C Funding Round](https://aviationweek.com/space/commercial-space/northwood-space-raises-375m-series-c-funding-round) |
| 实时 | 11-02 18:48:11 | 11-02 19:07:53 | 19.71 | [De Havilland Canada Set To Double DHC-515 Production Capacity](https://aviationweek.com/defense/aircraft-propulsion/de-havilland-canada-set-double-dhc-515-production-capacity) |
| 实时 | 11-02 19:50:10 | 11-02 20:07:49 | 17.66 | [China's State-Owned Airlines Head Toward Annual Losses](https://aviationweek.com/air-transport/airlines-lessors/chinas-state-owned-airlines-head-toward-annual-losses) |
| 实时 | 11-02 19:55:11 | 11-02 20:10:48 | 15.63 | [Greece Finalizes KC-390 Purchase](https://aviationweek.com/defense/aircraft-propulsion/greece-finalizes-kc-390-purchase) |
| 实时 | 11-02 22:52:28 | 11-02 22:54:49 | 2.37 | [Video: AI, Drones And More Tech At MRO Europe](https://aviationweek.com/mro/emerging-technologies/video-ai-drones-more-tech-mro-europe) |
| 实时 | 11-02 23:27:20 | 11-02 23:41:49 | 14.50 | [Spotlight: Joramco At MRO Europe 2026](https://aviationweek.com/mro/aircraft-propulsion/spotlight-joramco-mro-europe-2026) |

### canary_media — Canary Media

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 16:30:00 | 11-02 16:46:04 | 16.07 | [Boise plans to tap its geothermal network to heat 300 affordable homes](https://www.canarymedia.com/articles/geothermal/boise-geothermal-network-affordable-homes) |

### crn_cloud_security_datacenter — CRN / Cloud, Security & Data Center

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 22:36:00 | 11-02 22:49:12 | 13.21 | [Arctic Wolf CEO Nick Schneider: AI, Vulnerability Threats Changing In 2027](https://www.crn.com/news/security/2026/arctic-wolf-ceo-nick-schneider-ai-vulnerability-threats-changing-in-2027) |

### dcd_infrastructure_rss — Data Center Dynamics / Infrastructure

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-01 23:00:31 | 11-02 15:00:54 | 960.39 | [What could 1,000x more reliable quantum computation unlock?](https://www.datacenterdynamics.com/en/opinions/what-could-1000x-more-reliable-quantum-computation-unlock/) |
| 补扫 | 11-01 19:00:47 | 11-02 15:00:54 | 1200.12 | [Beyond the grid: the maintenance challenges putting the data center boom at risk](https://www.datacenterdynamics.com/en/opinions/beyond-the-grid-the-maintenance-challenges-putting-the-data-center-boom-at-risk/) |
| 实时 | 11-02 16:00:00 | 11-02 16:03:45 | 3.76 | [Sponsored: How the AI boom is reshaping security and resilience for data centers](https://www.datacenterdynamics.com/en/marketwatch/how-the-ai-boom-is-reshaping-security-and-resilience-for-data-centers/) |
| 实时 | 11-02 18:43:33 | 11-02 18:47:45 | 4.20 | [National Grid buys land in Teesside, UK, for 3.2GW substation to power AI data center](https://www.datacenterdynamics.com/en/news/national-grid-buys-land-in-teesside-uk-for-32gw-substation-to-power-ai-data-center/) |
| 实时 | 11-02 18:49:40 | 11-02 18:52:46 | 3.10 | [Nebul deploys Nvidia GPUs at Datacenter United campus in Belgium](https://www.datacenterdynamics.com/en/news/nebul-deploys-nvidia-gpus-at-datacenter-united-campus-in-belgium/) |
| 实时 | 11-02 18:52:27 | 11-02 18:52:46 | 0.32 | [Germany's Nexspace to develop 4MW data center outside Munich](https://www.datacenterdynamics.com/en/news/germanys-nexspace-to-develop-4mw-data-center-outside-munich/) |
| 实时 | 11-02 19:02:21 | 11-02 19:03:45 | 1.41 | [ServerDomes breaks ground on domed data center campus outside El Paso, Texas](https://www.datacenterdynamics.com/en/news/serverdomes-breaks-ground-on-domed-data-center-campus-outside-el-paso-texas/) |
| 实时 | 11-02 19:20:02 | 11-02 19:23:45 | 3.72 | [Google commits $160m to develop water treatment plant for planned data center in Negeri Sembilan, Malaysia](https://www.datacenterdynamics.com/en/news/google-commits-160m-to-develop-water-treatment-plant-for-planned-data-center-in-negeri-sembilan-malaysia/) |
| 实时 | 11-02 19:22:01 | 11-02 19:23:45 | 1.74 | [Wisconsin utility Alliant Energy expects to sign 1GW of data center supply contracts in next six months](https://www.datacenterdynamics.com/en/news/wisconsin-utility-alliant-energy-expects-to-sign-1gw-of-data-center-supply-contracts-in-next-six-months/) |
| 实时 | 11-02 19:46:37 | 11-02 19:49:45 | 3.14 | [Virgin Media O2 to cut thousands of jobs in cost-savings push - report](https://www.datacenterdynamics.com/en/news/virgin-media-o2-to-cut-thousands-of-jobs-in-cost-savings-push-report/) |
| 实时 | 11-02 20:13:30 | 11-02 20:15:45 | 2.26 | [Plans announced for 45MW data center campus in Oman](https://www.datacenterdynamics.com/en/news/plans-announced-for-45mw-data-center-campus-in-oman/) |
| 实时 | 11-02 20:21:13 | 11-02 20:25:45 | 4.54 | [DCD Studio: Building bespoke utility power agreements for data centers with Lon Huber, Xcel Energy](https://www.datacenterdynamics.com/en/videos/dcd-studio-building-bespoke-utility-power-agreements-for-data-centers-with-lon-huber-xcel-energy/) |
| 实时 | 11-02 20:21:56 | 11-02 20:25:45 | 3.83 | [Green to develop a new data center campus in Birr, Switzerland](https://www.datacenterdynamics.com/en/news/green-to-develop-a-new-data-center-campus-in-birr-switzerland/) |
| 实时 | 11-02 21:27:21 | 11-02 21:31:45 | 4.41 | [DCD Studio: Building at scale, with Michael Nudelman, IREN](https://www.datacenterdynamics.com/en/videos/dcd-studio-building-at-scale-with-michael-nudelman-iren/) |
| 实时 | 11-02 21:34:58 | 11-02 21:36:46 | 1.80 | [DCD Studio: Pricing compute, with Wayne Nelms, Ornn](https://www.datacenterdynamics.com/en/videos/dcd-studio-pricing-compute-with-wayne-nelms-ornn/) |
| 实时 | 11-02 21:59:58 | 11-02 22:02:45 | 2.79 | [GE Vernova order backlog hits $197bn as data center demand drives gas turbine sales](https://www.datacenterdynamics.com/en/news/ge-vernova-order-backlog-hits-197bn-as-data-center-demand-drives-gas-turbine-sales/) |
| 实时 | 11-02 22:03:11 | 11-02 22:07:45 | 4.57 | [FuriosaAI raises $620m at a post-money valuation of approximately $2.9bn](https://www.datacenterdynamics.com/en/news/furiosaai-raises-620m-at-a-post-money-valuation-of-approximately-29bn/) |
| 实时 | 11-02 22:16:57 | 11-02 22:17:45 | 0.80 | [Designing resilient global enterprise networks](https://www.datacenterdynamics.com/en/whitepapers/designing-resilient-global-enterprise-networks/) |
| 实时 | 11-02 22:15:59 | 11-02 22:17:45 | 1.77 | [Digital Realty revenue hits $1.9bn, with a $2.5bn backlog](https://www.datacenterdynamics.com/en/news/digital-realty-revenue-hits-19bn-with-a-25bn-backlog/) |
| 实时 | 11-02 22:21:56 | 11-02 22:22:45 | 0.83 | [Google's Project Suncatcher runs first workload on orbital TPU, but company admits space data centers cost 100x more than terrestrial](https://www.datacenterdynamics.com/en/news/googles-project-suncatcher-runs-first-workload-on-orbital-tpu-but-company-admits-space-data-centers-cost-100x-more-than-terrestrial/) |
| 实时 | 11-02 22:00:00 | 11-02 22:27:45 | 27.77 | [Sponsored: Huawei Cloud unveils four strategies to accelerate AI adoption in Brazil](https://www.datacenterdynamics.com/en/product-news/huawei-cloud-unveils-four-strategies-to-accelerate-ai-adoption-in-brazil/) |
| 实时 | 11-02 22:44:02 | 11-02 22:48:46 | 4.74 | [Rogers expands D2D roaming coverage to international markets](https://www.datacenterdynamics.com/en/news/rogers-expands-d2d-roaming-coverage-to-international-markets/) |
| 实时 | 11-02 23:01:15 | 11-02 23:04:45 | 3.50 | [Nvidia in talks to backstop Nscale's Monarch Compute Campus project for Anthropic - report](https://www.datacenterdynamics.com/en/news/nvidia-in-talks-to-backstop-nscales-monarch-compute-campus-project-for-anthropic-report/) |
| 实时 | 11-02 23:02:08 | 11-02 23:04:45 | 2.62 | [Aligned Data Centers launches new version of Delta Cube air cooling system](https://www.datacenterdynamics.com/en/news/aligned-data-centers-launches-new-version-of-delta-cube-air-cooling-system/) |
| 实时 | 11-02 23:11:35 | 11-02 23:14:45 | 3.18 | [Prometheus Hyperscale pulls out of gigawatt-scale data center project in Casper, Wyoming](https://www.datacenterdynamics.com/en/news/prometheus-hyperscale-pulls-out-of-gigawatt-scale-data-center-project-in-casper-wyoming/) |
| 实时 | 11-02 23:16:24 | 11-02 23:20:45 | 4.35 | [OpenAI CEO Sam Altman meets with Samsung execs as memory chip shortage deepens](https://www.datacenterdynamics.com/en/news/openai-ceo-sam-altman-meets-with-samsung-execs-as-memory-chip-shortage-deepens/) |
| 实时 | 11-02 23:20:47 | 11-02 23:25:45 | 4.98 | [Google eyes 1,000 acres for 1.2GW data center in Goochland County, Virginia](https://www.datacenterdynamics.com/en/news/google-eyes-1000-acres-for-12gw-data-center-in-goochland-county-virginia/) |
| 实时 | 11-02 23:30:40 | 11-02 23:35:45 | 5.09 | [From capacity, to the cost of intelligence](https://www.datacenterdynamics.com/en/opinions/from-capacity-to-the-cost-of-intelligence/) |

### digitimes_tw_rss — DIGITIMES Taiwan RSS

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-02 02:42:14 | 11-02 15:01:36 | 739.37 | [AI浪潮助攻記憶體廠商翻身　蘋果昔日強勢姿態不再奏效](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773084_9Z1L492M93GEAA3JI1384) |
| 补扫 | 11-02 03:00:00 | 11-02 15:01:36 | 721.61 | [Elon Musk態度轉趨務實　傳與台積電「重新協商」Terafab合作大計](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773129_G6I0XKDK38OGCY8KTB1JF) |
| 补扫 | 11-02 02:13:06 | 11-02 15:01:36 | 768.51 | [中國本土AI晶片替代H200　華為、寒武紀、平頭哥各有戰線](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000772973_YZ1L24F86ZVWXV4ND75T5) |
| 补扫 | 11-02 02:49:09 | 11-02 15:01:36 | 732.46 | [黃欽勇：台韓合作比競爭更重要　AI狂飆下台灣靠什麼勝出？](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773094_VAG12PFK8LUZ1Z5HV71RV) |
| 补扫 | 11-02 02:02:49 | 11-02 15:01:36 | 778.79 | [聯發科ASIC傳言逐一拆彈　第二專案與第二客戶雙雙鎖定2028發酵](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773050_MXCL295R6XPX6MLZ6ISCW) |
| 补扫 | 11-02 02:15:04 | 11-02 15:01:36 | 766.54 | [CSP大砸錢「AI世代跑輸代價更沉重」　光寶2027資本支出只多不減](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773147_N1D0AG5P5FS5XH6P8E4XJ) |
| 补扫 | 11-02 02:18:52 | 11-02 15:01:36 | 762.74 | [搶攻車用電子商機　三星電機量產ADAS用超高容量MLCC](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773085_QNF1FPHT7T09N462KR2MV) |
| 补扫 | 11-02 02:14:09 | 11-02 15:01:36 | 767.46 | [三星半導體獲利報捷 　全永鉉籲保持謙遜、穩固客戶信任](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773120_N4Z0KMIE3WZGKOL5PU2OB) |
| 补扫 | 11-02 02:21:29 | 11-02 15:01:36 | 760.12 | [【漫圖秒懂】Elon Musk揭Terafab投產進度　經濟價值目標全球前20大經濟體](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000772979_3A981E7A8ZNE1Z883ZCUE) |
| 补扫 | 11-02 02:28:47 | 11-02 15:01:36 | 752.82 | [【漫圖秒懂】NVIDIA擴張AI版圖受阻　美國政府徹查NVIDIA多起購併交易案](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773112_JO4045FA2PKYKC1JS3YA3) |
| 补扫 | 11-02 02:38:23 | 11-02 15:01:36 | 743.22 | [AI伺服器需求強勁難抵PC寒冬　英特爾面臨供應、製造與代工考驗](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773071_FVTLIMOH8F5DV50LFKPEZ) |
| 补扫 | 11-02 02:04:31 | 11-02 15:01:36 | 777.09 | [新唐：CSP和新創等新客戶加入　2027年BMC出貨規模擴大](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773101_BTK0B5XI1UTK1F0YI801B) |
| 补扫 | 11-02 02:07:46 | 11-02 15:01:36 | 773.84 | [【漫圖秒懂】博通獲630億美元天價融資　強勢供應Anthropic算力](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773110_B0I2NSOE0IZ6SX1AQ2ILU) |
| 补扫 | 11-02 02:33:55 | 11-02 15:01:36 | 747.69 | [立積：網通需求不差卻卡記憶體供貨　Wi-Fi 7明年市佔有望5成](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773051_5YK1IE054Q8DQ829Y30JJ) |
| 补扫 | 11-02 05:00:00 | 11-02 15:01:36 | 601.61 | [DIGITIMES Today：聯發科全面澄清ASIC業務傳言 \\| 台灣愈來愈重視研發 \\| 中國本土AI晶片替代H200](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773144_9DO2FUCT30D9XE53OBNHF) |
| 补扫 | 11-02 02:52:19 | 11-02 15:01:36 | 729.29 | [SK海力士SOCAMM2訂單逐季增加　基板廠急擴產卻恐缺原料](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773104_U9D0HLSU169UQL36R6WHG) |
| 补扫 | 11-02 02:21:02 | 11-02 15:01:36 | 760.57 | [FuriosaAI完成6.2億美元募資、估值翻4倍　挑戰NVIDIA再添底氣](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000773064_6ZQLECPE7DXUFZ354YPA3) |
| 补扫 | 11-02 02:30:18 | 11-02 15:01:36 | 751.31 | [天數智芯天垓300實測效能部分超越H200　長上下文、MoE推論成亮點](https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000772972_FKQLJAX06N9ETY3LYM717) |

### dod_releases — DoD / News Releases

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 23:00:17 | 11-02 23:11:51 | 11.58 | [Department of War's Office of Strategic Capital Signs Conditional Loan Commitment of up to $450 Million With Neros, Inc.](https://www.war.gov/News/Releases/Release/Article/4638926/department-of-wars-office-of-strategic-capital-signs-conditional-loan-commitmen/) |

### enterprise_apps_official_rss — enterprise apps official rss

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-02 13:15:00 | 11-02 15:01:34 | 106.58 | [How India Drives SAP’s AI Future](https://news.sap.com/2026/11/how-india-drives-sap-ai-future/) |
| 实时 | 11-02 20:15:00 | 11-02 20:18:42 | 3.70 | [AI and Transformation Guidance for Every Customer](https://news.sap.com/2026/11/ai-transformation-guidance-for-every-customer/) |
| 实时 | 11-02 21:30:00 | 11-02 21:31:22 | 1.37 | [Workday to Announce Fiscal 2027 Third Quarter Financial Results on November 24, 2026](https://newsroom.workday.com/2026-11-02-Workday-to-Announce-Fiscal-2027-Third-Quarter-Financial-Results-on-November-24,-2026) |

### etnews_rss — ETNews RSS

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-02 08:25:10 | 11-02 15:01:36 | 396.44 | [바디프랜드, 전국 라운지에 건강관리사 1급 자격 직원 배치](https://www.etnews.com/20261102000041) |
| 补扫 | 11-02 12:35:19 | 11-02 15:01:36 | 146.29 | [류재철 LG전자 대표 “AI 홈 다음 표준은 '돌봄'…고객 시간과 노력 아껴야”](https://www.etnews.com/20261102000268) |
| 补扫 | 11-02 13:11:05 | 11-02 15:01:36 | 110.53 | [삼성전자, '2026 다카르 청소년올림픽' 공식 후원](https://www.etnews.com/20261102000308) |
| 补扫 | 11-01 15:10:41 | 11-02 15:01:36 | 1430.93 | [日 APL 찾은 이재용, AI승부처 '첨단 패키징' 챙겼다…소부장 협력 논의](https://www.etnews.com/20261101000090) |
| 补扫 | 11-01 15:30:00 | 11-02 15:01:36 | 1411.61 | [삼성 TV 라인업, 내년 '울트라·플러스·베이직' 3단계 개편…복잡한 기술명 떼고 구독서비스 강화](https://www.etnews.com/20261030000262) |
| 补扫 | 11-02 08:51:21 | 11-02 15:01:36 | 370.26 | [다이슨코리아, 로봇·펜슬워시 물 청소기 2종 출시](https://www.etnews.com/20261102000075) |
| 补扫 | 11-02 13:07:52 | 11-02 15:01:36 | 113.74 | [한국디자인진흥원, 성남시 공공기관 협의체와 ESG 환경 실천](https://www.etnews.com/20261102000303) |
| 补扫 | 11-02 09:02:41 | 11-02 15:01:36 | 358.93 | [하츠, 아일랜드 주방 전용 '커브드 엣지 후드' 출시](https://www.etnews.com/20261102000091) |
| 补扫 | 11-01 15:00:00 | 11-02 15:01:36 | 1441.61 | [붙이기만 해도 20℃ 뚝…스타트업 0.1㎜ 방열시트로 전자제품 열 잡는다](https://www.etnews.com/20261026000245) |
| 补扫 | 11-02 13:00:00 | 11-02 15:01:36 | 121.61 | [쑥쑥 느는 AI데이터센터… “국산 NPU·냉각장비에 초기시장 열어야”](https://www.etnews.com/20261102000281) |
| 补扫 | 11-02 13:09:42 | 11-02 15:01:36 | 111.91 | [한국디자인진흥원, 2026 ISC 혁신성과 경진대회'대상'수상](https://www.etnews.com/20261102000307) |
| 实时 | 11-02 15:00:00 | 11-02 15:04:04 | 4.07 | [중국마저 꺾였다…글로벌 가전시장 침체 장기화](https://www.etnews.com/20261102000272) |

### globenewswire_semiconductors_rss — GlobeNewswire Semiconductors RSS

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 20:00:00 | 11-02 20:03:21 | 3.35 | [POET Technologies Names Giovanni Pacelli CFO](https://www.globenewswire.com/news-release/2026/11/02/3393016/0/en/poet-technologies-names-giovanni-pacelli-cfo.html) |
| 实时 | 11-02 21:00:00 | 11-02 21:02:21 | 2.36 | [Menlo Microsystems Appoints John B. Kennedy as Chief Financial Officer](https://www.globenewswire.com/news-release/2026/11/02/3393065/0/en/menlo-microsystems-appoints-john-b-kennedy-as-chief-financial-officer.html) |
| 实时 | 11-02 21:00:00 | 11-02 21:02:21 | 2.36 | [STMicroelectronics announces status of common share repurchase program](https://www.globenewswire.com/news-release/2026/11/02/3393073/0/en/stmicroelectronics-announces-status-of-common-share-repurchase-program.html) |
| 实时 | 11-02 21:00:00 | 11-02 21:02:21 | 2.36 | [ASML reports transactions under its current share buyback program](https://www.globenewswire.com/news-release/2026/11/02/3393071/0/en/asml-reports-transactions-under-its-current-share-buyback-program.html) |

### google_news_search_rss — Google News Search RSS

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-02 09:21:29 | 11-02 15:00:19 | 338.84 | [Oracle surges 10%! Is cloud providers' capex reaching an inf... - Futubull Community - 富途牛牛](https://news.google.com/rss/articles/CBMipwJBVV95cUxNWTFXS0VHbHJCdXp4WDlFa0ZTV2pSX3g1WkFHZFV2QVRqaEdtLXNnVnh6TVFsekJ4eXZ5QVlDVlA1UUFKcHJLUzRJd0NRcXpmbUdseWducG9CSGtqRkdmVF9JRFhoSmNRLXo5R2hUN3VqaGNUZDd1VU5ial9aUUNuOThsSkhXZFJrWm1fQ3ZyLVVJRG90eEhVVDZ6aW5jdUN1b1VLN2RqSVM3dnJNbUtXU2g2UTZYU0UwYjZSZV9CT1lIX0tOVTZtZGY1SF9ocmY5TEtyMmVKd1BVNDA4YmlmTk5kS2ZpYWlGQlZHeUFfTWJiSWN4aWYwNVNHSk1DZloyTHZCb0ZvVFpoazNuWm1OdEprbzVlNVZ6cjZqRFlpdWlPVDlsem1J?oc=5) |
| 补扫 | 11-02 02:33:00 | 11-02 15:00:19 | 747.32 | [Is the AI bubble bursting? In China, it has already burst - TradingView](https://news.google.com/rss/articles/CBMisgFBVV95cUxPN0RKYmtkVmtwT3lIWG1XczZLMFdxcm90dFpYSkNUbV9PakhyeG14SGtVY00ySnpMQnd3NWZCT21QMy01M1FRQ1BDX0NocndRbHNRSktuY1ZjYTZOdnRCM2U1dTJRMkFkWEdzT2ptcFNiRmtMX0FWaXlRbkpYNV9HT1l2bjd0RldTQXhCUHhhTEl2NkVjZ0xZb2R1SmphdHFJNFB6a3hLX25fekx0Sy14clZR?oc=5) |
| 补扫 | 11-01 15:49:04 | 11-02 15:00:19 | 1391.25 | [Neocloud Balance Sheets 2026: Cash vs Debt at CoreWeave, Nebius, IREN and Peers - FourWeekMBA](https://news.google.com/rss/articles/CBMiXEFVX3lxTFBQU0VwclVtdDdJNnZQcTg0RjRvMmtqeXcyN0ZmTU9NcUhuSkxHWWNTUkpkQ0NhLXRhVWJ3cmF0b1VaU3VyTHpsVkhCVXo0S0NjaUNfRzZuU0VsUW1Z?oc=5) |
| 补扫 | 11-01 20:10:54 | 11-02 15:00:19 | 1129.42 | [Nebius after Microsoft spend cut: $255.14 bull, $73.42 bear - FinanceFeeds](https://news.google.com/rss/articles/CBMikgFBVV95cUxPcDhTVlVpYzdxOHFscVpJV25seW5hVVQteDhVMFVQZ1FEU3A2VWdtQ2FVT1VYaEhJLVRuRzhCSndxb2V2eHRFNEhtbER6NlpKQnpVZ0E4eFBsWENZd2o1TkYxaG1QamtONjBtZW5xdlp0SDA2ajBhcGV5Z0JoZDZqbHo5NmViOW54YjRrRXJpWjlMZw?oc=5) |
| 补扫 | 11-01 21:08:50 | 11-02 15:00:19 | 1071.49 | [Top Investor: CoreWeave Locked In Compute “Way, Way Under Market.” Rising GPU Prices Won’t Save It - TIKR.com](https://news.google.com/rss/articles/CBMiuwFBVV95cUxNdUM5WnJvak9vY05Jd1BzMFNKd2lPeUxvM3lyU0lzYXc2RVZabFdxS1RkVDE3RjREZmVNblh3elJZS0toVks2MnBIdTNfYWtGLW9IS3FsU2NIOEJmSklUeWNVR1BtM1h1bmo2ZGV3Z1lvMm9aTmpLaTU1WVFIUG5HY2xzdVFCNWNUMEM5YXJhS1V5U0o0N1FlVzlqdndJQ2VGVEVPb2NhY0lYR1ZTZjh0ZndIR1F1MTEzS3hB?oc=5) |
| 补扫 | 11-01 19:34:34 | 11-02 15:00:19 | 1165.75 | [Obscure neocloud lenders, not Nvidia, are where the AI bubble could burst first - Startup Fortune](https://news.google.com/rss/articles/CBMiqAFBVV95cUxNUkVSYTZKODBMUGwyQnl0Nk9RaE1zYXYwQy00OVhDanNlU1laWm9BdU41YlZJeFRwX0VSX2xNc0RlOG8zbkJ2dkpRWi1pRzI1c2ZYV2I0T0piWkFVTVBxYUVLNmszRHp1eFh1MHRQQ2loeGVldll0TnM3WmIwbG4xejlVMEhqWVNUSVRfQVN3UHpyVGJ0QkdsUmItcXdxYUtTTVhPVEROeV8?oc=5) |
| 补扫 | 11-02 12:07:48 | 11-02 15:00:33 | 172.76 | [Archer appoints David Vander to lead quantum commercialisation - TipRanks](https://news.google.com/rss/articles/CBMitAFBVV95cUxNS2E0Z0Q3cDA4S3BQY2h4X2Vxdl9MdUZiQy1NenBhckFmbEVmSGsyeVNYeHZfalkwS1otRFBvaFAzRC1OZG5FU210cVk2d3BXV2ltOVFxSXkwb1RGTWp1cC1lZjRFSmpGYjZVRzVTUVBRWnVpNjhqMGl0cW94Y0FUbldqMHBaX2hGek5sYVVXWUowNi1Lek1jVThRcjg1NFkwaUM4ZG5FcUFXSF9FaENPWFRRY2o?oc=5) |
| 补扫 | 11-02 07:59:18 | 11-02 15:00:33 | 421.26 | [Archer Materials Appoints David Vander as Inaugural Quantum Business Executive - Kalkine](https://news.google.com/rss/articles/CBMivAFBVV95cUxONlJ2VUotcDVRY2UyZG5falhWVmtGQzYtTjhBYkJMdUw1VkMyWnlqVl9CWkp3YzVyTnBhdG13TU53ckVYZHliajNlZ095N0N1ODdpalZVYS1YNFhJX3ljRHdPSGQtMXVGTzRJTWJvXzlDRy1RNVVwU2l5cHJlOHR1WjdxcFRFbXBVb2NTN1lKUDBLRGZNeHh2UHRuYTc0R1hDRHBHSWl5U1JZVTRTMHZPVV9WOWJQRS0zQ1k3aQ?oc=5) |
| 补扫 | 11-02 01:52:18 | 11-02 15:01:09 | 788.85 | [BE Stock Recovers After 10% Selloff: Morgan Stanley Says Crusoe Project Pause Does Not Break AI Power Thesis - Stocktwits](https://news.google.com/rss/articles/CBMiwwFBVV95cUxQNjlDbi1IQTBRdFN2cjFXR0FUTzRHZnIwUlVfMFZYdzFOelF4dDRSZ3A2dTVnczZ5bG83X3hURURjMUJkdVZlckRublhWNHVFb0wwZ0JwQVdzTTMyVFhLZXNySnd3VV9TSHFSTlVFd1M4UnpIUVQxQ29uSHlPSjc1MjVrNy16dDJVVG5LQm5qT1NvMmlEYjZicHFrZ2ltYVd0WGFCYlRQLWt5a0xaNGF6c283THpXLTItZDU5b0lqTVBEa0U?oc=5) |
| 补扫 | 11-01 21:43:00 | 11-02 15:01:41 | 1038.70 | [Is Bloom Energy Stock a Buy Right Now in November (2026)? - The Motley Fool](https://news.google.com/rss/articles/CBMingFBVV95cUxNS1R3MVk0bjRNRTlKZVVqcGUtbng4OHJfVUx1NG5xVHRNajBqa3kyeGktN0ZyTnd4NVdYVDY0QnAxU0t1TmRTYzJxOTJBX2hxUzFiRlZ2YThXOFJZNi1SWHFWZ2g4UUg2S3ZSUjVTZ2JCWHlQeEtSY2h0emk4QXVrMUphTmhGWWZxbjdub21tWG9ueXlrYWY2VFFGUkFfQQ?oc=5) |
| 补扫 | 11-02 02:00:00 | 11-02 15:01:41 | 781.70 | [2026-11-01 \\| Bronstein, Gewirtz & Grossman, LLC Is Investigating Bloom Energy Corporation (BE) And Encourages Shareholders to Connect \\| TSX:BE \\| Press Release - Stockhouse](https://news.google.com/rss/articles/CBMizgFBVV95cUxPczFtdVA2bFAwSmlYck56ZU1fVzYxaVBvM2FVRGlhb3pkSlp0bFNXZ2tyS0ExOGtRMldiVjh4TjF4c1JqSzk0ZGJVRDBBMTk3Ml9fRFhBYWxJZjlLZTEzOGFKNHd1NE5qdFNTQTE2Qk42cHhuMS1yczlrR1BfbWYwd081OTJqYkx5dVJNd2ozcFNqeklJWmNJTFBydmg5dlRzQjVrNzdocFIwLWMzTDBJTTk4MHBQUzc1NDlIOFJsOWxvSm8xS0F5V1VIN1didw?oc=5) |
| 补扫 | 11-01 18:54:00 | 11-02 15:01:41 | 1207.70 | [Bloom Energy Tokenized bStocks Price (BEB/USD) Today \\| Live Price, Market Cap & Chart - Binance](https://news.google.com/rss/articles/CBMicEFVX3lxTE9peHpCQ0EwMnVfeFpUeG1PRmlYZGNndGJVZUNLd1dFdm9GMnloXzVnaEpNMllJT055RHF3c3phdWNKS2VOd3F5N2RZdWYxeVBGYkR2MGRJdHRQMXJMRmkzRDUzTUVJZDlMbDBuQnBFZXI?oc=5) |
| 补扫 | 11-02 11:07:00 | 11-02 15:01:41 | 234.70 | [AMETEK (AME) Q3 earnings report preview: what to look for - TradingView](https://news.google.com/rss/articles/CBMitgFBVV95cUxQdGhnMFNZRkpGVmVITlZNR05mR2FnVTI1eE1SdUF3VTRXakVyNjRjbE5aUjZkNWRxVjg0NktfckE5WVFLaHliZkFjVnVzSVpPR0oySTNycWlhaVdvXzlDcmdreGtub1FNR0JJVXlyN0xadHA2VVN6em55WVJlMnpvX19wYTMwTlF1dkZ4dnZQQjdxYm9PeFFqVWtsaTdCQVBVa3o0VlRRdE9Dc3dtaHpVLV9lcWI4dw?oc=5) |
| 补扫 | 11-01 22:30:47 | 11-02 15:01:41 | 990.92 | [Is Bloom Energy Stock a Buy Right Now in November (2026)? - The Globe and Mail](https://news.google.com/rss/articles/CBMizgFBVV95cUxQdkFIZEhtNmNtaEppV3pGeWNMemY4UW9QUWx6N19lTmhjM3FUeGlnWms4ZEFieGpVSEUtaXJDbVRHWFJVRjRneG1xSXJaamRUOFlDN3RYNXNXdF9DUjgtYTJ1VGVzTjVmV0JFMnNFWk0tLWhZdGpMakpSVkxTd1pFZG1venR4enRqS09VU1otUnJ5X2hNSmNTTjRINGFfVGloUXFkTDk5eEtkRnVUVlBGbW5PSGlpN0pDTC1QZlZBaTRveTJJQ19nWDNxWVEydw?oc=5) |
| 补扫 | 11-02 02:07:25 | 11-02 15:01:41 | 774.28 | [BE Stock Drops After-Hours: Bloom Energy Q4 Guidance Comes Above Estimates, But Negative Cash Flows In Q3 Dampen Sentiment - Stocktwits](https://stocktwits.com/news-articles/markets/equity/be-stock-drops-after-hours-bloom-energy-q4-guidance-comes-above-estimates-but-negative-cash-flows-in-q3-dampen-sentiment/cZxwNzSRBD1) |
| 实时 | 11-02 14:59:00 | 11-02 15:07:55 | 8.92 | [Institutions and buybacks lift South Korea stocks after Intel shock - Chosunbiz](https://news.google.com/rss/articles/CBMigAFBVV95cUxPWjdGQ0NvdnVyQi1HSThfUE1DcmhXTElEdTNrMUlCWVJPdi1OT2JYS0w5UkJGY2lnMXkxc2VFYUpkR2NOa3o0ZDRjLVp1a2FZb016NHZoMmQwZVc0a2Y1aVotdGhiOFJ5bXAwVzFqaVBETDFLZi1jZTVieDBGUURWYtIBlAFBVV95cUxOT1BqX2dwYl9BOEtkenRpSlJZSlltbVVmTTY4UXVwaG1obUIxbUt2VVh6eFI1TU5jckRTMDc4U1c2NVZud2lpaC1DZ25QNGpJNXRuQW50NG9fLU9JVU9qMG1BSnQ3TFh1c1Rjai1xTFA2dk5XX01xVFdRdVJJM082MXFvU3pQVGZ2RllPX1lQY2VqdEEw?oc=5) |
| 实时 | 11-02 15:14:57 | 11-02 15:18:55 | 3.97 | [AMD Zen 6 and Intel Nova Lake will deliver up to 20% performance gains in 2027. - GameGPU](https://news.google.com/rss/articles/CBMiugFBVV95cUxNbnNqTWNsQTU3RXhPSFVTZVdvaUx2S3ZHV3pPRjZTSzNCUUsyN3Bjb2E4b0Q5ZHhXOXhuY0ZwYXhwcmdpN0QzdDhmUlBMWFBBOTJ0YjFhQWg1T3puWHcyVGVXMWVUXzg1MDVpbzBsbkZ2d3RDSFVGTjJOdnlOYUtfVklnMHlNSl9rMkRlLUtJcFBCdHYtd21FZXBKSFMxN3FocDRMUjgycWctdHlGWFpvMXBZMWExS3E0b1E?oc=5) |
| 实时 | 11-02 15:22:17 | 11-02 15:24:55 | 2.64 | [Jim Cramer Warns Against Assuming Tech Market Bottom After $1 Trillion Chip Stock Drop - KuCoin](https://news.google.com/rss/articles/CBMiuwFBVV95cUxQOVFObU05LURPVHRCOGQ3UEttYWtobERBSzZfVHRuaU5NSWozd3BDZk5McXlhZFZJYi1sUUNjeGQzNnlDMkZtODV6T2E3ZUJ6TUFieVVRc2xDSE1iUkdEMHlBMmQ0X1lFUGdTb1JGVFJFZVdNTGE2dGoydnlWYjk5dDY4YWR6UTJtMnFqOHJtUFBWUm9ELVZScnh5bGtoamstcXZaWVRPTUtHSVNOdjFKQTUxN2FNVnpSc29j?oc=5) |
| 实时 | 11-02 15:35:42 | 11-02 15:36:28 | 0.78 | [CoreWeave, Nebius and Nscale effectively shut out of the bond market as AI lenders tighten terms - Dealroom](https://news.google.com/rss/articles/CBMiyAFBVV95cUxNWURYaHdfc0poN3IzaER2ZGNSTWdTaE5VWkItNlJ5UlFlT1J2QUpyMFYxbmN0T1dHekZQRlJyLUc3MlhCRWZyUUliSVRQMHllb1VYZHpHbUtfQ3d3RUIySnU0RGpCVDljVHJJWmYyQ2hFNFgtcXRfRloxcHAyLTFXekhoZmdjTTNoSFQ3MENqUzEzNHpLek1odHRIZGhoenhVc3N1dkxvWmVfWUo0YmhTdG1FemI1NThyd3A1R2l3ZVhqdGJQV3JnOQ?oc=5) |
| 实时 | 11-02 15:38:44 | 11-02 15:40:55 | 2.19 | [One-Chart Preview \\| AMD's Q3 earnings report will be released after the market closes on November 3! Following Intel's sharp decline, a critical test lies ahead—can AI growth offset concerns about PCs? - 富途牛牛](https://news.google.com/rss/articles/CBMimAFBVV95cUxOMnFUdV9jZGNlNGt3T2ZqalpVd2Jqb3B5Y0xheUpjaDhVWHlMRjk1MW80R291ajVBMVNzNVlLN1dQZlE4TjV6ZTZRUUd3U0lxbnlEWERVWkpfcWsyNGpvNGtsYXpuTkxScVRxenlUTHVfZlo1ZXMtUi1zUFptcWtHYlJXYlNoYU9aM3hKS1lFNi00MWNodGVvcg?oc=5) |
| 实时 | 11-02 15:42:59 | 11-02 15:43:55 | 0.94 | [Aitech introduces rugged VME SBC with Intel Core i7 processing - Military Aerospace](https://news.google.com/rss/articles/CBMiwAFBVV95cUxON1RLZnZwcXdYVk02ZzFabkc2ZmFLdHFwOHdWRmdOR0IwUmp3RWFFTkpkQjNoNkFlaW5ITV80dWpjYVhMNnJuMlZ1UDh5RWYxZzlRVS1CNGs1WWtVMml1cVNCVTM4d1lZcEdMbU1VUEN1blB3SEtCc0x5YTR2eDhhV2VfTHZVOUhjLUhNYnBscktiYmI3UFZValJwdDNRdWZnSUlKUThTbFNkNlBSNGFNZ3VodFRiVkNaczFkb2RWSWM?oc=5) |
| 实时 | 11-02 15:44:00 | 11-02 15:49:55 | 5.92 | [“Fears of layoffs have resurfaced”: Intel’s Israeli workforce shaken by stock collapse and factory disaster - CTech](https://news.google.com/rss/articles/CBMiZ0FVX3lxTE1ER3VqdmNQbXhaZk1Uc1VUSFJRVnMyZjlNU25RcU11UzRZLUFMYVVCbDFidDc5dGUxd2lmUkFMWWJaVkxEbFBpNUpiUFVnVU9TVXV1S25BS1FoaEZvTU1EUzNUMUpnejg?oc=5) |
| 实时 | 11-02 15:45:33 | 11-02 15:49:55 | 4.37 | [Where Smart Money Flows \\| Earnings Gravity: Alphabet and Intel Drag the AI Trade Back to Earth - Moomoo](https://news.google.com/rss/articles/CBMiugFBVV95cUxOLTI2eUZibWJrbEI3dl9QUko2Mk9QX2tiTVRkV0lxNjViaU5Ld2xSR2RoYzIwQ0E2anFESXZDdHJWUnJSM0lDVElvUTdYWDhoeG92alJWcTZ4dXNXRWQ4RXFGN2VVeGxGSkdzNk9LRlBuLWYyM1QtVWNrQV9XRURXekxucVo5SDczTGc1bEhHY3pGeUUzcUxVNXZoY3gyZVM3cEZjRHhpSjF1VENoRDZMMmEtOF83VmdKMEE?oc=5) |
| 实时 | 11-02 15:34:00 | 11-02 16:01:55 | 27.92 | [Intel Stock Plunges Nearly 29% After Weak Forecast, Its Worst Day Since 1974 - Technology Org](https://news.google.com/rss/articles/CBMilAFBVV95cUxOS0lQXzBqMk9CZGgxdzlrMVgxMFZZRXZraDJ1Y0g2OVBoUGV0REpnbUFuZEQwazdsWWs0OFFoQVpSemQxMW9TTExjMTZ4UjYxLVREcVg3VVBkUENnb2NSemo0TXZNMmc5SnJOMU0zX0syT0xqUl9WSVRNTHRhV0JWVGFHbHFlbVU0bllDSUQ5VFBZbHBM?oc=5) |
| 实时 | 11-02 16:06:49 | 11-02 16:09:55 | 3.11 | [Intel (INTC) Faces Further Downside as PC Slump and Rising Memory Costs Squeeze Margins - FXLeaders](https://news.google.com/rss/articles/CBMixwFBVV95cUxPeF9RNzhyVFJna3hKTXdWTmFEeFVkanhCZmdZMEVtVHUzM21JNEM0OUJxVjZuSEE4NERubjIzNURRLWVqaGp3aE5ER1djSGN2RjZmZnhNOHJ3VVF4VEVCbkxlamFvMGxKSU9sT3Bwck1OOURIcDd5djJPcXhUdEdaVHhvWUVKalBCeS1YdzIyQUpCVkxwRU5aZVFlWmdnTUdGa1hiQUlXek5pcUtzbWpFVTNfZzJ6SWN3V1p3QkYzYmUya1kzSWVJ?oc=5) |
| 实时 | 11-02 16:16:21 | 11-02 16:21:55 | 5.57 | [reBART Enables Resizable BAR On Haswell Z87 And B85 Boards - eTeknix](https://news.google.com/rss/articles/CBMibkFVX3lxTE81WW5ZTXhmXzRfSHRaNGNTM1dPWFRmSlhaMGJqOTRlQ2c5Qkd6aHEwSGxKV0hnSUVUeDN2ZWN0eGN2RTgzNFNHVlgtM2NSZU9yZFZGVE51OTlHYWUyaF9WNXJkY2NjY3J6Tl94Tlln?oc=5) |
| 实时 | 11-02 16:30:08 | 11-02 16:31:02 | 0.90 | [Rocket Lab USA (RKLB) Reiterated at Market Outperform by Citizens - Neutron Delay Not Thesis Changing - StreetInsider](https://news.google.com/rss/articles/CBMi-gFBVV95cUxOdHgxT1NBb3R4RVJqQ1lkNlhaNGZlSDJlV3FnZF9VRm1RVWszNUF4dkhSYVdiQUt5V3hLNkEtTHRGMnNXYUc2WjdYOWFOSXVmaF9DeE0wdngtVmM1Qk5pLWY0UFFTaEhWLTVPT3JhSkV1VWN3TjVUT05FVmdSRi1Wb1ZaTXhoNGp4Smw1T3o5bUpDWTc1bTdyXzQzRmptSGVwZkhKQmE5ZmhkSEVBT2lRUHJnWVFwY2dzZjNhNWNpbkFoaG5ObjJWbEFaRGM4RGpjc05JaUdScnpnbmpBY3JMLWwyNUF3ZHZqYlBJMHNoTlFRVWVfTkREZTFB?oc=5) |
| 实时 | 11-02 16:40:08 | 11-02 17:05:55 | 25.79 | [Apple And Palantir To Kick Off Tech Earnings In Another Crucial Week, AMD To Settle Chip Debate After Intel’s Warning - TradingView](https://news.google.com/rss/articles/CBMiiAJBVV95cUxNTmVOdnlCMGgtTzd5dUJVZnNkM3NwWVVtZlBvTlc0VXktZUVKd25xbnAzS2VhUjdLekt2UF94bU5IdE41eE1MOExLUjF2RG5RMXZiOHR3anNRTkZXV2FfbVRXYzZmTlAzcWpkald3Q1JldzdLQjdZaVhiUXNQdDlXY1hoUG1HbXpJNVVZMzFYbG4xaWM0VGxBLVRQV1gzOURBajEtNzFBV0hNSTE2akVrbmZKWGZ6Ny1LTUd3eE9ZdWJjbnR5VERYQVF4cFFELS1ZR090OUdoeEVVUDAzNUUwX1lOZE9TcUdjZlo0Slp5RUJBbnF2OUd6Z3ppbXE1ZUxWbzc1MmVHM0g?oc=5) |
| 实时 | 11-02 16:45:49 | 11-02 17:11:01 | 25.21 | [Radar Satellite That Sees Through Clouds and Night Set to Launch on Electron Rocket - Around Prague](https://news.google.com/rss/articles/CBMitwFBVV95cUxPejdNMkN3ZDAyTUROLTZsU2EwSWdORm0yUEpZYWVHRW92SjVDZHcza3dTUF9hTDBZUUwxNzlqTkV1MTF3X0xtMjFQRWJreHd5TUM2TldFRU5TTFgwc2c5M0x5dGNpUy1iV3JvMDhqd1VROEREUy1jNnd3SG9zX2hIeXB6VzBkeFpfQzdpLUp4c0J6ZGZ0Q0N1d3pWZXJ6ZEpoQjVEblhlOURkY3FyYTZEbjFjcDl0bnM?oc=5) |
| 实时 | 11-02 17:03:31 | 11-02 17:17:01 | 13.51 | [Electron Launches iQPS Radar Satellite Monday - KeepTrack](https://news.google.com/rss/articles/CBMiakFVX3lxTE8tYkJ6cC1mMEwxQ2R0UnB2by1fbGpSZzBLeGdxal9GcktCSUpNTVBnaGRHcUNNdXF1NzY2WVRfS09ickhWTzk1NlVHZ1VtUGV3RWF4MHF3VlpjQUh1aGJJeDJBTlFlUENGdEE?oc=5) |
| 实时 | 11-02 17:17:00 | 11-02 17:20:55 | 3.92 | [Intel Shares Plunge 28% as AI Demand for Memory Squeezes PC Market - AI Insider](https://news.google.com/rss/articles/CBMipgFBVV95cUxNaFBqV2IzRmRSem53QjhzRVdvZ21RMTROX2Fqa1dXZ2ViWFE1OWJIa2hTVk5jbHpjbU1nUnZvN2psNmZsYjJRQkdQTTd6Y0Q4M2NsMzA1Q2NHOFA0ckY1TGNacGZWN1ZXV1Zyb1QtOE94T005OVVKLTRWX3BlS0QwRUFjTjcwZFdtOVpNazB5VXE0cEE0VkZ4VmZNdXEtVkY2UlY5NldB?oc=5) |
| 实时 | 11-02 17:29:25 | 11-02 17:34:55 | 5.51 | [Russia attacks Ukraine with large Gerbera-2 drones: Intel reveals their specifications - RBC-Ukraine](https://news.google.com/rss/articles/CBMikwFBVV95cUxQaElncFhHR0djZHJkWFRhcnVGNTBPc0lac05TcE1aUjRfMVJSRjFCQ0lzSkhkV0NNZmkyOG9jMkk2N0I5UTRCckdUM3B2WFV6NTJ0M0xSVl9FZll6ZDRWZHJIU2NROU1iNWhXemVmMWhWTDNQbVdUM01nZXIxOUVxYW5VTjdoc0syaUowOUN1NTBvbG8?oc=5) |
| 实时 | 11-02 17:36:33 | 11-02 17:43:55 | 7.37 | [ENF Technology Secures Third Customer for Texas Plant After Samsung, Intel - www.thelec.net](https://news.google.com/rss/articles/CBMiZ0FVX3lxTE5FUnJlZUM4ellQVkstR0dBaFNFYTc2TUJZWnBCVWZ0UmYyazlvT3FjMEpRVlpPZ2l1ZE5uNTZJM3VwejhEUlZnR2dkajZjOS13Q24wWE5vaFc1dVdQVG1ma043QnN0dVE?oc=5) |
| 实时 | 11-02 17:37:03 | 11-02 17:54:55 | 17.87 | [Barclays Maintains Intel(INTC.US) With Hold Rating, Raises Target Price to $75 - Moomoo](https://news.google.com/rss/articles/CBMisgFBVV95cUxOOHYtQUF3VFZzN1FCTzROMC1DREI4Y2lNSzZwbkFJYmpLVkJJZ3hjYWx1MklBb25WcWxoREU1NkRvblgwVnhoakFobEZKQ1oxODIxbGRwb1QxbkcwbkVfRDlxVGRER0R3VEc5cld2amJzR2UxcDIzYlNWRTRkTVdHTHM1TzdjWHpEMjlMc2Z2VHJHM2hGWm9OVDBscFVPYkRYRW0tSlo2d1BZdTVURnJMV1J3?oc=5) |
| 实时 | 11-02 17:59:39 | 11-02 18:02:15 | 2.60 | [Ricky Gervais appeared to mock James Acaster over his Celebrity Traitors exit and it sent this magnificent Acaster routine viral all over again - The Poke](https://news.google.com/rss/articles/CBMiiwJBVV95cUxQa2JQTFp6bmpmaUFSeGR0bDljZl83QUY3QmNxNm5reXBYMHhSRFduYjFFYVNkaHUzdTJBRWxhNFlvUHJteFZnVkZwNDRkbmtocGNKZHdEb1pxQmZFVzBpd1d0YWlaU3ZYN3BtYWJzYzBnbGJUb2hGUlpUbUNjV2V3NUtFekZBbE9IaDFCdU8zRjdJMDFTUWNzMlhLcXZkeTFHZlhhbEoxUC16S0tsczF6S3dXS0ZGZkdPLU0zOGtYTkJKNjRDUHc0OXRMY2t6SU9qQzRDNVgxQWw3ZkN1WkhDc21OcXBhUjhsTzBuUVItWm56cTU5YU80Y3B4RVdSSnVKMS1JeTZ2MWl4M00?oc=5) |
| 实时 | 11-02 18:03:44 | 11-02 18:05:02 | 1.30 | [Rocket Lab backing new Alaska launch pads - Advanced Television](https://news.google.com/rss/articles/CBMikwFBVV95cUxNNU9yREJEUmk0YXZMa0RkRlZSQkNRdXlLWnM1Uy1yd0p1ekswaG1lcTRvTjFyVmpzWld0d0NNcXVtSEdJRHNoWVBLRGFxTmgyZnVsN2ExbkJJSHNkQW1PSldFVlJ0b0FRRF83RmF0VWZRTTk0VTdWVG5aY243UlhKRnhmdENQQ2tZRmFsU1M4LWNSd1U?oc=5) |
| 实时 | 11-02 18:00:00 | 11-02 18:05:55 | 5.92 | [Intel and ZKH launch AI to flag possible part mismatches before workers confirm delivery - Stock Titan](https://news.google.com/rss/articles/CBMivAFBVV95cUxNZU43aHZQanZLMGg5YUtXcmVyWEZWLXYyYXNDbUNudGtjTDJWeEd2LXBqRDhueGxMakdqN3V1dFBzcDE0Vzk4VDVYaWF1eGZ6M1ZXLUZaQkFJTGg2NWVydnJwbmYwOEoxSlJPcVlTMTBSRDZPSzF1YzFqdmNRZzROaHFxSDJLeC0xMWZ2dVhya3FVYVg2VzNZbFdUV0x4bEJkWmprb3VObEJsdGVscEQyUGVHZmpIcmtObmdQWg?oc=5) |
| 实时 | 11-02 18:01:37 | 11-02 18:09:06 | 7.48 | [Intel Z970 motherboards spotted shipping, providing cheaper alternative to Z990 for Nova Lake CPUs - Club386](https://news.google.com/rss/articles/CBMidkFVX3lxTE5QZEdXVU9TYkV0MHUxYjdkRnpxUjY4WktjT0wxR2o2SmkyaG1ydGIxLXFteElXTlp1YWQ4R29RSVFjR3RzbGplR05rMVE5dVRfZDh0Y0FQZURUZjZQWXdTNzBLMmdtTGVNWm5STDAzZ2s0Ny1waXc?oc=5) |
| 实时 | 11-02 17:52:00 | 11-02 18:10:35 | 18.59 | [Archer Materials Limited (ASX:AXE) Partners With Global Quantum Leader IonQ to Advance Sovereign Australian Quantum Computing - Kalkine](https://news.google.com/rss/articles/CBMi8wFBVV95cUxOLVp4Nzc1ZG5HX0gxMmR0UVM5TjVNWE9zOEVZOHhVY0pQblVVRl9kc3FCZDQtQllEby1PNm5hX3B1X0U2TG05aEJqTlJLQWxDbmJwUUNuYXRMbVFlSGNLa1hDX1djakJianNwdVRpQWtGbmp0R05IbXJsMnd2anNkSXd1ZEVRcVdYMFB0a3l3aEp2RVVRWGNQenRMRHUwOHA4N2pQR1N5UnY3TTBvQ1dZMy1HV1dNbVJCRFBMbG1VRlNITF80b1dfZko5RnZ6alVHSndvSnJMV2h3MDFXS0tKWmNVUmFaSkp2Vy04VDFhT1Zfa3M?oc=5) |
| 实时 | 11-02 18:00:00 | 11-02 18:11:55 | 11.92 | [Intel vs. Taiwan Semiconductor: The AI Boom Is Telling Two Very Different Stories - The Motley Fool](https://news.google.com/rss/articles/CBMixwFBVV95cUxOOENxSFBWSmJtaHBsUmdmZTRhNkV4LTFjRGZSVmZxa2kzS1JLYVBOZy04MVIzMEVQN20xTF9UdmxnNTBZaV9kSUQ0eEtsTTJLT2NRNFNpYnlFNElCbVpnUEs3ZjBWRFAwU3RoVTM0ckRvYmY3ZGZvMjBvN21UcjFwajU3TVluREh1elY4MFpadW9rMGJoLU4weE5qOHNUTUZ3THJuNHR1cEZJYktta25oQmxqdHZIY1MyN19YWHhsdEp2REw1WUhn?oc=5) |
| 实时 | 11-02 17:52:00 | 11-02 18:13:35 | 21.59 | [Quantum Cryptography Market Heads Toward US$ 15.28 Billion - openPR.com](https://news.google.com/rss/articles/CBMimAFBVV95cUxQN0NXc2k1dVN5dUNDb184RTEzNEFUZC04TklldEM2Y3dyMWJIcEhMeGJxSnpvM0ViT2hJcDNMYjB3OUJxMHduUlJnTjNFcnd6SWhoaUtGc2ZvenFNT3F1Wjk5UER0NDRWZ1BZMXFoYzJhUlpBVjRVc2FkS3BuZVFxSm9FWDRFMHY5TFFKb1oybHhwdlhUQll6dg?oc=5) |
| 实时 | 11-02 18:01:07 | 11-02 18:14:55 | 13.81 | [ZKH and Intel Bring Industrial Multimodal AI to the Edge with LingLong Insight 1.0 - www.marketscreener.com](https://news.google.com/rss/articles/CBMi0AFBVV95cUxOckFWS3VpVnpBZGxETTB5dEdiZGFJemM1SXFfQnNoem94bkJVeS1SNzNWQlVVaW4zRzJld0VVR0NyWEdFMHhXNkF5X3F2Q1dHUkZmYktkcXE0ZFNXT1gxNnM2U2FLbmppbjNnU3JmMVd4ZVc4cmdFaXJnT21CNDlRbHlzSGo4Nk9CQU9seWNwRHpRTU9uWTZmOXRQOVVqMmM1TmtSYUdGQ1YxYTB4VVl6c210UjVQdGFFTU5Dc0dEOTd5UUxIY05scmhMeUtlbDNu?oc=5) |
| 实时 | 11-02 18:10:00 | 11-02 18:16:01 | 6.03 | [You Can Do Better Than SpaceX: 2 Industrial Stocks to Buy Instead - The Motley Fool](https://news.google.com/rss/articles/CBMilwFBVV95cUxPeExqRkdTX1ExZ2RCMTBSLVJiY19BczRWaGo0VjczbWRNQzI0anN3RUNPYmlKT3h5TS1SM2RXVUtCZmtVX3FBU2t6YUJBN3ZOaHZzR3dmN1B1YTdHWmFxMjd1OXJtUnhJbE5YVXdnUUxHcGVtNlNqN2g1QnppUXNCa1Znb1RWUHBKOTBPTGNIVjRmZU9tUnNR?oc=5) |
| 实时 | 11-02 18:21:00 | 11-02 18:27:55 | 6.92 | [ENF Technology Kyle Texas Plant Lands 3rd Customer [2026] - tech-insider.org](https://news.google.com/rss/articles/CBMijgFBVV95cUxPTm5vbVFqM2FkZm5EU3lSSDk2YW56aGpjaDRxYjdXNUo2ZmJGbWMwbFR2cXJEYWU5RDVibVA3SzNidlVFaDF1SW4xRndlTXFMdDU2Mmgxemtvc2pzb1RtU3VfTWN3UEdlMGFTcTBvdjgwZWM5TmtVaWpzOUJVYlVvakN1ckJXMkR1OFNBb3BB?oc=5) |
| 实时 | 11-02 18:27:17 | 11-02 18:27:55 | 0.64 | [Clever Advertising snaps up Pickswise from Spotlight Sports Group - EGR North America](https://news.google.com/rss/articles/CBMiogFBVV95cUxNM3lNU3U4aEJpQm5vWkNUN1JER2Z3dU9MdUNGX0MyaE9UZjJHVFROQXIwYi1lZGU5RWtMejI3dVprdkNzQkVUdno5T203dl81V2RhNWxhNVRGcjNMa3g3bHdJS21VUnVPR05VZ3M0WXNXWGhLV0JrMlVaQ2I2YUdMV0lnTmpLR3lUSWtvWmNlS19icENVN1FYajJIZl8wa3J4VXc?oc=5) |
| 实时 | 11-02 18:31:26 | 11-02 18:32:55 | 1.49 | [GiG Software CFO departs with immediate effect - EGR North America](https://news.google.com/rss/articles/CBMiiAFBVV95cUxQeS1ndGgwSkw4SnNtVUFWbFNFcmtqczQ0QkIxb2JNUzh5S2dEVXlEbmxJUmF1VGVlTjV1OXJaOENRMFQzX0pubGhZMndwaGRESHZmRGFjb3pQMTNsVnBURXBlejRac3VnNmQ3bTJwTG9iR1ZUQzlfeDVTT2tJR1dNeEE0X2w2elRQ?oc=5) |
| 实时 | 11-02 18:32:41 | 11-02 18:41:55 | 9.24 | [ZKH and Intel Bring Industrial Multimodal AI to the Edge with LingLong Insight 1.0 - The Manila Times](https://news.google.com/rss/articles/CBMi6AFBVV95cUxPeXVweTBwdFpDajRLMWlsVHNGZld5bm1uNlk2dDg1aHFsSks2Yy1Qc3dYQkg4TzV1STZ2Z091NlgtMktVU2VpMXUzNm9kRy01ekpjNGw2M1JfTXR5QXVkZ1UxS0x6SmRmV1NEZnJPRHUzVkxIRFlZUDktYk1mR0RvU2pOUlQzRHlBR2hHQXRpTUxfdkhKWG5CQlcxdm1menhDMGstYThfVzdGVlR1d0lTU1dpc1o0U054MU9yd3d6Q25RX2pVWHlNOVRYSEVnc3IwanIxc2NhRjZCSGVsZkJpUG1WOWw2Z2dY0gHuAUFVX3lxTFB1djh4RzA2bmtRQzlyckJTRnh4bk11b09nbUJTNGJMM1dBMmNqaGx2d1Z0MS10eEtkQ3BOSzllMXFJTmxMeURYVTQ2akVaNUxUMHpvSVZjTmRYaG5TeElTSWVYWEd5MlcwWUJUOXhuWFlpdU5mQ2JJUGhQR3hyQjZvemowQTBiTF9UMVhWVmx5ZmZEUkJSZGVGRUp1WW5RXzhlaURteGcxWFVMS2pXNUp4aGR2R1hwbGNXTDJ5eHB6WVJ0NUxFMWU3TkFDYjUyN2JYbE1Fb1FWclJrUGJCdE5SbnZOU1M0eFdUcjNhdVE?oc=5) |
| 实时 | 11-02 18:45:29 | 11-02 18:46:55 | 1.44 | [Intel reports sharp reduction in advanced process costs - New Electronics](https://news.google.com/rss/articles/CBMiowFBVV95cUxQUkJCZnR3c0h3c3JSLUNwemZVRENhTS1qVEFwc0k2ZXo5UkNsaW1RbjJGMC03Y0FHbkZCQzNKcW9xTFA5a0tBZlo1VUZyeWI5Y3R0dHJQVnJlMFllTk5fbERZOGZRbHhKcl9IblM1REtTSWxEdUYxZHI1Y0dsdERBZ1dmb1VweVI1UjdkbGw3M1FhY0xBUWx6bzNFal9obmVyaEVF?oc=5) |
| 实时 | 11-02 18:42:11 | 11-02 18:49:55 | 7.74 | [Decomposing the Sell-Off: Google, Intel, Amazon, and Managing Options Exposure - Moomoo](https://news.google.com/rss/articles/CBMiuwFBVV95cUxPQVhGekFDbkpJQmFSdFJscVVjMERJZThROWlUOVFZaW5IVFJwWDFHemdXdlhVSzUzSjJnazRMUEV4amZETmRIVG5nLUxoX3RQYmtZc3IwX3dVU0t3cC1FZTZxY1NKUFFXdnJyMlFYWUlRT0tnaENYLXVQaXB5Zm9JRHNiZm1RblMwSXFHTUE1OC1sUVR6TGxZblAwNjNNVlpXbHpsSzBWOGtOSjJsS1RWenJLVE8tbDFtamJz?oc=5) |
| 实时 | 11-02 18:49:09 | 11-02 18:52:55 | 3.77 | [ASRock Z970 LiveMixer and Pro-A next-gen Intel motherboards spotted in shipping manifests - VideoCardz.com](https://news.google.com/rss/articles/CBMiuAFBVV95cUxOUXplSExoMUNGZlZFM3c5TjhaM1ZwZGt1Uzc3SW9yNlktUTV6T3pNLUItSlBPTWJuVk9JUFMzYjNCVFdNVkJ0NGNYNVFEaS04c3ljc0ZqX2gzcGRIQy1KZGtKZzdWb0EweUx4cmU1ME9VZ0c0MVc0YTgxRGFrWGtad1JJUnFueXVoNkhjX25uWXg3dW9CYmM0dmZwMThJSmgtZEZQcnU5cHdvZ2xYNE5vRjA0TEd3N01O?oc=5) |
| 实时 | 11-02 18:58:19 | 11-02 18:58:55 | 0.61 | [ZKH and Intel launch edge AI model for industrial supply verification - StreetInsider](https://news.google.com/rss/articles/CBMixwFBVV95cUxNZTVvNnRWUnBfd1hzRTlxX2RBWEE2bFA4RmRGbGVITDlrcVREOS1DOGo3WVRFZkVkQTNWMlZWTXhfVEJCZGFrQnlPTHVfb3kwbklqc2hkQnQzaTVRUDlxVm12b2tzMmFQeXQ3ZUJLZG1sZVVLWWstc0VYdVBoY2huT1U2Nkg1UHM3YVVLX3dRTWx2eWljX3dQdHZZWlNwNkJQalpBbWU4YnZqSVNpdlVlWlFHYU1ya0dlS2p3UFZmay0tTGc3WmxN?oc=5) |
| 实时 | 11-02 19:08:53 | 11-02 19:09:55 | 1.04 | [Intel, ZKH Launch AI Model to Verify Industrial Supplies - Moomoo](https://news.google.com/rss/articles/CBMioAFBVV95cUxQREFnVlpkaHUxWXgxY1o3SGdIbDFmNHRQQ1dTUnpXZFpmUWcxTVRYYmJCYWJsZFVpMDBsQklySUxOSmxfVm0tWDlHX3hZeE9vV3FRbmlrSlRWbzc2M3hmcHFxaFhfUHA1S1ZWcVFrQVdRNVlZTTdseTd6U2stQmNsa2NabGFWcWFSa1lMdVFUUEsxV2NONzhpZTBDVWJOSlZ1?oc=5) |
| 实时 | 11-02 19:06:48 | 11-02 19:09:55 | 3.12 | [Prediction markets bulletin: The key headlines from October - EGR North America](https://news.google.com/rss/articles/CBMimAFBVV95cUxNdTZwY0RNWjV1a2lqQkhSTXNDWHAtTUItZzlQMUNKM2U5ZXlrRlBocmtrNVFpLXpzUDltOUJzNDZrYXA2dWpMMjJ1U2tCN2cyazJPeFMxXzZMc0tGdmxocFpZdzhGWS1pQUs3eEh2cFJhaWxWdXdsa2o3SGxWcC1wY1EtSnp2QXJ1NEVpaTUxelNlcUZWUjd5Vg?oc=5) |
| 实时 | 11-02 19:06:31 | 11-02 19:11:41 | 5.18 | [AH Realty Trust Completes Dispositions and Financing Transactions, Advancing Transformation and Strengthening Balance Sheet - The Manila Times](https://news.google.com/rss/articles/CBMiogJBVV95cUxPbGdfcDdaeUE5QTFvMXBUV3p5R1Q1aHRvTXRVU2NidFpiQlZDTTlVWTBMNklGbzBPR1VibVV4VGhBb05KaGVtZmt2Yy05dnFoM210NzNOZnBIaTZWTWFpa1RPWnN2aE1kX19ILUJMWGhZd0NJbDhyaVJZeHJGVzYzZDh1MXZWeWFVZm9CbmtCeW5rUVlXbUVUTnZBZ1ROdTY0MFlhRVk1SGgtRHAwUG03U2ZQcENXWU8zWkxrczJCaHZXOVVINXk1LURPQUdnUFhMVXlHZzJLVm9GX2JTSElXWEJrUU5Lb2NPRWJaUUZraDZWcXpQRFItOTRIQllnVGhPX2RfUEpQMG10S05Qa3hWdmgwVVU3NmNLdjk3Ml9XQTlHUdIBpwJBVV95cUxPbU91WHlGc0lPYThtcnlYMWk2MUhhQVJrMjFBY1BhcE0wVDF4a1hVTjJSS2luU3NsVHVkaTAxbGtiZHNCOGJDQ29yQzVxX085aXNlczgxQnFPNlF3eUZvampCSGdFZi1XNFZpNW1rVWNHZHd4WkxWcGNGVU5WdjdFRWdSODdTUGpwOXgwWkZLRkotbm5ITllXYmZ5LXhFM0htTFVMV0M3RVRtS3JkV1EzaXZZRklPWkxDNng2WXEycFBzQ3hYeTAzenlxVXdjOVZtcGVhQjdXQTlsZURPMGJ3T3k3bTczNEJwUFlNZEY4N1UwOUVndFE4bHdoQW5oV191QTJRSEw5UzJnME1ubTVrQUIwQ29lTnVDOWpOT0gzZ0V4TngxV0M4?oc=5) |
| 实时 | 11-02 18:46:05 | 11-02 19:15:41 | 29.61 | [Illinois Nuclear Plant Upgrades: $5bn Federal Loan Targets Six Sites to Strengthen Grid - Construction Review](https://news.google.com/rss/articles/CBMiwAFBVV95cUxOaExzUERkcmFPMkZkWUZDbFhRaVdxUUxfejV4TG5BRFY1Ty1RMFpHSy01d2tSTl93eG5xbG5HUHNiOTVmZHQ0UHpvMktCMTBoV1l5TzQwRFd0WkJSMjhKMXlVX2tmMkc3Y21qNUlEdDU1UWxIejd4VjJTNDBrVjB4Zk5nYWtwT3hHb0pYQnRjNnVFdjR3bzJtcDdpdUVic3duVHB5Nm1ObmQ5Xy05Y2NEN3BXRTJUTElIX2V6MUZrNWo?oc=5) |
| 实时 | 11-02 18:57:04 | 11-02 19:20:55 | 23.85 | [Intel, ZKH Launch AI Model to Verify Industrial Supplies - www.marketscreener.com](https://news.google.com/rss/articles/CBMirAFBVV95cUxQdTVlcmhhcVprTHUyaGlfTkhCLXVLTTc5SDNYTGhjWGVlVTU2eV91QUVrYzBlZC1VQnB6NHdGejMzNFhZdnVHT1JwaFVETmtDeV9aZ2RfNVRiNV85QnJFM3ZQSUk5MGJDc282YVBoY1dUME1TS2gwX0pyU0pjSDdFaG8yendPQTV6ZE42aWpjRzZYTUtvUERwMk50SUFTOUNsWEdyQzh5S2M0aDVr?oc=5) |
| 实时 | 11-02 19:16:00 | 11-02 19:20:55 | 4.92 | [Apple, SpaceX, Intel, Palantir, and More Stocks That Explain Today's Market -- Barrons.com - Moomoo](https://news.google.com/rss/articles/CBMiqgFBVV95cUxQbXpCWmwyM0l0NUZQNjdNYlVPN0tpY3FONE1XNjZjOW0xSTFBcDlPTTFReVlmYmIzb2ZKMHNfbmNROEFjOFZMdHJVRHJYblFia3lXRDRRaXR4MXhGZXdrTmV2UGhTMHYxc0VUVHBkMmdtaTA0V1ZvVGU3RGRVVXNRcTBtOWZVd0ctd25zTFdOYWlONjZKbjNvdFplZWUwQmtqTmstdHh6cU1KUQ?oc=5) |
| 实时 | 11-02 19:03:50 | 11-02 19:27:41 | 23.86 | [AH Realty Trust completes property sales and debt restructuring - StreetInsider](https://news.google.com/rss/articles/CBMivwFBVV95cUxOMUFCQkFXSHY1RHdlQ0tOWWxYR2xodlk3ZDl1QlU4TjZpOFRBeEVicXpXbXFRRnJkTGFoTFl4TkNEYXRwZDBLNWwtbW9MV2RnRVZHbzBNSnY3c1JMdGt2V05ER3ctdEtNV0pET01uci0wNGY4TzhvU01NSy1DdVVpRFZGdTlyaFM1S2MyV25YWWUxSWVfT3pBUVBrem92OXNKa3c1VHJzaGM5M28xT00wZy1lT19KMnQ0QVdjNTFoYw?oc=5) |
| 实时 | 11-02 19:21:58 | 11-02 19:28:55 | 6.96 | [Here’s why Apple could be next stock to plummet after Google and Intel - Finbold](https://news.google.com/rss/articles/CBMilAFBVV95cUxOQTFmM1lKTlBCdlVJeUNIdjBpNlRpdWc4cVVva2hhbmc5enZYbFZNNVFGVUh4cTFFUk4tcTRtR2FINWlBVjFiajJMdm5JWW15dmhSY0tUTW9QblI3Ny1UME1vVEZMTElDcGNDNmwtOG4yV29TUm5oSWNGODB4eWhhRmpMekdpczdmMmIwSG0tMlZ4YWI4?oc=5) |
| 实时 | 11-02 19:30:19 | 11-02 19:31:55 | 1.61 | [Intel's Quant Rating plunges to 3.26 as weak Q4 outlook weighs on sentiment (INTC:NASDAQ) - Seeking Alpha](https://news.google.com/rss/articles/CBMisAFBVV95cUxOY0VSdVV0dGhwNEVmTHBDMmQ3T05xajI0OVNoRlFaLTRERlpJcWdVc05GNzU3ZEUzMDNsRnJZME9COGJfa2xxODdKTVc0TGRLWmMyTmRZeHJJUjViN000NHRrVkVtSDA4emdvTzhPczctV1ZSZ1dNR0xwbmpibFVHVjFodHBEVXJfVTRJQWozZk5sUGNLU1NaY3JsRlNNYW9CWktvMWY0RUlXN3VwU1YxWg?oc=5) |
| 实时 | 11-02 19:13:51 | 11-02 19:41:15 | 27.40 | [Time to change your tyres: rules in Luxembourg and the Greater Region explained - Luxembourg Times](https://news.google.com/rss/articles/CBMixwFBVV95cUxNOWlRNEJxaFhlZmF2TXU1S3lyb21TNkFuZGxZM0xWT2RucWNFdlhDLTJwNGFFUGhFTWRzMHhlZjNXdFR0YndmX0JWQU1OQ29sd2o0Mk9SR3drSmQyRERFVnNvdms0ZWZOXzlEU2tWTUFoemdZWThJWnhxVFB0LTJXU1pkcDEzUGI1LTlSeF9UbkFkVTJMMjBNa0R5RTRZRThFUFJpek1kMmo2b0dsYUFZbFY1QjF5MGlrLXp0R25wUTVLZDRtY3VR?oc=5) |
| 实时 | 11-02 19:49:33 | 11-02 19:50:55 | 1.38 | [Intel stock plunges 28.6%, its worst day in 52 years - varindia](https://news.google.com/rss/articles/CBMihwFBVV95cUxPLUFla3gwR3p2MkRGc0luWWR2aDVoaHJDWE1vd0wybFhtODNqRUV2MDQ3bGpMQ3d1aEpHbUlTN0hlMUdmY2JxdHIwckVrVUlxOEFDT0xWV21iQldSLVJXWkVMNUpBVFF1akRLOTRmMWZBRGtzOE5wZlphQTZHc2tWMzkzRDNNM2s?oc=5) |
| 实时 | 11-02 19:48:07 | 11-02 19:52:55 | 4.81 | [Morning Bid: Apple's healthier core - Miami Herald](https://news.google.com/rss/articles/CBMiggFBVV95cUxOdVpLUVBvQ2RRckdHRnltVGtHb05rUUk1anl4dGtLRFNSTU5qYkJpVTNfWnRON29OMUFEbWtfbmpWQzFKSjItZ3YyMkR1SjNSRXlSYmFYSHdadTRBY3Y0ajVzTkhVYzRZcHV3N294VHd5cWtvc2xqbGVmcmVYOFc1cUpB?oc=5) |
| 实时 | 11-02 19:55:13 | 11-02 19:58:35 | 3.37 | [Quantum Stocks IONQ, QBTS, RGTI Rebound After Friday’s Selloff Ahead of Earnings - TipRanks](https://news.google.com/rss/articles/CBMiqwFBVV95cUxOV0Fna3dGcElwZzBNM0p5RWZWVDg0bm9yNTl4bzRHRTJzdDJPb25VWEZsWFkwenMyOE1wQ0czQllTMWF4c3VINmVweTB2ckxja1dQQ1p2STB2UDdvS2FzQzUwQ292LVBaM25zUm5BaUEwMFR6ajdlMjdzVGhzblpLaFltMFU5VWp5SGk0cXROdnZnNm1HOXBIVkZEUEM4TEdrTXlsLUVBNGhhN1E?oc=5) |
| 实时 | 11-02 19:46:52 | 11-02 20:02:55 | 16.06 | [Morning Bid: Apple's healthier core - شبكة تواصل الإخبارية](https://news.google.com/rss/articles/CBMiU0FVX3lxTE15TUJOTmJNOUhocWttNHNHMjhzNGNBdnBPZlp2bC05UW15eGo2eWNjLVYxRWsxYXg2cDFkcjNiRE1CQ2M5d2dkbG9ZWU1FazNkQk80?oc=5) |
| 实时 | 11-02 20:00:00 | 11-02 20:05:55 | 5.93 | [MLB offseason buzz: Intel on Skubal, free agency, trade rumors - ESPN](https://news.google.com/rss/articles/CBMiogFBVV95cUxQSXZOYlYyTkFfcFJncWZ5UjZWUHdYdk1jTWVIVWtET1o5QVJycXFwZHk1VDk5T2d5cXlsYmpZNjNnT190ZkdVNzYyUEV2b3dPOFdTcGVnRDBPdHBvSml4V3VFTTBiS0dFenVlcWZ5WlJWeDhzWUNrVER0d04xbTV6eGJvSmhoc19BLU5QOV9RRUQ1czRvTXpyejhtLUlYc2kwT2c?oc=5) |
| 实时 | 11-02 20:04:43 | 11-02 20:10:48 | 6.09 | [Bernstein Adjusts Price Target on Bloom Energy to $306 From $282 - www.marketscreener.com](https://news.google.com/rss/articles/CBMitgFBVV95cUxQYXY4dWwzdTYyU280TThSMHJhWFE3Z0FQcmg1a1lldFl0NzBXY09jTlk1a1ptVDE4OVZURm5HNVdPQjhjdkFWdl9POXBvMldlblpoM2Rsc1I3OHFpTkVaYjNfNTJXc2RVTnZMc3NQMUN4THVaWEJPUzczMXJ2VTRhR05xRkRGNVVWR1BXYURMSHJ3X05sYjlEZ1k5ZDV3a1FrMVgzNE16ZU1mRzRMekExSnpUdm5fQQ?oc=5) |
| 实时 | 11-02 20:11:41 | 11-02 20:18:35 | 6.91 | [Stephens upgrades Oracle with funding questions resolved - TipRanks](https://news.google.com/rss/articles/CBMiqgFBVV95cUxOMVY1NU82UDR3RXlwdlRyMlVTcUhxSDhrUXBjQTlHc2psQUZHS01sSUpRV08xX2RUa2xnUjhIZDl2WGVKYzJJYXhPRFJkMzlBR0xIOFJZWDZ3WWZwcmZOVE9peWhXcE1tWnFLNG1sRFgzY2FmLTdUZjl4SFdEeWMzQkFLMFo4YkF3R1FkbWQ0dDJ5Y1FVRjUtVTVOaHhoTXZxUlp0d3pTdkJkZw?oc=5) |
| 实时 | 11-02 20:12:00 | 11-02 20:18:35 | 6.59 | [IONQ Q3 Earnings Preview – Options Traders Brace for an 11.38% Move - TipRanks](https://news.google.com/rss/articles/CBMimgFBVV95cUxQNUhtVHZUcTFSb1BLTnZtbmtQM20ydWZHbklHMC1abVh5Y212N0dIcE1mSHNTMEJoRjJHM2hlbVRVcFlSQlZrY09qbUQyTU0ybE51NUpoN0JrcGxYYkEybEdsTHdIdEZWM0JEckdocDQ2ZWlIMnBhVEZ4Z3pzbTFOY2MwOGxSSDNFUVN4dkxWU1Z3NmRYSDNJaTF3?oc=5) |
| 实时 | 11-02 20:12:00 | 11-02 20:19:55 | 7.92 | [Why are US stock futures rising today? Dow Jones, S&P… - inkl](https://news.google.com/rss/articles/CBMiiwJBVV95cUxOTGJtcEFpcE9DOG9XX2VSSHg0V1RpQ0dYS1JkZ3J6NXBMdXk3bVB4by11YmpZNUJYQnB3VXRJdllFdnZDQ0w2SWxtUDJPZUFXbFpkVGNPTmZsdE0xSVdlNnZCUnJEY0hBc2hGa0hTWmFpNWI2TXZPcnJVSVBWSzIyS3NQWjdvN3RMRGltaTBwRTBOUVkwRVRWdzRJeHJBM1EyWXpyQ3JuZWdnV0twbTlyZUw1eEVMb20tcGZseXpmZWdKOTI3TXpnbUl5QkhyclVMNnk3aVl5SGU2T0hHWmdLc1JlY0VPdDdRcnRRd3BwSlV1LUlTMURncktGYm84MjZiS3YyclBPVTRGQXM?oc=5) |
| 实时 | 11-02 20:19:14 | 11-02 20:22:55 | 3.69 | [This week’s new spirits launches – 2-6 November 2026 - Global Drinks Intel](https://news.google.com/rss/articles/CBMiiwFBVV95cUxNYnF4Q1lrVUl0NXVsbERJQ3hGMkhCRjJ4ZGljNkd3MGZzSE4yajM1R3FQNHJsdDN2dDFsZkZ2eXRSNkhWQkZMRk1XVlRlZzZVa1ZvV2dwdkRIa0ZFSXEtZlRDajdHcC1NZjBFdVVMSGUzZDR2UGZ3RHJUN0ZCOVpUOEwtU0RHcHNiTHkw?oc=5) |
| 实时 | 11-02 20:21:56 | 11-02 20:24:28 | 2.54 | [Director Boynton John Wilson Iv sold $57,632 worth of Class A... - Quantisnow](https://news.google.com/rss/articles/CBMinAFBVV95cUxQTzgxZnBLd3liRHRtcXpKN0dTTF92SVVENVlwNjh0TC1pQUpCSTdWYUFXell0bDIwU0h4ak13M3NlZHZIWUU2SkdTdFk0Y1VTRVFFZnJJc2ItYjBfd1VQck1nWmhEZ2hWU04xUmphdVo1OVlXTTBwT3RUVkI2Ym9JQ3VlZ1ZGUjlHSUFzVjNoM0lvMXo1NnhSNndXUW4?oc=5) |
| 实时 | 11-02 20:24:09 | 11-02 20:25:55 | 1.77 | [Lenovo launches Xiaoxin Pro 27 all-in-one PC with Intel Wildcat Lake and QHD 120Hz display - VideoCardz.com](https://news.google.com/rss/articles/CBMiugFBVV95cUxPUi1rTmpMM29GSUk1R1VSWnczaURLU1VsWTFJZnRJWEpEcjd5am1ydGZ2MUNTSUhHcHVJdUtpbmZ4YlAtTWM4Y3gtZUZud1dwbmlPdEppa3Q5aFNlTm9xMk5PZG9HYURpdlhJR0FxdzdFTk9kRWxkanhoZDRtYjlPYUs1RkN0UUt6cm1Yakp1VElIbnpRa0NEdWdDUEZoLWxkN2VQNUNMdmcxUXZJNmktTGRPTDBQZVVlOGc?oc=5) |
| 实时 | 11-02 20:27:04 | 11-02 20:30:35 | 3.52 | [Tripadvisor Completes $700 Million Sale of TheFork Business - TipRanks](https://news.google.com/rss/articles/CBMirwFBVV95cUxPMHFtT0UtWTM4Mllxb2gwV2R2bmlyaG1PeXc0bDlYRkhHazFreE1EV0hBREFldnF6aE1wcGtDT1JyRUctSncyMkpCNGxTM0dob3BrM0pvaWdYbXk3QkI1a3RVbnJkX3hMLTMxNWVaQUVjSjBXeFhBNlpDZFdBWWxEay00bTFZLVpDSGI2MW5TUVJuWVZWRUVyd1doMVpibC1MQ0lzYWR1Tk4xN0NhaGhR?oc=5) |
| 实时 | 11-02 20:30:18 | 11-02 20:35:55 | 5.62 | [The Call Intel's Fab 28 Shutdown - The Hillsboro Herald](https://news.google.com/rss/articles/CBMingFBVV95cUxPRWlLSW5CZ0R1cjZjX2FIMUlIYjRHbXo1N2F2UkgwdnNCaDBTaVhBQXZGMFNyZGZEd3cyelJLYWVDS1JGSzhJWExsbE9wbG96VzNIN1NfekRjV0xkZy1GY0dCZDNYTzg1bklWcG1jaE80MnU3YzZkUW9EMk5BTFZiMnpyVXlmc09mNUc3blRVV05WWDBkTVFMM2swd3dOZw?oc=5) |
| 实时 | 11-02 20:25:31 | 11-02 20:37:35 | 12.07 | [Cipher Mining reports Q3 EPS (22c), consensus (16c) - TipRanks](https://news.google.com/rss/articles/CBMinAFBVV95cUxNV3RmTHd5WlBwbnRoczJVWFZMTFNWOTZiODMtVWMxVEd6UXBaRjB1T1VNaGtfVURrQ0o5YlFsVjRrWEN2MWlHd0ZSUU9Rb2tHd2c2bWpRVm1Sb2Q1Ti0wd3lOUlc4LXE0T3Y1U01TV1A0ZF81cDRwRjA2Y0tJNGJ0cmtOd1g0b1BybEVsT2NSVlNVYmxudC1Sc3h0TFk?oc=5) |
| 实时 | 11-02 20:15:31 | 11-02 20:38:35 | 23.07 | [Stonepeak completes acquisition of 65% stake in Castrol from BP - TipRanks](https://news.google.com/rss/articles/CBMisgFBVV95cUxQRGxZMUQzTVhHU0Z3VFAzM0ZnQjUzXzVwOFI3NFdKRl9YMFgtOC1UekVrLUJMcjRzNEdUbFNFZEFWZ0N0UVpscUxOTWdjTGt0Q1ZXbXREUk8tSHlIX282Z25keUFNVjhYMWYwTngtTm5kd1VRamdNcF9kUkswbGR3MDV1N0VzcVFSeHVEWWJDSUp4M0Rzc3JHTUhyUHdleU5MT04zVHBHVmc4VHZPWEc0RlRn?oc=5) |
| 实时 | 11-02 20:21:08 | 11-02 20:38:35 | 17.46 | [Karyopharm announces planned merger with Ayrmid - TipRanks](https://news.google.com/rss/articles/CBMingFBVV95cUxOMS1zU1VzNzdzWDl4eGdGWWI0LVVJai1xQ0E4dTlGQklsSmcwQ0dMWDdrTjRmMXBWT2plUW5wRHUtczR5VTRSRzNjNlNfdDVmVEtpRUpyLTdGYkowV2xaQWhNWWFvNTdnU2prSFhKQnZDdWJDdU9oUkRKQ0dqN1VFMzFlZkRuWnVoeUtMWkNMaVlpenZDSHdqcTNWXzYxZw?oc=5) |
| 实时 | 11-02 20:41:25 | 11-02 20:46:55 | 5.51 | [Asrock Z970 Motherboards Spotted For Intel Nova Lake - eTeknix](https://news.google.com/rss/articles/CBMic0FVX3lxTE80TmIwVzJ5ZVF5S1p3eVUwWVdtLVlMQlNhak5YVmxwNjY3LXJhYkNpd0RrTnRPSkpOVFZyLVFla2xBdnBBN1FuUFR0TUpWWEVyRGtlRTRMd0JrMHB4ZE5VQmNiSFJ2TG8tMWVBNDFSMFdiaDA?oc=5) |
| 实时 | 11-02 20:38:00 | 11-02 20:46:55 | 8.92 | [Intel Stock Cratered 28%, Opening a Massive Buying Opportunity for Investors Looking for a Stock That Could Nearly Double - The Motley Fool](https://news.google.com/rss/articles/CBMi9AFBVV95cUxQVmJUMC1NUEloTW5yUndLdHY1T1FaVDFyaF9OSGpmTDRNdDJROFJXM2lqT2ZMYVAydk9PT3RiMG9QNENuZWRFUFJLTXJUdUxUcjZSU3V2aXhCdXUyTGlnX09GSFgwLXdtX3VZMHBMVjVoQURRTGgtWnlXQUlFdElnWGFQSV9mWlVhXy1aVDlFNDEzeWFxRHJ6V2dLUlBYaXBpMnJjMnkyVTNjLUVYZEtoUDFtWUhWZXF5TmFSUnVKdl81d2l4STI4bEpVNzl5UmRidlF0T3NoMklucmZKd1Fyd1VmTG1SVDh6dEo1RDIxVEZha2ps?oc=5) |
| 实时 | 11-02 20:47:00 | 11-02 20:49:01 | 2.03 | [The Space Stock With a Much Bigger Opportunity Than Launches - 24/7 Wall St.](https://news.google.com/rss/articles/CBMi1gFBVV95cUxPZGFSLTA0RXFPOHE2LUtyYzZ2YXhTRC1uSWhmZG15bm5Xd2U5WlpwbHJIa1RnWXl3dlVDMFN6NlFycmFYc1RYaUdQcW9SSWJmRXdvdmo4b2tPalJEVl9EMGs1RGttdlVUcUJGQV85cnk1ZUc2Tkc5VHd2MUcyZ3B4ODg5SDJoaGdPRDJUZ2xXNEE5YlJoZ2ViaHhQQXFtcWxyV1ctTW9Rb2xnT1RhXy1iUldTWS1FSy1CcHVWZk1Od1d3WGp1dWsweHVQcmtaTjBUblJrek5R?oc=5) |
| 实时 | 11-02 20:47:00 | 11-02 20:51:01 | 4.03 | [The Space Stock With a Much Bigger Opportunity Than Launches - 24/7 Wall St.](https://news.google.com/rss/articles/CBMipwFBVV95cUxQeUxEck1hOEZDQ3NxN2ZwcnVkZHV3SVJOdXVoY1lEeDlycXJDOUhDOGV0WC1VbWczV2ppdjZicEhwTWhMMndLQXhjdEVPTFgxTnh3M2M1ampSYmlPUks5Qjk2N1hITXF4NmJHY1lzVzc0emFqeVNPajd6cTNhUTlhdFIzaW13TXdxYTNvMHJ2LUROdEkwejhVTHNlQ1RyWHZuWm5nMUMybw?oc=5) |
| 实时 | 11-02 20:59:54 | 11-02 21:02:35 | 2.69 | [Why Inovio Pharmaceuticals (INO) Stock Is Skyrocketing Over 200% Today — and How Much Upside Wall Street Sees From Here - TipRanks](https://news.google.com/rss/articles/CBMi3AFBVV95cUxQem9rSjVQQ0pCRVQ5U1FrMnY0alY4OHRYWXRPeXNGai1kLUhJWm5BVDMwLWZUc1pmTjMzX1BhV291R1Vxblh2Y28yRThVSEZUaEdtZ00yYmhhbmRUbGcwcklldTdTS3hEVUdNejZWakdhcllmN3BuQUxCV1hON1VBUDhNalNmUmhydEQ4UGRMd1pUbXB1aUd5OWNKeFZfa0stSndhNkFab0NuS0tGLVEySkxDOXFJS1FMSTE5b2J4VHR5Q0kyZlRRMHBObjhhaG9IRkpoS21pdno2VUgx?oc=5) |
| 实时 | 11-02 21:00:11 | 11-02 21:05:35 | 5.40 | [Quantum Computing News: IBM, IONQ, QBTS Push Congress for More Funding - TipRanks](https://news.google.com/rss/articles/CBMingFBVV95cUxNZlhwdHV0LTZtcHdCb1pzenRBMkRDNTJVNE1ULVBMd3pYanUwZUttdkhQZnMyVThkOEpjbk92aUM1VmZYYnZRVTVUaFV2YTVNMi0zczMtdkpSTjZ5Y0FYZHNidXhEakZtWnVRSTV5RGNFdFNpX1R5Y0ctTHQ3cWtZM1d5b3RIbFUxSnVGblVoWkpOYlZFY3VsakpTc2M2dw?oc=5) |
| 实时 | 11-02 20:58:00 | 11-02 21:05:35 | 7.59 | [3 Hot Stocks with Recent Insider Buying - TipRanks](https://news.google.com/rss/articles/CBMigwFBVV95cUxNaU5YSzA0MDJ5eG9rMXg5RlBNZS16enFybWlCTGZzNEVLdVVjcFM0WFB0YmVCZTYwY0lDVE1uYUw2cUxYNGJPUW9iVy1ZWmp2SWd3LXVkdVF4Z0dKWWI3NVN1ajRHdnEzQU52ZFAtSzJWanoxMHdMa0VsUkJHR25ZZlRYMA?oc=5) |
| 实时 | 11-02 20:59:00 | 11-02 21:05:35 | 6.59 | [3 AI Tech Stocks to Buy in November with 70%+ Upside, According to Analysts - TipRanks](https://news.google.com/rss/articles/CBMirwFBVV95cUxPRllhQk9jVlBOel9aOHdmSmxHUGF3QUhMZWxGbFI5WWZEeWdwVFNXVE8tbE1zVThqdEdrNDFLZ3Zyd3NpZFAzUERveWhxb0FPWEFycHQxOFlxaFlrdTZXY3ZWQkdfNU15SGdJU0Fya0tjR3JfTmJkVGZBS1ljUGZYZEFSc2JDWGZEc0FLelROUWw5b01DLVp3cjFqV2pMWHVhclA5eUliVzlSQXBnMHN3?oc=5) |
| 实时 | 11-02 21:05:06 | 11-02 21:05:35 | 0.49 | [Tower Semiconductor Reports Record Third-Quarter 2026 Results - TipRanks](https://news.google.com/rss/articles/CBMiswFBVV95cUxOeG54Z2Z1UFNSM210X1NlbUZGU0tINkFEWTZ2Smp2WExPb0ZIVzlrLUZia1BZZEFRVWdndXFHcXN2amhaQVJlZ1lSc25PVlBsbzJXeE92OGJnWDVUcnRzWDZUblBpM01ESmNvcVhKcGVpdU5La2hfbHpJemJiNXpOVVdyMDFpeGJEYUlwZm1hLXVXMU9xT3p5Tk1uZTNqaFphZzd3UUY3VVY3Mi1zT01GUENJbw?oc=5) |
| 实时 | 11-02 21:03:05 | 11-02 21:05:55 | 2.84 | [Intel in focus as GF lowers price target, says foundry catalyst still intact (INTC:NASDAQ) - Seeking Alpha](https://news.google.com/rss/articles/CBMiswFBVV95cUxPWjRGOXY3RzQ5NWs5M2ZEQW9PTzl1cFZSTEY5clFjOE1tMm9kaXQ0TWwtYVA2dmJsdXZwTDM1aGRfSlJ0SmU3VDJzVDd5ZGdLMG44VjVfd3JjcEEtbjhBalJWVDBfVTJSNUxjSUpEeTdDNkcyX2d0MlhNX3VpOTRmYkgyMVNGZmdDa3p4YUsyTmVYRzYtNlRUN1Z3T1MxWnpmVThlUGd6OF9JOHR4bE9iQ1RhUQ?oc=5) |
| 实时 | 11-02 21:02:34 | 11-02 21:08:55 | 6.36 | [Intel "Larrabee" GPU System Surfaces with Working Drivers - TechPowerUp](https://news.google.com/rss/articles/CBMilAFBVV95cUxQampTUzFzWXdVTWo2QWRwWnd1d0JIMVZrdUNTci1wc3lhcW1ROU0tX1p3WTd5dWpHZWhxN0VwbU1mNHFzcmdtWndab0R0dVFBb3hfZ3ZtdVZjcXZWYTJIRTVKbnZJeDVOeVNfaDdhUDh2aW05S2Z4WlBnd21JVjRLUVFQd0lfaUJsaWhXb1ZZVmhKY1dV0gGaAUFVX3lxTE1maWxvZW9GNFdfQUgxa1VxV3VqcGdtVXFsOENORzM2NE94cktvWnFxbFpQT09iZmdzZ2xWdXdNNC1ObEJlOXZiZy1WWUNmNTFKQndNYm1GSFhvbW96Tm9CdzRGQzFlckh1RUtwV2FhY2xvYW0xSWMtdmcwRlQyeWhpZkdUdTNkTHE2X0RjZFpScDZISUY2NnJJYnc?oc=5) |
| 实时 | 11-02 21:08:57 | 11-02 21:13:55 | 4.98 | [What Went Wrong With Intel Stock? - Forbes](https://news.google.com/rss/articles/CBMilgFBVV95cUxQR3JGY0p4NXdwaVF1b0dvQ3VzMkFsSWZTY0VFQzRMRlFRbFhHbjM2Tlh0enhoTzJsZUxpS0xVTkNGd0l2eWx6WGZIcElaNlRmOGJ2VXRPN1ZuZzdsMzZuTUhWY2ppTmRPOVZCN3ZWOVVNczN6YkVGcVVUdDMwaVoyajlXXy01Xy1tNmstMXQwN1ZMOG5CX2c?oc=5) |
| 实时 | 11-02 20:46:39 | 11-02 21:15:55 | 29.27 | [Intel: CPU Story Collapses Before Memory Bubble (NASDAQ:INTC) - Seeking Alpha](https://news.google.com/rss/articles/CBMikAFBVV95cUxPRmlBSGZOV0RSbE1Fb2tZLWlNa0s4TTRaLXpab1pzbVA1RDVTYl93V0h4Q1U4aUtpaWpraWNxYmdnV3luSmowcmstR2lLeS00TlBJSzJXWUVsMmNzcDdFRl9FTG42SzZqbTNXYnhuRHhLQnlLajBIRzZVT3g3SmF1SjdWbERnUUtsUzRFX0xmTzI?oc=5) |
| 实时 | 11-02 21:11:48 | 11-02 21:25:01 | 13.23 | [Rocket Lab founder Sir Peter Beck takes flight with Jetson ONE - Vertical Mag](https://news.google.com/rss/articles/CBMipAFBVV95cUxPX0FyYVdMaVZHN1llRFc2azZ6NmpoSU9SZFNXZFJKeUtkTzRPYW0wUlFvbVdBVGdxd3I0YWZXNXg1UVQyODUwZnM0VlNFZjd1bnFaMGtzZ3JSVnpyMklsdTJUd1haZGhGT1NlZFdUaVNhcWhHaWs0cHZnVTA5ZTNHSnI5cS1zclo2am53Ukk3b0hxTC1UcTJGWlNoVVlsN3JKNUhnVQ?oc=5) |
| 实时 | 11-02 21:25:32 | 11-02 21:27:55 | 2.39 | [Intel Logs Worst Daily Drop Since 1970s: Buy the Dip With ETFs? - Zacks Investment Research](https://news.google.com/rss/articles/CBMipAFBVV95cUxQanRWY2hJV3ZUTk1GRWk1QTNnS19EdDRlVVVNTjZBZ1hWeUJ2YnY4UGhnMWV3ajdZMExNbC05d0piQkQ0OEpQREZzR3VaWVRfS1g0ZDlUMnVCeHRqMkRzR0EwMEswZkVxX0pDN0RVcUJKUjVxNDFCRmN3U09Cd2k0M3E0THlXU0ExcFZESjRYYjVuMTJJV1ZYeWZvTGVtS0tzMTdISA?oc=5) |
| 实时 | 11-02 21:08:36 | 11-02 21:31:15 | 22.66 | [Made in New Jersey: Papa’s Tomato Pies keeps a 114-year-old family tradition alive - News12 \\| New Jersey](https://news.google.com/rss/articles/CBMirgFBVV95cUxQMTVnUUhkZ2t4UFlZb3hrdUtVLWYxU3ZHTUUxNkY5SncyVklzcV82aGE0M2pRanJJREZLTHduMzM5bWNJWU1mVTVoZEg2dFFzenRFTkF6RXN5Q0FoOFBQV0V4VTZQU1NhYm9RSG1lWXpqZ2FrTFBFYXdJSUN3d1NGUUx0TFA4ZnF4T1VNVlN0OTNZaUY3Z1ZYYmxBeG9CeGlYeW1Kd2VrT1JUa2lkSGc?oc=5) |
| 实时 | 11-02 21:20:14 | 11-02 21:36:35 | 16.35 | [Futures Rise as Investors Focus on Midterm Elections and Key Earnings; Oil Prices Fall - TipRanks](https://news.google.com/rss/articles/CBMitgFBVV95cUxPcDRVaXkxLTB6LTZXd2VZOGxIWFZZY3JRLVZqSDNwb1Y1MHBRdDI5V05DZXRxeVhVY0RTTVZLUVpiTldGOXVFU1ZYeTJaYTQ2UXVpSFRReDFaOWMtUkd1eDNEMjBWNDV3cWVmQV9qalgwMFhyajNhXzdyV0E5UU1abTZQcmdENXM1M2tLNU5XYXRlMk41VmJCTzQ5TWFQcURsWmlPaHRmak96ZWQwekt2dDBvX3JkQQ?oc=5) |
| 实时 | 11-02 21:30:00 | 11-02 21:40:55 | 10.92 | [ASML Fell 6% in Intel’s Chip Rout. Its CEO Says Supply “Will Not Fully Meet the Demand” - TIKR.com](https://news.google.com/rss/articles/CBMirgFBVV95cUxNVGZZRVN0WEduVXE2UHUxYm1EMEh0dmhxbWZYRXhMWVludnlqWE11anB3cVBtMWQtbkhFdDBVTkxnNnZzVS1la0FabGhSUmZvVzRJTVMyUF9qZVpMQmVvUjhqZWU2OXAtWVowSkt0cG1LOUkzdW9la3hLWlBwcE9DUmpnSUtiOGtpLU5sMzY4OEFMSW1VeTJCeXRzYTdyeVNHOW9DdldHNUpiX1J3bmc?oc=5) |
| 实时 | 11-02 21:38:25 | 11-02 21:40:55 | 2.51 | [INTC Stock Rebounds to $67.34 in Premarket After Intel’s Weak Q4 Outlook - FXLeaders](https://news.google.com/rss/articles/CBMiswFBVV95cUxNLTItWEt6T1Fra0U5QXo0NTdkdXBzODlhenpvVE9iOWtSQW1ZN3RtQ3Z6RC1zRXJIRmQxQktKZ296RmxiLW9EV2VVSW8wWm1BVk5qNEVvVXM5U05LVjJIVjJJaUF0ZkhUczM3d0JzOWs2RU9nRndTdVRJc2h4QVRnOXdZeGliRmViZmRJSjY5TW1fSHp5bkhPM081QVFPNmdheWJaNUlrZWF2SzZJZ25zeVg0MA?oc=5) |
| 实时 | 11-02 21:45:12 | 11-02 21:50:21 | 5.16 | [Northland Securities Upgrades Coherent to Outperform, $280 Price Target - marketscreener.com](https://news.google.com/rss/articles/CBMivwFBVV95cUxQZ1k3dGgwMVA3SDRhOG1BSExmSlNZb2RENXhXYTkwTUFWM3lPY291a2NweEFvd19ubTdIVkd6QllvM2NYT1JoRVMwX2VMVVZmOS1pSmVQbldtNHJDVloycDhCMkNpOVFqM0NsTjZCTXhoY09VMGlPS3BSMHMyUUM3NTdUQVU2VnA4aFNuVmlNOU1DeGhIRVdMVlllZGhnYUFBWFlMUDczUGdWekQwMkNPb3ZxektpSWRWajNVVDRNRQ?oc=5) |
| 实时 | 11-02 21:50:06 | 11-02 21:54:15 | 4.16 | [UPDATE: Joseph City Schools on 2-hour delay - White Mountain Independent](https://news.google.com/rss/articles/CBMixwFBVV95cUxOY2NIUXBLMXQyRmlFOHVmeDJpaVVDSy1mZENFaFB2ZVZIWUFjY1hfMUltRWoyWDlSbm5JbHRUVVpFd1FvcFN4aFFoMFhQLTEwR2NuNEM4ejJJSl9leWplQmVZd2VlcnV4OW9xdVRRRTdzRTQ3aUxza1FWOHAtU2lLaFlUbzdsZGhqMnFGdzBhSmxGM0pvakZ1VngzZVUybXVWQkNfZWZRbW1uUHJtVEhpR05QOWJyMXM3Q3l5MEVuT0Iza01JY0g4?oc=5) |
| 实时 | 11-02 21:49:49 | 11-02 21:54:55 | 5.11 | [Lenovo Xiaoxin Pro 27 unveiled: an all-in-one with an Intel Core 5 processor and a 13 mm-thick profile - mobidevices.com](https://news.google.com/rss/articles/CBMiXEFVX3lxTFBJWUdpMDdDdGUwci1JeTBOdGR4OTNCb2hTbHZSWjIzVHIwcm1RTzVvUElfbUItc2NNRElMX0NncTM5aG9nbWZqaHlyd05oOVhxQm5fdFQyeWVfMDZm?oc=5) |
| 实时 | 11-02 21:50:01 | 11-02 21:56:22 | 6.35 | [JPMorgan Adjusts Coherent Price Target to $385 From $465 - www.marketscreener.com](https://news.google.com/rss/articles/CBMiqwFBVV95cUxOSEJ5VG81N1hqa1BzUFlzV1NNa0t5bXlldUlJOHJ3b1JVMGtTY3VKUklYVndDQjNoU0JiZnVxRFVkQXpyc3MtbGJqNTYyUU1kSy1WREEwbkROYWIzTlYzV3o3NHVOX3I1Q3RDYkRjQ2V1R0tLWktmTklHMXd5T2ZqTk5Rb0VNQjhIc19mRnQxT1FmTkE4bnZidWRfcnFXRUFZMHlGRjJiYm5zQVU?oc=5) |
| 实时 | 11-02 21:55:00 | 11-02 22:00:15 | 5.26 | [TalentNeuron Adds Monitoring to Strategic Workforce Planning; Brings Labor Market Intelligence to Snowflake Marketplace - PR Newswire](https://news.google.com/rss/articles/CBMihAJBVV95cUxQcksxREZ6QUljcGhscUpLWlFGbFg0bW1oWkVERVNBLVFtU0Jaa3hrVGdKQWJBdmltQUo1a0F5M3BNT1FEOXlyel8wX3NYQzJXdjB5eC1wSENSdzQ1MmZWa2ctTFFfbEh2UVV5WGpCbk9XdU1mdEI2TzhJRWEycXI2M2VFdmdZY1JCMVVUSU1yX2RHVElRa2JBTGVQY0ItSFBYcmM0WjFHZkN0RTdiRmhrWGRBdjI4ZVVHNTQxSkR3TlBGZnpHY20wRVo1YmktcVRJel9UdVZHODhKbmtFZUx0Y3BCWVRHa253ODVTNnVSYW1jVzdkVmk0dlNyS1k0Vk9TMWpYNg?oc=5) |
| 实时 | 11-02 22:00:00 | 11-02 22:05:15 | 5.26 | [Snowflake to Announce Financial Results for the Third Quarter of Fiscal 2027 on December 2, 2026 - Business Wire](https://news.google.com/rss/articles/CBMi5wFBVV95cUxNend6ajNad1RSazhXVG93TmV2RFoxSThFcHNvaUpndi04MDdub01HSmN2X29tWWN6b3ZUWlNaLThXWWV6RHhrX0tWMkF5SW5UeXVZV0l2dncySE5wRnZkNkZoUjhjdzktdkw2a3BWV1dGWXA5eUJCMzl4WFpYdGlmLW1PblVDaXBHTHRabUFYZmhGdDdIaWo5UUhRS2dpZVJkWGEwVk5xdEtiSmhVLU5CY2w1TlQ2WWI4TXdldHA3OTZMdzg5OTZFY3hIcUZzSWswZzhsZjRfN29FZXduZXo1T1hGb1JqNDA?oc=5) |
| 实时 | 11-02 21:54:00 | 11-02 22:05:55 | 11.92 | [Zotac quietly makes its new fanless Wildcat Lake mini PCs official - Notebookcheck](https://news.google.com/rss/articles/CBMisAFBVV95cUxQNE1qdW01dXh6eVM3MFgxUUlfazFFdmQtYy01RlFTbDZFbFhGaDI3bVlRaWZ5UnlXamNaSFh1c1VXQWdnUmpubG45NURaS2NKTHVsb2JLVGs0dVJsaXZMVHRhNEs0Umt4NURnUHJLTWYtS3dqSlZVcEthX2dwcHJ2czlOT1ViTGRfcjI0RWFUUFR1bkhGNWNBVG9oRkVtR3lwRjVUVTVqbUlWQ09KRjB6Zg?oc=5) |
| 实时 | 11-02 22:03:45 | 11-02 22:10:55 | 7.17 | [Dell Fell Nearly 11% on Intel’s PC and Memory Warning. PCs Earned Just 19% of Its Segment Profit Last Quarter - TIKR.com](https://news.google.com/rss/articles/CBMizAFBVV95cUxNdnVTbnNxR1RqUGY5dEFyb01RaHlLczhKeElKYlRhaFpndnFIVmc3VkktWFN1ZHRrWFZZdGcwMjJQcE1hVTN0QkVsdnRLVDcxXzR3WXlMMFRVZlI1RkNJVmkxNElOTFVQdmQwTmxYUXAzTXY3X2I5RFVNOHU4S0NpV2tDNjlmYk5VVkRPQk82QVFWTVVZVGppSEozTzVKZ01oVjV2ZHFmU3hoeEJsaWdWdUVFM3lDclZqUlZOWS1NeTZadEI3eDhVVXRoSUI?oc=5) |
| 实时 | 11-02 22:08:55 | 11-02 22:10:55 | 2.01 | [Arm Holdings Fell Nearly 10% on Intel’s Warning, About as Much as Options Expect Its Own Report to Move It. Here’s What November 4 Has to Show - TIKR.com](https://news.google.com/rss/articles/CBMi9wFBVV95cUxQLXlQb3R2cEs2RnZVQ2tFSkx6Q3JCMEQzdzFWTnZNNllFTXl4NnJiQktRVlYxTTZyZFA4QWswM1ZQcVhqNGVOaDdKSW80R0pUdzlVRlZ3MkRua0pUY0pHbTc2OUNjRDd1ZGN0WHVCVmFtMHdrZnJOeHJnUS1lMXhnS25HSFE5TnhiOHdueTFrQXA0QW1rRWRRZ2JOZjIzOThQX2Fqdk5qTVBZMlpyZ2tKRnpQa1JJTGRxUDdNclVWdGpLeTRoLTM1N1dpSnhPdTUzZ0JBVVQyTUNJUWRCY0ZYYjhaV2dYVHpXRVlWM211c0ZTWld3VHhN?oc=5) |
| 实时 | 11-02 22:08:10 | 11-02 22:11:35 | 3.43 | [IonQ (IONQ) Earnings Preview: 3 Key Metrics Behind Its 287% Revenue Surge - TipRanks](https://news.google.com/rss/articles/CBMioAFBVV95cUxOSjI3LVV6NXE4S3dCX1VGdG1IeHRzZGhWdlNTZzJQemdaeTFxeUFhTmNqUEpxbHFCSUVKd0J3ejRJUHdzamN2MTlXdHQzbWdmR1pCaWZSYUtnOTdKNWdJUm9VUldaMVpHczNFZ1FnRmJfa3FHS3BDNFo5R25tNUNZeEo4NUFvVXAxbnBuVURpTVJUb0NISXYzVGd2ZmRQX2Fp?oc=5) |
| 实时 | 11-02 22:11:55 | 11-02 22:15:35 | 3.67 | [MicroTech Medical Proposes Governance Overhaul and New Board - TipRanks](https://news.google.com/rss/articles/CBMisgFBVV95cUxQSnlKME82SlRzcGJVOE82MUhFVzE4cW5XYUcyQzgxRW94OTA0cFVPUjgtWGs1TjVKaHpxSXRVUUVWcjBYWVNPV204UUZoMTEwU1NSdVpRQUlHS281eDNjSmJQd1NraDAtcTJxVzNvTmpjWFlUYXFENmZoYzNRT2ZvQjFaMDNmREdoenlCSFVwcWRRbUoxTkJJa0J5Z295TXFUY2dZNWV5dlJPT3ZhMDdkNDln?oc=5) |
| 实时 | 11-02 22:12:26 | 11-02 22:17:35 | 5.16 | [Benchmark Electronics expands U.S. manufacturing for Oxide Computer Company - TipRanks](https://news.google.com/rss/articles/CBMiwgFBVV95cUxQSHFrdjl1ajd6Z05kcENuWnhDODlYMkRUMUZNVTMza0JveVNvUS03Nmh4QnBkaVVzdE5iRFpkWUU1N2RNWl9Ud3pTWnpGNlpGTFZ3a0VMQTBQMFQwYTh2YjFEWmdPaTFzS3dPU1lpSHVvdVIzMGJLcHl5VENpMkxpeWpEbWNKN1pxM3cxb1JrcXg5MlN1VnRraWVpbzY3R0hKMktZMG1WZnZqWUlkc203YXluV3RZekx2ZWJ0UThsLUx6dw?oc=5) |
| 实时 | 11-02 22:15:00 | 11-02 22:18:15 | 3.25 | [Snowflake to Present at Upcoming Investor Conferences - Business Wire](https://news.google.com/rss/articles/CBMirwFBVV95cUxPWW8xS01DYjJBMklEMm1VeTlWU1J3ODdfa0V4VTA2VmNkQ3ppR1hhUG90M2c0bW1nZTdnY3NPVTdTRDVYR05UTE1HeG41bHlPc0NKbjZTcDY1NzBtNUZFWGh6QnVpVlpHd1JmYktXNmI3N3M5V3lMQ25DMmNvVk9tSFg5SDVzYVlZYlpvclFkcUJIZ3BoOFhZLXRvcEV3aFYyaVlnVlRhQTNLNXhFX0Fv?oc=5) |
| 实时 | 11-02 22:14:15 | 11-02 22:24:55 | 10.67 | [Intel "Wildcat Lake" Powers Zotac's New Fanless ZBOX Mini PCs - TechPowerUp](https://news.google.com/rss/articles/CBMimAFBVV95cUxPczl0djRtaEl5OVdNZG5nMnZhRVpHdkN2UVpDQnk0SE4yLTVreXE2dHpOUWdlTEpNaVc1YkVuajVYSlRLbHdwelhpbUpCckpNMExycE1lM1lBQmYtSVNIRHpPX1RNaXhxQnFmQ29VSlJRU2dQRG9MT1VTdnVtNERPVGhJZEN4enhvczZBalozRmNTS2pLdFJKN9IBngFBVV95cUxOeGhtOW8yYmFKaHdtNEg3eDVfSXp1dC1YSklXcmY5OFVndG9lcGx6d0tGNUlBUWVlenVMMkdlTUtMekpibll6WXhyb3pvY1pQNkVwSXd0eGZkeE12M0ZkQ0o1ZDMybTVpZm40Sm5samdtdlA2TjJsM0Vybm5tY3FJQlZjZWtsR1Ztb1hwZklwLWtIckhaNzJGSWYyajNIUQ?oc=5) |
| 实时 | 11-02 22:22:59 | 11-02 22:28:35 | 5.61 | [Bilici Yatırım Repurchases 392,095 Shares in Buyback - TipRanks](https://news.google.com/rss/articles/CBMipgFBVV95cUxOVVZaenN5UThETE9PblNWWGZ3UzdhQWNrZGNhYjRrS3g4TjM0aDNyNUp4LXktNi1LSl9wY2NyWWRtTnJHWGFmOC1xQjJhWnZieldGYnRtU3R6Y0I2c0k3OUFKYzhLX3dxTUNpakxkd0FrLThXUk84Nldqbm9ReWRqVUZNMGZQNkJwMnZuTzg2ZHAxaWU5WXAyUWhmM19xWDVOZ0h2WlVR?oc=5) |
| 实时 | 11-02 22:25:22 | 11-02 22:28:35 | 3.23 | [Mid Penn Bank appoints Scott Micklewright as COO - TipRanks](https://news.google.com/rss/articles/CBMinwFBVV95cUxQYWVBblBPQmJwcUNSRy1yeU1QOFF3b3BEZGVjaGRUWk5uYURST1NIekN0LXVVVURsaFY3M0c3SE80aHR2Vk51M2FUVFNLNnI5eGYzbWdkaWp5WjBsT3hOeDNva0ZqdC1LMmozZU80UHh3My1mOEpaY3M4dVhJbFNsTE1rYXZkcUN1REp0bnJNdzBCMWN3bTFBcEd0V2l1TWc?oc=5) |
| 实时 | 11-02 22:25:40 | 11-02 22:28:35 | 2.93 | [Methanex price target raised to $75 from $65 at RBC Capital - TipRanks](https://news.google.com/rss/articles/CBMiqwFBVV95cUxQR05HYW5ZZGcybFJvVGVsdzdIVXZ3Z1IycVU5OGNzajFDeXVYZjBfcERUSzRFS1V6OTNPWVVZMkhPNXJfMEQ2bjU4SzEwM21fLVRTNXRTTXhlVEdBdjRDeDZJbmdKOFdNY1lNa1VkVEFYVEtuSVdGZDV4cHh1bG5FZnJSZEpDNlF6d1o4V0p5OFVLc3RzSE9abmpZWXJoUmxMTldubms2WHdyTWM?oc=5) |
| 实时 | 11-02 22:30:53 | 11-02 22:34:15 | 3.37 | [Snowflake to Announce Financial Results for the Third Quarter of Fiscal 2027 on December 2, 2026 - Snowflake](https://news.google.com/rss/articles/CBMijAFBVV95cUxOM3lfOHNFTW5mM1NDZkZiNnJSdHRxbnJDSjB5WVl4VF9mUEd1Y0QwMm9jMW1aQUVycVNNOGx1bDE1aVVqcTBmbFJMcnhZV1Q5ZnByUmxBUVl2N1ZSNEMzYW5BQlJpUWdJVEpaZHZOSmlpT2I2M3NOR0IwT0N3aWtOdkFhOW5Lc3ZvYWJoZw?oc=5) |
| 实时 | 11-02 22:36:07 | 11-02 22:37:15 | 1.14 | [Snowflake to Present at Upcoming Investor Conferences - Snowflake](https://news.google.com/rss/articles/CBMisgFBVV95cUxQYW5scHkwQWNXcXpyS3dUeG5LTkRaMEMxTDZTVnNrNUlyeWM3Z01VNlZzNmdqOERvQ0ZGWGtkUzdWby1sQzdiTnVudEY2TV9CQjNyQ180WFp0bnBWZkthM2RyU0x4NjFxWTRWWjFsbEpaYk8xWGt6TjA1cG5GUElZdUZReDhoNXBEZTVONHpHYXhEdXhLbllGemg0YVk0MnRaRHJLTF8tbDFIZ29WUjBDYXN3?oc=5) |
| 实时 | 11-02 22:21:14 | 11-02 22:38:35 | 17.36 | [Neighborhood Intelligence enters Stockholder Support Agreement - TipRanks](https://news.google.com/rss/articles/CBMisgFBVV95cUxPVDlRYTA2N0hCaElqZ1NPRVNiRTR0Vm8ycHRVXzNpeDhPMDMtbGJCTEVlRUd3aV8wYjJXejVhbV9UT1pHenBoaUc1TEpTYTEwMHdmMUJUWnZGYkl5SzYwU21HdG9uZnNPMy0tYldGQ3licURrdzVNTUVMbHhvbGJ6ZElpQVYtZS1Lc2FENHhHbmh6alM3aURDblBsUXI2aGQzVGNOdURwLTVVT0djMzNuMDNn?oc=5) |
| 实时 | 11-02 22:39:59 | 11-02 22:43:15 | 3.27 | [Testing Support Level, SNOW Stock Drops 7.32% on Friday - JournalArta](https://news.google.com/rss/articles/CBMilwFBVV95cUxQOW05eGVFbk81SEZNMDliSk5uN3VNcUgzZHdOOEFzRE5rczRISmxoM3JuX1Q4S2t3SVd6Z1l0R1RIWDB4WDdib3AyenVxUzRLUW9raDZWNWphQ3NHZS1nWFdKNHdMUEhURTRJc3QzUGVaVUg0eTEwXzBwam01azhyZU9TUWxadFZmTWdBOEY3WUk4M2hfam9R?oc=5) |
| 实时 | 11-02 22:31:00 | 11-02 22:46:15 | 15.25 | [Celebrating the wonder and tradition of the winter season - PR Newswire Canada](https://news.google.com/rss/articles/CBMirwFBVV95cUxOTm9CWm9UZWlabFdJcUxxSzd2WFRVel96ZjZGYmhBNGFNdHJNQkc3d3FtWE5jM2lnNkVBV085UWVTalN5NDhYSklPRTRZTURWcUhHV3dxWXRDVUE5Qk0yclRwZXJ6RUE3OW5KdE5iNzM2c3N2MG9oeUJ0d1lYYk8yX1pVU2EzRzZNT0xhQ0VpUi1kUmpCcVZQNUN6dTBMY3dOWXp3eWZfWEU4dWhvRXBF?oc=5) |
| 实时 | 11-02 22:45:37 | 11-02 22:46:28 | 0.86 | [Nebius Group NV (NBIS) Director Boynton John Wilson IV Sells 333 Shares - GuruFocus](https://news.google.com/rss/articles/CBMirAFBVV95cUxQMm92a1F2NnUyeVVJZ01PR3ROZTAxR0l4Z3dlczJHZ3AxRXRlRkNXNW1FY0llaGVWUTl4X1JWM2dodUx5Wjl6NEl5V1h2Ykh4RFVQR2tRc2k3SmNMRmUxa3lBMDhIWjdOQmlRZHppUWpNUzR0bWFWWXNnTGp4U2hiTFdFVjJ3SHJlUGFYNGlrMGVQUmk1NWxYOTBZVG1JNEg2QkNGdGtUemdseEZD?oc=5) |
| 实时 | 11-02 22:49:13 | 11-02 22:52:55 | 3.71 | [Intel Q3 FY 2026: Xeon Demand Tests Supply as PC Spending Weakens - The Futurum Group](https://news.google.com/rss/articles/CBMioAFBVV95cUxOcjJScjU1WVVIZm1VRTItdzdjdFhIcDJobW9kR0lqM1hHM0ZLNE9ydy14NFhxdi1vc19TR09KSGtudWw3eU9tVVdGUnNwdWZJOG9YUHUxcFI4SUNmUVdobTR4SnVWQkZpMXZWaGNXZ0dJRzVoZ3N3eG8zaTVEbl9fN3ZzR3hiQzlKNDc4RzhEMHBRcV9fcXNYUjEyT20yTUNj?oc=5) |
| 实时 | 11-02 22:55:00 | 11-02 22:58:55 | 3.93 | [Intel's 30% Collapse Finally Changes The Setup (Rating Upgrade) (NASDAQ:INTC) - Seeking Alpha](https://news.google.com/rss/articles/CBMirAFBVV95cUxOSXhwc3VvcDN4LXN6cGhULXp5X2NjSHdPTDY1djJqd1VQanpSaFpBQVZSdGgxV3hQT2x2QkZKd1p5OVV3STVtSU5xSGlUZ3N4dWRSRzlHaFBfR3FlQW9ocEkzdDRINlJPWmZEQThISzJudUlpTUVzZVdxZC1pOHVva2RicTZmS1o3XzlSNzlIb2FqTDJhdHFLNkxnR3dBY0J4QVhxTXBSN094UUpY?oc=5) |
| 实时 | 11-02 22:59:53 | 11-02 23:01:55 | 2.04 | [Tesla Could Soon Bring Grok to Intel-Based Cars - Drive Tesla Canada](https://news.google.com/rss/articles/CBMihAFBVV95cUxOaElIbm5aVW84ang3QkkyOGZ2Umsya2lLN0lmTWQwUjk3bEtCclJUdFkyYzV1R19jb2NXdFJrOEFpR1ktanpjN1pMaTB3bkItd2pzYTA5TUZZRHdHWTdwS2xHUk1FMlFucjA1U2VjZEhoUDF2akxwdGI4akdEN1RKTzFhSk8?oc=5) |
| 实时 | 11-02 23:05:00 | 11-02 23:08:02 | 3.03 | [I’m Eying Ocular Therapeutix, Rocket Lab; Also Small Caps Steady - RealMoney Pro](https://news.google.com/rss/articles/CBMioAFBVV95cUxOVWxlcG9LQmZpRU9Hd2twVFZLWGlGbGhrZjZ1SGNtM01DNXg3SE5YRDZuOVpVWkJpczVSX2llOUpqWUlHZXVIUHRBQ3gxOElUdnhYNzNUTUEycHExNFdBWDd3SzJFdXNYMEpqVF9iRVVrdll6eDVDUkNfZk96Q0YtcXBtR2xDaHZ1Rl9DYkVkb3lFQ2RGMkhEZmllWHF0VS1x?oc=5) |
| 实时 | 11-02 23:25:03 | 11-02 23:25:28 | 0.43 | [Nebius shares fall as AI infrastructure outlook faces scrutiny - StreetInsider](https://news.google.com/rss/articles/CBMitwFBVV95cUxNQmowcVBCUE5HaGY0U0U1SnFYUW8yRC00RkpGZUtjRDJ3LTNXNUVoQTFObGsydmZTSl9YZW9HVmZCeThjdkZRVE5iUm9nMDVIeDhjclBlaHEyM3dZZjU4UGxocERVTzhWcVFFSXZ0YzBQZGJwVnRKQmwtY2pVQmJSZWdfQjZ6bGNaWXdvZGVIUXlEZkk2N3JHMWhpdnpRdDVMSHBoOTU5cEJ5Q29ac0RYbmtBT3BkU0E?oc=5) |
| 实时 | 11-02 23:22:50 | 11-02 23:33:55 | 11.09 | [AMD Stock Got Caught in Intel’s Selloff. What to Watch This Week. - Barchart.com](https://news.google.com/rss/articles/CBMiqgFBVV95cUxOQ0dnXzRmUWRQeVFlSVdGUFVQTEhqQ0I3aWw0WXMwLUdLWVUwdGtfZF9aYk1mNXF4YkZqRXRpZnZoMV9INDI3eHdMZGFlTFF0cWg1MHV2cjc1Tko0S3hkbUg4NzctQ2VnR2VaLWRLY0RJWXQ1N3RtNWtuVHdrU1hyZ3F6THIxaFNEU1g2ZXd5VnVQOFhzM0JYSmd1eVBJdjgwZDQ3T3ljZmlsZw?oc=5) |
| 实时 | 11-02 23:45:00 | 11-02 23:50:28 | 5.48 | [Nebius Lost 35% Last Month As Contagion Spikes: One Wall Street Pro Says 165% Gains Are Coming - 24/7 Wall St.](https://news.google.com/rss/articles/CBMi0AFBVV95cUxPTExJa3pYS0N4MXM0QVlsU1NabVJDUjJ4ZnBLcEg1dDNIeTk3T1llcTVzRnhtZVNPLWFMR1pWaGphbG5DcThQdlZnM2hSQ0RfZ0FMQWFDekJjdXMxT0xTTlJxRTdESjNqbGxqNllTZFh4N3pZanFaUl84SGtURDVBcFZuNjdZS0pJcHBvWmwzN2ZHb3RkT3JrVHpMQnU3b0Q0M01lZzZWWk1jOXdzMjNFOHM3Qkh1djZoc05kMmhoenRNRTAwU1doYXZBYmZnMFdo?oc=5) |
| 实时 | 11-02 23:51:28 | 11-02 23:56:55 | 5.46 | [Intel may discontinue Arc B580, shifting GPU focus to AI instead - Club386](https://news.google.com/rss/articles/CBMia0FVX3lxTE5rR3NlTU8zN2JGbXRLazZ4MmtCb2lOMVFYUS1obnlGOHlZbHVUMHpLRFRFWS1IeTZaRzUzZWpzME5kUm5lVENCUUhHRFpuS0kwX1lxS185bVVQQ2JSdmtwN2EzaDlMWnllMVBZ?oc=5) |

### huggingnews_rss — HuggingNews Atom

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-02 14:37:08 | 11-02 15:01:37 | 24.48 | [UAE Signs 4 OpenAI Deals to Build World's First AI-Native Government](https://huggingnews.com/ai/uae-signs-4-openai-deals-to-build-worlds-first-ai-native-government-add1c408) |
| 补扫 | 11-02 06:22:20 | 11-02 15:01:37 | 519.27 | [Trump Says US Will Look 'Very Closely' at Reported Chinese Spy Plot Targeting Nvidia CEO](https://huggingnews.com/tech/trump-says-us-will-look-very-closely-at-reported-chinese-spy-plot-target-4d055e6a) |
| 补扫 | 11-02 06:23:47 | 11-02 15:01:37 | 517.83 | [xAI to Implement Invisible Text Watermarking for Grok API to Comply With EU Law](https://huggingnews.com/ai/xai-to-implement-invisible-text-watermarking-for-grok-api-to-comply-with-e0c94074) |
| 补扫 | 11-02 08:53:45 | 11-02 15:01:37 | 367.86 | [Anthropic's Unreleased AI Model Identifies 62 Chrome Flaws and Critical Linux Kernel Vulnerability](https://huggingnews.com/cybersecurity/anthropics-unreleased-ai-model-identifies-62-chrome-flaws-and-critical-l-2ac84c3a) |
| 补扫 | 11-02 10:52:41 | 11-02 15:01:37 | 248.92 | [White House Withholds Voluntary AI Testing Framework Citing National Security](https://huggingnews.com/us-politics/white-house-withholds-voluntary-ai-testing-framework-citing-national-sec-f124d082) |
| 补扫 | 11-02 01:38:34 | 11-02 15:01:37 | 803.05 | [GPT-6.1 and Grok 4.7 Select Differing Treatment Targets in Robotic Brain Surgery Tests](https://huggingnews.com/ai/gpt-61-and-grok-47-select-differing-treatment-targets-in-robotic-brain-s-e370fe16) |
| 补扫 | 11-02 14:51:34 | 11-02 15:01:37 | 10.04 | [Google's Gemini 4 Carbon Tops Mercor's APEX-Agents Leaderboard With First Mean Score Above 90%](https://huggingnews.com/ai/googles-gemini-4-carbon-tops-mercors-apex-agents-leaderboard-with-first-36f3932d) |
| 补扫 | 11-02 00:22:50 | 11-02 15:01:37 | 878.78 | [Apple Plans Next-Gen Touchscreen MacBooks With M7 Chips and New AI Hardware](https://huggingnews.com/tech/update-apple-plans-next-gen-touchscreen-macbooks-with-m7-chips-and-new-a-ac92a7a9) |
| 补扫 | 11-02 07:07:31 | 11-02 15:01:37 | 474.09 | [Mitsubishi Heavy Expands U.S. Gas Turbine Output to Address AI Power Surge](https://huggingnews.com/ai/mitsubishi-heavy-expands-us-gas-turbine-output-to-address-ai-power-surge-aca6655c) |
| 补扫 | 11-01 23:23:15 | 11-02 15:01:37 | 938.36 | [Dulo Aims to Generate Valid Car and Plane Designs from AI Prompts](https://huggingnews.com/startups/dulo-aims-to-generate-valid-car-and-plane-designs-from-ai-prompts-00756016) |
| 补扫 | 11-01 22:21:51 | 11-02 15:01:37 | 999.76 | [Meta, Alphabet and Intel Fall Below Offering Prices After AI Stock Sales](https://huggingnews.com/us-markets/meta-alphabet-and-intel-fall-below-offering-prices-after-ai-stock-sales-39388088) |
| 补扫 | 11-02 14:52:31 | 11-02 15:01:37 | 9.09 | [Anthropic Plans to Raise $3.5B From Japanese Investors Before Nasdaq Debut](https://huggingnews.com/startups/anthropic-plans-to-raise-35b-from-japanese-investors-before-nasdaq-debut-2f378a3b) |
| 补扫 | 11-01 22:52:36 | 11-02 15:01:37 | 969.01 | [OpenAI Plans New Washington Lobbying Blitz to Push for Mandatory AI Speed Limits](https://huggingnews.com/ai/openai-plans-new-washington-lobbying-blitz-to-push-for-mandatory-ai-spee-13469f2b) |
| 补扫 | 11-02 08:22:14 | 11-02 15:01:37 | 399.37 | [Saudi Aramco Acquires 49% Stake in Humain AI Startup With $2.6B Capital Boost](https://huggingnews.com/startups/saudi-aramco-acquires-49percent-stake-in-humain-ai-startup-with-26b-capi-f847da6a) |
| 补扫 | 11-02 05:23:02 | 11-02 15:01:37 | 578.58 | [Elon Musk Cites High Serving Costs for Reduced Grok Bot Usage Limits](https://huggingnews.com/ai/elon-musk-cites-high-serving-costs-for-reduced-grok-bot-usage-limits-ea8ebd44) |
| 补扫 | 11-02 13:37:54 | 11-02 15:01:37 | 83.70 | [Opus 5.5 Tops New SemiAnalysis GDPx AI Benchmark With 12% Score](https://huggingnews.com/ai/opus-55-tops-new-semianalysis-gdpx-ai-benchmark-with-12percent-score-994048ca) |
| 补扫 | 11-02 10:51:58 | 11-02 15:01:37 | 249.64 | [Hedge Funds Go Underweight AI for First Time Since Early 2023 After $5T Rout](https://huggingnews.com/us-markets/hedge-funds-go-underweight-ai-for-first-time-since-early-2023-after-5t-r-c6e872fd) |
| 补扫 | 11-02 02:06:12 | 11-02 15:01:37 | 775.41 | [Nvidia Tops 1,000 Tokens a Second per User on 700B+ Models With Agent Swarm Code](https://huggingnews.com/ai/nvidia-tops-1000-tokens-a-second-per-user-on-700b-models-with-agent-swar-17485e8c) |
| 补扫 | 11-02 05:14:29 | 11-02 15:01:37 | 587.12 | [Google Energy Chief Says Space Data Centers Cost Up to 100x More Than Earth Facilities](https://huggingnews.com/tech/google-energy-chief-says-space-data-centers-cost-up-to-100x-more-than-ea-c7f7e0c5) |
| 补扫 | 11-02 04:52:05 | 11-02 15:01:37 | 609.53 | [Anthropic Research Shows 0.1% Data Poisoning Can Plant Persistent Backdoors in AI Agents](https://huggingnews.com/ai/anthropic-research-shows-01percent-data-poisoning-can-plant-persistent-b-c78a60e3) |
| 补扫 | 11-02 09:52:15 | 11-02 15:01:37 | 309.35 | [South Korea Will Nearly Double Government AI Spending to 21.3 Trillion Won](https://huggingnews.com/ai/south-korea-will-nearly-double-government-ai-spending-to-213-trillion-wo-195b30ad) |
| 补扫 | 11-02 09:53:07 | 11-02 15:01:37 | 308.49 | [Proton AI Memo Outlines Future With Minimal Human Code Review](https://huggingnews.com/ai/proton-ai-memo-outlines-future-with-minimal-human-code-review-bc799bdc) |
| 补扫 | 11-02 14:37:36 | 11-02 15:01:37 | 24.01 | [OpenAI Signs Partnership With Abu Dhabi's ADNOC at ADIPEC Summit](https://huggingnews.com/business/openai-signs-partnership-with-abu-dhabis-adnoc-at-adipec-summit-8e6ddbf3) |
| 补扫 | 11-02 05:07:52 | 11-02 15:01:37 | 593.75 | [Andrej Karpathy Builds Custom YouTube Recommendation Site in 2 Hours With Claude](https://huggingnews.com/ai/andrej-karpathy-builds-custom-youtube-recommendation-site-in-2-hours-wit-96ac66d0) |
| 补扫 | 11-02 11:08:42 | 11-02 15:01:37 | 232.91 | [DeepSeek's New Super-Agent Prototype Reduces Coding Task Times to 15.7 Minutes](https://huggingnews.com/ai/deepseeks-new-super-agent-prototype-reduces-coding-task-times-to-157-min-de7990cd) |
| 实时 | 11-02 15:22:44 | 11-02 15:32:36 | 9.86 | [Adnoc Becomes First Energy Company to Deploy OpenAI Frontier AI](https://huggingnews.com/business/adnoc-becomes-first-energy-company-to-deploy-openai-frontier-ai-f71880cf) |
| 实时 | 11-02 16:51:59 | 11-02 16:52:35 | 0.59 | [Nvidia VR200 Rack Volume Ramp Pushed to Q4 2026 on PCB Issues](https://huggingnews.com/tech/nvidia-vr200-rack-volume-ramp-pushed-to-q4-2026-on-pcb-issues-ac657529) |
| 实时 | 11-02 18:22:45 | 11-02 18:33:35 | 10.84 | [OpenAI Pitches Potential 2028 Presidential Candidates on AI Messaging](https://huggingnews.com/us-politics/openai-pitches-potential-2028-presidential-candidates-on-ai-messaging-8df5783c) |
| 实时 | 11-02 19:08:21 | 11-02 19:13:36 | 5.26 | [OpenAI Seals New Pacts to Deploy AI Across UAE Energy and Investment Sectors](https://huggingnews.com/ai/openai-seals-new-pacts-to-deploy-ai-across-uae-energy-and-investment-sec-7a4884aa) |
| 实时 | 11-02 19:22:42 | 11-02 19:23:34 | 0.87 | [Nous Research Targets Full Public Launch of Hermes AI App After Thanksgiving](https://huggingnews.com/ai/nous-research-targets-full-public-launch-of-hermes-ai-app-after-thanksgi-cfa9fc81) |
| 实时 | 11-02 20:39:58 | 11-02 20:43:34 | 3.60 | [Marvell Agrees to Acquire Lumentum for $115B in All-Stock Deal](https://huggingnews.com/business/marvell-agrees-to-acquire-lumentum-for-115b-in-all-stock-deal-ee07b1c8) |
| 实时 | 11-02 21:08:50 | 11-02 21:15:34 | 6.73 | [Google's Gemini 4 Carbon Offers 4x Lower Cost Than Opus 5.5 on New AI Agent Benchmark](https://huggingnews.com/ai/googles-gemini-4-carbon-offers-4x-lower-cost-than-opus-55-on-new-ai-agen-e2a87831) |
| 实时 | 11-02 21:23:19 | 11-02 21:25:35 | 2.26 | [Chinese AI Hacking Campaign Compromises 140 Global Organizations in 11 Days](https://huggingnews.com/cybersecurity/chinese-ai-hacking-campaign-compromises-140-global-organizations-in-11-d-694b64cb) |
| 实时 | 11-02 21:52:34 | 11-02 21:57:35 | 5.01 | [North Korea Integrates AI Into Crypto Hacking to Fund Military Expansion](https://huggingnews.com/cybersecurity/north-korea-integrates-ai-into-crypto-hacking-to-fund-military-expansion-277df852) |
| 实时 | 11-02 22:24:30 | 11-02 22:27:34 | 3.07 | [SpaceX Restores Grok Bot Access Limits After Musk Warns of Unsustainable Costs](https://huggingnews.com/ai/spacex-restores-grok-bot-access-limits-after-musk-warns-of-unsustainable-28fbc17d) |
| 实时 | 11-02 22:24:33 | 11-02 22:27:34 | 3.02 | [Cipher Digital Targets 5.9GW Total Power Pipeline With Pivot to On Site Generation](https://huggingnews.com/earnings/cipher-digital-targets-59gw-total-power-pipeline-with-pivot-to-on-site-g-34e00988) |
| 实时 | 11-02 22:38:44 | 11-02 22:44:40 | 5.94 | [Anthropic's Q3 Revenue Surges to $17B Despite $23.3B Net Loss](https://huggingnews.com/earnings/anthropics-q3-revenue-surges-to-17b-despite-233b-net-loss-e3bb2170) |
| 实时 | 11-02 22:52:03 | 11-02 22:56:34 | 4.52 | [OpenAI to Release Next GPT Using Model That Solved Navier-Stokes Equations](https://huggingnews.com/ai/openai-to-release-next-gpt-using-model-that-solved-navier-stokes-equatio-1dde1157) |
| 实时 | 11-02 23:06:59 | 11-02 23:11:38 | 4.65 | [Anthropic's Amended IPO Filing Shows $17B Q3 Revenue and Swing to Operating Loss as Commitments Hit $895.4B](https://huggingnews.com/earnings/anthropics-amended-ipo-filing-shows-17b-q3-revenue-and-swing-to-operatin-68d5e07a) |
| 实时 | 11-02 23:38:01 | 11-02 23:38:35 | 0.57 | [Oracle Targets $20B in Fresh Capital to Fund AI Infrastructure Buildout](https://huggingnews.com/ai/oracle-targets-20b-in-fresh-capital-to-fund-ai-infrastructure-buildout-4d1ae76a) |
| 实时 | 11-02 23:36:30 | 11-02 23:38:35 | 2.08 | [JPMorgan Estimates Amazon 2027 Capex Will Jump 77% to $395B as Stock Erases Post-Earnings Drop](https://huggingnews.com/earnings/jpmorgan-estimates-amazon-2027-capex-will-jump-77percent-to-395b-as-stoc-89aa42f0) |
| 实时 | 11-02 23:38:10 | 11-02 23:38:35 | 0.42 | [Deep Principle's New AI Model Beats GPT-6-Astra in Scientific Research Benchmarks](https://huggingnews.com/ai/deep-principles-new-ai-model-beats-gpt-6-astra-in-scientific-research-be-4a445940) |
| 实时 | 11-02 23:54:32 | 11-02 23:58:36 | 4.07 | [BAAJA Law to Shift $50B in Power Grid Transmission Costs to Data Centers](https://huggingnews.com/ai/baaja-law-to-shift-50b-in-power-grid-transmission-costs-to-data-centers-9b7fa413) |

### nbis_official_actor_rss — NBIS official minority-investment and buyer RSS

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 20:56:05 | 11-02 20:57:21 | 1.28 | [How OpenRouter observes, understands, and acts on 740 million LLM requests a day with ClickHouse Cloud](https://clickhouse.com/blog/openrouter-llm-routing-observability-clickhouse-cloud) |
| 实时 | 11-02 22:45:00 | 11-02 22:46:22 | 1.37 | [What's new in clickhousectl v0.5.0](https://clickhouse.com/blog/clickhousectl-v0-5-0) |

### newswire_rss — Newswire RSS

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-02 05:53:39 | 11-02 15:00:56 | 547.30 | [Post-Quantum Cryptography Vendors in 2026: Who Does What](https://www.elisity.com/blog/post-quantum-cryptography-vendors) |

### quantum_insider — The Quantum Insider / Business, National & Capital Markets

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 17:01:00 | 11-02 17:01:34 | 0.58 | [IDEMIA Secure Transactions Receives EMVCo Certification for PQC Payment Cards](https://thequantuminsider.com/2026/11/02/idemia-payment-cards-post-quantum-transition/) |
| 实时 | 11-02 17:07:06 | 11-02 17:07:34 | 0.48 | [BTQ Technologies Brings MIMIQ Quantum Emulator to qBraid](https://thequantuminsider.com/2026/11/02/mimiq-quantum-emulator-available-qbraid/) |
| 实时 | 11-02 17:29:12 | 11-02 17:29:34 | 0.38 | [Chattanooga Accelerator Selects Four Quantum Startups For Commercialization Program](https://thequantuminsider.com/2026/11/02/chattanooga-accelerator-selects-four-quantum-startups-for-commercialization-program/) |
| 实时 | 11-02 19:34:39 | 11-02 19:35:34 | 0.93 | [Equal1 Appoints Andrew Flynn as Chief Operating Officer](https://thequantuminsider.com/2026/11/02/andrew-flynn-joins-equal1-chief-operating-officer/) |
| 实时 | 11-02 20:38:55 | 11-02 20:39:34 | 0.66 | [Quantcore Welcomes Innovation Minister to Showcase Manufacturing Ambitions](https://thequantuminsider.com/2026/11/02/quantcore-welcomes-innovation-minister-to-showcase-manufacturing-ambitions/) |
| 实时 | 11-02 20:42:49 | 11-02 20:43:34 | 0.76 | [Nobel Laureate John Martinis to Deliver Quantum Computing Lecture at UWA](https://thequantuminsider.com/2026/11/02/john-martinis-quantum-computing-lecture-uwa/) |
| 实时 | 11-02 21:05:38 | 11-02 21:05:54 | 0.28 | [Scottish Widows Targets Quantum Computing With UK Venture Growth Investments](https://thequantuminsider.com/2026/11/02/scottish-widows-targets-quantum-computing-with-uk-venture-growth-investments/) |
| 实时 | 11-02 21:24:22 | 11-02 21:24:34 | 0.21 | [South Korea Launches Hangang Supercomputer, Plans Quantum Integration](https://thequantuminsider.com/2026/11/02/south-korea-hangang-supercomputer-quantum-integration/) |
| 实时 | 11-02 21:55:24 | 11-02 21:55:34 | 0.18 | [HKUST and HKMA Launch PQC Toolkit for Banks](https://thequantuminsider.com/2026/11/02/hkust-hkma-launch-pqc-toolkit-banks/) |
| 实时 | 11-02 23:51:08 | 11-02 23:51:34 | 0.45 | [Qtonic Quantum Appoints Former NATO Commander as Head of Operations](https://thequantuminsider.com/2026/11/02/former-nato-allied-land-command-chief-qtonic-quantum-coo/) |

### quantum_zeitgeist — Quantum Zeitgeist

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-01 18:06:15 | 11-02 15:00:15 | 1254.00 | [Researchers Measure Logical Qubit with 0.72% Error Using Quantum Surgery](https://quantumzeitgeist.com/quantum-surgery-logical-qubit-error-measurement/) |
| 补扫 | 11-02 03:50:09 | 11-02 15:00:15 | 670.10 | [Researchers Predict Stable Electronic Structures Using Mamba](https://quantumzeitgeist.com/mamba-generative-quantum-eigensolver-electronic-structure-prediction/) |
| 补扫 | 11-02 03:46:42 | 11-02 15:00:15 | 673.55 | [Researchers Find Sound Splits Kelvins, Boosting Their Numbers](https://quantumzeitgeist.com/kelvins-phonon-emission-splits-increase-population-energy/) |
| 补扫 | 11-01 18:07:33 | 11-02 15:00:15 | 1252.70 | [Researchers Link Entanglement Bootstrap to Algebraic Theory](https://quantumzeitgeist.com/entanglement-bootstrap-doplicher-haag-roberts-theory-connection/) |
| 补扫 | 11-02 03:31:08 | 11-02 15:00:15 | 689.12 | [Researchers Map Exotic States Within Quantum Hall Fluids](https://quantumzeitgeist.com/quantum-hall-fluids-exotic-state-mapping/) |
| 补扫 | 11-02 03:30:51 | 11-02 15:00:15 | 689.40 | [Researchers Bound Performance of Quantum Error Recovery](https://quantumzeitgeist.com/quantum-error-recovery-performance-upper-bound-codes/) |
| 补扫 | 11-01 18:14:31 | 11-02 15:00:15 | 1245.73 | [Researchers Stabilise Quantum Memory with Random Updates](https://quantumzeitgeist.com/quantum-memory-stabilization-random-update-technique/) |
| 补扫 | 11-01 18:26:42 | 11-02 15:00:15 | 1233.55 | [Pavia Team Detects Non-Gaussian Noise Using EPs](https://quantumzeitgeist.com/quantum-devices-exceptional-points-noise-detection-method/) |
| 补扫 | 11-02 03:30:40 | 11-02 15:00:15 | 689.58 | [Researchers Cut Quantum Search Time for Tournament Solutions](https://quantumzeitgeist.com/quantum-search-tournament-solution-time-reduction/) |
| 补扫 | 11-01 18:05:47 | 11-02 15:00:15 | 1254.47 | [Researchers Cut Training Time Using Longer-Range Quantum Links](https://quantumzeitgeist.com/quantum-links-longer-range-variational-algorithms-training/) |
| 补扫 | 11-02 03:30:57 | 11-02 15:00:15 | 689.30 | [Researchers Link Entropy Fluctuations to Quantum System Spectra](https://quantumzeitgeist.com/entropy-fluctuations-and-quantum-system-spectra/) |
| 补扫 | 11-02 03:33:06 | 11-02 15:00:15 | 687.15 | [Atoms’ dipole moment controls collective quantum motion](https://quantumzeitgeist.com/massachusetts-technology-atoms-dipole-moment-controls/) |
| 补扫 | 11-02 03:46:45 | 11-02 15:00:15 | 673.50 | [Southeast University Bounds Continuous Variable Estimation Errors](https://quantumzeitgeist.com/continuous-variable-estimation-error-bounds-distributed-ipe/) |
| 补扫 | 11-01 18:22:33 | 11-02 15:00:15 | 1237.70 | [IBM Quantum’s Joseph Broz to keynote Next Edge Alliance launch](https://quantumzeitgeist.com/ibm-quantums-next-edge-alliance-joseph/) |
| 补扫 | 11-02 03:46:52 | 11-02 15:00:15 | 673.38 | [Researchers Find Attack Exploiting QKD Transmitter Recovery](https://quantumzeitgeist.com/quantum-key-distribution-transmitter-recovery-attack-demonstration/) |
| 补扫 | 11-02 03:46:56 | 11-02 15:00:15 | 673.32 | [Researchers Boost Superconductor Transition Temp by 1%](https://quantumzeitgeist.com/superconductor-terahertz-resonator-transition-temperature-boost/) |
| 补扫 | 11-02 03:31:05 | 11-02 15:00:15 | 689.17 | [Syracuse University Bounds Quantum Channel Advantages](https://quantumzeitgeist.com/quantum-channel-entanglement-advantage-bounds-disprove-conjecture/) |
| 补扫 | 11-02 05:20:19 | 11-02 15:00:15 | 579.93 | [IBM Quantum Safe helps firms plan a move to quantum-resistant data](https://quantumzeitgeist.com/quantum-safe-helps-firms-ibm/) |
| 补扫 | 11-02 03:45:18 | 11-02 15:00:15 | 674.95 | [Model Reproduces GAN Defect Positions with Errors under 10MHz](https://quantumzeitgeist.com/gan-defects-optically-detected-magnetic-resonance-model/) |
| 补扫 | 11-02 03:46:59 | 11-02 15:00:15 | 673.27 | [Researchers Find Complex Automata Aren’t Standard Circuits](https://quantumzeitgeist.com/quantum-cellular-automata-nonstandard-circuit-implementation/) |
| 补扫 | 11-02 03:33:10 | 11-02 15:00:15 | 687.08 | [Quantum smoothing theories find common ground with new retrodiction method](https://quantumzeitgeist.com/hong-kong-quantum-smoothing-theories-common/) |
| 补扫 | 11-02 03:31:02 | 11-02 15:00:15 | 689.22 | [Distributions Fail Quantum Design Test: Researchers Quantify Defect with Formula](https://quantumzeitgeist.com/gottesman-kitaev-preskill-distributions-rigged-quantum-three-design/) |
| 补扫 | 11-02 03:30:44 | 11-02 15:00:15 | 689.52 | [Researchers Bound Quantum Divergence under Indefinite Causality](https://quantumzeitgeist.com/quantum-divergence-indefinite-causality-geometric-limit/) |
| 补扫 | 11-01 18:07:36 | 11-02 15:00:15 | 1252.65 | [Researchers Map Hidden Entanglement Shifts Using Novel Negativity Tool](https://quantumzeitgeist.com/logarithmic-negativity-mapping-hidden-entanglement-shifts/) |
| 补扫 | 11-02 03:30:54 | 11-02 15:00:15 | 689.35 | [Researchers Link Quantum Views to Sharper Particle Paths](https://quantumzeitgeist.com/quantum-reference-frames-sharper-particle-trajectories-correlation/) |
| 补扫 | 11-01 18:42:02 | 11-02 15:00:15 | 1218.22 | [Meet NAKO, A Benchmarking Framework for Evaluating and Diagnosing Neutral-Atom Quantum Compilers](https://quantumzeitgeist.com/meet-nako-a-benchmarking-framework-for-evaluating-and-diagnosing-neutral-atom-quantum-compilers/) |
| 补扫 | 11-01 18:14:46 | 11-02 15:00:15 | 1245.48 | [Researchers Bound Quantum State Discrimination across Regimes](https://quantumzeitgeist.com/quantum-hypothesis-testing-nussbaum-szkola-mapping-bounds/) |
| 补扫 | 11-01 18:05:41 | 11-02 15:00:15 | 1254.57 | [Researchers Measure Entanglement on 12-Qubit System Via Resets](https://quantumzeitgeist.com/quantum-system-resets-measure-twelve-qubit-entanglement/) |
| 补扫 | 11-02 03:32:55 | 11-02 15:00:15 | 687.33 | [Researchers Verify Quantum Computation Beyond Polynomial Hierarchy](https://quantumzeitgeist.com/quantum-computation-polynomial-hierarchy-verification-breakthrough/) |
| 补扫 | 11-01 18:06:50 | 11-02 15:00:15 | 1253.42 | [Rochester Institute of Technology Detects Weak Forces Faster Using Quantum Points](https://quantumzeitgeist.com/quantum-points-detecting-weak-forces-faster/) |
| 补扫 | 11-01 18:06:54 | 11-02 15:00:15 | 1253.35 | [Researchers Link Code Subspaces to Preserving Quantum Measurements](https://quantumzeitgeist.com/quantum-measurements-noiseless-code-subspace-preservation-proof/) |
| 补扫 | 11-02 03:30:48 | 11-02 15:00:15 | 689.45 | [Quantum Retrodiction Follows Unique Map with Three Consistency Rules](https://quantumzeitgeist.com/quantum-retrodiction-bayes-rule-consistency-principles/) |
| 补扫 | 11-01 18:06:19 | 11-02 15:00:15 | 1253.93 | [Researchers Train Algorithms to Detect Entanglement Using Quantum Computing](https://quantumzeitgeist.com/quantum-algorithms-detect-entanglement-using-adiabatic-computing/) |
| 补扫 | 11-01 18:26:50 | 11-02 15:00:15 | 1233.42 | [Researchers Bound Memory Error to Gamma Raised to R Plus One](https://quantumzeitgeist.com/continuous-variable-quantum-error-correction-compatibility/) |
| 补扫 | 11-01 18:26:46 | 11-02 15:00:15 | 1233.48 | [Researchers Link Quantum States’ Entropy to Correlated Wishart Ensembles](https://quantumzeitgeist.com/quantum-states-entropy-wishart-ensembles-correlation/) |
| 实时 | 11-02 16:26:39 | 11-02 16:30:17 | 3.65 | [Researchers Find Adaptive Measurements Outperform Static Ones Exponentially](https://quantumzeitgeist.com/adaptive-measurements-outperform-static-ones-exponentially/) |
| 实时 | 11-02 16:26:47 | 11-02 16:30:17 | 3.51 | [Quantum Biology Lab’s math camp sparks STEM interest in young students](https://quantumzeitgeist.com/howard-quantum-math-camp-biology-labs/) |
| 实时 | 11-02 16:26:59 | 11-02 16:30:17 | 3.31 | [ExeQuantum, Bluebik Titans partner on Thailand’s quantum security](https://quantumzeitgeist.com/exequantum-bluebik-partnership-titans-thailands-quantum/) |
| 实时 | 11-02 16:27:17 | 11-02 16:30:17 | 3.01 | [Iceberg Quantum gains Jens Eisert as quantum advisor](https://quantumzeitgeist.com/iceberg-quantum-jens-eisert-gains/) |
| 实时 | 11-02 17:08:29 | 11-02 17:12:17 | 3.81 | [QSolid consortium ends five years of quantum hardware and software work](https://quantumzeitgeist.com/qsolid-quantum-hardware-software-consortium/) |

### register_software_cloud_security — The Register / Software, Cloud & Security

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-02 12:39:50 | 11-02 15:04:31 | 144.69 | [India floats AI plan under which ‘The model travels to the data, rather than the data travelling to the model’](https://www.theregister.com/public-sector/2026/11/02/india-floats-ai-plan-under-which-the-model-travels-to-the-data-rather-than-the-data-travelling-to-the-model/5306872) |
| 实时 | 11-02 18:15:00 | 11-02 18:15:48 | 0.81 | [Microsoft's moolah mountain is more massive than ever. It just doesn't want the people who built it](https://www.theregister.com/software/2026/11/02/microsofts-moolah-mountain-is-more-massive-than-ever-it-just-doesnt-want-the-people-who-built-it/5306576) |
| 实时 | 11-02 19:45:00 | 11-02 19:46:08 | 1.14 | [FBI pins $6.3B Binance crypto heist on North Korean hackers](https://www.theregister.com/security/2026/11/02/fbi-pins-63b-binance-crypto-heist-on-north-korean-hackers/5306889) |
| 实时 | 11-02 21:15:00 | 11-02 21:16:08 | 1.14 | [Frozen part II: Hackers remotely defrost produce in Euro supermarkets ... again](https://www.theregister.com/cyber-crime/2026/11/02/frozen-part-ii-hackers-remotely-defrost-produce-in-euro-supermarkets-again/5306924) |
| 实时 | 11-02 22:00:00 | 11-02 22:00:48 | 0.80 | [Microsoft's Canada timezone troubles leave admins fixing calendars, pretending they're in Bogota](https://www.theregister.com/software/2026/11/02/microsofts-canada-timezone-troubles-leave-admins-fixing-calendars-pretending-theyre-in-bogota/5306934) |
| 实时 | 11-02 22:45:00 | 11-02 22:46:08 | 1.14 | [Nuclear threats and street brawls: Russia, Ukraine report attacks on their emergency alert apps](https://www.theregister.com/security/2026/11/02/nuclear-threats-and-street-brawls-russia-ukraine-report-attacks-on-their-emergency-alert-apps/5306901) |
| 实时 | 11-02 23:30:00 | 11-02 23:30:48 | 0.80 | [SUSE to cut one in ten employees because of... AI](https://www.theregister.com/software/2026/11/02/suse-to-cut-one-in-ten-employees-because-of-ai/5306950) |

### siliconangle_ai — SiliconANGLE / AI

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 17:00:22 | 11-02 17:01:12 | 0.85 | [Marble Cognition raises $3.75M for its AI agent workers that watch and learn how people work](https://siliconangle.com/2026/11/02/marble-cognition-raises-3-75m-for-its-ai-agent-workers-that-watch-and-learn-how-people-work/) |
| 实时 | 11-02 19:34:14 | 11-02 19:35:12 | 0.98 | [Enterprises bring AI to their data as privacy rules tighten and costs climb](https://siliconangle.com/2026/11/02/enterprise-ai-strategy-cloudera-brings-models-data-evolve26/) |
| 实时 | 11-02 22:00:26 | 11-02 22:01:12 | 0.78 | [With Agentic Studio, LinearB provides the critical context needed to accelerate software delivery](https://siliconangle.com/2026/11/02/with-agentic-studio-linearb-provides-the-critical-context-needed-to-accelerate-software-delivery/) |

### siliconangle_software — SiliconANGLE / Big Data, Cloud & Security

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-01 22:25:39 | 11-02 15:04:31 | 998.88 | [Agent demand can’t be forecast, meaning the database has to be ready for anything](https://siliconangle.com/2026/11/01/agentic-ai-architecture-demands-real-time-databases-evolve26/) |
| 补扫 | 11-01 22:25:54 | 11-02 15:04:31 | 998.63 | [Project-by-project security reviews can’t keep pace with enterprise AI agents](https://siliconangle.com/2026/11/01/tokenization-helps-enterprises-move-ai-agents-production-evolve26/) |
| 实时 | 11-02 22:00:29 | 11-02 22:01:22 | 0.89 | [Exclusive: Guardz brings Palo Alto Networks browser protection to MSPs serving small businesses](https://siliconangle.com/2026/11/02/exclusive-guardz-brings-palo-alto-networks-browser-protection-to-msps-serving-small-businesses/) |
| 实时 | 11-02 22:00:45 | 11-02 22:01:22 | 0.62 | [MazeBolt launches native RADAR deployment for Akamai DDoS protection customers](https://siliconangle.com/2026/11/02/mazebolt-launches-native-radar-deployment-for-akamai-ddos-protection-customers/) |
| 实时 | 11-02 23:00:41 | 11-02 23:01:22 | 0.68 | [Google finds ‘LLMjacking’ attackers running up $100K a day in fraudulent cloud charges](https://siliconangle.com/2026/11/02/google-finds-llmjacking-attackers-running-up-100k-a-day-in-fraudulent-cloud-charges/) |

### spacecom_launches — Space.com / Launches & Spacecraft

没有满足精确可比条件的记录，原因见第 5 节。

### spacenews_rss — SpaceNews

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 22:00:00 | 11-02 22:01:33 | 1.55 | [How to Expand Reliable, Flexible Access to Space](https://spacenews.com/how-to-expand-reliable-flexible-access-to-space/) |

### technews_hardware — TechNews / 半導體、AI

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-01 15:00:25 | 11-02 15:00:06 | 1439.69 | [美 AI 私校 Alpha School 號稱學習快兩倍，紐時揭數據僅 42% 學生達標](https://technews.tw/2026/11/01/alpha-school-claims-to-teach-kids-twice-as-fast-many-dont-make-it/) |
| 补扫 | 11-02 09:00:47 | 11-02 15:00:57 | 360.18 | [CPO 產值大爆發 思渤科技助攻企業搶攻市場商機](https://technews.tw/2026/11/02/cybernet-cpo-npo-ansys-optimus/) |
| 补扫 | 11-02 10:10:00 | 11-02 15:00:57 | 290.97 | [遊戲「AI 重製潮」踢鐵板，Anthropic 出手防堵 Claude 踩版權紅線](https://technews.tw/2026/11/02/anthropic-updates-claude-banning-ai-game-remakes/) |
| 补扫 | 11-02 11:40:36 | 11-02 15:00:57 | 200.37 | [DeepSeek 發表程式設計代理 Super-Agent 不需指令即可主動修錯，正面挑戰 Claude Code](https://technews.tw/2026/11/02/deepseek-unveils-ai-super-agent-in-coding-race-with-openai-anthropic/) |
| 补扫 | 11-02 08:10:01 | 11-02 15:00:57 | 410.95 | [AI 寫程式又快又多，漏洞風險與「責任歸屬」成企業開發新難題](https://finance.technews.tw/2026/11/02/ai-makes-coding-faster-but-who-is-responsible-for-what-it-produces/) |
| 补扫 | 11-02 08:01:57 | 11-02 15:00:57 | 419.02 | [駭客也開始用 AI 了，企業的資安防護跟得上嗎？](https://infosecu.technews.tw/2026/11/02/hackers-are-using-ai-can-corporate-cybersecurity-keep-up/) |
| 补扫 | 11-02 14:40:23 | 11-02 15:00:57 | 20.58 | [國會版圖恐重劃，OpenAI 提前布局：強制 AI 安全防護、推 AI 運算園區創地方雙贏](https://technews.tw/2026/11/02/openai-post-election-washington-dc-pr-strategy/) |
| 补扫 | 11-02 11:20:33 | 11-02 15:00:57 | 220.42 | [Anthropic 衝刺 11 月上市估值上看 2 兆美元，面試竟拋出靈魂提問：股價歸零怎麼辦？](https://finance.technews.tw/2026/11/02/anthropic-asks-candidates-about-shares-falling-to-zero/) |
| 补扫 | 11-02 08:40:37 | 11-02 15:00:57 | 380.35 | [換臉、改身材、拿去訓練 AI 還不另付費？路透揭時尚品牌合約條款](https://technews.tw/2026/11/02/face-swaps-and-digital-clones/) |
| 补扫 | 11-02 07:00:45 | 11-02 15:00:57 | 480.22 | [5G 功耗最佳化：從晶片到網路之系統解方](https://technews.tw/2026/11/02/5g-power-optimization-system-level-solutions-chip-to-network/) |
| 补扫 | 11-02 11:50:35 | 11-02 15:00:57 | 190.38 | [透過 robots.txt 一鍵拒絕 AI 訓練，四大科技巨頭宣布支援 IETF 共通標準草案](https://infosecu.technews.tw/2026/11/02/ai-training-scraping-new-regulations-response/) |
| 补扫 | 11-02 08:15:33 | 11-02 15:00:57 | 405.42 | [NAND Flash 廠鎧俠純益估飆 14 倍、2027 年產能「售罄」](https://finance.technews.tw/2026/11/02/kioxia-consolidated-financial-results-for-the-six-months-ended-september-30-2026/) |
| 补扫 | 11-02 13:00:58 | 11-02 15:00:57 | 120.00 | [需求激增 三井金屬提前擴產 AI DC 用銅箔](https://technews.tw/2026/11/02/mitsui-kinzoku-accelerates-capacity-expansion-for-ai-data-center-copper-foil/) |
| 补扫 | 11-02 14:50:52 | 11-02 15:00:57 | 10.10 | [SK 海力士 Q4 營益估首破 100 兆韓圜 券商喊買進](https://finance.technews.tw/2026/11/02/sk-hynix-q4-operating-profit-seen-surpassing-100-trillion-won-for-first-time/) |
| 补扫 | 11-02 11:30:05 | 11-02 15:00:57 | 210.88 | [AI 算力的盡頭是核能？全球在建機組飆升至 79 座創 40 年新高](https://technews.tw/2026/11/02/ai-power-shortage-triggers-nuclear-energy-revival/) |
| 补扫 | 11-02 14:10:39 | 11-02 15:00:57 | 50.32 | [Linus Torvalds 釋出最新 Linux 核心 RC 版，肯定 AI 參與開發模式漸趨「健康」](https://technews.tw/2026/11/02/linus-torvalds-says-linux-kernel-is-out-of-stabilization-phase-and-using-much-healthier-model-for-ai-contributions/) |
| 补扫 | 11-02 10:55:59 | 11-02 15:00:57 | 244.98 | [李在鎔視察三星橫濱先進封裝實驗室，強化日本材料與設備供應鏈合作](https://technews.tw/2026/11/02/samsung-chief-visits-advanced-package-lab-in-japan/) |
| 补扫 | 11-02 08:25:32 | 11-02 15:00:57 | 395.43 | [AI 回報疑慮衝擊 OpenAI 融資目標大縮水百億美元](https://finance.technews.tw/2026/11/02/openai-slashes-funding-target-by-10bn-as-tech-anxieties-mount/) |
| 补扫 | 11-02 07:40:38 | 11-02 15:00:57 | 440.33 | [讓 AI 代理拿假金鑰辦事，機密管理新創 Infisical 完成 4 千萬美元 B 輪募資](https://finance.technews.tw/2026/11/02/this-600-million-startup-is-helping-keep-company-secrets-away-from-ai-agents/) |
| 补扫 | 11-02 08:50:41 | 11-02 15:00:57 | 370.28 | [中國女主管與黃仁勳關係密切疑為北京間諜，川普：會仔細了解](https://technews.tw/2026/11/02/chinese-female-executive-close-ties-jensen-huang-suspected-beijing-spy-trump-vows-investigate/) |
| 补扫 | 11-02 10:50:41 | 11-02 15:00:57 | 250.28 | [微軟 AI 執行長蘇萊曼示警，打造有意識的 AI 非常魯莽應立即停止](https://technews.tw/2026/11/02/microsoft-ai-chief-ai-is-not-conscious-and-we-should-keep-it-that-way/) |
| 补扫 | 11-02 11:55:57 | 11-02 15:00:57 | 185.02 | [Anthropic 恐遭評垃圾級 上市前永續期貨月摔 20%](https://finance.technews.tw/2026/11/02/anthropic-may-not-secure-investment-grade-rating-after-ipo/) |
| 补扫 | 11-02 08:30:07 | 11-02 15:00:57 | 390.85 | [Palantir 前高層創立 Dream 獲 a16z 領投 1,700 萬美元，以 AI 世界模型自動設計建築](https://technews.tw/2026/11/02/ex-palantir-execs-launch-dream-with-17m-from-a16z-to-build-ai-world-models-for-construction/) |
| 补扫 | 11-02 09:45:36 | 11-02 15:00:57 | 315.37 | [驚豔 AI ASIC 領域執行力與新客戶拓展，外資給聯發科最高目標價 7,300 元](https://finance.technews.tw/2026/11/02/foreign-investors-have-given-mediatek-a-target-price-of-up-to-nt7100/) |
| 补扫 | 11-02 09:55:53 | 11-02 15:00:57 | 305.08 | [衛浴大廠 TOTO 砸 600 億日圓建新廠，量產次世代半導體靜電吸盤](https://technews.tw/2026/11/02/toto-invests-60-billion-jpy-to-build-new-plant-mass-producing-next-generation-semiconductor-electrostatic-chucks/) |
| 补扫 | 11-02 11:47:07 | 11-02 15:00:57 | 193.85 | [搶資料中心記憶體供應！OpenAI 執行長 Sam Altman 赴韓見三星高層](https://technews.tw/2026/11/02/sam-altman-meet-samsung-leadership/) |
| 补扫 | 11-02 10:27:18 | 11-02 15:00:57 | 273.67 | [矽光子需求旺、產能持續擴張！高盛點讚聯亞，目標價喊上 4,490 元](https://technews.tw/2026/11/02/goldman-sachs-see-landmark-2026-q3-financial-report/) |
| 补扫 | 11-02 09:10:40 | 11-02 15:00:57 | 350.30 | [AI 股崩 歷史：選舉年晶片股 10 月底至隔年 6 月皆漲](https://finance.technews.tw/2026/11/02/ai-stocks-just-lost-5-trillion-but-history-says-the-stock-markets-sweetest-stretch-starts-now/) |
| 补扫 | 11-02 11:45:02 | 11-02 15:00:57 | 195.93 | [AI 駭客掀黑色十月、恐成新常態？資安股不漲反跌](https://infosecu.technews.tw/2026/11/02/ai-hackers-trigger-black-october-new-normal-cybersecurity-stocks-decline/) |
| 补扫 | 11-02 09:50:28 | 11-02 15:00:57 | 310.50 | [擺脫地球缺電與散熱瓶頸，馬斯克霸氣宣告：AI 算力主戰場在太空](https://technews.tw/2026/11/02/elon-musk-ai-compute-empire/) |
| 补扫 | 11-02 10:20:59 | 11-02 15:00:57 | 279.98 | [AI 變現能力堪慮 標普下調 Meta 信評展望至負面](https://finance.technews.tw/2026/11/02/sp-global-revises-metas-outlook-to-negative-on-ai-spending/) |
| 补扫 | 11-01 17:00:03 | 11-02 15:00:57 | 1320.92 | [學者授四招破解假圖，點名食物照是 AI 最大死穴](https://infosecu.technews.tw/2026/11/01/identifying-flaws-ai-generated-photos/) |
| 补扫 | 11-02 12:00:16 | 11-02 15:00:57 | 180.70 | [ASML 將「員工士氣」納入高層薪酬指標，搶救下滑敬業度](https://technews.tw/2026/11/02/asmls-ceo-is-putting-his-paycheck-on-the-line-for-employee-morale/) |
| 补扫 | 11-02 11:10:39 | 11-02 15:00:57 | 230.32 | [OpenAI 收緊青少年武器資訊限制，ChatGPT 將強制中止暴力對話](https://technews.tw/2026/11/02/openai-adds-stricter-limits-for-teens-asking-about-firearms/) |
| 补扫 | 11-02 07:20:23 | 11-02 15:00:57 | 460.58 | [蓋洛普調查：AI 職場紅利分配不均，高學歷與高薪族群受惠最多](https://technews.tw/2026/11/02/gallup-ai-survey-10-findings-on-how-ai-helps-workers/) |
| 补扫 | 11-02 12:30:59 | 11-02 15:00:57 | 149.98 | [蘋果 iOS 27.2 導入 Google AI 浮水印，修圖痕跡寫入畫素但自家 App 竟無法直接驗證](https://infosecu.technews.tw/2026/11/02/iphone-ai-watermark-potential-concerns/) |
| 补扫 | 11-02 12:40:49 | 11-02 15:00:57 | 140.15 | [上週全球股市資金大挪移 美股失血、亞股吸金](https://finance.technews.tw/2026/11/02/us-equity-funds-see-net-outflows/) |
| 补扫 | 11-02 08:45:54 | 11-02 15:00:57 | 375.07 | [量子運算邁入分子時代，NVision 攜手 HyperLight 將分子量子位元整合至光子晶片](https://technews.tw/2026/11/02/nvision-breakthrough-molecular-qubit-technology/) |
| 补扫 | 11-02 10:00:17 | 11-02 15:00:57 | 300.68 | [High-NA EUV 發展未歇，英特爾率先採用、台積電與三星推進量產時程](https://technews.tw/2026/11/02/high-nas-obituary-was-premature/) |
| 补扫 | 11-02 07:00:59 | 11-02 15:00:57 | 479.98 | [長鑫存儲正面回應美光質疑，指第六代 4F² DRAM 工程樣品年底前送樣](https://technews.tw/2026/11/02/changxin-memory-responds-directly-to-microns-accusations/) |
| 补扫 | 11-02 14:20:29 | 11-02 15:00:57 | 40.48 | [韓國 2027 預算案規模 20 兆創新高，聚焦民生、AI 及國防](https://finance.technews.tw/2026/11/02/south-korea-2027-budget-record-20-trillion-public-welfare-ai-defense/) |
| 补扫 | 11-02 13:57:13 | 11-02 15:00:57 | 63.75 | [CoWoS 供應瓶頸浮現，Intel EMIB-T 開闢另一條封裝路線](https://technews.tw/2026/11/02/cowos-supply-bottlenecks-intel-emib-t-alternative-packaging/) |
| 补扫 | 11-02 07:50:44 | 11-02 15:00:57 | 430.23 | [從股價剩 8 元到打入輝達供應鏈，一詮靠黑手功夫擠進 AI 散熱賽道](https://finance.technews.tw/2026/11/02/ichiun/) |
| 补扫 | 11-02 10:26:03 | 11-02 15:00:57 | 274.92 | [火星生技導入 AI 輔助篩選機能配方！研發突破複方設計瓶頸獲國際肯定](https://finance.technews.tw/2026/11/02/diu/) |
| 补扫 | 11-02 12:10:51 | 11-02 15:00:57 | 170.12 | [Meta AI 代理 Muse 聲勢高漲，企業端實際訂單仍寥寥可數](https://finance.technews.tw/2026/11/02/is-metas-muse-the-start-of-the-business-to-agent-era/) |
| 补扫 | 11-02 13:34:36 | 11-02 15:00:57 | 86.37 | [新型 Windows 惡意軟體曝光，利用 Claude 評估資料價值後自動刪除](https://infosecu.technews.tw/2026/11/02/volta-infostealer-leverages-llms-to-identify-high-value-stolen-assets/) |
| 补扫 | 11-02 10:00:14 | 11-02 15:00:57 | 300.73 | [新唐第三季虧損進一步擴大，大摩給不如大盤評等目標價 95 元](https://finance.technews.tw/2026/11/02/nuvotons-third-quarter-losses-widened-further/) |
| 补扫 | 11-02 07:00:09 | 11-02 15:00:57 | 480.82 | [AI 為什麼會數錯字母、亂引用來源？關鍵在「預測」而非「理解」](https://technews.tw/2026/11/02/everyday-small-tasks-ai-still-struggles-with/) |
| 补扫 | 11-02 11:15:18 | 11-02 15:00:57 | 225.67 | [AI 首度操刀活體腦部手術，生技新創讓 Claude 自主決策並執行小鼠基因治療](https://technews.tw/2026/11/02/an-ai-agent-injected-drugs-into-a-live-mouses-brain/) |
| 实时 | 11-02 15:00:09 | 11-02 15:01:18 | 1.16 | [攻 AI 電力 三菱重工傳擴增美國燃氣渦輪機產能](https://technews.tw/2026/11/02/mitsubishi-heavy-industries-expands-us-gas-turbine-capacity-tackle-ai-power-demand/) |

### thelec_semiconductors_rss — The Elec Semiconductor RSS

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-02 12:00:01 | 11-02 15:01:38 | 181.62 | [엔비디아, 9일부터 AI 데이 서울 2026 개최...최신 AI 인프라 등 공개](https://www.thelec.kr/news/articleView.html?idxno=64663) |
| 实时 | 11-02 15:27:41 | 11-02 15:29:02 | 1.36 | [삼성전자, 2030년 8인치 파운드리 완전 철수](https://www.thelec.kr/news/articleView.html?idxno=64709) |
| 实时 | 11-02 15:30:11 | 11-02 15:33:02 | 2.86 | [이재용 삼성전자 회장, 日 첨단 패키징 연구거점 방문](https://www.thelec.kr/news/articleView.html?idxno=64640) |
| 实时 | 11-02 15:49:51 | 11-02 15:57:02 | 7.19 | [코아시아세미, 삼성 파운드리 4나노 반도체 출하 시작](https://www.thelec.kr/news/articleView.html?idxno=64651) |
| 实时 | 11-02 15:51:33 | 11-02 15:57:02 | 5.49 | [AMD, 아드레날린 26.10.2 버전 출시...시스템 종료 현상 수정](https://www.thelec.kr/news/articleView.html?idxno=64711) |

### tomshardware_rss — Tom's Hardware RSS

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-01 23:00:00 | 11-02 15:01:38 | 961.64 | [Biwin M560 2TB SSD Review: The DRAM-less drive to beat](https://www.tomshardware.com/pc-components/ssds/biwin-m560-2tb-ssd-review) |
| 补扫 | 11-01 20:33:52 | 11-02 15:01:38 | 1107.77 | [AI agent swarms unleashed in Minecraft to ‘build a thriving civilization’](https://www.tomshardware.com/tech-industry/artificial-intelligence/ai-agent-swarms-unleashed-in-minecraft-to-build-a-thriving-civilization-50-agent-teams-fail-after-falling-into-paralyzing-bureaucracy) |
| 补扫 | 11-02 05:00:00 | 11-02 15:01:38 | 601.64 | [New AI-driven Windows malware feeds stolen data to Claude to rank victims’ assets by value, then deletes itself](https://www.tomshardware.com/tech-industry/cyber-security/new-ai-driven-windows-malware-feeds-stolen-data-to-claude-to-rank-victims-assets-by-value-then-deletes-itself-payload-builds-custom-target-assessment-can-switch-models-if-one-refuses-or-skip-the-ai-step-entirely) |
| 补扫 | 11-01 19:30:00 | 11-02 15:01:38 | 1171.64 | [USAF successfully tests dropping SpaceX Starfall cargo drop pods from space](https://www.tomshardware.com/tech-industry/space/usaf-successfully-tests-dropping-spacex-starfall-cargo-drop-pods-from-space-2-200-pound-capsule-drops-military-supplies-in-the-pacific-to-test-starcraft-like-supply-drops) |
| 补扫 | 11-01 23:05:00 | 11-02 15:01:38 | 956.64 | [Brazilian lawyers busted for attempting to manipulate court's AI with prompt injection hidden in filing](https://www.tomshardware.com/tech-industry/artificial-intelligence/brazilian-lawyers-busted-for-attempting-to-manipulate-courts-ai-with-prompt-injection-hidden-in-filing-courts-ai-flags-hidden-text-that-demanded-superficial-legal-scrutiny-lawyers-fined-usd16-000) |
| 补扫 | 11-01 20:00:00 | 11-02 15:01:38 | 1141.64 | [Voyager 1 probe to hit historic one light-day distance from Earth in 17 days — 49-year-old spacecraft travels 16 billion miles](https://www.tomshardware.com/tech-industry/space/voyager-1-probe-to-hit-historic-one-light-day-distance-from-earth-in-17-days-49-year-old-spacecraft-travels-16-billion-miles-and-survives-nearly-ten-times-the-length-of-its-original-five-year-mission) |
| 补扫 | 11-01 19:00:00 | 11-02 15:01:38 | 1201.64 | [College student unofficially breaks world record for fastest jet-powered drone with 491 mph aircraft built in his dorm](https://www.tomshardware.com/tech-industry/drones/college-student-unofficially-breaks-world-record-for-fastest-jet-powered-drone-with-491-mph-aircraft-built-in-his-dorm-reaper-aims-to-grab-the-usd800-000-boom-prize) |
| 补扫 | 11-02 02:09:28 | 11-02 15:01:38 | 772.17 | [Snag a Nintendo Switch 2 for only $469 while stock lasts — $30 off a system that's not often on sale](https://www.tomshardware.com/pc-components/snag-a-nintendo-switch-2-for-only-usd469-while-stock-lasts-usd30-off-a-system-thats-not-often-on-sale) |
| 补扫 | 11-01 22:21:23 | 11-02 15:01:38 | 1000.26 | [Sam Altman meets with Samsung leadership to secure more RAM for data centers amid crushing shortages](https://www.tomshardware.com/tech-industry/data-centers/sam-altman-travels-to-south-korea-to-meet-samsung-leadership-to-secure-more-ram-for-data-centers-amid-crushing-shortages-openai-ceo-meets-samsung-leadership-to-discuss-future-ai-data-centers) |
| 补扫 | 11-02 02:45:00 | 11-02 15:01:38 | 736.64 | [GOG explores reviving physical PC game releases](https://www.tomshardware.com/video-games/gog-explores-reviving-physical-pc-game-releases-digital-storefront-needs-publisher-approvals-has-to-figure-out-the-logistics-and-legal-challenges) |
| 补扫 | 11-01 20:30:00 | 11-02 15:01:38 | 1111.64 | [California county deploys autonomous firefighting drones with 500-gallon foam payload to attack wildfires](https://www.tomshardware.com/tech-industry/drones/california-county-secures-autonomous-firefighting-drones-with-500-gallon-foam-payload-to-deal-with-wildfires-five-drone-strike-team-can-be-controlled-by-a-single-operator-conduct-nighttime-ops-and-access-difficult-terrain) |
| 补扫 | 11-01 21:30:00 | 11-02 15:01:38 | 1051.64 | [Elon Musk says Neuralink's 'BlindSight' is ready to cure blindness](https://www.tomshardware.com/peripherals/wearable-tech/elon-musk-says-neuralink-is-ready-to-cure-blindness-says-blindsight-will-enable-even-those-who-are-completely-blind-or-have-never-seen-to-see) |
| 补扫 | 11-01 23:00:00 | 11-02 15:01:38 | 961.64 | [Anycubic and Thingiverse announce exclusive 3D printing partnership](https://www.tomshardware.com/3d-printing/anycubic-and-thingiverse-announce-exclusive-3d-printing-partnership-makeronline-platform-to-offer-1-click-multicolor-3mf-models) |
| 补扫 | 11-01 22:00:00 | 11-02 15:01:38 | 1021.64 | [Open-source USB tool unlocks Nvidia's half-speed GPU AI limiter on Windows](https://www.tomshardware.com/pc-components/gpus/open-source-usb-tool-unlocks-nvidias-half-speed-gpu-ai-limiter-on-windows-lifts-gpus-fp16-math-performance-up-to-65-percent-uefi-unlocker-covers-rtx-20-30-and-40-series-desktops-and-laptops) |
| 补扫 | 11-01 21:55:26 | 11-02 15:01:38 | 1026.21 | [Police Chief physically unplugs Flock cameras after the company ignores contract cancellation](https://www.tomshardware.com/tech-industry/big-tech/police-chief-physically-unplugs-flock-cameras-after-the-company-ignores-contract-cancellation-city-takes-matters-into-its-own-hands-amid-privacy-controversies) |
| 补扫 | 11-01 21:00:00 | 11-02 15:01:38 | 1081.64 | [Lenovo's 'encrypted' fingerprint USB drive is just an unencrypted SD card in a fancy case](https://www.tomshardware.com/tech-industry/cyber-security/lenovo-fingerprint-usb-stick-sold-as-aes-256-encrypted-had-a-plain-sd-card-inside-ai-assisted-follow-up-finds-a-backdoor-and-hardcoded-keys-in-other-secure-usb-products) |
| 补扫 | 11-01 23:53:45 | 11-02 15:01:38 | 907.89 | [Walmart honors massive shelf price mistake, lucky buyer gets two $2,299 CyberPowerPC gaming rigs for $1,099 each](https://www.tomshardware.com/desktops/gaming-pcs/lucky-pc-buyer-gets-usd1-200-discount-on-9900x3d-gaming-pc-walmart-honors-usd1-099-shelf-price-on-usd2-299-unit) |
| 补扫 | 11-01 22:30:00 | 11-02 15:01:38 | 991.64 | [Bambu Lab's Black Friday sale cuts up to $300 on the flagship H2C printer and more](https://www.tomshardware.com/3d-printing/bambu-labs-black-friday-sale-cuts-up-to-usd300-on-the-flagship-h2c-printer-and-more-deep-cuts-across-companys-3d-printer-and-filament-lineup) |
| 实时 | 11-02 19:52:11 | 11-02 19:54:24 | 2.22 | [Grab a $450 saving on this 4K-ready gaming PC with an RTX 5070 Ti and 32GB DDR5 right now](https://www.tomshardware.com/desktops/gaming-pcs/grab-a-usd450-saving-on-this-4k-ready-gaming-pc-with-an-rtx-5070-ti-and-32gb-ddr5-right-now-abs-rig-from-newegg-ships-with-24-core-intel-cpu-and-a-2tb-ssd-for-usd2-549-99) |
| 实时 | 11-02 20:54:57 | 11-02 20:59:24 | 4.45 | [Triple your Nintendo Switch 2's storage for just $99](https://www.tomshardware.com/pc-components/microsd-cards/triple-your-nintendo-switch-2s-storage-for-just-usd99-save-usd50-on-this-ultra-fast-512gb-samsung-p9-microsd-card-for-a-limited-time-only) |
| 实时 | 11-02 22:00:00 | 11-02 22:04:24 | 4.40 | [Apple finally has a touchscreen MacBook. Why now?](https://www.tomshardware.com/laptops/macbooks/apple-finally-has-a-touchscreen-macbook-why-now) |
| 实时 | 11-02 23:19:12 | 11-02 23:25:24 | 6.20 | ['America hates data centers' — New data highlights public sentiment](https://www.tomshardware.com/tech-industry/data-centers/america-hates-data-centers-new-database-reveals-more-than-90-percent-of-10-000-public-comments-are-negative-only-21-percent-mention-ai-73-percent-of-meetings-had-no-data-center-supporters-water-is-the-number-one-concern) |

### unmannedairspace_counteruas — Unmanned Airspace / Counter-UAS

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 16:41:01 | 11-02 16:42:32 | 1.52 | [Allen Control Systems and CX2 partner to provide integrated C-UAS capabilities](https://www.unmannedairspace.info/counter-uas-systems-and-policies/allen-control-systems-and-cx2-partner-to-provide-integrated-c-uas-capabilities/) |
| 实时 | 11-02 17:11:41 | 11-02 17:15:32 | 3.85 | [Ukraine launches competition to develop radars, forms closer ties with NATO](https://www.unmannedairspace.info/counter-uas-systems-and-policies/ukraine-launches-competition-to-develop-radars-forms-closer-ties-with-nato/) |
| 实时 | 11-02 19:10:02 | 11-02 19:11:32 | 1.50 | [Jersey Airport reports unauthorised drone incidents](https://www.unmannedairspace.info/counter-uas-systems-and-policies/jersey-airport-reports-unauthorised-drone-incidents/) |

### utility_dive — Utility Dive

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 18:00:00 | 11-02 18:01:43 | 1.73 | [Rethinking the outage map for a multi-hazard world](https://www.utilitydive.com/spons/rethinking-the-outage-map-for-a-multi-hazard-world/872890/) |
| 实时 | 11-02 18:00:00 | 11-02 18:01:43 | 1.73 | [Utilities need to become project developers. Are they ready?](https://www.utilitydive.com/spons/utilities-need-to-become-project-developers-are-they-ready/831847/) |

### venturebeat_software — VentureBeat / Orchestration, Data & Security

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-02 04:00:00 | 11-02 15:00:49 | 660.83 | [AI agents don't have to break security controls to go rogue. Here's how to keep them in check](https://venturebeat.com/security/ai-agents-dont-have-to-break-security-controls-to-go-rogue-heres-how-to-keep-them-in-check) |
| 实时 | 11-02 23:30:00 | 11-02 23:31:25 | 1.42 | [When AI agents agree on the data but not what it means](https://venturebeat.com/data/when-ai-agents-agree-on-the-data-but-not-what-it-means) |

### wccftech_hardware — Wccftech / AI, Industry & Hardware

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-01 20:31:24 | 11-02 15:00:59 | 1109.59 | [Snapdragon X2 Elite Users Report Up To 87% Slower LLM Performance After Adreno Driver Update, Premiere Renders Also Taking 6x Longer](https://wccftech.com/snapdragon-x2-elite-adreno-driver-32-0-181-0-slowdown-local-ai-premiere/) |
| 补扫 | 11-01 23:19:59 | 11-02 15:01:15 | 941.27 | [Modded RTX 2080 Ti With 22 GB Memory Delivers Up To 73% Boost At 4K Compared To 11 GB Variant, Benchmarks Reveal](https://wccftech.com/modded-rtx-2080-ti-with-22-gb-memory-delivers-up-to-64-boost-at-4k-compared-to-11-gb/) |
| 补扫 | 11-01 17:10:50 | 11-02 15:01:15 | 1310.42 | [NVIDIA Is Reportedly Eyeing Teflon, The Non-Stick Cookware Material, To Fix AI Server Signal Loss In Next-Gen NVLink Switch Boards](https://wccftech.com/nvidia-eyeing-teflon-ptfe-ai-server-signal-loss-nvlink-switch-boards/) |
| 补扫 | 11-02 00:11:52 | 11-02 15:01:15 | 889.39 | [Acer Predator Undercuts Every Other 500 Hz OLED Monitor With The Lowest-Ever $499 Price Tag](https://wccftech.com/acer-predator-27-inch-500-hz-oled-monitor-drops-to-499/) |
| 补扫 | 11-01 20:00:50 | 11-02 15:01:15 | 1140.42 | [Modder Spent 7 Years To Complete His “Perfect” Cable-Less Wooden Setup That Has A Radiator Running In The Basement](https://wccftech.com/modder-spends-7-years-to-complete-his-perfect-cable-less-wood-setup/) |
| 补扫 | 11-02 01:46:03 | 11-02 15:01:15 | 795.20 | [G.Skill 32 GB DDR5 Now Costs $156 When Purchased With Ryzen 9850X3D And B850 Motherboard Bundle](https://wccftech.com/g-skill-32-gb-ddr5-now-costs-156-when-purchased-with-ryzen-9850x3d-and-b850-motherboard-bundle/) |
| 实时 | 11-02 16:25:26 | 11-02 16:25:37 | 0.19 | [Amazon Is Allegedly Canceling GPU Orders And, In Rare Cases, Closing Accounts For Purchasing Compute Hardware; Tim Sweeney Also A Victim Of This Practice](https://wccftech.com/amazon-allegedly-canceling-gpu-orders-closing-accounts-compute-hardware-tim-sweeney/) |
| 实时 | 11-02 17:49:34 | 11-02 17:49:57 | 0.39 | [Elon Musk Rumored To Soften Stance On TSMC For Terafab After Calling It A Subcontractor At Best, With Operational Control And Joint Investment On The Table](https://wccftech.com/elon-musk-rumored-to-soften-stance-on-tsmc-for-terafab-after-calling-it-a-subcontractor-at-best-with-operational-control-and-joint-investment-on-the-table/) |
| 实时 | 11-02 17:49:16 | 11-02 17:50:37 | 1.36 | [Intel’s Regression: Accuracy Of Its Guidance Falters, As 18A Capacity Gets Cannibalized To Feed The Older Intel 3 Process](https://wccftech.com/intels-regression-accuracy-of-its-guidance-falters-as-18a-capacity-gets-cannibalized-to-feed-the-older-intel-3-process/) |
| 实时 | 11-02 19:25:12 | 11-02 19:25:37 | 0.42 | [AMD Plans RDNA 5+ GPU Architecture For 2029, An Optimized Follow-Up To RDNA 5 That Could Debut Inside Zen 7 “Grimlock” APUs](https://wccftech.com/amd-rdna-5-gpu-architecture-2029-zen-7-grimlock-apus/) |
| 实时 | 11-02 19:59:10 | 11-02 19:59:37 | 0.45 | [NVIDIA RTX 3060 Takes Back Number One Spot From RTX 5070 While 32 GB System RAM Took A Massive Hit According To The Latest Steam Hardware Survey](https://wccftech.com/steam-hardware-survey-october-2026/) |
| 实时 | 11-02 20:13:16 | 11-02 20:13:37 | 0.35 | [October 2026 Steam Hardware Survey: 16 GB RAM Reclaims Top Spot, Intel Gains CPU Share, Linux Approaches 4%](https://wccftech.com/october-2026-steam-hardware-survey-16-gb-ram-reclaims-top-spot-intel-gains-cpu-share-linux-approaches-4/) |
| 实时 | 11-02 20:35:10 | 11-02 20:35:37 | 0.46 | [NVIDIA GeForce RTX 5080 24 GB May Miss January 2027 Launch As Board Partners Still Await Design Kits](https://wccftech.com/nvidia-geforce-rtx-5080-24-gb-may-miss-january-2027-launch-as-board-partners-still-await-design-kits/) |
| 实时 | 11-02 22:03:35 | 11-02 22:03:57 | 0.37 | [TSMC’s 1-nanometer Manufacturing Site Likely Tipped By Taiwan’s Minister In Discussion That Included Robots](https://wccftech.com/tsmcs-1-nanometer-manufacturing-site-likely-tipped-by-taiwans-minister-in-discussion-that-included-robots/) |
| 实时 | 11-02 23:17:44 | 11-02 23:18:37 | 0.89 | [Windows 11 Can Now Reinstall Itself From The Cloud Even When The OS Fails To Boot](https://wccftech.com/windows-11-can-now-reinstall-itself-from-the-cloud-even-when-the-os-fails-to-boot/) |
| 实时 | 11-02 23:20:20 | 11-02 23:20:37 | 0.29 | [Vendors Are Now Recommending Distributors To Focus On Older RTX 30 Series And AMD GPUs As RTX 50 Series Faces Shortages](https://wccftech.com/vendors-are-now-recommending-distributors-to-focus-on-older-rtx-30-series-and-amd-gpus/) |
| 实时 | 11-02 23:50:03 | 11-02 23:50:37 | 0.58 | [NVIDIA RTX 5090 Records A Staggering 370% Hike In Price Since Launch While Every RTX 50 GPU Saw A Price Hike In 40-120% Range](https://wccftech.com/nvidia-rtx-5090-records-a-staggering-370-hike-in-price-since-launch/) |

### world_nuclear_news — World Nuclear News

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 实时 | 11-02 22:37:39 | 11-02 23:01:08 | 23.49 | [First concrete poured for unit 5 at Haiyang plant](https://www.world-nuclear-news.org/articles/first-concrete-poured-unit-5-haiyang-plant) |

### yahoo_finance_news — Yahoo Finance News

| 模式 | 发布时间 | 首次抓取 | 差值(分) | 标题 |
| --- | --- | --- | --- | --- |
| 补扫 | 11-01 19:49:09 | 11-02 15:00:09 | 1151.01 | [Top Investor: CoreWeave Locked In Compute “Way, Way Under Market.” Rising GPU Prices Won’t Save It](https://www.tikr.com/blog/top-investor-coreweave-locked-in-compute-way-way-under-market-rising-gpu-prices-wont-save-it?.tsrc=rss&ref=yahoofinance&tlnk=yf-261101-2mcq2gcm) |
| 补扫 | 11-02 03:35:00 | 11-02 15:00:09 | 685.16 | [Is Nebius Stock a Buy After Its 36% Pullback?](https://www.fool.com/investing/2026/11/01/is-nebius-stock-a-buy-after-its-36-pullback/?.tsrc=rss) |
| 补扫 | 11-01 19:31:14 | 11-02 15:00:34 | 1169.34 | [US Hedge Funds Just Sold Every AI Stock They Bought Since 2025](https://beincrypto.com/hedge-funds-dump-ai-stocks/?.tsrc=rss) |
| 补扫 | 11-02 00:30:00 | 11-02 15:00:34 | 870.57 | [There's a $12,000 Reason to Consider Investing in QQQ Over SPY, but It Comes With a Caveat](https://www.kiplinger.com/what-1000-invested-in-qqq-etf-spy-etf-is-worth-now?.tsrc=rss) |
| 补扫 | 11-01 22:50:00 | 11-02 15:00:34 | 970.57 | [Why Micron Stock Looks Cheap After a Record Year](https://www.fool.com/investing/2026/11/01/why-micron-stock-looks-cheap-after-a-record-year/?.tsrc=rss) |
| 补扫 | 11-02 02:20:00 | 11-02 15:00:34 | 760.57 | [Here's What $6,000 Invested in Micron Could Be Worth in 2028](https://www.fool.com/investing/2026/11/01/heres-what-6000-invested-in-micron-could-be-worth/?.tsrc=rss) |
| 补扫 | 11-02 00:16:38 | 11-02 15:00:34 | 883.94 | [SK hynix Just Dropped 33% in a Month. Is It Time to Sell, or Load Up?](https://www.tikr.com/blog/sk-hynix-just-dropped-33-in-a-month-is-it-time-to-sell-or-load-up?.tsrc=rss&ref=yahoofinance&tlnk=yf-261101-wv59jg34) |
| 补扫 | 11-02 00:57:37 | 11-02 15:00:34 | 842.95 | [ICE And OKX's Tokenized Stock Venue Hits A Wall As Nvidia, Nike Join Growing List Of Objectors](https://stocktwits.com/news-articles/markets/cryptocurrency/ice-and-okx-s-tokenized-stock-venue-hits-a-wall-as-nvidia-nike-join-growing-list-of-objectors/cNGewptR927?.tsrc=rss) |
| 补扫 | 11-02 00:14:06 | 11-02 15:00:34 | 886.47 | [Sandisk Sinks 13% Last Friday After a Beat: Kioxia Says the Flash Price Surge Is Fading](https://www.tikr.com/blog/sandisk-sinks-13-last-friday-after-a-beat-kioxia-says-the-flash-price-surge-is-fading?.tsrc=rss&ref=yahoofinance&tlnk=yf-261101-v7m7rqjf) |
| 补扫 | 11-02 10:09:08 | 11-02 15:00:34 | 291.44 | [MU, SNDK, DRAM Inch Higher Overnight After Mixed Week: Japan’s Kioxia Plunges After Cautious Memory Outlook](https://stocktwits.com/news-articles/markets/equity/mu-sndk-dram-inch-higher-overnight-after-mixed-week-japan-s-kioxia-plunges-after-cautious-memory-outlook/cNG9K4wR92O?.tsrc=rss) |
| 补扫 | 11-01 21:30:00 | 11-02 15:00:49 | 1050.82 | [Nebex CEO: NASA wants a commercial space economy. Here’s how it can enable one](https://finance.yahoo.com/economy/policy/articles/nebex-ceo-nasa-wants-commercial-133000127.html?.tsrc=rss) |
| 补扫 | 11-01 19:52:00 | 11-02 15:00:49 | 1148.82 | [$10,000 in Rocket Lab at Its Record High Is Worth About $3,900 Today. Here's What Gets That Money Back.](https://www.fool.com/investing/2026/11/01/10000-in-rocket-lab-at-its-record-high-is-worth-ab/?.tsrc=rss) |
| 补扫 | 11-02 10:43:25 | 11-02 15:00:49 | 257.41 | [Rocket Lab’s 64% Collapse Tests Its Space Infrastructure Strategy](https://247wallst.com/investing/2026/11/01/rocket-labs-64-collapse-tests-its-space-infrastructure-strategy/?.tsrc=rss) |
| 补扫 | 11-01 23:05:00 | 11-02 15:00:49 | 955.82 | [2 Space Stocks Down More Than 50% That Could Be the Best Buys of Q4](https://www.fool.com/investing/2026/11/01/2-space-stocks-down-more-than-50-that-could-be-the/?.tsrc=rss) |
| 补扫 | 11-01 22:23:00 | 11-02 15:01:00 | 998.02 | [Anthropic Says It's Building Its Own Data Centers. Bloom Energy Sells Exactly the Onsite Power That Requires.](https://www.fool.com/investing/2026/11/01/anthropic-says-its-building-its-own-data-centers-b/?.tsrc=rss) |
| 补扫 | 11-01 19:05:00 | 11-02 15:01:07 | 1196.13 | [Nuclear Stocks Have Crashed 35% to 88% From Their Highs. Here's What $1,000 in Each of These 5 Could Be Worth by 2030.](https://www.fool.com/investing/2026/11/01/nuclear-stocks-have-crashed-45-to-88-from-their-hi/?.tsrc=rss) |
| 补扫 | 11-01 23:00:00 | 11-02 15:01:25 | 961.42 | [Wall Street Braces for More Wild Days in Stocks After Tech Rout](https://finance.yahoo.com/markets/stocks/articles/wall-street-braces-more-wild-150000411.html?.tsrc=rss) |
| 补扫 | 11-02 12:48:15 | 11-02 15:01:25 | 133.17 | [Is a Stock-Pocalypse Coming This Week? Analysts Share Their Boldest Calls](https://beincrypto.com/stock-pocalypse-us-stock-market-crash-this-week/?.tsrc=rss) |
| 补扫 | 11-02 10:20:00 | 11-02 15:01:25 | 281.42 | [Intel Shares Collapse. Why I'm Still Not Buying the Stock](https://www.fool.com/investing/2026/11/01/intel-shares-collapse-why-im-still-not-buying-the/?.tsrc=rss) |
| 补扫 | 11-02 07:42:00 | 11-02 15:01:25 | 439.42 | [Trump Bragged About Intel Stock Gains. Now the U.S. Stake Has Lost $11 Billion](https://beincrypto.com/intel-stock-drop-us-stake-loss/?.tsrc=rss) |
| 补扫 | 11-01 23:15:02 | 11-02 15:01:25 | 946.38 | [AI Stocks Are Getting a Reality Check, But Don’t Count the Trade Out Just Yet. Here’s Why.](https://www.barchart.com/story/news/8580824/ai-stocks-are-getting-a-reality-check-but-dont-count-the-trade-out-just-yet-heres-why?.tsrc=rss) |
| 补扫 | 11-02 00:40:27 | 11-02 15:01:25 | 860.97 | [A Wall Street Firm Modeled Out an AI Crash: S&P 500 Down 30%, $20 Trillion Gone. But Here's What Happens Next](https://www.fool.com/investing/2026/11/01/a-wall-street-firm-modeled-out-an-ai-crash-s-and-p-500-down-30-usd20-trillion-gone-but-here-s-what-happens-next/?.tsrc=rss) |
| 补扫 | 11-02 12:13:11 | 11-02 15:01:25 | 168.23 | [Elon Musk Blinks? Tesla’s Terafab May Reportedly Lean on TSMC After All](https://finance.yahoo.com/technology/articles/elon-musk-blinks-tesla-terafab-041311073.html?.tsrc=rss) |
| 补扫 | 11-02 09:54:00 | 11-02 15:01:25 | 307.42 | [INTC, MSFT, META, DASH, VZ: Why Retail Traders Couldn’t Take Their Eyes Off These Stocks Last Week](https://stocktwits.com/news-articles/markets/equity/intc-msft-meta-dash-vz-why-retail-traders-couldn-t-take-their-eyes-off-these-stocks-last-week/cNG9irRR92a?.tsrc=rss) |
| 补扫 | 11-02 11:59:13 | 11-02 15:01:25 | 182.20 | [INTC, AMZN, GOOGL Stocks Rise Overnight After Brutal Week: Dan Niles Sees AI Risks Showing Up In Big Tech Earnings](https://stocktwits.com/news-articles/markets/equity/intc-amzn-googl-stocks-rise-overnight-after-brutal-week-dan-niles-sees-ai-risks-showing-up-in-big-tech-earnings/cNG9k2DR92h?.tsrc=rss) |
| 补扫 | 11-01 22:05:07 | 11-02 15:01:25 | 1016.30 | [5 Best Tech Stocks for the Next 5 Years According to Financial Media](https://finance.yahoo.com/markets/stocks/articles/5-best-tech-stocks-next-140507052.html?.tsrc=rss) |
| 实时 | 11-02 15:04:15 | 11-02 15:22:18 | 18.05 | [Is Seagate Still The Business You Bought?](https://www.trefis.com/stock/stx/articles/621844/is-seagate-still-the-business-you-bought/2026-11-02?.tsrc=rss) |
| 实时 | 11-02 15:04:06 | 11-02 15:22:18 | 18.20 | [Why Is Intel Stock Falling?](https://www.trefis.com/stock/intc/articles/621871/why-is-intel-stock-falling/2026-11-02?.tsrc=rss) |
| 实时 | 11-02 15:04:09 | 11-02 15:22:47 | 18.64 | [What Is The Case For Waiting On Rigetti Computing Stock?](https://www.trefis.com/stock/rgti/articles/621850/what-is-the-case-for-waiting-on-rigetti-computing-stock/2026-11-02?.tsrc=rss) |
| 实时 | 11-02 16:56:00 | 11-02 17:01:37 | 5.63 | [Your stock portfolio increasingly hinges on the bond market — and the midterm elections](https://www.marketwatch.com/story/your-stock-portfolio-increasingly-hinges-on-the-bond-market-and-the-midterm-elections-dc1abadb?.tsrc=rss) |
| 实时 | 11-02 18:00:00 | 11-02 18:05:08 | 5.13 | [ZKH and Intel Bring Industrial Multimodal AI to the Edge with LingLong Insight 1.0](https://finance.yahoo.com/technology/ai/articles/zkh-intel-bring-industrial-multimodal-100000600.html?.tsrc=rss) |
| 实时 | 11-02 18:18:32 | 11-02 18:25:37 | 7.10 | [S&P 500, Nasdaq Futures Rise Premarket After Wall Street’s Worst Week In Months: AAPL, PLTR, GOOGL, INTC, TGTX In Focus](https://stocktwits.com/news-articles/markets/equity/s-and-p-500-nasdaq-futures-rise-premarket-after-wall-street-s-worst-week-in-months-aapl-pltr-googl-intc-tgtx-in-focus/cNG9MJPR92w?.tsrc=rss) |
| 实时 | 11-02 19:05:31 | 11-02 19:08:37 | 3.12 | [Apple Earnings, Midterms, Jobs Data Put Markets To The Test: 3 Charts To Watch This Week](https://stocktwits.com/news-articles/markets/equity/apple-earnings-midterms-jobs-data-put-markets-to-the-test-3-charts-to-watch-this-week/cNG9HShR92U?.tsrc=rss) |
| 实时 | 11-02 20:52:01 | 11-02 21:03:38 | 11.62 | [Astera Labs Fell Harder Than the Market in the AI Selloff. Here’s What November 3 Must Show](https://www.tikr.com/blog/astera-labs-fell-harder-than-the-market-in-the-ai-selloff-heres-what-november-3-must-show?.tsrc=rss&ref=yahoofinance&tlnk=yf-261102-qx5g4q4r) |
| 实时 | 11-02 21:02:26 | 11-02 21:15:08 | 12.71 | [ASML Fell 6% in Intel’s Chip Rout. Its CEO Says Supply “Will Not Fully Meet the Demand”](https://www.tikr.com/blog/asml-fell-6-in-intels-chip-rout-its-ceo-says-supply-will-not-fully-meet-the-demand?.tsrc=rss&ref=yahoofinance&tlnk=yf-261102-3w8qqpgr) |
| 实时 | 11-02 21:05:20 | 11-02 21:26:07 | 20.80 | [Super Micro Stock Has Given Back Nearly All of Its October 21 Rally. Here’s Where the Stock Could Go](https://www.tikr.com/blog/super-micro-stock-has-given-back-nearly-all-of-its-october-21-rally-heres-where-the-stock-could-go?.tsrc=rss&ref=yahoofinance&tlnk=yf-261102-rv24gc9f) |
| 实时 | 11-02 21:20:00 | 11-02 21:28:17 | 8.30 | [My Top 3 AI Stock Picks for November](https://www.fool.com/investing/2026/11/02/my-top-3-ai-stock-picks-for-november/?.tsrc=rss) |
| 实时 | 11-02 22:00:00 | 11-02 22:12:41 | 12.69 | [Snowflake to Announce Financial Results for the Third Quarter of Fiscal 2027 on December 2, 2026](https://finance.yahoo.com/markets/stocks/articles/snowflake-announce-financial-results-third-140000170.html?.tsrc=rss) |
| 实时 | 11-02 22:45:00 | 11-02 22:48:37 | 3.63 | [Global Intelligent Edge Computing Module Market Report 2026 \\| NVIDIA, Dell, Siemens, Cisco and Intel Capitalize on Edge AI as Market Hits $12.35 Billion by 2030](https://finance.yahoo.com/technology/ai/articles/global-intelligent-edge-computing-module-144500175.html?.tsrc=rss) |
| 实时 | 11-02 23:26:00 | 11-02 23:35:07 | 9.13 | [Global Superconducting Quantum Computing Market Report 2026: Size, Trends, and Forecast \\| Capitalize on the $6.57 Billion Market by 2030 with Google, IBM, Intel, and Fujitsu](https://finance.yahoo.com/technology/ai/articles/global-superconducting-quantum-computing-market-152600279.html?.tsrc=rss) |
| 实时 | 11-02 23:28:28 | 11-02 23:39:37 | 11.16 | [AMD Just Dropped 16% in a Month. Is It Time to Sell, or Load Up?](https://www.tikr.com/blog/amd-just-dropped-16-in-a-month-is-it-time-to-sell-or-load-up?.tsrc=rss&ref=yahoofinance&tlnk=yf-261102-vw32x445) |
| 实时 | 11-02 23:50:06 | 11-02 23:58:04 | 7.97 | [COHR Stock Draws Upgrade As Marvell’s $115B Lumentum Deal Highlights ‘Value Of Scarce AI Optics’](https://stocktwits.com/news-articles/markets/equity/cohr-stock-draws-upgrade-as-marvell-s-115-b-lumentum-deal-highlights-value-of-scarce-ai-optics/cNGaRcAR9Pg?.tsrc=rss) |

