"""Configure verified wire discovery and intentional RSS; preserve BODY and isolation."""

import argparse
import json
from pathlib import Path

from doxagent.message_bus_v2.newswire_listing import RECIPES, RSS_ONLY_SOURCES
from doxagent.message_bus_v2.schema import UpdateActor


def preserve_businesswire_owners(repository):
    """Reserve new provider identities against retained owners, without rewriting history."""
    from doxagent.message_bus_v2.body_results import article_key, update_hint
    from doxagent.message_bus_v2.newswire_listing import businesswire_item_key
    from doxagent.message_bus_v2.schema import RawMessageInput, utc_now, sha256_text, canonical_json

    with repository._connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_articles'").fetchone():
            return 0
        rows = db.execute("SELECT article_key,url,title,first_seen_at,delivery_mode FROM live_articles "
            "WHERE source_id='businesswire_massive' AND delivery_mode='test' AND state!='MERGED' "
            "ORDER BY CASE WHEN outcome IN ('FULL','SHORT_FULL') THEN 1 ELSE 0 END DESC,first_seen_at,article_key").fetchall()
    owners = {}
    for row in rows:
        identity = businesswire_item_key(row['url'])
        if not identity:
            continue
        language = identity.rsplit(':', 1)[-1]
        language = {'zh-hk':'zh-Hant', 'zh-cn':'zh-Hans'}.get(language, language)
        message = RawMessageInput(url=row['url'], title=row['title'], published_at=utc_now(), raw_payload={},
            metadata={'language':language, '_delivery_mode':row['delivery_mode'],
                      'identity_evidence':{'url_kind':'article'}})
        origin = sha256_text(canonical_json(['businesswire_massive:test', identity]))
        owners.setdefault(article_key(message, 'businesswire_massive'), (row, origin, update_hint(message)))
    with repository.transaction() as db:
        for canonical, (row, origin, hint) in owners.items():
            db.execute("INSERT OR IGNORE INTO live_origins VALUES(?,?,'resolved','canonical','',?,'[]')",
                ('canonical:'+canonical, row['article_key'], row['first_seen_at']))
            db.execute("INSERT OR IGNORE INTO live_origins VALUES(?,?,'businesswire_massive','WIRE:identity',?,?,'[]')",
                (origin, row['article_key'], hint, row['first_seen_at']))
    return len(owners)


def configure(service):
    preserved = preserve_businesswire_owners(service.repository)
    report = []
    with service.repository.atomic_intake():
        for sid, recipe in RECIPES.items():
            source = service.repository.get_source(sid)
            if source is None:
                report.append(dict(source_id=sid, action="absent"))
                continue
            if source.delivery_mode != "test":
                raise ValueError("expected existing isolated test source: " + sid)
            mode = "rss" if sid in RSS_ONLY_SOURCES else "http_listing"
            kind = "api" if mode == "rss" else "crawler"
            if source.default_parameters.get("discovery_mode") == mode and source.kind == kind:
                report.append(dict(source_id=sid, action="unchanged", version=source.version,
                                   **({'retained_body_identities':preserved} if sid == 'businesswire_massive' else {})))
                continue
            updated = service.update_source(sid, {
                "kind": kind,
                "parameter_schema": {**source.parameter_schema, "properties": {
                    **source.parameter_schema.get("properties", {}),
                    "discovery_mode": {"type": "string", "enum": ["http_listing", "rss"]}}},
                "default_parameters": {**source.default_parameters, "discovery_mode": mode},
            }, actor=UpdateActor.USER, reason="Verified low-cost wire discovery; intentional Newsfile RSS choice; BODY and test isolation unchanged")
            report.append(dict(source_id=sid, action="updated", version=updated.version,
                               discovery_mode=mode,
                               listing=source.default_parameters["endpoints"][0]["url"] if mode == "rss" else recipe["url"],
                               review=recipe.get("review") if mode == "http_listing" else None))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service

    repo = MessageBusV2Repository(args.bus, initialize=args.apply)
    try:
        result = configure(MessageBusV2Service(repo, enrichment_queue_enabled=True)) if args.apply else [
            dict(source_id=sid, version=repo.get_source(sid).version if repo.get_source(sid) else None, recipe=recipe)
            for sid, recipe in RECIPES.items()]
    finally:
        repo.close()
    Path(args.receipt).write_text(json.dumps(result, indent=2), encoding="utf8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
