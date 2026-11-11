"""Latest's official Artemis snapshot + by-id metadata, using shared HTTP polling."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from urllib.parse import quote

from .bloomberg_latest import ENTRY_URL, parse_stories
from .newswire_listing import cache_evidence
from .schema import AcquisitionFailure, PollResult, canonical_json, sha256_text, utc_now

BASE = "https://www.bloomberg.com"
TOPICS = '[{"providerName":"STORIES_SESSION","topics":["LATEST_STORIES"]}]'
ROUTE = "jp-standard-6"


def topic_checksum(text):
    # The publisher's public Artemis client uses cyrb53 for this cache/routing key.
    h1, h2 = 3735928559, 1103547991
    for char in text:
        h1 = ((h1 ^ ord(char)) * 2654435761) & 0xffffffff
        h2 = ((h2 ^ ord(char)) * 1597334677) & 0xffffffff
    h1 = (((h1 ^ (h1 >> 16)) * 2246822507) ^ ((h2 ^ (h2 >> 13)) * 3266489909)) & 0xffffffff
    h2 = (((h2 ^ (h2 >> 16)) * 2246822507) ^ ((h1 ^ (h1 >> 13)) * 3266489909)) & 0xffffffff
    return 4294967296 * (2097151 & h2) + h1


POLL_URL = BASE + "/artemis/oscillator/poll/" + str(topic_checksum(TOPICS))


def parse_snapshot(text):
    data = json.loads(text)
    if not isinstance(data, list) or len(data) != 1 or data[0].get("providerName") != "STORIES_SESSION":
        raise ValueError("bloomberg_realtime_invalid_provider")
    topic = data[0].get("topicsData", {}).get("LATEST_STORIES", {})
    rows = topic.get("data")
    if topic.get("name") != "LATEST_STORIES" or not isinstance(rows, list) or not rows:
        raise ValueError("bloomberg_realtime_missing_topic")
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r"[A-Z0-9]{14}", row.get("id", "")):
            raise ValueError("bloomberg_realtime_invalid_id")
        if datetime.fromisoformat(row["publishedAt"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("bloomberg_realtime_missing_timezone")
    return rows


def failure_reason(exc):
    reason = str(exc)
    if isinstance(exc, ValueError) and reason.startswith(("bloomberg_", "metadata_")):
        return type(exc).__name__ + ":" + reason[:200]
    # Transport errors must never serialize a private authorization header.
    return type(exc).__name__ + ":" + str(getattr(exc, "code", ""))


def parse_metadata_batch(text, requested, source):
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("metadata_invalid_batch")
    values = [row["value"] for row in data if isinstance(row, dict) and row.get("status") == "fulfilled"]
    rows = parse_stories(json.dumps(values), source)
    if any(row.external_id not in requested for row in rows):
        raise ValueError("metadata_identity_mismatch")
    return rows


async def poll_realtime(adapter, context):
    from .collection_override import CollectionStopped
    from .poll_trace import REQUESTS, validate_listing
    from .publisher_http import network_timeout

    previous = context.checkpoint or {}
    seen = dict(previous.get("seen", {}))
    pending = dict(previous.get("pending", {}))
    state = getattr(adapter, "_bloomberg_realtime_session", None)
    if state is None:
        state = adapter._bloomberg_realtime_session = {"token": None, "warmed": False}
    events = REQUESTS.get()
    start_event = len(events or [])
    started = time.monotonic()
    parsed_ids, caches, messages, errors = [], [], [], []
    snapshot_valid = False

    async def get(url, *, token=False, metadata=False):
        offset = len(events or [])
        headers = {"Referer": ENTRY_URL}
        if token:
            headers["x-artemis-topics"] = TOPICS
            if state["token"] is not True:
                headers["x-access-token"] = state["token"]
        recipe = {"method": "GET", "routes": [ROUTE]}
        if metadata:
            recipe.update(routes=["direct"], client_scope="bloomberg-public-metadata", discard_cookies=True)
        response, _ = await adapter.listing_request(context, url, recipe, headers)
        ids = [e["physical_request_id"] for e in (events or [])[offset:] if e.get("status_code") == 200]
        return response, ids

    async def authorize():
        if not state["warmed"]:
            response, _ = await get(ENTRY_URL)
            if response.status_code != 200:
                raise ValueError("bloomberg_realtime_session_http_" + str(response.status_code))
            state["warmed"] = True
        response, _ = await get(BASE + "/artemis/sentry")
        if response.status_code != 200:
            state["warmed"] = False
            raise ValueError("bloomberg_realtime_authorization_http_" + str(response.status_code))
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("bloomberg_realtime_invalid_authorization")
        token = data.get("token") or True
        if not isinstance(token, (str, bool)) or isinstance(token, str) and any(c in token for c in "\r\n"):
            raise ValueError("bloomberg_realtime_invalid_token")
        state["token"] = token  # Private, memory only; never checkpointed.

    try:
        async with network_timeout(12):
            if state["token"] is None:
                await authorize()
            response, ids = await get(POLL_URL, token=True)
            if response.status_code == 401:
                # Expiration of an anonymous publisher token is not a new source outage.
                state["token"] = None
                await authorize()
                response, ids = await get(POLL_URL, token=True)
            evidence = cache_evidence(response, POLL_URL, "GET")
            caches.append(evidence)
            if response.status_code != 200:
                if response.status_code in {401, 403, 412}:
                    state["token"] = None
                raise ValueError("bloomberg_realtime_http_" + str(response.status_code))
            if evidence.get("error") or evidence.get("validation", {}).get("observed_age_seconds", 0) > 45:
                raise ValueError("bloomberg_realtime_stale_cache")
            rows = parse_snapshot(response.text)
            parsed_ids.extend(ids)
            snapshot_valid = True
            observed = max((e["response_received_at"] for e in events or []
                            if e.get("physical_request_id") in ids and e.get("response_received_at")),
                           default=utc_now().isoformat())
            fresh = [row["id"] for row in rows if row["id"] not in seen]
            for row in rows:
                if row["id"] not in seen:
                    pending.setdefault(row["id"], {**row, "id_first_seen_at": observed})
            if len(pending) > 128:
                raise ValueError("bloomberg_realtime_pending_overflow")
            # New HEAD ids precede retries of older metadata, bounded to one small batch.
            identifiers = list(dict.fromkeys([*fresh, *pending]))[:10]
            if identifiers:
                # The official client also provides one batch endpoint. Its tested
                # route avoids the repeatedly blocked single-id query and five serial RTTs.
                url = BASE + "/lineup-next/api/stories/" + ",".join(identifiers) + "?autofillData=" + quote('{"locale":"en"}', safe="")
                response, ids = await get(url, metadata=True)
                if response.status_code != 200:
                    raise ValueError("metadata_http_" + str(response.status_code))
                candidates = parse_metadata_batch(response.text, set(identifiers), context.source)
                parsed_ids.extend(ids)
                for message in candidates:
                    identifier = message.external_id
                    item = pending[identifier]
                    message.metadata.update(discovery_method="bloomberg_realtime_http",
                                            id_first_seen_at=item["id_first_seen_at"])
                    message.raw_payload.update(realtime_published_at=item["publishedAt"], listing_url=POLL_URL)
                    messages.append(message)
                    seen[identifier] = item["publishedAt"]
                    pending.pop(identifier)
                for identifier in identifiers:
                    if identifier in pending:
                        errors.append(identifier + ":metadata_unavailable")
    except CollectionStopped:
        raise
    except Exception as exc:
        errors.append(failure_reason(exc))

    proven = validate_listing(events or [], parsed_ids, since=start_event) if snapshot_valid else []
    checkpoint = {"seen": dict(list(seen.items())[-512:]), "pending": pending} if snapshot_valid else previous
    metadata = {"method": "bloomberg_realtime_http", "route": ROUTE, "metadata_route": "direct",
                "request_seconds": round(time.monotonic() - started, 3), "cache": caches,
                "delta_items": len(messages), "pending": len(pending),
                "validated_request_ids": [e["physical_request_id"] for e in proven]}
    if snapshot_valid and context.on_page is not None:
        # Commit valid progress before a sibling metadata failure. Reuse the existing
        # page/capture transaction, not a new queue or a success-shaped error.
        await context.on_page(messages, checkpoint=checkpoint, validated_request_ids=metadata["validated_request_ids"])
    failures = []
    if errors:
        metadata["error"] = ";".join(errors[:5])
        failures.append(AcquisitionFailure(source_id=context.source.source_id,
            binding_id="shared:"+context.source.source_id, ticker="*", error_code="BLOOMBERG_REALTIME_FAILED",
            error_message=metadata["error"], raw_hash=sha256_text(canonical_json(errors)),
            original_payload={"cache": caches, "pending_count": len(pending)}))
    return PollResult(messages=messages, next_checkpoint=checkpoint, failures=failures,
                      window_done=not failures, window_coverage="PARTIAL", acquisition_metadata=metadata)
