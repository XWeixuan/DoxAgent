# Google News Search RSS：12 个待复核站点全量复核与最终黑名单建议（2026-10-27）

此前“待复核”源于首轮抽样和正文不足：有的站点噪音高却出现重要事件标题，无法只据比例判断全局封禁。这一轮已对 **12 站全部 649 条召回消息逐条复核**，并给出逐站最终建议：**2 站建议封禁，10 站建议保留**。复核范围沿用首轮 30 天 Google RSS 召回快照，未扩大为站点全网历史。

## 最终结果

|Site|历史召回|低价值|潜在有价值|证据不足|最终建议|
|---|---:|---:|---:|---:|---|
|ad-hoc-news.de|121|27|54|40|保留|
|marketbeat.com|100|78|14|8|保留|
|fool.com|72|10|8|54|保留|
|247wallst.com|53|17|11|25|保留|
|digitalfoundry.net|46|22|21|3|保留|
|simplywall.st|45|18|15|12|保留|
|shattered.io|40|11|17|12|保留|
|tradingkey.com|39|8|15|16|保留|
|aol.com|38|6|10|22|保留|
|media.unisba.ac.id|35|20|0|15|封禁|
|kalkinemedia.com|32|2|6|24|保留|
|pcguide.com|28|26|1|1|封禁|

全量 649 条中，245 条判低价值、172 条为潜在有价值、232 条证据不足；低价值比例以每站全部召回为分母。证据不足保留在分母而不算低价值，不能误称这些内容已被证明无价值。站点最终建议是对现有证据和全局封禁损失的权衡，不把 uncertain 伪装成确定事实。

三组 Codex 子 Agent 按上一轮用户指定的 **gpt-6.1-terra / medium** 分工，复读所有 649 条的标题、RSS 摘要及已有存储片段，并分别核对了每站逐条 ID。原文补查后对 14 条标签作了可追溯修正；新修正覆盖逐条 JSON 旧标签。标题/摘要被完整审阅，不等于 649 篇文章全文已读。

为核实边界，对 12 站选取 111 条做本地轻量 HTTP 正文补查：57 条 HTTP 200 且抽取文本≥200 字符，但其中 **7 条是 Simply Wall St 的导航/社区壳**，仅 50 条获得可识别的目标文章文本；另 42 条为 403、12 条连接超时/失败。文本最多保留前 10,000 字符，页面可能后来更新，不等于首次发布时的完整原文。对没有正文的条目仅据标题/摘要与已有片段判断，不把访问失败当低质量。

## 四个全局黑名单候选与收益

|Site|本次召回总量|BE|INTC|本次样本评价|
|---|---:|---:|---:|---|
|on3.com|109|0|109|109/109 体育情报词义碰撞|
|247sports.com|40|0|40|40/40 体育情报词义碰撞|
|pcguide.com|28|0|28|26 条低价值、1 条潜在有价值、1 条不确定|
|media.unisba.ac.id|35|35|0|20 条低价值、15 条不确定|

合并去重召回 **212/4641 = 4.57%**；BE **35/1685 = 2.08%**；INTC **177/2956 = 5.99%**。相比首轮两站 149 条/3.21%，新增两站 63 条，增加约 1.36 个百分点。以上是对本次 RSS 唯一条目数的静态事后命中，不等价于线上请求、CPU 或下游成本实测节省。

四站中的 212 条已全量按标题/可用内容复核：195 条低价值，**1 条潜在有价值、16 条证据不足**。潜在误杀以 PC Guide 的 Amazon CPU 销售榜为例；它是有限渠道的竞争信号，不能与 Steam 调查直接视为同一数据，也尚未证实能被其他已绑定消息源覆盖。Unisba 的视频标题中还有未经核实的业绩、合作与持仓线索，封禁时将一并损失，不能把未抓到的正文断言为垃圾或虚假。

现有 `HiddenNewsIngressPolicy` 的域名配置同时作用于 Google News RSS 和 Yahoo Finance，而本测试只评估 Google 的两个 ticker；若未来要实际启用，需要先确认这一跨源影响。本轮未更改配置，四个域名只是建议名单。未来纳入消费硬件/体育等 ticker 时也应复核全局封站的跨板块损失。

## 每站最终判断与证据

下文示例链接为来源直链或 Google RSS 原始链接；“潜在有价值”是对报道主题的判断，不证明事实真实、独家、首发或其他渠道一定不可替代。各站全部 ID、最终标签及 14 项正文复核更正在 `final-decisions.json` 中。

### ad-hoc-news.de：建议保留

全量 121 条：低价值 27（22.3%）、潜在有价值 54、证据不足 40。

保留：121条中低质报价模板明显，但存在跨公司合同、项目监管、产品与业绩的具体信息；历史片段证实连行情标题也可能含 AEP/Google 702 MW 项目等事实。跨 ticker 全站封禁会丢失此类线索。大量 D-Wave 反复组合合同与诉讼，独家和新增程度未证实，保留不等于认定全部准确、首发或高价值。

正文补充：新请求超时；历史片段中AEP/Google项目、Oracle微网与Intel产品等使全站封禁存在跨ticker误杀。

典型低价值：

