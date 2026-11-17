"""Check and document the frozen simple experiment; no candidate revisions."""
import logging
import shutil

import run

ops, HERE, ROOT = run.ops, run.HERE, run.ROOT
frozen = ops.read(HERE / 'final-freeze.json')
warnings = []


class Capture(logging.Handler):
    def emit(self, record):
        warnings.append(record.getMessage())


logging.getLogger('doxagent.message_bus_v2.relevance').addHandler(Capture())
summary = []
native_replay_count = 0
for ticker, attempts in frozen['attempts'].items():
    for attempt, fingerprint in attempts.items():
        directory = HERE / ticker / 'regex' / attempt
        assert ops.sha(directory/'candidate.json') == fingerprint
        config = ops.read(directory/'candidate.json')
        original = ops.read(ops.DATA/'baseline-terms'/f'{ticker}.json')
        assert all(config[k] == v for k,v in original.items() if k != 'l2')
        terms = ops.TickerMonitoringTerms.model_validate(config)
        for split in ['development', 'validation']:
            saved = ops.rows(directory/split/'predictions.jsonl')
            assert len(saved) == 50
            dataset = ops.DATA/'final'/ticker/f'{split}-gold.jsonl'
            by_id = {r['pair_id']:r for r in ops.rows(dataset)}
            assert {r['pair_id'] for r in saved} == set(by_id)
            assert ops.metrics(saved) == ops.read(directory/split/'metrics.json')
            meta = ops.read(directory/split/'metadata.json')
            assert meta['candidate_sha256'] == fingerprint
            assert meta['dataset_sha256'] == ops.sha(dataset)
            if split == 'validation':
                assert ops.read(directory/split/'receipt.json')['completed_at'] > frozen['frozen_at']
            for prediction in saved:
                row = by_id[prediction['pair_id']]
                message = ops.RawMessageInput(**row['evidence'], url=row['canonical_url'], raw_payload={}, metadata={'language':row['language']})
                assert ops.regex_relevant(terms,row['language'],message) == prediction['prediction']
                native_replay_count += 1
    attempt = frozen['selected_by_reasonableness'][ticker]
    candidate = ops.read(HERE/ticker/'regex'/attempt/'candidate.json')
    prior_attempt = ops.read(run.PRIOR/ticker/'comparison.json')['final_best']
    assert ops.sha(run.PRIOR/'selected'/f'{ticker}.json') == ops.sha(run.PRIOR/ticker/'regex'/prior_attempt/'candidate.json')
    old = {s:ops.read(run.PRIOR/ticker/'regex'/prior_attempt/s/'metrics.json') for s in ['development','validation']}
    metrics = {s:ops.read(HERE/ticker/'regex'/attempt/s/'metrics.json') for s in ['development','validation']}
    patterns = [v['regex'] for g in candidate['l2']['en']['groups'] for v in g.get('any',[])]
    old_config = ops.read(run.PRIOR/'selected'/f'{ticker}.json')
    old_patterns = [v['regex'] for l in old_config['l2'].values() for g in l['groups'] for k in ['any','all','none'] for v in g.get(k,[]) if v.get('regex')]
    summary.append({'ticker':ticker,'attempt':attempt,'metrics':metrics,'previous':old,'previous_attempt':prior_attempt,
                    'groups':len(candidate['l2']['en']['groups']),'unique_pattern_characters':sum(map(len,set(patterns))),
                    'previous_groups':sum(len(l['groups']) for l in old_config['l2'].values()),
                    'previous_unique_pattern_characters':sum(map(len,set(old_patterns))),
                    'development_passed':metrics['development']['passed'],
                    'both_splits_passed':all(m['passed'] for m in metrics.values())})
    (HERE/'proposed').mkdir(exist_ok=True)
    shutil.copyfile(HERE/ticker/'regex'/attempt/'candidate.json',HERE/'proposed'/f'{ticker}.json')

integrity = ops.read(ops.DATA/'verification.json')
for split in ['development','validation']:
    assert ops.sha(ops.DATA/'final'/f'{split}-gold.jsonl') == integrity['sha256'][split]
assert not warnings
ops.write(HERE/'verification.json',{'verified_at':ops.now(),'native_replayed_predictions':native_replay_count,
          'candidate_hashes_match':True,'GOLD_and_inputs_unchanged':True,'only_l2_changed':True,
          'prior_selected_configs_unchanged':True,'validation_after_freeze':True,
          'native_regex_errors_or_timeouts':warnings,'production_writes':0,'decision_calls':0})
ops.write(HERE/'summary.json',summary)


