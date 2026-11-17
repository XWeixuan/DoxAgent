"""Local worst-case cost probe, bounded to 100 ms per case; no production calls."""
import json, random, time
from pathlib import Path
from rapidfuzz.fuzz import ratio

rng=random.Random(20261117)
out=[]
for size in (8192,32768,65536):
    # Same-length 94%-similar bodies defeat length pruning but must not match 95%.
    a=''.join(rng.choices('abcdefghijklmnopqrstuvwxyz ',k=size))
    chars=list(a)
    for i in rng.sample(range(size),int(size*.065)):
        chars[i]='0'
    b=''.join(chars)
    exact=ratio(a,b)
    started=time.perf_counter();calls=0;single=[]
    while calls<1024 and time.perf_counter()-started<.1:
        t=time.perf_counter()
        assert ratio(a,b,score_cutoff=95)<95
        single.append((time.perf_counter()-t)*1000);calls+=1
    elapsed=(time.perf_counter()-started)*1000
    out.append({'chars':size,'ratio':exact,'comparisons_in_probe':calls,
        'elapsed_ms':round(elapsed,3),'max_one_comparison_ms':round(max(single),3),
        'linear_1024_comparison_ms_estimate':round(elapsed/calls*1024,1)})
result={'note':'Local workstation synthetic cost, not server SLA; linear estimate is not a full 1024 run.','cases':out}
Path(__file__).with_name('body-stress.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
