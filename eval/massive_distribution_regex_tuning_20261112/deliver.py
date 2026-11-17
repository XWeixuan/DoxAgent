"""Package scored artifacts and operator findings; never edits candidates or GOLD."""
from ops import *

LESSONS = {
'INTC': '少量业务主题的首轮结果良好，但验证暴露了产品范围缺口和泛词的字段凑合。补齐稳定产品市场并收紧局部关系后两集过线；仍存在词根误命中、把普通动词和名词搭配误当项目建设，以及跨字段拼出业务关系的问题。样本过线不等于表达式已经没有这些缺陷。应在编写前逐分支检查词边界和语法角色；达到反馈轮数上限后保留问题清单，不偷偷修订已冻结版本。',
'MU': '只围绕开发集常见产品缩写容易遗漏价格、成本、需求替代、共享制造设备和客户部署等事实。增加有明确业务对象的稳定事实表达，比继续堆公司名称更有效。泛化补充同时引入了背景容量和设施用途误召，说明同篇共现不等于同一项目；这种代价必须与召回收益一起记录。不能把普通风险清单机械等同于已报告的供需变化。',
'BE': '移除泛行业词后，主要误召从纯关键词转向公司介绍、潜在终端应用和替代市场边界。不能因为一次验证偏严，就把所有早期计划和相邻供给方式删掉；先核对合同、已冻结标签是否同尺度。保留原标签评分，并把标注疑点与确实的规则缺陷分开。额外修正提升了表现但精度仍未达标，不能靠放宽验收口径交付为成功。',
'COHR': '简短规则在开发集完全命中也可能只是该集词面与题材分布较容易。动作词和业务对象放在不同位置，会把普通经营描述拼成具体项目；正文推荐区也会制造产品词误召。额外收紧没有提高两集平均表现，应保留旧版并按既定选择规则交付。均分选中的版本仍可能有很低的单项精度，平均值不能代替各项门槛或准确率优先的业务验收。',
'NBIS': '减少泛化的标题主题和组件广告，可以提高精度，但把可接受的信息都压成实体建设或容量数字，也会漏掉市场结构、试验、供电条件和项目统计等有效事实。应区分“关系有证据”与“必须有具体订单”这两种门槛。本次精度提高而召回不足，说明局部收紧不是无代价的通用改进；不能用合并指标掩盖单个目标的缺口。',
'CEG': '独立匹配行业背景词容易接受潜在应用和其他业务的介绍；改为产品市场主体及实际经营/工程事实后精度提高。要求过窄的项目措辞同时遗漏运行维护、配套设施和供需信息。准确率优先可以接受更保守的候选作为研究结果，但仍须明确召回未达标，不能把零误召的小样本外推成长期百分之百准确。',
'RKLB': '开发时应覆盖完整业务范围及同一业务的多种常见称谓；不能用最显眼的公司与产品替代整个市场。验证修正补齐范围后改善召回，但通用组件用途和免责声明仍可能触发误放。来源元数据与文章实际语言未必完全一致，回放应保存原入口语言并按实际语料补词，而不是固定要求若干语言或改写输入以抬分。',
'SNOW': '相邻产品、真实客户采用与跨系统架构事实能补召回，但一般数据治理词也广泛出现在垂直应用中。标题主体和正文独立事实需要结合，不能只依赖“存在数据”或者“出现平台名”。正式公司活动与背景介绍必须按同一业务尺度审查；对标签争议保留原判并单列，不用体裁黑名单追求指标。',
'ONDS': '先审查业务范围是否遗漏完整产品分支，再设计词表。扩大稳定产品与关键组件表达可以补漏，撤掉宽泛动作词又会丢失采购、供应和技术事实；两者须同时评估。公司介绍中的真实产品词仍可能误触发，普通测量活动也可能被动作词拼成产品事件。一次验证修正用尽后停下并保留未解决机制，比继续按验证坏例追加特判更可靠。',
}

