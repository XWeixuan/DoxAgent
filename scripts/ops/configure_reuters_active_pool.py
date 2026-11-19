"""Run inside Site Access. Preview by default; normal three-member work, no Cookie cloning.

The third member earns its own business/auth proofs before selection. Disabling
pool routing retains the identity/Profile and evidence; never deletes them.
"""
import argparse
import copy
import json
import urllib.request
from pathlib import Path

from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.governance_models import SourceAccessScope, scope_identity_ids
from doxagent.site_strategy.schema import BrowserIdentitySpec, BrowserProfile
from math import ceil


THIRD = 'reuters-active-3'


def configuration(scope, main, backup):
    """Hold the two-member aggregate envelope constant while spreading work."""
    identity = copy.deepcopy(main)
    identity.update(identity_id=THIRD, profile_id=THIRD, revision=0,
                    owner_scope_id=None, credential_ref=None)
    identity['lifecycle']['residency'] = 'always_on'
    identity = BrowserIdentitySpec.model_validate(identity)
    profile = BrowserProfile(profile_id=THIRD, site_id='reuters', directory_key=THIRD,
        bound_egress_id=identity.egress_id, environment=identity.environment)
    updated = copy.deepcopy(scope)
    updated['verification_plan'].update(crawler_routing='active_pool', body_routing='active_pool',
        additional_active_identity_ids=[THIRD], max_parallel_concurrency=min(4,
            main['access']['max_concurrency'] + backup['access']['max_concurrency']))
    intervals = [member['access']['min_interval_ms'] for member in (main, backup)]
    updated['verification_plan']['min_parallel_interval_ms'] = (
        ceil(1 / sum(1 / interval for interval in intervals)) if min(intervals) > 0 else 0)
    return identity, profile, SourceAccessScope.model_validate(updated)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--disable', action='store_true')
    args = parser.parse_args()
    settings = DoxAgentSettings()
    token = Path(settings.site_access_admin_token_file).read_text().strip()

    def api(path, value=None, method=None):
        request = urllib.request.Request('http://127.0.0.1:8011' + path,
            data=json.dumps(value).encode() if value is not None else None,
            headers={'Authorization':'Bearer ' + token, 'Content-Type':'application/json'},
            method=method or ('POST' if value is not None else 'GET'))
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.load(response)

    scope = next(s for s in api('/v2/scopes') if s['scope_id']=='source:reuters_site_search')
    identities = {item['spec']['identity_id']:item['spec'] for item in api('/v1/identities')}
    primary, backup = (identities[scope[key]] for key in ('primary_identity_id','fallback_identity_id'))
    identity, profile, updated = configuration(scope, primary, backup)
    if args.disable:
        value = copy.deepcopy(scope)
        value['verification_plan'].update(crawler_routing='dual_active')
        for key in ('body_routing','additional_active_identity_ids','max_parallel_concurrency','min_parallel_interval_ms'):
            value['verification_plan'].pop(key, None)
        updated = SourceAccessScope.model_validate(value)
    profiles = {p['profile_id']:p for p in api('/v1/profiles')}
    existing = identities.get(THIRD)
    if existing and not args.disable:
        # Never mutate a later human-maintained identity merely to make reruns identical.
        if (existing['profile_id'], existing['egress_id'], existing['runtime_kind'],
            existing['environment'], existing['browser_release']) != (
            identity.profile_id, identity.egress_id, identity.runtime_kind.value,
            identity.environment.model_dump(mode='json'), identity.browser_release):
            raise ValueError('third identity differs from approved environment; review explicitly')
    if THIRD in profiles and profiles[THIRD]['directory_key'] != THIRD:
        raise ValueError('third Profile directory differs')
    if args.apply and not args.disable:
        if THIRD not in profiles:
            api('/v1/profiles/' + THIRD, profile.model_dump(mode='json'), method='PUT')
        if not existing:
            api('/v1/identities:apply', {'spec': identity.model_dump(mode='json'),
                'expected_revision':None, 'actor':'reuters-active-pool', 'enabled':True})
    changed = updated.model_dump(mode='json') != scope
    if args.apply and changed:
        updated = SourceAccessScope.model_validate(api('/v2/scopes:apply', {
            'spec':updated.model_dump(mode='json'), 'expected_revision':scope['revision']}))
    print(json.dumps({'apply':args.apply, 'changed':changed, 'revision':updated.revision,
        'members':scope_identity_ids(updated), 'third_profile':THIRD,
        'shares_primary_egress':True, 'cookies_copied':False,
        'max_parallel_concurrency':updated.verification_plan.get('max_parallel_concurrency'),
        'min_parallel_interval_ms':updated.verification_plan.get('min_parallel_interval_ms'),
        'crawler_routing':updated.verification_plan.get('crawler_routing'),
        'body_routing':updated.verification_plan.get('body_routing'),
        'requires_independent_auth_and_business_proof':True}))


if __name__ == '__main__':
    main()
