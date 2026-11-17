import importlib.util
from pathlib import Path

import pytest


def config(sources):
    path = Path(__file__).parents[1] / 'deploy/configure-datadome.py'
    spec = importlib.util.spec_from_file_location('solver_proxy', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.squid_config({'trial': {'proxyLogin': 'dd-trial', 'egress_id': 'de2'}},
        {'de2': ('doxagent-egress-clash', 18080)}, '172.18.0.0/16', solver_source_ips=sources)


def test_observed_source_is_exact_and_old_provider_and_auth_remain():
    text = config(['158.180.12.56', '158.180.12.56'])
    acl = next(s for s in text.splitlines() if s.startswith('acl solver_src '))
    assert acl == 'acl solver_src src 138.201.188.166/32 172.18.0.0/16 158.180.12.56/32'
    assert 'http_access deny !solver_src' in text and 'proxy_auth dd-trial' in text
    assert 'never_direct allow all' in text and 'ssl_bump' not in text
    assert '18792' not in text


@pytest.mark.parametrize('value', ['0.0.0.0', '0.0.0.0/0', '158.180.12.0/24', '127.0.0.1', '10.0.0.1'])
def test_source_discovery_does_not_expand_to_broad_or_private_acl(value):
    with pytest.raises(ValueError):
        config([value])
