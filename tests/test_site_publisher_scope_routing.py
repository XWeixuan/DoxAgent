from types import SimpleNamespace

from doxagent.site_strategy.scope import ScopeResolver


def model(**values):
    return SimpleNamespace(**values, model_copy=lambda *, update: model(**{**values, **update}))


def owner(key, site, *, default=False):
    return model(
        scope_id=key,
        owner_site_id=site,
        enabled=True,
        revision=1,
        primary_identity_id=key.replace(":", "-").replace(".", "-") + "-main",
        fallback_identity_id=None,
        verification_plan={"default_publisher": default},
    )


def resolver_with(*scopes):
    resolver = ScopeResolver.__new__(ScopeResolver)
    mapping = {s.scope_id: s for s in scopes}
    resolver.governance = SimpleNamespace(
        get_scope=mapping.get, list_scopes=lambda: list(mapping.values())
    )
    return resolver


def resolved(site, host):
    return model(site_id=site, enabled=True, access=model(), match_evidence={"host": host})


def test_cross_source_body_reuses_explicit_default_instead_of_empty_profile():
    yahoo = owner("source:yahoo", "yahoo_finance", default=True)
    barrons = owner("source:barrons", "barrons", default=True)
    empty = owner("publisher:barrons", "barrons")
    resolver = resolver_with(yahoo, barrons, empty)
    result = resolver.resolve(resolved("barrons", "www.barrons.com"), yahoo.scope_id)
    assert result.runtime_key == "scope:source:barrons"
    assert result.access.combinations[0].identity_id == barrons.primary_identity_id


def test_same_site_feed_keeps_its_isolated_identity():
    news = owner("source:news", "trendforce", default=True)
    press = owner("source:press", "trendforce")
    result = resolver_with(news, press).resolve(
        resolved("trendforce", "www.trendforce.com"), press.scope_id
    )
    assert result.runtime_key == "scope:source:press"


def test_no_origin_also_prefers_explicit_default_over_old_auto_scope():
    empty = owner("publisher:barrons", "barrons")
    preferred = owner("source:barrons", "barrons", default=True)
    result = resolver_with(empty, preferred).resolve(resolved("barrons", "www.barrons.com"))
    assert result.runtime_key == "scope:source:barrons"


def test_generic_publishers_remain_domain_isolated():
    origin = owner("publisher:one.test", "generic", default=True)
    target = owner("publisher:two.test", "generic")
    result = resolver_with(origin, target).resolve(resolved("generic", "two.test"), origin.scope_id)
    assert result.runtime_key == "scope:publisher:two.test"
