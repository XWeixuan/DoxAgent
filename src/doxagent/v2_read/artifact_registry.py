"""Artifact ownership and explicit retention holds, installed by migrations only."""
import json
from pathlib import Path
from .outbox import quoted


def migrate(db):
    schema = """
        CREATE TABLE IF NOT EXISTS artifact (
            id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
            codec TEXT NOT NULL, location TEXT NOT NULL, class TEXT NOT NULL,
            created_at TEXT NOT NULL, verified_at TEXT, state TEXT NOT NULL DEFAULT 'LIVE');
        CREATE TABLE IF NOT EXISTS artifact_ref (
            owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
            PRIMARY KEY(owner_type,owner_id,artifact_id));
        CREATE INDEX IF NOT EXISTS artifact_ref_target ON artifact_ref(artifact_id);
        CREATE TABLE IF NOT EXISTS retention_hold (
            scope TEXT NOT NULL,id TEXT NOT NULL,reason TEXT NOT NULL,released_at TEXT,
            PRIMARY KEY(scope,id,reason));
        CREATE INDEX IF NOT EXISTS artifact_expiry ON artifact(class,state,created_at);
        CREATE TABLE IF NOT EXISTS artifact_deletions (
            id TEXT PRIMARY KEY,manifest TEXT NOT NULL,at TEXT NOT NULL);
    """
    for statement in schema.split(";"):
        if statement.strip():
            db.execute(statement)

    db.execute("CREATE TRIGGER IF NOT EXISTS artifact_reference_guard BEFORE INSERT ON artifact_ref "
               "WHEN EXISTS(SELECT 1 FROM artifact WHERE id=NEW.artifact_id AND state<>'LIVE') "
               "BEGIN SELECT RAISE(ABORT,'ARTIFACT_RECLAIM_IN_PROGRESS'); END")
    db.execute("CREATE TRIGGER IF NOT EXISTS artifact_hold_guard BEFORE INSERT ON retention_hold "
               "WHEN EXISTS(SELECT 1 FROM artifact WHERE (id=NEW.id OR NEW.scope='ALL') AND (state='DELETING' OR (state='DELETED' AND NEW.scope<>'ALL'))) "
               "BEGIN SELECT RAISE(ABORT,'ARTIFACT_ALREADY_DELETING'); END")


def native_triggers(db, table, columns, identity):
    """Historical references are append-only business evidence, not age-based logs."""
    for column in columns:
        for operation in ("INSERT", "UPDATE"):
            name = quoted("v2_artifact_" + table + "_" + column + "_" + operation)
            changed = "UPDATE OF " + quoted(column) if operation == "UPDATE" else "INSERT"
            db.execute("CREATE TRIGGER IF NOT EXISTS " + name + " AFTER " + changed + " ON " + quoted(table) +
                " WHEN instr(NEW." + quoted(column) + ",'$doxagent_content_v1')>0 BEGIN "
                "INSERT INTO artifact(id,sha256,size,codec,location,class,created_at) "
                "SELECT value,value,0,'json-chunks-v1','native-files/'||substr(value,1,2)||'/'||value,'BUSINESS',strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "FROM json_tree(NEW." + quoted(column) + ") WHERE key='$doxagent_content_v1' ON CONFLICT DO NOTHING; "
                "INSERT INTO artifact_ref SELECT '" + table + "'," + identity + ",value FROM json_tree(NEW." + quoted(column) + ") WHERE key='$doxagent_content_v1' ON CONFLICT DO NOTHING; END")


def retain(db, identity, sha256, size, *, codec, location, owner_type, owner_id, classification="BUSINESS"):
    if classification not in {"BUSINESS", "DIAGNOSTIC", "UNKNOWN"}:
        raise ValueError("unsupported retention class")
    db.execute("INSERT INTO artifact(id,sha256,size,codec,location,class,created_at) VALUES(?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
               "ON CONFLICT(id) DO UPDATE SET size=excluded.size WHERE artifact.sha256=excluded.sha256 AND artifact.state='LIVE'",
               (identity, sha256, size, codec, location, classification))
    state = db.execute("SELECT state,sha256 FROM artifact WHERE id=?", (identity,)).fetchone()
    if not state or state[0] != "LIVE" or state[1] != sha256:
        raise ValueError("artifact unavailable for new reference")
    db.execute("INSERT OR IGNORE INTO artifact_ref VALUES(?,?,?)", (owner_type, owner_id, identity))


