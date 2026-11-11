"""Parallel discovery paths; a fast validated list commits without waiting for its sibling."""

from __future__ import annotations

import asyncio
from .publisher_http import network_timeout
import copy
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin

from lxml import html

from .public_listing import Listing
from .schema import AcquisitionFailure, PollResult, sha256_text, utc_now
from .technology_sources import REGISTER

RECIPES = {
    "unmannedairspace_counteruas": tuple(
        Listing(
            "https://www.unmannedairspace.info/category/" + section + "/feed/",
            "https://www.unmannedairspace.info/category/" + section + "/",
            "race_wp_archive",
            fresh_query=True,
        )
        for section in ("counter-uas-systems-and-policies", "counter-uas-systems-tenders")
    ),
    "register_software_cloud_security": tuple(
        Listing(
            REGISTER + query,
            (REGISTER + query).replace("remapper=rss&", "").replace("limit=50", "limit=30")
            + "&fields=id,title,published,published_url,subtitle",
            "race_register",
            "us-standard-5",
            fresh_query=True,
        )
        for query in ("tag:software", "(tag:off-prem%20OR%20tag:off_prem)", "tag:security")
    ),
    "nbis_official_actor_rss": (
        Listing(
            "https://clickhouse.com/rss.xml",
            "https://clickhouse.com/blog",
            "race_clickhouse",
            fresh_query=True,
        ),
        Listing(
            "https://about.fb.com/news/feed/",
            "https://about.fb.com/wp-json/wp/v2/posts?per_page=30&_fields=id,link,title,date_gmt,modified_gmt,excerpt",
            "wp_rest",
            fresh_query=True,
        ),
    ),
    "siliconangle_software": tuple(
        Listing(
            "https://siliconangle.com/category/" + category + "/feed/",
            "https://siliconangle.com/wp-json/wp/v2/posts?categories="
            + key
            + "&per_page=30&_fields=id,link,title,date_gmt,modified_gmt,excerpt",
            "wp_rest",
            fresh_query=True,
        )
        for category, key in (("big-data", "1505426"), ("cloud", "603"), ("security", "1507578"))
    ),
    "siliconangle_ai": (
        Listing(
            "https://siliconangle.com/category/ai/feed/",
            "https://siliconangle.com/wp-json/wp/v2/posts?categories=369&per_page=30&_fields=id,link,title,date_gmt,modified_gmt,excerpt",
            "wp_rest",
            fresh_query=True,
        ),
    ),
    "wccftech_hardware": tuple(
        Listing(
            "https://wccftech.com/" + path + "/feed/",
            "https://wccftech.com/" + path + "/",
            "race_wccftech",
            "us-standard-5",
            fresh_query=True,
        )
        for path in ("topic/ai", "topic/industry", "department/hardware")
    ),
    "airspaceforces_space": (
        Listing(
            "https://www.airandspaceforces.com/category/space/feed/",
            "https://www.airandspaceforces.com/wp-json/wp/v2/posts?categories=8&per_page=30&_fields=id,link,title,date_gmt,modified_gmt,excerpt",
            "wp_rest",
            fresh_query=True,
        ),
    ),
    "ans_nuclear_newswire": (
        Listing(
            "https://www.ans.org/news/feed/",
            "https://www.ans.org/news/",
            "race_ans",
            fresh_query=True,
        ),
    ),
    "quantum_insider": tuple(
        Listing(
            "https://thequantuminsider.com/category/" + category + "/feed/",
            "https://thequantuminsider.com/wp-json/wp/v2/posts?categories="
            + key
            + "&per_page=30&_fields=id,link,title,date_gmt,modified_gmt,excerpt",
            "wp_rest",
            "us-standard-5",
            fresh_query=True,
        )
        for category, key in (
            ("daily/business", "21"),
            ("daily/national", "9291"),
            ("capital-markets", "9288"),
        )
    ),
    "power_magazine_energy": tuple(
        Listing(
            "https://www.powermag.com/category/" + category + "/feed/",
            "https://www.powermag.com/category/" + category + "/",
            "race_power",
            "us-standard-5",
            fresh_query=True,
        )
        for category in (
            "nuclear",
            "data-centers",
            "legal-regulatory",
            "safety",
            "distributed-generation",
            "energy-storage-2",
            "hydrogen",
        )
    ),
}

