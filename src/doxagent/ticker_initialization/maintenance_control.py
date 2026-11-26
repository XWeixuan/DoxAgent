"""Control authority for Runtime O3; initialization and O2 remain independent."""


def install(db):
    db.execute('CREATE TABLE IF NOT EXISTS maintenance_control('
        'id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 0,'
        'revision INTEGER NOT NULL DEFAULT 1,reason TEXT NOT NULL)')
    db.execute("INSERT OR IGNORE INTO maintenance_control VALUES(1,0,1,'Storage lifecycle governance: O3 remains disabled')")
    # Enforce at the authority boundary, including late results from a previous
    # Scheduler image. Compare release identity, never JSON byte formatting.
    db.execute("CREATE TRIGGER IF NOT EXISTS runtime_o3_activation_gate BEFORE INSERT ON activation_revisions "
        "WHEN json_extract(NEW.payload,'$.runtime_metadata.maintenance_id') IS NOT NULL "
        "AND (SELECT enabled FROM maintenance_control WHERE id=1)=0 "
        "AND (json_extract(NEW.payload,'$.artifacts.document3.version') IS NOT "
        "(SELECT json_extract(payload,'$.artifacts.document3.version') FROM activation_revisions WHERE revision_id=NEW.base_revision) "
        "OR json_extract(NEW.payload,'$.artifacts.document3.run_id') IS NOT "
        "(SELECT json_extract(payload,'$.artifacts.document3.run_id') FROM activation_revisions WHERE revision_id=NEW.base_revision)) "
        "BEGIN SELECT RAISE(ABORT,'O3_MAINTENANCE_DISABLED'); END")
