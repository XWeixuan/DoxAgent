"""Normalize source-specific payloads into bus-standard messages."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, cast

from doxagent.monitoring.schema import (
    EndpointKind,
    JsonObject,
    MonitoringSourceConfig,
    RawExternalMessage,
    StandardMessage,
    new_monitoring_id,
)


def normalize_message(
    raw: RawExternalMessage,
    source: MonitoringSourceConfig,
) -> StandardMessage:
    payload = raw.raw_payload
    if source.endpoint_kind is EndpointKind.BENZINGA_NEWS:
        fields = _normalize_benzinga(payload)
    elif source.endpoint_kind is EndpointKind.FINNHUB_COMPANY_NEWS:
        fields = _normalize_finnhub(payload)
    elif source.endpoint_kind is EndpointKind.STOCKTWITS_MESSAGES:
        fields = _normalize_stocktwits(payload)
    elif source.endpoint_kind in {
        EndpointKind.TIKHUB_X_SEARCH,
        EndpointKind.TIKHUB_X_USER_POSTS,
    }:
        fields = _normalize_tikhub_x(payload)
    elif source.endpoint_kind is EndpointKind.RSS_FEED:
        fields = _normalize_rss(payload)
    else:
        fields = _normalize_generic(payload)

    published_at = _first_datetime(fields.get("published_at")) or raw.source_published_at
    url = _first_str(fields.get("url")) or raw.source_url
    symbols = _symbols(fields.get("symbols")) or [raw.ticker]
    keywords = _strings(fields.get("keywords"))
    metadata = {
        "normalizer": source.endpoint_kind.value,
        "source_display_name": source.display_name,
        **dict(fields.get("metadata") or {}),
        **raw.metadata,
    }
    return StandardMessage(
        standard_message_id=new_monitoring_id("std"),
        raw_message_id=raw.raw_message_id,
        source_id=raw.source_id,
        binding_id=raw.binding_id,
        ticker=raw.ticker,
        source_type=raw.source_type,
        interface_type=raw.interface_type,
        title=_first_str(fields.get("title")),
        body=_first_str(fields.get("body")),
        url=url,
        author=_first_str(fields.get("author")),
        symbols=symbols,
        keywords=keywords,
        username=_first_str(fields.get("username")),
        published_at=published_at,
        collected_at=raw.collected_at,
        provider_message_id=raw.provider_message_id,
        metadata=metadata,
    )


def _normalize_benzinga(payload: JsonObject) -> JsonObject:
    stocks = payload.get("stocks")
    channels = payload.get("channels")
    tags = payload.get("tags")
    return {
        "title": payload.get("title"),
        "body": payload.get("body") or payload.get("teaser"),
        "url": payload.get("url"),
        "author": payload.get("author"),
        "symbols": [_named_item(item) for item in _objects(stocks)],
        "keywords": [_named_item(item) for item in _objects(channels)]
        + [_named_item(item) for item in _objects(tags)],
        "published_at": payload.get("created") or payload.get("updated"),
        "metadata": {
            "benzinga_id": payload.get("id"),
            "channels": channels,
            "tags": tags,
        },
    }


def _normalize_finnhub(payload: JsonObject) -> JsonObject:
    return {
        "title": payload.get("headline"),
        "body": payload.get("summary"),
        "url": payload.get("url"),
        "author": payload.get("source"),
        "symbols": _split_symbols(payload.get("related")),
        "published_at": payload.get("datetime"),
        "metadata": {
            "finnhub_id": payload.get("id"),
            "category": payload.get("category"),
            "image": payload.get("image"),
        },
    }


def _normalize_stocktwits(payload: JsonObject) -> JsonObject:
    user = _object(payload.get("user"))
    sentiment = _object(_object(payload.get("entities")).get("sentiment"))
    symbols = payload.get("symbols") or _object(payload.get("entities")).get("symbols")
    return {
        "title": _first_str(payload.get("title")) or _first_str(payload.get("body")),
        "body": payload.get("body") or payload.get("message"),
        "url": payload.get("url") or payload.get("source", {}).get("url")
        if isinstance(payload.get("source"), dict)
        else payload.get("url"),
        "author": user.get("name") or user.get("username"),
        "username": user.get("username"),
        "symbols": [_named_item(item) for item in _objects(symbols)],
        "published_at": payload.get("created_at") or payload.get("createdAt"),
        "metadata": {
            "stocktwits_id": payload.get("id") or payload.get("messageId"),
            "sentiment": sentiment.get("basic") or payload.get("sentiment"),
            "likes": payload.get("likes") or payload.get("like_count"),
            "replies": payload.get("conversation", {}).get("replies")
            if isinstance(payload.get("conversation"), dict)
            else None,
        },
    }


def _normalize_tikhub_x(payload: JsonObject) -> JsonObject:
    legacy = _object(payload.get("legacy"))
    user = _object(payload.get("user")) or _object(payload.get("core"))
    full_text = (
        payload.get("full_text")
        or payload.get("text")
        or payload.get("content")
        or legacy.get("full_text")
        or legacy.get("text")
    )
    username = (
        payload.get("screen_name")
        or payload.get("username")
        or legacy.get("screen_name")
        or _deep_get(user, ("screen_name", "username", "name"))
    )
    return {
        "title": _first_str(full_text),
        "body": full_text,
        "url": payload.get("url") or payload.get("expanded_url"),
        "author": username,
        "username": username,
        "published_at": (
            payload.get("created_at")
            or payload.get("createdAt")
            or legacy.get("created_at")
            or payload.get("time")
        ),
        "metadata": {
            "tikhub_id": payload.get("id")
            or payload.get("rest_id")
            or payload.get("tweet_id")
            or legacy.get("id_str"),
            "raw_user": user,
        },
    }


def _normalize_rss(payload: JsonObject) -> JsonObject:
    return {
        "title": payload.get("title"),
        "body": payload.get("summary") or payload.get("description"),
        "url": payload.get("link") or payload.get("id"),
        "author": payload.get("author") or payload.get("source"),
        "published_at": (
            payload.get("published") or payload.get("updated") or payload.get("pubDate")
        ),
        "metadata": {
            "rss_guid": payload.get("guid") or payload.get("id"),
            "feed_url": payload.get("feed_url"),
        },
    }


def _normalize_generic(payload: JsonObject) -> JsonObject:
    return {
        "title": payload.get("title") or payload.get("headline") or payload.get("name"),
        "body": payload.get("body") or payload.get("summary") or payload.get("text"),
        "url": payload.get("url") or payload.get("link"),
        "author": payload.get("author") or payload.get("source"),
        "published_at": payload.get("published_at") or payload.get("created_at"),
        "metadata": {},
    }


def _first_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=UTC)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list | tuple | set):
        result: list[str] = []
        for item in value:
            text = _first_str(item)
            if text:
                result.append(text)
        return result
    text = _first_str(value)
    return [text] if text else []


def _symbols(value: object) -> list[str]:
    return [item.strip().upper().lstrip("$") for item in _strings(value) if item.strip()]


def _split_symbols(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        separators = value.replace(";", ",").replace(" ", ",").split(",")
        return [item.strip().upper().lstrip("$") for item in separators if item.strip()]
    return _symbols(value)


def _objects(value: object) -> list[JsonObject]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [dict(value)]
    return []


def _object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def _named_item(value: JsonObject) -> str:
    for key in ("name", "symbol", "ticker", "title", "id"):
        item = _first_str(value.get(key))
        if item:
            return item
    return ""


def _deep_get(value: JsonObject, keys: tuple[str, ...]) -> object | None:
    queue: list[Any] = [value]
    while queue:
        item = queue.pop(0)
        if isinstance(item, dict):
            for key in keys:
                if key in item:
                    return cast(object, item[key])
            queue.extend(item.values())
        elif isinstance(item, list):
            queue.extend(item)
    return None
