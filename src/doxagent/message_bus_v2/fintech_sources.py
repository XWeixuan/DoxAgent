"""Fintech publisher recipes on the existing shared HTTP discovery and BODY lanes."""
from dataclasses import dataclass

from .schema import AcquisitionMode, DistributionPolicy, PollingConfig, SourceDefinition, SourceKind, SchedulerConstraints
from .technology_sources import TechnologySource


@dataclass(frozen=True)
class FintechSource(TechnologySource):
    mode: AcquisitionMode = AcquisitionMode.BY_DISTRIBUTION


CATALOG = (
    FintechSource('theblock_crypto', 'The Block / News', 'theblock.co', ('https://www.theblock.co/news',), ('COIN',), route='us-standard-5', parser='fintech_block'),
    FintechSource('coindesk_crypto', 'CoinDesk / Policy, Finance & Tech', 'coindesk.com', tuple('https://www.coindesk.com/'+p for p in ('policy','business','tech')), ('COIN',), route='us-standard-5', parser='fintech_coindesk'),
    FintechSource('rg_prediction_markets', 'RG / Prediction Markets', 'rg.org', ('https://rg.org/news/prediction-markets',), ('COIN',), route='us-standard-5', parser='fintech_rg'),
    FintechSource('unchained_crypto', 'Unchained / Regulation, Business & DeFi', 'unchainedcrypto.com', tuple('https://unchainedcrypto.com/category/'+p+'/feed/' for p in ('regulation','business','defi')), ('COIN',), route='us-standard-5', parser='fintech_unchained'),
    FintechSource('decrypt_crypto', 'Decrypt / News, Business, Technology & DeFi', 'decrypt.co', tuple('https://decrypt.co/news'+p for p in ('','/business','/technology','/defi')), ('COIN',), parser='fintech_decrypt'),
    FintechSource('cointelegraph_crypto', 'Cointelegraph / Regulation, Coinbase & Prediction Markets', 'cointelegraph.com', tuple('https://cointelegraph.com/tags/'+p for p in ('regulation','coinbase','prediction-markets')), ('COIN',), parser='fintech_cointelegraph'),
    FintechSource('bankingdive_fintech', 'Banking Dive / Fintech', 'bankingdive.com', ('https://www.bankingdive.com/topic/fintech/',), ('COIN',), parser='fintech_banking', transport='urllib'),
    FintechSource('bleepingcomputer_crypto', 'BleepingComputer / Cryptocurrency', 'bleepingcomputer.com', ('https://www.bleepingcomputer.com/news/cryptocurrency/',), ('COIN',), route='us-standard-5', parser='fintech_bleeping'),
    FintechSource('sbcamericas_prediction_markets', 'SBC Americas / Prediction Markets', 'sbcamericas.com', ('https://sbcamericas.com/category/prediction-markets/feed/',), ('COIN',), route='us-standard-5', parser='rss'),
    FintechSource('ledgerinsights_news', 'Ledger Insights / News', 'ledgerinsights.com', ('https://www.ledgerinsights.com/category/news/',), ('COIN',), route='us-standard-5', parser='fintech_ledger'),
    FintechSource('thedefiant_fintech_regulation', 'The Defiant / TradFi & Fintech, Regulation & Politics', 'thedefiant.io', tuple('https://thedefiant.io/news/'+p for p in ('tradfi-and-fintech','regulation')), ('COIN',), route='us-standard-5', parser='fintech_defiant'),
    FintechSource('cftc_trading_rules', 'CFTC / Coinbase & Kalshi Trading Organization Rules', 'cftc.gov', tuple('https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizationRules?Organization='+p+'&dir=DESC&col=Date' for p in ('COIN','KEX')), ('COIN',), route='us-standard-5', transport='urllib', parser='fintech_cftc_rules', mode=AcquisitionMode.BY_TICKER),
    FintechSource('cftc_press_releases', 'CFTC / Press Releases', 'cftc.gov', ('https://www.cftc.gov/PressRoom/PressReleases',), ('COIN',), route='us-standard-5', transport='urllib', parser='fintech_cftc_press'),
    FintechSource('occ_newsroom', 'OCC / Newsroom', 'occ.gov', ('https://www.occ.gov/news-events/newsroom/?q=&nr=NewsRelease,Bulletin,Speech,CongressionalTestimony,AdvisoryLetter&topic=&dte=0',), ('COIN',), parser='fintech_occ'),
)
SOURCES = {s.source_id: s for s in CATALOG}
OCC_API = 'https://apps.occ.gov/Occ.FusionServices.WebApi.Public/api/NewsItems/Search'
OCC_TYPES = ('NewsRelease', 'Bulletin', 'Speech', 'CongressionalTestimony', 'AdvisoryLetter')


def initial_sources():
    return [SourceDefinition(
        source_id=s.source_id, display_name=s.name, adapter_ref='builtin:technology_public@1',
        kind=SourceKind.CRAWLER, acquisition_mode=s.mode, content_language='en', entry_url=s.urls[0].removesuffix('feed/'),
        scheduler_group='technology.'+s.domain,
        scheduler_constraints=SchedulerConstraints(max_concurrency=2, minimum_request_gap_seconds=0.5),
        default_polling_config=PollingConfig(target_interval_seconds=20, active_windows=[]),
        default_parameters={'discovery_mode':'http_listing'},
        parameter_schema={'type':'object','additionalProperties':False,'properties':{'discovery_mode':{'const':'http_listing'}}},
        distribution_policy=DistributionPolicy(jev_enabled=s.mode.shared_distribution, require_complete_body=True),
    ) for s in CATALOG]


def listing_recipes():
    from urllib.parse import urlencode
    from .public_listing import Listing
    recipes = {}
    for s in CATALOG:
        entries=[]
        for feed in s.urls:
            url, method, fresh, fallback = feed, 'GET', False, False
            if s.parser in {'fintech_block','fintech_coindesk','fintech_rg','fintech_decrypt','fintech_cointelegraph','fintech_ledger','fintech_defiant'}:
                method='POST'
            if s.parser=='fintech_unchained':
                topic=feed.removesuffix('feed/').rstrip('/').split('/')[-1]
                url='https://unchainedcrypto.com/wp-admin/admin-ajax.php?'+urlencode(dict(
                    action='alm_get_posts', query_type='standard', id='', posts_per_page=20, page=0,
                    offset=0, post_type='post,podcast,choppingblock,review,video,learn,uneasymoney,dex',
                    theme_repeater='archive.php', taxonomy='category', taxonomy_terms=topic,
                    taxonomy_operator='IN', order='DESC', orderby='date'))
                fallback=True
            if s.parser=='fintech_banking':
                fresh=True
            if s.source_id=='sbcamericas_prediction_markets':
                url=feed.removesuffix('feed/');fallback=True
            if s.parser=='fintech_occ':
                url=OCC_API;method='POST'
            entries.append(Listing(feed,url,s.parser if s.parser!='rss' else 'fintech_sbc',
                s.route,s.transport,fresh,max_age_seconds=30,method=method,rss_fallback=fallback,
                cache_diagnostic_only=True,
                coverage_review='HTTP listing needs browser review; preserve scoped RSS fallback'
                if s.source_id in {'unchained_crypto','sbcamericas_prediction_markets'} else None))
        recipes[s.source_id]=tuple(entries)
    return recipes
