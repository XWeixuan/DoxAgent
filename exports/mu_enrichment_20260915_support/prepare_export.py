"""Exact, case-sensitive URL pass followed by an exact title pass."""
import collections
import json
from pathlib import Path
from urllib.parse import urlsplit

folder=Path(__file__).parent
data=json.loads((folder/'remote_stream_snapshot.json').read_text(encoding='utf-8'))
sources={s['source_id']:s['display_name'] for s in data['sources']}
def enrichment(record):
    return record['standard'].get('metadata',{}).get('media_enrichment') or {}
def failed(record):
    e=enrichment(record)
    return e.get('status')=='failed' or (
        e.get('succeeded') is False and e.get('status')!='skipped'
        and bool(record['standard'].get('metadata',{}).get('v2_body_completion')))
failures=sorted((r for r in data['records'] if failed(r)),
                key=lambda r:(r['stream_offset'],r['member_index']),reverse=True)
url_groups={}
for r in failures:
    url_groups.setdefault(r['standard']['url'],[]).append(r)
title_groups={}
for records in url_groups.values():
    title_groups.setdefault(records[0]['standard'].get('title'),[]).append(records)

categories={
'http_401':'HTTP 401：目标站认证或访问限制',
'http_403':'HTTP 403：拒绝访问或风控',
'http_429':'HTTP 429：限流',
'http_404':'HTTP 404：地址不存在或通用入口失效',
'http_400':'HTTP 400：请求或地址不被接受',
'timeout':'网络请求超时',
'deadline_exceeded':'队列/执行预算到期',
'source_summary_only':'仅提取到摘要',
'incomplete_extract':'正文提取不完整',
'empty_extract':'未提取到正文',
'poison_or_navigation_extract':'提取到风控页或导航内容',
'unsupported_media':'不支持的媒体类型',
}
headers=['ticker','source_id','source_name','publisher_name','resolved_domain',
'failure_category','failure_reason','enrichment_outcome','pipeline_version','enrichment_stage',
'http_status','url','title','source_url','article_published_at_utc','stream_published_at_utc',
'collected_at_utc','enrichment_attempted_at_utc','queue_attempt_count','network_attempts_count',
'network_attempts_json','existing_body_chars','existing_body_complete_like','published_body_chars',
'published_body_excerpt','completion_need_assessment','url_kind','observed_stream_count',
'url_pass_groups_merged_by_title','distinct_original_titles','merged_titles_json',
'observed_sources_json','observed_failure_counts_json','first_stream_published_at_utc',
'last_stream_published_at_utc','standard_message_id','raw_message_id','stream_item_id','stream_offset']
rows=[]
generic_groups=[]
for groups in title_groups.values():
    records=sorted([r for g in groups for r in g],
                   key=lambda r:(r['stream_offset'],r['member_index']),reverse=True)
    representative=records[0]
    s=representative['standard'];raw=representative['raw'];e=enrichment(representative)
    quality=e.get('existing_quality') or {}
    reason=e.get('reason_code') or e.get('reason') or 'unknown'
    need=('native_body_looks_complete' if quality.get('complete_like') is True else
          'needs_completion' if quality.get('complete_like') is False else 'unknown')
    identity=s.get('metadata',{}).get('identity_evidence') or {}
    url_kind=identity.get('url_kind') or ''
    if s['url'] in ('https://www.interactivebrokers.com/en/trading/providers.php',
                    'http://www.marketwatch.com/newsviewer',
                    'https://www.benzinga.com/quote/MU/analyst-ratings'):
        url_kind='known_generic_non_article_entry'
    titles=list(dict.fromkeys(r['standard'].get('title') for r in records))
    source_counts=collections.Counter(r['standard']['source_id'] for r in records)
    reason_counts=collections.Counter((enrichment(r).get('reason_code') or enrichment(r).get('reason') or 'unknown') for r in records)
    attempts=e.get('attempts') or []
    row={
      'ticker':'MU','source_id':s['source_id'],'source_name':sources.get(s['source_id'],s['source_id']),
      'publisher_name':s.get('publisher_name') or s.get('source'),
      'resolved_domain':s.get('resolved_domain') or urlsplit(s['url']).hostname,
      'failure_category':categories.get(reason,'其他补全失败'),'failure_reason':reason,
      'enrichment_outcome':e.get('outcome') or 'UNAVAILABLE','pipeline_version':e.get('pipeline_version') or 'legacy_unversioned',
      'enrichment_stage':e.get('stage') or '', 'http_status':e.get('http_status'),
      'url':s['url'],'title':s.get('title'),'source_url':e.get('source_url') or identity.get('original_url') or raw.get('url'),
      'article_published_at_utc':s['published_at'],'stream_published_at_utc':representative['stream_published_at'],
      'collected_at_utc':s['collected_at'],'enrichment_attempted_at_utc':e.get('attempted_at'),
      'queue_attempt_count':e.get('queue_attempt_count') or s.get('metadata',{}).get('v2_body_completion',{}).get('attempt_count'),
      'network_attempts_count':len(attempts),'network_attempts_json':json.dumps(attempts,ensure_ascii=False,separators=(',',':')),
      'existing_body_chars':quality.get('body_length'),'existing_body_complete_like':quality.get('complete_like'),
      'published_body_chars':len(s.get('body') or ''),'published_body_excerpt':(s.get('body') or '')[:500],
      'completion_need_assessment':need,'url_kind':url_kind,'observed_stream_count':len(records),
      'url_pass_groups_merged_by_title':len(groups),'distinct_original_titles':len(titles),
      'merged_titles_json':json.dumps(titles,ensure_ascii=False,separators=(',',':')),
      'observed_sources_json':json.dumps(dict(source_counts),ensure_ascii=False,separators=(',',':')),
      'observed_failure_counts_json':json.dumps(dict(reason_counts),ensure_ascii=False,separators=(',',':')),
      'first_stream_published_at_utc':min(r['stream_published_at'] for r in records),
      'last_stream_published_at_utc':max(r['stream_published_at'] for r in records),
      'standard_message_id':s['standard_message_id'],'raw_message_id':s['raw_message_id'],
      'stream_item_id':representative['stream_item_id'],'stream_offset':representative['stream_offset'],
    }
    rows.append(row)
    if len(titles)>1 and url_kind.startswith('known_generic'):
        generic_groups.append({'url':s['url'],'occurrences':len(records),'distinct_titles':len(titles)})
