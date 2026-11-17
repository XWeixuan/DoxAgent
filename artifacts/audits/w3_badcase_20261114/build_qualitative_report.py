import json,html
from pathlib import Path
p=Path(__file__).parent
cases=json.loads((p/'case_manifest.json').read_text(encoding='utf-8'))
records=json.loads((p/'selected_records.json').read_text(encoding='utf-8-sig'))['cases']
def esc(x):return html.escape(str(x))
def para(x):return '<p>'+x+'</p>'
def link(path,label):return f'<a href="{esc(path)}">{esc(label)}</a>'

sections=[
('COHR：Fermi / TensorWave',[
 '原消息为 Data Center Dynamics 的 <b>Fermi does 600MW power deal with hyperscaler, but TensorWave lease is officially dead</b>，当前文章发布时间为 11/13 09:53:30Z，消息截点为 09:57:21Z，W3 最终 SHORT，SDK 约49.2秒。W2并未命中：它保留了租户退出与新600MW安排是否替代的真实歧义。W3在独立评估中把“222MW旧租约及AMD信用支持正式撤回”选为决定性变化。',
 '它实际补查的是内部 TensorWave / Fermi 事件以及 E120，目的是把650MW上限与222MW已签部分分开，并恢复“错过10月31日交割、双方当时仍协商”的旧状态；随后调用行情。它没有追查原公司公告、终止声明最早公开时间、原租约完整交付窗口或此前市场传播。其搜索返回的仍是E120，新增研究没有补出原始披露时间。',
 '<b>可直接确认的时效失职：</b>原正文已明确引用“Thursday’s statement”，而当前文章日期是星期五。公司公告 <i>Fermi Announces New Agreements with Investment-Grade Hyperscaler and Third Quarter 2026 Financial Results</i> 已在11/12 07:00 EST（12:00Z）披露租约终止和新客户安排。到本次消息截点已约21小时57分钟，而W3传给market_view的event_time却是次日DCD文章的09:53:30Z。它没有说明这次DCD再传播带来了什么重要的新解释或新的有效受众。终止实际发生日与首次公开日也不同，不能拿11/10的终止日直接当公开时间。'+link('https://www.newswire.com/news/fermi-announces-new-agreements-with-investment-grade-hyperscaler-and-22888329','公司原公告及07:00 EST时间'),
 '<b>业务推断断点：</b>租约终止确实改变Fermi项目风险，但这不等于已经充分证明COHR整个ticker的光连接机会有分量地下修。原租约本就错过交割，旧状态不是无风险必然兑现；新客户虽没有同等正式租约，却有600MW开发与2亿美元前期承诺。不能把“尚未证实同窗等量替代”当成“已证实可归属COHR的重要净需求损失”。最终理由用“无需COHR具名供货”跨过了这个缺口。无需具名订单可以成立，但仍须论证传导概率、代表性和ticker级分量；本次没有完成。',
 'Policy是实质推动因素：融资/已签容量Policy的C2明确允许在不要求目标公司供应关系的条件下，凭重要阶段的租户/信用撤回判断SHORT。W3找到了该情景，却未独立完成公共AGENTS要求的ticker级Surprise评估。它不是把600MW误当650MW、也不是读错终止事实；错误发生在事实之后的赋权与时效上。',
 '同篇消息另被BE、CEG、MU、NBIS和INTC抓到，五者均NO_TRADE。BE/CEG/INTC强调目标公司连接与分量未建立；MU强调新旧承诺与同窗替代未对齐，净方向未定；NBIS不把竞争者项目终止自动换成自身新需求。这些结论不是因为少看了同篇600MW安排。对照显示，COHR将未解歧义用于维持SHORT，其他ticker则让该歧义实际影响是否交易。'
 ]),
('BE：Nebius基础设施延期',[
 '原消息为 HuggingNews Atom 的 <b>Nebius Shares Drop 15% as Infrastructure Delays Hit Revenue Outlook</b>，RSS published_at为11/12 13:53:34Z，截点13:53:50Z，W3最终SHORT，SDK约66.0秒。它把旧的第三方140MW延期风险升级为“客户确认DataOne许可/供电问题、微软容量及收入转至2027上半年”，认为Bloom关联项目的重要近期商业窗口可信度下降。',
 '它先用长查询“DataOne Vineland 延期 现场 交付”检索内部资料，得到空结果，随后翻阅Policy目录，找到并读取“大负载项目关键路径与现场并行兑现能力”，再以短词DataOne检索背景。该Policy不是本次W2命中的条件；W2原本只RELATED另一条AI计算/现场负载Policy。自主使用其他研究并非违规，但本次研究主要完成的是找到可套用的C3负向关键路径情景，而不是补齐当前披露的时间和因果细节。无Web Search、无Nebius原公告或股东信读取。',
 '<b>时间字段本身已有冲突：</b>正文写着“Nov 12, 7:43 AM EST”，即12:43Z，比RSS的13:53Z早约70分钟。W3仍把13:53:34Z传给行情工具，原始公开时间未被核对。更早的完整股东信已出现在本轮取到的12:34Z/12:44Z公开传播记录中，财报新闻稿为12:11Z；不能因BE自身知识库此前只有第三方风险估计，就认定13:53这次转载是市场首次得到公司确认。12:34完整版距BE截点已有约79分钟。这里不以IR接口05:00的日期占位字段作为精确首发证据，也不把14:22才入库的SEC文件倒用为决策前已读证据。',
 '<b>原始材料改变解释：</b>股东信明确区分DataOne提前退役临时发电、微软剩余批次转至2Q27，与Bloom已现场发电、正在扩容及未来数周增加容量。当前摘要省掉了这一关键区分，W3既没补读，也没有把“园区算力收入延期”与“Bloom商业供电/验收/设备批次延期”对齐。许可/供电问题可能表明电力方案切换的必要性，也可能影响现场节奏；不经核对便归为Bloom商业兑现受损，方向依据不足。此处不据反证机械改判LONG；更可辩护的是当前不交易。'+link('https://www.sec.gov/Archives/edgar/data/1513845/000110465926126008/tm2629571d1_ex99-2.htm','公司股东信原文（用于本轮事后内容核对）'),
 '它的最终reason已承认：不能把140MW当确认量，不能把Nebius收入递延等同Bloom收入递延或订单取消。但这些限制只留在措辞中，没有阻止SHORT。Policy将重要服务窗口受损视作负向情景，W3用云服务窗口替代了需要核对的Bloom供电/接受窗口，再以“行情不足以确认增量已完全消化”保留交易。',
 '同一消息的NBIS为NO_TRADE，理由是完整股东信已提供更具体原因与窗口，13:53转载不重置首次披露；COHR也NO_TRADE，明确云服务收入后移不能直接推导光学采购后移；INTC同样NO_TRADE，未建立产品/订单传导。NBIS是最有力的时效对照：主语公司自身的W3识别为既有信息，BE却从不完整摘要里制造了新确认。'
 ]),
('RKLB：Berenberg目标价85美元',[
 '原消息经Google News Search RSS进入，真实发布者为StreetInsider：<b>Rocket Lab USA (RKLB) PT Raised to $85 at Berenberg</b>。输入有完整分析师引文，不是只有标题。当前标注07:38:18Z，截点07:40:16Z，最终LONG，SDK约39.8秒。原网页显示11/12 02:37 EST，即07:37Z，比RSS标注早约78秒，本次截点距该公开页约3分钟。'+link('https://www.streetinsider.com/Analyst%2BComments/Rocket%2BLab%2BUSA%2B(RKLB)%2BPT%2BRaised%2Bto%2B$85%2Bat%2BBerenberg/27328523.html','StreetInsider原文与时间'),
 'W1把该机构2028年备考调整后EBITDA上调8%认作新增，W2命中卖方独立新判断Policy。W3没有调用Web Search或内部研究，只取了分页行情，然后提交LONG。它明确排除了83→85美元目标价和维持Buy作为主因，选取“2027预测不变、2028盈利预测上修8%”作为足够的正向增量，并指出这是单机构模型、包含待交割Iridium，不是公司指引或市场共识。',
 '<b>时效结论需区别对待：</b>它确实做了“文章距截点约2分钟、夜盘稀疏、没有完整发布后五分钟区间”的评估，没有追查研报更早分发时间。但当前原始财经快讯的时间与入库吻合，本轮没有找到足以坐实这是数小时前旧研报的证据。原规则允许已有来源与时间足够时直接采用，不要求每条消息穷尽全球绝对首发。因此这里可确认的是未独立追溯研报分发，不能直接判定该case违反时效义务，更不能硬归为旧闻交易。',
 '<b>更明确的根因是交易门槛：</b>它用单机构、远期、备考口径的8%模型修订，直接代替相对既有市场认识的ticker级重要预期修正。报告本身说Q3强化既有论点，2027预测基本不变，定价和需求事实多已由公司此前披露。不能以“这家机构的旧模型尚未包含这8%”等同市场尚不知道相应业务变化；也不能把模型增量的数字精确性当重要性证明。远期预测并非一律不能做当日交易，但这里缺少使普通业绩后模型跟随升级为重大Surprise的额外依据。',
 '它看到了72小时内Morgan Stanley的LONG，并明确用“另一机构的2030年售价模型”解释本次并不重复。说明不是忘了历史，而是将机会身份切到券商及预测年份层面，容许相同公司定价/订单故事被不同机构模型连续消费。',
 '后续Investing.com英国、加拿大版，以及TipRanks完整转述，进入W3的三条全部NO_TRADE。后续reason正确指出换媒体不产生新修订、之前执行失败不重置时效；另有Investing.com原始短稿在路由归档。它能识别同一机构同一报告的重复，却没有在首次LONG中评估普通模型更新本身是否值得交易。'
 ]),
('RKLB：Morgan Stanley / TipRanks',[
 '原消息为Google News Search RSS转入TipRanks：<b>‘Get Ready for What’s Next,’ Says Morgan Stanley About Rocket Lab Stock</b>。当前文章11/11 15:40:17Z，截点15:48:26Z，W3最终LONG，SDK约31.2秒。它与次日Berenberg使用同一个持久thread，不是两个独立新thread。',
 'W1知道公司此前已公布新Neutron合同约7000万美元基础价，并召回E188/E189及E252；E252已记录Morgan Stanley下调目标价2美元。W2仍以单机构独立新判断命中。W3只调用行情，无Web Search、无原研报、无更早同机构报道核对。核心选择是Liwag将2030年平均售价从5550万美元提高到6600万美元，约18.9%；2030年收入31.1亿美元和EBITDA9.43亿美元没有旧值，未另计上修。',
 '<b>时效关键缺口：</b>它把TipRanks文章当本次模型更新的起点，称距截点约8分钟，并以“主要上涨发生在本文发布前”排除旧上涨作为本次完成态。可是本轮查到同机构本轮评级/目标价报告的更早专业渠道报道：StreetInsider于11/11 08:38 EST（13:38Z），内部TheFly记录更早，另有MarketScreener 13:39Z报道。到当前截点，13:38那轮披露已约2小时10分钟。'+link('https://www.streetinsider.com/dr/news.php?id=27326830','更早的Morgan Stanley快讯'),
 '严格边界：更早快讯能证明同一机构本轮研报早已传播，但付费正文未完整取得，尚不能确认“5550万→6600万”的具体条款第一次何时对相关受众公开。因而不能把这2小时10分钟直接当作已证实的该数字年龄。能确认的失职是：已知同机构更新、当前文体又是业绩后评论转述，W3却未核实这一决定性细节属于新披露还是旧研报解释，没有说明TipRanks带来何种有分量的传播升级，便断言本次更新仍新。',
 '<b>分量误判：</b>分析师把此前已知7000万美元新签合同价格放入2030平均售价模型，不等于公司再次提价，也不自动形成又一次市场预期跳变。W3用机构模型内18.9%的修订替代了公司/市场层面的新信息贡献。已知目标价下调、首飞延后、研发与稀释等内容不必机械压过所有利好，但必须参与对本轮报告整体增量的判断；它们在最终输出中主要充当风险背景。',
 '该case说明，加入“知识库没记录不等于市场不知道”的文字规则仍不够：W1的模型数字NEW、W2的卖方新判断HIT和W3对当前文章时间的采用，三者共同把迟来的模型解释包装成了新催化。'
 ])]

