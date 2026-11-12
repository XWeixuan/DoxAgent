# 五个 Massive Newswire：生产根因、修复与验收

日期：北京时间2026-11-13。源为 Business Wire、PR Newswire、GlobeNewswire、ACCESS Newswire、TMX Newsfile。使用 main 工作树，未修改其他会话未提交项目。

## 结论与实际恢复范围

已修复正文正式入口未完成切换、原站HTTP方法错误、正文展开误识别、本地分发饥饿及部分缓存诊断问题，相关代码已推送 origin/main 并窄发布。不能据此称五源已经通过长期可靠性验收，GlobeNewswire历史发现长尾和ACCESS上游宽限缓存仍有明确边界。

正文最终入口修复后的自然业务窗口为 UTC **2026-11-12 22:59:32–23:23:54**，即北京时间 **11月13日06:59:32–07:23:54**。窗口包含后续Site Access与Collector发布，非纯稳态。以下均为生产自然新增文章，不包含正文canary，也没有补发旧测试稿：

| 来源 | 自然新增/FULL | 新稿发现延迟均值 | 正文完成→本地判定均值 | 最终分发 |
|---|---:|---:|---:|---|
| Business Wire | 9 / 9 | 51.61s | 5.46s | 81个文章×ticker阴性，无PENDING |
| PR Newswire | 3 / 3 | 36.15s | 5.43s | 27个阴性，无PENDING |
| GlobeNewswire | 2 / 2 | 151.61s | 8.97s | 18个阴性，无PENDING |
| ACCESS Newswire | 1 / 1 | 82.63s | 3.42s | 9个阴性，无PENDING |
| TMX Newsfile | 9 / 9 | 35.90s | 8.20s | 81个阴性，无PENDING |

合计24篇全部FULL、216个本地分发均完成。没有正则阳性，所以这个窗口没有新的正式发布正例；没有为了验收修改规则或强制将阴性稿投入交易消息流。小样本结果不能外推长期成功率。列表部分时间只有分钟粒度，发现延迟存在最多约一分钟的量化误差；不同来源不应直接按毫秒排序。

最后一次发布完成后 UTC23:21:28–23:23:55 的短稳态窗：每个物理入口5次采集，GNW两入口分别5次，均成功；BW与TMX各自然新增1篇FULL，另外三源无新增文章。ACCESS其中2次标记缓存时效退化，不能因HTTP成功就说内容必然最新。

## 1. 共性根因：正式模式仍绕开站点正文策略

不是“所有网站都需要登录”或“换浏览器就好”。原实现有两层独立短路：

1. `SharedContentExtractor.extract_version` 对非Bloomberg的 `_massive` 源直接返回本地 `_pipeline.extract`，即使请求 `body_v2.2`、已经注入 SiteAccessClient，也绕开受管策略，实际沿body_v2.1式公共路径运行。
2. `ArticlePipeline._extract` 再把所有这些来源的访问顺序覆盖为HTTP/Reader，TMX为Reader/HTTP。即使直接修正文管线，第一层仍会阻止生产入口使用它。

这解释了“已有Managed浏览器或独立探测成功，正式作业仍全失败”。首轮发布的实际canary暴露第一层仍存在：BW/PRN通过本地HTTP取得FULL，TMX却仍使用Reader；生产一篇新稿仍render_required。该中间结果已保留，没有当作最终通过。

最终将BW、PRN、TMX的body_v2.2明确接回已有Site Access管线；具名Registry策略不再被Massive默认覆盖。GNW和ACCESS已经有效的公共HTTP及其出口保持原样，未将所有来源升级为长期Chrome。新增BW/PRN/Newsfile站点策略沿用既有Registry、Profile和Managed Runtime，不引入新服务或状态机。

## 2. Business Wire

### 真实失败链

初始生产快照173篇全部UNAVAILABLE。原站普通GET返回403；日志最后的 `http_401` 实际来自 `r.jina.ai` Reader，不能解释成Business Wire账号掉线。原先并未真正调用正式受管正文路径。

同一篇 Helen of Troy 稿件的生产出口对照：GET403，普通只读POST200、完整HTML约451KB，RSC POST也200。普通POST已足够，不需要新增浏览器或解析RSC正文流。页面JSON-LD `articleBody` 只有160字符，不能把该teaser直接当正文；真实全文在 `#bw-release-story`。

### 修复与证据