FINDINGS = {
'INTC': '补充硅自旋计算、处理器接口、制造设备及实际计算部署信息。剩余 FP 包括把“Open Line Systems”“building entrances”解释为设施动作。`intel_manufacturing_fact` 的一个分支在 Intel 后缺少独立词边界，实际命中了 Artificial Intelligence；同一文章另有封装组命中。该词面缺陷是本轮明确遗留，不能因总分过线忽略。FN 仍有正文中的具体项目、EDA 及互连事实。',
'MU': '补充存储成本/价格、外部存储替代、制造检测和大规模客户设施事实，验证召回明显改善。仍有同篇电池储能容量与数据中心背景凑合、器件说明中的普通建设动词误召；不能将这一轮的高召回解释为全市场高召回。',
'BE': '删去移动燃料电池、便携电源及过远核燃料题材的泛匹配，保留固定供电替代、储能、负荷和电网信息。验证余下5个 FP：公司介绍、连接器终端用途、热储能背景，以及2个业务边界争议；漏掉实际楼宇储能项目。原 GOLD 下精度不足。',
'COHR': '第二轮修复光纤连接、专用激光、单位和连字符，开发集100%/100%。验证的背景施工、相关报告推荐区及泛数据中心主题仍导致5个 FP；具体算力项目/融资的2个 FN未覆盖。额外版精度提高至70%，但召回降至70%，四项均分低于第二轮，按 SOP 保留第二轮为数值最佳；不建议作为生产候选。',
'NBIS': '额外版移除泛 AI 基础设施标题和组件广告，验证20%的原负例误召降至0/32。对应的5个 FN主要是供电试验/行业论坛、同业市场整合、项目统计和电源协议；把容量工程条件收得过严。开发仍有背景容量和连接产品误放。',
'CEG': '补充发电市场、核能项目及具体负荷事实，去除泛电网背景。验证无 FP，但4个 FN包括超导输电、储能设施投运、独立太阳能储能项目和电厂维护产品；行业范围/表达仍不完整。',
'RKLB': '补齐月球经济、空间供电、卫星制造与连接生态，验证27个正例全部保留。余下误召来自导航用途、卫星业务模板及一个卫星连接业务边界争议，不能单凭 satellite/constellation 共现当成独立经营事件。',
'SNOW': '补齐实时数据库、工业数据平台、跨系统数据治理及平台实际采用。验证13/14个正例保留，剩余垂直医疗数据词误召、公司正式财报通知的 GOLD 争议和产品信息管理漏判；通用数据层与垂直应用的边界仍较难用关键词表达。',
'ONDS': '补齐反无人机、无人地面系统、专网、无人机组件与自主飞行器。验证由21/32提升至30/32，仍误收公司简介/历史用途事实，漏掉威胁检测合作及无人系统经营更新；开发召回由87.5%降至81.25%，属修正代价。',
}

def fmt(value):
    return '—' if value is None else f'{value*100:.2f}%'

def table_row(t, a):
    d,v=a['development'],a['validation']
    return f"|{t}|{a['attempt']}|{fmt(d['precision'])} / {fmt(d['recall'])}|{fmt(v['precision'])} / {fmt(v['recall'])}|{fmt(a['mean'])}|{'达标' if d['passed'] and v['passed'] else '**未达标**'}|"

selection = []
aggregate = {s:{'baseline':[],'selected':[]} for s in ['development','validation']}
all_attempts=[]
for t in TICKERS:
    report=read(HERE/t/'comparison.json')
    best=next(a for a in report['attempts'] if a['attempt']==report['final_best'])
    record(t,LESSONS[t],True)
    selection.append({'ticker':t,**best})
    for a in report['attempts']: all_attempts.append((t,a))
    for s in aggregate:
        aggregate[s]['baseline']+=rows(HERE/t/'regex/baseline'/s/'predictions.jsonl')
        aggregate[s]['selected']+=rows(HERE/t/'regex'/best['attempt']/s/'predictions.jsonl')
    candidate=read(HERE/'selected'/f'{t}.json')
    write(HERE/t/'selection.json',{'selected':best['attempt'],'sha256':sha(HERE/'selected'/f'{t}.json'),'development':best['development'],'validation':best['validation'],'mean':best['mean'],'sample_target_met':best['development']['passed'] and best['validation']['passed'],'not_production_accepted':True,'findings':FINDINGS[t]})