rows.sort(key=lambda r:(r['source_id'],r['failure_reason'],r['resolved_domain'] or '',r['title'] or ''))
assert len({r['url'] for r in rows})==len(rows)
assert len({r['title'] for r in rows})==len(rows)
assert sum(r['observed_stream_count'] for r in rows)==len(failures)
summary={
'window_start_utc':data['window_start_utc'],'window_end_utc':data['window_end_utc'],
'total_published':len(data['records']),'failed_published':len(failures),
'after_exact_url_dedup':len(url_groups),'after_exact_title_dedup':len(rows),
'url_duplicates_removed':len(failures)-len(url_groups),'title_duplicates_removed':len(url_groups)-len(rows),
'raw_source_counts':dict(collections.Counter(r['standard']['source_id'] for r in failures)),
'dedup_source_counts':dict(collections.Counter(r['source_id'] for r in rows)),
'dedup_source_reason_counts':[{ 'source_id':k[0],'reason':k[1],'count':v} for k,v in collections.Counter((r['source_id'],r['failure_reason']) for r in rows).items()],
'generic_url_collapses':generic_groups,
'ibkr_native_complete_like_failed_count':sum(r['standard']['source_id']=='ibkr_news' and (enrichment(r).get('existing_quality') or {}).get('complete_like') is True for r in failures),
'dedup_rule':'Case-sensitive exact original Standard URL; then case-sensitive exact representative title; keep latest stream occurrence. No trim, URL rewrite, case folding, punctuation normalization or fuzzy matching.',
}
(folder/'prepared_table.json').write_text(json.dumps({'headers':headers,'rows':[[r.get(h) for h in headers] for r in rows]},ensure_ascii=False),encoding='utf-8')
(folder/'export_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
