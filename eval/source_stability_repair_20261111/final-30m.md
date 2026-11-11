# 消息源稳定性修复：最终自然运行验收

窗口：UTC 2026-11-11T18:33:45.641277+00:00 — 2026-11-11T19:04:09.953015+00:00。排除 Reuters；不额外请求网站。

## 计数边界

以下是按 physical_run_id 去重的有效采集周期，不是独立网站请求数、新文章数或正文成功数；CAPTURED 可包含明确标注的 PARTIAL 首页覆盖。未到期/在途不进完成分母。对比窗口长度不同，仅比较比例，不比较绝对吞吐。

修复前正式入口 84.507%；最终窗口 99.626%（{'CAPTURED': 23471, 'FAILED': 88, 'DISPATCHED': 1}）。试验源单列 99.873%（{'CAPTURED': 789, 'FAILED': 1}）。

配置轮询间隔漂移：0。这不代表实际成功间隔均达到目标；下面按每个 unit 统计，完整 P50/P95/最大值见 `final-30m-per-unit-cadence.csv`。

## 逐入口结果

| 入口 | 模式 | 有效周期/失败 | 有效率 | 最慢unit P95 / 最大间隔(s) |
|---|---|---:|---:|---:|
| access_newswire_massive | test | 61/0 | 100.0% | 33.37 / 35.70 |
| airspaceforces_space | production | 91/0 | 100.0% | 22.96 / 25.46 |
| ans_nuclear_newswire | production | 90/0 | 100.0% | 22.77 / 28.26 |
| army_news_releases | production | 91/0 | 100.0% | 22.87 / 25.29 |
| army_news_rss | production | 91/0 | 100.0% | 23.05 / 24.53 |
| aviationweek_rocketlab | production | 91/0 | 100.0% | 23.63 / 25.26 |
| aviationweek_rss | production | 152/0 | 100.0% | 64.97 / 66.16 |
| aws_platform_marketplace_rss | production | 273/0 | 100.0% | 23.52 / 25.68 |
| barrons_ticker_news | production | 1817/0 | 100.0% | 24.07 / 36.37 |
| benzinga_news | production | 1209/0 | 100.0% | 18.67 / 30.80 |
| bloomberg_massive | test | 425/0 | 100.0% | 33.51 / 36.96 |
| breakingdefense_space | production | 91/0 | 100.0% | 21.83 / 25.75 |
| businesswire_massive | test | 61/0 | 100.0% | 32.84 / 34.55 |
| calcalist_search | production | 122/0 | 100.0% | 67.10 / 72.99 |
| canary_media | production | 91/0 | 100.0% | 23.42 / 25.61 |
| corporate_ir_press_releases:IR:be.press | production | 60/0 | 100.0% | 33.92 / 34.51 |
| corporate_ir_press_releases:IR:ceg.press | production | 90/0 | 100.0% | 22.18 / 23.49 |
| corporate_ir_press_releases:IR:cohr.corporate | production | 91/0 | 100.0% | 22.28 / 25.68 |
| corporate_ir_press_releases:IR:cohr.financial | production | 91/0 | 100.0% | 22.96 / 25.54 |
| corporate_ir_press_releases:IR:intc.press | production | 91/0 | 100.0% | 22.32 / 23.70 |
| corporate_ir_press_releases:IR:mu.ir | production | 61/0 | 100.0% | 33.10 / 35.27 |
| corporate_ir_press_releases:IR:mu.newsroom | production | 91/0 | 100.0% | 23.68 / 25.91 |
| corporate_ir_press_releases:IR:nbis.press | production | 91/0 | 100.0% | 23.44 / 26.01 |
| corporate_ir_press_releases:IR:onds.press | production | 81/10 | 89.011% | 39.97 / 43.18 |
| corporate_ir_press_releases:IR:rklb.ir | production | 91/0 | 100.0% | 22.84 / 26.43 |
| corporate_ir_press_releases:IR:rklb.updates | production | 88/3 | 96.703% | 26.02 / 43.38 |
| corporate_ir_press_releases:IR:snow.press | production | 91/0 | 100.0% | 22.72 / 24.86 |
| crn_cloud_security_datacenter | production | 273/0 | 100.0% | 23.04 / 26.02 |
| ctee_semiconductor | production | 30/1 | 96.774% | 60.64 / 111.55 |
| darpa_news | production | 91/0 | 100.0% | 21.95 / 25.99 |
| data_center_frontier | production | 91/0 | 100.0% | 23.69 / 26.10 |
| dcd_energy_ticker | production | 182/0 | 100.0% | 23.03 / 25.50 |
| dcd_infrastructure_rss | production | 91/0 | 100.0% | 22.22 / 23.74 |
| defensescoop_drones | production | 127/55 | 69.78% | 23.91 / 321.56 |
| defensescoop_space | production | 91/0 | 100.0% | 22.39 / 25.49 |
| digitimes_semiconductors | production | 30/0 | 100.0% | 61.01 / 61.14 |
| digitimes_tw_rss | production | 90/0 | 100.0% | 22.90 / 37.56 |
| dod_contracts | production | 91/0 | 100.0% | 22.56 / 25.41 |
| dod_releases | production | 91/0 | 100.0% | 23.04 / 28.90 |
| doe_energy_news | production | 364/0 | 100.0% | 23.53 / 26.04 |
| enterprise_apps_official_rss | production | 424/0 | 100.0% | 34.68 / 41.28 |
| etnews_rss | production | 91/0 | 100.0% | 23.02 / 25.76 |
| finnhub_company_news | production | 606/0 | 100.0% | 34.53 / 36.73 |
| globenewswire_massive | test | 121/0 | 100.0% | 33.48 / 37.67 |
| globenewswire_search | production | 2472/0 | 100.0% | 22.97 / 37.80 |
| google_news_search_rss | production | 818/0 | 100.0% | 23.38 / 25.94 |
| huggingnews_rss | production | 90/0 | 100.0% | 22.19 / 23.77 |
| ibkr_news | production | 909/0 | 100.0% | 27.35 / 32.52 |
| investing_ticker_news | production | 606/0 | 100.0% | 32.50 / 39.23 |
| investorshub_ticker_news | production | 602/3 | 99.504% | 39.58 / 61.32 |
| ionq_nautilus_rss | production | 91/0 | 100.0% | 22.03 / 25.77 |
| ionq_quantum_actors_rss | production | 273/0 | 100.0% | 23.48 / 25.47 |
| iridium_press_releases | production | 91/0 | 100.0% | 22.13 / 26.70 |
| lakehouse_platform_official_rss | production | 182/0 | 100.0% | 22.68 / 25.53 |
| nasa_2026_releases | production | 91/0 | 100.0% | 23.26 / 25.66 |
| national_quantum_initiative | production | 91/0 | 100.0% | 23.74 / 26.05 |
| nbis_official_actor_rss | production | 182/0 | 100.0% | 23.63 / 26.71 |
| nist_news | production | 91/0 | 100.0% | 21.71 / 24.59 |
| nrc_event_notifications | production | 91/0 | 100.0% | 21.89 / 24.60 |
| nrc_news_releases | production | 91/0 | 100.0% | 22.11 / 25.17 |
| nro_press_releases | production | 90/0 | 100.0% | 22.55 / 23.46 |
| nsf_news | production | 91/0 | 100.0% | 22.61 / 23.79 |
| optical_systems_official_rss | production | 91/0 | 100.0% | 21.95 / 23.60 |
| power_engineering_energy | production | 545/0 | 100.0% | 23.73 / 26.30 |
| power_magazine_energy | production | 817/0 | 100.0% | 23.53 / 37.81 |
| prnewswire_massive | test | 61/0 | 100.0% | 34.28 / 39.34 |
| quantum_computing_report | production | 90/0 | 100.0% | 22.38 / 24.23 |
| quantum_insider | production | 272/0 | 100.0% | 23.44 / 38.00 |
| quantum_zeitgeist | production | 30/0 | 100.0% | 71.51 / 76.24 |
| register_software_cloud_security | production | 272/0 | 100.0% | 23.79 / 25.98 |
| sdxcentral_news | production | 59/2 | 96.721% | 33.93 / 60.65 |
| sec_edgar | production | 1798/0 | 100.0% | 17.90 / 21.62 |
| shephard_uncrewed | production | 90/1 | 98.901% | 23.61 / 39.41 |
| siliconangle_ai | production | 91/0 | 100.0% | 22.58 / 25.60 |
| siliconangle_software | production | 272/0 | 100.0% | 23.55 / 26.46 |
| spacecom_launches | production | 91/0 | 100.0% | 23.62 / 25.73 |
| spacenews_rss | production | 23/7 | 76.667% | 177.66 / 240.86 |
| ssc_newsroom | production | 91/0 | 100.0% | 23.09 / 25.83 |
| technews_hardware | production | 179/0 | 100.0% | 28.63 / 47.99 |
| thefly_ticker_news | production | 544/2 | 99.634% | 34.03 / 60.27 |
| thelec_semiconductors_rss | production | 91/0 | 100.0% | 22.86 / 26.25 |
| tmx_newsfile_massive | test | 60/1 | 98.361% | 35.01 / 52.41 |
| tomshardware_rss | production | 90/0 | 100.0% | 23.45 / 38.33 |
| trendforce_news | production | 90/0 | 100.0% | 22.96 / 36.50 |
| trendforce_press_releases | production | 91/0 | 100.0% | 23.35 / 25.54 |
| unmannedairspace_counteruas | production | 182/0 | 100.0% | 23.64 / 37.22 |
| utility_dive | production | 90/0 | 100.0% | 24.16 / 25.42 |
| venturebeat_ai | production | 90/0 | 100.0% | 23.29 / 37.23 |
| venturebeat_software | production | 273/0 | 100.0% | 22.98 / 25.65 |
| wccftech_hardware | production | 273/0 | 100.0% | 23.30 / 26.15 |
| world_nuclear_news | production | 91/0 | 100.0% | 23.08 / 25.66 |
| yahoo_finance_news | production | 1814/4 | 99.78% | 36.44 / 61.73 |

