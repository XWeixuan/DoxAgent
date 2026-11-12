"""Promote Bloomberg through the existing source/binding configuration owners."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from doxagent.message_bus_v2.massive_relevance import massive_configured
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings

TICKERS = ['INTC', 'MU', 'BE', 'COHR', 'NBIS', 'CEG', 'RKLB', 'SNOW', 'ONDS', 'IONQ']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    repo = MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path, initialize=False)
    bus, terms = MessageBusV2Service(repo), MonitoringTermsService(repo)
    source = repo.get_source('bloomberg_massive')
    if source is None:
        raise ValueError('Bloomberg source is not registered')
    found = {ticker: terms.get(ticker) for ticker in TICKERS}
    for ticker, value in found.items():
        if not value or (not massive_configured(value[1], 'en')
                         and (ticker != 'IONQ' or not value[1].l2.get('en'))):
            raise ValueError('Missing monitoring rules: ' + ticker)
    receipt = dict(at=utc_now().isoformat(), source_id=source.source_id,
                   before_mode=source.delivery_mode, tickers=TICKERS,
                   initialize_ionq_massive=not massive_configured(found['IONQ'][1], 'en'))
    if not args.apply:
        print(json.dumps(receipt))
        return
    args.directory.mkdir(parents=True, exist_ok=True)
    backup = args.directory / 'before.private.json'
    if not backup.exists():
        backup.write_text(json.dumps(dict(source=source.model_dump(mode='json'),
            bindings=[b.model_dump(mode='json') for b in repo.list_bindings(source_id=source.source_id)],
            terms={t: v[1].model_dump(mode='json') for t, v in found.items()})), encoding='utf-8')
        backup.chmod(0o600)
    if receipt['initialize_ionq_massive']:
        revision, current = found['IONQ']
        config = current.model_dump(mode='json')
        unchanged = {k: v for k, v in config.items() if k not in {'expected_revision', 'massive_l2'}}
        config.update(expected_revision=revision, massive_l2={'en': config['l2']['en']})
        terms.apply(TickerMonitoringTerms.model_validate(config), actor='bloomberg-production-20261113')
        saved = terms.get('IONQ')[1].model_dump(mode='json')
        assert {k: v for k, v in saved.items() if k not in {'expected_revision', 'massive_l2'}} == unchanged
    bus.update_source(source.source_id, {'enabled': False}, actor=UpdateActor.USER,
                      reason='Prepare user-authorized Bloomberg formal subscriptions')
    current = repo.get_source(source.source_id)
    policy = current.distribution_policy.model_copy(update={
        'test_tickers': [], 'jev_enabled': False, 'require_complete_body': True})
    bus.update_source(source.source_id, dict(delivery_mode='production',
        default_parameters={**current.default_parameters, 'massive_regex_enabled': True},
        distribution_policy=policy.model_dump(mode='json')), actor=UpdateActor.USER,
        reason='User requested Bloomberg production for all ten monitored tickers')
    for ticker in TICKERS:
        binding = repo.get_binding(ticker + ':' + source.source_id)
        if not binding or not binding.enabled:
            bus.configure_binding(ticker=ticker, source_id=source.source_id, enabled=True,
                                  actor=UpdateActor.USER, reason='User requested Bloomberg subscription')
    final = bus.update_source(source.source_id, {'enabled': True}, actor=UpdateActor.USER,
                              reason='Enable Bloomberg production with ten real subscriptions')
    bindings = repo.list_bindings(source_id=source.source_id)
    assert {b.ticker for b in bindings if b.enabled} == set(TICKERS)
    assert final.default_parameters['endpoints'] == source.default_parameters['endpoints']
    receipt.update(source_version=final.version, delivery_mode=final.delivery_mode,
                   enabled=final.enabled, jev_enabled=final.distribution_policy.jev_enabled,
                   require_complete_body=final.distribution_policy.require_complete_body,
                   bindings=[dict(ticker=b.ticker, binding_id=b.binding_id, enabled=b.enabled,
                                  source_version=b.source_version) for b in bindings],
                   terms_revisions={t: terms.get(t)[0] for t in TICKERS},
                   rules_sha256={t: hashlib.sha256(json.dumps(terms.get(t)[1].massive_l2['en'].model_dump(),
                        sort_keys=True).encode()).hexdigest() for t in TICKERS})
    (args.directory / 'activation.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
