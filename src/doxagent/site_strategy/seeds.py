"""Idempotent initial Site Strategy records derived from existing adapters."""

from __future__ import annotations

import hashlib

from .repository import SiteStrategyRepository
from .schema import (
    AccessCombination,
    AccessOrderItem,
    AccessPolicy,
    AuthPolicy,
    AuthRequirement,
    BodyStrategyPointer,
    BrowserProfile,
    DomainRole,
    DomainRule,
    MatchType,
    ProxyEgress,
    SiteStrategySpec,
    StrategyPointer,
)
from .service import SiteStrategyService

EGRESSES = (
    ("jp-standard-6", "🇯🇵 日本标准 IEPL 专线 6", 18080),
    ("us-standard-5", "🇺🇸 美国标准 IEPL 专线 5", 18081),
    ("nl-standard-2", "🇳🇱 荷兰标准 IEPL 专线 2", 18082),
    ("de-standard-1", "🇩🇪 德国标准 IEPL 专线 1", 18083),
)


def bootstrap_seed(repository: SiteStrategyRepository, service: SiteStrategyService) -> list[str]:
    for egress_id, node_ref, port in EGRESSES:
        if repository.get_egress(egress_id) is None:
            repository.upsert_egress(
                ProxyEgress(
                    egress_id=egress_id,
                    node_ref=node_ref,
                    node_fingerprint=hashlib.sha256(node_ref.encode()).hexdigest(),
                    listener_port=port,
                    endpoint=f"http://doxagent-egress-clash:{port}",
                )
            )
    specs = seed_specs()
    for spec in specs:
        for combination in spec.access.combinations:
            if repository.get_profile(combination.profile_id) is None:
                repository.save_profile(
                    BrowserProfile(
                        profile_id=combination.profile_id,
                        site_id=spec.site_id,
                        bound_egress_id=combination.egress_id,
                        directory_key=combination.profile_id,
                        login_url=spec.auth.login_url,
                    )
                )
        if repository.get_head(spec.site_id) is None:
            service.apply_strategy(spec, expected_revision=None, actor="seed")
    return [item.site_id for item in specs]