# Endpoint recipes remain inside the existing corporate IR source identity.
IR_RECIPES = {
    "intc.press": Listing(
        "https://www.intc.com/news-events/press-releases/rss",
        "https://www.intc.com/news-events/press-releases",
        "race_ir_intel",
        fresh_query=True,
    ),
    "rklb.ir": Listing(
        "https://investors.rocketlabcorp.com/rss.xml",
        "https://investors.rocketlabcorp.com/news",
        "race_ir_nir",
        fresh_query=True,
    ),
    **{
        key: Listing(
            feed,
            "https://" + host + "/feed/PressRelease.svc/GetPressReleaseList"
            "?LanguageId=1&bodyType=0&pressReleaseDateFilter=1"
            "&categoryId=00000000-0000-0000-0000-000000000000&year=-1&pageNumber=0&pageSize=30&includeTags=true",
            "race_ir_q4",
            "us-standard-5",
            fresh_query=True,
        )
        for key, host, feed in (
            ("mu.ir", "investors.micron.com", "https://investors.micron.com/rss/pressrelease.aspx"),
            (
                "be.press",
                "investor.bloomenergy.com",
                "https://investor.bloomenergy.com/rss/pressrelease.aspx",
            ),
        )
    },
}


def enabled(source):
    return (
        source.source_id in RECIPES
        and source.default_parameters.get("discovery_mode") == "http_rss_race"
    )


def endpoint_units(source, binding=None):
    recipes = RECIPES[source.source_id]
    if binding is not None:
        feeds = binding.source_parameters.get("rss_urls", source.default_parameters.get("rss_urls"))
        if source.source_id == "power_magazine_energy":
            from .energy_catalog import SOURCES

            feeds = SOURCES[source.source_id].urls_for(binding.ticker)
        if feeds is not None:
            by_feed = {r.feed: r for r in recipes}
            recipes = [
                by_feed.get(
                    feed,
                    Listing(
                        feed,
                        feed,
                        "race_rss_only",
                        coverage_review="No verified low-cost HTTP entry; retain subscribed RSS",
                    ),
                )
                for feed in feeds
            ]
    return {"FEED:" + sha256_text(r.feed)[:24]: r for r in recipes}


