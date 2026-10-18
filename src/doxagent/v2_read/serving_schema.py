"""Single physical current image with a bounded history and SQL compatibility views.

The views preserve the existing internal query contract. They never duplicate
current rows. Production V3 files are converted by the generation builder, not
by a live startup migration.
"""
import json


def install(db):
    db.execute('PRAGMA recursive_triggers=ON')
    for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND "
                            "(tbl_name IN ('objects','contributions','metric_buckets') "
                            "OR name LIKE '%_current_insert' OR name LIKE '%_current_close')").fetchall():
        db.execute('DROP TRIGGER "'+name+'"')
    db.execute('DROP TABLE business_revisions')
    db.execute('CREATE TABLE IF NOT EXISTS definitions('
               'sha256 TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL)')
    db.execute('CREATE TABLE definition_ref(owner_type TEXT,owner_id TEXT,sha256 TEXT,'
               'PRIMARY KEY(owner_type,owner_id,sha256))')
    db.execute('CREATE INDEX definition_ref_target ON definition_ref(sha256)')
    upgrade(db)
    for relation, current, history, identity in (
        ('objects','object_current','object_history',('kind','ticker','id','valid_from')),
        ('contributions','contributions_current','contributions_history',
         ('metric','ticker','entity','day','dimensions','valid_from')),
        ('metric_buckets','metric_buckets_current','metric_buckets_history',
         ('metric','ticker','day','dimensions','valid_from')),
    ):
        # This runs on an empty new generation. Old current rows must not remain
        # in history, including when used on small explicit migration fixtures.
        db.execute('DELETE FROM '+relation+' WHERE valid_to IS NULL')
        db.execute('ALTER TABLE '+relation+' RENAME TO '+history)
        db.execute('CREATE VIEW '+relation+' AS SELECT * FROM '+current+' UNION ALL SELECT * FROM '+history)
        columns=[r[1] for r in db.execute('PRAGMA table_info('+history+')')]
        names=','.join(columns)
        values=','.join('NEW.'+c for c in columns)
        match=' AND '.join(c+'=OLD.'+c for c in identity)
        # Current metric tables store generated dimensions as ordinary columns.
        current_columns=[r[1] for r in db.execute('PRAGMA table_info('+current+')')]
        extras=[c for c in current_columns if c not in columns]
        current_names=','.join(current_columns)
        current_values=values+''.join(
            ",json_extract(NEW.dimensions,'$."+c+"')" for c in extras)
        insert_current='INSERT OR REPLACE INTO '+current+'('+current_names+') SELECT '+current_values+' WHERE NEW.valid_to IS NULL;'
        insert_history='INSERT OR REPLACE INTO '+history+'('+names+') SELECT '+values+' WHERE NEW.valid_to IS NOT NULL;'
        delete='DELETE FROM '+current+' WHERE '+match+';DELETE FROM '+history+' WHERE '+match+';'
        db.execute('CREATE TRIGGER '+relation+'_v4_insert INSTEAD OF INSERT ON '+relation+
                   ' BEGIN '+insert_current+insert_history+' END')
        db.execute('CREATE TRIGGER '+relation+'_v4_update INSTEAD OF UPDATE ON '+relation+
                   ' BEGIN '+delete+insert_current+insert_history+' END')
        db.execute('CREATE TRIGGER '+relation+'_v4_delete INSTEAD OF DELETE ON '+relation+
                   ' BEGIN '+delete+' END')
    db.execute('DROP INDEX IF EXISTS objects_head')
    db.execute('CREATE INDEX IF NOT EXISTS history_expiry ON object_history(valid_to)')
    db.execute('CREATE INDEX IF NOT EXISTS current_case_fact ON object_current('
               "kind,ticker,parent,json_extract(payload,'$.fact.fact_id')) WHERE kind='fact'")
    db.execute('CREATE INDEX IF NOT EXISTS current_case_policy ON object_current('
               "kind,ticker,parent,json_extract(payload,'$.summary.policy_id')) WHERE kind='policy_detail'")
    db.execute('CREATE INDEX IF NOT EXISTS current_case_provisional ON object_current('
               "kind,ticker,json_extract(payload,'$.provisional_event_id'),json_extract(payload,'$.trading_date'),json_extract(payload,'$.snapshot_version')) WHERE kind='native:runtime_v2_candidates'")
    db.execute('UPDATE schema_meta SET version=4')
    db.execute("INSERT OR REPLACE INTO read_meta VALUES('serving_schema','4')")
    from .artifact_registry import native_triggers
    for table in ('object_current','object_history'):
        identity = "json_array(NEW.kind,NEW.ticker,NEW.id,NEW.valid_from)"
        native_triggers(db,table,['payload'],identity,classification='SERVING')
        old_identity = identity.replace('NEW.', 'OLD.')
        for op in ('INSERT', 'UPDATE', 'DELETE'):
            clear = ("DELETE FROM definition_ref WHERE owner_type='"+table+"' AND owner_id="+old_identity+";") if op != 'INSERT' else ''
            add = ("INSERT OR IGNORE INTO definition_ref SELECT '"+table+"',"+identity+",value FROM json_tree(NEW.payload) WHERE key='$doxagent_definition_v4';") if op != 'DELETE' else ''
            db.execute('CREATE TRIGGER '+table+'_definition_'+op+' AFTER '+op+' ON '+table+' BEGIN '+clear+add+' END')


