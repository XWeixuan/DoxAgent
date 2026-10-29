# 统一I/O修复：90分钟生产观察结算

窗口：2026-10-29T14:30:32.571819+00:00–2026-10-29T16:00:32.571819+00:00。包括冷启动、失败、版本切换、在途和未完成；最终子窗口不替代整体。

|窗口|应执行周期|新鲜/全部覆盖|缺失/在途|首次FULL|≤10秒交付|已完成mean/p95/max秒|
|---|---:|---:|---:|---:|---:|---|
|完整90分钟|39505|28730 / 72.72%|1769 / 4|56/70 = 80.00%|98/125 = 78.40%|8.164396355371903 / 17.529013 / 116.837068|
|最后10分钟|4221|3761 / 89.10%|12 / 3|4/5 = 80.00%|4/5 = 80.00%|4.441858 / 7.376799 / 7.376799|

独立SQL核对启用订阅267条；期望分母必需单元396个，来源74个。逐单元≥99%：16/396。
未进入任何期望unit的启用binding：[]。多频道合同仍须按完整manifest/源参数核对。

## 逐来源总账

|Source|必需单元|应执行|新鲜|全部覆盖|真实请求周期|准点观察due槽|缺失/在途|
|---|---:|---:|---:|---:|---:|---:|---:|
|airspaceforces_space|1|91|74|81.32%|74|74|2/0|
|ans_nuclear_newswire|1|89|73|82.02%|74|70|0/0|
|army_news_releases|1|89|73|82.02%|73|71|1/0|
|army_news_rss|1|91|73|80.22%|73|69|1/0|
|aviationweek_rocketlab|1|89|73|82.02%|73|73|0/0|
|aviationweek_rss|1|89|74|83.15%|74|72|0/0|
|aws_platform_marketplace_rss|3|271|224|82.66%|224|220|1/0|
|barrons_ticker_news|20|1801|1509|83.79%|1512|1452|17/0|
|benzinga_news|10|899|699|77.75%|739|727|6/0|
|breakingdefense_space|1|91|74|81.32%|74|74|2/0|
|calcalist_search|4|272|181|66.54%|225|218|2/0|
|canary_media|2|182|150|82.42%|152|148|0/0|
|corporate_ir_press_releases|12|2159|2099|97.22%|2131|2056|11/0|
|crn_cloud_security_datacenter|3|270|222|82.22%|222|220|3/0|
|ctee_semiconductor|2|178|156|87.64%|156|144|2/0|
|darpa_news|5|455|365|80.22%|365|335|5/0|
|data_center_frontier|6|546|444|81.32%|444|444|12/0|
|dcd_energy_ticker|2|180|148|82.22%|148|146|2/0|
|dcd_infrastructure_rss|6|534|438|82.02%|438|432|6/0|
|defensescoop_drones|1|91|74|81.32%|74|73|1/0|
|defensescoop_space|1|91|74|81.32%|74|74|2/0|
|digitimes_semiconductors|5|455|400|87.91%|400|370|5/0|
|digitimes_tw_rss|5|455|375|82.42%|375|370|5/0|
|dod_contracts|2|182|148|81.32%|148|148|4/0|
|dod_releases|2|178|148|83.15%|148|142|0/0|
|doe_energy_news|7|633|521|82.31%|521|515|7/0|
|enterprise_apps_official_rss|3|271|223|82.29%|223|219|2/0|
|etnews_rss|5|455|375|82.42%|375|375|5/0|
|finnhub_company_news|10|899|286|31.81%|295|630|7/0|
|globenewswire_search|25|2251|2164|96.14%|2165|2101|13/0|
|globenewswire_semiconductors_rss|5|455|370|81.32%|370|370|10/0|
|google_news_search_rss|2|180|149|82.78%|149|146|0/0|
|huggingnews_rss|5|450|370|82.22%|370|365|5/0|
|ibkr_news|10|901|0|0.00%|0|0|148/0|
|investing_ticker_news|10|899|702|78.09%|702|675|6/1|
|investorshub_ticker_news|10|901|555|61.60%|623|521|5/0|
|ionq_nautilus_rss|1|89|73|82.02%|73|71|0/0|
|ionq_quantum_actors_rss|3|271|223|82.29%|223|220|3/0|
|iridium_press_releases|1|90|74|82.22%|74|74|1/0|
|lakehouse_platform_official_rss|2|181|149|82.32%|150|142|0/0|
|nasa_2026_releases|1|89|73|82.02%|74|71|0/0|
|national_quantum_initiative|1|91|74|81.32%|74|73|2/0|
|nbis_official_actor_rss|2|180|147|81.67%|147|147|3/0|
|newswire_rss|14|1260|990|78.57%|1034|965|8/0|
|nrc_event_notifications|1|89|74|83.15%|74|71|0/0|
|nrc_news_releases|1|91|75|82.42%|75|73|1/0|
|observability_vendors_official_rss|2|179|144|80.45%|147|147|1/0|
|optical_systems_official_rss|1|90|74|82.22%|74|74|1/0|
|power_engineering_energy|7|632|522|82.59%|522|508|8/0|
|power_magazine_energy|9|810|668|82.47%|668|653|12/0|
|register_software_cloud_security|3|271|221|81.55%|222|219|2/0|
|reuters_site_search|33|2971|8|0.27%|11|2|105/1|
|sdxcentral_news|5|455|360|79.12%|370|370|5/0|
|sec_edgar|10|1710|1667|97.49%|1674|1487|13/0|
|shephard_uncrewed|1|89|65|73.03%|74|70|0/0|
|siliconangle_ai|5|455|375|82.42%|375|365|5/0|
|siliconangle_software|3|271|222|81.92%|222|222|4/0|
|spacecom_launches|1|89|73|82.02%|73|73|1/0|
|spacenews_rss|1|90|88|97.78%|89|83|1/0|
|ssc_newsroom|1|90|73|81.11%|73|67|0/0|
|technews_hardware|10|900|745|82.78%|745|715|5/0|
|thefly_ticker_news|9|810|661|81.60%|666|657|8/0|
|thelec_semiconductors_rss|5|455|380|83.52%|380|365|0/0|
|tikhub_x_user_posts|1|9|6|66.67%|7|7|0/0|
|tomshardware_rss|5|455|370|81.32%|370|370|10/0|
|trendforce_news|5|450|395|87.78%|395|365|0/0|
|trendforce_press_releases|5|450|385|85.56%|385|365|5/0|
|unmannedairspace_counteruas|1|90|74|82.22%|75|72|0/0|
|utility_dive|2|178|176|98.88%|176|176|2/0|
|venturebeat_ai|5|455|375|82.42%|375|365|5/0|
|venturebeat_software|3|271|222|81.92%|222|222|3/0|
|wccftech_hardware|15|1345|1110|82.53%|1110|1080|5/0|
|world_nuclear_news|1|91|74|81.32%|74|72|2/0|
|yahoo_finance_news|30|4790|3162|66.01%|3166|3063|1250/2|

