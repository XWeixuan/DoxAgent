"""Narrow IR recipe upgrade; preserve ticker cadence and existing article owners."""
import argparse
import json
import os
from collections import Counter
from pathlib import Path

from doxagent.message_bus_v2.corporate_ir import SOURCE_ID
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.schema import RawMessageInput, UpdateActor, utc_now
from doxagent.settings import DoxAgentSettings
from scripts.ops.configure_corporate_ir_sources import configure, TICKERS


def planned(source, bindings):
    seeds = {s.endpoint_id: s for s in initial_endpoints()}
    rows = []
    for old in source.default_parameters['endpoints']:
        value = dict(old)
        if old['endpoint_id'] == 'snow.press':
            seed = seeds['snow.press'].model_dump(mode='json')
            value.update({k: seed[k] for k in ['entry_url', 'method', 'parser_ref', 'allowed_article_hosts']})
        if old['method'] == 'rss':
            from doxagent.message_bus_v2.rss_http_race import IR_RECIPES
            if old['endpoint_id'] not in IR_RECIPES:
                raise ValueError('RSS endpoint needs a verified HTTP recipe: '+old['endpoint_id'])
            value['parser_parameters'] = {**value.get('parser_parameters', {}), 'http_rss_race': True}
        if value != old:
            value['revision'] = old.get('revision', 1) + 1
        rows.append(value)
    if not any(s['endpoint_id'] == 'ionq.press' for s in rows):
        intervals = Counter(b.polling.target_interval_seconds for b in bindings if b.enabled)
        interval = intervals.most_common(1)[0][0] if intervals else 30
        rows.append(seeds['ionq.press'].model_copy(update={'target_interval_seconds': interval}).model_dump(mode='json'))
    return {**source.default_parameters, 'endpoints': rows}


def seed_onds_aliases(repo):
    from doxagent.message_bus_v2.live_repository import LiveRepository
    from doxagent.message_bus_v2.body_results import article_key
    live = LiveRepository(repo)
    with repo._connect() as db:
        rows = db.execute("SELECT article_key,source_id,url,input_ref,first_seen_at,outcome FROM live_articles "
                          "WHERE state IN ('READY','FETCH_DUE','FETCHING') AND publisher='ir.ondas.com' "
                          "ORDER BY CASE WHEN outcome IN ('FULL','SHORT_FULL') THEN 0 ELSE 1 END,first_seen_at").fetchall()
    changed = 0
    for row in rows:
        message = RawMessageInput.model_validate(live.load(row['input_ref'])).model_copy(update={'url': row['url']})
        key = article_key(message, row['source_id'])
        with repo.transaction() as db:
            changed += db.execute("INSERT OR IGNORE INTO live_origins VALUES(?,?,'resolved','ir-native-id','',?,'[]')",
                                  ('canonical:'+key, row['article_key'], row['first_seen_at'])).rowcount
    return changed


def upgrade(bus):
    repo = bus.repository
    with repo.atomic_intake():
        source = repo.get_source(SOURCE_ID)
        before = repo.list_bindings(source_id=SOURCE_ID)
        if not set(TICKERS) <= {s.ticker for s in repo.list_ticker_states()}:
            raise ValueError('IR tickers must already be registered')
        aliases = seed_onds_aliases(repo)
        parameters = planned(source, before)
        if parameters != source.default_parameters:
            bus.update_source(SOURCE_ID, {'default_parameters': parameters}, actor=UpdateActor.USER,
                              reason='IONQ HTTP, Snowflake public AEM, Ondas HTTP/RSS parallel')
        configure(bus)
        after = repo.list_bindings(source_id=SOURCE_ID)
        by_id = {b.binding_id: b for b in after}
        assert all(by_id[b.binding_id].polling == b.polling for b in before)
        return dict(at=utc_now().isoformat(), source_version=repo.get_source(SOURCE_ID).version,
                    aliases_seeded=aliases, endpoint_count=len(parameters['endpoints']),
                    bindings=[{'ticker': b.ticker, 'enabled': b.enabled,
                               'interval': b.polling.target_interval_seconds,
                               'endpoint_ids': b.source_parameters['endpoint_ids']} for b in after])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--directory', type=Path, required=True)
    args = p.parse_args()
    repo = MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path, initialize=False)
    source, bindings = repo.get_source(SOURCE_ID), repo.list_bindings(source_id=SOURCE_ID)
    if not args.apply:
        print(json.dumps({'source_version': source.version, 'planned': planned(source, bindings)}))
        return
    os.umask(0o077)
    args.directory.mkdir(parents=True, exist_ok=True)
    backup = args.directory / 'before.private.json'
    if not backup.exists():
        backup.write_text(json.dumps({'source': source.model_dump(mode='json'),
            'bindings': [b.model_dump(mode='json') for b in bindings]}), encoding='utf-8')
        backup.chmod(0o600)
    result = upgrade(MessageBusV2Service(repo))
    (args.directory/'activation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