## 补充覆盖与正文

补充页真实传输证明：`{"quantum_zeitgeist": {"both_pages": 29, "head_only": 1}, "technews_hardware": {"both_pages": 178, "head_only": 1}}`。head_only不得称完整两页覆盖；覆盖欠账继续保留。

正文为 body_outcomes 回执计数，不是唯一文章数；无新正文机会的入口不能凭列表成功宣称正文验收。

| final_site | outcome | reason | 回执 |
|---|---|---|---:|
| fool | FULL |  | 1 |
| generic | FULL |  | 31 |
| generic | PARTIAL | subscription_required | 10 |
| generic | SHORT_FULL |  | 2 |
| generic | UNAVAILABLE | http_401 | 7 |
| generic | UNAVAILABLE | render_required | 6 |
| investing | FULL |  | 1 |
| yahoo_finance | FULL |  | 2 |

## 真实交付

按本窗口 collected_at 及既有状态索引读取；raw条目按ticker计数，不能当独立文章数。

| source | processing | raw | standardized | published |
|---|---|---:|---:|---:|
| aws_platform_marketplace_rss | completed | 1 | 1 | 1 |
| data_center_frontier | completed | 5 | 5 | 5 |
| google_news_search_rss | completed | 6 | 6 | 6 |
| quantum_computing_report | completed | 1 | 1 | 1 |
| wccftech_hardware | completed | 4 | 4 | 4 |
| yahoo_finance_news | completed | 2 | 2 | 2 |

