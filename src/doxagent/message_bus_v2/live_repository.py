"""Three small realtime facts: article work, ticker delivery and origin aliases.

Content/model preparation is explicitly outside the SQLite writer. No realtime
poll/distribution run or durable copy of an EnrichmentJob is created here.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from .admission import AdmissionContext, evaluate_admission
from .body_results import article_key, update_hint
from .schema import canonical_json, new_id, sha256_text, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_articles(
 article_key TEXT PRIMARY KEY,source_id TEXT NOT NULL,url TEXT NOT NULL,publisher TEXT NOT NULL DEFAULT '',
 material_hint TEXT NOT NULL,first_seen_at TEXT NOT NULL,input_ref TEXT NOT NULL,
 input_revision INTEGER NOT NULL DEFAULT 1,material_observed_at TEXT NOT NULL DEFAULT '',state TEXT NOT NULL,due_at TEXT NOT NULL,
 attempt INTEGER NOT NULL DEFAULT 0,claim_token TEXT,lease_until TEXT,first_fetch_at TEXT,
 result_ref TEXT,outcome TEXT,body_digest TEXT,acquired_at TEXT,checked_at TEXT,
 revision INTEGER NOT NULL DEFAULT 0,last_error TEXT,next_check_at TEXT,work_kind TEXT NOT NULL,
 attempts_json TEXT NOT NULL DEFAULT '[]',validators_json TEXT NOT NULL DEFAULT '{}',title TEXT NOT NULL DEFAULT '',last_result_id TEXT,
 fetch_total INTEGER NOT NULL DEFAULT 0,full_total INTEGER NOT NULL DEFAULT 0,cache_total INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS live_article_due ON live_articles(state,due_at,article_key);
CREATE INDEX IF NOT EXISTS live_article_publisher ON live_articles(state,publisher,due_at);
CREATE INDEX IF NOT EXISTS live_article_check ON live_articles(next_check_at,article_key);
CREATE INDEX IF NOT EXISTS live_article_observed ON live_articles(first_seen_at,article_key);
CREATE TABLE IF NOT EXISTS live_deliveries(
 article_key TEXT NOT NULL,ticker TEXT NOT NULL,source_id TEXT NOT NULL,binding_id TEXT NOT NULL,
 terms_revision INTEGER NOT NULL,admission_json TEXT NOT NULL,bootstrap INTEGER NOT NULL,
 state TEXT NOT NULL,due_at TEXT NOT NULL,claim_token TEXT,lease_until TEXT,
 decision_digest TEXT,published_digest TEXT,regex_result INTEGER,jev_result REAL,last_error TEXT,
 raw_id TEXT,standard_id TEXT,consumable_at TEXT,created_at TEXT NOT NULL,
 PRIMARY KEY(article_key,ticker));
CREATE INDEX IF NOT EXISTS live_delivery_due ON live_deliveries(state,due_at,article_key);
CREATE TABLE IF NOT EXISTS live_origins(
 origin_key TEXT PRIMARY KEY,article_key TEXT NOT NULL,source_id TEXT NOT NULL,
 unit TEXT NOT NULL,material_hint TEXT NOT NULL,first_seen_at TEXT NOT NULL,targets_json TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS live_origin_article ON live_origins(article_key);
CREATE INDEX IF NOT EXISTS collection_acceptance_time ON collection_acceptances(accepted_at);
"""


