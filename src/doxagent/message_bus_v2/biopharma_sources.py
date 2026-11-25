"""Biopharma recipes on the existing publisher HTTP, scheduling and BODY lanes."""

from dataclasses import dataclass

from .schema import (
    AcquisitionMode,
    DistributionPolicy,
    PollingConfig,
    SchedulerConstraints,
    SearchPolicy,
    SourceActivationWindow,
    SourceDefinition,
    SourceKind,
)
from .technology_sources import TechnologySource

TICKERS = ("XENE", "RGNX", "PRAX", "CGEM", "SMMT")
FDA_CALENDAR = "https://www.fda.gov/datatables-json/advisory-committee-calendar-json"
AES_EMBED = (
    "https://fuse.fusesearch.app/api/1/views/fgjcnalwpsvtsmopnhsw.js"
    "?element_id=sjview_fgjcnalwpsvtsmopnhsw"
)
AES_PAGE = "https://aesnet.org/education/annual-meeting/aes-abstract-search"


@dataclass(frozen=True)
class BiopharmaSource(TechnologySource):
    mode: AcquisitionMode = AcquisitionMode.BY_DISTRIBUTION

    def accepts(self, url):
        from urllib.parse import urlsplit

        if self.source_id == "akeso_press_releases" and urlsplit(url).hostname in {
            "prnewswire.com",
            "www.prnewswire.com",
        }:
            return urlsplit(url).scheme == "https" and not urlsplit(url).username
        return super().accepts(url)


CATALOG = (
    BiopharmaSource(
        "fda_release",
        "FDA / Approvals, Biologics & Advisory Calendar",
        "fda.gov",
        (
            "https://www.fda.gov/drugs/news-events-human-drugs/notable-approvals-drugs",
            "https://www.fda.gov/vaccines-blood-biologics/news-events-biologics/whats-new-biologics",
            "https://www.fda.gov/advisory-committees/advisory-committee-calendar",
        ),
        TICKERS,
        parser="bio_fda",
    ),
    BiopharmaSource(
        "endpoints_biopharma",
        "Endpoints / News",
        "endpoints.news",
        ("https://endpoints.news/news/",),
        TICKERS,
        parser="bio_endpoints",
    ),
    BiopharmaSource(
        "fiercebiotech_biotech",
        "Fierce Biotech / Biotech",
        "fiercebiotech.com",
        ("https://www.fiercebiotech.com/biotech",),
        TICKERS,
        route="us-standard-5",
        parser="bio_fierce",
    ),
    BiopharmaSource(
        "stat_biotech_trials",
        "STAT / Biotech & Clinical Trials",
        "statnews.com",
        (
            "https://www.statnews.com/topic/biotech/",
            "https://www.statnews.com/topic/clinical-trials/",
        ),
        TICKERS,
        parser="bio_stat",
    ),
    BiopharmaSource(
        "hkex_akeso",
        "HKEXnews / AKESO 09926 (SMMT)",
        "hkexnews.hk",
        ("https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en",),
        ("SMMT",),
        parser="bio_hkex",
        mode=AcquisitionMode.BY_TICKER,
    ),
    BiopharmaSource(
        "akeso_press_releases",
        "Akeso / Press Releases (SMMT)",
        "akesobio.com",
        (
            "https://akesobio.com/en/media/akeso-news",
            "https://www.prnewswire.com/news/akeso%2C-inc./",
        ),
        ("SMMT",),
        parser="bio_akeso",
        mode=AcquisitionMode.BY_TICKER,
    ),
    BiopharmaSource(
        "taiho_press_releases",
        "Taiho Oncology / Press Releases (CGEM)",
        "taihooncology.com",
        ("https://www.taihooncology.com/us/news/",),
        ("CGEM",),
        parser="bio_taiho",
        mode=AcquisitionMode.BY_TICKER,
    ),
    BiopharmaSource(
        "ash_cullinan",
        "ASH / Cullinan Abstracts (CGEM)",
        "hematology.org",
        ("https://onlineprogram.hematology.org/",),
        ("CGEM",),
        parser="bio_ash",
        mode=AcquisitionMode.BY_TICKER,
    ),
    BiopharmaSource(
        "aes_abstract_search",
        "AES / Scientific Abstract Search (XENE)",
        "aesnet.org",
        (AES_PAGE,),
        ("XENE",),
        parser="bio_aes",
        mode=AcquisitionMode.BY_SEARCH,
    ),
)
SOURCES = {s.source_id: s for s in CATALOG}
WINDOWS = {
    "ash_cullinan": ("2026-12-03", "2026-12-09"),
    "aes_abstract_search": ("2026-12-07", "2026-12-07"),
}


