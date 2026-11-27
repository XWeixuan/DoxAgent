"""Securitize/COIN additions on the existing shared publisher HTTP and BODY lanes."""

from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from .schema import (
    AcquisitionMode,
    DistributionPolicy,
    PollingConfig,
    SchedulerConstraints,
    SourceDefinition,
    SourceKind,
)
from .technology_sources import TechnologySource

Q4_QUERY = urlencode(
    dict(
        LanguageId=1,
        bodyType=0,
        pressReleaseDateFilter=1,
        categoryId="00000000-0000-0000-0000-000000000000",
        year=-1,
        pageNumber=0,
        pageSize=40,
        includeTags="true",
    )
)
SECZ_IR = "https://investors.securitize.io/feed/PressRelease.svc/GetPressReleaseList?" + Q4_QUERY
ICE_API = "https://ir.theice.com/feed/PressRelease.svc/GetPressReleaseList?" + Q4_QUERY
SIFMA_GRAPH = "https://live-sifma-new.pantheonsite.io/graphql-0206"
TZERO_API = "https://jpd7ydtd.api.sanity.io/v2025-01-01/data/query/pre-prod"
TZERO_QUERY = '*[_type == "news" && !(_id in path("drafts.**"))] | order(publishedAt desc)[0...40]{_id,_type,title,publishedAt,slug,summary}'


@dataclass(frozen=True)
class SecuritiesSource(TechnologySource):
    mode: AcquisitionMode = AcquisitionMode.BY_TICKER

    def accepts(self, url):
        p = urlsplit(url)
        if p.scheme != "https" or p.username:
            return False
        if self.source_id == "exchange_infrastructure_press":
            return p.hostname in {"ir.theice.com", "www.dtcc.com", "dtcc.com"}
        if (
            self.source_id == "sifma_advocacy_press"
            and p.hostname == "live-sifma-new.pantheonsite.io"
        ):
            return p.path.startswith("/wp-content/uploads/")
        return super().accepts(url)


CATALOG = (
    SecuritiesSource(
        "sec_crypto_newsroom",
        "SEC / Crypto & Newsroom",
        "sec.gov",
        (
            "https://www.sec.gov/featured-topics/crypto-task-force/cryptosec",
            "https://www.sec.gov/newsroom/press-releases",
        ),
        ("SECZ", "COIN"),
        parser="securities_sec",
    ),
    SecuritiesSource(
        "marketsmedia_exchanges",
        "Markets Media / Exchanges & From The Markets",
        "marketsmedia.com",
        (
            "https://www.marketsmedia.com/category/exchanges-sectors/",
            "https://www.marketsmedia.com/category/from-the-markets/",
        ),
        ("SECZ", "COIN"),
        parser="securities_markets",
        mode=AcquisitionMode.BY_DISTRIBUTION,
    ),
    SecuritiesSource(
        "sifma_advocacy_press",
        "SIFMA / Letters & Press Releases",
        "sifma.org",
        ("https://www.sifma.org/advocacy/letters", "https://www.sifma.org/news/press-releases"),
        ("SECZ", "COIN"),
        route="us-standard-5",
        parser="securities_sifma",
    ),
    SecuritiesSource(
        "exchange_infrastructure_press",
        "NYSE / ICE & DTCC Press Releases (SECZ)",
        "theice.com",
        (
            "https://ir.theice.com/press/press-releases/default.aspx",
            "https://www.dtcc.com/press-releases",
        ),
        ("SECZ",),
        parser="securities_infrastructure",
    ),
    SecuritiesSource(
        "tzero_press_releases",
        "tZERO / News (SECZ)",
        "tzero.com",
        ("https://www.tzero.com/news",),
        ("SECZ",),
        parser="securities_tzero",
    ),
)
SOURCES = {s.source_id: s for s in CATALOG}
EXISTING = {"theblock_crypto", "coindesk_crypto", "ledgerinsights_news", "unchained_crypto"}


def initial_sources():
    return [
        SourceDefinition(
            source_id=s.source_id,
            display_name=s.name,
            adapter_ref="builtin:technology_public@1",
            kind=SourceKind.CRAWLER,
            acquisition_mode=s.mode,
            content_language="en",
            entry_url=s.urls[0],
            scheduler_group="technology." + s.domain,
            scheduler_constraints=SchedulerConstraints(
                max_concurrency=2, minimum_request_gap_seconds=0.5
            ),
            default_polling_config=PollingConfig(target_interval_seconds=20),
            default_parameters={"discovery_mode": "http_listing"},
            parameter_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"discovery_mode": {"const": "http_listing"}},
            },
            distribution_policy=DistributionPolicy(
                jev_enabled=s.mode.shared_distribution, require_complete_body=True
            ),
        )
        for s in CATALOG
    ]


def subscription_ids(sources, ticker):
    """Only SECZ uses this portfolio; existing ticker defaults are unchanged."""
    if ticker != "SECZ":
        return None
    import json
    from pathlib import Path

    catalog = json.loads(
        Path(__file__).with_name("source_catalog.json").read_text(encoding="utf-8")
    )["sources"]
    return (
        EXISTING
        | set(SOURCES)
        | {
            s.source_id
            for s in sources
            if s.enabled and catalog.get(s.source_id, {}).get("applicability") == "GLOBAL"
        }
    )


def listing_recipes():
    from .public_listing import Listing

    result = {}
    for s in CATALOG:
        entries = []
        for i, feed in enumerate(s.urls):
            url, parser, route, method = feed, s.parser, s.route, "GET"
            if s.source_id == "sec_crypto_newsroom":
                parser = "securities_sec_crypto" if i == 0 else "securities_sec_press"
            elif s.source_id == "sifma_advocacy_press":
                url, method = SIFMA_GRAPH, "POST"
            elif s.source_id == "marketsmedia_exchanges":
                method = "POST"
            elif s.source_id == "exchange_infrastructure_press":
                parser = "securities_q4" if i == 0 else "securities_dtcc"
                if i == 0:
                    url, route = ICE_API, "us-standard-5"
                else:
                    from .securities_http import DTCC_SEARCH

                    url, method = DTCC_SEARCH, "POST"
            elif s.source_id == "tzero_press_releases":
                url = (
                    TZERO_API + "?" + urlencode({"query": TZERO_QUERY, "perspective": "published"})
                )
            entries.append(
                Listing(
                    feed,
                    url,
                    parser,
                    route,
                    method=method,
                    rss_fallback=False,
                    cache_diagnostic_only=True,
                )
            )
        result[s.source_id] = tuple(entries)
    return result
