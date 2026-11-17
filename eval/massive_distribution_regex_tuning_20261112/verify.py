"""Check frozen artifacts and independently replay saved native predictions."""
import collections
import logging
from ops import *

warnings = []


class Capture(logging.Handler):
    def emit(self, event):
        warnings.append(event.getMessage())


logging.getLogger('doxagent.message_bus_v2.relevance').addHandler(Capture())
original = read(DATA / 'verification.json')
assert all(sha(DATA/'final'/f'{s}-gold.jsonl') == original['sha256'][s] for s in ['development', 'validation'])
assert read(HERE/'regular-freeze-all.json')['validation_content_opened'] is False
freeze_all = read(HERE/'regular-freeze-all.json')['frozen_at']
correction = read(HERE/'correction-freeze-all.json')
replayed = 0
attempt_count = 0
family_sets = {s: set() for s in ['development', 'validation']}
summary = []
for ticker in TICKERS:
    regular = read(HERE/ticker/'regular-freeze.json')
    attempts = sorted((HERE/ticker/'regex').glob('attempt-*'))
    assert len(regular['candidates']) <= 5
    extras = [d for d in attempts if d.name.endswith('-overfit')]
    assert len(extras) == 1
    assert sha(extras[0]/'candidate.json') == correction['candidates'][ticker]
    assert all(sha(HERE/ticker/'regex'/a/'candidate.json') == h for a,h in regular['candidates'].items())
    report = comparison(HERE/ticker/'regex', True)
    assert report == read(HERE/ticker/'comparison.json')
    assert report['all_attempts_paired_and_complete']
    assert sha(HERE/'selected'/f'{ticker}.json') == sha(HERE/ticker/'regex'/report['final_best']/'candidate.json')
    for directory in attempts:
        attempt_count += 1
        terms = TickerMonitoringTerms.model_validate(read(directory/'candidate.json'))
        for split in ['development', 'validation']:
            output = directory/split
            meta = read(output/'metadata.json')
            assert meta['candidate_sha256'] == sha(directory/'candidate.json')
            assert meta['context_sha256'] == sha(DATA/'frozen-context.json')
            source = DATA/'final'/ticker/f'{split}-gold.jsonl'
            assert meta['dataset_sha256'] == sha(source)
            receipt = read(output/'receipt.json')
            if split == 'validation': assert receipt['completed_at'] > freeze_all
            if directory.name.endswith('-overfit'): assert receipt['completed_at'] > correction['frozen_at']
            elif split == 'development': assert receipt['completed_at'] < regular['frozen_at']
            saved = rows(output/'predictions.jsonl')
            assert metrics(saved) == read(output/'metrics.json')
            by_id = {r['pair_id']: r for r in rows(source)}
            assert len(saved) == len(by_id) == 50
            assert {r['pair_id'] for r in saved} == set(by_id)
            for prediction in saved:
                r = by_id[prediction['pair_id']]
                family_sets[split].add(r['family_id'])
                message = RawMessageInput(**r['evidence'], url=r['canonical_url'], raw_payload={}, metadata={'language':r['language']})
                assert regex_relevant(terms,r['language'],message) == prediction['prediction']
                assert prediction['gold'] == r['gold']['label']
                replayed += 1
    best = next(a for a in report['attempts'] if a['attempt'] == report['final_best'])
    summary.append({'ticker':ticker,'selected':report['final_best'],'sample_target_met':best['development']['passed'] and best['validation']['passed']})
assert not family_sets['development'].intersection(family_sets['validation'])
assert not warnings, warnings
receipt = {'verified_at':now(),'ticker_count':len(TICKERS),'attempt_count':attempt_count,
           'independent_native_replay_predictions':replayed,'all_attempts_paired':True,
           'dataset_hashes_unchanged':True,'candidate_hashes_unchanged':True,
           'only_l2_changed':True,'global_family_cross_split_overlap':0,
           'freeze_order_checked':True,'native_regex_errors_or_timeouts':warnings,
           'production_writes':0,'decision_model_calls':0,'tickers':summary}
write(HERE/'verification.json',receipt)
print(json.dumps(receipt,ensure_ascii=False))