def pct(value):
    return '—' if value is None else f'{value:.2%}'


def pr(metrics):
    return f"{pct(metrics['precision'])} / {pct(metrics['recall'])}"


findings = {
'BE':'首版6组，没有样本ID、数值门槛、词距或排除清单。调试漏掉3条：数据中心供电储能综合报告、澳洲输电建设的物流报告、学校捐赠稿中的独立电网建设事实。未为这3篇补泛电网/建设词。旧验证诊断明显较差；业务范围和正文表达覆盖仍不足，保留简化方案但不建议替换旧版。',
'COHR':'5组，围绕光通信/光子产品与少量明确的算力供给主题。调试漏掉5条：综合数据中心市场、Bitzero设备到货、设施运维、配电设备和印尼建筑报告中的独立算力设施事实。它们按原GOLD仍相关；本轮未把其遗漏解释为业务上应当无关。撤掉数字与宽动作推断后，这些间接消息覆盖变弱；不为凑召回扩充项目谓词。',
'NBIS':'首版的AI数据中心标题词误收固态变压器融资稿，不能区分算力设施主体和器件终端用途。第二版只删此分支，其他表达式保持原样，未加负例黑名单。调试由18TP/1FP变为16TP/0FP；同时失去散热与IT资产退役报告，累计7个FN，还包括实际计算部署、云市场和供给合同。旧验证3TP/0FP/15FN，说明保守简化代价很大，不能称为效果优于旧版，也不建议直接用于生产替换。',
'CEG':'4组，保留核能/燃料/废物、发电与电网储能、少量正文经营事实，不给每个词绑定订单或建设动作。调试仅漏Chesapeake公用事业财报中的配电项目；没有单独加公司名救这一条。两集均过线，但验证是已曝光旧样本，不能把结果当作新的独立验收。',
}
lines=['# Massive 四 ticker 简化重调 · 2026-11-12','',
'本轮按最新要求，以**表达搭配直观合理、准确率优先、不过度设计**为优先级，基于原调试集重写 BE、COHR、NBIS、CEG。共5个候选：四个首版，NBIS一次仅删除分支的修正。没有容量数字、单位推断、任意词距、前后向断言、文章特判或负例黑名单。未追求把每个漏判“补到过线”。','',
'## 本轮结果','',
'每ticker仍为50条调试＋50条旧验证。P为准确率、R为召回率，过线仍是两项严格>80%。**调试BE、CEG过线；若同时看旧验证，仅CEG两集过线。**','',
'|Ticker|本轮版本|调试 P / R|旧验证诊断 P / R|两集四项均>80%|','|---|---|---:|---:|---|']
for r in summary:
    lines += [f"|{r['ticker']}|{r['attempt']}|{pr(r['metrics']['development'])}|{pr(r['metrics']['validation'])}|{'是' if r['both_splits_passed'] else '否'}|"]
lines += ['',
'**旧验证已经在上一轮曝光，本轮不可能恢复成未见数据。** 候选依据调试集及业务范围设计，最终版本在此次复测前冻结；旧验证只作诊断，不据其坏例追加修改，不把本轮分数称为新的独立泛化验收。NBIS第二版在复测前按业务合理性确定，未按验证均分改选首版。','',
'## 简化方式','',
'- 标题/摘要中的公司主体、具体产品市场优先；正文仅保留少量有明确业务对象的词组。多组OR，主语可以出现在标题或摘要，正文规则独立补充。','- 不把普通AI、云、能源、data center或“建设/扩张”动词全篇共现当成业务联系，不要求容量达到特定数字。','- 不为每个行业对象穷举订单、收入、发布、采购等事件谓词；不按体裁排除通知、诉讼或市场报告。','- 保留正文独立事实的业务相关GOLD；简单规则匹配不到就记FN，不改变业务定义或标签替表达式辩护。','- 英文fallback包含实际语料词组；没有强制补齐三语。L1、definition等原字段不变。','',
'|Ticker|上一轮入选组数 → 本轮组数|去重后表达式字符数：旧 → 新|','|---|---:|---:|']
for r in summary:
    lines += [f"|{r['ticker']}|{r['previous_groups']} → {r['groups']}|{r['previous_unique_pattern_characters']} → {r['unique_pattern_characters']}|"]
lines += ['','字符数只描述可维护性，不是质量分。尤其NBIS的结果表明，表达短并不自动代表召回合理。','',
'## 与上一轮入选版本对照','',
'|Ticker|上一轮调试 P / R|本轮调试 P / R|上一轮旧验证 P / R|本轮旧验证 P / R|','|---|---:|---:|---:|---:|']
for r in summary:
    lines += [f"|{r['ticker']}|{pr(r['previous']['development'])}|{pr(r['metrics']['development'])}|{pr(r['previous']['validation'])}|{pr(r['metrics']['validation'])}|"]
