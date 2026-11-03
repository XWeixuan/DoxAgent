# Reuters 出口/Profile 对照与真实搜索、排队修复（2026-11-04）

本轮记录使用北京时间日期，证据文件中的时间为UTC。基线代码28e559af；生产Site Access仍为a7dcec8b，Supervisor未重建。只涉及Reuters及其通用访问预算中的一个错误分支，不修改其他消息源或轮询时段。

## 现状与确定的根因

1. 主身份digitimes-de-1的当前DataDome原页为明确IP拒绝（t=bv），原出口205.198.126.113；不能继续把该现场称为可解滑块。备用reuters-backup在205.198.126.114已有持续“搜索成功回执”，不是两个身份一直全无访问能力。截图上的“Access is temporarily restricted”本身不足以证明属于Reuters，本轮以Registry和原生页面证据归因。
2. 基线近40分钟有765次备用CRAWLER成功回执、897次site_queue_timeout，后者都在约3秒且network_ms=0退出，实际未向网站发请求。备用单并发、3秒间隔与采集队列3秒上限冲突，多个query并行到达时大量排队失败。22个词加上分页/正文需求也不能保证用同一个单并发身份全部在60秒内完成；不靠调高网站并发掩盖容量事实。
3. BODY近40分钟另有2008次fallback_not_ready。代码对任意备用身份都要求剩余预算至少5秒，遗漏了它已经是活动身份、无需冷启动的情况；这会把短预算正文请求拦在网站请求之前。修复仅对尚未活动的备用保留冷启动门槛，维护/鉴权/真实冷启动约束继续保留。
4. **原“搜索成功”不能全部当成真实搜索成功。** 对照实验看到，URL已有query=Micron，但页面搜索框仍空，正文是`No search results match the term “”`。旧程序的就绪判断和恢复验证接受这个初始空壳，因此返回零条并报成功。人工式正常填词提交后，同一旧IP、正式Chrome出现`Search results for “Micron”`及40个文章链接（图片链接/标题链接重复，解析后20条新闻）。这直接证明现有业务搜索流程缺陷，不能把全部问题归咎IP或DataDome。

因此，上述765条只是旧语义的SUCCESS回执，不能作为765次正确搜索的证明；本轮没有追认旧数据，也不回写旧验收记录。此前“原页CLEARED/CRAWLER验证”若走了空查询判据，也不能作为持续业务可用的充分证明。

## 受控试验结果

均为普通启动的正式Google Chrome153 + CDP，固定环境，无UA伪装、stealth、密码搬运或额外付费打码。轮间等待60秒；实验身份未挂到正式采集scope，结束后均停止Chrome并禁用实验Identity。原始用户Profile保留。

| 条件 | 出口 | 结果与限制 |
|---|---|---|
| 旧Profile状态 + 新IP | 土耳其185.40.106.84 | 一致性快照副本；修正搜索提交方式后Micron/Intel/Nebius三轮实际分别20/19/20条，无Challenge |
| 干净Profile + 旧IP | 德国205.198.126.113 | 原方法只读到空壳；正常搜索流程三轮20/19/20条，无Challenge；最终候选代码重复三轮通过 |
| 干净Profile + 新IP | 土耳其185.40.106.84 | 第一次遇自动interstitial；后续候选流程三轮20/19/20条，无人工解码 |
| 其他方法：正常UI查询及翻页 | 德国205.198.126.113 | 原生搜索框提交、Next stories；offset=20第二页真实20条，通过当前query/offset匹配验证 |

旧Profile的对照使用Owner一致性快照，先正常drain主Chrome再复制私有状态，仅在服务器内落盘；不是清空原Profile，也不是复制正在写入的Cookies数据库。快照`digitimes-de-1-20261103T174227695449Z`，SHA256 `788ead709fd5b95623d52149e63ff92ff1cc559f1aa66ba0e7f22c34dcb9a93f`。该操作会使旧主Chrome活页不能继续存在，Profile保留，未重建Supervisor；工作中的备用Chrome没有被停止。副本重新启动Chrome存在进程因素，因此不把试验结果解释为IP的唯一因果证明。

这些是短时实际搜索通过，不能证明几天持续稳定，也不能证明新Profile拥有订阅正文权限。生产保留已登录的活动备用身份，不直接用匿名实验Profile替换它。

## 已实现的修复