## 资源与保留边界

截止样本磁盘剩余 22.72 GiB；MemAvailable 14470160 kB；acquisition 13.32 GiB；WAL 3.95 MiB。PSI原样保存在JSON，单点不推导整窗分位。

Supervisor/Profile未重建；不改出口、Cookie、主备或既定周期。测试源、外部429、权限/认证、排队延期与完整覆盖欠账必须分开，不宣称全部长期稳定。30分钟短窗不替代24小时/跨原复发点老化验收。

## 最终结论及未完成的老化边界

最终连续自然窗口 30.41 分钟；正式有效采集周期 23471/23559（99.626%），试验源 789/790（99.873%）。这些仍不是新文章数。
全窗无成功的失败入口：[]；截止失败/部分状态 unit：[{"source_id": "defensescoop_drones", "ticker": "ONDS", "unit": "FEED:3e7ad1dabf2ec5b4b61c9c0c", "status": "failed", "error": "HTTP_LISTING_FAILED_RSS_FALLBACK", "last_success_at": "2026-11-11T18:59:43.943038+00:00"}, {"source_id": "defensescoop_drones", "ticker": "ONDS", "unit": "FEED:27cdf1757342a7b11cdfb4c3", "status": "failed", "error": "HTTP_LISTING_FAILED_RSS_FALLBACK", "last_success_at": "2026-11-11T18:59:52.119276+00:00"}]。截止投影与完整历史分开。
共 496 个 ticker×unit 有本窗口记录，32 个出现大于两倍配置周期的内部或窗口边缘空窗；逐项见 CSV。不能将总体成功率当作全部时效目标达标。
正式正文 FULL/SHORT_FULL 合计 37 条回执；其他权限/鉴权/需渲染回执在上表单列，不能称所有文章正文均已恢复。

### 仍出现失败的入口