totals={s:{k:metrics(r) for k,r in parts.items()} for s,parts in aggregate.items()}
write(HERE/'summary.json',{'selected':selection,'aggregate':totals,'attempts':len(all_attempts),'target_met':[r['ticker'] for r in selection if r['development']['passed'] and r['validation']['passed']],'limitations':['prior-regex-positive enriched sample; conditional recall only','validation used for final selection','900 pairs; 1 INSUFFICIENT excluded from binary scoring','no production acceptance or deployment']})
write(HERE/'selected-massive-regex-bundle.json',{'scope':'massive_distribution','usage':'OFFLINE_ONLY_DO_NOT_APPLY_TO_COMMON_L2','selection_rule':'SOP four-metric mean','tickers':{r['ticker']:{'l2':read(HERE/'selected'/f"{r['ticker']}.json")['l2'],'attempt':r['attempt'],'sample_target_met':r['development']['passed'] and r['validation']['passed'],'production_accepted':False} for r in selection}})

review=['# 最终入选候选的逐条偏差索引','', '保留原 GOLD；以下不是重新标注。每条链接指向完整冻结证据、GOLD 理由和正式匹配 trace。未为追分修改已冻结候选。','']
for r in selection:
    t=r['ticker']; a=r['attempt']
    review += [f'## {t} · {a}','',FINDINGS[t],'']
    for s in ['development','validation']:
        bad=rows(HERE/t/'regex'/a/s/'badcases.jsonl')
        review += [f'### {s} · {len(bad)} 个偏差','']
        for b in bad:
            hits='；'.join(f"{h['group']}: "+' / '.join(x['matched'] for x in h['terms']) for h in b['hits']) or '没有放行组命中'
            review += [f"- **{b['pair_id']} · {'FP' if b['prediction'] else 'FN'}**：{b['evidence']['title']}",f"  GOLD：{b['gold']['reason']}",f"  匹配：{hits}"]
        review += ['',f'完整证据：[{s}/badcases.jsonl]({t}/regex/{a}/{s}/badcases.jsonl)。','']
(HERE/'case-review.md').write_text('\n'.join(review)+'\n',encoding='utf-8')

headers=['|Ticker|最终版本|调试 P / R|验证 P / R|四项均分|四项均 >80%|','|---|---|---:|---:|---:|---|']
lines=['# Massive Distribution 九 ticker 正则调试报告 · 2026-11-12','',
'本轮完成 INTC、MU、BE、COHR、NBIS、CEG、RKLB、SNOW、ONDS 的一次 ticker 级调试，共 **21 个候选、42 次两集评分（21组成对结果）、2,100 条候选预测**。最终入选版本中 **5/9 达到两集 P、R 均严格大于80%**；BE、COHR、NBIS、CEG 为“最佳但未达标”。不能把总体过线解释成九个 ticker 全部完成指标验收。','',
'## 最终结果','']+headers+[table_row(r['ticker'],r) for r in selection]+['',
'P=TP/(TP+FP)，R=TP/(TP+FN)。一条消息是文章×ticker。ONDS 调试集的1条 INSUFFICIENT 仍执行正则，但不计入二分类 P/R。COHR 验证召回恰为80%，按 SOP 也不算过线。','',
'合并900对样本的微平均：','',
'|集合|旧正则 P / R|入选正则 P / R|入选 TP / FP / FN / TN|','|---|---:|---:|---:|']
for s,label in [('development','调试450条'),('validation','验证450条')]:
    b,n=totals[s]['baseline'],totals[s]['selected']
    lines += [f"|{label}|{fmt(b['precision'])} / {fmt(b['recall'])}|{fmt(n['precision'])} / {fmt(n['recall'])}|{n['TP']} / {n['FP']} / {n['FN']} / {n['TN']}|"]