- 正式Site Access HTTP和本地公共传输共用严格限定的Business Wire文章请求选择：仅原站 `/news/home/<数字>/<语言>` 使用只读POST，去掉末尾跳转斜杠。其他网站、Reader、newsroom列表不受影响。
- 提取实际文章容器，保留原有完整性/标题校验，不强制短摘要FULL。
- 两篇最终正式管线canary：Helen of Troy FULL14382字符/2.510秒；CKX FULL1343字符/0.852秒。Site Access trace均证实走Business Wire原站HTTP。
- 自然窗口9/9 FULL；HTTP列表47次成功，另有1次发布取消。未因正文修复更换原已可用的RSC/POST列表。

历史8.88分钟发现长尾的几篇稿件均在同一RSS fallback capture中出现，poll_due到收到列表约22秒。RSS备用不能证明HTTP当时及时发现；历史原始RSC页面没有逐页保留，尚不能把此前整段延迟完全归因于上游或客户端分页。当前正常新稿样本已单列，未重写旧发布时间。

## 3. PR Newswire

### 两个独立问题

- 列表旧配方使用POST；生产连续返回405。直接GET返回200、100条，第二页另100条且与第一页无重合。故这是HTTP方法/接口契约问题，RSS虽接续收到消息，却没有修复HTTP。
- Edun等文章HTTP200且已有数千字符完整正文，但手机端 `Continue reading` 链接指向同页 `#continue-jump`。旧通用识别将跳转导航当作待展开内容，进入Reader后401，最终PARTIAL；属于正文误识别，不是网站不给全文。

### 修复与证据

列表改为GET；正文限定在 `section.release-body`，只在正文内目标确实存在时排除上述跳转导航。真实展开按钮、缺少目标的Continue reading和登录墙仍不能FULL。Registry保留HTTP优先、Managed浏览器作为失败后的可用候选。

最终正式canary：Edun FULL6331字符/0.343秒；Willamette FULL5869字符/0.246秒。自然窗口3/3 FULL，48次物理HTTP列表成功，未以RSS冒充HTTP恢复。生产此前71 FULL、22 PARTIAL、1 UNAVAILABLE的可比历史子窗另留档，未据一次请求解释所有历史PARTIAL。

普通GET与加时间参数的URL均命中同一份CDN缓存，Age44秒、TTL60秒、列表完全相同。没有加入这种无效刷新参数，也没有承诺30秒轮询就能保证30秒内发现。

## 4. TMX Newsfile

HTTP202实际为AWS WAF自动JavaScript验证：有的响应空，有的2490字节、标题JavaScript is disabled，带 `x-amzn-waf-action: challenge`。GET/POST均如此，不能把它作为正文。原Managed探测立即取页面，只得到bootstrap；正式流程还完全绕开浏览器。

既有Managed浏览器有界等待后，初始202自动导航到真实200，取得Mayfair全文。新增Newsfile独立站点策略使用Managed浏览器、`article#release p` 就绪条件、15秒浏览器阶段；原20秒总预算不变，RSS列表不改。只有观察到同主文档的真实200响应才更新状态/头，保留最初202的网络时间线，不伪造200。

真实生产负载下首次两篇受管canary曾15秒超时，不能隐藏。后续回执显示页面可正常200，但该身份按既有 `public_identity` 回收Chrome，冷启动和访问判定中的重复通用正文抽取放大耗时。去掉访问判定不需要的通用抽取，最终质量校验保留；未修改全局资源回收或增加并发。

最终两篇正式canary：Mayfair FULL6536字符/11.725秒；LGL FULL18273字符/8.787秒，均为 `browser_wire_article_body`、Managed Playwright、原NL出口，非Reader结果。自然窗口9/9 FULL。无需新External Chrome、人工登录或付费验证码；仍可能因冷启动/突发排队发生延期，不能承诺任意峰值零超时。

## 5. ACCESS及共用本地分发

初始报告中的PENDING后来已自行收敛，但不可因此否认问题。完整时间窗证实：ACCESS正文结束后，本地判定平均约652秒、最长2429秒；GNW/BW也有同类长等待。原Worker每轮预算0.25秒，虽然每8轮选一个旧待办，却放在新待办之后；新任务持续占满本轮时，保留名额永远得不到执行，直到老化或流量下降。

修复为每8轮先执行一个最旧待办，按文章+ticker+binding去重，再继续新稿；不提高全局并发、不新增队列。Massive仍必须重新判定正则，故阴性/无正文分支无需提前查询已发布历史；正例发布阶段保留原去重及事务边界。回归覆盖在短到只允许执行一个任务的轮次中，旧待办仍推进，下一轮新任务继续推进。