def seed_specs() -> list[SiteStrategySpec]:
    specs = [
        _site(
            "generic",
            [],
            "builtin:generic@1",
            egresses=("jp-standard-6", "us-standard-5"),
        ),
        _site(
            "yahoo_finance",
            [
                "finance.yahoo.com",
                "www.finance.yahoo.com",
                "query1.finance.yahoo.com",
                "query2.finance.yahoo.com",
            ],
            "builtin:yahoo@1",
            crawler="builtin:yahoo_page@1",
            egresses=("jp-standard-6", "us-standard-5"),
            support=(
                "s.yimg.com",
                "yimg.com",
                "login.yahoo.com",
                "consent.yahoo.com",
                "guce.yahoo.com",
            ),
            body_parameters={
                "body_xpath": [
                    '//*[@data-testid="article-body"]',
                    '//*[contains(concat(" ",normalize-space(@class)," ")," caas-body ")]',
                ]
            },
        ),
        _site(
            "reuters",
            ["reuters.com", "www.reuters.com"],
            "builtin:reuters@1",
            crawler="builtin:reuters_search@1",
            egresses=("nl-standard-2", "de-standard-1"),
            support=(
                "dd.reuters.com",
                "api.reuters.com",
                "auth.reuters.com",
                "reutersmedia.net",
            ),
            auth="optional",
            access_order=["browser", "http_public", "reader"],
            body_parameters={"body_xpath": ['//*[@data-testid="ArticleBody"]']},
        ),
        _site(
            "barrons",
            ["barrons.com", "www.barrons.com"],
            "builtin:barrons@1",
            egresses=("us-standard-5", "jp-standard-6"),
            auth="required",
            login_url="https://www.barrons.com/login",
            support=("accounts.barrons.com", "accounts.dowjones.com", "api-secure.wsj.net"),
            body_parameters={
                "body_xpath": [
                    "//article",
                    '//*[@data-id="LiveCoverageCard_index_CardWrapper"]',
                ]
            },
        ),
        _site(
            "wsj",
            ["wsj.com", "www.wsj.com"],
            "builtin:wsj@1",
            egresses=("us-standard-5", "jp-standard-6"),
            auth="required",
            login_url="https://accounts.wsj.com/login",
            support=("accounts.wsj.com", "accounts.dowjones.com", "api-secure.wsj.net"),
            body_parameters={
                "body_xpath": [
                    '//article//*[contains(concat(" ",normalize-space(@class)," ")," paywall ")]'
                ]
            },
        ),
        _site(
            "seeking_alpha",
            ["seekingalpha.com", "www.seekingalpha.com"],
            "builtin:seeking_alpha@1",
            egresses=("us-standard-5", "jp-standard-6"),
            auth="required",
            login_url="https://seekingalpha.com/account/login",
            support=("static.seekingalpha.com",),
            body_parameters={"body_xpath": ['//*[@data-test-id="content-container"]']},
        ),
        _site(
            "marketwatch",
            ["marketwatch.com", "www.marketwatch.com"],
            "builtin:marketwatch@1",
            egresses=("us-standard-5", "jp-standard-6"),
            auth="required",
            login_url="https://accounts.marketwatch.com/login",
            support=("accounts.marketwatch.com", "accounts.dowjones.com", "api-secure.wsj.net"),
            body_parameters={
                "body_xpath": [
                    '//article//*[@id="js-article__body"]',
                    '//article//*[contains(@class,"article__body")]',
                    "//article",
                ],
                "remove_xpath": [
                    './/*[contains(@class,"related") or contains(@class,"recommend")]'
                ],
            },
        ),
        _site("thestreet", ["thestreet.com", "www.thestreet.com"], "builtin:thestreet@1"),
        _site("finnhub", ["finnhub.io", "www.finnhub.io"], "builtin:finnhub_redirect@1"),
        _site("cnbc", ["cnbc.com", "www.cnbc.com"], "builtin:cnbc@1"),
        _site("247wallst", ["247wallst.com", "www.247wallst.com"], "builtin:247wallst@1"),
        _site("fool", ["fool.com", "www.fool.com"], "builtin:fool@1"),
        _site("chartmill", ["chartmill.com", "www.chartmill.com"], "builtin:chartmill@1"),
        _site("benzinga", ["benzinga.com", "www.benzinga.com"], "builtin:benzinga@1"),
    ]
    return specs


def _site(
    site_id: str,
    domains: list[str],
    body: str,
    *,
    crawler: str | None = None,
    egresses: tuple[str, ...] = ("jp-standard-6",),
    support: tuple[str, ...] = (),
    auth: AuthRequirement = "none",
    login_url: str | None = None,
    access_order: list[AccessOrderItem] | None = None,
    body_parameters: dict[str, object] | None = None,
) -> SiteStrategySpec:
    combinations = [
        AccessCombination(
            id=f"{site_id}-{index + 1}",
            profile_id=f"{site_id}-{index + 1}",
            egress_id=egress_id,
            priority=(index + 1) * 10,
        )
        for index, egress_id in enumerate(egresses)
    ]
    return SiteStrategySpec(
        site_id=site_id,
        domains=[DomainRule(host=host) for host in domains],
        support_hosts=[
            DomainRule(
                host=host,
                match=MatchType.SUFFIX
                if host in {"yimg.com", "reutersmedia.net"}
                else MatchType.EXACT,
                role=DomainRole.ASSET,
            )
            for host in support
        ],
        access=AccessPolicy(
            combinations=combinations,
            probe_url=(f"https://{domains[0]}/" if domains else None),
        ),
        crawler=StrategyPointer(ref=crawler) if crawler else None,
        body=BodyStrategyPointer(
            ref=body,
            parameters=body_parameters or {},
            access_order=access_order
            or (["browser"] if auth == "required" else ["http_public", "browser", "reader"]),
        ),
        auth=AuthPolicy(requirement=auth, login_url=login_url),
    )


__all__ = ["EGRESSES", "bootstrap_seed", "seed_specs"]