async def execute(context, runners):
    """Keep lane checkpoints/errors separate; never cancel a healthy slow coverage path."""
    from .poll_trace import REQUESTS, validate_listing
    from .collection_override import CollectionStopped

    outer = REQUESTS.get()
    previous = context.checkpoint.get("race_lanes") or {"rss": dict(context.checkpoint)}
    checkpoints = dict(previous)
    observations, messages, ids = {}, [], []
    winner = None

    async def one(name, runner):
        events = []
        token = REQUESTS.set(events)
        started = time.monotonic()
        try:
            async with network_timeout(30 if name == "rss" else 15):
                result = await runner(previous.get(name, {}))
            proven = (
                validate_listing(events, result.acquisition_metadata.get("validated_request_ids"))
                if not result.failures and not result.site_access_deferred
                else []
            )
            success = bool(proven) and not result.failures and not result.site_access_deferred
            error = (
                None
                if success
                else ";".join(f.error_message for f in result.failures)
                or result.site_access_deferred_reason
                or "listing_not_transport_validated"
            )
            return name, result, events, proven, success, error, time.monotonic() - started
        except CollectionStopped:
            raise
        except Exception as exc:
            return (
                name,
                PollResult(),
                events,
                [],
                False,
                type(exc).__name__ + ":" + str(exc)[:240],
                time.monotonic() - started,
            )
        finally:
            REQUESTS.reset(token)

    tasks = [asyncio.create_task(one(name, runner)) for name, runner in runners.items()]
    try:
        for task in asyncio.as_completed(tasks):
            name, result, events, proven, success, error, seconds = await task
            for event in events:
                event["race_lane"] = name
            if outer is not None:
                outer.extend(events)
            observations[name] = {
                "success": success,
                "error": error,
                "seconds": round(seconds, 3),
                "items": len(result.messages),
                "validated_request_ids": [p["physical_request_id"] for p in proven],
            }
            if not success:
                from .collection_plane.worker import recovery_checkpoint

                checkpoints[name] = recovery_checkpoint(
                    previous.get(name, {}), result.recovery_delta or result.next_checkpoint
                )
                continue
            winner = winner or name
            checkpoints[name] = result.next_checkpoint
            ids.extend(p["physical_request_id"] for p in proven)
            for message in result.messages:
                message.metadata["discovery_lane"] = name
            messages.extend(result.messages)
            if context.on_page is not None:
                await context.on_page(
                    result.messages,
                    checkpoint={"race_lanes": checkpoints},
                    validated_request_ids=[p["physical_request_id"] for p in proven],
                )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    failures = []
    if winner is None:
        failures = [
            AcquisitionFailure(
                source_id=context.source.source_id,
                binding_id=getattr(
                    getattr(context, "binding", None),
                    "binding_id",
                    "shared:" + context.source.source_id,
                ),
                ticker=getattr(context, "ticker", "*"),
                error_code="HTTP_RSS_BOTH_FAILED",
                error_message="; ".join(
                    name + ":" + str(o["error"]) for name, o in observations.items()
                ),
                raw_hash=sha256_text(context.acquisition_unit),
                original_payload={"race": observations},
            )
        ]
    return PollResult(
        messages=messages,
        failures=failures,
        window_done=bool(winner),
        next_checkpoint={"race_lanes": checkpoints},
        acquisition_metadata={
            "validated_request_ids": ids,
            "race": {"winner": winner, "channels": observations},
        },
    )


async def poll(adapter, context, *, shared):
    units = endpoint_units(context.source, getattr(context, "binding", None))
    recipe = units.get(context.acquisition_unit)
    if recipe is None:
        raise ValueError("race_requires_one_subscribed_feed_unit")

    async def http(checkpoint):
        c = copy.copy(context)
        c.checkpoint = checkpoint
        result = await adapter._one(c, recipe, shared=shared, fallback=False)
        # A new transport is not a reason to re-download every old known story.
        # RSS retains its original coverage; HTTP emits recent discoveries and
        # explicit recent publisher updates, without rewriting publication time.
        from .admission import evaluate_admission

        recent = []
        for m in result.messages:
            updated = m.raw_payload.get("updated_at")
            try:
                stamp = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
                stamp = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
                is_update = (
                    context.requested_at - timedelta(minutes=30) <= stamp <= context.requested_at
                )
            except ValueError:
                is_update = False
            if (
                is_update
                or evaluate_admission(
                    m.published_at, None, context.requested_at, m.publication_time_basis
                )
                is None
            ):
                recent.append(m)
        result.messages = recent
        if context.source.adapter_ref in {"builtin:newswire_rss", "builtin:newswire_rss@1"}:
            # Match the existing ticker RSS relay's identity contract, including
            # its provider GUID fallback for /blog/ URLs without hard URL aliases.
            for m in result.messages:
                m.metadata.pop("identity_evidence", None)
                m.external_id = m.source_item_key = m.raw_payload.get("rss_guid") or m.url
        return result

    async def rss(checkpoint):
        c = copy.copy(context)
        c.checkpoint = checkpoint
        c.on_page = None
        result = await (adapter.delegate.poll_shared(c) if shared else adapter.delegate.poll(c))
        if not result.failures:
            # Generic ticker RSS relays return the whole feed every time. Keep
            # their BODY metadata, but emit only changes instead of 900 old rows.
            from .schema import canonical_json
            from .body_results import update_hint

            seen = dict(checkpoint.get("_race_seen", {}))
            changed = []
            for m in result.messages:
                stamp = sha256_text(
                    canonical_json(
                        [
                            update_hint(m),
                            m.published_at,
                            m.raw_payload.get("content_hash"),
                            m.raw_payload.get("updated_at"),
                            sha256_text(str(m.raw_payload.get("feed_html", ""))),
                        ]
                    )
                )
                if seen.get(m.url) != stamp or getattr(c, "force_refresh", False):
                    changed.append(m)
                seen[m.url] = stamp
            result = result.model_copy(
                update={
                    "messages": changed,
                    "next_checkpoint": {
                        **result.next_checkpoint,
                        "_race_seen": dict(list(seen.items())[-1024:]),
                    },
                }
            )
        return result

    runners = {"http": http, "rss": rss} if recipe.parser != "race_rss_only" else {"rss": rss}
    result = await execute(context, runners)
    if recipe.parser == "race_rss_only":
        result.acquisition_metadata["race"]["channels"]["http"] = {
            "enabled": False,
            "reason": recipe.coverage_review,
        }
    return result


