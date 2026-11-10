"""Approved first-round collection cadence, 2026-11-11; no admission/identity changes."""

RETIRED_SOURCES = frozenset({"tikhub_x_search", "tikhub_x_user_posts", "globenewswire_semiconductors_rss"})

SOURCE_INTERVALS = {'airspaceforces_space': 20,
 'ans_nuclear_newswire': 20,
 'army_news_releases': 20,
 'army_news_rss': 20,
 'aviationweek_rocketlab': 20,
 'aws_platform_marketplace_rss': 20,
 'barrons_ticker_news': 20,
 'benzinga_news': 15,
 'breakingdefense_space': 20,
 'canary_media': 20,
 'corporate_ir_press_releases': 20,
 'crn_cloud_security_datacenter': 20,
 'darpa_news': 20,
 'data_center_frontier': 20,
 'dcd_energy_ticker': 20,
 'dcd_infrastructure_rss': 20,
 'defensescoop_drones': 20,
 'defensescoop_space': 20,
 'digitimes_tw_rss': 20,
 'dod_contracts': 20,
 'dod_releases': 20,
 'doe_energy_news': 20,
 'enterprise_apps_official_rss': 20,
 'etnews_rss': 20,
 'finnhub_company_news': 30,
 'globenewswire_search': 20,
 'google_news_search_rss': 20,
 'huggingnews_rss': 20,
 'ibkr_news': 20,
 'investing_ticker_news': 30,
 'investorshub_ticker_news': 30,
 'ionq_nautilus_rss': 20,
 'ionq_quantum_actors_rss': 20,
 'iridium_press_releases': 20,
 'lakehouse_platform_official_rss': 20,
 'nasa_2026_releases': 20,
 'national_quantum_initiative': 20,
 'nbis_official_actor_rss': 20,
 'nist_news': 20,
 'nrc_event_notifications': 20,
 'nrc_news_releases': 20,
 'nro_press_releases': 20,
 'nsf_news': 20,
 'optical_systems_official_rss': 20,
 'power_engineering_energy': 20,
 'power_magazine_energy': 20,
 'quantum_computing_report': 20,
 'quantum_insider': 20,
 'register_software_cloud_security': 20,
 'sdxcentral_news': 30,
 'sec_edgar': 10,
 'shephard_uncrewed': 20,
 'siliconangle_ai': 20,
 'siliconangle_software': 20,
 'spacecom_launches': 20,
 'ssc_newsroom': 20,
 'technews_hardware': 20,
 'thefly_ticker_news': 30,
 'thelec_semiconductors_rss': 20,
 'tomshardware_rss': 20,
 'trendforce_news': 20,
 'trendforce_press_releases': 20,
 'unmannedairspace_counteruas': 20,
 'utility_dive': 20,
 'venturebeat_ai': 20,
 'venturebeat_software': 20,
 'wccftech_hardware': 20,
 'world_nuclear_news': 20,
 'yahoo_finance_news': 30}

IR_INTERVALS = {'be.press': 30, 'ceg.press': 20, 'cohr.corporate': 20, 'cohr.financial': 20, 'intc.press': 20, 'mu.ir': 30, 'mu.newsroom': 20, 'nbis.press': 20, 'onds.press': 20, 'rklb.ir': 20, 'rklb.updates': 20, 'snow.press': 20}

def unit_interval(source, unit, base):
    # The existing shared feed unit is stable; one slow endpoint must not slow siblings.
    if source.source_id == "enterprise_apps_official_rss":
        from .schema import sha256_text
        if unit == "FEED:" + sha256_text("https://newsroom.workday.com/press-releases?pagetemplate=rss")[:24]:
            return max(30, base)
    return base


def source_defaults(source):
    if source.source_id not in SOURCE_INTERVALS:
        return source
    updates = {"default_polling_config": source.default_polling_config.model_copy(
        update={"target_interval_seconds": SOURCE_INTERVALS[source.source_id]})}
    if source.source_id == "corporate_ir_press_releases":
        parameters = dict(source.default_parameters)
        parameters["endpoints"] = [{**entry, "target_interval_seconds": IR_INTERVALS.get(
            entry["endpoint_id"], entry["target_interval_seconds"])} for entry in parameters["endpoints"]]
        updates["default_parameters"] = parameters
    return source.model_copy(update=updates)