lines += ['','这是冻结输入相同的版本对照，不是线上实绩。没有因为旧版均分更高，就在本轮按验证成绩反向拼装表达式；上一轮的九套入选文件保持原样。','',
'## 逐ticker取舍与调试偏差','']
for r in summary:
    t=r['ticker'];a=r['attempt'];lines += [f'### {t}','',findings[t],'']
    for b in ops.rows(HERE/t/'regex'/a/'development/badcases.jsonl'):
        lines += [f"- `{b['pair_id']}`：{b['evidence']['title']}。GOLD：{b['gold']['reason']}"]
    lines += ['']
lines += ['## 全部候选','',
'|Ticker / Attempt|调试 TP/FP/FN/TN|调试 P / R|旧验证 TP/FP/FN/TN|旧验证 P / R|','|---|---:|---:|---:|---:|']
for t,attempts in frozen['attempts'].items():
    for a in attempts:
        d,v=[ops.read(HERE/t/'regex'/a/s/'metrics.json') for s in ['development','validation']]
        counts=lambda m:'/'.join(str(m[k]) for k in ['TP','FP','FN','TN'])
        lines += [f'|{t} / {a}|{counts(d)}|{pr(d)}|{counts(v)}|{pr(v)}|']
lines += ['',
'## 文件与复现','',
'- [四套本轮候选](../eval/massive_distribution_regex_round2_20261112/proposed/README.md)：BE/COHR/CEG首版、NBIS删减版。','- [完整表达式及字段](../eval/massive_distribution_regex_round2_20261112/rules.md)，方便直接检查搭配是否合理。','- [冻结回执](../eval/massive_distribution_regex_round2_20261112/final-freeze.json)；[全部指标](../eval/massive_distribution_regex_round2_20261112/summary.json)；[核验](../eval/massive_distribution_regex_round2_20261112/verification.json)。','- 每个候选目录保留完整输入、GOLD理由、逐条预测、命中片段和FP/FN；全部5候选均有两集结果。','- [唯一共享经验](by-distribution-relevance-debug-experience.md)新增4个round2 session，NBIS两版合并为同一条；上一轮记录保留。','',
'```powershell',"$env:PYTHONIOENCODING='utf-8'",'.venv/Scripts/python.exe eval/massive_distribution_regex_round2_20261112/finish.py','```','',
'核验实际重放500条候选预测，结果与记录一致，正则错误/超时0；输入与GOLD哈希、候选哈希、两集配置及冻结时序一致。配置只改L2，未调用Decision Model、未改远端生产。','',
'本轮仅CEG具备两集样本门槛结果；其他三个保留为未通过的简化实验。特别是BE和NBIS，不能把更好读的规则当成更好的筛选结果。原池是旧正则阳性且标题富集的样本，召回仍只是原召回池内保留率，精度不是自然生产精度。','']
report = ROOT/'docs/massive-distribution-regex-simple-round2-20261112.md'
report.write_text('\n'.join(lines),encoding='utf-8')
rules=['# 本轮实际表达式','', '这里只展开最终本轮候选；各组OR，标题/摘要任一命中即可。正文组仅匹配body。没有全局排除词或隐藏预处理。','']
for r in summary:
    t=r['ticker'];rules += [f'## {t}','']
    config=ops.read(HERE/'proposed'/f'{t}.json')
    for g in config['l2']['en']['groups']:
        rules += [f"### {g['id']} · {' / '.join(v['field'] for v in g['any'])}",'','```regex',g['any'][0]['regex'],'```','']
(HERE/'rules.md').write_text('\n'.join(rules),encoding='utf-8')
(HERE/'proposed/README.md').write_text('# 本轮四套离线简化候选\n\nBE.json、COHR.json、NBIS.json、CEG.json 为本轮按表达合理性确定的版本，不按旧验证分数重新拼装。仅CEG两集过线；其余保留未通过状态。\n\n这些完整配置来自冻结旧快照，不要直接应用到生产统一L2。上一轮九套入选配置未覆盖。本轮无生产变更。\n\n见[报告](../../../docs/massive-distribution-regex-simple-round2-20261112.md)及[表达式](../rules.md)。\n',encoding='utf-8')
print(ops.json.dumps({'report':str(report),'native_replayed':native_replay_count,'results':summary},ensure_ascii=False))
