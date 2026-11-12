from ..schema import PollingConfig, SchedulerConstraints, SourceDefinition, SourceKind
from . import ADAPTER_REF, SOURCE_ID
from .schema import IrEndpointSpec
from .snowflake import LISTING_URL as SNOW_LISTING_URL, HOST as SNOW_API_HOST


def initial_endpoints():
    rows = [
        (
            "intc.press",
            "INTC",
            "Intel press releases",
            "https://www.intc.com/news-events/press-releases/rss",
            "rss",
            "rss",
            ["www.intc.com"],
        ),
        (
            "mu.ir",
            "MU",
            "Micron IR",
            "https://investors.micron.com/rss/pressrelease.aspx",
            "rss",
            "rss",
            ["investors.micron.com"],
        ),
        (
            "mu.newsroom",
            "MU",
            "Micron newsroom",
            "https://www.micron.com/about/press/news",
            "html",
            "micron",
            ["www.micron.com", "investors.micron.com"],
        ),
        (
            "be.press",
            "BE",
            "Bloom Energy press releases",
            "https://investor.bloomenergy.com/rss/pressrelease.aspx",
            "rss",
            "rss",
            ["investor.bloomenergy.com"],
        ),
        (
            "cohr.corporate",
            "COHR",
            "Coherent corporate releases",
            "https://ir.coherent.com/news-events/corporate-releases",
            "html",
            "coherent",
            ["ir.coherent.com"],
        ),
        (
            "cohr.financial",
            "COHR",
            "Coherent financial releases",
            "https://ir.coherent.com/news-events/financial-releases",
            "html",
            "coherent",
            ["ir.coherent.com"],
        ),
        (
            "nbis.press",
            "NBIS",
            "Nebius press releases",
            "https://nebius.com/newsroom",
            "html",
            "nebius",
            ["nebius.com"],
        ),
        (
            "ceg.press",
            "CEG",
            "Constellation press releases",
            "https://www.constellationenergy.com/content/constellationenergy/paths/newsroom.json?sortorder=desc&limit=6&offset=1",
            "json",
            "ceg",
            ["www.constellationenergy.com"],
        ),
        (
            "rklb.ir",
            "RKLB",
            "Rocket Lab IR",
            "https://investors.rocketlabcorp.com/rss.xml",
            "rss",
            "rss",
            ["investors.rocketlabcorp.com"],
        ),
        (
            "rklb.updates",
            "RKLB",
            "Rocket Lab updates",
            "https://rocketlabcorp.com/updates/",
            "html",
            "rocketlab",
            ["rocketlabcorp.com"],
        ),
        (
            "snow.press",
            "SNOW",
            "Snowflake press releases",
            SNOW_LISTING_URL,
            "json",
            "snowflake_json",
            ["www.snowflake.com", "investors.snowflake.com", SNOW_API_HOST],
        ),
        (
            "onds.press",
            "ONDS",
            "Ondas press releases",
            "https://ir.ondas.com/rss",
            "rss",
            "rss",
            ["ir.ondas.com"],
        ),
        (
            "ionq.press",
            "IONQ",
            "IonQ press releases",
            "https://www.ionq.com/news?251779c9_page=1",
            "html",
            "ionq",
            ["www.ionq.com", "ionq.com"],
        ),
    ]
    specs = [
        IrEndpointSpec(
            endpoint_id=k,
            owner_ticker=t,
            display_name=n,
            entry_url=u,
            method=m,
            parser_ref=p,
            allowed_article_hosts=h,
        )
        for k, t, n, u, m, p, h in rows
    ]
    specs = [
        s.model_copy(update={"fallback_urls": ["https://investors.rocketlabcorp.com/news"]})
        if s.endpoint_id == "rklb.ir"
        else s
        for s in specs
    ]
    stdlib_hosts = {
        "mu.ir": ["investors.micron.com"],
        "mu.newsroom": ["investors.micron.com"],
        "be.press": ["investor.bloomenergy.com"],
        "rklb.updates": ["rocketlabcorp.com"],
    }
    specs = [
        s.model_copy(
            update={
                "parser_parameters": {"urllib_hosts": stdlib_hosts[s.endpoint_id]},
                "revision": 2,
            }
        )
        if s.endpoint_id in stdlib_hosts
        else s
        for s in specs
    ]
    specs = [s.model_copy(update={'revision': 3}) if s.endpoint_id == 'snow.press' else s for s in specs]
    return [s.model_copy(update={
        'parser_parameters': {**s.parser_parameters, 'http_rss_race': True},
        'revision': max(3, s.revision),
    }) if s.endpoint_id in {'intc.press', 'mu.ir', 'be.press', 'rklb.ir', 'onds.press'} else s
        for s in specs]


def initial_source():
    return SourceDefinition(
        source_id=SOURCE_ID,
        display_name="企业 IR / Press Releases",
        kind=SourceKind.API,
        adapter_ref=ADAPTER_REF,
        scheduler_group=SOURCE_ID,
        scheduler_constraints=SchedulerConstraints(
            max_concurrency=6, minimum_request_gap_seconds=0
        ),
        default_polling_config=PollingConfig(target_interval_seconds=30),
        default_parameters={"endpoints": [s.model_dump(mode="json") for s in initial_endpoints()]},
        parameter_schema={
            "type": "object",
            "properties": {
                "endpoint_ids": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                }
            },
            "required": ["endpoint_ids"],
            "additionalProperties": False,
        },
    )