## 口径与限制

- 新鲜沿全部有效SCHEDULE重建，不是CAPTURED条数相除；真实TRANSPORT_SEND、解析验证及原physical ID关联缺一不可。RSS不抵消独立Browser频道。
- 准点观察due槽为D+min(3秒,0.1P)内真实发网run去重；版本/资格另以cadence核对，此辅助列不替代有效覆盖。逐unit CSV保留启动间隔及长尾，首尾无观测按缺口保留。
- BODY首态/当前并列；视频与订阅不足仍计失败，不伪造FULL。NOT_APPLICABLE、MERGED按原合同单列，旧首态缺证据为UNKNOWN。5篇2157–9253字符正文头尾抽检未见仅订阅预览；抽检与程序标签都不证明全量上游完整性。
- T0沿首次列表响应关联；T1为Owner可消费，未完成保留年龄。Read/UI水位另见快照，真实取网缺证据保留NULL。
- 混合版本90分钟及最后10分钟小样本不能证明24/72小时稳定或20ticker/30秒容量；版本切换、相位迁移及失败保留。
- unit-summary.csv、source-summary.csv及逐篇articles/deliveries、cycle_status_reasons.csv构成可复核总账；资源、物理归因、sysstat与测试XML另存本目录。


## 最终Review补记

T0已逐篇重核：70/70篇及125/125应交付项全部通过exact source/first_seen→capture→request start→validated physical ID关联。相同physical ID的重复capture合并取证；记录T0与物理响应最大差17.876ms。物理耗时mean为8.164705秒，≤10秒98/125、p95/max不变，deliveries.csv保留两套耗时。13d2d63b修正未来分页T0优先级，16:05:22Z正式Collector更新只做短检，不冒充该版本已跑满90分钟。

首次FULL56/70恰为80%，结算FULL58/70；均不证明稳定>80%。最后10分钟Barron’s200/200、IHub97/100、Investing99/100、Yahoo379/380；Reuters0/320、IBKR0/100、Calcalist7/30等保留在总体分母。Calcalist失败物理样本CSE bootstrap200、真正搜索403，之后恢复15轮不等于根治。

资源差分14:31:12–16:01:59，13个离散快照、同boot：整机读40.03GiB/写45.32GiB（包括构建和其它业务），末区间读4.08MiB/s、CPU busy52.57%、iowait1.40%、await0.566ms。采样MemAvailable最低4.43GiB；仍有Swap流量、CPU排队及约8.78GiB匿名内存，不能证明24/72小时稳定。实际版本、剩余Investing BODY认证/权益、IBKR上游及交易EXIT风险、测试合同差异与完整成本限制见[执行记录](../../docs/message-pipeline-unified-io-repair-implementation-20261029.md)。
