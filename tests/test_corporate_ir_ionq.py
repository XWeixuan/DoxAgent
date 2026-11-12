from pathlib import Path

from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing
from doxagent.message_bus_v2.corporate_ir.body import inspect_article


def test_ionq_only_own_press_releases_and_correct_archive_pagination():
    spec = next(s for s in initial_endpoints() if s.endpoint_id == 'ionq.press')
    raw = (Path(__file__).parent / 'fixtures/corporate_ir/ionq.press.txt').read_text()
    rows, next_url = parse_listing(spec, raw)
    assert len(rows) == 2 and all(r.source == 'IONQ' for r in rows)
    assert rows[0].url == 'https://www.ionq.com/news/ionq-quantum-contract'
    assert rows[0].published_at.date().isoformat() == '2026-11-10'
    assert rows[0].publication_time_basis == 'DATE'
    assert rows[0].metadata['publication_time_precision'] == 'day'
    assert next_url == 'https://www.ionq.com/news?251779c9_page=2'


def test_ionq_body_preserves_contract_and_excludes_sidebar():
    title = 'IonQ announces quantum supply contract'
    paragraph = 'The supply contract provides $40 million in committed revenue. Delivery begins in 2027. '
    raw = '<h1>' + title + '</h1><div class="body-rich-text w-richtext">' + ''.join('<p>' + paragraph + ' Tranche ' + str(i) + '.</p>' for i in range(12)) + '</div><aside><p>Unrelated sidebar and signup forms.</p></aside>'
    candidate, quality, _ = inspect_article(raw, 'https://www.ionq.com/news/ionq-quantum-contract', title)
    assert candidate and quality == 'FULL' and '$40 million' in candidate.text
    assert '2027' in candidate.text and 'Unrelated sidebar' not in candidate.text