def collect_diagnostics(store, *, now, limit=10):
    """Recoverable mark/quarantine/finalize protocol; business/unknown files stay put."""
    import hashlib
    import shutil
    from datetime import timedelta
    from .settings import Limits
    from .repository import instant, encode
    root = store.path.parent.resolve()
    protected = ("EXISTS(SELECT 1 FROM artifact_ref r WHERE r.artifact_id=a.id) OR "
                 "EXISTS(SELECT 1 FROM retention_hold h WHERE h.released_at IS NULL AND (h.scope='ALL' OR h.id=a.id))")
    with store.connect(write=True, timeout=.05) as db:
        db.execute("UPDATE artifact SET state='MARKED' WHERE id IN (SELECT id FROM artifact a WHERE class='DIAGNOSTIC' AND state='LIVE' AND created_at<? AND NOT ("+protected+") LIMIT ?)",
                   (instant(now-timedelta(days=Limits.load().diagnostic_days)),limit))
    with store.connect() as db:
        candidates = db.execute("SELECT id,sha256,location,state FROM artifact WHERE class='DIAGNOSTIC' AND state IN ('MARKED','QUARANTINED','DELETING') LIMIT ?", (limit,)).fetchall()
    changed = []
    for identity, digest, location, stage in candidates:
        source = (root / location).resolve()
        quarantine = root / "quarantine" / digest
        if not source.is_relative_to(root) or source == root or source.is_symlink():
            raise ValueError("artifact escaped owned storage")
        with store.connect(write=True, timeout=.05) as db:
            if db.execute("SELECT 1 FROM artifact a WHERE a.id=? AND ("+protected+")", (identity,)).fetchone():
                continue
            manifest = {"id": identity, "sha256": digest, "original": location, "quarantine": str(quarantine.relative_to(root))}
            if stage == "MARKED":
                quarantine.parent.mkdir(exist_ok=True)
                if source.exists():
                    if quarantine.exists():
                        raise ValueError("quarantine collision")
                    if source.is_file():
                        with source.open("rb") as stream:
                            if hashlib.file_digest(stream,"sha256").hexdigest() != digest:
                                raise ValueError("diagnostic checksum mismatch")
                    else:
                        from .content_files import ContentFiles
                        info = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
                        if info["sha256"] != digest:
                            raise ValueError("diagnostic manifest mismatch")
                    source.replace(quarantine)
                elif not quarantine.exists():
                    raise ValueError("marked artifact missing from source and quarantine")
                db.execute("INSERT OR REPLACE INTO artifact_deletions VALUES(?,?,?)", (identity,encode(manifest),instant(now)))
                db.execute("UPDATE artifact SET state='QUARANTINED' WHERE id=?", (identity,))
                changed.append(manifest)
            else:
                # Persist the intent first; an interrupted unlink resumes on the next pass.
                db.execute("UPDATE artifact SET state='DELETING' WHERE id=?", (identity,))
        if stage in {"QUARANTINED","DELETING"}:
            if quarantine.exists():
                if quarantine.resolve().parent != (root / "quarantine").resolve() or quarantine.is_symlink():
                    raise ValueError("quarantine escaped owned storage")
                if quarantine.is_dir():
                    shutil.rmtree(quarantine)
                else:
                    quarantine.unlink()
            with store.connect(write=True, timeout=.05) as db:
                db.execute("UPDATE artifact SET state='DELETED' WHERE id=? AND state='DELETING'", (identity,))
    return changed


def inventory(store, *, limit=1000):
    """Register preexisting material without guessing that it is disposable."""
    import re
    from .repository import instant
    from datetime import datetime, UTC
    inspected = []
    with store.connect() as db:
        row = db.execute("SELECT value FROM read_meta WHERE key='artifact_inventory_after'").fetchone()
        after = row[0] if row else ""
    last = after
    for folder in ("content-files", "native-files"):
        root = store.path.parent / folder
        for path in sorted(root.glob("*/*/manifest.json")):
            if len(inspected) >= limit:
                break
            coordinate = folder + "/" + path.parent.name
            if coordinate <= after:
                continue
            identity = path.parent.name
            last = coordinate
            if not re.fullmatch("[0-9a-f]{64}", identity):
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("sha256") != identity:
                raise ValueError("content inventory identity mismatch")
            inspected.append((identity, value["size"], str(path.parent.relative_to(store.path.parent))))
    with store.connect(write=True) as db:
        db.execute("INSERT INTO read_meta VALUES('artifact_inventory_after',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (last if len(inspected)>=limit else "",))
        for identity, size, location in inspected:
            db.execute("INSERT INTO artifact(id,sha256,size,codec,location,class,created_at) VALUES(?,?,?,'chunks-v1',?,'UNKNOWN',?) "
                       "ON CONFLICT(id) DO UPDATE SET size=excluded.size WHERE artifact.sha256=excluded.sha256",
                       (identity,identity,size,location,instant(datetime.now(UTC))))
    return {"inspected": len(inspected), "unclassified_preserved": True}


def register_diagnostic(store, path, *, created_at, producer, identity):
    """Only a producer-owned terminal diagnostic may enter age-based retention."""
    import hashlib
    path = Path(path).resolve(strict=True)
    root = store.path.parent.resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise ValueError("diagnostic must be an owned regular file")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream,"sha256").hexdigest()
    with store.connect(write=True) as db:
        db.execute("INSERT OR IGNORE INTO artifact(id,sha256,size,codec,location,class,created_at) VALUES(?,?,?,'raw',?,'DIAGNOSTIC',?)",
                   (identity,digest,path.stat().st_size,str(path.relative_to(root)),created_at))
    return identity


def upgrade_native_trigger_conflicts(db):
    """Small transactional DDL repair; leaves all rows and capture identities intact."""
    rows = db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'v2_artifact_%'").fetchall()
    for name, sql in rows:
        if "INSERT OR IGNORE INTO artifact" not in sql:
            continue
        fixed = sql.replace("INSERT OR IGNORE INTO artifact", "INSERT INTO artifact").replace(
            "WHERE key='$doxagent_content_v1';", "WHERE key='$doxagent_content_v1' ON CONFLICT DO NOTHING;")
        db.execute("DROP TRIGGER " + quoted(name))
        db.execute(fixed)
