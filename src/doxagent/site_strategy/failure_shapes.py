"""Bounded structural failure evidence. No DOM values, forms, cookies or query strings."""

from collections import Counter

from lxml import html
from lxml.etree import ParserError

from .governance_repository import safe_display_url
from .schema import digest_json, utc_now


def record_failure_shape(repository, request, resolved, identity_id, response, category, reason):
    if not response.body:
        return
    try:
        root = html.fromstring(response.body)
    except (ParserError, ValueError):
        return
    tags = Counter(node.tag.lower() for node in root.iter() if isinstance(node.tag, str))
    # The structural fingerprint contains no user-entered/hidden form values,
    # article text, full query string, authentication headers or generated IDs.
    shape = {
        "tags": dict(tags.most_common(32)),
        "status": response.status_code,
        "strategy": resolved.body.ref,
        "category": category.value,
        "reason": reason.split(":url=", 1)[0][:160],
    }
    now = utc_now()
    scope_id = resolved.match_evidence.get("scope_id") or f"publisher:{resolved.site_id}"
    fingerprint = digest_json([scope_id, identity_id, shape])
    import json

    payload = json.dumps({**shape, "url": safe_display_url(response.final_url)}, ensure_ascii=True)
    if len(payload.encode()) > 64 * 1024:
        return
    with repository.transaction() as connection:
        connection.execute(
            "insert into access_failure_shapes values(?,?,?,?,?,?,1) "
            "on conflict(fingerprint) do update set "
            "seen_count=seen_count+1,last_seen=excluded.last_seen",
            (fingerprint, scope_id, identity_id, payload, now.isoformat(), now.isoformat()),
        )
        overflow = connection.execute("select count(*)-8192 from access_failure_shapes").fetchone()[
            0
        ]
        if overflow > 0:
            connection.execute(
                "delete from access_failure_shapes where fingerprint in "
                "(select fingerprint from access_failure_shapes order by last_seen limit ?)",
                (min(overflow, 2000),),
            )
