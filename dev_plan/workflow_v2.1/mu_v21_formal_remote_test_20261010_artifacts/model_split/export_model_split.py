"""Export final MU Policies by accepted research author, preserving release 22."""
import copy
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'artifacts/document3/releases/22/document3.json'
ORIGINAL = 'mu-v21-20261010-02-d3'
SUCCESSOR = ORIGINAL + '-resume1'


def save(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def main():
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    document = json.loads(SOURCE.read_text(encoding='utf8'))
    evidence = json.loads((HERE / 'origin_evidence.json').read_text(encoding='utf8'))
    path_tasks = {rid: {path: task for task in data['tasks'] for path in task['files']} for rid, data in evidence.items()}
    old = {d['policy']['policy_id']: d for d in evidence[ORIGINAL]['drafts'] if d['policy']}
    current = defaultdict(list)
    for draft in evidence[SUCCESSOR]['drafts']:
        if draft['policy']:
            current[draft['policy']['policy_id']].append(draft)
    groups = {('gpt-6-sol', 'high'): [], ('gpt-6-luna', 'max'): []}
    provenance = []

    def receipt(rid, draft):
        task = path_tasks[rid][draft['path']]
        assert task['model'] == task['request_model'] and task['effort'] == task['request_effort']
        return dict(run_id=rid, task_key=task['key'], path=draft['path'], job_id=task['job_id'], model=task['model'], effort=task['effort'], task_status=task['status'], worker_status=task['worker_status'])

    for policy in document['policies']:
        pid = policy['policy_id']
        candidates = [d for d in current[pid] if d['path'].startswith(('output/work/v21/research/', 'output/work/v21/supplement/'))]
        # A supplement can replace a Policy while deliberately retaining its ID.
        supplements = [d for d in candidates if d['path'].startswith('output/work/v21/supplement/')]
        candidates = supplements or candidates
        assert len(candidates) == 1, (pid, [d['path'] for d in candidates])
        accepted = candidates[0]
        author = receipt(SUCCESSOR, accepted)
        assert author['task_status'] == 'COMPLETED' and author['worker_status'] == 'succeeded'
        groups[(author['model'], author['effort'])].append(policy)
        born = receipt(ORIGINAL, old[pid]) if pid in old else author
        changed = [key for key in policy if policy[key] != accepted['policy'].get(key)]
        final_writes = [d for d in current[pid] if d['path'].startswith('output/work/v21/final/') and d['policy'] == policy]
        assert len(final_writes) <= 1
        assert not changed or final_writes, (pid, changed)
        editor = receipt(SUCCESSOR, final_writes[0]) if final_writes else author
        provenance.append(dict(policy_id=pid, title=policy['title'], initial_origin=born, accepted_research_author=author, final_content_writer=editor, changed_after_accepted_research=changed, same_id_reworked_by_other_model=(born['model'], born['effort']) != (author['model'], author['effort'])))

    all_ids = {p['policy_id'] for p in document['policies']}
    split_ids = [{p['policy_id'] for p in values} for values in groups.values()]
    assert not split_ids[0] & split_ids[1] and split_ids[0] | split_ids[1] == all_ids
    for (model, effort), policies in groups.items():
        filename = f'document3_{model}_{effort}'
        subset = copy.deepcopy(document)
        subset['policies'] = policies
        save(filename + '.json', subset)
        text = f'# MU D3 · {model} / {effort} 研究稿来源\n\n'
        text += f'从版本 22 抽取 {len(policies)} 条最终 Policy。按最终接纳的 Build/补研稿模型分组；正文保留最终版本，可能经 Luna Integration 编辑。此文件是离线摘录，不是独立发布版本。详情见 README.md 和 policy_model_provenance.json。\n\n'
        for p in policies:
            meta = next(item for item in provenance if item['policy_id'] == p['policy_id'])
            text += f"## {p['title']}\n\nPolicy ID：`{p['policy_id']}`\n\n"
            if meta['same_id_reworked_by_other_model']:
                text += '来源说明：最初 Sol 稿保留同一 ID，后由 Luna 续跑/补研重新交付；按最终研究稿归入 Luna。\n\n'
            if meta['changed_after_accepted_research']:
                text += '后续编辑：Luna 最终写入调整了 ' + '、'.join(meta['changed_after_accepted_research']) + '。\n\n'
            text += p['transmission'] + '\n\n' + '匹配范围：' + p['match_scope'] + '\n\n'
            for c in p['activation_conditions']:
                text += f"### {c['condition_id']} · {c['decision']} · {c['trigger_layer']}\n\n{c['criterion']}\n\n"
                text += f"参考基线：{c['calibration']['reference_state']}\n\n触发边界：{c['calibration']['trigger_boundary']}\n\n"
        (HERE / (filename + '.md')).write_text(text, encoding='utf8')

    index = dict(ticker=document['ticker'], source_policy_set_version=document['policy_set_version'], source_sha256=source_hash, policy_count=len(document['policies']), condition_count=sum(len(p['activation_conditions']) for p in document['policies']), policies=[dict(policy_id=p['policy_id'], title=p['title'], conditions=[dict(condition_id=c['condition_id'], criterion=c['criterion']) for c in p['activation_conditions']]) for p in document['policies']])
    save('policy_title_criterion_index.json', index)
    summary = dict(classification='model of final accepted research/supplement draft; final Integration edits tracked separately', source_sha256=source_hash, counts={model + '/' + effort: len(values) for (model, effort), values in groups.items()}, initial_origin_counts=dict(Counter(p['initial_origin']['model'] for p in provenance)), same_id_reworked=sum(p['same_id_reworked_by_other_model'] for p in provenance), sol_research_luna_final_edits=sum(p['accepted_research_author']['model']=='gpt-6-sol' and bool(p['changed_after_accepted_research']) for p in provenance), final_writer_counts=dict(Counter(p['final_content_writer']['model'] for p in provenance)), condition_count=index['condition_count'])
    save('policy_model_provenance.json', dict(summary=summary, policies=provenance))
    readme = '''# MU D3 版本 22：模型拆分与条件索引

完整版本 22 保持原样。本目录是离线摘录，不是新的发布版本；不应把单个子集导入正式工作流或当作完整 coverage。

## 拆分口径

按最终接纳的 Build/补研稿的实际 Worker request.model/effort 分组，保留最终版本 22 的 Policy 正文及 ID：

| 分组 | Policy 数 | 文件 |
| --- | ---: | --- |
| gpt-6-sol / high | 140 | [可读版](document3_gpt-6-sol_high.md) · [JSON](document3_gpt-6-sol_high.json) |
| gpt-6-luna / max | 122 | [可读版](document3_gpt-6-luna_max.md) · [JSON](document3_gpt-6-luna_max.json) |

两组无重叠，合计原版全部 262 条 Policy，内容与原版逐条相同。

单纯按 Policy ID 首次出现划分会得到 Sol 148 / Luna 114，但其中 8 条原 Sol 稿后来由 Luna 续跑或补研保留同一 ID 重新交付，因此本次归入 Luna 组。Sol 组剩余 140 条中，还有 8 条的条件经 Luna 最终写入修改，已在可读版逐条标注；132 条保持接纳的 Sol 研究稿内容。所有 Policy 共同经过 Luna Integration 审查，这两份不能解释成完全独立的模型对照实验。

## 全量索引

[policy_title_criterion_index.json](policy_title_criterion_index.json) 包含 262 条 Policy 的 title 与全部 576 条 condition 的 criterion，保持原顺序与完整文本；保留 policy_id、condition_id 供定位，不包括 transmission、校准或其他长字段。

## 可核验来源

[policy_model_provenance.json](policy_model_provenance.json) 逐条记录初始来源、最终接纳研究稿来源、最终内容写入任务，以及 run/task/path/job/model/effort 与发生变化的字段。[origin_evidence.json](origin_evidence.json) 保存从远端隔离测试库及 Worker JobStore 只读提取的任务和接纳稿证据。生成器 export_model_split.py 可在本目录重建导出。

提取时验证真实 Worker 模型与持久任务 request 一致、接纳任务完成、子集完整且互斥、索引 title/criterion 完整相等；原 document3.json SHA-256 仍为 `7b55edcda0dd2d354543544845d6ec973f80bd7be591df021fe642c56b38213a`。

## 保留 ID、后由 Luna 重新交付的 8 条

'''
    for p in provenance:
        if p['same_id_reworked_by_other_model']:
            readme += f"- `{p['policy_id']}`：{p['title']}（{p['accepted_research_author']['task_key']}）。\n"
    readme += '\n## Sol 研究稿后经 Luna 最终编辑的 8 条\n\n'
    for p in provenance:
        if p['accepted_research_author']['model'] == 'gpt-6-sol' and p['changed_after_accepted_research']:
            readme += f"- `{p['policy_id']}`：{p['title']}（{p['final_content_writer']['task_key']}）。\n"
    (HERE / 'README.md').write_text(readme, encoding='utf8')
    assert SOURCE.read_bytes() and hashlib.sha256(SOURCE.read_bytes()).hexdigest() == source_hash
    # Validate exact extraction, including every condition criterion and final content.
    for p, i in zip(document['policies'], index['policies'], strict=True):
        assert p['title'] == i['title'] and [(c['condition_id'], c['criterion']) for c in p['activation_conditions']] == [(c['condition_id'], c['criterion']) for c in i['conditions']]
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
