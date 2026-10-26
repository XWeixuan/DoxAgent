"""One external identity per publisher/sector/method; ticker-specific internal sections."""

from dataclasses import dataclass

from .schema import (
    AcquisitionMode,
    DistributionPolicy,
    PollingConfig,
    SchedulerConstraints,
    SearchPolicy,
    SourceDefinition,
    SourceKind,
    sha256_text,
)
from .technology_sources import TechnologySource

ENERGY = ("CEG", "BE")
COMMON = ("CEG", "BE", "INTC", "MU", "COHR", "NBIS")
NRC_EVENTS = "https://www.nrc.gov/documents-reports/document-collections/events-reports-associated-with/event-notification-reports/en"


@dataclass(frozen=True)
class EnergySource(TechnologySource):
    mode: AcquisitionMode = AcquisitionMode.BY_DISTRIBUTION
    sections: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def urls_for(self, ticker):
        return dict(self.sections).get(ticker, self.urls)


def feeds(domain, paths):
    return tuple("https://www." + domain + "/" + path + "/feed/" for path in paths)


POWER_CEG = feeds(
    "powermag.com",
    tuple(
        "category/" + p
        for p in (
            "nuclear",
            "data-centers",
            "legal-regulatory",
            "safety",
        )
    ),
)
POWER_BE = feeds(
    "powermag.com",
    tuple(
        "category/" + p
        for p in (
            "data-centers",
            "distributed-generation",
            "energy-storage-2",
            "legal-regulatory",
            "hydrogen",
        )
    ),
)
POWERENG_CEG = feeds("power-eng.com", ("nuclear", "operations-maintenance", "business"))
POWERENG_BE = feeds("power-eng.com", ("onsite-power", "energy-storage", "renewables", "business"))
DCD_CEG = "https://www.datacenterdynamics.com/en/news/?company=constellation-energy"
DCD_BE = "https://www.datacenterdynamics.com/en/news/?company=bloom-energy"
DOE_NE = "https://www.energy.gov/ne/listings/ne-press-releases"
DOE_COMMON = tuple(
    "https://www.energy.gov/" + path
    for path in (
        "edf/listings/edf-news",
        "oe/listings/grid-deployment-office-news",
        "oe/listings/news-highlights",
    )
)

