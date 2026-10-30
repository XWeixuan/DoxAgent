"""Yahoo's public Content Gateway ticker stream, independent of Chrome and RSS."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

ENDPOINT = "https://nexus-finance-prod.media.yahoo.com/"


def is_ticker_request(request, ticker):
    parsed = urlsplit(request.url)
    if parsed.hostname != 'nexus-finance-prod.media.yahoo.com' or parsed.path != '/':
        return False
    try:
        body = json.loads(request.post_data or '{}')
        return bool(re.match(r'query\s+(?:FinancePolarisTicker(?:List)?News|GetQSPLeafNewsStream)\b',
                             body.get('query', ''))
                    and body['variables']['listInput']['queryVariables']['tickerSymbol'] == [ticker.upper()])
    except (ValueError, KeyError, TypeError, AttributeError):
        return False

# Same typed list used by Yahoo's loadQSPLeafNews; omit images, ads and ranking.
QUERY = """query GetQSPLeafNewsStream($listInput:LightyearListInput!,$clientContext:ClientContext!,$start:Int!,$count:Int!) {
 lightyearList(list_input:$listInput,cc:$clientContext,start:$start,count:$count) {
 main:contentItems {stream:assets {asset {__typename
 ... on Story {id:uuid title contentAttributes {pubDate:publishTime canonicalUrl clickthroughUrl(cc:$clientContext) summary provider {displayName url}}}
 ... on Video {id:uuid title contentAttributes {pubDate:publishTime canonicalUrl clickthroughUrl(cc:$clientContext) summary provider {displayName url}}}
 ... on Outlink {uuid headline displayTime url description provider {displayName url}}
 }} pageInfo {totalCount start count nextPage:hasNextPage}}}}
"""


def variables(ticker: str, *, start=0, count=50) -> dict:
    return {
        "clientContext": {"device": "desktop", "region": "US", "site": "finance", "lang": "en-US"},
        "listInput": {
            "slug": "list=finance-US-en-US-ticker-all",
            "queryVariables": {"tickerSymbol": [ticker.upper()]},
            "assetTypes": ["story", "video"],
            "disableDedupe": False,
            "enableBlockedContent": False,
            "filterClientContext": False,
        },
        "start": start,
        "count": count,
    }


def parse_stream(payload: dict) -> tuple[list[dict], dict]:
    """Do not turn GraphQL HTTP-200 errors/partial responses into empty success."""
    try:
        if payload.get("errors"):
            raise ValueError("yahoo_graphql_errors")
        main = payload["data"]["lightyearList"]["main"]
        modern = 'edges' in main
        stream = [edge['node'] for edge in main['edges']] if modern else main['stream']
        page = main['pagination'] if modern else main['pageInfo']
        if not isinstance(stream, list) or not isinstance(page, dict):
            raise ValueError("yahoo_graphql_schema_changed")
        if not isinstance(page.get("nextPage"), bool) or (not modern and not isinstance(page.get("count"), int)):
            raise ValueError("yahoo_graphql_pagination_changed")
        rows = []
        for node in stream:
            asset = node.get("asset")
            if not isinstance(asset, dict):
                raise ValueError("yahoo_graphql_missing_asset")
            kind = asset.get("__typename")
            if kind in {"Story", "Video"}:
                attributes = asset.get("contentAttributes") or {}
                row = {
                    "id": asset.get("id"), "title": asset.get("title"),
                    "pubDate": attributes.get("pubDate"),
                    "link": attributes.get("canonicalUrl") or attributes.get("clickthroughUrl"),
                    "summary": attributes.get("summary"), "provider": attributes.get("provider"),
                }
            elif kind == "Outlink":
                row = {"id": asset.get("uuid"), "title": asset.get("headline"),
                       "pubDate": asset.get("displayTime"), "link": asset.get("url"),
                       "summary": asset.get("description"), "provider": asset.get("provider")}
            else:
                raise ValueError("yahoo_graphql_unknown_asset")
            if not row["id"] or not row["title"] or not row["link"]:
                raise ValueError("yahoo_graphql_incomplete_article")
            rows.append(row)
        return rows, page
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("yahoo_graphql_schema_changed") from exc


async def acquire(transport, ticker, *, start=0, count=50):
    response = await transport.request(
        "POST", ENDPOINT, json={"query": QUERY, "variables": variables(ticker, start=start, count=count)},
        headers={"x-yahoo-cg-client-name": "finance"}, rate_scope="graphql", timeout=8,
    )
    rows, page = parse_stream(response.json())
    return rows, {
        "endpoint": "yahoo_content_gateway", "capture_method": "graphql_http",
        "page_info": page, "listing_item_count": len(rows),
        "cache_validators": {k: response.headers[k] for k in ("date", "age", "cache-control")
                             if k in response.headers},
    }
