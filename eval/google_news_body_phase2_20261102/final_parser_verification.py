"""Verify final cleaning against captured real responses; never refetch or choose another sample."""
import gzip,json
from pathlib import Path
from doxagent.content_enrichment.quality import inspect_html,inspect_reader,choose_candidate
D=Path(__file__).parent
rows=json.load(gzip.open(D/'validation-final.json.gz','rt',encoding='utf-8'))
captures=json.load(gzip.open(D/'validation-captures.json.gz','rt',encoding='utf-8'))
out=[]
for r in rows:
    value={**r}
    if r['outcome']=='FULL':
        stage='reader' if r['method']=='reader_article' else 'zyte_http' if r['method'].startswith('zyte_http_') else 'zyte' if r['method'].startswith('zyte_') else 'http_public'
        c=next(c for c in captures if c['url']==r['url'] and c['stage']==stage)
        info=(inspect_reader if stage=='reader' else inspect_html)(c['text'],c['final_url'],r['title'])
        candidate,outcome,reason=choose_candidate(info,r['title'])
        assert outcome in ('FULL','SHORT_FULL'),(r['site'],r['title'],reason)
        value.update(outcome=outcome,content=candidate.text,chars=len(candidate.text),parser_method=candidate.method)
    elif r['site']=='moomoo.com' and '/community/feed/' in r['url']:
        c=next((c for c in captures if c['url']==r['url'] and c['stage']=='http_public'),None)
        if c:
            info=inspect_html(c['text'],c['final_url'],r['title'])
            if info.access_reason=='short_social_post':value.update(reason='short_social_post')
    elif r['site']=='futunn.com' and '/discussion/' in r['url']:
        value.update(reason='non_article_target')
    out.append(value)
(D/'accepted-final.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print('Final captured-body verification',len(out),'FULL',sum(r['outcome']=='FULL' for r in out),'SHORT_FULL',sum(r['outcome']=='SHORT_FULL' for r in out))