- [Bloom Energy stock at USD 288.70 on September 25, 2026 - ad-hoc-news.de](https://news.google.com/rss/articles/CBMisAFBVV95cUxQTTRVVFlUamhCcGtwYWY3Mnk3UUtvUV92cF9Tbm9mMXh5aTRYQlBYTjNtVUdTZmhoUGxqWDV4c1ZLUG52UWlTMTMweVdrdHh0dHgtNDU1VUloOVQ2Sk9rQnllRGdjNmdvS2tkejBoOWdXQkZkLUxSV0R0T2VtN2NfTHBwTmRabDEwQ1J0NUZZWm4zNWp2QUlhZVpYdC03ZzFhODBHdFNQZmRfMGJrZklPTg?oc=5)（2026-09-28T07:00:00+00:00，ID 2160807907349e9c）
- [Intel stock at USD 115.92 on September 28, 2026, down 5.76 percent - AD HOC NEWS](https://news.google.com/rss/articles/CBMiwAFBVV95cUxOSWc3bjhUY1RvU0VfMkN6Y0hEU1NQRENPS05LVTZBc1VGWDAwRG1KY0VTZ1ZBbi1OdFYwMURrb2RKSEVyY2RCanFJSXNLT1dCaE5lYzhuSmRsLUt0cjljOThUNG5PdWRGUW9rT3JDYzJ1WGZhb2l4LUdWWFJtejBuZmh2RnFiTFhPVnZWX2hyX1dXV3d1RGpQVDdjNXFyX0U3bzJzczdoY0ZaX0YwdzAyQU1YZFVxeW00Y21BR1Y2TEs?oc=5)（2026-09-28T07:00:00+00:00，ID cd4bc356fe002a84）

潜在有价值例外：

- [Bloom Energy Weathers Legal Storm as Pipeline Delays Cloud Oracle-Linked Project - ad-hoc-news.de](https://news.google.com/rss/articles/CBMi5wFBVV95cUxPNWIwQTZNU0tIQXBLVGpCZ0p0MFRFMjBEdTg1YVZVRjdMMW9ULXh4S09hM1FFNkNmUldaQ2JFQllkMGdIbmxXeG85TjFQNGUxZEloaXFNS2l0R1FqSWF5T21mRnRaUHRwb1B3TERnNWZqX29rMGN4RlFIOHFKTnFFYWp6MWdNLUZyQy10UUpic3BSaDB0QXh4VTAtbFlFRndBV0tQNnFoM1N0VFRnbGNvRFRYSUdZb1VRX2k4cjJvcC0tQmlteWZOVllka1pkT0RHYzdodFFBSkJlWW5ublBMdThaQ003Vmc?oc=5)（2026-09-27T07:00:00+00:00，ID 2a0b33c1713c77f5）
- [Nvidia Tests Intel's 14A Node While Building a Certification Moat Around AI Factories - AD HOC NEWS](https://news.google.com/rss/articles/CBMi7gFBVV95cUxOT1JiSEpRTVVuX0tMZ1hGLV96d0hFVk5tSi1IYXJNUU5palIxSmJwY1puQV9NVGxoU3h5TnFpbXg2TmVwc1Z2WUktYVBJMklHbG14UXNBSDFLNXZpTzZ0ellTM1ZBZElxNkVBSWhsak82TEg3SHB1U2pZZWNkVHg0UUx6THBwY0JlOElES3E2YnhjUF9PaEtsejJIQWFaRlVmWjNiZFlFX3FENGlDc3pCR1BrQ2daODAzQkJJNzBLdlBKNVNWdmp2eUZxWjdNeU9fNGtGZDVJMnc2cUNuRjBUWW1FS3dyRVUyc3poQXpn?oc=5)（2026-09-27T07:00:00+00:00，ID b9d0fbe18102f1e3）

与 [Intel’s Crescent Island AI Server GPU Gets a Name and Specs: Meet the Intel GPU D-Series - ServeTheHome](https://news.google.com/rss/articles/CBMitwFBVV95cUxORTY1NEFuaFM3dl9EOFd6SmdTc19lRlhYWHRCWWNPSFpfM2oxQVZWNlNCaFlSODl4eUUwSGdZZkpxMVQ0YTdONk93OUJKWTljZm1GRFUwLXZyazZLR2p3YklJZXVIdDZ1RjdlVlNJV3lZM0I1WUJiZjZVd1RTbkltSjVtSkdpNFdqOEJBQVA1ZkE3Z0pFY05ETXVzaXUzSWpoU2R3QlFBWVlLcjREX3pKemRXTkQwYzg?oc=5) 对照：本站标题称 GPU 客户采样及机柜密度；ServeTheHome 10月12日报道同一 GPU 命名及规格，本站 RSS 为10月13日。仅凭标题无法判定采样和机柜细节是否独有，07:00可能只有日期精度。

与 [Intel Crescent Island Squeezes 1.3 PFLOPs Of FP8 Into Just 350W As D270 Targets The Competition With Value-Oriented LPDDR5X Up To 480 GB - Wccftech](https://news.google.com/rss/articles/CBMiqwFBVV95cUxOV3BSeU1venZEQmpqNEk3R3FuZks5cEZBT2tPcFRZTVc5Z0wwLXpZSnZPODVvM01lNHRRb1pEdGRJU1pNVHpfeHBsZW9XRVpMcDRQZXhJTHQteDJoN25YdWJMZ185NVV0ME92VXNpWmx3LVVXM2VCREZBLTBUTkNKbDFjSHRSWmNfZWlxTDcwT2htbTJVRVVkaXU3LU1EZWJNdFNYMnh5WXhPWjDSAbABQVVfeXFMTVBmeVJ0V0pvTTlKcVZmSm83WGxZcFY0M2xVMzgxa2Z1eWdCR3NmQXNNSEhtc0MtaE5PWDgteURSN1g3NlQ5bU4zMTZYR2tiUHg5c0hQY3Eydm5kN09VZVJ3a3k0Y0F6YjZSbGZEX0ZSMXhGRkVtUWNicExpNHJRSlZTTGRQakZNemJqY2hiNlFyUElRY240SG9nNzR4YVpYbmJRTEo5N18ydFN3ejNNX1Y?oc=5) 对照：本站已有片段明确引用 Intel 官方 10月12日发布，列出480GB、1.3 PFLOPS及采样；Wccftech 10月12日标题同列1.3 PFLOPS及480GB。具体发布时间和全文未核验，不判断日内先后或独有内容。

### marketbeat.com：建议保留

全量 100 条：低价值 78（78.0%）、潜在有价值 14、证据不足 8。

**最终判断与初判不同。** 由 block 改为 retain。12 篇提取正文显示不仅有持仓与行情模板，还存在 WSP 管理层电力业务会议、Texas 监管对 NRG/Vistra 的合同及项目差异、Intel D-Series 技术与财务的跨公司分析；这些具体增量及未来其他 ticker 的误杀风险使全域封禁代价过高。不能仅凭正文证明独家或首发；大量模板噪声仍然存在。部分正文被截断至 10000 字符，内容可能随页面更新；不将抓取页面中的后续汇总当作 RSS 当时内容。

典型低价值：

- [Bloom Energy (NYSE:BE) Given Outperform Rating at Royal Bank Of Canada - MarketBeat](https://www.marketbeat.com/instant-alerts/analyst-bloom-energys-be-outperform-rating-reiterated-at-royal-bank-of-canada-2026-09-28/)（2026-09-27T07:00:00+00:00，ID 1934e05bd1063d40）
- [Bloom Energy (NYSE:BE) Stock Price Down 4.8% - Should You Sell? - MarketBeat](https://www.marketbeat.com/instant-alerts/price-bloom-energy-nyse-be-stock-price-down-48-should-you-sell-2026-09-30/)（2026-09-30T07:00:00+00:00，ID 42ba9c418925b712）

潜在有价值例外：

- [WSP Global Targets Grid, Data Center Boom With Expanding Power Platform - MarketBeat](https://www.marketbeat.com/instant-alerts/event-wsp-global-targets-grid-data-center-boom-with-expanding-power-platform-2026-09-29/)（2026-09-29T07:00:00+00:00，ID db9dbedc4c210e86）
- [Bloom Energy (NYSE:BE) Insider Aman Joshi Sells 8,437 Shares of Company Stock - MarketBeat](https://www.marketbeat.com/instant-alerts/insider-bloom-energy-nyse-be-insider-aman-joshi-sells-8437-shares-of-company-stock-2026-10-02/)（2026-10-02T07:00:00+00:00，ID 4ab0f8382117ee9d）

与 [Oracle Invokes Force Majeure as New Mexico Project Jupiter Power Work Faces Hurdles - Engineering News-Record](https://news.google.com/rss/articles/CBMiugFBVV95cUxPVnpWWXcyU2Q0WF9Hc0prRURZVzFBRHRSOW5GZEFqY2tGWEFxNW5UUEV3SzZFSzhjLU9iTk1haTVCOHJnQ053eGM1RXNlczdHWlVVcHo4d1ZqM1dVbTNLY2ZjNEpnR3lZZ2JqQ0pLMUhwcjkyYWNveE9qQ1Y2a0doNW12VTNDaGNNUzEwekFVUE13dURhN0xLOU8xcjRrTlJtRDBDM3ZXOG9UVDhrLWN0TWx6Tm4tWWtrcXc?oc=5) 对照：ENR 的 9月28日 RSS 日期早于 MarketBeat 9月29日；两者时间均为 07:00 UTC 日期粒度，未读 MarketBeat 原文，不能判断其是否有独特内容或小时级先后。

与 [Texas Data Center Permit Freeze Could Delay New Behind-the-Meter Power Projects - Hart Energy](https://news.google.com/rss/articles/CBMiqwFBVV95cUxQYWxOTDRLMkVEdUM5dkl4SFdhRXl5U0tGZ1FrQXJVQkxyUWkxTmM1aVlxWnpwOE9ROVdEWGdrVVNpeTJDVEdyb0YyV0xkVWo0aDF6UVpGQk10RzZXd0pmX3BySC1kdjlqNnJwVHZvVU1nX1A5dkt3OHN2TjhsQkR6UW1Xc2JCdHFTR3QwUjNublVybGRFZUdLOE5fUTFFZjRUV0wtZkNEVjM2Mjg?oc=5) 对照：Hart Energy 9月30日已报道 Texas 许可冻结，MarketBeat 为10月22日16:11 UTC；两者关注的具体合同与后续监管不完全相同，未读 Hart 原文，不能认定完全替代。

### fool.com：建议保留

全量 72 条：低价值 10（13.9%）、潜在有价值 8、证据不足 54。

保留：荐股、价格历史和对比模板占相当部分，但 Terafab、Generac/Amazon 合同、Bloom 财报和 Project Jupiter 内容体现可影响不同 ticker 的实质事件。Terafab 已有其他来源更早日期报道，未证明独家；不能因为重复就断定未来独家价值为零。全局封禁的漏报代价高于仅根据本样本清除模板的收益。

正文补充：retain。补抓 9 篇均成功：Terafab 为明确归因的二次分析，Bloom 财报分析有经营事实，部分荐股文也有跨 ticker 经济信息。两条原 low 因正文中的其他 ticker 事实应降为 uncertain，一条原 uncertain 宜升 valuable；这进一步提高全局封禁误杀风险。未证明该站首发或所有财报事实独家。

典型低价值：

- [History Says Intel's Best Years Have Been Hard to Follow. 2026 Is Its Best in More Than 4 Decades. - The Motley Fool](https://news.google.com/rss/articles/CBMi1gFBVV95cUxNcnBFclB3LVJMQXh3TVBvZnNDYWdnaDFrVFFwY2RrOXUzaE1vZWZrUUdGQjhLdDFRMmhMVTZWeC11YkVkNVlpZjdUNTlXUHNxTlZNVEJRSG13dDMtSjROaE4yN0FMZ1lucmVvWEFNcmZzYWlyem9lRWNmNDB1bERlRUppRHhOTXlJRkk0MFAyNkxMc2x2bTd0NjVTYWFuQjg5aGFfWFl3RnVOdERRRHhndlNiMHZJUy14c1hUNWYxQTJKVk5kazN1STR6SFJ2OHJrb0xnbEFn?oc=5)（2026-09-27T07:00:00+00:00，ID 13f310b39373f959）
- [Intel Stock Surged Over 40% in September. History Shows What's Next. - The Motley Fool](https://news.google.com/rss/articles/CBMimAFBVV95cUxOTDFLclMzZjJ3Ti1keVQ2SFdpSUZuY2NrMnhHbUtDSU1PTzdnN21oQ2JJM2pLcDlQZ2RsZHlUalIxVUZpN2VPR0c2SjEtUTF6Y21SZ01vQklaU2tJT0RuaTFCNWh0bHM0S1lJYUtwSnF2eXN6UTFGZHNzbDgyYi14VEUtRGlyMnc1cjlXQnZKc0RoTG5EMENnTQ?oc=5)（2026-09-27T07:00:00+00:00，ID 15f73c787ebf223e）

潜在有价值例外：

- [Why Bloom Energy Stock Is Powering Higher Today - The Motley Fool](https://www.fool.com/investing/2026/09/29/why-bloom-energy-stock-is-powering-higher-today/)（2026-09-29T07:00:00+00:00，ID a8325e6d30064119）
- [Elon Musk Confirmed Terafab Talks With TSMC. Intel Has Been Its Only Named Chip Partner. - The Motley Fool](https://www.fool.com/investing/2026/10/05/elon-musk-confirmed-terafab-talks-with-tsmc-intel-has-been-its-only-named-chip-partner/)（2026-10-05T07:00:00+00:00，ID db12f00dbbe83c68）

与 [Elon Musk confirms discussions with TSMC about Terafab chipmaking collaboration — Intel is the only other named partner, Terafab to exclusively supply Tesla, SpaceX, and xAI - Tom's Hardware](https://news.google.com/rss/articles/CBMiygJBVV95cUxNeXItamxLWmIyc21RQWhkVFlValdBRFZZWF9aamZYcmZrdFBXM1BZSHAzZFhydlFHcUNpMlE3bV83dlJLZ0ZIQnZtWGFuNmp4Vzh1QVA2WFFFZk5faHU2U0ZyZlE0RzJxdWplX3pFTm1QTVdibHZTMUZFOHRmTGFjWk1XRnQtSVFLTmpoVjd1cV9HR3dlRDB1TW5jdnpIMk9WZjJhUFRINXFqMWFlNjFVNldTUWxxQk1zeXJlQ1lwRzNaOHpZemdEaWNtUnd3RXdCSEpuNWphVWxuMXZpeGM5cXBYaUlRU0dVVnpGaUF1d09pMWM5MkhQY2FWQnlWeHROQ2RibUZwWVY4VXNtS1JjZ0lzZGZWS183UjZpZVJfZU9UV0NKa05Oam1SY2JNeFhMWjd5RTdYNmVnQWUzZ0lrb21GaVJpR2lWTGc?oc=5) 对照：Tom’s Hardware 记录日期为 10-03，Fool 为 10-05，标题所述同一会谈。仅标题比较，未读原文，不能断言独有细节或日内时序；07:00 可能仅日期精度。

### 247wallst.com：建议保留

全量 53 条：低价值 17（32.1%）、潜在有价值 11、证据不足 25。

保留：大量行情流水和模型目标价，但存在供货协议、CFO 离职、半导体竞争与季度指引。样本显示有实质事件而非完全价格稿；跨 ticker 全局封禁会抹去这些事件，不能假设其他来源总能先且完整覆盖。

正文补充：retain。9 篇均 403；不能把失败当作低质证据。旧片段及 AOL 成功正文显示 CXMT 分析、Fit Energy 项目的潜在实质内容，亦有纯行情及泛投资稿。AOL 正文含 247 联系方式构成转载归属线索，不能据此证明首发时间；对于全站独有性仍有明显不确定性。

典型低价值：

- [Bloom Energy Sheds About $5 Billion in Market Cap in One Session - 24/7 Wall St.](https://news.google.com/rss/articles/CBMiigFBVV95cUxQWjJJSlVTc0hvUjJWTmdtTG5PWUp0ZVRMa1pENkNCemg2bG9RUThqWEs2VGtMc05QbTR3X3BUaHJuek9DODlfbHJOcVpRUHdiR0Rzb0M1QmpVc0NkaEw2ZldjQjRiM0JYYnVpdVdzbk9NRHdGRFdRV21WSG1LLUlINm5xVjJ4d2wzTlE?oc=5)（2026-09-28T07:00:00+00:00，ID 697ad6fe4ece7d70）
- [Intel Drops 4% on Oil-Driven Rate Fears, NVIDIA Rises 3% on Record $150B Buyback; Taiwan Semiconductor Slips - 24/7 Wall St.](https://news.google.com/rss/articles/CBMi4AFBVV95cUxQXzRzbk8yU3RhekhEaXNXMF9WNUFObjZhLW9NWG9Fclp6U2xyckZmSDRRNDFxd2sxanY5VUpXMlJLOGR4TUVDaGpMeUYwc1M2azRGVzFrRm9JeVphSndQckUzRU9wYlE5aXE5UGVUZkN4ZHNGNWVZTnRWS24ydHc3MGVkZTljQk9kR3o0TklBOFRtamw2OHF3NXc1Ykk4eEVWUEJGa3N3Sll5TkVvRlZ0cGJDWm9UQlFhZTVTRkdEV09MRm8yTzNvZHRIWmFOQ3FESm9HRG5mM3c1eHRpaFMzQg?oc=5)（2026-09-28T07:00:00+00:00，ID aeb86d9c60ea76a7）

潜在有价值例外：

- [Plug Power Advances 4% on Electrolyzer Supply Agreement; Bloom Energy Sinks 3%, FuelCell Energy Treads Water - 24/7 Wall St.](https://news.google.com/rss/articles/CBMi4gFBVV95cUxOSldEcHVmVWhOSTVRQkVvbGZpUWhVcU5mUVQ0X0gzcVZzWkNFY0Z1ZVBlNkVCLTlFcloyNEtRUjlTMndHSEs2MEZvRmJJclhxck43dzNMb2dNcmR3SXNIRFRFWUszMWY5c3lqX1VpT0Rvc1JSQy1CME9pNnVZUWxFU0xlYVBoeEFUc3ZQYUpScXFsb3dYVUg0cUhidWc3UXlQUzFNbUhRWTFBZFVZVDNybHR5N21la3Z1LXo4OHZRYlQwT1dQX0RuY21kS0hUVm44MlBFLVR0RW5OUDBOM29UbXpR?oc=5)（2026-09-30T07:00:00+00:00，ID 0160eaffcecb1f09）
- [Intel Drops 4% as Elon Musk Signals Taiwan Semiconductor Could Join Terafab; AMD Slips, NVIDIA Holds Steady - 24/7 Wall St.](https://news.google.com/rss/articles/CBMi4gFBVV95cUxNTHJ1WV95RFlmcV9UZERudXpIN3JNbE1jZnpNUk9ESmpvOV9PbVpIREFzblFfai03QUM5QW42TlZWVDZVOHFZZERJY05kU0NLckk4Z2FoclVkYjRDWVJqVnpwaG5xd2lKSV9WS1hHMS1kZkZhZzdFQ1o5X3h0SUtqd0Y0aEh1aHlPX3pZSkxaazIxYXBYck1qLUF3X3gxekJyREJQRk5kVWxORDVRVXdnOXFlUklJdmZoTkZRZG9NeVY0bldjU1RCaWNfZjhqQW9mSWJyVjFWS2NwUmNXZ2d2Zm13?oc=5)（2026-10-05T07:00:00+00:00，ID 25a308cf224154c5）

与 [Fit Energy Will Use Gas-Powered Fuel Cells For Pennsylvania Data Center Project - POWER Magazine](https://news.google.com/rss/articles/CBMiqAFBVV95cUxNNllZNUJWdTNHbEhtNVZ0RWxXTHQyeUx2cDAtNGF1d1ozdlozNWliMllWNC14Y3h1ak5oc0tLNEdaZU5vcTlDN3g3YVZMNHBqbFdWM04tNjBHNGttby1WNUZaWW44VERqY0VzTXFFTEhkYWtUcThwMEtDWGRnSjlsdnVvUXdjSXJvUkxIbHVRWDY0eU41aGRnMXBTTzNHRWlBX05zTFF2UnM?oc=5) 对照：两者均记录 10-06 项目；仅标题佐证关联，07:00 可能仅日期精度，不可判先后、独家或具体供货关系。

### digitalfoundry.net：建议保留

全量 46 条：低价值 22（47.8%）、潜在有价值 21、证据不足 3。

46 条有游戏及外设评测噪声，但也有 AMD/Nvidia/Sony/Microsoft 重要技术、产品与竞争事件，甚至 RDNA 5 传闻的原作者澄清；这些会影响未来多个 ticker。全局封禁误杀技术与独立一手分析的代价高，保留。

正文补充：8 篇补抓均 403，无法凭本次抓取核实具体技术内容或首发。既有标题及 RDNA 5 历史片段显示多家公司重要技术和纠偏线索，维持 retain。

典型低价值：

- [Creative XF1 Review: Classy PC Speakers That Hit Harder Than You'd Expect - Digital Foundry](https://news.google.com/rss/articles/CBMisAFBVV95cUxNNjBYSVM0TUpSV1F5cVhMb0JfdlZvMzhZcjBnQmpYRFpybmJBdERNV1lTSmJJelVUWjRyRjYyRS1pR1FnRE52M3dYRGhXU091M2hQN3pvMHlaeDlHOU1NR3ZYUC1jYTQxU202enFTR3ByeldyRG9jSk9XZjUwdFBJYUJHbmtEdDBHUkRGUlJ6QllrdVNzLTc1b1o1b0phdEl3NDZNa2tIVHpNelNBc2VlOA?oc=5)（2026-09-27T07:00:00+00:00，ID 006647b007f3ac40）
- [Review: Control Resonant: An Impressive PC Version With Superb Scalability - Digital Foundry](https://news.google.com/rss/articles/CBMipwFBVV95cUxOV3p1dzJubThCcUdOZTZpVzdFZFpCeU9qUUtxMkF4dTVTMjNNSkd4dDctQ3NrWjN0THFWQmxqNEVUZzNyeHFLeUpEeXdlLVotTDJ1UVN4c3RCRjFmSkRsYUJialhYZWRuNDloTHUybmNWajRIR29pclRBcEhLVDQ1cGpVdGkwZ2UyLVR4QzNQVDNDd2ZNYVp5Q3o4YW9sTkl2VWtjMnBqYw?oc=5)（2026-09-27T07:00:00+00:00，ID 4a3afed0a36ea166）

潜在有价值例外：

- [AOC's First 1050Hz Monitor Using "Oxide-Based" Fast IPS Panel Arrives in 2027 - Digital Foundry](https://news.google.com/rss/articles/CBMiugFBVV95cUxORjUwUlVldGFRdHBOUW5Yb0hUQXBxOTladUtORUt5Uzd2aUJYOUlnNHNFYVJZSnpBbmVQYVExTnh2cHBfNlMxdnlTWWRkdkJBM2VKSVRFSUo5OGIzRzMzaHdvYlhnbnh1NDIzXzAtT0M3Ml93WWhZTFJ4ZVIxTkd4c0duZWxXczVZQmY2bnI0T0R1Mk5QQmI5TzdxX2NaLXJtVGYtT0ZTaWk1ellkNktLS2haV21MbFJWeEE?oc=5)（2026-09-30T07:00:00+00:00，ID 41567033b99cada0）
- [Sony announces QSSR AI upscaling tech for base PS5 in Marvel's Wolverine and Ghost of Yōtei - Digital Foundry](https://news.google.com/rss/articles/CBMizgFBVV95cUxQUmZUakd4WW1QdEM0YkhTeFNmYkt2c0NlZjhZVVRYcnpBbEdMWWF3dkFRNkFGMVZzbDVSSkdnQlpOZ29FWVF3dzhyMU1iVjNUUHRzREtfb290aVczb0hmLVczUmFVYVFuTENOamxEcjlnQU0wdnZfYWRjS2lFMGsxTmt6TzNpaU9qdWJHajdZdzB5TmRyU1cwNk11VllHN1NnalBsQi1URkdCang0czRzRnRfbHVudVhERTBURGlNYjdmY29WWEd1TGlJbDJUQQ?oc=5)（2026-10-01T07:00:00+00:00，ID 0e36bec346c86e92）

与 [Sony’s PS6 Handheld Could Be the Only RDNA 5 Portable, as AMD Reportedly Requires Big Orders the Others Can’t Guarantee - Wccftech](https://news.google.com/rss/articles/CBMijwFBVV95cUxOZkdPQjNzMlpMQUdiY083RE9ib2F6bVB0MmFRenE1SFh3MDNfRUpfOUkzdHFCM2ZQMDBTczYzZnFaTVYzN2EyT2lBYU1FVzJNZGhQdHZCSXppbmhwT21EUWl5S2p0VXBOSklOTGsxVW01Mk5wM2tLM1pDMlpsN2l1cGhrTGkydXhUeGF0ekFORdIBlAFBVV95cUxPYVI1c1ZDZV9lWXh3VDYxMERtRDhaZ2YxR1N0bDZJMXZWWTVZektKZHhjVElicmRqdW53MjhyX3Jpc1UzQWRQLXVqZ0VETkRkcmw2cTJQWFNDRWxZQ1Y2anIyR3FDcG9ubzEwVDFMaEVveW9LelI2bjE1Y2Yzal9nX3JlNGpzWkgxcHk0MVFySUE2NkJs?oc=5) 对照：Wccftech 10月26日09:00 UTC 先于 DF 17:04 UTC；DF 历史片段明确澄清其早前播客言论被扩展解读，显示一手纠偏价值，但 Wccftech 原文未核实，不能认定转载链。

与 [Microsoft: FSR 4.1 Coming Very Soon to Xbox Game Developers for Series X, Series S - Digital Foundry](https://news.google.com/rss/articles/CBMiwgFBVV95cUxQNFlEM0FSYjdzX2lWZzg4ekRseV8yeDlhb01VRUg3VXNjeHU3M2Z3aXNFQXMyRkthSG5meUlQeTAwV0k0M3JQa1lFdkpEZjQtQmhpclFnb2gtanBBZlNudndmNnFMY3lab0VYREFkWWxvRE1OUC1JLURLbWxtcmZDZEhOb3ItbzAtWGRVV0M3b3pZU01Hd0hmc2ZOc0pJa2t6UWtidVA3cmJqbGRnZEF0RWxlUlJLdFJvVjZSa2d0LXhMQQ?oc=5) 对照：同站后续 10月21日报道开发者支持；全库标题另有 Wccftech 10月23日 Intel Arc 移植报道但涉及不同版本与事件，不能视为同一发布的替代或证明首发。

### simplywall.st：建议保留

全量 45 条：低价值 18（40.0%）、潜在有价值 15、证据不足 12。

保留：选股清单、估值和推广筛选器占明显部分，但片段及标题覆盖 Enerflex 合同、Intel 产品、Liberty 1.2GW 协议和 Caterpillar 投资等跨公司经济事实；即便已看到其他来源，尚不足以证明将来均无独有事件信息。需注意摘要事实与原始公告仍需核验。

正文补充：9篇HTTP 200中7篇仅页面壳，另两篇有目标正文；既有片段及正文含合同、投资和订单线索，全站封禁有误杀风险。

典型低价值：

- [3 Hydrogen Stocks With Revenue Growth Up To 43% - Simply Wall Street](https://news.google.com/rss/articles/CBMitAFBVV95cUxQbjdZSm56MHlCODc1TTl6TzZXR0JqWDVWQzE5Z28wMzZtMlZTTDUtV1QtZk0zRHNvd2F2QzY4MkV5TDZLRUhyakRCbjlLd3Nxd2hYZ2hoZnlLMHpjdUF6YmRNS0t2ZHFQVDVUeG5SamZWcGR3bXB4Zlh4R1dVYml1V2dTY3NYdlJWVlR4ZDhFSElJdTVZbnpmcjRPR21nNy1xYV9aWC1OdFkxYXlkN25FYjhzakrSAboBQVVfeXFMTWk0RWRFY1kxejVjc0hvTzJCNElRRjFhd1NCSXY4VmdNcWVvalRnWUFZXy1ieGNXamZMa0x2Q2x6Nk1GaG84TFpkZHNFRE1EOGQxX3NRazgtRUduUGhHZ0gyZ0kxQTVRM1NVMzFkazRWeUQ2RHV2eXhmTEliblVzbkczaWZPZDFUMlUydWNWQTFCM0otWGdGSE1IS0xNajhUS2RRdVkzMlgxZE5JYVM3S05GaU5SQnAtc0FB?oc=5)（2026-10-07T07:00:00+00:00，ID 1a372984f3503041）
- [3 Grid Stocks Riding The AI Data Center Power Bottleneck - Simply Wall Street](https://news.google.com/rss/articles/CBMixAFBVV95cUxQd1hnRWtEQWdRNTMzWEpkLUpNbm85Ykg3aDFaNXFHMUVQMTN3bzBZWWVMUUg0NVF3Q1B6V3lsRkZYQlowQ0R5Sko1S2xYcnAxZHFMaGNxaGhYRkZhd19oczd2SklobHVuM2paUklmWXFDUkJsMWN1QlJqZmZDeXl4dGJjcS1HUk1lc2tKUXBrS3VXUi14cjlGUnVDX2pSLXVwbzZSTUgxYUo3alAxQURFMTFDY0ZHTTJUWW1vZXVOdWl2Vm820gHKAUFVX3lxTFBDOG9LVWZObU9ibG1HQTh1WE9UV2p4T2hlbUktdkFYNmVVTXBhNlJrcENRMnBCejRQZ2pDNkFYd21tenZvelVTVUhCTnBNTGxBUTdZODAzV0VaZ3A4M1ltcmVvLWpXQkhZbm5rWE1TeGJyZl9MV2Jmc3BSV3VMTkJvX1BpSEdFbnQ5WGQxc1p2MloxM0h3a3lXcGZGUGFTZ1ZHUnEtUk1fWTBiRVhrNXFyVnZ1WS1yd2g2Q2l6el8tN1djNU1xMGJyckE?oc=5)（2026-10-08T07:00:00+00:00，ID b787cfa75d9654cb）

潜在有价值例外：

- [Is Fulton Financial (FULT) Undervalued As Its Blue Foundry Deal Starts To Pay Off? - Simply Wall Street](https://news.google.com/rss/articles/CBMizAFBVV95cUxOWmpMakJDSTR0dms3VS16MjJEaVhma1hsdVNCVHRUbWt6aGJ6cEk0WE4xM3dLcFRXYU1QXzVjb2R3SEhZbFNmSTUwd1VnamNTazJwUzhHNy1mX0pORmNlOXBQakk4eVdTR2hxSnFLSk5EeGFnd2c1bEFobnFiNEgtMnpNbzFTREdZMDNiOEdBV09CNkZfRHBESUFaNzlNUGdabDJWc3NxZjBlNVJiZ0ZHMGkxel9JWlpQZlJ4b0xVYjl4SmM2c0hEZFloOTjSAdIBQVVfeXFMTWFEclRGbEM5cXRUTGRORHJMV3E5VnNmZW84ZldLZHdpcFVBbEw4dFpad2NYcW5aQmZUMnlzbTVrS3lBTTNjbGE5aVNxZzR4dXZLOUtZSzhlZzA3bUtleFdITzA1VXRBWmtMWnhvZkYzY3FiUnI1dWw0WGNBREVnOWlhOFpWcVhqY1h2V3F2V0hPQkhtd0p2eE9OdUJyV1FSTzBTR01LanlxUDZDb1JSSTV5RlhHc29ISmZyZFFQYVhjZVR6ei1LVTYyZV9haTQ2YUtR?oc=5)（2026-10-18T18:46:54+00:00，ID 204a5b357f3daab9）
- [Learn Why The Bull Case For Intel (INTC) Could Change Following Tesla Foundry Deal - Simply Wall Street](https://simplywall.st/stocks/us/semiconductors/nasdaq-intc/intel/news/learn-why-the-bull-case-for-intel-intc-could-change-followin)（2026-10-19T23:51:15+00:00，ID 7212c23449e104f9）

与 [Enerflex wins over 1GW data center power generation contract - StreetInsider](https://news.google.com/rss/articles/CBMiuwFBVV95cUxOWVd3bHp4OXViNnRpLS1rc3NXZURGZXBreEU1QWs2UmRjV2RfOGdBSzJwRmlxb2RSZ1BXTlBXem03V3UtSFNwS2lSOUhEUWlEVUJyOVJNNkZ4VXduMkR4SWRQSU50VTk5YXN4VUEzRlI1OTBIZy01bndQanNtdXQ1MnU5dVg2dk50RlZaTnc1V05OQVF1UVZzVlJ6NWVrNnBacnBRU3JnY0VYRjZ6ZllvMGJwUWNSa3MxaGlr?oc=5) 对照：本站10月14日04:04 UTC标题提数据中心电力合同；StreetInsider 10月13日10:02 UTC标题已有超1GW合同，可能同事件但本站全文未取得，无法证明合同完全一致或内容独有。

与 [Intel Crescent Island Squeezes 1.3 PFLOPs Of FP8 Into Just 350W As D270 Targets The Competition With Value-Oriented LPDDR5X Up To 480 GB - Wccftech](https://news.google.com/rss/articles/CBMiqwFBVV95cUxOV3BSeU1venZEQmpqNEk3R3FuZks5cEZBT2tPcFRZTVc5Z0wwLXpZSnZPODVvM01lNHRRb1pEdGRJU1pNVHpfeHBsZW9XRVpMcDRQZXhJTHQteDJoN25YdWJMZ185NVV0ME92VXNpWmx3LVVXM2VCREZBLTBUTkNKbDFjSHRSWmNfZWlxTDcwT2htbTJVRVVkaXU3LU1EZWJNdFNYMnh5WXhPWjDSAbABQVVfeXFMTVBmeVJ0V0pvTTlKcVZmSm83WGxZcFY0M2xVMzgxa2Z1eWdCR3NmQXNNSEhtc0MtaE5PWDgteURSN1g3NlQ5bU4zMTZYR2tiUHg5c0hQY3Eydm5kN09VZVJ3a3k0Y0F6YjZSbGZEX0ZSMXhGRkVtUWNicExpNHJRSlZTTGRQakZNemJqY2hiNlFyUElRY240SG9nNzR4YVpYbmJRTEo5N18ydFN3ejNNX1Y?oc=5) 对照：本站片段提 GPU D 系列和 Xeon 机柜；Wccftech 10月12日标题已有1.3 PFLOPS与480GB，且本站片段以估值为重点。未比较全文或准确原始发布时间，不断言独家或同日先后。

### shattered.io：建议保留

全量 40 条：低价值 11（27.5%）、潜在有价值 17、证据不足 12。

保留：Foundry 一词碰撞造成多条智能合约安全教程，以及购物和消费对比噪声；但同时有芯片路线图、AI GPU、竞争产品与基础设施事件，跨 ticker 全站封禁将漏掉这些线索。抽查多个芯片事件已被专业媒体更早按日期报道，尚不能证明无独家价值；传闻和产品性能主张需要原始来源验证。

正文补充：存在Foundry词义碰撞与消费对比，但亦含芯片、机柜和云故障线索；文章经常转述外部媒体及未确认传闻，无法证明独家。

典型低价值：

- [Reentrancy Attack Testing: 12 Foundry Steps [2026\] - shattered.io](https://shattered.io/reentrancy-attack-testing-foundry-2026/)（2026-10-03T07:00:00+00:00，ID 4a784266334d967e）
- [Truncation Bug Testing: 12 Foundry Steps, $1.7M Lost [2026\] - shattered.io](https://news.google.com/rss/articles/CBMiaEFVX3lxTE44SFNseVlsd01RZnIwRVk5ejVuanFVN0I4aEZxa2RWNDhIZTBqMGlHblZ3c1lvd0tORzVMZkdudC1XMVh6cmJrampVWWxKT0Ywalp6aGR1c05YZmZaZGxsSGxZYlVxc25X?oc=5)（2026-09-30T07:00:00+00:00，ID 638d0f99ae5131c4）

潜在有价值例外：

- [Intel Nova Lake Leak: 28 Cores, 144MB Cache [2026\] - shattered.io](https://shattered.io/intel-nova-lake-leak-28-core-144mb-cache-2026/)（2026-10-03T07:00:00+00:00，ID 7b1b3c8abb7f4dc8）
- [Raptor Lake Next: Gigabyte BIOS Backs DDR4, DDR5 [2026\] - shattered.io](https://shattered.io/gigabyte-bios-ddr4-ddr5-raptor-lake-next-2026/)（2026-10-18T15:55:00+00:00，ID 4d3eb1f6d37f93dd）

与 [Intel Crescent Island Squeezes 1.3 PFLOPs Of FP8 Into Just 350W As D270 Targets The Competition With Value-Oriented LPDDR5X Up To 480 GB - Wccftech](https://news.google.com/rss/articles/CBMiqwFBVV95cUxOV3BSeU1venZEQmpqNEk3R3FuZks5cEZBT2tPcFRZTVc5Z0wwLXpZSnZPODVvM01lNHRRb1pEdGRJU1pNVHpfeHBsZW9XRVpMcDRQZXhJTHQteDJoN25YdWJMZ185NVV0ME92VXNpWmx3LVVXM2VCREZBLTBUTkNKbDFjSHRSWmNfZWlxTDcwT2htbTJVRVVkaXU3LU1EZWJNdFNYMnh5WXhPWjDSAbABQVVfeXFMTVBmeVJ0V0pvTTlKcVZmSm83WGxZcFY0M2xVMzgxa2Z1eWdCR3NmQXNNSEhtc0MtaE5PWDgteURSN1g3NlQ5bU4zMTZYR2tiUHg5c0hQY3Eydm5kN09VZVJ3a3k0Y0F6YjZSbGZEX0ZSMXhGRkVtUWNicExpNHJRSlZTTGRQakZNemJqY2hiNlFyUElRY240SG9nNzR4YVpYbmJRTEo5N18ydFN3ejNNX1Y?oc=5) 对照：本站10月13日标题的1.3 PFLOPS和480GB已见 Wccftech 10月12日标题；本站是否新增客户采样细节未取全文核验，07:00可能仅为日期。

与 [Intel confirms Razor Lake-AX for 2028 with 700 TOPS AI performance - VideoCardz.com](https://videocardz.com/newz/intel-confirms-razor-lake-ax-for-2028-with-700-tops-ai-performance) 对照：VideoCardz 10月17日标题已称2028年700 TOPS；本站日期10月18日，未取两篇全文，不能断言额外细节或独家。

### tradingkey.com：建议保留

全量 39 条：低价值 8（20.5%）、潜在有价值 15、证据不足 16。

保留：虽有行情波动模板和技术预测，片段亦包含工厂事故、财报和铸造进展。85%良率为转述 KeyBanc 的报道，客户订单仍未确认，标题强于摘要，须独立验证；全球跨 ticker 封禁会损失事件线索，样本不足以证明全站无独特或早期价值。

正文补充：财报及电话会提取有业绩和运营线索，虽价格预测及回顾较多，全站封禁仍可能遗漏事件。

典型低价值：

- [Bloom Energy Corp Stock (BE) Moved Down by 8.11% on Sep 28: Drivers Behind the Movement - TradingKey](https://www.tradingkey.com/news/market-movers/262190261-market-movers-be-20260928)（2026-09-28T07:00:00+00:00，ID 67a6b6b3e32ffa49）
- [Intel Stock Forecast: INTC Falls 8% Toward $81.64 Support Ahead of the October 29 Margin Test - TradingKey](https://www.tradingkey.com/analysis/stocks/us-stocks/262235573-intel-stock-forecast-q3-earnings-preview-margin-foundry-81-support-october-25-2026-tradingkey)（2026-10-25T13:03:20+00:00，ID 7f2edc25a47bf26c）

潜在有价值例外：

- [Intel Stock Price Forecast: Confirms Continued Participation in Terafab, Can INTC Hold $100? - TradingKey](https://www.tradingkey.com/analysis/stocks/us-stocks/262209799-inter-intc-spcx-spacex-terafab-tradingkey)（2026-10-10T07:00:00+00:00，ID efb97b64b6687b20）
- [Intel Falls Below $90 as Robotics Ban Hits Chipmaking, Stock Risks Falling Over 10% More - TradingKey](https://www.tradingkey.com/analysis/stocks/us-stocks/262225939-stock-intel-intc-bis-fcc-80-tradingkey)（2026-10-20T13:14:24+00:00，ID c41d0c2b72046d3c）

与 [Concern of a Hazardous Materials Incident Following an Explosion at the Intel Plant in Kiryat Gat - Gov.il](https://news.google.com/rss/articles/CBMiV0FVX3lxTE5hLTNzVXAtdjAzaG53aFE0TTYwWjR0ZjBLVE1pMUp3blVZRTNuTG85aTJVcmRPNUJPRG9OaTRydE5NMDdXbVNoSWNNS0lEa3NiczQwZXRpRQ?oc=5) 对照：政府来源10月14日标题已报道同厂爆炸；本站10月19日11:03 UTC片段回顾死亡与潜在影响。政府条目07:00可能仅日期，未取双方全文，不能断言本站无独有细节。

与 [Bloom Energy Q3 revenue beats estimates on record product sales; raises FY2026 guidance - TradingView](https://news.google.com/rss/articles/CBMi7AFBVV95cUxOU2I3d2NHaExuM0JBXzVRVHBGZFVYaTFMS3NuYXVlck1mWVpfcGpTVFluVmhXRmRlbEw3aG1LZ19HYmtvRjZmWmZHR3JIZEUwNE4zZWJBUkUyRjdyWEpqM1pIX0tkRHlCeFB2a2NjU0lEMDYzQkUzbGJFdTBqWkplaDJiSVlwbzI1NFo5MkRjZjlSLUlHazZqaHMwSnRINGtUUmR1b252b21mVUxvYlBlbjVUYnJCVFdseklkMkE3cVU2cG40Uk9uekp5ZHV3cFBsd050bHRzZzBGajhqajVDMUdpZGxLbWNadHJmcQ?oc=5) 对照：TradingView 10月22日20:13 UTC标题已报道季度营收超预期和上调指引；本站20:52 UTC片段给出营收、EPS与指引区间。两者时间按RSS显示先后，未核验原文发布时间或全部内容。

### aol.com：建议保留

全量 38 条：低价值 6（15.8%）、潜在有价值 10、证据不足 22。

保留：聚合页里混有大量重复荐股、行情及 INTC 词义碰撞，但也转发 Engadget 的 DDR4 新产品消息、市场供给警告等不同领域报道。政治情报和 Boom Supersonic 虽不相关当前召回 ticker，对其他公司或未来 ticker 可能有意义，不能据此判低。平台跨领域误杀成本高；同题可能是转载，未证明独家或领先。

正文补充：retain。9 篇成功。多篇可确认来自 Engadget、Motley Fool、Yahoo Finance 或有 247wallst 联系方式，聚合不等于无价值；正文包含 Lam 供给限制、Nokia 业绩及 Intel 产品报道。两条原 low 宜改 uncertain，一条 uncertain 宜升 valuable；报道称 Axios 首报的地缘事件亦不能按 INTC 误召回认作全局低质。转载时序和是否覆盖全部原文仍未知。

典型低价值：

- [Intel Stock Surged Over 40% in September. History Shows What's Next. - AOL.com](https://news.google.com/rss/articles/CBMiekFVX3lxTE9OS0pmc3NYMktUX2xvRnFUV1o0S21EUldnQlpBbTQzX1hxSTNQUGctWDkxbXFzcF9sdzhoWHZjTzlpUnpURS1vZEFSTlVZNUNFLWpaTS1QQUtBamZMZkhTb2UzRGJBcHhmYS0xMTFYaGpTaW9Fbjd3ZUFR?oc=5)（2026-09-27T07:00:00+00:00，ID ad939a776a1877ac）
- [Intel Drops 4% on Oil-Driven Rate Fears, NVIDIA Rises 3% on Record $150B Buyback; Taiwan Semiconductor Slips - AOL.com](https://news.google.com/rss/articles/CBMid0FVX3lxTE5UckhDYUZtVHBnaUhSVk1NaXBUNVlNcjZ4OTVVdXA4aHljSGxjYTltX2U4MHhTY3NsTTlHamxiejh6bmFVZG5yNEM3eFJjWmlzbGFHU3FXdS1JaWx4TTBwZFRCdk1zeUVycXdXS0ZMZWJMLWxxeWtZ?oc=5)（2026-09-28T07:00:00+00:00，ID 49203c4d9bbd1e9d）

潜在有价值例外：

- [AI spending will fuel wins for Micron, Nvidia, Intel, and other chip stocks: BofA analyst - AOL.com](https://www.aol.com/articles/ai-spending-fuel-wins-micron-193925000.html)（2026-09-30T07:00:00+00:00，ID 3ccbae19ddb1f76a）
- [FuelCell Surges 11% on Fit Energy’s Pennsylvania Data Center Plan; Bloom Energy Climbs 4%, Plug Power Barely Budges - AOL.com](https://www.aol.com/articles/fuelcell-energy-surges-11-fit-164806000.html)（2026-10-06T07:00:00+00:00，ID 7f2fc3c6de71c661）

与 [Intel Is Reportedly Launching New CPUs On Old Sockets That Support DDR4 Memory - Engadget](https://news.google.com/rss/articles/CBMisgFBVV95cUxPVUJtdHBJZmlQYWZzRVR0ZV9hSkNJNjVrSGNFWjJXcFBNZGs5WXhadU9CMXBPYS1xbDM5VUJHcThGZXo2V1UxNHhDcGFXVFU2YWNPdXRqM3NyWTRCT05HVlpkRjdUU21RX0MxZ1F0ZWFCa3Y2SlNvMElUUVBoeHpoTjRsUGt3OEs3aUZjWHdQS1VUVVQ5T0VzbE55VDhBY05PWUVaUlVocWM0SWxBdTJkTnVR?oc=5) 对照：标题一致，均记 10-09；可能转载，未核正文及来源署名，不可断言独家或先后。

### media.unisba.ac.id：建议封禁

全量 35 条：低价值 20（57.1%）、潜在有价值 0、证据不足 15。

35 条反复出现投资视频式标题及无关热词、重复，来源可靠性与原创身份不明；标题涉及营收、交易、合约的保留为 uncertain 而非当作虚假或默认低质。Brookfield 线索已有其他来源但金额并不相同，未核实原文不能视为同一金额确认。全局封禁有漏掉视频线索的风险，但重复拼接噪声和来源不确定性使封禁更合适。

正文补充：9 篇补抓均 403，无法判明页面与视频关系、原始出处或真实性。重复、无关热词和投资视频标题占主导；含业绩与持仓等线索存在误杀风险但未证实独家，维持 block。

典型低价值：

- [The $2,200 Options Mistake: Why Selling Puts On Bloom Energy Cost Me A $3,350 Day Sergio Bonelli (PsokFodbtq) - Unisba Media](https://news.google.com/rss/articles/CBMiZkFVX3lxTE1LbGRTYzNFdER3b1phQXR4RGMydm41SGFfNXNCSm5oZnNFOU1wZjFaTm83VEwzSFhDcFlKS09jQ1NRRElNaWRBeER4Q29yeU90LThFcjk2cUJ3d1VZdnA4Z2hCd3U4QQ?oc=5)（2026-09-30T20:06:10+00:00，ID ab6a0c37effe3144）
- [Don't Miss This: Bloom Energy And The AI Power Boom Steven Spielberg (CKI9I9XQLv) - Unisba Media](https://news.google.com/rss/articles/CBMia0FVX3lxTFBfYjhiX0tpaHI2N1BMb3R2Y3daNlB0SHBaYWZsU3JlcDRZc3BPaUZlZ0h2MUhocjFwdV9jeUxZaUZyUHBsWWw5MmRYSk1Za2tnQUoya2dnWk56T1lENDlHTFhNMFRvQXF1bGZz?oc=5)（2026-10-02T03:22:52+00:00，ID 2b7fa9cdd9d03c4b）

潜在有价值例外：该站样本中未能确认；有证据不足条目不能证明不存在例外。

证据不足示例：

- [Bloom Energy S&P 500 Addition & Macro Breakouts Cincinnati - Inter Miami (kK0cR6NeeI) - Unisba Media](https://news.google.com/rss/articles/CBMiZkFVX3lxTE5BbTIwSHdBZWxVU2llcDRUOFRtQ1lGSHNZYldCc0xKeHliVkQyTWlzMGNzOEhNQUgzbHk5YTNHVEEwQ2hhbzhDMXZ3bzBPZ3BfaTdZa0xIcXhzYlZwb0l0a1NRUEY5Zw?oc=5)（2026-10-01T02:56:49+00:00，ID ff33d98759c140b2）
- [Bloom Energy Corporation (BE) Earnings: $1.1B Revenue, $0.62 EPS — Full Same-Day Breakdown Scott Foley (lO6ssaE0VP) - Unisba Media](https://news.google.com/rss/articles/CBMiggFBVV95cUxNeTNKSDJCelo2X2hfNFR3dThneEVneW44NkxfUF9mNzlCLWNraERTWXJmNnZtcnZMeDNieHlYT1hkMkExeFNYMnQxRXJ5QjdqY3dySkR0ZllVYWxSTkZBNUlPT0tIb24yMGw4NENLYnZmWFZ4MlliQ29YN0F2aEc2NlZn?oc=5)（2026-10-01T09:17:34+00:00，ID 083fac89b72eccfd）

与 [Solid Oxide Fuel Cell Market Expands as Bloom Energy and Brookfield Scale AI Power Partnership to US$25 Billion - openPR.com](https://news.google.com/rss/articles/CBMikgFBVV95cUxQOV9NV3B6VEVvTVo0ME9xa1lCR1BXb1I4eUdfamo1RUl2SmZJU0ptQ0pkTndLdTk3S2I3VUtXd1RyWlg5S0pndUxzaUlPdnFpV2hyaTFfOTlfT3Y1QTFqcXhzWDdHX3NzY3lnUGRhS29jOWZ1WU5oVHpTM0lZYThDdk9aelRuX3JnMTZIR1VNLXNaUQ?oc=5) 对照：openPR 10月8日标题为 250亿美元扩展，Unisba 标题为50亿美元合作；事件相关但金额和阶段不同，无法据标题核实该声明、先后或原创。

### kalkinemedia.com：建议保留

全量 32 条：低价值 2（6.2%）、潜在有价值 6、证据不足 24。

保留：多数仅有泛化数据中心话题的标题与重复题目，无法据此断定正文无新事实；2 条明确以短期价格和估值为核心。已有业绩指引及首个电力模块历史片段具有实质事件，虽在其他来源可找到相似记录，也没有证据保证未来其他 ticker 获得相同覆盖。有限样本不足以证明全站全局封禁收益超过误杀。

正文补充：retain。8 篇均 403，旧片段有 Bloom 指引与首电力模块等事实，但大部分只有重复标题；不能证明这些内容独家，也不能因未抓到正文认定低质。相比无条件全局封禁，以现有证据保留更能避免其他 ticker 的潜在误杀。

典型低价值：

- [Tower Semiconductor (NASDAQ:TSEM) Slips: A Chance or A Warning? - Kalkine Media](https://news.google.com/rss/articles/CBMipwFBVV95cUxQNTFseUYzSDZvWVRiY1JmRXd6d1hIOC1ucTRZcjNZa1RrT2lpTnVEWmlLWEYxRzFBMmRYQXVqUUZLQ09IVUJPUlc5aTZ4R3R2ZW9WNzlWdHVMS3JJb2tQSHBkTHpHTkcwQ2ExV2szV3lNM3BpV1BkTlV5c0dQSC1nVEswUjJPRjkxN3ExdWRFRmpHd1pOSHgzUUxUMnNFRVNlMklvOHZfYw?oc=5)（2026-10-20T20:00:00+00:00，ID 2e3ab0de06c26d56）
- [Bloom Energy (NYSE:BE) Just Dropped Hard: Is It Undervalued? - Kalkine Media](https://kalkinemedia.com/us/stocks/industrial/bloom-energy-nysebe-just-dropped-hard-is-it-undervalued)（2026-10-21T11:23:00+00:00，ID 941192012c182a20）

潜在有价值例外：

- [Why Is FuelCell Energy Drawing Scrutiny Over Disclosures? - Kalkine Media](https://news.google.com/rss/articles/CBMinwFBVV95cUxPNkNsQmVENWl6b2Y4M0c2UDVPV1B5bW8yTW55Yzd3TjUtUjhfekE5WW5URlhtMDZHWlNTdUV6WUl2UE5tQnozbGZQNjBYemxYOER0UGZ5aXJfdGJ0a0ZjU2trZU1ZTDh4Tk0wdnVXb2xfZEJJZW5jb0Y0MXlOdVhlOWFOUy1BTjZNRnA0OU5YYVRTanBWSlpKUkVnRmY3aWs?oc=5)（2026-10-09T17:56:00+00:00，ID 8026990a45e5d47e）
- [Can Intel's (NASDAQ:INTC) Foundry Comeback and Apple Chip Reports Keep Driving It? - Kalkine Media](https://news.google.com/rss/articles/CBMiugFBVV95cUxORmNGaUtXU3QwTWRRTnp2UzJTWVN4eTd3cUZKN1JLYnlLLWYzVko2RHNyR1c4WFE0T1Jsa2hkZmxrSHVnSWxnRkZCTFp3eXQ3WWtvWHFOWjNnYkRMMnd5VnJFaEUzWXVMaWVhUEJaQkJNYUU2RkdvWENHUTlUNmFwT1hTNTFUNk5McGZWZGJlRHZpZm1PQjJMQzJyeUJURUlLLXlCM2d0ODZabFdlbGU5bUxWWmtZcTkxSHc?oc=5)（2026-10-12T07:00:00+00:00，ID 43551b8cbf0a6f3a）

与 [Bloom Energy says first power block is ready at Nebius’s 300 MW New Jersey data center - Blockspace Media](https://blockspace.media/insight/bloom-energy-readies-nebius-power/) 对照：Kalkine 片段显示首个电力模块完成、容量协议；Blockspace 10-23 记录标题明确 Nebius 新泽西 300 MW 首个电力模块，Kalkine 10-26。标题/片段支持同类事件，未完整核原文，不能确认全部细节相同或独有。

### pcguide.com：建议封禁

全量 28 条：低价值 26（92.9%）、潜在有价值 1、证据不足 1。

28 条绝大多数是零售折扣推广，不是新公司经营事实；Amazon CPU 榜单是有限渠道份额信号，Acer 上市预订有潜在产品事件，但非全市场份额且独特性和首发不明。全局封禁会损失少数消费技术线索，促销噪声占据明显主导。

正文补充：8 篇补抓均 403，没有获得新正文；促销占样本主导，Amazon CPU 排名只代表渠道榜单且原文、独特性及首发未知。维持 block，承认会漏掉少量消费硬件及竞争信息。

典型低价值：

- [Dell RTX 5050 gaming laptop with 240H and 32GB DDR5 RAM is worth checking out at its 30-day low price - PC Guide](https://news.google.com/rss/articles/CBMizAFBVV95cUxPMHJZTXIyRktHOVFEaFhoSmdEYWMybzN3TWdXVW94cElpekVvd2s0NEJueDFhUG83NjdpTWhSeEF5a0xBZzVqUVdLTnRkU1dxLWVnMGJ2Y2x6Z0pDN3dsN0NUVjJFZVcwcWxUbkpPeFBKUkFRNzQ4emxnZTBDMU9CVFlOUGs0Z293TlJWblpVOTRKTUpkcG4xYmJhSl9oZV8wWktLVVU5V0ZyOFl0blE2VG9uTERjcnFCc0w2Zm1vTkRfTlc5bmJuYkk0dFI?oc=5)（2026-09-28T07:00:00+00:00，ID 4a0ec79c91ce854b）
- [RTX 5070 and Intel Core i9 14900KF PC with 32GB of RAM drops to just $1,900 in this phenomenal deal for $600 off the MSRP! - PC Guide](https://news.google.com/rss/articles/CBMi4wFBVV95cUxOSDJid0FPMW4xRU9kXzNGUDJkRXd1QzMyVEduNFh1MmpzN1FCQ203LVpiYVBpMDlQeUdYZHBRTUFqcjVBbU54T29iWjNDekdiU3hMVkdXLTZMQW5Rdy1xd0FTYnZQSWYxZTVHbmlsZnBSQVFLUm9tc0NPNm5sSXNwY1FXejl0ZFE5b2NKX01QaDhfOXozZ3l6Mjg5WUNoQmpuM0VWbzFzZTg0Um5acEdRWmVGTW1zLU96aDY3bWl0b3dudS05dkZoRmNCdktsbVB6SENrY2JGdl9KeGRESVZkNHpDcw?oc=5)（2026-09-28T07:00:00+00:00，ID 6ddcdf5b7c2081ad）

潜在有价值例外：

- [AMD continues to dominate Amazon CPU sales, with only one Intel processor in the top 10, and it's a last-gen model - PC Guide](https://news.google.com/rss/articles/CBMi2AFBVV95cUxPNDlBbTBsWndfTmwwMWZPMTNSbVdvMGxFZkJnQWFkSkduenlXZGVPczhGdEFJSmdlQ254UGY5elR4Nngwc0NzR1M1U0piZXl4Q3YzcFd5Qmx1bUJDRFhmZVBKX285dHdTem1Db2h6aDlWQVVoT2FKTmIzYTN5WUplRmROM0dZOUhuU29IQlB6LWRaQ1lzcU9IUFd0ZDdydmx4VUs4YjJVakdBWFlRdm1VUkYybVY2a09NUGxyYzRGcHhjT09sZjJqR1FYeWFUa0tJUjgxdERDT1I?oc=5)（2026-10-14T07:00:00+00:00，ID aaa1f822d3f72ffd）

与 [Steam Hardware Survey: Intel CPU Share Drops To Lowest Figure, Nvidia GPUs See Bump, Octa-Core CPUs Hit 30% - Digital Foundry](https://news.google.com/rss/articles/CBMi6gFBVV95cUxPc1U2cEdxaGh3V2lsdzJmb01CNjZDdS16ZE5yREdIaURTQVBWUlBDeHBiby03VXRQYU1meVFPa25odkcyWlkya0hCcm1MbW9oMklIMlJ3b0laMzFKRW96LUFqckZBUWtyNTdRSVVTVm9GR2FlNGY0dkpJOGZ5cGdhM2ExU2Z3VFVLdUlfUExoWkczaUpSaUR2YnJQdVFvbks1YjNGUy1Xazh5Wnd3TUhxVExsc0hMZnc3MDNZeGkxNFBMUHdja0VMOTVzVWxIN0l5QmJLb2FIZER6UURSbFRrQi1iVnBFNFFnSWc?oc=5) 对照：Steam 调查与 Amazon 畅销榜都是竞争线索但样本不同，不能直接比较份额或认定同一事件；均未核实原文，07:00 UTC 不足以判断小时级早晚。

## 对早披露和替代覆盖的边界

曾对照 MarketBeat 的 Oracle 项目与 ENR 报道，RSS 显示 ENR 的日期更早；但 Texas 许可冻结合并公司合同分析的具体内容不相同，不可说另一篇完全替代。Digital Foundry 的 RDNA 5 澄清对其他技术传闻有一手纠偏价值；AOL 正文有明确转载来源。其他例子的逐项对照见子 Agent 完整报告。Google 返回的不少历史时间固定为 07:00 UTC，可能只有日期精度；且系统中其他源当时是否订阅并及时接纳没有逐篇证明。因此本轮给出封站建议，但不声称所有被挡的重要文章都可替代或证明某站没有早披露。

## 方法和证据

- 复核的是首轮实际召回而非站点全部文章；30 次/ ticker 日查询及拆词补查仍有若干 100 条触顶。Google 索引限制可能改变站点排序和比例。[首轮报告](google-news-search-site-blacklist-review-20261027.md) 保存完整扫描方法和 Top 30，报告开头已标明本报告取代首轮 12 站“待复核”状态。

- [12 站逐条标签与最终结论](../eval/google_news_blacklist_audit_20261027/final-decisions.json)、[111 条原文补查结果](../eval/google_news_blacklist_audit_20261027/final-body-evidence.json)；原始 4 组全量审核 `final-review-1/2a/2b/3.json/.md`、补查 `final-evidence-1/2/3.json/.md` 和标签更正 `final-label-overrides-1/2/3.json` 均保留。

- 生成前断言 12 站各自所有 article ID 与标签一一对应、无重复，并断言四个候选域名命中 212 条；对原文补查和报告生成 SHA-256 证据清单。生产消息、订阅及过滤配置没有修改。