| 入口 | 有效周期 | 失败 | 已有回执原因 |
|---|---:|---:|---|
| corporate_ir_press_releases:IR:onds.press | 81 | 10 | {"ConnectTimeout": 10} |
| corporate_ir_press_releases:IR:rklb.updates | 88 | 3 | {"URLError": 1, "TimeoutError": 2} |
| ctee_semiconductor | 30 | 1 | {"site_access_unavailable": 1} |
| defensescoop_drones | 127 | 55 | {"HTTP_LISTING_FAILED_RSS_FALLBACK": 55} |
| investorshub_ticker_news | 602 | 3 | {"site_queue_timeout": 3} |
| sdxcentral_news | 59 | 2 | {"publisher_endpoint_backoff": 2} |
| shephard_uncrewed | 90 | 1 | {"DEFENSE_ENDPOINT_FAILED": 1} |
| spacenews_rss | 23 | 7 | {"publisher_http_429": 7} |
| thefly_ticker_news | 544 | 2 | {"TimeoutError": 2} |
| tmx_newsfile_massive | 60 | 1 | {"publisher_response_timeout": 1} |
| yahoo_finance_news | 1814 | 4 | {"browser_coverage_pending": 2, "UNPROVEN_LISTING": 2} |

### 重点分支和验收限制

- `calcalist_search`：{"CAPTURED": 122}；原因 {}。
- `nrc_event_notifications`：{"CAPTURED": 91}；原因 {}。
- `tomshardware_rss`：{"CAPTURED": 90}；原因 {}。
- `digitimes_semiconductors`：{"CAPTURED": 30}；原因 {}。
- `investing_ticker_news`：{"CAPTURED": 606}；原因 {}。
- `investorshub_ticker_news`：{"CAPTURED": 602, "FAILED": 3, "DISPATCHED": 1}；原因 {"site_queue_timeout": 3}。
- Calcalist 当前既有 unit 累计上下文修复计数 [4, 4, 4, 4]。这是同一进程的累计数，不按 ticker 相加；与同窗真实 CAPTURED 结合判断，自身不是新请求成功数。
- DIGITIMES 自然列表成功不能证明原先所有 302 都属于规范跳转；若窗口没有真实 302→200 分支，只能将该分支列为离线验证通过、生产触发未覆盖。
- 可选 http_timing 字段在无新 HTTP 标量的 SiteClient 周期可能保留最近值，因此不将它当成每轮独立网络证明；以实际请求回执/ACCESS_RESULT/周期终态为准。
- 后半窗 DefenseScoop 先有30个失败物理周期（UTC18:52:41—18:57:31），18:58快照两个入口自然恢复；18:59后再次失败，最终共55次 public_listing_http_403，同轮另有200，截止两个入口均failed。属于真实主HTTP拒绝与复发，不能写成全网准入故障，也不能仅凭另一请求200认定完整回退已成功。该源未通过持续稳定验收；保留独立主失败与RSS证明，不重启/换路由掩盖或降低整批周期。最终有界核对见 final-residuals-end.json。
- ONDS IR仍有ConnectTimeout，RKLB IR出现TLS握手/读超时；这些不是已证实的坏池PoolTimeout。已保留正常后续周期与其他host，不将普通网络超时触发成无界client重建。尚不能承诺各单源均达到99.5%。
- 真实 429 遵循既有受限 lane 的 Retry-After；共享浏览器容量仍为两槽，队列偶发延期与网站 504/鉴权问题不能靠扩大等待伪装成功。
- 此次没有降低任何配置轮询间隔、增加 Collector＋Site 的总 HTTP 槽数或重建 Supervisor/Profile；没有操作 Reuters 的身份/出口/验证码。
- 短窗满足与否与长期老化分开：尚未跨越此前约 18 小时复发点，方案的 24 小时或大于故障前两倍请求次数的老化验收尚未完成。未创建长期观察任务，不能据此宣称长期无人值守稳定。

### 资源样本

首次快照 2026-11-11T18:44:53.464996+00:00 与截止 2026-11-11T19:04:09.953015+00:00：磁盘空闲 21.59→22.72 GiB；acquisition 13.302→13.319 GiB；WAL 0→4140632 bytes。这是两点样本，不证明长期增长有界，也不能把全部磁盘变化归因消息源。
本轮诊断复用既有小字段、许可队列内存通知和现有索引；没有新增逐请求诊断表、抓包、截图或高频监控写入。CPU/IO PSI 仍须按原始样本评估，不编造整窗分位。
