import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing
from doxagent.message_bus_v2.corporate_ir.body import IrBodyReader
from doxagent.message_bus_v2.corporate_ir.http import Capture
from doxagent.message_bus_v2.corporate_ir.snowflake import article_html, model_url
from doxagent.message_bus_v2.schema import RawMessageInput
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

URL = 'https://www.snowflake.com/en/news/press-releases/company-agreement/'
TITLE = 'Snowflake announces company agreement'


def model():
    text = ''.join('<p>Contract section '+str(i)+' commits $80 million to data services. '
                   'Delivery begins in 2027 and includes customer support.</p>' for i in range(10))
    return {'canonicalLink': URL, ':items': {
        'hero': {':type': 'snowflake-site/components/hero-system', 'headline': {'lines': [TITLE]}},
        'columns': {':type': 'snowflake-site/components/flexible-column-container',
                    'flexible_column_content_container_1': {':items': {'body': {
                        ':type': 'snowflake-site/components/text', 'text': text, 'richText': True}}},
                    'flexible_column_content_container_2': {':items': {'sidebar': {
                        ':type': 'snowflake-site/components/text', 'text': '<p>Unrelated sidebar</p>', 'richText': True}}}},
        'footer': {':type': 'cq/experience-fragments/editor/components/experiencefragment',
                   'text': '<p>Newsletter signup</p>'}}}


def test_model_article_rejects_other_canonical_and_discards_sidebar():
    result = article_html(model(), URL)
    assert '$80 million' in result and '2027' in result
    assert 'Unrelated sidebar' not in result and 'Newsletter signup' not in result
    with pytest.raises(ValueError, match='identity mismatch'):
        article_html(model(), URL+'other')


@pytest.mark.asyncio
async def test_snowflake_body_reads_public_model_and_reports_actual_source():
    spec = next(s for s in initial_endpoints() if s.endpoint_id == 'snow.press')
    raw = json.dumps(model())
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(200, model_url(URL), raw, {}, len(raw))))
    message = RawMessageInput(title=TITLE, url=URL, raw_payload={}, metadata={'ir_endpoint_spec': spec.model_dump(mode='json')})
    record = MediaEnrichmentRecord('id','id','corporate_ir_press_releases','SNOW',TITLE,'',URL)
    result = await IrBodyReader(http).extract(SimpleNamespace(message=message,binding=SimpleNamespace(ticker='SNOW')), record)
    assert result.succeeded and result.diagnostics['outcome'] == 'FULL'
    assert result.final_url == URL and result.diagnostics['body_source_url'] == model_url(URL)
    http.fetch.assert_awaited_once_with(spec, url=model_url(URL))


def test_snowflake_json_uses_dates_and_distinct_offset_page():
    from pathlib import Path
    spec = next(s for s in initial_endpoints() if s.endpoint_id == 'snow.press')
    rows, following = parse_listing(spec, Path('tests/fixtures/corporate_ir/snow.model.txt').read_text())
    assert len(rows) == 2 and 'offset=2' in following
    assert rows[0].published_at.date().isoformat() == '2026-11-03'
    assert rows[0].publication_time_basis == 'DATE'
