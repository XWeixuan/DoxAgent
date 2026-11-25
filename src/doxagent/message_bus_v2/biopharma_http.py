"""Native public form/search requests, using the shared HTTP pool and permits."""

import base64
import asyncio
import json
import math
import re
from dataclasses import replace
from datetime import timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

from lxml import html

from .biopharma_sources import AES_EMBED, AES_PAGE
from .schema import PollResult


def hidden_fields(raw):
    doc = html.fromstring(raw)
    return {n.get("name"): n.get("value", "") for n in doc.xpath('//input[@type="hidden"][@name]')}


async def fetch_listing(adapter, recipe, spec, headers, context):
    if recipe.parser == "bio_fierce":
        key = getattr(adapter, "_fierce_public_header", None)
        if key is None or (context.requested_at - key[0]).total_seconds() > 3600:
            # The anonymous HTML shell is challenged intermittently, while its
            # public client bundle and native APIs work without that shell.
            c = await adapter._fetch(
                "https://www.fiercebiotech.com/modules/custom/quartz_apifirst/compiled/quartz-app.js?v=1795217470",
                spec,
            )
            match = re.search(r'headers\["Quartz-Request-Key"\]="([^"]+)"', c.text)
            if c.status != 200 or not match:
                raise ValueError("fierce_public_request_header_missing")
            key = (context.requested_at, match[1])
            adapter._fierce_public_header = key
        native_headers = {**headers, "Quartz-Request-Key": key[1]}
        c = await adapter._fetch(recipe.url, spec, headers=native_headers)
        if c.status != 200:
            return c
        data = json.loads(c.text)
        if not isinstance(data.get("articles"), list) or not data.get("pager"):
            raise ValueError("fierce_native_fronts_changed")
        slots = asyncio.Semaphore(2)

        async def node(item):
            async with slots:
                result = await adapter._fetch(
                    "https://www.fiercebiotech.com/jsonapi/node/article/" + item["uuid"],
                    spec,
                    headers={"Quartz-Request-Key": key[1]},
                )
                if result.status != 200:
                    raise ValueError("fierce_native_node_http_" + str(result.status))
                value = json.loads(result.text)["data"]
                if str(value["attributes"]["drupal_internal__nid"]) != str(item["nid"]):
                    raise ValueError("fierce_node_identity_mismatch")
                return result, value

        nodes = await asyncio.gather(*(node(item) for item in data["articles"]))
        result = replace(c, text=json.dumps({"data": [v for _, v in nodes]}))
        result.listing_captures = [r for r, _ in nodes]
        return result
    if recipe.parser == "bio_hkex":
        today = context.requested_at.astimezone(ZoneInfo("Asia/Hong_Kong")).date()
        return await adapter._fetch(
            recipe.url,
            spec,
            method="POST",
            headers=headers,
            form_body={
                "lang": "EN",
                "category": "0",
                "market": "SEHK",
                "searchType": "1",
                "documentType": "-1",
                "t1code": "10000",
                "t2Gcode": "-2",
                "t2code": "-2",
                "stockId": "1000035461",
                "from": (today - timedelta(days=30)).strftime("%Y%m%d"),
                "to": today.strftime("%Y%m%d"),
            },
        )
    if recipe.parser == "bio_ash":
        # Cache only bootstrap form state, never search results or news HTML.
        form = getattr(adapter, "_ash_search_form", None)
        if form is None:
            c = await adapter._fetch(recipe.url, spec)
            if c.status != 200:
                raise ValueError("ash_bootstrap_http_" + str(c.status))
            form = hidden_fields(c.text)
            form.update(__EVENTTARGET="ctl00$ashcontent$btnSearch", __EVENTARGUMENT="")
            c = await adapter._fetch(recipe.url, spec, method="POST", form_body=form)
            if c.status != 200 or "ashcontent_tbKeyword" not in c.text:
                raise ValueError("ash_search_form_missing")
            form = hidden_fields(c.text)
            adapter._ash_search_form = form
        data = {
            **form,
            "__EVENTTARGET": "ctl00$ashcontent$btnSubmit",
            "__EVENTARGUMENT": "",
            "ctl00$ashcontent$tbKeyword": "Cullinan",
            "ctl00$ashcontent$ddlOptions": "Any",
        }
        c = await adapter._fetch(recipe.url, spec, method="POST", form_body=data)
        if "paper-details.aspx?pid=" not in c.text and not re.search(
            r"no (?:results|abstracts)|0 results", c.text, re.I
        ):
            adapter._ash_search_form = None
            raise ValueError("ash_search_session_expired_or_changed")
        return c
    if recipe.parser == "bio_aes":
        auth = getattr(adapter, "_aes_public_embed_auth", None)
        if auth is None or (context.requested_at - auth[0]).total_seconds() > 3600:
            c = await adapter._fetch(AES_EMBED, spec, headers={"Referer": AES_PAGE})
            user = re.search(r'user:\s*"([^"]+)"', c.text)
            key = re.search(r'api_key:\s*"([^"]+)"', c.text)
            if c.status != 200 or not user or not key:
                raise ValueError("aes_public_embed_unavailable")
            # Public read-only embed credential; never persist in URLs/receipts.
            auth = (
                context.requested_at,
                base64.b64encode((user[1] + ":" + key[1]).encode()).decode(),
            )
            adapter._aes_public_embed_auth = auth
        c = await adapter._fetch(
            recipe.url,
            spec,
            headers={**headers, "Referer": AES_PAGE, "Authorization": "Basic " + auth[1]},
        )
        if c.status == 200:
            c.listing_total = json.loads(c.text).get("total")
        return c
    return await adapter._fetch(recipe.url, spec, headers=headers, method=recipe.method)


async def poll_aes(adapter, context):
    from .biopharma_sources import listing_recipes

    plan = context.query_plan or {}
    queries = plan.get("queries", [])
    if not queries:
        raise ValueError("CONFIG_INCOMPLETE: AES requires the XENE unified L1 query plan")
    messages = {}
    results = []
    base = listing_recipes()["aes_abstract_search"][0]
    for query in queries:
        page = 1
        while True:
            params = {
                "query": "sid:aes doc_type=Abstracts set=vector1536 (" + query["query"] + ")",
                "per_page": 100,
                "page": page,
                "fields": "description,link,doc_date,custom_doc_date,itemdefaulturl",
                "sort": "custom_doc_date desc",
            }
            recipe = replace(
                base, url="https://fuse.fusesearch.app/api/1/series?" + urlencode(params)
            )
            result = await adapter._one(context, recipe, shared=False)
            results.append(result)
            messages.update({m.url: m for m in result.messages})
            if result.failures:
                break
            total = result.acquisition_metadata.get("listing_total")
            if total is None or page >= math.ceil(total / 100):
                break
            page += 1
    checkpoint = {
        "listings": {
            k: v for r in results for k, v in r.next_checkpoint.get("listings", {}).items()
        }
    }
    return PollResult(
        messages=list(messages.values()),
        failures=[f for r in results for f in r.failures],
        next_checkpoint=checkpoint,
        recovery_delta=checkpoint,
        window_done=all(r.window_done for r in results),
        window_coverage="PARTIAL" if any(r.failures for r in results) else "UNKNOWN",
        acquisition_metadata={
            "discovery_method": "http_listing",
            "validated_request_ids": [
                i for r in results for i in r.acquisition_metadata.get("validated_request_ids", [])
            ],
        },
    )
