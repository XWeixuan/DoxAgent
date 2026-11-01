"""Transfer Google discoveries into existing native article work, before BODY."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .schema import AuditRecord, UpdateActor, binding_id_for, canonical_json, sha256_text

NATIVE = {
    "barrons.com": "barrons_ticker_news",
    "marketwatch.com": "barrons_ticker_news",
    "reuters.com": "reuters_site_search",
    "investing.com": "investing_ticker_news",
}


def native_source_id(url):
    host = (urlsplit(url).hostname or "").lower()
    return next(
        (source for site, source in NATIVE.items() if host == site or host.endswith("." + site)),
        None,
    )


def canonical_url(url):
    if not native_source_id(url):
        return url
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        return url
    host = (parsed.hostname or "").lower()
    root = host.removeprefix("www.")
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
        and not (root in {"barrons.com", "marketwatch.com"} and k.lower() in {"mod", "siteid"})
    ]
    # The four native publishers' bare hosts are their www HTTPS article service.
    host = "www." + root if root in NATIVE and host in {root, "www." + root} else host
    return urlunsplit(("https", host, parsed.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


def prepare(live, job, url):
    source_id = native_source_id(url)
    if not source_id or job.source.source_id != "google_news_search_rss":
        return None
    discovered_url = url
    url = canonical_url(url)
    repo = live.bus
    source = repo.get_source(source_id)
    if not source or not source.enabled:
        return None
    with repo._connect() as db:
        deliveries = db.execute(
            "SELECT ticker,binding_id FROM live_deliveries WHERE article_key=?", (job.article_id,)
        ).fetchall()
    targets = []
    for ticker, _old_binding in deliveries:
        binding = repo.get_binding(binding_id_for(ticker, source_id))
        if not binding or not binding.enabled or binding.tombstoned_at:
            return None  # No new native subscription or shadow binding is created.
        targets.append((binding.binding_id, ticker, 0))
    if not targets:
        return None
    provenance = {
        "from_source_id": job.source.source_id,
        "to_source_id": source_id,
        "first_discovered_at": job.created_at.isoformat(),
        "wrapper_url": job.message.raw_url or job.message.url,
        "article_url": url,
        "discovered_url": discovered_url,
    }
    metadata = {
        **job.message.metadata,
        "google_handoff": provenance,
        "canonical_resolution": "resolved",
    }
    metadata.pop("body_access_scope", None)
    evidence = metadata.get("identity_evidence", {})
    metadata["identity_evidence"] = {
        **evidence,
        "url_kind": "article",
        "id_kind": "unknown",
        "verified_urls": list(
            dict.fromkeys([*evidence.get("verified_urls", []), job.message.url, url])
        ),
    }
    message = job.message.model_copy(
        update={
            "url": url,
            "raw_url": job.message.raw_url or job.message.url,
            "external_id": None,
            "source_item_key": url,
            "metadata": metadata,
        }
    )
    rows = live.prepare_input(
        source,
        targets,
        [message],
        observed_at=job.created_at.isoformat(),
        unit="google_handoff",
        bootstrap=job.bootstrap,
        admission=message.admission_context,
    )
    # This is a Google observation, not the native list's first observation.
    # Their different RSS/list summaries must not look like a native revision.
    for row in rows:
        row["origin"] = sha256_text(canonical_json(["google-handoff", job.article_id, url]))
    ref, refs = live.put(message.model_dump(mode="json"))
    return {
        "source": source,
        "message": message,
        "rows": rows,
        "provenance": provenance,
        "ref": ref,
        "refs": refs,
    }


def commit(live, job, prepared):
    if not prepared or not prepared["rows"]:
        return False
    repo = live.bus
    with repo.atomic_intake() as db:
        current = db.execute(
            "SELECT claim_token,source_id FROM live_articles WHERE article_key=?", (job.article_id,)
        ).fetchone()
        if not current or current[0] != job.claim_token or current[1] != "google_news_search_rss":
            raise RuntimeError("enrichment_lease_lost")
        # Native acceptance, canonical reservation and old-lease retirement are one commit.
        keys = live.accept(prepared["rows"])
        if not keys:
            return False
        target = keys[0].removeprefix("live:")
        if target == job.article_id:
            db.execute(
                "UPDATE live_articles SET source_id=?,input_ref=?,url=?,state='FETCH_DUE',"
                "claim_token=NULL,lease_until=NULL,attempt=max(0,attempt-1) WHERE article_key=?",
                (prepared["source"].source_id, prepared["ref"], prepared["message"].url, target),
            )
            live._refs(db, "live_articles_input", target, prepared["refs"])
        else:
            live.reserve_resolved(
                job.model_copy(update={"source": prepared["source"]}),
                prepared["message"],
                join_failure=True,
            )
        for binding_id, ticker, _ in prepared["rows"][0]["targets"]:
            db.execute(
                "UPDATE live_deliveries SET source_id=?,binding_id=? "
                "WHERE article_key=? AND ticker=? AND raw_id IS NULL",
                (prepared["source"].source_id, binding_id, target, ticker),
            )
        repo.save_audit(
            AuditRecord(
                entity_type="article",
                entity_id=target,
                action="GOOGLE_NATIVE_HANDOFF",
                actor=UpdateActor.SYSTEM,
                payload=prepared["provenance"],
            )
        )
    return True
