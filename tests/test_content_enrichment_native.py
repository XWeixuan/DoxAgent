from __future__ import annotations

import pytest

from doxagent.content_enrichment.native import native_article
from doxagent.content_enrichment.service import ContentEnrichmentHub
from tests.test_content_enrichment_hub import _ForbiddenExtractor, _message, _setup

BODY = "Micron's price target was revised after the company's earnings report. " * 3


def message(provider="ibkr", body=BODY):
    payload = (
        {"articleId": "DJ-1", "articleType": 0, "articleText": body}
        if provider == "ibkr" else {"id": 123, "body": body}
    )
    return _message(900, body=body).model_copy(update={
        "raw_payload": payload,
        "metadata": {"provider": provider, "identity_evidence": {"url_kind": "generic"}},
    })


@pytest.mark.parametrize("provider", ["ibkr", "benzinga"])
def test_native_provider_body_is_full_evidence_even_for_short_news(provider):
    assert native_article(message(provider))


@pytest.mark.parametrize("changes", [
    {"body": None, "summary": BODY},
    {"body": "Unrelated text " * 20},
    {"metadata": {"provider": "yahoo_finance"}},
    {"raw_payload": {"articleId": "1", "articleType": 1, "articleText": BODY}},
])
def test_native_provenance_does_not_accept_summaries_or_unrelated_content(changes):
    assert native_article(message().model_copy(update=changes)) is None


@pytest.mark.parametrize("body", ["Short headline", BODY + "...", BODY + " Continue Reading"])
def test_native_truncated_body_still_needs_completion(body):
    assert native_article(message(body=body)) is None


async def test_native_body_bypasses_network_and_preserves_raw_identity(tmp_path):
    repository, bus, source, binding = _setup(tmp_path)
    original = message()
    bus.enqueue_enrichment(source=source, binding=binding, message=original,
                           bootstrap=False, poll_run_id="native")
    hub = ContentEnrichmentHub(repository, bus, extractor=_ForbiddenExtractor())
    assert await hub.run_once() == 1
    raw = repository.list_raw(ticker="MU")[0]
    assert raw.body == BODY.strip()
    assert raw.external_id == original.external_id and raw.url == original.url
    enrichment = raw.metadata["media_enrichment"]
    assert enrichment["succeeded"] and enrichment["body_source"] == "provider_article_api"
    assert enrichment["attempts"] == []