- Reuters采集队列等待上限由3秒改为20秒，Registry CAS revision15；保留浏览器访问间隔、并发及60秒轮询配置。不把排队延期当Challenge，不循环重置请求预算。
- 活动备用身份的短预算请求不再误入“冷启动不足5秒”分支；真正未激活备用仍保留门槛。
- 搜索recipe识别未初始化的空查询页，使用真实搜索框提交；已有查询页面可正常切词，分页使用原生Next stories，必须等待对应query/offset及页面推进。
- 观察浏览器自己发出的`articles-by-search-v2`请求，精确匹配keyword/offset。读取该响应的数据，保留published_time；不自行伪造/重放网站内部API请求。等待真实响应后解析，避免异步页面仍残留上一查询DOM时抓错新闻。
- 请求回执记录同一个实际搜索XHR；有该响应时将页面初始化导航标为辅助，防止一个搜索被重复计数或被Collector判为多条含混listing证据。
- 原页恢复的只读验证也拒绝空查询占位页。不能仅凭URI中的query和`No search results`字样判定业务通过。

## 回归、发布与生产验收

相关回归最终90项通过，含活动/未活动备用短预算分流、空查询/错查询不能证明Micron搜索、原生响应解析/精确发布时间及请求绑定。候选代码在隔离的正式Chrome上实际得到有内容结果，并完成第二页验证。Ruff F/E9与diff检查通过。

首发`daee305329a4e3eeaaa7281172010c45a35d0919`于UTC18:01:44窄发布Site Access。首窗18:01:44–18:03:11已出现15次CRAWLER成功和4条正式正文FULL，但仍有29次采集排队超时，不能称全部恢复。进一步确认：单并发备用不足以承载现有HEAD/TAIL和正文工作量；主身份已由新搜索recipe真实恢复，于本轮通过Owner `ACTIVATE_PRIMARY`重新验收并接回主路由，保留其原配置的并发2/1500ms间隔，不增改网站访问速率参数。首发后实际新增的合法空结果响应省略articles字段，旧严格parser将其报为invalid_reuters_search_response；已用Ondas原生页面复现并补齐，仅在当前query明确零结果且匹配原生响应时接受为空。

Collector侧同时保留原生published_time为EXACT，避免再次降格为纽约中午的日精度时间，减少不必要的正文取时间任务和错过30分钟实时窗口的问题。该补充需要更新Collector，不能只替换Site Access镜像。

补发`bad5251443a8c76d849d525e8343a067e4d7d293`于UTC18:14:17更新Site Access及Collector，Supervisor未重建，5个相关运行文件哈希逐一匹配。18:14:17–18:19:50有208次真实CRAWLER成功回执、49次采集队列延期，无新增Challenge/付费任务；当前10个ticker的26个有效QUERY最新状态全部succeeded。该窗cycles中尾页排队失败42次、主查询排队失败3次，分别保留；不能把最新全绿当窗口内没有失败。

该窗另有UNPROVEN_LISTING：完整physical_cycles证实一个操作有两个同query/offset的200搜索响应，都被当作listing，造成歧义。收尾修复让recipe标记真正完成JSON读取并被解析的Response.request；Runtime将其他响应标为辅助，保留物理请求数量。若Node未先发完结事件，已完成实际JSON读取的时刻可补足该响应完成证据，不伪造请求发送时刻。证据为proof-gap.json。

正文方面，首发后已有多条正式FULL回执，尚不能把某一发行版本无新增正文任务当作正文失败；保留精确时间后，窗口外旧文章不会再仅为取时间而进入正文。单独listing canary曾因无排程due优先级在高负载下排队超时，未计成功。最终版本仍需分列自然采集、单独正文验证及残留延期，不以一次canary外推长期稳定。

单独正文验证已通过：原主身份digitimes-de-1，Chrome instance `5584793148dc4bb7a77b8aa25d53037f`，HTTP200，排队1548ms，独立正文解析FULL/complete_article，1243字符。该验证是手动canary，不冒充自然新增文章或所有订阅正文均可见。

剩余容量边界：26个HEAD/分钟，若均再产生一个TAIL，便超过现有主身份1500ms启动间隔所能提供的约40次/分钟，且正文也共享预算。此次保持既有节奏，优先保障HEAD；未通过提高请求频率或把延期伪装成功来声称容量无限。后续若要求尾页也每分钟全部完成，需要独立评估补扫窗口与容量，而不是继续换IP。

脱敏证据位于`eval/reuters_egress_profile_20261104/`：state/state2、queue-change、profile-snapshot、candidate-old-ip、candidate-oldstate-new-ip、candidate-new-ip、final-candidate-old-ip、pagination-old-ip。最初实验脚本把零条标为LISTING_OK的文件保留，但本报告已依据截图将其纠正为未验证空壳，不计通过。完整CaptchaURL、Cookie、代理密钥未输出或提交。