def history_relation(db, base='objects'):
    name={'objects':'object_history','contributions':'contributions_history',
          'metric_buckets':'metric_buckets_history'}[base]
    return name if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone() else base


def upgrade(db):
    """Small, explicit V4 DDL addition; no live table rewrite or index build."""
    db.execute('CREATE TABLE IF NOT EXISTS definition_exit(sha256 TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE IF NOT EXISTS serving_content_exit(id TEXT PRIMARY KEY)')
    db.execute('CREATE INDEX IF NOT EXISTS current_native_identity ON object_current(kind,id,ticker)')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='object_history'").fetchone():
        # Empty by V4's physical contract; keeps legacy head-only UNION queries
        # from walking every historical payload while compatible callers retire.
        db.execute('CREATE INDEX IF NOT EXISTS history_empty_heads ON object_history(kind,id,ticker) WHERE valid_to IS NULL')
    for table,column,queue in (('definition_ref','sha256','definition_exit'),
                               ('artifact_ref','artifact_id','serving_content_exit')):
        target_column='sha256' if table=='definition_ref' else 'id'
        db.execute('CREATE TRIGGER IF NOT EXISTS '+table+'_exit AFTER DELETE ON '+table+' BEGIN INSERT OR IGNORE INTO '+queue+' VALUES(OLD.'+column+'); END')
        db.execute('CREATE TRIGGER IF NOT EXISTS '+table+'_acquire AFTER INSERT ON '+table+' BEGIN DELETE FROM '+queue+' WHERE '+target_column+'=NEW.'+column+'; END')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='object_history'").fetchone() and not db.execute("SELECT 1 FROM read_meta WHERE key='serving_native_reuse_v1'").fetchone():
        from .artifact_registry import native_triggers
        for table in ('object_current','object_history'):
            native_triggers(db,table,['payload'],'json_array(NEW.kind,NEW.ticker,NEW.id,NEW.valid_from)',classification='SERVING')
        db.execute("INSERT INTO read_meta VALUES('serving_native_reuse_v1','1')")


DEFINITION_MARKER='$doxagent_definition_v4'


def normalize_definition(db,kind,value):
    """One immutable definition, many snapshot/activation membership rows."""
    import hashlib
    field={'fact':'fact','event_detail':'event','policy_detail':'policy'}.get(kind)
    if not field or not isinstance(value,dict) or not isinstance(value.get(field),dict):
        return value
    definition=value[field]
    if DEFINITION_MARKER in definition:return value
    raw=json.dumps(definition,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    digest=hashlib.sha256(raw.encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO definitions VALUES(?,?,?)',(digest,kind,raw))
    # Preserve only scalars actually used by SQL predicates. Detail readers
    # dereference the definition through the row codec, within the same DB txn.
    inline={k:definition[k] for k in ('fact_id','event_id','policy_id','title') if k in definition}
    return {**value,field:{DEFINITION_MARKER:digest,**inline}}