lines += ['',
'验证合计 FP 从282降至20，保留149/168条相关消息；这说明冻结样本内明显提准，同时漏掉19条原池中的相关消息。样本经过标题分层富集，不能将其精度或误召减少比例直接外推为生产量、自然分布表现。','',
'## 范围、数据与执行纪律','',
'- 使用上一轮冻结的 Massive GOLD，每 ticker 调试50、验证50，共900；未重新抽样、未改标签。只包含五家 Newswire，排除 Bloomberg、IONQ。两集原文 SHA-256 与交付时相同，文章 family 全局跨集重叠为0。','- 样本来自旧正则已召回的完整正文池；旧正则900/900均放行。这里的召回率是**旧召回池内的相关消息保留率**，不含旧正则从未召回的新闻，因此不是全市场召回率。Business Wire正文不足、仅有极少样本的原始覆盖限制继续存在。','- 正则使用原 `regex_relevant`、NFC/空白归一化、原始入口 language 与 en fallback；正文/摘要未改写。因实际稿件包含法语等内容，候选按观察到的内容补充必要表达，没有固定三语要求。','- 基线为上一轮生产快照的统一 L2，在相同冻结输入上重新回放；不是本轮实时生产实绩，也不是仅统计历史旧日志。','- 常规候选：INTC、BE、CEG、RKLB、SNOW、ONDS各1轮；MU、COHR、NBIS各2轮。达到开发 P/R 门槛后即停止常规轮次，未机械用满5轮。','- 九个 ticker 的全部常规候选一起冻结后，才逐个开放开发最佳版的验证；冻结时间及候选哈希见 `regular-freeze-all.json` 和各 ticker 的 `regular-freeze.json`。','- 首轮验证九个均未双过线。各使用1次额外修正，九个修正版一起冻结后再评分；没有根据修正版验证继续调参。之后补齐先前候选的验证。','- 全部21候选都有相同配置的两集完整结果；按 SOP `(调试P+调试R+验证P+验证R)/4` 选优，平手取较高四项最小值、再取更早版本。验证已用于选择，不能继续称为未见的独立验收集。','- 准确率优先体现在去除泛词、组件用途和背景凑词，以及对低精度版本不做生产验收；没有用平均分替代每项80%的门槛。','',
'## 逐 ticker 结论','']
for r in selection:
    t=r['ticker'];lines += [f'### {t}','',FINDINGS[t],'']
