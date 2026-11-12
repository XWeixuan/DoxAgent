"""Article-bound public publisher documents; no browser or identity management."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from lxml import html as html_parser


def host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


PUBLISHER_ROOTS = frozenset(
    {
        "barrons.com",
        "marketwatch.com",
        "fool.com",
        "stocktwits.com",
        "stocktitan.net",
        "investors.com",
        "chosun.com",
        "syracuse.com",
        "moomoo.com",
        "futunn.com",
        "thestreet.com",
        "seekingalpha.com",
        "investing.com",
        "reuters.com",
        "benzinga.com",
        "techpowerup.com",
        "gurufocus.com",
        "streetinsider.com",
    }
)


def publisher_host(url: str) -> str:
    value = host(url)
    return next(
        (root for root in PUBLISHER_ROOTS if value == root or value.endswith("." + root)), value
    )


def same_article(left: str, right: str) -> bool:
    a, b = urlsplit(left), urlsplit(right)
    if a.scheme not in {"http", "https"} or b.scheme not in {"http", "https"}:
        return False

    def significant(query: str) -> list[tuple[str, str]]:
        return sorted(
            (k, v)
            for k, v in parse_qsl(query)
            if not k.lower().startswith("utm_") and k not in {"fbclid", "gclid"}
        )

    return (
        host(left) == host(right)
        and unquote(a.path).rstrip("/") == unquote(b.path).rstrip("/")
        and significant(a.query) == significant(b.query)
    )


def body_nodes(root: Any, url: str) -> list[Any]:
    publisher = publisher_host(url)
    if publisher == "barrons.com" and "/articles/" in urlsplit(url).path:
        paragraphs = root.xpath(
            '//article[@id]//*[@data-type="paragraph"]'
            '[not(ancestor::*[contains(concat(" ",normalize-space(@class)," ")," crawler ")])]'
        )
        if not paragraphs:
            paragraphs = root.xpath('//article[@id]//*[@data-type="paragraph"]')
        # Paragraph-only scopes exclude recommendation cards and copyright teasers.
        return paragraphs
    selectors = {
        "marketwatch.com": '//*[@id="js-article__body"]|//*[contains(@class,"article__body")]',
        "fool.com": '//*[@id="article-body"]|//*[@data-testid="article-body"]',
        "stocktwits.com": '//*[@data-testid="article-body"]',
        "streetinsider.com": '(//div'
        '[contains(concat(" ",normalize-space(@class)," ")," article_body ")])[1]',
        "techpowerup.com": "//article"
        '[contains(concat(" ",normalize-space(@class)," ")," newspost ")]'
        '//*[contains(concat(" ",normalize-space(@class)," ")," text ")]',
        "gurufocus.com": "//div"
        '[contains(concat(" ",normalize-space(@class)," ")," article-container ")]',
        "moomoo.com": "//div"
        '[contains(concat(" ",normalize-space(@class)," ")," feed-detail-preset ")]',
    }
    return root.xpath(selectors[publisher]) if publisher in selectors else []


def _text(markup: str) -> str:
    try:
        root = html_parser.fromstring("<div>" + markup + "</div>")
        for node in root.xpath(".//script|.//style"):
            node.drop_tree()
        return re.sub(r"[ \t]+", " ", root.text_content()).strip()
    except (ValueError, html_parser.etree.ParserError):
        return ""


def _arc_elements(items: list[Any]) -> list[str]:
    result = []
    for item in items:
        if isinstance(item, str):
            if text := _text(item):
                result.append(text)
            continue
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind in {"text", "header", "quote", "raw_html"}:
            value = item.get("content")
            if isinstance(value, str):
                text = _text(value)
                if text:
                    result.append(text)
            if isinstance(item.get("content_elements"), list):
                result.extend(_arc_elements(item["content_elements"]))
        elif kind == "list" and isinstance(item.get("items"), list):
            result.extend(_arc_elements(item["items"]))
        elif kind == "table":
            for row in [item.get("header", []), *item.get("rows", [])]:
                if isinstance(row, list):
                    cells = [
                        _text(str(c.get("content", "") if isinstance(c, dict) else c)) for c in row
                    ]
                    if any(cells):
                        result.append(" | ".join(cells))
    return result


def apply_document(
    root: Any,
    url: str,
    expected_title: str | None,
    info: Any,
    *,
    title_match: Any,
    candidate_type: Any,
    node_text: Any,
) -> Any | None:
    publisher, path = publisher_host(url), urlsplit(url).path
    if publisher == "barchart.com":
        nodes = root.xpath(
            '//*[contains(concat(" ",normalize-space(@class)," ")," article-content ")]'
        )
        if nodes:
            info.candidates = [
                candidate_type(
                    node_text(nodes[0]), "barchart_article_body", True, info.headline, 40
                )
            ]
            return nodes[0]  # Footer Continue Reading is outside the actual article.
    if publisher == "gurufocus.com":
        for node in body_nodes(root, url):
            for widget in node.xpath(
                './/a[contains(@class,"el-popover__reference")]//*'
                '[contains(@class,"gf-score-badge-wrapper") '
                'or contains(@class,"span-red") or contains(@class,"span-green")]'
            ):
                widget.drop_tree()
            text = node_text(node)
            info.candidates = [
                candidate_type(text, "gurufocus_article_body", True, info.headline, 40)
            ]
            return node
    if publisher == "moomoo.com" and "/community/feed/" in path:
        posts = root.xpath(
            '//div[contains(concat(" ",normalize-space(@class)," ")," feed-detail-preset ")]'
        )
        if posts and len(node_text(posts[0])) < 120 and not posts[0].xpath(".//img|.//video|.//a"):
            info.page_kind, info.access_reason = "social_post", "short_social_post"
            info.candidates = []
            return posts[0]
    if publisher == "stocktitan.net" and re.search(r"/sec-filings/[^/]+/form-4-", path):
        for node in root.xpath(
            '//*[@id="sec-filing-content"]//*'
            '[contains(concat(" ",normalize-space(@class)," ")," filing-content-inner ")]'
        ):
            text = node_text(node)
            if (
                re.search(r"FORM\s*4", text, re.I)
                and re.search(r"SECURITIES\s+AND\s+EXCHANGE\s+COMMISSION", text, re.I)
                and re.search(r"Signature", text, re.I)
            ):
                info.candidates = [
                    candidate_type(text, "stocktitan_embedded_form4", True, info.headline, 40)
                ]
                info.page_kind = "article"
                # The original filing is present. Its SEC attribution is a citation,
                # not evidence that this complete document is merely a summary.
                return node
    if publisher == "barrons.com" and "/articles/" in path:
        rendered = root.xpath('//article[@data-dox-barrons-visible="v1"]')
        if rendered:
            # Only the browser-produced visible snapshot can certify this path.
            # Raw HTML retaining the publisher's `crawler` class stays conservative.
            info.access_reason = None if rendered[0].get('data-dox-barrons-complete') == 'true' else 'subscription_required'
            if info.access_reason is None:
                info.subscription_article = False
        body = root.xpath('//article[@id]//*[@data-type="paragraph"]')
        live = root.xpath(
            '//article[@id]//*[@data-type="paragraph"]'
            '[not(ancestor::*[contains(concat(" ",normalize-space(@class)," ")," crawler ")])]'
        )
        if body and not live and info.subscription_article:
            # This observed crawler document contains only a preview. Its large
            # recommendation subtree must never turn it into a paid FULL body.
            info.subscription_article = True
            info.access_reason = "subscription_required"
        if body:
            text = "\n\n".join(node_text(p) for p in (live or body))
            info.candidates = [
                candidate_type(text, "barrons_article_paragraphs", True, info.headline, 35)
            ]
    if publisher == "investors.com":
        if "/ibd-videos/" in path:
            nodes = root.xpath(
                '//*[@id="transcript"]|//*'
                '[contains(concat(" ",normalize-space(@class)," ")," video-transcript ")]'
            )
            info.page_kind = "media"
            info.candidates = [
                candidate_type(
                    "Transcript\n\n" + node_text(n),
                    "ibd_public_transcript",
                    True,
                    info.headline,
                    35,
                )
                for n in nodes
                if len(node_text(n)) >= 120
            ]
        elif any(
            c.method == "article_dom" and re.search(r"…\s+Copyright\s*©", c.text)
            for c in info.candidates[:1]
        ):
            info.subscription_article = True
            info.access_reason = "subscription_required"
    if publisher not in {"chosun.com", "syracuse.com"}:
        return None
    for script in root.xpath("//script/text()"):
        match = re.search(r"(?:window\.)?Fusion\.globalContent\s*=\s*", script)
        if not match:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(script[match.end() :])
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("type") != "story":
            continue
        headlines = data.get("headlines")
        headline = headlines.get("basic") if isinstance(headlines, dict) else None
        canonical = data.get("canonical_url") or data.get("website_url")
        if (
            not isinstance(headline, str)
            or not expected_title
            or not title_match(headline, expected_title)
            or not isinstance(canonical, str)
            or not same_article(urljoin(url, canonical), url)
        ):
            continue
        if data.get("content_restrictions") or data.get("isAccessibleForFree") is False:
            info.subscription_article = True
            info.access_reason = "subscription_required"
            continue
        elements = data.get("content_elements")
        if not isinstance(elements, list):
            continue
        body = "\n\n".join(_arc_elements(elements))
        if body:
            info.headline, info.canonical, info.page_kind = (
                headline,
                urljoin(url, canonical),
                "article",
            )
            info.candidates.insert(0, candidate_type(body, "arc_public_story", True, headline, 40))


READER_HEADER_HOSTS = frozenset(
    {
        "barrons.com",
        "marketwatch.com",
        "thestreet.com",
        "seekingalpha.com",
        "investing.com",
        "reuters.com",
        "streetinsider.com",
        "stocktwits.com",
    }
)

# Verified public routes. These do not relax configured authentication or create browsers.
PUBLIC_READER_FIRST_HOSTS = frozenset({"benzinga.com"})
READER_STAGE_SECONDS = {"benzinga.com": 4.0, "futunn.com": 12.0}
