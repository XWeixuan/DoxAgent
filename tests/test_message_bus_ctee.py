from __future__ import annotations

import json

from doxagent.message_bus_v2.ctee import parse_category_item, parse_category_response


def test_ctee_observed_category_payload_and_date_precision() -> None:
    row = {
        "articleID": 20260923701847,
        "categoryID": 430501,
        "title": "台積電擴大 OIP 雲端聯盟",
        "content": "半導體供應鏈新聞",
        "publishDatetime": "2026-09-23T18:19:19",
        "hyperLink": "/news/20260923701847-430501",
    }
    payload = parse_category_response("<html><body>" + json.dumps([row]) + "</body></html>")
    message = parse_category_item(payload[0])
    assert message is not None
    assert message.external_id == "20260923701847"
    assert message.publication_time_basis == "EXACT"
    assert message.published_at.hour == 10
    row["publishDatetime"] = "2026-09-23T03:00:00"
    date_only = parse_category_item(row)
    assert date_only is not None and date_only.publication_time_basis == "DATE"
    row["hyperLink"] = "https://evil.example/news/20260923701847-430501"
    assert parse_category_item(row) is None
