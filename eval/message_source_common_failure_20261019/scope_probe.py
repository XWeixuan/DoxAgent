"""Read-only effective publisher ownership checks; no browser startup or state reset."""
import json
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.resolver import SiteResolver
from doxagent.site_strategy.scope import ScopeResolver

s=DoxAgentSettings();r=SiteStrategyRepository(s.site_access_sqlite_path,read_only=True)
try:
    out=[]
    for url,origin in [
        ('https://www.barrons.com/articles/test','source:google_news_search_rss'),
        ('https://finance.yahoo.com/news/test','source:google_news_search_rss'),
        ('https://www.reuters.com/world/test','source:yahoo_finance_news'),
        ('https://www.trendforce.com/presscenter/news','source:trendforce_press_releases'),
    ]:
        base=SiteResolver(r).resolve(url)
        try:
            route=ScopeResolver(r).resolve(base,origin)
            out.append(dict(origin=origin,site=route.site_id,runtime_key=route.runtime_key,
                            identities=[c.identity_id for c in route.access.combinations]))
        except Exception as e:
            out.append(dict(origin=origin,site=base.site_id,error=type(e).__name__,
                            reason='read_only_probe_prevented_profile_provisioning'))
    print(json.dumps(out))
finally:r.close()
