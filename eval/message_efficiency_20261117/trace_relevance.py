"""Bounded replay of retained inputs: no fetch, classifier call, or mutation."""
import base64
import hashlib
import inspect
import json
import os
import re
import sqlite3
from types import SimpleNamespace

import regex
from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms
from doxagent.message_bus_v2.relevance import _match, _normalize, match_rules
from doxagent.v2_read.native_content import NativeContent

TARGETS = json.loads(base64.b64decode('__TARGETS__'))
p = os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']
c = sqlite3.connect('file:' + p + '?mode=ro', uri=True, timeout=2)
c.execute('pragma query_only=on')
codec = NativeContent(p)
out = {'samples': [], 'hashes': {}}
terms_cache = {}
footer = re.compile(r"\s*See today[’']s quantum computing news on Quantum Zeitgeist[^\n]*(?:\n|$)", re.I)
for item in TARGETS:
    raw = c.execute('select data_json from standard_messages where standard_message_id=?', (item['id'],)).fetchone()
    d = codec.decode(json.loads(raw[0]))
    md = d.get('metadata', {})
    rev = md.get('distribution', {}).get('terms_revision')
    if not rev:
        rev = c.execute('select current_revision from ticker_monitoring_terms where ticker=?', (d['ticker'],)).fetchone()[0]
    key = (d['ticker'], rev)
    if key not in terms_cache:
        saved = c.execute('select config_json from ticker_monitoring_term_revisions where ticker=? and revision=?', key).fetchone()
        terms_cache[key] = TickerMonitoringTerms.model_validate_json(codec.text(saved[0]))
    terms = terms_cache[key]
    massive = d['source_id'].endswith('_massive')
    rule_map = terms.massive_l2 if massive else terms.l2
    # Standard stores the classifier's complete evidence in metadata.
    ce = md.get('content_evidence', {})
    msg = SimpleNamespace(title=d.get('title') or '', summary=ce.get('summary') or '', body=d.get('body') or '')
    if not isinstance(msg.body, str):
        raise TypeError('body is not decoded')
    language = md.get('language') or 'en'
    traces = []
    for lang in dict.fromkeys((language, 'en')):
        rules = rule_map.get(lang)
        for g in rules.groups if rules else []:
            if g.any and not any(_match(t, msg) for t in g.any): continue
            if not all(_match(t, msg) for t in g.all): continue
            if any(_match(t, msg) for t in g.none): continue
            hits = []
            for term in [*g.any, *g.all]:
                for field in ('title', 'summary', 'body') if term.field == 'all' else (term.field,):
                    value = _normalize(getattr(msg, field))
                    pattern = term.regex or regex.escape(_normalize(term.literal))
                    if term.literal is not None and term.whole_word: pattern = rf'(?<!\w){pattern}(?!\w)'
                    matches = list(regex.finditer(pattern, value, flags=0 if term.case_sensitive else regex.I, timeout=.05))
                    if matches:
                        m = matches[0]
                        hits.append({'field': field, 'count': len(matches), 'matched': m.group(), 'position': m.start(), 'field_length': len(value), 'context': value[max(0,m.start()-65):m.end()+90]})
            traces.append({'language': lang, 'group': g.id, 'hits': hits})
    stripped = footer.sub('', msg.body)
    if d['source_id'] == 'dcd_infrastructure_rss':
        stripped = re.split(r'\n\s*More in [^\n]+\n', msg.body, maxsplit=1)[0]
    stripped_msg = SimpleNamespace(title=msg.title, summary=footer.sub('', msg.summary), body=stripped)
    out['samples'].append({'id': item['id'], 'day':item.get('day'), 'ticker': d['ticker'], 'source_id': d['source_id'], 'title': msg.title, 'url':d.get('url'), 'terms_revision': rev, 'distribution': md.get('distribution'), 'body_length':len(msg.body), 'body_tail':msg.body[-650:] if d['source_id'] in ('quantum_zeitgeist','dcd_infrastructure_rss') else None, 'footer_removed_chars':len(msg.body)-len(stripped), 'original_regex':match_rules(rule_map,language,msg), 'without_template_regex':match_rules(rule_map,language,stripped_msg), 'traces': traces})
for module in ['doxagent.message_bus_v2.live','doxagent.message_bus_v2.relevance','doxagent.message_bus_v2.deduplication','doxagent.message_bus_v2.body_results']:
    m = __import__(module, fromlist=['_'])
    path = inspect.getfile(m)
    out['hashes'][module] = hashlib.sha256(open(path,'rb').read()).hexdigest()
c.close()
print(json.dumps(out, ensure_ascii=False))
