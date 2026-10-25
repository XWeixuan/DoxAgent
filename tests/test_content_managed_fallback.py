from types import SimpleNamespace

import pytest

from doxagent.content_enrichment.managed import SiteAccessBrowserReader, SiteAccessTransport
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.site_strategy.schema import AccessMode, AccessResult
from tests.test_content_enrichment_pipeline import BODY, TITLE, article


@pytest.mark.parametrize('browser_complete', [True, False])
async def test_real_managed_browser_adapter_preserves_pipeline_phase_list(browser_complete):
    calls = []
    class Client:
        async def resolve(self, url):
            return SimpleNamespace(
                body=SimpleNamespace(ref=None, parameters={}, access_order=['http_public','browser','reader']),
                auth=SimpleNamespace(body_requirement='none'), site_id='generic', strategy_revision=1, runtime_key='generic',
            )
        async def execute(self, request):
            calls.append(request)
            browser = request.mode == AccessMode.BROWSER
            reader = 'r.jina.ai' in request.url
            full = reader or (browser and browser_complete)
            return AccessResult(
                request_id=request.request_id, operation_id=request.operation_id,
                disposition='SUCCESS' if full else 'ACCESS_EXHAUSTED', site_id='generic',
                runtime_key='generic', strategy_revision=1, final_url=request.url,
                status_code=200 if full else 403,
                body=('# '+TITLE+'\n\n'+BODY) if reader else article() if full else 'Access denied',
                reason_code=None if full else 'http_403',
                stage_timings={'navigation_ms':45},
            )
    client = Client()
    transport = SiteAccessTransport(client)
    pipeline = ArticlePipeline(transport, browser=SiteAccessBrowserReader(client, transport), site_client=client)
    result = await pipeline.extract(MediaEnrichmentRecord('id','id','source','MU',TITLE,'','https://example.com/news/1'))
    assert result.succeeded and result.diagnostics['outcome'] == 'FULL'
    assert isinstance(result.diagnostics['stage_timings'], list)
    expected = ['http_public','browser'] + ([] if browser_complete else ['reader'])
    assert [r['phase'] for r in result.diagnostics['stage_timings']] == expected
    assert result.diagnostics['site_stage_timings'] == {'navigation_ms':45}
    assert any(r['stage_timings'] == {'navigation_ms':45} for r in result.diagnostics['site_access_trace'])
    assert len(calls) == len(expected)