class LiveRepository:
    def __init__(self, bus_repository):
        self.bus = bus_repository
        self.path = self.bus.path
        self.spool = self.path.parent / "live-results"

    def install(self):
        with self.bus.transaction() as db:
            db.executescript(SCHEMA)
            for table, fields in {
                "live_articles": {
                    "material_observed_at": "TEXT NOT NULL DEFAULT ''",
                    "attempts_json": "TEXT NOT NULL DEFAULT '[]'",
                    "validators_json": "TEXT NOT NULL DEFAULT '{}'",
                    "title": "TEXT NOT NULL DEFAULT ''",
                    "last_result_id": "TEXT",
                    "fetch_total": "INTEGER NOT NULL DEFAULT 0",
                    "full_total": "INTEGER NOT NULL DEFAULT 0",
                    "cache_total": "INTEGER NOT NULL DEFAULT 0",
                },
                "live_origins": {"targets_json": "TEXT NOT NULL DEFAULT '[]'"},
            }.items():
                existing = {r[1] for r in db.execute("PRAGMA table_info(" + table + ")")}
                for field, definition in fields.items():
                    if field not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {field} {definition}")

    def put(self, value):
        """Prepare an immutable document and all of its refs before BEGIN."""
        from doxagent.v2_read.source_lifecycle import native_hashes
        from doxagent.v2_read.native_content import MARKER

        encoded = self.bus.content.encode(value)
        raw = encoded.encode()
        digest = self.bus.content.files.put(raw)
        ref = canonical_json({MARKER: digest, "bytes": len(raw)})
        return ref, {digest, *native_hashes(encoded)}

    def load(self, ref):
        # The root is an immutable encoded JSON document; expand its explicit
        # nested refs once, not by selecting data_json on a scalar queue query.
        value = self.bus.content.decode(json.loads(ref))
        return self.bus.content.decode(value)

    @staticmethod
    def _refs(db, owner, key, refs):
        db.execute("DELETE FROM artifact_ref WHERE owner_type=? AND owner_id=?", (owner, key))
        for digest in refs:
            db.execute(
                "INSERT INTO artifact(id,sha256,size,codec,location,class,created_at) "
                "VALUES(?,?,0,'json-chunks-v1',?,'BUSINESS',?) ON CONFLICT(id) DO NOTHING",
                (digest, digest, f"native-files/{digest[:2]}/{digest}", utc_now().isoformat()),
            )
            db.execute("INSERT OR IGNORE INTO artifact_ref VALUES(?,?,?)", (owner, key, digest))

    def prepare_input(
        self,
        source,
        targets,
        messages,
        *,
        observed_at,
        unit,
        bootstrap=False,
        admission=None,
        recheck=False,
    ):
        from .schema import RawMessageInput

        context = admission or AdmissionContext(observed_at=datetime.fromisoformat(observed_at))
        rows = []
        for value in messages:
            message = (
                value
                if isinstance(value, RawMessageInput)
                else RawMessageInput.model_validate(value)
            )
            if (
                not (
                    message.metadata.get("language")
                    or message.metadata.get("identity_evidence", {}).get("language")
                )
                and source.content_language
            ):
                message = message.model_copy(
                    update={
                        "metadata": {
                            **message.metadata,
                            "language": source.content_language.strip().lower(),
                        }
                    }
                )
            if not message.admission_context:
                message = message.model_copy(update={"admission_context": context})
            key, hint = article_key(message, source.source_id), update_hint(message)
            from .deduplication import article_url

            locator = article_url(
                message.url,
                message.metadata.get("identity_evidence", {}).get("url_kind", "unknown"),
            )
            origin = sha256_text(
                canonical_json(
                    [
                        source.source_id,
                        locator or message.external_id or message.source_item_key or message.url,
                    ]
                )
            )
            with self.bus._connect() as db:
                alias = db.execute(
                    "SELECT article_key FROM live_origins WHERE origin_key=?", ("canonical:" + key,)
                ).fetchone()
                key = alias[0] if alias else key
                prior = db.execute(
                    "SELECT a.input_ref,o.material_hint,a.material_observed_at FROM live_articles a "
                    "LEFT JOIN live_origins o ON o.origin_key=? WHERE a.article_key=?",
                    (origin, key),
                ).fetchone()
            reason = evaluate_admission(
                message.published_at, context, utc_now(), message.publication_time_basis
            )
            legacy_known = None
            if targets and not prior:
                first = self.bus.get_binding(targets[0][0])
                legacy_known = self.bus.known_published_article(first, message) if first else None
            if reason and not prior and not legacy_known and not recheck:
                continue  # The durable list/delta is proof enough for expired, never-admitted input.
            if prior and (prior[1] is None or prior[1] == hint or observed_at < prior[2]):
                ref, refs = prior[0], set()
            else:
                ref, refs = self.put(message.model_dump(mode="json"))
            from .deduplication import text, stable_fields

            old_fields = (
                legacy_known.metadata.get("content_evidence", {}).get("input_fields", {})
                if legacy_known
                else {}
            )
            fields = stable_fields(message)
            legacy_changed = bool(
                legacy_known
                and (
                    text(message.title) != text(legacy_known.title)
                    or any(
                        fields.get(k) and fields[k] != old_fields.get(k)
                        for k in ("updated_at", "provider_version")
                    )
                )
            )
            baseline = bootstrap
            if source.source_id == "yahoo_finance_news" and isinstance(
                message.metadata.get("_poll_bootstrap"), bool
            ):
                baseline = message.metadata["_poll_bootstrap"]
            if source.source_id == "sec_edgar" and targets:
                first = self.bus.get_binding(targets[0][0])
                if first and message.published_at >= first.created_at:
                    baseline = False
            rows.append(
                dict(
                    key=key,
                    hint=hint,
                    origin=origin,
                    ref=ref,
                    refs=refs,
                    source_id=source.source_id,
                    url=message.url,
                    title=message.title or "",
                    observed=observed_at,
                    first_seen=legacy_known.first_seen_at.isoformat()
                    if legacy_known
                    else observed_at,
                    legacy_body_key=legacy_known.metadata.get("article_body_result_key")
                    if legacy_known
                    else None,
                    kind="content_update" if legacy_changed else "new_article",
                    unit=unit,
                    targets=targets,
                    admission=context.model_dump_json(),
                    direct=source.acquisition_mode.value != "by_distribution",
                    reason=reason if not legacy_known else None,
                    bootstrap=baseline,
                )
            )
        return rows

    def accept(self, rows, *, capture_id=None):
        keys = []
        now = utc_now().isoformat()
        with self.bus.transaction() as db:
            if (
                capture_id
                and db.execute(
                    "SELECT 1 FROM collection_acceptances WHERE capture_id=?", (capture_id,)
                ).fetchone()
            ):
                return []
            for item in rows:
                key = item["key"]
                if item.get("legacy_body_key") and item["legacy_body_key"] != key:
                    db.execute(
                        "INSERT OR IGNORE INTO live_origins VALUES(?,?,'resolved','legacy','',?,'[]')",
                        ("legacy_body:" + item["legacy_body_key"], key, item["first_seen"]),
                    )
                old = db.execute(
                    "SELECT state,result_ref,material_hint,input_revision,material_observed_at,checked_at,body_digest,work_kind FROM live_articles "
                    "WHERE article_key=?",
                    (key,),
                ).fetchone()
                origin = db.execute(
                    "SELECT material_hint FROM live_origins WHERE origin_key=?", (item["origin"],)
                ).fetchone()
                changed = bool(
                    old
                    and origin
                    and origin[0] != item["hint"]
                    and item["observed"] >= old["material_observed_at"]
                )
                rejected = bool(item.get("reason"))
                baseline = bool(item["bootstrap"])
                if not old:
                    from urllib.parse import urlsplit

                    db.execute(
                        "INSERT INTO live_articles(article_key,source_id,url,publisher,material_hint,"
                        "first_seen_at,input_ref,state,due_at,title,material_observed_at,work_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            key,
                            item["source_id"],
                            item["url"],
                            urlsplit(item["url"]).hostname or "",
                            item["hint"],
                            item["first_seen"],
                            item["ref"],
                            "NOT_APPLICABLE"
                            if (rejected or baseline) and not item.get("recheck")
                            else "FETCH_DUE",
                            now,
                            item["title"],
                            item["observed"],
                            item["kind"],
                        ),
                    )
                elif changed or (
                    old["state"] == "NOT_APPLICABLE"
                    and (item.get("recheck") or (not rejected and not baseline))
                ):
                    changed = True
                    db.execute(
                        "UPDATE live_articles SET material_hint=?,input_ref=?,input_revision=input_revision+1,"
                        "source_id=?,state='FETCH_DUE',due_at=?,attempt=0,first_fetch_at=NULL,title=?,attempts_json='[]',"
                        "work_kind='content_update',material_observed_at=? WHERE article_key=?",
                        (
                            item["hint"],
                            item["ref"],
                            item["source_id"],
                            now,
                            item["title"],
                            item["observed"],
                            key,
                        ),
                    )
                elif (
                    old["state"] == "READY"
                    and old["body_digest"]
                    and old["checked_at"]
                    and (
                        datetime.fromisoformat(item["observed"])
                        - datetime.fromisoformat(old["checked_at"])
                        >= timedelta(minutes=30)
                    )
                ):
                    # This is a durable re-entry/TAIL delta, not every unchanged
                    # poll. Check once in the background; existing FULL stays readable.
                    db.execute(
                        "UPDATE live_articles SET state='FETCH_DUE',due_at=?,attempt=0,first_fetch_at=NULL,"
                        "work_kind='recheck',attempts_json='[]',next_check_at=NULL WHERE article_key=?",
                        (now, key),
                    )
                if (not old or changed) and item["refs"]:
                    self._refs(db, "live_articles_input", key, item["refs"])
                if not origin:
                    db.execute(
                        "INSERT INTO live_origins VALUES(?,?,?,?,?,?,?)",
                        (
                            item["origin"],
                            key,
                            item["source_id"],
                            item["unit"],
                            item["hint"],
                            item["observed"],
                            canonical_json(sorted({t[1] for t in item["targets"]})),
                        ),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO live_origins VALUES(?,?,?,?,?,?,?)",
                        (
                            "canonical:" + key,
                            key,
                            "resolved",
                            item["unit"],
                            item["hint"],
                            item["observed"],
                            "[]",
                        ),
                    )
                elif changed:
                    db.execute(
                        "UPDATE live_origins SET material_hint=? WHERE origin_key=?",
                        (item["hint"], item["origin"]),
                    )
                if origin:
                    previous = db.execute(
                        "SELECT targets_json FROM live_origins WHERE origin_key=?",
                        (item["origin"],),
                    ).fetchone()[0]
                    targets = canonical_json(
                        sorted(set(json.loads(previous)) | {t[1] for t in item["targets"]})
                    )
                    if targets != previous:
                        db.execute(
                            "UPDATE live_origins SET targets_json=? WHERE origin_key=?",
                            (targets, item["origin"]),
                        )
                for target in item["targets"]:
                    # The payload holds references, not an immutable roster model.
                    binding_id, ticker, terms_revision = target
                    db.execute(
                        "INSERT INTO live_deliveries(article_key,ticker,source_id,binding_id,"
                        "terms_revision,admission_json,bootstrap,state,due_at,created_at) "
                        "VALUES(?,?,?,?,?,?,?, ?,?,?) ON CONFLICT(article_key,ticker) DO UPDATE SET "
                        "source_id=excluded.source_id,binding_id=excluded.binding_id,terms_revision=excluded.terms_revision,"
                        "state=CASE WHEN state IN ('L2_DUE','CANCELLED','CONFIG_INCOMPLETE','ADMISSION_REJECTED') THEN excluded.state ELSE state END "
                        "WHERE raw_id IS NULL AND source_id!=excluded.source_id AND (? OR NOT EXISTS("
                        "SELECT 1 FROM ticker_source_bindings b WHERE b.binding_id=live_deliveries.binding_id "
                        "AND b.enabled=1 AND b.tombstoned_at IS NULL))",
                        (
                            key,
                            ticker,
                            item["source_id"],
                            binding_id,
                            terms_revision,
                            item["admission"],
                            int(item["bootstrap"]),
                            "ADMISSION_REJECTED"
                            if rejected
                            else "SUPPRESSED"
                            if baseline
                            else "PENDING"
                            if old
                            and old["result_ref"]
                            and not changed
                            and not (
                                old["state"] in {"FETCH_DUE", "FETCHING"}
                                and old["work_kind"] == "content_update"
                            )
                            else "WAIT_BODY",
                            now,
                            now,
                            int(item["direct"]),
                        ),
                    )
                keys.append("live:" + key)
            if capture_id:
                db.execute("INSERT INTO collection_acceptances VALUES(?,?)", (capture_id, now))
        return keys

    def claim(self, *, limit, background=False):
        now = utc_now()
        stamp = now.isoformat()
        recent = (now - timedelta(minutes=30)).isoformat()
        claimed = []
        with self.bus._connect() as db:
            if background:
                limit = min(limit, 1)
                if db.execute(
                    "SELECT 1 FROM live_articles WHERE state='FETCHING' AND work_kind='recheck' "
                    "AND lease_until>? LIMIT 1",
                    (stamp,),
                ).fetchone():
                    return []
            active = {
                r[0]: r[1]
                for r in db.execute(
                    "SELECT publisher,count(*) FROM live_articles "
                    "WHERE state='FETCHING' AND lease_until>? GROUP BY publisher",
                    (stamp,),
                )
            }
            publishers = db.execute(
                "SELECT publisher,min(due_at) first_due,max(first_seen_at) newest,max(first_seen_at>=? OR work_kind='content_update') urgent FROM live_articles "
                "WHERE state='FETCH_DUE' AND due_at<=? AND (work_kind='recheck')=? "
                "GROUP BY publisher ORDER BY urgent DESC,newest DESC,first_due LIMIT 64",
                (recent, stamp, int(background)),
            ).fetchall()
            rows = []
            for publisher in publishers:
                available = min(limit - len(rows), max(0, 2 - active.get(publisher[0], 0)))
                if available:
                    rows += db.execute(
                        "SELECT article_key,input_revision FROM live_articles WHERE state='FETCH_DUE' "
                        "AND publisher=? AND due_at<=? AND (work_kind='recheck')=? "
                        "ORDER BY (first_seen_at>=? OR work_kind='content_update') DESC,first_seen_at DESC,due_at LIMIT ?",
                        (publisher[0], stamp, int(background), recent, available),
                    ).fetchall()
                if len(rows) >= limit:
                    break
            if len(rows) < limit:
                rows += db.execute(
                    "SELECT article_key,input_revision FROM live_articles WHERE state='FETCHING' "
                    "AND lease_until<=? ORDER BY lease_until LIMIT ?",
                    (stamp, limit - len(rows)),
                ).fetchall()
        if not rows:
            return []
        with self.bus.transaction() as db:
            for row in rows:
                token = new_id("live-fetch")
                updated = db.execute(
                    "UPDATE live_articles SET state='FETCHING',claim_token=?,lease_until=?,"
                    "attempt=attempt+1,first_fetch_at=coalesce(first_fetch_at,?) WHERE article_key=? "
                    "AND ((state='FETCH_DUE' AND due_at<=?) OR (state='FETCHING' AND lease_until<=?)) "
                    "RETURNING input_revision",
                    (token, (now + timedelta(seconds=60)).isoformat(), stamp, row[0], stamp, stamp),
                ).fetchone()
                if updated:
                    claimed.append((row[0], updated[0], token))
        return claimed

    def get(self, key):
        with self.bus._connect() as db:
            row = db.execute("SELECT * FROM live_articles WHERE article_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def adopt_first_seen(self, key, observed):
        with self.bus.transaction() as db:
            db.execute(
                "UPDATE live_articles SET first_seen_at=? WHERE article_key=? AND first_seen_at>?",
                (observed, key, observed),
            )

    def renew(self, key, token):
        with self.bus.transaction() as db:
            if not db.execute(
                "UPDATE live_articles SET lease_until=? WHERE article_key=? "
                "AND claim_token=? AND state='FETCHING'",
                ((utc_now() + timedelta(seconds=60)).isoformat(), key, token),
            ).rowcount:
                raise RuntimeError("enrichment_lease_lost")

    def defer(self, job):
        key = job.article_id
        attempts = [
            {
                k: a.get(k)
                for k in ("phase", "reason", "latency_ms", "status_code", "retry_after_seconds")
                if a.get(k) is not None
            }
            for a in job.prior_attempts[-4:]
        ]
        with self.bus.transaction() as db:
            db.execute(
                "UPDATE live_articles SET state='FETCH_DUE',due_at=?,claim_token=NULL,lease_until=NULL,"
                "last_error=?,attempt=?,attempts_json=? WHERE article_key=? AND claim_token=?",
                (
                    job.not_before.isoformat(),
                    "awaiting_capability" if job.attempt_count == 0 else "retry",
                    job.attempt_count,
                    canonical_json(attempts),
                    key,
                    job.claim_token,
                ),
            )

    def prepare_result(self, job, message, *, outcome=None):
        meta = message.metadata.get("media_enrichment", {})
        native_only = job.source.content_enrichment_mode.value == "skip" or message.metadata.get(
            "body_not_applicable"
        )
        success = bool(
            native_only or (meta.get("succeeded") and meta.get("outcome") in {"FULL", "SHORT_FULL"})
        )
        from .deduplication import text

        # Publication revisions include material title corrections as well as
        # body changes; a body-only digest would silently lose headline fixes.
        digest = (
            sha256_text(canonical_json([text(message.title), text(message.body or "")]))
            if success
            else None
        )
        previous = self.get(job.article_id)
        reuse_ref = bool(
            previous
            and previous["result_ref"]
            and previous["body_digest"]
            and (not success or digest == previous["body_digest"])
        )
        if reuse_ref:
            ref, refs = previous["result_ref"], set()
        else:
            ref, refs = self.put(message.model_dump(mode="json"))
        value = dict(
            key=job.article_id,
            token=job.claim_token,
            input_revision=job.provider_raw_hash,
            ref=ref,
            refs=sorted(refs),
            success=success,
            outcome=meta.get("outcome", "UNAVAILABLE"),
            error=meta.get("reason_code") or meta.get("reason"),
            digest=digest,
            reuse_ref=reuse_ref,
            validators=meta.get("validators", {}),
            canonical=article_key(message, job.source.source_id),
            url=message.url,
            acquired=meta.get("body_acquired_at") or utc_now().isoformat(),
            checked=utc_now().isoformat(),
        )
        completion = message.metadata.get("v2_body_completion", {})
        value.update(
            result_id=job.claim_token,
            network=bool(completion.get("network_attempted")),
            reused=bool(completion.get("reused_from_execution_id")),
        )
        if outcome and value["network"]:
            value["site_outcome"] = outcome
        if job.source.content_enrichment_mode.value == "skip" or message.metadata.get(
            "body_not_applicable"
        ):
            value["outcome"] = "NOT_APPLICABLE"
        self.spool.mkdir(parents=True, exist_ok=True)
        path = self.spool / f"{job.article_id}.{job.claim_token}.json"
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        value["spool_path"] = str(path)
        return value

    def commit_result(self, value, *, token=None):
        key = value["key"]
        now = utc_now()
        stamp = now.isoformat()
        with self.bus.transaction() as db:
            current = db.execute(
                "SELECT input_revision,claim_token,body_digest,result_ref,first_seen_at,last_result_id,state,acquired_at,work_kind "
                "FROM live_articles WHERE article_key=?",
                (key,),
            ).fetchone()
            if not current or str(current[0]) != str(value["input_revision"]):
                return False
            if current[5] == value["result_id"]:
                return True
            if current[1] != (token or value["token"]):
                raise RuntimeError("enrichment_lease_lost")
            alias = db.execute(
                "SELECT article_key FROM live_origins WHERE origin_key=?",
                ("canonical:" + value["canonical"],),
            ).fetchone()
            preserve_owner = False
            if value["success"] and alias and alias[0] != key:
                target = alias[0]
                columns = (
                    "ticker,source_id,binding_id,terms_revision,admission_json,bootstrap,state,due_at,"
                    "claim_token,lease_until,decision_digest,published_digest,regex_result,jev_result,last_error,"
                    "raw_id,standard_id,consumable_at,created_at"
                )
                db.execute(
                    "INSERT OR IGNORE INTO live_deliveries(article_key,"
                    + columns
                    + ") SELECT ?,"
                    + columns
                    + " FROM live_deliveries WHERE article_key=?",
                    (target, key),
                )
                db.execute("DELETE FROM live_deliveries WHERE article_key=?", (key,))
                db.execute(
                    "UPDATE live_origins SET article_key=? WHERE article_key=?", (target, key)
                )
                db.execute(
                    "UPDATE live_articles SET state='MERGED',outcome='NOT_APPLICABLE',claim_token=NULL,"
                    "lease_until=NULL,next_check_at=NULL,last_error=?,last_result_id=? WHERE article_key=?",
                    (target, value["result_id"], key),
                )
                db.execute(
                    "UPDATE live_articles SET first_seen_at=min(first_seen_at,?) WHERE article_key=?",
                    (current[4], target),
                )
                db.execute(
                    "UPDATE live_articles SET last_error=? WHERE state='MERGED' AND last_error=?",
                    (target, key),
                )
                key = target
                current = db.execute(
                    "SELECT input_revision,claim_token,body_digest,result_ref,first_seen_at,last_result_id,state,acquired_at,work_kind "
                    "FROM live_articles WHERE article_key=?",
                    (key,),
                ).fetchone()
                preserve_owner = current["state"] == "FETCHING" or (
                    current["state"] == "FETCH_DUE" and current["work_kind"] == "content_update"
                )
            # Failure cannot replace an already complete body with a summary.
            usable = not (preserve_owner and current["work_kind"] == "content_update") and (
                (
                    value["success"]
                    and not (current["acquired_at"] and current["acquired_at"] > value["acquired"])
                )
                or not current[3]
            )
            if usable and not value.get("reuse_ref"):
                self._refs(db, "live_articles_result", key, value["refs"])
            changed = bool(usable and value["success"] and value["digest"] != current[2])
            check = (
                (now + timedelta(minutes=30)).isoformat()
                if (
                    now - datetime.fromisoformat(current[4]) < timedelta(hours=24)
                    and value["outcome"] != "NOT_APPLICABLE"
                )
                else None
            )
            db.execute(
                "UPDATE live_articles SET state=CASE WHEN ? THEN state ELSE 'READY' END,"
                "claim_token=CASE WHEN ? THEN claim_token ELSE NULL END,lease_until=CASE WHEN ? THEN lease_until ELSE NULL END,"
                "result_ref=CASE WHEN ? THEN ? ELSE result_ref END,outcome=CASE WHEN ? THEN ? ELSE outcome END,"
                "body_digest=CASE WHEN ? THEN coalesce(?,body_digest) ELSE body_digest END,acquired_at=CASE WHEN ? THEN ? ELSE acquired_at END,"
                "checked_at=?,revision=revision+?,last_error=?,next_check_at=?,url=CASE WHEN ? THEN ? ELSE url END,last_result_id=?,"
                "fetch_total=fetch_total+?,full_total=full_total+?,cache_total=cache_total+?,"
                "validators_json=CASE WHEN ? THEN ? ELSE validators_json END WHERE article_key=?",
                (
                    int(preserve_owner),
                    int(preserve_owner),
                    int(preserve_owner),
                    int(usable),
                    value["ref"],
                    int(usable),
                    value["outcome"],
                    int(usable),
                    value["digest"],
                    int(changed),
                    value["acquired"],
                    stamp,
                    int(changed),
                    value["error"],
                    check,
                    int(value["success"] and usable),
                    value["url"],
                    value["result_id"],
                    int(value["network"]),
                    int(value["success"] and value["network"]),
                    int(value["reused"]),
                    int(value["success"] and usable),
                    canonical_json(value.get("validators", {})),
                    key,
                ),
            )
            if value["success"]:
                db.execute(
                    "INSERT OR IGNORE INTO live_origins VALUES(?,?,?,?,?,?,?)",
                    (
                        "canonical:" + value["canonical"],
                        key,
                        "resolved",
                        "canonical",
                        "",
                        current[4],
                        "[]",
                    ),
                )
            db.execute(
                "UPDATE live_deliveries SET state='PENDING',due_at=? WHERE article_key=? "
                "AND ? AND (state='WAIT_BODY' OR (? AND state IN ('PUBLISHED','IRRELEVANT','NO_PUBLIC_BODY')))",
                (stamp, key, int(not preserve_owner or usable), int(changed)),
            )
            if value.get("site_outcome"):
                payload = value["site_outcome"]
                db.execute(
                    "INSERT OR IGNORE INTO site_strategy_result_outbox(outbox_id,kind,status,attempts,next_attempt_at,created_at,payload_json) VALUES(?,'body','PENDING',0,?,?,?)",
                    (payload["execution_id"], stamp, stamp, canonical_json(payload)),
                )
        return True

    def pending_spool(self, key):
        if not self.spool.exists():
            return []
        from itertools import islice

        return [
            json.loads(p.read_text(encoding="utf-8")) | {"spool_path": str(p)}
            for p in islice(self.spool.glob(key + ".*.json" if key else "*.json"), 32)
        ]

    def retire_acceptances(self):
        cutoff = (utc_now() - timedelta(hours=48)).isoformat()
        with self.bus._connect() as db:
            keys = [
                r[0]
                for r in db.execute(
                    "SELECT capture_id FROM collection_acceptances WHERE accepted_at<? "
                    "ORDER BY accepted_at LIMIT 128",
                    (cutoff,),
                )
            ]
        if not keys:
            return 0
        with self.bus.transaction() as db:
            db.executemany(
                "DELETE FROM collection_acceptances WHERE capture_id=?", [(k,) for k in keys]
            )
        return len(keys)

    def enqueue_checks(self, *, limit=4):
        now = utc_now()
        stamp = now.isoformat()
        cutoff = (now - timedelta(hours=24)).isoformat()
        with self.bus._connect() as db:
            rows = db.execute(
                "SELECT article_key FROM live_articles WHERE state='READY' "
                "AND next_check_at<=? AND first_seen_at>=? ORDER BY next_check_at LIMIT ?",
                (stamp, cutoff, limit),
            ).fetchall()
            expired = db.execute(
                "SELECT article_key FROM live_articles WHERE next_check_at<=? "
                "AND first_seen_at<? ORDER BY next_check_at LIMIT 128",
                (stamp, cutoff),
            ).fetchall()
        if not rows and not expired:
            return 0
        with self.bus.transaction() as db:
            db.executemany(
                "UPDATE live_articles SET next_check_at=NULL WHERE article_key=?",
                [(r[0],) for r in expired],
            )
            for row in rows:
                db.execute(
                    "UPDATE live_articles SET state='FETCH_DUE',due_at=?,attempt=0,"
                    "first_fetch_at=NULL,work_kind='recheck',attempts_json='[]',next_check_at=NULL WHERE article_key=? AND state='READY'",
                    (stamp, row[0]),
                )
        return len(rows)

    def deliveries(self, state="PENDING", *, limit=32, old_only=False):
        now = utc_now()
        recent = (now - timedelta(minutes=30)).isoformat()
        with self.bus._connect() as db:
            rows = db.execute(
                "SELECT d.*,a.result_ref,a.input_ref,a.body_digest,a.revision,a.first_seen_at,"
                "(a.first_seen_at>=? OR a.work_kind='content_update') foreground "
                "FROM live_deliveries d JOIN live_articles a USING(article_key) "
                "WHERE d.state=? AND d.due_at<=? AND a.result_ref IS NOT NULL "
                + (
                    "ORDER BY d.due_at,d.article_key LIMIT ?"
                    if old_only
                    else "ORDER BY foreground DESC,a.first_seen_at DESC,d.due_at,d.article_key LIMIT ?"
                ),
                (recent, state, now.isoformat(), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def refresh_decisions(self, *, restarted=False):
        """Configuration repair resumes local decisions, never BODY downloads."""
        with self.bus.transaction() as db:
            return db.execute(
                "UPDATE live_deliveries SET state='PENDING',due_at=?,last_error=NULL "
                "WHERE rowid IN (SELECT d.rowid FROM live_deliveries d "
                "JOIN ticker_source_bindings b ON b.binding_id=d.binding_id "
                "LEFT JOIN ticker_monitoring_terms t ON t.ticker=d.ticker "
                "JOIN live_articles a ON a.article_key=d.article_key "
                "WHERE d.state IN ('CONFIG_INCOMPLETE','CANCELLED') AND b.enabled=1 AND b.tombstoned_at IS NULL "
                "AND a.result_ref IS NOT NULL AND (? OR d.state='CANCELLED' OR t.current_revision!=d.terms_revision) LIMIT 2048)",
                (utc_now().isoformat(), int(restarted)),
            ).rowcount

    def decision(self, row, state, *, regex=None, score=None, error=None, retry=False):
        with self.bus.transaction() as db:
            changed = db.execute(
                "UPDATE live_deliveries SET state=?,regex_result=?,jev_result=?,last_error=?,"
                "decision_digest=?,due_at=?,terms_revision=? WHERE article_key=? AND ticker=? AND binding_id=? "
                "AND EXISTS(SELECT 1 FROM live_articles a WHERE a.article_key=live_deliveries.article_key "
                "AND a.revision=? AND a.result_ref=?) AND state IN ('PENDING','WAIT_BODY','L2_DUE') "
                "AND NOT EXISTS(SELECT 1 FROM ticker_monitoring_terms t WHERE t.ticker=live_deliveries.ticker "
                "AND t.current_revision!=?)",
                (
                    state,
                    regex,
                    score,
                    error,
                    row["body_digest"],
                    (utc_now() + timedelta(seconds=5 if retry else 0)).isoformat(),
                    row["terms_revision"],
                    row["article_key"],
                    row["ticker"],
                    row["binding_id"],
                    row["revision"],
                    row["result_ref"],
                    row["terms_revision"],
                ),
            ).rowcount
        if changed:
            row.update(regex_result=regex, jev_result=score, decision_digest=row["body_digest"])
        return bool(changed)

    def pending_ids(self, ids):
        requests = [
            (v, *v.removeprefix("live:").partition(":")[::2]) for v in ids if v.startswith("live:")
        ]
        keys = [r[1] for r in requests]
        if not keys:
            return []
        with self.bus._connect() as db:
            aliases = dict(
                db.execute(
                    "SELECT article_key,last_error FROM live_articles WHERE state='MERGED' AND article_key IN "
                    "(SELECT value FROM json_each(?))",
                    (json.dumps(keys),),
                )
            )
            keys = [aliases.get(k, k) for k in keys]
            body = {
                r[0]
                for r in db.execute(
                    "SELECT article_key FROM live_articles WHERE article_key IN "
                    "(SELECT value FROM json_each(?)) AND state IN ('FETCH_DUE','FETCHING','COMMIT_DUE')",
                    (json.dumps(keys),),
                )
            }
            deliveries = list(
                db.execute(
                    "SELECT article_key,ticker FROM live_deliveries WHERE article_key IN "
                    "(SELECT value FROM json_each(?)) AND state IN ('WAIT_BODY','PENDING','L2_DUE')",
                    (json.dumps(keys),),
                )
            )
        pending = set(map(tuple, deliveries))
        any_delivery = {r[0] for r in deliveries}
        return [
            identity
            for identity, key, ticker in requests
            if (target := aliases.get(key, key)) in body
            or ((target, ticker) in pending if ticker else target in any_delivery)
        ]

    def failures_for_work(self, ids):
        """Terminal failures remain in CLOSED_SWEEP coverage, never vanish as settled work."""
        requests = [
            (v, *v.removeprefix("live:").partition(":")[::2]) for v in ids if v.startswith("live:")
        ]
        if not requests:
            return []
        with self.bus._connect() as db:
            aliases = dict(
                db.execute(
                    "SELECT article_key,last_error FROM live_articles WHERE state='MERGED' "
                    "AND article_key IN (SELECT value FROM json_each(?))",
                    (json.dumps([r[1] for r in requests]),),
                )
            )
            rows = {
                (r[0], r[1])
                for r in db.execute(
                    "SELECT article_key,ticker FROM live_deliveries WHERE article_key IN "
                    "(SELECT value FROM json_each(?)) AND state IN ('CONFIG_INCOMPLETE','NO_PUBLIC_BODY','FAILED')",
                    (json.dumps([aliases.get(r[1], r[1]) for r in requests]),),
                )
            }
        return [
            v
            for v, key, ticker in requests
            if (
                (aliases.get(key, key), ticker) in rows
                if ticker
                else any(k == aliases.get(key, key) for k, _ in rows)
            )
        ]

    def reserve_resolved(self, job, final_message):
        """One canonical reservation before downloading; join an existing owner."""
        canonical = article_key(final_message, job.source.source_id)
        key = job.article_id
        stamp = utc_now().isoformat()
        with self.bus.transaction() as db:
            current = db.execute(
                "SELECT claim_token,first_seen_at FROM live_articles WHERE article_key=?", (key,)
            ).fetchone()
            if not current or current[0] != job.claim_token:
                raise RuntimeError("enrichment_lease_lost")
            alias = db.execute(
                "SELECT article_key FROM live_origins WHERE origin_key=?",
                ("canonical:" + canonical,),
            ).fetchone()
            target = alias[0] if alias else canonical
            exists = db.execute(
                "SELECT result_ref FROM live_articles WHERE article_key=?", (target,)
            ).fetchone()
            if not exists or target == key:
                db.execute(
                    "INSERT OR IGNORE INTO live_origins VALUES(?,?,'resolved','canonical','',?,'[]')",
                    ("canonical:" + canonical, key, current[1]),
                )
                return False
            columns = (
                "ticker,source_id,binding_id,terms_revision,admission_json,bootstrap,state,due_at,"
                "claim_token,lease_until,decision_digest,published_digest,regex_result,jev_result,last_error,"
                "raw_id,standard_id,consumable_at,created_at"
            )
            db.execute(
                "INSERT OR IGNORE INTO live_deliveries(article_key,"
                + columns
                + ") SELECT ?,"
                + columns
                + " FROM live_deliveries WHERE article_key=?",
                (target, key),
            )
            db.execute("DELETE FROM live_deliveries WHERE article_key=?", (key,))
            db.execute("UPDATE live_origins SET article_key=? WHERE article_key=?", (target, key))
            db.execute(
                "INSERT OR IGNORE INTO live_origins VALUES(?,?,'resolved','canonical','',?,'[]')",
                ("canonical:" + key, target, current[1]),
            )
            db.execute(
                "UPDATE live_articles SET state='MERGED',outcome='NOT_APPLICABLE',claim_token=NULL,"
                "lease_until=NULL,next_check_at=NULL,last_error=? WHERE article_key=?",
                (target, key),
            )
            db.execute(
                "UPDATE live_articles SET last_error=? WHERE state='MERGED' AND last_error=?",
                (target, key),
            )
            db.execute(
                "UPDATE live_articles SET first_seen_at=min(first_seen_at,?) WHERE article_key=?",
                (current[1], target),
            )
            if exists[0]:
                db.execute(
                    "UPDATE live_deliveries SET state='PENDING',due_at=? WHERE article_key=? AND state='WAIT_BODY'",
                    (stamp, target),
                )
        return True

    def prepare_publication(self, bus, row, source, binding, message):
        """All models, full reads and Native publication happen before BEGIN."""
        from .schema import RawProcessingStatus, StreamItem, StreamMember, IngestResult
        from doxagent.v2_control.mirror import state_in

        key, ticker = row["article_key"], row["ticker"]
        known = (
            self.bus.get_raw(row["raw_id"])
            if row["raw_id"]
            else self.bus.known_published_article(binding, message)
        )
        from .schema import RawMessageInput

        original_input = RawMessageInput.model_validate(self.load(row["input_ref"]))
        original = message.model_copy(
            update={
                "admission_context": AdmissionContext.model_validate_json(row["admission_json"])
            }
        )
        raw = bus.prepare_raw_message(
            source=source,
            binding=binding,
            message=original,
            bootstrap=bool(row["bootstrap"]),
            collected_at=datetime.fromisoformat(row["first_seen_at"]),
            trusted_enrichment=True,
            enrichment_input=original_input,
            first_seen_resolved=True,
            published_article_update=bool(known),
        )
        if isinstance(raw, IngestResult):
            return {"rejected": raw.error_code, "row": row}
        raw_id = (
            known.raw_message_id if known else "raw_live_" + sha256_text(key + ":" + ticker)[:32]
        )
        standard = self.bus.get_standard_for_raw(raw_id) if known else None
        standard_id = (
            standard.standard_message_id
            if standard
            else "std_live_" + sha256_text(key + ":" + ticker)[:32]
        )
        old_version = known.metadata.get("message_version", {}) if known else {}
        from .deduplication import text

        same = bool(
            known and text(known.body) == text(raw.body) and text(known.title) == text(raw.title)
        )
        revision = int(old_version.get("content_revision", 1)) + (0 if same else 1) if known else 1
        logical_id = old_version.get("logical_message_id") or "live:" + key
        metadata = {
            **(known.metadata if known else {}),
            **raw.metadata,
            "live_article_key": key,
            "article_body_result_key": key,
            "message_version": {
                **old_version,
                "logical_message_id": logical_id,
                "publication_policy": "ARTICLE_ONCE",
                "business_version": old_version.get("business_version", 1),
                "content_revision": revision,
                "classification": "BUSINESS_UPDATE" if known else "NEW_ARTICLE",
            },
            "body_update_state": {"last_checked_at": utc_now().isoformat(), "updated": bool(known)},
            "pipeline_trace": {
                **original.metadata.get("pipeline_trace", {}),
                "decision_ready_at": utc_now().isoformat(),
            },
        }
        if source.acquisition_mode.value == "by_distribution":
            metadata["distribution"] = {
                "decision_id": f"live:{key}:{ticker}:{row['revision']}:{row['terms_revision']}",
                "terms_revision": row["terms_revision"],
                "regex_hit": bool(row.get("regex_result")),
                "jev_result": row.get("jev_result"),
            }
        with self.bus._connect() as db:
            origin = state_in(db, ticker)
            consumed = (
                db.execute(
                    "SELECT i.published_at FROM stream_members m JOIN stream_items i USING(stream_item_id) "
                    "WHERE m.standard_message_id=? ORDER BY i.published_at LIMIT 1",
                    (standard_id,),
                ).fetchone()
                if known
                else None
            )
        metadata.pop("v2_control_origin", None)
        if known and known.metadata.get("v2_control_origin"):
            metadata["v2_control_origin"] = known.metadata["v2_control_origin"]
        elif origin:
            metadata["v2_control_origin"] = {
                "epoch": origin["epoch"],
                "mode": origin["mode"],
                "recorded_at": utc_now().isoformat(),
            }
        raw = raw.model_copy(
            update={
                **(
                    {
                        name: getattr(known, name)
                        for name in (
                            "source_id",
                            "binding_id",
                            "source_definition_version",
                            "source_item_key",
                            "identity_key",
                            "external_id",
                            "raw_payload",
                            "raw_hash",
                            "collected_at",
                            "published_at",
                            "bootstrap_suppressed",
                            "streaming_config",
                        )
                    }
                    if known
                    else {}
                ),
                "raw_message_id": raw_id,
                "metadata": metadata,
                "processing_status": RawProcessingStatus.COMPLETED,
                "first_seen_at": known.first_seen_at if known else raw.first_seen_at,
                "revision": known.revision if known else 1,
            }
        )
        standard = bus._standardize(raw).model_copy(update={"standard_message_id": standard_id})
        from .deduplication import aliases

        item_id = "stream_live_" + sha256_text(key + ":" + ticker)[:32]
        item = StreamItem(
            stream_item_id=item_id,
            ticker=ticker,
            stream_offset=1,
            publication_mode=binding.streaming.publication_mode,
            member_count=1,
        )
        member = StreamMember(
            stream_item_id=item_id, member_index=0, standard_message_id=standard_id
        )
        from doxagent.v2_read.source_lifecycle import native_hashes

        raw_json = self.bus._json(raw)
        standard_json = self.bus._json(standard)
        refs = set(native_hashes(raw_json)) | set(native_hashes(standard_json))
        return dict(
            row=row,
            raw=raw,
            standard=standard,
            raw_json=raw_json,
            standard_json=standard_json,
            refs=refs,
            item=item,
            item_json=self.bus._json(item),
            member_json=self.bus._json(member),
            known=bool(known),
            unchanged=same,
            known_consumable_at=consumed[0] if consumed else None,
            logical_id=logical_id,
            revision=revision,
            origin_epoch=origin["epoch"] if origin else None,
            binding_version=binding.version,
            aliases=aliases(raw),
        )

    def publish(self, prepared):
        from .schema import PublicationMode
        from doxagent.v2_control.mirror import state_in

        row = prepared["row"]
        key = row["article_key"]
        ticker = row["ticker"]
        with self.bus.transaction() as db:
            current = db.execute(
                "SELECT published_digest,raw_id,binding_id FROM live_deliveries WHERE article_key=? AND ticker=?",
                (key, ticker),
            ).fetchone()
            if not current:
                return
            if current[2] != row["binding_id"]:
                raise RuntimeError("live_publication_configuration_changed")
            article = db.execute(
                "SELECT revision,result_ref FROM live_articles WHERE article_key=?", (key,)
            ).fetchone()
            if not article or article[0] != row["revision"] or article[1] != row["result_ref"]:
                return
            terms = db.execute(
                "SELECT current_revision FROM ticker_monitoring_terms WHERE ticker=?", (ticker,)
            ).fetchone()
            if not prepared.get("known") and terms and terms[0] != row["terms_revision"]:
                raise RuntimeError("live_publication_terms_changed")
            if prepared.get("rejected"):
                db.execute(
                    "UPDATE live_deliveries SET state='ADMISSION_REJECTED',last_error=? "
                    "WHERE article_key=? AND ticker=?",
                    (prepared["rejected"], key, ticker),
                )
                return
            raw, standard, item = prepared["raw"], prepared["standard"], prepared["item"]
            if current[0] == row["body_digest"] and current[1]:
                db.execute(
                    "UPDATE live_deliveries SET state='PUBLISHED' WHERE article_key=? AND ticker=?",
                    (key, ticker),
                )
                return
            # A tiny current-binding/ownership check, not a fresh roster snapshot.
            active = db.execute(
                "SELECT version,enabled,tombstoned_at FROM ticker_source_bindings WHERE binding_id=?",
                (row["binding_id"],),
            ).fetchone()
            control = state_in(db, ticker)
            if not active or not active[1] or active[2]:
                db.execute(
                    "UPDATE live_deliveries SET state='CANCELLED' WHERE article_key=? AND ticker=?",
                    (key, ticker),
                )
                return
            if (control["epoch"] if control else None) != prepared["origin_epoch"]:
                raise RuntimeError("live_publication_configuration_changed")
            if prepared.get("unchanged"):
                db.execute(
                    "UPDATE live_deliveries SET state='PUBLISHED',raw_id=?,standard_id=?,decision_digest=?,"
                    "published_digest=?,consumable_at=coalesce(consumable_at,?),last_error=NULL WHERE article_key=? AND ticker=?",
                    (
                        raw.raw_message_id,
                        standard.standard_message_id,
                        row["body_digest"],
                        row["body_digest"],
                        prepared.get("known_consumable_at"),
                        key,
                        ticker,
                    ),
                )
                return
            self._refs(db, "live_publication", raw.raw_message_id, prepared["refs"])
            db.execute(
                "INSERT INTO raw_messages(raw_message_id,ticker,source_id,binding_id,identity_key,content_hash,"
                "raw_hash,revision,processing_status,published_at,collected_at,data_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(raw_message_id) DO UPDATE SET data_json=excluded.data_json,content_hash=excluded.content_hash,"
                "processing_status=excluded.processing_status",
                (
                    raw.raw_message_id,
                    ticker,
                    raw.source_id,
                    raw.binding_id,
                    raw.identity_key,
                    raw.content_hash,
                    raw.raw_hash,
                    raw.revision,
                    raw.processing_status.value,
                    raw.published_at.isoformat(),
                    raw.collected_at.isoformat(),
                    prepared["raw_json"],
                ),
            )
            db.execute(
                "INSERT INTO standard_messages VALUES(?,?,?,?,?,?,?) ON CONFLICT(raw_message_id) "
                "DO UPDATE SET data_json=excluded.data_json",
                (
                    standard.standard_message_id,
                    raw.raw_message_id,
                    ticker,
                    raw.source_id,
                    raw.binding_id,
                    raw.published_at.isoformat(),
                    prepared["standard_json"],
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO message_content_revisions VALUES(?,?,?,?)",
                (ticker, prepared["logical_id"], prepared["revision"], prepared["raw_json"]),
            )
            if not prepared["known"]:
                db.execute(
                    "INSERT OR IGNORE INTO logical_message_versions VALUES(?,?,?,?,?,?)",
                    (
                        ticker,
                        prepared["logical_id"],
                        1,
                        raw.raw_message_id,
                        row["body_digest"],
                        canonical_json(raw.metadata["message_version"]),
                    ),
                )
            for alias in prepared["aliases"]:
                db.execute(
                    "INSERT OR IGNORE INTO message_identity_aliases VALUES(?,?,?)",
                    (ticker, alias, prepared["logical_id"]),
                )
            published = db.execute(
                "SELECT 1 FROM stream_members WHERE standard_message_id=? LIMIT 1",
                (standard.standard_message_id,),
            ).fetchone()
            stamp = utc_now().isoformat()
            if not published and not raw.bootstrap_suppressed:
                if raw.streaming_config.publication_mode is PublicationMode.BUFFERED:
                    db.execute(
                        "INSERT INTO buffer_entries VALUES(?,?,?,?,?,?) ON CONFLICT(standard_message_id) "
                        "DO UPDATE SET data_json=excluded.data_json",
                        (
                            standard.standard_message_id,
                            raw.binding_id,
                            ticker,
                            raw.source_id,
                            stamp,
                            prepared["standard_json"],
                        ),
                    )
                else:
                    offset = db.execute(
                        "SELECT coalesce(max(stream_offset),0)+1 FROM stream_items WHERE ticker=?",
                        (ticker,),
                    ).fetchone()[0]
                    db.execute(
                        "INSERT INTO stream_items VALUES(?,?,?,?,?,json_set(?,'$.stream_offset',?,'$.published_at',?))",
                        (
                            item.stream_item_id,
                            ticker,
                            offset,
                            item.publication_mode.value,
                            stamp,
                            prepared["item_json"],
                            offset,
                            stamp,
                        ),
                    )
                    db.execute(
                        "INSERT INTO stream_members VALUES(?,0,?,?)",
                        (
                            item.stream_item_id,
                            standard.standard_message_id,
                            prepared["member_json"],
                        ),
                    )
                    published = True
            db.execute(
                "UPDATE live_deliveries SET state=?,raw_id=?,standard_id=?,decision_digest=?,"
                "published_digest=?,consumable_at=coalesce(consumable_at,?),last_error=NULL WHERE article_key=? AND ticker=?",
                (
                    "PUBLISHED"
                    if published
                    else "SUPPRESSED"
                    if raw.bootstrap_suppressed
                    else "BUFFERED",
                    raw.raw_message_id,
                    standard.standard_message_id,
                    row["body_digest"],
                    row["body_digest"],
                    (prepared.get("known_consumable_at") or stamp) if published else None,
                    key,
                    ticker,
                ),
            )
