"""Pinned source clocks from Read metadata; never query source databases in HTTP."""
import json

DEPENDENCIES = {
    'EVENTS': ('initialization', 'runtime'),
    'POLICIES': ('initialization', 'runtime'),
    'RESEARCH': ('research', 'initialization', 'runtime'),
    'EXPECTATIONS': ('research', 'initialization', 'runtime'),
    'RUNTIME': ('initialization', 'bus', 'runtime'),
    'MESSAGE_BUS': ('initialization', 'bus', 'runtime'),
}


def snapshot(db, page):
    health = {r[0]: r for r in db.execute('SELECT source,head,checkpoint,checked_at,error FROM source_health')}
    clocks = {r[0].split(':', 1)[1]: json.loads(r[1]) for r in db.execute("SELECT key,value FROM read_meta WHERE key LIKE 'projection_clock:%'")}
    gaps = dict(db.execute('SELECT source,count(*) FROM gaps GROUP BY source'))
    result = []
    for source in DEPENDENCIES.get(page, ('research', 'initialization', 'bus', 'runtime')):
        row = health.get(source)
        if row is None:
            row = (source, 0, 0, '1970-01-01T00:00:00Z', 'SOURCE_HEALTH_UNAVAILABLE')
        clock = clocks.get(source, {})
        matched = clock.get('checkpoint') == row[2] and clock.get('head') == row[1]
        result.append(dict(source=source, head=row[1], checkpoint=row[2], checked_at=row[3],
                           error=row[4], historical_gaps=gaps.get(source, 0),
                           source_event_at=clock.get('source_event_at') if matched else None,
                           oldest_pending_at=clock.get('oldest_pending_at') if matched else None))
    return result
