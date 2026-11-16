import sqlite3

from doxagent.message_bus_v2.repository import MessageBusV2Repository


def test_alias_lookup_stays_bounded_with_stale_history_statistics():
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE message_identity_aliases(ticker,alias,logical_message_id,
          PRIMARY KEY(ticker,alias,logical_message_id));
        CREATE TABLE logical_message_versions(ticker,logical_message_id,business_version,
          raw_message_id UNIQUE,body_fingerprint,data_json,
          PRIMARY KEY(ticker,logical_message_id,business_version));
        CREATE INDEX logical_content ON logical_message_versions(ticker,body_fingerprint);
        CREATE TABLE standard_messages(standard_message_id PRIMARY KEY,raw_message_id UNIQUE);
        CREATE TABLE stream_members(stream_item_id,standard_message_id);
        CREATE INDEX member_standard ON stream_members(standard_message_id);
    ''')
    db.executemany('INSERT INTO logical_message_versions VALUES(?,?,?,?,?,?)',
                   [('MU',str(i),1,'raw'+str(i),'body'+str(i),'{}') for i in range(10000)])
    db.executemany('INSERT INTO standard_messages VALUES(?,?)',
                   [('std'+str(i),'raw'+str(i)) for i in range(10000)])
    db.executemany('INSERT INTO stream_members VALUES(?,?)',
                   [('item'+str(i),'std'+str(i)) for i in range(9999)])
    db.executemany('INSERT INTO message_identity_aliases VALUES(?,?,?)',
                   [('MU','url:'+str(i),str(i)) for i in range(10000)])
    db.execute('ANALYZE')
    # Reproduce a planner that still expects the version table to be tiny.
    # History growth must not turn a miss into a full walk.
    db.execute("UPDATE sqlite_stat1 SET stat='1 1 1' WHERE idx='logical_content'")
    db.execute('ANALYZE sqlite_schema')
    for aliases, fingerprint, expected in [
        (['url:missing'], None, {}),
        (['url:9998'], None, {'raw9998':False}),
        (['url:9999'], None, {}),  # A known but unpublished article is not a match.
        (['url:missing'], 'body9998', {'raw9998':True}),
    ]:
        instructions = [0]
        def progress():
            instructions[0] += 100
            return instructions[0] > 5000
        db.set_progress_handler(progress, 100)
        assert MessageBusV2Repository.published_candidates(db,'MU',aliases,fingerprint) == expected
        assert instructions[0] < 2000
    db.close()