def initial_sources():
    result = []
    for s in CATALOG:
        parameters = {"discovery_mode": "http_listing"}
        properties = {"discovery_mode": {"const": "http_listing"}}
        if s.source_id in WINDOWS:
            parameters["activation_window"] = SourceActivationWindow(
                start_date=WINDOWS[s.source_id][0], end_date=WINDOWS[s.source_id][1]
            ).model_dump(mode="json")
            properties["activation_window"] = {
                "type": "object",
                "required": ["start_date", "end_date", "timezone"],
                "additionalProperties": False,
                "properties": {
                    k: {"type": "string"} for k in ("start_date", "end_date", "timezone")
                },
            }
        result.append(
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
                default_parameters=parameters,
                parameter_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": properties,
                },
                search_policy=SearchPolicy(mode="or")
                if s.mode is AcquisitionMode.BY_SEARCH
                else None,
                distribution_policy=DistributionPolicy(jev_enabled=True, require_complete_body=True)
                if s.mode.shared_distribution
                else None,
            )
        )
    return result


def listing_recipes():
    from .public_listing import Listing

    recipes = {}
    for s in CATALOG:
        values = []
        for i, u in enumerate(s.urls):
            parser = s.parser
            method = "GET"
            url = u
            if s.source_id == "fda_release":
                parser = ("bio_fda_approvals", "bio_fda_biologics", "bio_fda_calendar")[i]
                url = FDA_CALENDAR if i == 2 else u
                method = "GET" if i == 2 else "POST"
            elif s.source_id == "fiercebiotech_biotech":
                url = "https://www.fiercebiotech.com/api/v1/fronts/51691?page=1&noHeroArticles=true"
            elif s.source_id in {
                "endpoints_biopharma",
                "stat_biotech_trials",
                "taiho_press_releases",
                "akeso_press_releases",
            }:
                method = "POST" if "prnewswire.com" not in u else "GET"
            elif s.source_id in {"hkex_akeso", "ash_cullinan"}:
                method = "POST"
            values.append(
                Listing(
                    u,
                    url,
                    parser,
                    s.route,
                    s.transport,
                    method=method,
                    rss_fallback=False,
                    cache_diagnostic_only=True,
                )
            )
        recipes[s.source_id] = tuple(values)
    return recipes


def subscription_ids(sources, ticker):
    """The user's five-ticker portfolio, also respected by default materialization."""
    import json
    from pathlib import Path

    if ticker not in TICKERS:
        return None
    metadata = json.loads(
        Path(__file__).with_name("source_catalog.json").read_text(encoding="utf-8")
    )["sources"]
    approved = {s.source_id for s in CATALOG if ticker in s.tickers}
    for source in sources:
        entry = metadata.get(source.source_id, {})
        if not source.enabled or entry.get("applicability") != "GLOBAL":
            continue
        if source.source_id == "newswire_rss" and not source.default_parameters.get("rss_urls"):
            continue
        methods = {
            m
            for stage in ("listing", "body")
            for path in ("primary", "fallback")
            for m in entry.get(stage, {}).get(path, [])
        }
        chrome = any("CHROME" in m or "PLAYWRIGHT" in m for m in methods)
        if source.source_id == "yahoo_finance_news":
            continue
        if chrome and not (
            source.source_id == "reuters_site_search" and ticker in {"RGNX", "SMMT"}
        ):
            continue
        approved.add(source.source_id)
    return approved
