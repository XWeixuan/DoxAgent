"""Idempotent initial Site Strategy records derived from existing adapters."""

from __future__ import annotations

import hashlib
from typing import Literal

from .identity_migration import migrate_legacy_identities
from .repository import SiteStrategyRepository
from .schema import (
    AccessCombination,
    AccessOrderItem,
    AccessPolicy,
    AuthPolicy,
    AuthRequirement,
    BodyStrategyPointer,
    BrowserEnvironment,
    BrowserProfile,
    DomainRole,
    DomainRule,
    MatchType,
    ProxyEgress,
    SiteStrategySpec,
    StrategyPointer,
    VerificationKind,
)
from .service import SiteStrategyService

EGRESSES = (
    ("jp-standard-6", "🇯🇵 日本标准 IEPL 专线 6", 18080),
    ("us-standard-5", "🇺🇸 美国标准 IEPL 专线 5", 18081),
    # Keep the stable egress id/port because existing Profiles are immutably
    # bound to it; the subscribed node behind that slot is replaceable.
    ("nl-standard-2", "🇳🇱 荷兰标准 IEPL 专线 1", 18082),
    ("de-standard-1", "🇩🇪 德国标准 IEPL 专线 1", 18083),
    ("server-direct", "DIRECT", 18084),
)

EGRESS_TIMEZONES = {
    "jp-standard-6": "Asia/Tokyo",
    "us-standard-5": "America/Los_Angeles",
    "nl-standard-2": "Europe/Amsterdam",
    "de-standard-1": "Europe/Berlin",
    "server-direct": "Asia/Singapore",
}


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
            assert combination.profile_id is not None
            assert combination.egress_id is not None
            profile = repository.get_profile(combination.profile_id)
            if profile is None:
                repository.save_profile(
                    BrowserProfile(
                        profile_id=combination.profile_id,
                        site_id=spec.site_id,
                        bound_egress_id=combination.egress_id,
                        directory_key=combination.profile_id,
                        login_url=spec.auth.login_url,
                        environment=BrowserEnvironment(
                            timezone_id=EGRESS_TIMEZONES.get(
                                combination.egress_id, "Asia/Singapore"
                            )
                        ),
                    )
                )
            elif (
                profile.environment.revision == 1
                and profile.environment.timezone_id == "Asia/Singapore"
                and combination.egress_id != "server-direct"
            ):
                repository.save_profile(
                    profile.model_copy(
                        update={
                            "environment": profile.environment.model_copy(
                                update={
                                    "timezone_id": EGRESS_TIMEZONES.get(
                                        combination.egress_id, "Asia/Singapore"
                                    )
                                }
                            )
                        }
                    )
                )
        head = repository.get_head(spec.site_id)
        if head is None:
            service.apply_strategy(spec, expected_revision=None, actor="seed")
        else:
            current = repository.get_strategy(spec.site_id)
            if current is not None:
                auth_updates: dict[str, object] = {}
                if current.auth.verification_url is None and spec.auth.verification_url is not None:
                    auth_updates["verification_url"] = spec.auth.verification_url
                    auth_updates["verification_kind"] = spec.auth.verification_kind
                if current.auth.maintenance_url is None and spec.auth.maintenance_url is not None:
                    auth_updates["maintenance_url"] = spec.auth.maintenance_url
                if current.auth.login_url is None and spec.auth.login_url is not None:
                    auth_updates["login_url"] = spec.auth.login_url
                if (
                    current.auth.crawler_requirement == "inherit"
                    and spec.auth.crawler_requirement == "none"
                ):
                    auth_updates["crawler_requirement"] = "none"
                if (
                    spec.site_id == "barrons"
                    and current.auth.crawler_requirement != "required"
                ):
                    auth_updates["crawler_requirement"] = "required"
                support = list(current.support_hosts)
                known_support = {(item.match, item.host, item.role) for item in support}
                for rule in spec.support_hosts:
                    if (rule.match, rule.host, rule.role) not in known_support:
                        support.append(rule)
                access_updates: dict[str, object] = {}
                if (
                    spec.access.max_concurrency == 1
                    and current.access.max_concurrency == 2
                    and current.access.min_interval_ms == 500
                ):
                    access_updates.update(max_concurrency=1, min_interval_ms=3000)
                if (
                    spec.site_id == "barrons"
                    and current.access.overrides != spec.access.overrides
                ):
                    access_updates["overrides"] = spec.access.overrides
                body = current.body
                crawler = current.crawler or spec.crawler
                if (
                    spec.body.access_order
                    and spec.body.access_order[0] == "browser"
                    and current.body.access_order == ["http_public", "browser", "reader"]
                ):
                    body = current.body.model_copy(update={"access_order": spec.body.access_order})
                if (
                    not auth_updates
                    and len(support) == len(current.support_hosts)
                    and not access_updates
                    and body is current.body
                    and crawler is current.crawler
                ):
                    continue
                upgraded = current.model_copy(
                    update={
                        "auth": current.auth.model_copy(update=auth_updates),
                        "support_hosts": support,
                        "access": current.access.model_copy(update=access_updates),
                        "body": body,
                        "crawler": crawler,
                    }
                )
                service.apply_strategy(
                    upgraded,
                    expected_revision=current.revision,
                    actor="seed:browser-environment-v2",
                )
    migrate_legacy_identities(repository)
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
            access_order=["browser", "http_public", "reader"],
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
            login_url="https://www.reuters.com/",
            maintenance_url="https://www.reuters.com/site-search/?query=markets",
            verification_url="https://www.reuters.com/world/",
            verification_kind="public_access",
            access_order=["browser", "http_public", "reader"],
            body_parameters={"body_xpath": ['//*[@data-testid="ArticleBody"]']},
        ),
        _site(
            "barrons",
            ["barrons.com", "www.barrons.com"],
            "builtin:barrons@1",
            crawler="builtin:barrons_ticker@1",
            egresses=("us-standard-5", "jp-standard-6"),
            auth="required",
            crawler_auth="required",
            access_overrides={
                "body": ["barrons-1", "barrons-2"],
                "crawler": ["barrons-1", "barrons-2"],
            },
            login_url="https://www.barrons.com/login",
            verification_url=(
                "https://www.barrons.com/articles/"
                "annaly-capital-sports-mortgage-reit-dividend-stock-price-3972cdfb"
            ),
            support=(
                "accounts.barrons.com",
                "accounts.dowjones.com",
                "sso.accounts.dowjones.com",
                "customercenter.barrons.com",
                "api-secure.wsj.net",
            ),
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
            verification_url=(
                "https://www.wsj.com/business/retail/cvs-small-store-openings-b0e196a2"
            ),
            support=(
                "accounts.wsj.com",
                "accounts.dowjones.com",
                "sso.accounts.dowjones.com",
                "api-secure.wsj.net",
            ),
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
            verification_url=(
                "https://seekingalpha.com/article/4765976-prologis-buying-the-data-center-story"
            ),
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
            verification_url=(
                "https://www.marketwatch.com/story/"
                "orion-properties-says-director-nomination-notice-from-kawa-is-invalid-67812229"
            ),
            support=(
                "accounts.marketwatch.com",
                "accounts.dowjones.com",
                "sso.accounts.dowjones.com",
                "api-secure.wsj.net",
            ),
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
        _site(
            "ctee",
            ["ctee.com.tw", "www.ctee.com.tw"],
            "builtin:generic@1",
            egresses=("jp-standard-6",),
        ),
        _site(
            "trendforce",
            ["trendforce.com", "www.trendforce.com"],
            "builtin:generic@1",
            crawler="builtin:trendforce_listings@1",
            egresses=("server-direct",),
            support=("img.trendforce.com",),
            body_parameters={"body_xpath": ["//article"]},
            default_content_language="en",
        ),
        _site(
            "digitimes",
            ["digitimes.com", "www.digitimes.com"],
            "builtin:generic@1",
            crawler="builtin:digitimes_semiconductors@1",
            egresses=("server-direct",),
            support=("img.digitimes.com", "accounts.digitimes.com"),
            auth="required",
            crawler_auth="none",
            login_url="https://www.digitimes.com/register/signin.php",
            maintenance_url="https://www.digitimes.com/topic/semiconductors/",
            verification_url=(
                "https://www.digitimes.com/news/"
                "a20260923VL216/absolics-testing-investment-glass-substrate-subsidiary.html"
            ),
            body_parameters={
                "body_xpath": [
                    '//*[@itemprop="articleBody"]',
                    '//article',
                    '//*[contains(@class,"article-body")]',
                ]
            },
            default_content_language="en",
        ),
        _site(
            "huggingnews",
            ["huggingnews.com", "www.huggingnews.com"],
            "builtin:generic@1",
            egresses=("de-standard-1",),
            body_parameters={"body_xpath": ["//article"]},
            default_content_language="en",
        ),
        _site(
            "tomshardware",
            ["tomshardware.com", "www.tomshardware.com"],
            "builtin:generic@1",
            egresses=("server-direct",),
            body_parameters={"body_xpath": ["//article"]},
            default_content_language="en",
        ),
        _site(
            "thelec",
            ["thelec.kr", "www.thelec.kr"],
            "builtin:generic@1",
            egresses=("server-direct",),
            body_parameters={"body_xpath": ['//*[@itemprop="articleBody"]']},
            default_content_language="ko",
        ),
        _site(
            "etnews",
            ["etnews.com", "www.etnews.com"],
            "builtin:etnews@1",
            egresses=("server-direct",),
            body_parameters={"body_xpath": ['//*[@itemprop="articleBody"]']},
            default_content_language="ko",
        ),
        _site(
            "digitimes_tw",
            ["digitimes.com.tw", "www.digitimes.com.tw"],
            "builtin:generic@1",
            egresses=("server-direct",),
            support=("img.digitimes.com",),
            default_content_language="zh-Hant",
        ),
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
    crawler_auth: Literal["inherit", "none", "optional", "required"] = "inherit",
    access_overrides: dict[Literal["body", "crawler"], list[str]] | None = None,
    login_url: str | None = None,
    maintenance_url: str | None = None,
    verification_url: str | None = None,
    verification_kind: VerificationKind = "subscription_article",
    access_order: list[AccessOrderItem] | None = None,
    body_parameters: dict[str, object] | None = None,
    default_content_language: str | None = None,
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
        default_content_language=default_content_language
        or ("zh-Hant" if site_id == "ctee" else None),
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
            overrides=access_overrides or {},
            probe_url=(f"https://{domains[0]}/" if domains else None),
            max_concurrency=(
                1
                if site_id
                in {"yahoo_finance", "reuters", "barrons", "wsj", "seeking_alpha", "marketwatch"}
                else 2
            ),
            min_interval_ms=(
                3000
                if site_id
                in {"yahoo_finance", "reuters", "barrons", "wsj", "seeking_alpha", "marketwatch"}
                else 500
            ),
        ),
        crawler=StrategyPointer(ref=crawler) if crawler else None,
        body=BodyStrategyPointer(
            ref=body,
            parameters=body_parameters or {},
            access_order=access_order
            or (["browser"] if auth == "required" else ["http_public", "browser", "reader"]),
        ),
        auth=AuthPolicy(
            requirement=auth,
            crawler_requirement=crawler_auth,
            login_url=login_url,
            maintenance_url=maintenance_url,
            verification_url=verification_url,
            verification_kind=verification_kind,
        ),
    )


__all__ = ["EGRESSES", "bootstrap_seed", "seed_specs"]
