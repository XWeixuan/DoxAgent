"""Durable article downloads, independent of ticker admission/publication receipts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from . import deduplication as dedup
from .schema import canonical_json, sha256_text, utc_now


def article_key(message, source_id: str) -> str:
    evidence = message.metadata.get("identity_evidence", {})
    url = dedup.article_url(message.url, evidence.get("url_kind", "unknown"))
    # Only a provider-declared article identity can substitute for a verified URL.
    locator = url or (
        f"{source_id}:{message.external_id}"
        if evidence.get("id_kind") == "stable_article" and message.external_id
        else f"{source_id}:{message.external_id or message.source_item_key or message.url}"
    )
    scope = access_scope(message, source_id)
    language = message.metadata.get("language") or evidence.get("language") or ""
    return sha256_text(canonical_json([locator, scope, language]))


def access_scope(message, source_id):
    configured = message.metadata.get("body_access_scope")
    host = (urlsplit(message.url).hostname or "").lower()
    if configured:
        return configured
    if any(
        host == domain or host.endswith("." + domain)
        for domain in (
            "finnhub.io",
            "news.google.com",
            "barrons.com",
            "wsj.com",
            "reuters.com",
            "digitimes.com",
            "investing.com",
            "investorshub.advfn.com",
        )
    ):
        return "scope:source:" + source_id
    return "public"


def update_hint(message) -> str:
    fields = dedup.stable_fields(message)
    # Provider publication timestamps and tracking URLs are not content versions.
    fields.pop("published_at", None)
    fields.pop("article_locator", None)
    return sha256_text(canonical_json(fields))


class ArticleBodyResults:
    def __init__(self, repository):
        self.repository = repository

    def observe(self, message, source_id: str, *, now: datetime | None = None) -> dict:
        now = now or utc_now()
        key, hint = article_key(message, source_id), update_hint(message)
        with self.repository.transaction() as db:
            row = db.execute(
                "SELECT data_json FROM article_body_results WHERE fetch_key=?", (key,)
            ).fetchone()
            if row and not self.compatible(json.loads(row[0]), message, source_id):
                key = sha256_text(canonical_json([key, "scope:source:" + source_id]))
                row = db.execute(
                    "SELECT data_json FROM article_body_results WHERE fetch_key=?", (key,)
                ).fetchone()
            if not row and (
                url := dedup.article_url(
                    message.url,
                    message.metadata.get("identity_evidence", {}).get("url_kind", "unknown"),
                )
            ):
                row = db.execute(
                    "SELECT data_json FROM article_body_results WHERE json_extract(data_json,'$.final_canonical_url')=? AND json_extract(data_json,'$.access_scope')=? AND coalesce(json_extract(data_json,'$.language'),'')=? AND status='READY' LIMIT 1",
                    (
                        url,
                        access_scope(message, source_id),
                        message.metadata.get("language")
                        or message.metadata.get("identity_evidence", {}).get("language")
                        or "",
                    ),
                ).fetchone()
            if row:
                result = json.loads(row[0])
                # Do not rewrite on every list observation or manufacture CDC traffic.
                if result.get("pending_hint") != hint:
                    result["pending_hint"] = hint
                    self._save(db, result)
                return result
            result = {
                "fetch_key": key,
                "canonical_article_key": key,
                "publisher": urlsplit(message.url).hostname,
                "access_scope": access_scope(message, source_id),
                "language": message.metadata.get("language")
                or message.metadata.get("identity_evidence", {}).get("language")
                or "",
                "source_id": source_id,
                "url": message.url,
                "title": dedup.text(message.title),
                "first_observed_at": now.isoformat(),
                "status": "NOT_APPLICABLE"
                if message.metadata.get("body_not_applicable")
                else "PENDING",
                "eligible": not message.metadata.get("body_not_applicable", False),
                "pending_hint": hint,
                "check_seq": 0,
                "next_check_at": now.isoformat(),
            }
            self._save(db, result)
            return result

    def get(self, key: str) -> dict | None:
        with self.repository._connect() as db:
            row = db.execute(
                "SELECT data_json FROM article_body_results WHERE fetch_key=?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def adopt_legacy(self, message, source_id, ticker, *, now):
        """Lazily import one compatible, already verified FULL; never scan history."""
        with self.repository.atomic_intake() as db:
            result = self.observe(message, source_id, now=now)
            if result.get("content"):
                return result
            rows = db.execute(
                "SELECT data_json FROM raw_messages WHERE ticker=? AND json_extract(data_json,'$.url')=? ORDER BY collected_at DESC LIMIT 4",
                (ticker, message.url),
            ).fetchall()
            fields = dedup.stable_fields(message)
            fields.pop("published_at", None)
            for row in rows:
                old = json.loads(row[0])
                old_metadata = old.get("metadata", {})
                old_input = message.model_copy(update={"url": old["url"], "metadata": old_metadata})
                old_language = (
                    old_metadata.get("language")
                    or old_metadata.get("identity_evidence", {}).get("language")
                    or ""
                )
                if access_scope(old_input, old["source_id"]) != result[
                    "access_scope"
                ] or old_language != result.get("language", ""):
                    continue
                evidence = old_metadata.get("content_evidence", {})
                completion = old.get("metadata", {}).get("media_enrichment", {})
                if not (
                    evidence.get("kind") == "FULL"
                    or completion.get("succeeded")
                    and completion.get("outcome") in {"FULL", "SHORT_FULL"}
                ):
                    continue
                # Caller already proved its own observation compatible in observe_input.
                # A cross-provider canonical Raw may retain another provider's teaser.
                if fields.get("title") and fields["title"] != dedup.text(old.get("title")):
                    continue
                if not evidence.get("article_body"):
                    continue
                first = old.get("first_seen_at") or old["collected_at"]
                result.update(
                    content=evidence["article_body"],
                    content_hash=sha256_text(dedup.text(evidence["article_body"])),
                    outcome=completion.get("outcome") or "FULL",
                    status="READY",
                    verified_hint=update_hint(message),
                    execution_id="legacy:" + old["raw_message_id"],
                    acquired_at=old["collected_at"],
                    checked_at=now.isoformat(),
                    first_observed_at=min(first, result["first_observed_at"]),
                    legacy_result=True,
                    next_check_at=(now + timedelta(minutes=30)).isoformat(),
                    check_pending=False,
                    final_url=old["url"],
                    diagnostics={"outcome": completion.get("outcome") or "FULL"},
                )
                self._save(db, result)
                return result
            return result

    @staticmethod
    def compatible(result, message, source_id):
        scope = result.get("access_scope", "public")
        return (
            scope == access_scope(message, source_id)
            or scope == "scope:source:" + source_id
            or (
                scope == "public"
                and not message.metadata.get("body_access_scope")
                and urlsplit(message.url).hostname in {"finnhub.io", "news.google.com"}
            )
        )

    @staticmethod
    def reusable(result: dict, message, now: datetime | None = None) -> bool:
        source_id = message.metadata.get("body_observation_source_id")
        verified_hint = result.get("verified_hints", {}).get(source_id, result.get("verified_hint"))
        return bool(
            result.get("outcome") in {"FULL", "SHORT_FULL"}
            and ArticleBodyResults.compatible(result, message, source_id or result["source_id"])
            and result.get("content")
            and verified_hint == update_hint(message)
            and result.get("next_check_at", "") > (now or utc_now()).isoformat()
        )

    def schedule(self, message, source_id: str, *, now=None) -> tuple[dict, str]:
        """One sequence per due content check, not one per polling observation."""
        now = now or utc_now()
        with self.repository.atomic_intake() as db:
            result = self.observe(message, source_id, now=now)
            hint = update_hint(message)
            hints = dict(result.get("verified_hints", {}))
            if (
                result.get("content")
                and source_id not in hints
                and dedup.text(message.title) == result.get("title")
            ):
                hints[source_id] = hint
                result["verified_hints"] = hints
                self._save(db, result)
            comparable = message.model_copy(
                update={"metadata": {**message.metadata, "body_observation_source_id": source_id}}
            )
            if self.reusable(result, comparable, now):
                return result, "reuse"
            due = result.get("next_check_at", "") <= now.isoformat()
            if result.get("scheduled_hint") != hint or (due and not result.get("check_pending")):
                result["check_seq"] += 1
                result["scheduled_hint"] = hint
                result["check_pending"] = True
                self._save(db, result)
            return result, (
                "recheck"
                if result.get("content") and result.get("verified_hint") == hint
                else "content_update"
                if result.get("content")
                else "new_article"
            )

    def acquire(self, key: str, execution_id: str, *, now=None) -> bool:
        now = now or utc_now()
        with self.repository.transaction() as db:
            row = db.execute(
                "SELECT data_json FROM article_body_results WHERE fetch_key=?", (key,)
            ).fetchone()
            if not row:
                return False
            result = json.loads(row[0])
            if (
                result.get("fetch_owner")
                and result["fetch_owner"] != execution_id
                and result.get("lease_until", "") > now.isoformat()
            ):
                return False
            result.update(
                fetch_owner=execution_id, lease_until=(now + timedelta(seconds=210)).isoformat()
            )
            self._save(db, result)
            return True

    def prepare(self, key, execution_id, message):
        with self.repository.atomic_intake():
            cached = self.get(key)
            if cached and self.reusable(cached, message):
                return cached, True, False
            return cached, False, self.acquire(key, execution_id)

    def finish(self, key: str, execution_id: str, message, extracted, *, now=None) -> dict | None:
        now = now or utc_now()
        with self.repository.transaction() as db:
            row = db.execute(
                "SELECT data_json FROM article_body_results WHERE fetch_key=?", (key,)
            ).fetchone()
            if not row:
                return
            result = json.loads(row[0])
            if result.get("fetch_owner") != execution_id:
                return  # An expired claimant must not replace a newer result.
            outcome = extracted.diagnostics.get("outcome")
            if extracted.succeeded and outcome in {"FULL", "SHORT_FULL"}:
                content_hash = sha256_text(dedup.text(extracted.content))
                if result.get("content_hash") != content_hash:
                    result.update(
                        content=extracted.content,
                        content_hash=content_hash,
                        acquired_at=now.isoformat(),
                        execution_id=execution_id,
                    )
                final_message = message.model_copy(
                    update={"url": extracted.final_url or message.url}
                )
                final_scope = access_scope(
                    final_message,
                    message.metadata.get("body_observation_source_id", result["source_id"]),
                )
                if extracted.diagnostics.get("credential_ref") and final_scope == "public":
                    final_scope = "scope:source:" + message.metadata.get(
                        "body_observation_source_id", result["source_id"]
                    )
                result.update(
                    access_scope=final_scope,
                    final_canonical_url=dedup.article_url(
                        extracted.final_url or message.url, "article"
                    ),
                    status="READY",
                    outcome=outcome,
                    verified_hint=update_hint(message),
                    diagnostics=extracted.diagnostics,
                    final_url=extracted.final_url,
                    checked_at=now.isoformat(),
                    last_error=None,
                )
                result["validators"] = extracted.diagnostics.get("validators", {})
                result["verified_hints"] = {
                    **result.get("verified_hints", {}),
                    message.metadata.get(
                        "body_observation_source_id", result["source_id"]
                    ): update_hint(message),
                }
                result["title"] = dedup.text(message.title)
            else:
                result["last_error"] = extracted.reason
                if not result.get("content"):
                    result["status"] = "FAILED"
            result.update(
                fetch_owner=None,
                lease_until=None,
                check_pending=False,
                next_check_at=(
                    now + timedelta(seconds=1800 if extracted.succeeded else 60)
                ).isoformat(),
            )
            self._save(db, result)
            return result

    def release(self, key: str, execution_id: str) -> None:
        with self.repository.transaction() as db:
            result = self.get(key)
            if result and result.get("fetch_owner") == execution_id:
                result.update(fetch_owner=None, lease_until=None)
                self._save(db, result)

    def _save(self, db, result):
        db.execute(
            """INSERT INTO article_body_results(fetch_key,first_observed_at,status,next_check_at,data_json)
               VALUES(?,?,?,?,?) ON CONFLICT(fetch_key) DO UPDATE SET
               first_observed_at=min(article_body_results.first_observed_at,excluded.first_observed_at),
               status=excluded.status,next_check_at=excluded.next_check_at,data_json=excluded.data_json""",
            (
                result["fetch_key"],
                result["first_observed_at"],
                result["status"],
                result["next_check_at"],
                self.repository.content.encode(result),
            ),
        )
