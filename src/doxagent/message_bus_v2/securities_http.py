"""Use DTCC's normal frontend search request, with its public scoped client key."""

import re
from dataclasses import replace
from datetime import timedelta
from urllib.parse import urljoin
from lxml import html

DTCC_PAGE = "https://www.dtcc.com/press-releases"
DTCC_SEARCH = "https://platform.cloud.coveo.com/rest/search/v2?organizationId=depositorytrustclearingcorporationproductionbw3fbrqw"


async def fetch_listing(adapter, recipe, spec, headers, context):
    if recipe.parser == "securities_sifma":
        taxonomy, slug, connection = (
            ("filingTypes", "letters", "advocacies")
            if "/advocacy/" in recipe.feed
            else ("newsTypes", "press-releases", "news")
        )
        query = (
            "query { "
            + taxonomy
            + '(where:{slug:["'
            + slug
            + '"]}) { nodes { name slug '
            + connection
            + "(first:40,where:{orderby:{field:DATE,order:DESC}}) { nodes { databaseId title uri dateGmt modifiedGmt } } } } }"
        )
        return await adapter._fetch(
            recipe.url, spec, headers=headers, method="POST", json_body={"query": query}
        )
    if recipe.parser != "securities_dtcc":
        return await adapter._fetch(recipe.url, spec, headers=headers, method=recipe.method)
    cached = getattr(adapter, "_dtcc_public_client", None)
    if not cached or context.requested_at - cached[0] >= timedelta(hours=1):
        frontend = replace(spec, domain="dtcc.com")
        page = await adapter._fetch(DTCC_PAGE, frontend)
        if page.status != 200:
            raise ValueError("dtcc_public_frontend_http_" + str(page.status))
        scripts = html.fromstring(page.text).xpath("//script/@src")
        webpack = next((urljoin(DTCC_PAGE, u) for u in scripts if "/webpack-" in u), None)
        if not webpack:
            raise ValueError("dtcc_public_manifest_missing")
        manifest = await adapter._fetch(webpack, frontend)
        module = re.search(r'1335:"([a-f0-9]+)"', manifest.text) if manifest.status == 200 else None
        if not module:
            raise ValueError("dtcc_press_component_manifest_changed")
        bundle = await adapter._fetch(
            "https://www.dtcc.com/_next/static/chunks/1335." + module[1] + ".js", frontend
        )
        # This exact frontend component has the PressReleaseSearchModuleBlock's
        # facets (without the broader News/Insights content-type selector).
        if (
            bundle.status != 200
            or "LabelFacetContentType" in bundle.text
            or "hideContentTypeBadge:!0" not in bundle.text
        ):
            raise ValueError("dtcc_press_component_scope_changed")
        key = re.search(r'apiKey:"([^"]+)"', bundle.text)
        if not key:
            raise ValueError("dtcc_public_press_key_missing")
        cached = (context.requested_at, key[1])
        adapter._dtcc_public_client = cached
    return await adapter._fetch(
        recipe.url,
        spec,
        headers={**headers, "Authorization": "Bearer " + cached[1]},
        method="POST",
        json_body={
            "q": "",
            "searchHub": "DTCC Opti PROD Global",
            "pipeline": "DTCC Opti PROD Global",
            "numberOfResults": 40,
            "sortCriteria": "@news_date descending",
            "fieldsToInclude": [
                "news_full_name",
                "news_short_name",
                "news_date",
                "news_short_description",
                "news_content_type",
                "classification",
                "uri",
            ],
        },
    )
