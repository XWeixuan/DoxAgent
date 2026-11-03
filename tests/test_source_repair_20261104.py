from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2.technology_sources import SOURCES, TechnologyHttp
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService
from doxagent.site_strategy.governance_repository import GovernanceRepository
from scripts.ops.configure_sdxcentral_browser import configure


def test_sdx_governance_uses_real_listing_contract_instead_of_http_status():
    from doxagent.site_strategy.verification import validate_crawler_capture
    raw = (Path(__file__).parent / "fixtures/technology_sources/sdxcentral_news.html").read_text(encoding="utf-8")
    response = SimpleNamespace(body=raw, final_url=SOURCES["sdxcentral_news"].urls[0],
        recipe_result=None, recovery_id=None, assessment={})
    request = SimpleNamespace(url=response.final_url, recipe_ref=None, recipe_parameters={})
    assert validate_crawler_capture("sdxcentral_news", response, request)
    response.body = '<html><title>Just a moment...</title><body>Performing security verification</body></html>'
    with pytest.raises(ValueError, match="listing_empty_or_changed"):
        validate_crawler_capture("sdxcentral_news", response, request)


def test_sdx_identity_configuration_is_isolated_and_idempotent(tmp_path):
    repository = SiteStrategyRepository(tmp_path / "site.sqlite3")
    bootstrap_seed(repository, SiteStrategyService(repository, profile_root=tmp_path / "profiles"))
    configure(repository)
    scope = GovernanceRepository(repository).get_scope("source:sdxcentral_news")
    assert scope.primary_identity_id == "sdxcentral-news-main"
    assert scope.fallback_identity_id == "sdxcentral-news-backup"
    primary = repository.get_identity(scope.primary_identity_id)
    backup = repository.get_identity(scope.fallback_identity_id)
    assert primary.profile_id != backup.profile_id
    assert primary.egress_id != backup.egress_id
    assert primary.runtime_kind == backup.runtime_kind == "external_chrome"
    assert backup.lifecycle.idle_seconds == 120
    configure(repository)
    assert repository.get_identity(primary.identity_id).revision == primary.revision
    assert GovernanceRepository(repository).get_scope(scope.scope_id).revision == scope.revision
    repository.close()


@pytest.mark.parametrize("purpose", ["CRAWLER", "BODY"])
async def test_sdx_browser_transport_uses_same_owned_scope_for_listing_and_body(purpose):
    result = SimpleNamespace(disposition=SimpleNamespace(value="SUCCESS"),
        status_code=200, final_url="https://www.sdxcentral.com/news/example/",
        body="<article>news</article>", headers={})
    client = SimpleNamespace(execute=AsyncMock(return_value=result), close=AsyncMock())
    http = TechnologyHttp(purpose=purpose, browser_client=client)
    capture = await http.fetch(result.final_url, SOURCES["sdxcentral_news"])
    request = client.execute.await_args.args[0]
    assert request.origin_scope_id == "source:sdxcentral_news"
    assert request.purpose.value == purpose and request.mode.value == "BROWSER"
    assert capture.status == 200 and capture.text == result.body
    await http.close()
    client.close.assert_awaited_once()