lines += ['## 仍需单列的 GOLD 尺度疑点','',
'这些疑点来自真实冻结输入，不代表下表已经获得人工改判。全部指标仍使用原 GOLD，未按新判断重算。明确的词面/字段缺陷同样保留，不能把低分都归咎于标注。','',
'|Ticker|冻结 case / 对照|疑点与影响|','|---|---|---|',
'|BE|开发 F1a524de34fd434、F5cf6f945740b09；验证 Fd97f09b71d1a6b、F8bd5ea0a5e2484|开发接受核电设备/退役作为替代供电市场，验证将核电市场/退役按“不涉及燃料电池”排除。不能靠核电标题细微措辞拟合不同答案。|',
'|BE|开发 Ff9b7e66739cd35；开发其他电网投资正例|ONCOR已报告电网投资、接电负荷，被判无关；同集另有电网投资报告判相关，边界不一致。|',
'|INTC|开发 Ff891e2a6a6aef2、验证 F2c575d6e64a1b8；开发 Fcdc6ec38025d1e|具体数据中心园区/设计被以未指定Intel芯片排除，而其他早期数据中心计划为相关；与无需点名公司、可收早期计划的准则有冲突。|',
'|COHR|验证 Fef61acfdc72963；开发 F044387a7199c03、F29fee94e01c102|Edgevana明确NVIDIA部署及多年算力合同被判无关，而其他计算部署按光互连需求近邻判相关。|',
'|NBIS|验证 Fafae42680035a5 与 Fd73d9d3deb215c|两条FuelCell诉讼均涉及同一最高380MW数据中心供电合同；一条理由要求具名云设施，另一条按相同合同判相关，需对全文边界复核。|',
'|SNOW|验证 F7be20ec30037f3；NBIS开发 Fb425ed6bac01d4 / 既定规则6|MongoDB自身财报会议通知被排除；同类核心竞争者正式通知可相关。无需因通知没有新增产品参数而一律排除。|',
'|RKLB|验证 Fb0f3ecac5ceb35|Gogo正文有卫星连接政府合同续约和实际经营变化，仅以没有新卫星制造/发射计划排除，疑似窄于“卫星运营服务”范围。|','',
'完整偏差见 [逐条偏差索引](../eval/massive_distribution_regex_tuning_20261112/case-review.md)。其中包含最终版本两集73条 FP/FN、原 GOLD 理由、实际命中子串及完整输入文件链接。','',
'## 所有候选成绩','']+headers+[table_row(t,a) for t,a in all_attempts]+['',
'COHR特别说明：额外修正版验证为70%/70%，首版为75%/60%，第二版为61.54%/80%。按既定四项均分第二版最高，但按精度优先的业务目标，三个版本都不能视为合格上线方案；没有因为新版编号更大而强行选择它。','',
'## 交付与复现','',
'- [九套入选配置](../eval/massive_distribution_regex_tuning_20261112/selected/README.md)；[L2汇总包](../eval/massive_distribution_regex_tuning_20261112/selected-massive-regex-bundle.json)。保留全部原候选与设计理由，入选文件与被评分候选字节哈希一致。','- [完整机器汇总](../eval/massive_distribution_regex_tuning_20261112/summary.json)；[冻结与回放核验](../eval/massive_distribution_regex_tuning_20261112/verification.json)。各 `TICKER/regex/attempt-*` 下有两集 `metadata.json`、`predictions.jsonl`、`metrics.json`、`details.jsonl`、`badcases.jsonl`。','- [唯一共享经验账本](by-distribution-relevance-debug-experience.md)新增九条合并复盘；每次开发评分后均更新对应ticker的同一session。','- 本轮新增的只是任务目录内的人工候选配方、正式函数回放及留痕脚本，没有新增生产正则引擎、自动调参器或验证权限系统。','',
'在项目根目录 PowerShell：','',
'```powershell',
"$env:PYTHONIOENCODING='utf-8'",
'.venv/Scripts/python.exe eval/massive_distribution_regex_tuning_20261112/verify.py',
'.venv/Scripts/python.exe eval/massive_distribution_regex_tuning_20261112/ops.py evaluate INTC attempt-02-overfit validation',
'```','',
'评分命令对已存在同配置的成功结果复用；独立 `verify.py` 实际重放全部2,100条候选预测并逐条比对，另核对原数据、分组隔离、冻结时序、候选哈希、成对择优。原生regex错误/超时告警为0。','',
'**本轮交付是离线 Massive 专用候选，不是生产发布。** 当前生产 Massive 与普通 By Distribution 共用统一L2，直接应用这些完整配置会同时影响普通来源。未修改生产、未调用Decision Model、未对Bloomberg扩展。本轮五项样本过线也不能替代生产验收；四项未过线及上文已知词面缺陷均如实保留。后续若继续调试，应先复核标签并使用新隔离验证数据，不能反复使用本轮已开放验证集宣称泛化达标。','']
(ROOT/'docs/massive-distribution-regex-tuning-report-20261112.md').write_text('\n'.join(lines),encoding='utf-8')

readme=['# Massive 专用离线入选正则','', '**不要将这些完整JSON直接应用到当前生产统一L2。** Massive与普通By Distribution目前共用配置；这里仅用于独立回放和后续接入评审。','', '每个文件是所评分候选的原样副本，只改变原词表的l2；expected_revision来自旧冻结快照，不能视为实时生产revision。5个样本达标、4个未达标，全部未做生产验收。','']+headers+[table_row(r['ticker'],r) for r in selection]+['','完整报告：../../../docs/massive-distribution-regex-tuning-report-20261112.md。']
(HERE/'selected/README.md').write_text('\n'.join(readme)+'\n',encoding='utf-8')
(HERE/'README.md').write_text('# Massive Distribution regex tuning · 2026-11-12\n\nSee [report](../../docs/massive-distribution-regex-tuning-report-20261112.md), [results](summary.json), [case index](case-review.md), [verification](verification.json), and [selected configs](selected/README.md).\n\nAll candidates and GOLD are immutable. `design.py`, `second.py`, `correction.py` are preserved manual authoring recipes and refuse overwrites. `ops.py` replays the production classifier; validation timing is governed by the SOP. `verify.py` independently replays saved predictions. No production writes.\n',encoding='utf-8')
print(json.dumps({'report':'docs/massive-distribution-regex-tuning-report-20261112.md','attempts':len(all_attempts),'aggregate':totals},ensure_ascii=False))