async def poll_ir(collector, adapter, spec, context):
    from .public_listing import ListingAdapter
    from .corporate_ir.schema import IrEndpointState
    from .admission import evaluate_admission

    recipe = IR_RECIPES[spec.endpoint_id]
    listing = ListingAdapter(adapter, adapter.listing_http)

    async def http(checkpoint):
        c = copy.copy(context)
        c.checkpoint = checkpoint
        result = await listing._one(c, recipe, shared=False, fallback=False)
        for m in result.messages:
            if spec.endpoint_id == "mu.ir":
                # Q4's current RSS declares HTTP permalinks. Preserve the
                # existing article key; BODY upgrades only the network request.
                m.url = m.url.replace(
                    "https://investors.micron.com/", "http://investors.micron.com/", 1
                )
            # The original frozen IR recipe still owns BODY. Do not introduce
            # the generic technology parser's language/access/URL capabilities.
            m.external_id = m.url
            m.source_item_key = None
            m.source = m.publisher_name = spec.owner_ticker
            m.raw_payload = {"guid": None, "article_url": m.url, "feed_html": ""}
            m.metadata = {
                "provider": "corporate_ir",
                "ir_endpoint_id": spec.endpoint_id,
                "ir_endpoint_revision": spec.revision,
                "ir_endpoint_spec": spec.model_dump(mode="json"),
                "acquisition_method": "http_listing",
                "feed_url": recipe.url,
                "time_origin": m.published_at.isoformat(),
                "publication_time_precision": "exact"
                if m.publication_time_basis == "EXACT"
                else "day",
                "ir_body_field": "",
            }
        result.messages = [
            m
            for m in result.messages
            if evaluate_admission(
                m.published_at, None, context.requested_at, m.publication_time_basis
            )
            is None
        ]
        return result

    async def rss(checkpoint):
        async with context.request_permit():
            return await collector.ir(adapter, spec, context.binding, checkpoint)

    result = await execute(context, {"http": http, "rss": rss})
    state = result.next_checkpoint["race_lanes"].get("rss", {}).get(
        "ir_state"
    ) or context.checkpoint.get("ir_state")
    state = IrEndpointState.model_validate(
        state or {"binding_id": context.binding.binding_id, "endpoint_id": spec.endpoint_id}
    )
    if not result.failures:
        state = state.model_copy(
            update={
                "last_success_at": utc_now(),
                "status": "succeeded",
                "bootstrap_complete": True,
                "method_used": "http_rss_race",
            }
        )
    result.next_checkpoint["ir_state"] = state.model_dump(mode="json")
    return result