自然窗口ACCESS完整正文后判定平均3.42秒、最大5.34秒，无PENDING；该窗口没有正例，不能把阴性终结当作新的正式流发布验收。

ACCESS另有独立缓存问题：Age73秒，`x-cache=hit graced cached`，HTTP Date仍是当前时间；原站声明 `max-age=60, stale-while-revalidate=300, stale-if-error=600`。不应单凭Date和200判定新鲜。no-cache有时得到较新版本，但不能可靠消除宽限缓存；随机参数400，POST401，未部署这些错误路径。

最终保留GET、请求重新验证，修复ETag基线覆盖固定请求头的问题；保持既有120秒硬上限，将TTL外但仍可用的页面标记 `freshness_degraded/publisher_stale_grace`。开发中曾短暂把60秒作为硬失败，造成3次人为HTTP失败，已纠正并保留回执。最终短窗5/5采集，其中2次时效退化：**可用性与新鲜度分别记录，未把上游缓存问题宣称为已消失。**

## 6. GlobeNewswire与覆盖边界

保留HTTP/RSS并行及原出口，不新增Chrome。可比历史子窗209篇全FULL，HTTP330成功/1降级、RSS332成功。自然修复窗口2篇全FULL，两路分别49次成功。RSS偶发零交集只说明溢出风险，另一HTTP入口可能补齐，不能直接当作确已漏稿。

历史发现延迟中位113秒、P95约219秒，四篇16:05 ET稿件直到16:19:08才在首个HTTP capture出现；poll_due→listing_received约0.79秒，故14分钟不是BODY排队或该次请求调度迟发造成。按release ID+语言检查未发现重复owner，排除了本批样本的URL差异假延迟。

实际HTTP/RSS对照时，两路顶端同一release ID，均有约60秒缓存策略；列表与RSS的深度分别50/20。现有历史只保留处理后的输入和回执，不能证明这些稿件在此前每份原始列表里的实际出现时点，**上游索引晚出现与客户端有限分页/晚插稿仍未完全区分**。不把2.5分钟新样本均值或当前双路成功外推为全部发现及时。

为后续定位仅在已有cycle增加最新/最早可见发布时间、已见交集、缓存退化标志；无额外轮询、截图、抓包或新表。另修复正常HTTP分页中途失败后，partial/RSS分支漏用正常路径30分钟边界的实现差异，防止失败恢复时误纳入更旧卡片。

## 发布与验证边界

- 核心/Registry校验：db23633ba、85737e532；正式总入口：0e789d7a4；访问判定优化：3298c63d2；最终缓存分流：1a9dafa43。中间严格缓存版本c329fa79b已由最终版替代。
- 通过现有 `storage_release.py` 精确SHA窄发布；最终Collector为1a9dafa43、Site Access为3298c63d2、Enrichment为0e789d7a4、Bus为85737e532的相关内容，11个相关源码文件SHA256与main核对一致。
- Supervisor容器仍是 `9bc69ac8...`（11月10日启动）。现有Chrome/Profile、账号和出口未重建/切换。recheck仍关闭，未批量重跑旧失败正文或重新投递历史测试稿。
- 9×5订阅、Massive独立词库、JEV关闭、FULL门槛、30秒轮询及业务接纳规则保持。Bloomberg、Reuters等并行开发未纳入本轮调参。
- 相关组合68通过/1可选fixture跳过；Registry/共享路径73通过；访问判断/维护68通过；最终列表33通过，ruff严重错误检查通过。组合有重叠，不相加为独立用例数。广测曾在132项通过后发现新就绪参数未获Registry校验许可，已修复并补全bootstrap回归；未把该次失败报告当全绿。

最后稳态只有约2.5分钟，正文入口自然窗口约24分钟。仍需在开市集中发稿时评估冷启动、队列与稀疏RSS覆盖；没有安排额外自动化，也未为制造样本增加新闻站请求。下一步应重点复核GNW/BW历史晚出现稿件的原始HTTP/RSS出现时间及分页边界，和ACCESS上游宽限缓存，而不是继续按401/Challenge笼统换IP或改时间戳。

证据：`eval/newswire_repair_20261113/acceptance-summary.json`，正文canary与各次发布回执、只读审计脚本均在同目录。归档摘要不含正文、Cookie、管理令牌或网站凭据。现场公共HTML仅作为临时诊断材料，未提交Git。