CATALOG = (
    EnergySource(
        "national_quantum_initiative",
        "National Quantum Initiative / News",
        "quantum.gov",
        ("https://www.quantum.gov/news/",),
        ("IONQ",),
        parser="quantum",
        mode=AcquisitionMode.BY_TICKER,
    ),
    EnergySource(
        "nist_news",
        "NIST / News",
        "nist.gov",
        ("https://www.nist.gov/news-events/news/rss.xml",),
        ("IONQ",),
    ),
    EnergySource(
        "nro_press_releases",
        "NRO / Press Releases",
        "nro.gov",
        ("https://www.nro.gov/news-media-featured-stories/news-media-press-releases/",),
        ("IONQ",),
        parser="nro",
        route="us-standard-5",
    ),
    EnergySource(
        "nsf_news",
        "NSF / News",
        "nsf.gov",
        ("https://www.nsf.gov/rss/rss_www_news.xml",),
        ("IONQ",),
        route="us-standard-5",
    ),
    EnergySource(
        "dcd_energy_ticker",
        "Data Center Dynamics / Energy Companies",
        "datacenterdynamics.com",
        (DCD_CEG, DCD_BE),
        ENERGY,
        parser="dcd",
        route="us-standard-5",
        mode=AcquisitionMode.BY_TICKER,
        sections=(("CEG", (DCD_CEG,)), ("BE", (DCD_BE,))),
    ),
    EnergySource(
        "power_magazine_energy",
        "POWER Magazine / Energy",
        "powermag.com",
        tuple(dict.fromkeys((*POWER_CEG, *POWER_BE))),
        ENERGY,
        route="us-standard-5",
        mode=AcquisitionMode.BY_TICKER,
        sections=(("CEG", POWER_CEG), ("BE", POWER_BE)),
    ),
    EnergySource(
        "power_engineering_energy",
        "Power Engineering / Energy",
        "power-eng.com",
        tuple(dict.fromkeys((*POWERENG_CEG, *POWERENG_BE))),
        ENERGY,
        route="us-standard-5",
        sections=(("CEG", POWERENG_CEG), ("BE", POWERENG_BE)),
    ),
    EnergySource(
        "utility_dive",
        "Utility Dive",
        "utilitydive.com",
        ("https://www.utilitydive.com/feeds/news/",),
        ENERGY,
        transport="urllib",
    ),
    EnergySource(
        "world_nuclear_news",
        "World Nuclear News",
        "world-nuclear-news.org",
        ("https://www.world-nuclear-news.org/rss",),
        ("CEG",),
    ),
    EnergySource(
        "ans_nuclear_newswire",
        "ANS / Nuclear Newswire",
        "ans.org",
        ("https://www.ans.org/news/feed/",),
        ("CEG",),
    ),
    EnergySource(
        "canary_media",
        "Canary Media",
        "canarymedia.com",
        ("https://www.canarymedia.com/rss.rss",),
        ENERGY,
    ),
    EnergySource(
        "nrc_news_releases",
        "NRC / News Releases",
        "nrc.gov",
        ("https://www.nrc.gov/about-nrc/news-releases/2026",),
        ("CEG",),
        parser="nrc_news",
    ),
    EnergySource(
        "nrc_event_notifications",
        "NRC / Event Notifications by Licensee",
        "nrc.gov",
        (NRC_EVENTS,),
        ("CEG",),
        parser="nrc_events",
        mode=AcquisitionMode.BY_SEARCH,
    ),
    EnergySource(
        "doe_energy_news",
        "DOE / Nuclear Energy, EDF & Electricity",
        "energy.gov",
        (DOE_NE, *DOE_COMMON),
        ENERGY,
        parser="doe",
        sections=(("CEG", (DOE_NE, *DOE_COMMON)), ("BE", DOE_COMMON)),
    ),
    EnergySource(
        "dcd_infrastructure_rss",
        "Data Center Dynamics / Infrastructure",
        "datacenterdynamics.com",
        ("https://www.datacenterdynamics.com/en/rss/",),
        COMMON,
        route="us-standard-5",
    ),
    EnergySource(
        "data_center_frontier",
        "Data Center Frontier",
        "datacenterfrontier.com",
        (
            "https://www.datacenterfrontier.com/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22:%22home%22%7D",
        ),
        COMMON,
        route="us-standard-5",
    ),
)
SOURCES = {spec.source_id: spec for spec in CATALOG}


def endpoint_units(source, binding=None):
    spec = SOURCES[source.source_id]
    urls = spec.urls_for(binding.ticker) if binding is not None else spec.urls
    return {"FEED:" + sha256_text(url)[:24]: url for url in urls}


def initial_sources():
    return [
        SourceDefinition(
            source_id=s.source_id,
            display_name=s.name,
            adapter_ref="builtin:energy_public@1",
            kind=SourceKind.API if s.parser == "rss" else SourceKind.CRAWLER,
            acquisition_mode=s.mode,
            content_language=s.language,
            entry_url=s.urls[0],
            scheduler_group="technology." + s.domain,
            scheduler_constraints=SchedulerConstraints(
                max_concurrency=2, minimum_request_gap_seconds=0.5
            ),
            default_polling_config=PollingConfig(target_interval_seconds=60, active_windows=[]),
            distribution_policy=DistributionPolicy(jev_enabled=True, require_complete_body=True)
            if s.mode == AcquisitionMode.BY_DISTRIBUTION
            else None,
            search_policy=SearchPolicy(mode="or") if s.mode == AcquisitionMode.BY_SEARCH else None,
            parameter_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"licensees": {"type": "array", "items": {"type": "string"}}},
            }
            if s.parser == "nrc_events"
            else {"type": "object", "additionalProperties": False, "properties": {}},
            default_parameters={
                "licensees": ["Constellation Energy Generation", "Exelon Generation"]
            }
            if s.parser == "nrc_events"
            else {},
        )
        for s in CATALOG
    ]