def parse_listing(recipe, spec, raw, now):
    from .technology_sources import article
    from .public_listing import text

    doc = None if recipe.parser in {"race_register", "race_ir_q4"} else html.fromstring(raw)
    rows = []
    if recipe.parser == "race_register":
        spec = replace(spec, domain="theregister.com")
        for row in json.loads(raw)["result"]:
            rows.append(
                (
                    "https://www.theregister.com" + row["published_url"],
                    row["title"],
                    row["published"],
                )
            )
    elif recipe.parser == "race_wccftech":
        for node in doc.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," post ")]'):
            links = node.xpath(".//h3/a[@href]")
            dates = node.xpath(".//*[@data-time]/@data-time")
            if links and dates:
                rows.append((links[0].get("href"), text(links[0]), dates[0]))
    elif recipe.parser == "race_ans":
        for node in doc.xpath('//article[starts-with(@id,"article_")]'):
            links = node.xpath('.//h2[@class="headline"]/a[@href]')
            dates = node.xpath(".//*[@data-epoch]/@data-epoch")
            if links and dates:
                rows.append(
                    (
                        links[0].get("href"),
                        text(links[0]),
                        datetime.fromtimestamp(float(dates[0]), UTC).isoformat(),
                    )
                )
    elif recipe.parser == "race_wp_archive":
        for node in doc.xpath(
            '//article[contains(concat(" ",normalize-space(@class)," ")," small-archive-post ")]'
        ):
            links = node.xpath('.//h2[contains(@class,"entry-title")]/a[@href]')
            dates = node.xpath('.//time[contains(@class,"published")]/@datetime')
            if links and dates:
                rows.append((links[0].get("href"), text(links[0]), dates[0]))
    elif recipe.parser == "race_clickhouse":
        import re
        from dateutil.parser import parse

        for link in doc.xpath(
            '//h2/a[starts-with(@href,"/blog/")]|//h3/a[starts-with(@href,"/blog/")]'
        ):
            node = link.getparent()
            for _ in range(3):
                match = re.search(r"\b[A-Z][a-z]{2} \d{1,2}, \d{4}\b", text(node))
                if match:
                    rows.append((link.get("href"), text(link), parse(match[0]).date().isoformat()))
                    break
                node = node.getparent()
    elif recipe.parser == "race_power":
        import re
        from dateutil.parser import parse

        for node in doc.xpath("//time/../.."):
            links = node.xpath(".//h3/a[@data-postid][@href]")
            dates = node.xpath(".//time")
            if links and dates:
                date = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text(dates[0]))
                rows.append((links[0].get("href"), text(links[0]), parse(date).date().isoformat()))
    elif recipe.parser in {"race_ir_intel", "race_ir_q4"}:
        from dateutil.parser import parse
        from zoneinfo import ZoneInfo

        if recipe.parser == "race_ir_q4":
            for item in json.loads(raw)["GetPressReleaseListResult"]:
                stamp = parse(item["PressReleaseDate"]).replace(tzinfo=ZoneInfo("America/New_York"))
                rows.append((item["LinkToDetailPage"], item["Headline"], stamp.isoformat()))
        else:
            for node in doc.xpath('//article[contains(@class,"media-container")]'):
                links = node.xpath('.//*[contains(@class,"media-title")]/a[@href]')
                dates = node.xpath(".//time/@datetime")
                if links and dates:
                    stamp = parse(dates[0]).replace(tzinfo=ZoneInfo("America/New_York"))
                    rows.append((links[0].get("href"), text(links[0]), stamp.isoformat()))
    elif recipe.parser == "race_ir_nir":
        from .corporate_ir.parsers import parse_listing as parse_ir
        from .corporate_ir.schema import IrEndpointSpec

        native, _ = parse_ir(
            IrEndpointSpec(
                endpoint_id="rklb.ir",
                owner_ticker="RKLB",
                display_name="Rocket Lab IR",
                entry_url=recipe.url,
                method="html",
                parser_ref="coherent",
                allowed_article_hosts=["investors.rocketlabcorp.com"],
            ),
            raw,
        )
        rows = [(m.url, m.title, m.published_at.date().isoformat()) for m in native]
    else:
        raise ValueError("unknown_race_parser:" + recipe.parser)
    result = []
    for url, title, date in rows:
        m = article(
            spec, urljoin(recipe.url, url), title, date, now, payload={"listing_url": recipe.url}
        )
        if m:
            m.metadata.update(discovery_method="http_listing", listing_url=recipe.url)
            result.append(m)
    if not result:
        raise ValueError("race_listing_empty_or_changed:" + recipe.parser)
    return list({m.url: m for m in result}.values())