out=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>W3四个交易badcase复盘</title><style>body{max-width:1120px;margin:36px auto;padding:0 24px;font:16px/1.85 system-ui;color:#182432;background:#f6f7f9}h1,h2{line-height:1.4}section{background:white;border:1px solid #dce2e8;border-radius:10px;padding:24px;margin:22px 0}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #ccd4dc;padding:10px;vertical-align:top}a{color:#155cad}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 system-ui}small{color:#596775}.tag{color:#9b2c2c;font-weight:650}code{overflow-wrap:anywhere}</style><h1>W3四个交易badcase：决策依据、首次披露与上下文复盘</h1>']
out += [para('审计日期按客户端为2026-11-14；历史事件及下表统一使用UTC（Z），北京时间加8小时。生产机时钟仍显示11月13日，因此本报告不用“今天/昨天”代替绝对日期。'),para('<b>核心结论：</b>四次决策均未调用Web Search。COHR/Fermi与BE/Nebius存在可证实的更早完整披露；Morgan Stanley缺少对原研报及本次有效增量的时间核查；Berenberg暂不能归为明显旧闻。共同问题是把局部NEW或Policy情景成立当作ticker级Surprise；在首发有疑点的案例中，又用“未确认行情完成态”保留已选择的交易，未完成独立的时效判断。')]
out.append('<section><h2>原thread与决策定位</h2><table><tr><th>消息 / 来源</th><th>决策 / 模型</th><th>原thread / 原生回合</th><th>可读证据</th></tr>')
for i,c in enumerate(cases,1):
 r=records[i-1];dec=r['w3']['trade_decision']['decision'];src=r['case']['source']['source_id'];ctx=json.loads((p/'reviews'/f'{i:02d}_evidence.json').read_text(encoding='utf-8'))['meta']['turn_contexts'][0]
 out.append(f'<tr><td>{esc(c["title"])}<br><small>{esc(src)}<br>{esc(c["case_id"])}</small></td><td>{esc(dec)}<br>{esc(c["actual_model"])} / {esc(c["actual_effort"])}</td><td><code>{esc(c["thread_id"])}</code><br><small>原生行 {c["start_line"]}–{c["end_line"]}<br>turn {esc(ctx["turn_id"])}</small></td><td>'+link(f'reviews/{i:02d}_decision_and_research.txt','决定与研究主题')+'<br>'+link(f'reviews/{i:02d}_input.json','冻结的本次输入')+'<br>'+link(f'reviews/{i:02d}_tool_results.json','原始工具返回')+'<br>'+link(f'threads/{c["thread_id"]}.jsonl','完整原thread快照')+'</td></tr>')
out.append('</table>'+para('模型以原生turn_context为准：前三条实际为gpt-6-luna/max，Morgan Stanley为gpt-6.1-sol/medium；不能将冻结的逻辑profile中Sol/medium误当所有回合的实际模型。没有对照重跑，不据此归因模型优劣。四次决策的reasoning summary均为空，原生加密reasoning没有解密或冒充可见思维链。')+'</section>')
out.append('<section><h2>时效义务的逐案结论</h2><table><tr><th>Case</th><th>W3实际采用</th><th>本轮核查</th><th>评价</th></tr>')
for row in [
 ('COHR / Fermi','11/13 09:53:30Z DCD文章','公司11/12 12:00Z已公开；截点时约21h57m','未尽到核心时效核查义务；正文Thursday线索被跳过'),
 ('BE / Nebius','11/12 13:53:34Z RSS时间','正文内嵌12:43Z，完整股东信至少12:34Z已传播','未核对时间冲突及原披露；把BE内部首次确认误当市场新确认'),
 ('RKLB / Berenberg','11/12 07:38:18Z快讯','原页07:37Z，距截点约3分钟；研报更早分发未证实','做过有限评估；暂无证据认定明显旧闻，不能强行归为时效失职'),
 ('RKLB / Morgan Stanley','11/11 15:40:17Z TipRanks','同机构报告至少13:38Z已传播；ASP条款最早公开时刻未查清','决定性溯源缺口未处理；没有证明本次转述构成有效更新')]:out.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>')
out.append('</table>'+para('本次规则并不要求每条消息都做Web Search，也不要求找全球绝对首发。问题是出现决定性的时间冲突、原公告引用或更早研报线索时，没有沿线解决便交易。以上外部检索是本轮审计补查，绝不是原W3曾执行的工具。')+'</section>')
for idx,(title,ps) in enumerate(sections,1):out.append(f'<section id="case{idx}"><h2>{title}</h2>'+''.join(para(x) for x in ps)+'</section>')

out.append('<section><h2>共性机制与责任归属</h2>')
for x in [
 '<b>三层“新”被混用。</b>某条Fact未入本ticker事件库，是库内新；某机构模型第一次出现，是作者观点新；市场在当前时点得到重要信息，才是可交易的新。前三者之间需要论证，不能由W1的NEW、W2的HIT自动跨过。Nebius跨ticker的原始材料覆盖差异直接暴露这一点。',
 '<b>研究在找能支持方向的Policy，而非解决会推翻方向的缺口。</b>COHR反复恢复旧租约状态；BE主动找现场关键路径Policy；两者都没有优先查现成的原公司披露。RKLB正文已给出可量化模型变化，研究随即缩到行情。可读工具轨迹支持这个模式，但这不是对未公开内部心理过程的断言。',
 '<b>限制被写进reason，却没有改变选择。</b>COHR承认缺少同窗替代与直接供货；BE承认不能等同Bloom收入；RKLB承认非共识、非公司指引。规则要求这些缺口影响传导概率、分量和净方向，实际却被当成对原方向的措辞修饰。',
 '<b>时效被行情完成态代偿。</b>四条都把当前文章时间传给market_view。原规则将时效与行情完成态分开：没有足够原始时间或传播升级依据时，行情无法确认已完成不能为交易补证。尤其先于转载的市场反应，可能正是原催化已经传播的结果，不能仅因发生在文章之前而把它排除。',
 '<b>重复机会按作者与模型年份切得过细。</b>Berenberg的最终reason明确看到Morgan Stanley先前交易，却以不同机构/不同年份模型排除重复。真正要比的是本轮改变了哪项重要市场认识，不能只用券商不同来划新机会。',
 '<b>Policy有倾向性，W3未完成独立把关。</b>冻结Policy含“一次新独立观点即可跨界”“无需目标公司供货”等较宽边界；公共AGENTS/Skill则明确要求ticker级重要Surprise、净影响、相对市场认识和独立时效。仅补一条“查首发”文字不足，因为规则中早有该要求；还需要业务验证证明模型实际执行了这些判断。',
 '<b>本轮没有证明压缩导致原决策出错。</b>四个决策回合内部均未见compaction；两个RKLB还在同一thread中主动引用彼此。现在的记忆缺失影响事后解释，不能倒推出交易当时忘记了决定性信息。'
]:out.append(para(x))
out.append('</section>')

out.append('<section><h2>原thread压缩与审计续写</h2>')
out += [para('已先保全原始快照，再按用户授权对三个原thread各追加一次AUDIT_ONLY说明请求（RKLB一条请求覆盖两案）。这不是隐藏思维链提取：请求仅要求基于保留记录说明证据、假设、反证与时间处理，明确禁止工具、新研究、交易或运营提交。'),para('COHR快照共有7次compaction，BE为7次，RKLB为4次。Morgan Stanley之后有两次压缩，Berenberg之后原快照尚无新压缩。最新替换历史中COHR/BE/Morgan Stanley的标题多出现在后续PIN或72h意图嵌入，而不是原决策完整工具回合；历史文件仍有原记录，不等于模型当前有效上下文仍原样保留。加密reasoning或压缩项的存在，也不能证明模型可回忆或输出当时的隐性推理。')]
for tid,label in [('01a1c97b-c51d-7820-92f8-1617e60a3134','COHR'),('01a1ae33-8a7f-7333-b738-f9c97c819cf2','BE'),('01a1ba20-d1bb-7e30-a55a-7c287b0411bb','RKLB（两案）')]:
 path=p/'reviews'/f'retrospective_{tid}.txt';out.append('<details><summary>'+label+'：事后依据说明（点击展开）</summary><pre>'+esc(path.read_text(encoding='utf-8'))+'</pre></details>')
out += [para('<b>自述的证据等级低于原记录。</b>COHR/BE自述不知道具体接收、cutoff及工具过程；原始JSONL实际上保留了这些字段。RKLB也承认未找到原研报首发核查。它额外提出“行情采集晚于cutoff约19秒”的疑问，这不在本轮被确认为违规：工具是在决策阶段调用，cutoff字段的意义需要按宿主合同解释，不能把事后自述的新疑虑直接追加为根因。三份说明都不提供当时隐性推理的原样证明。'),para('续写后逐项核对：三个审计回合工具调用数为0，均有一个最终回复；四个Runtime case的updated_at保持不变，W3阶段仍各为2个。没有新建或重放业务W3任务，没有修改交易决策、Policy、应用代码或部署。仅按授权追加了原thread审计回合。'+link('after_audit_verification.json','续写后的验证收据')),'</section>']

out.append('<section><h2>建议的修复优先级（本轮未实施）</h2>')
out += [para('先用这四条建立业务验收：Fermi/Nebius必须识别更早披露；Morgan Stanley必须解决原研报与当前解释的时间关系；Berenberg必须评估普通远期模型上修是否达到ticker级Surprise。不要把“四条都查过Web”当通过标准，也不要把所有研报、项目延期或间接产业链消息永久屏蔽。'),para('具体应验证：当前文章时间是否被误作催化起点；缺失的是数值精度还是传导成立本身；正反证是否真的改变方向；同一业务变化是否仅因不同分析师而再次消费；行情未知是否被错误用于放行。针对这些能力做冻结输入回归，再决定是改Policy边界、W3提示还是宿主可观测性；本轮不修改规则或发布。'),para('完整证据：'+link('case_manifest.json','四案定位清单')+' · '+link('cross_ticker.json','同篇消息跨ticker原始记录')+' · '+link('earliest_messages.json','更早消息与来源时间记录')+' · '+link('thread_audit.json','压缩与原生行定位')+' · '+link('retrospectives.jsonl','审计请求及原thread回复')+'。完整快照含较大上下文，正文已给出逐案关键证据入口。'),'</section></html>']
(p/'full_report.html').write_text('\n'.join(out),encoding='utf-8')
print('report written',len('\n'.join(out)))
