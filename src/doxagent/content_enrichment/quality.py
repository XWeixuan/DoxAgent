"""Article-scoped candidates and deterministic completeness evidence for V2 only."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import html as html_parser  # type: ignore[import-untyped]

from doxagent.content_enrichment.strategies import legacy_strategy_ref
from doxagent.content_enrichment.strategies.adapters import (
    READER_HEADER_STRATEGIES,
    apply_candidate_overrides,
    article_node_options,
    prepare_document,
    select_article_nodes,
)
from doxagent.monitoring.media_enrichment import _default_extractor


def clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def tokens(text: str) -> set[str]:
    latin = set(re.findall(r"[a-z0-9]{3,}", text.lower())) - {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "stock",
        "stocks",
    }
    # Korean and Chinese feed/page headlines often differ by a short editorial
    # prefix. Latin-only tokens make unrelated CJK headlines indistinguishable.
    cjk = {
        run[index : index + 2]
        for run in re.findall(r"[\u3400-\u9fff\uac00-\ud7a3]+", text.casefold())
        for index in range(max(1, len(run) - 1))
    }
    return latin | cjk


def title_match(left: str, right: str) -> bool:
    a, b = tokens(left), tokens(right)
    return bool(a and b and len(a & b) / min(len(a), len(b)) >= 0.45)


@dataclass(frozen=True)
class Candidate:
    text: str
    method: str
    structured: bool = False
    headline: str = ""
    scope_priority: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "chars": len(self.text),
            "sha256": hashlib.sha256(self.text.encode()).hexdigest(),
            "structured": self.structured,
        }


@dataclass
class Inspection:
    candidates: list[Candidate] = field(default_factory=list)
    headline: str = ""
    canonical: str = ""
    publisher_links: list[str] = field(default_factory=list)
    page_kind: str = "unknown"
    access_reason: str | None = None
    interactive_challenge: bool = False
    expansion_required: bool = False
    subscription_article: bool = False


CHALLENGE = re.compile(
    r"verify (?:that )?you are human|press\s*(?:&|and)\s*hold|checking your browser|"
    r"access to this page has been denied|datadome device check|px-captcha|"
    r"this page maybe requiring captcha|prove you are not a robot|人工验证挑战|"
    r"access is temporarily restricted|unusual activity from your (?:device|network)|"
    r"automated \(bot\) activity|use of developer or inspection tools|"
    r"enable javascript and cookies to continue",
    re.I,
)
WALL = re.compile(
    r"subscribe to (?:continue|read|unlock)|subscription (?:plan is )?required|"
    r"upgrade to read|sign in to (?:continue|read)|log in to (?:continue|read)|"
    r"(?:silver or gold|premium) subscription.{0,50}(?:required|access)|"
    r"unlock (?:this|the full) article|already a subscriber\??\s*(?:sign|log) in|"
    r"create (?:a )?free account to (?:read|continue)|创建免费账号以阅读全文",
    re.I,
)
EXPAND = re.compile(r"^(?:continue reading|read more|show more|read full article)$", re.I)


def _json_articles(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [node for item in value for node in _json_articles(item)]
    if not isinstance(value, dict):
        return []
    result = []
    kind = value.get("@type", "")
    if any(k in str(kind).lower() for k in ("article", "blogposting", "reportage")):
        result.append(value)
    for key in ("@graph", "mainEntity", "article"):
        result.extend(_json_articles(value.get(key)))
    return result


def _node_text(node: Any, *, sa_body: bool = False, street_body: bool = False) -> str:
    # Work on a copy: selectors and access evidence need the original document.
    copy = html_parser.fromstring(html_parser.tostring(node))
    for junk in copy.xpath(
        ".//script|.//style|.//nav|.//aside|.//footer|.//header|.//button|.//form|"
        './/*[contains(@class,"related") or contains(@class,"recommend") '
        'or contains(@class,"advert") or contains(@class,"share-button")]'
    ):
        junk.drop_tree()
    paragraphs = copy.xpath(".//p|.//h2|.//h3|.//li|.//table")
    if not paragraphs:
        return clean(copy.text_content())
    lines = []
    for paragraph in paragraphs:
        # Do not duplicate table cells or nested list paragraphs.
        if any(
            parent.tag in {"li", "table"}
            for parent in paragraph.iterancestors()
            if parent is not copy
        ):
            continue
        text = clean(paragraph.text_content())
        if (
            street_body
            and paragraph.tag in {"h2", "h3"}
            and re.fullmatch(r"About the authors?", text, re.I)
        ):
            break
        if (
            sa_body
            and paragraph.tag in {"h2", "h3"}
            and re.fullmatch(r"More on my IG service", text, re.I)
        ):
            break
        if re.match(
            r"^(?:Sign up for our (?:daily|weekly) news|You also may be interested in|"
            r"Should You Invest \$1,000 .* Right Now\?|Featured Articles and Offers|"
            r"Click here for the latest technology news|"
            r"Read the latest financial and business news)",
            text,
            re.I,
        ):
            break
        if re.match(
            r"^Warning! GuruFocus has detected|^Is .{1,25} fairly valued\? Test your thesis", text
        ):
            continue
        if text and not EXPAND.fullmatch(text) and text not in lines:
            lines.append(text)
    return "\n\n".join(lines)


def inspect_html(
    html: str,
    url: str,
    expected_title: str | None,
    *,
    strategy_ref: str | None = None,
    strategy_parameters: dict[str, Any] | None = None,
) -> Inspection:
    result = Inspection()
    if not html.strip():
        return result
    try:
        root = html_parser.fromstring(html)
    except (ValueError, html_parser.etree.ParserError):
        return result
    strategy = strategy_ref or legacy_strategy_ref(url)
    parameters = strategy_parameters or {}
    for selector in parameters.get("remove_xpath", []):
        if isinstance(selector, str):
            for node in root.xpath(selector):
                if hasattr(node, "drop_tree"):
                    node.drop_tree()
    headlines = root.xpath("//h1//text()")
    result.headline = clean(" ".join(headlines))
    if not result.headline:
        result.headline = next(iter(root.xpath('//meta[@property="og:title"]/@content')), "")
    result.canonical = next(iter(root.xpath('//link[@rel="canonical"]/@href')), "")
    visible = clean(" ".join(root.xpath("//text()[not(ancestor::script or ancestor::style)]")))
    title = clean(" ".join(root.xpath("//title/text()")))
    alternatives = [result.headline, *root.xpath('//meta[@property="og:title"]/@content'), title]
    if expected_title:
        result.headline = next(
            (h for h in alternatives if title_match(h, expected_title)), result.headline
        )
    # WAF bootstrap documents are not articles, even when served as HTTP 202.
    # Do this before title matching: otherwise the browser fallback is never reached.
    if root.xpath(
        '//*[@id="px-captcha-wrapper" and '
        'contains(concat(" ",normalize-space(@class)," ")," px-captcha-visible ")]'
    ):
        result.page_kind, result.access_reason = "challenge", "challenge_required"
        result.interactive_challenge = True
        return result
    has_article_body = bool(
        root.xpath(
            '//article//p|//*[@itemprop="articleBody"]//p|'
            '//*[contains(@class,"article-content") or contains(@class,"article-body")]//p'
        )
    )
    if not has_article_body:
        captcha_frame = any(
            (urlparse(src).hostname or "").endswith(".captcha-delivery.com")
            for src in root.xpath("//iframe/@src")
        )
        if "awsWafCookieDomainList" in html and ("challenge.js" in html or "gokuProps" in html):
            result.page_kind, result.access_reason = "app_shell", "render_required"
            return result
        if (
            ("captcha-delivery.com" in html and "Please enable JS" in visible)
            or captcha_frame
            or ("/cdn-cgi/challenge-platform/" in html and "_cf_chl_opt" in html)
            or re.search(r"access denied.*cloudflare|cloudflare to restrict access", title, re.I)
        ):
            result.page_kind, result.access_reason = "challenge", "challenge_required"
            result.interactive_challenge = captcha_frame
            return result
    if CHALLENGE.search(title) or (CHALLENGE.search(visible) and not root.xpath("//article//p")):
        result.page_kind, result.access_reason = "challenge", "challenge_required"
        return result
    path = urlparse(url).path.lower()
    if prepare_document(
        strategy,
        root,
        url,
        expected_title,
        result,
        title_match=title_match,
    ):
        return result
    if re.search(r"/(?:quote|quotes|search)(?:/|$)", path) or path.rstrip("/") == "/market-news":
        result.page_kind = "quote" if "/quote" in path else "listing"
        return result
    generic_nodes = root.xpath(
        '//*[@data-testid="article-body"]|//*[@itemprop="articleBody"]|'
        '//*[contains(concat(" ",normalize-space(@class)," ")," caas-body ")]|'
        '//*[contains(@class,"article-body") or contains(@class,"articleBody") '
        'or contains(@class,"body-content") or contains(@class,"article-content")]|//article'
    )
    configured_nodes = []
    for selector in parameters.get("body_xpath", []):
        if isinstance(selector, str):
            configured_nodes.extend(root.xpath(selector))
    strategy_document = select_article_nodes(strategy, root, path, generic_nodes, configured_nodes)
    article_nodes = strategy_document.article_nodes
    gate_text = " ".join(_node_text(n) for n in article_nodes) or visible
    if re.search(
        r"This headline only article is a sample of real.time intelligence", gate_text, re.I
    ):
        result.access_reason = "headline_only_source"
    wall = WALL.search(gate_text)
    if wall:
        result.access_reason = (
            "login_required"
            if re.search(r"sign in|log in|free account|免费账号", wall[0], re.I)
            else "subscription_required"
        )
    for script in root.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            articles = _json_articles(json.loads(script))
        except (ValueError, TypeError):
            continue
        for article in articles:
            headline = str(article.get("headline") or article.get("name") or "")
            if headline and expected_title and not title_match(headline, expected_title):
                continue
            body = article.get("articleBody")
            result.subscription_article |= article.get("isAccessibleForFree") in (
                False,
                "False",
                "false",
            )
            if isinstance(body, str) and body.strip():
                body = html_parser.fromstring("<div>" + body + "</div>").text_content()
                result.candidates.append(
                    Candidate(clean(body), "json_ld_article_body", True, headline)
                )
    for node in article_nodes:
        text = _node_text(node, **article_node_options(strategy))
        if text:
            result.candidates.append(
                Candidate(
                    text,
                    "article_dom",
                    True,
                    result.headline,
                    (5 + sum(parent.tag == "article" for parent in node.iterancestors()))
                    if node.tag == "article"
                    else 10,
                )
            )
    extracted = _default_extractor()(html)
    if extracted:
        result.candidates.append(
            Candidate(clean(extracted), "trafilatura", bool(article_nodes), result.headline)
        )
    expansion_root = apply_candidate_overrides(
        strategy,
        root,
        url,
        path,
        html,
        expected_title,
        result,
        strategy_document,
        clean=clean,
        title_match=title_match,
        node_text=_node_text,
        candidate_type=Candidate,
    )
    # Only semantically identified original-article links; never arbitrary external assets.
    for link in expansion_root.xpath(".//a[@href]"):
        label = clean(link.text_content())
        if len(label) > 100:
            continue
        href = urljoin(url, link.get("href"))
        if EXPAND.fullmatch(label) or re.search(
            r"read (?:the )?(?:original|full) article|view original", label, re.I
        ):
            if urlparse(href).hostname != urlparse(url).hostname:
                result.publisher_links.append(href)
            elif urlparse(href).path == urlparse(url).path:
                result.expansion_required = True
            elif re.search(r"(?:original|full) article|continue reading", label, re.I):
                result.publisher_links.append(href)
    result.expansion_required |= any(
        EXPAND.fullmatch(clean(node.text_content())) is not None
        for node in expansion_root.xpath(".//button")
    )
    if "/video/" in path or "jwplayer.com" in urlparse(url).netloc or "/previews/" in path:
        result.page_kind = "media"
    elif article_nodes or any(c.method == "json_ld_article_body" for c in result.candidates):
        result.page_kind = "article"
    elif not result.candidates and re.search(r"flutter|__next|id=[\"']root[\"']", html, re.I):
        result.page_kind = "app_shell"
    return result


def inspect_reader(
    text: str,
    url: str,
    expected_title: str | None,
    *,
    strategy_ref: str | None = None,
) -> Inspection:
    result = Inspection(page_kind="article")
    if CHALLENGE.search(text[:2000]):
        result.page_kind, result.access_reason = "challenge", "challenge_required"
        return result
    # Locate article heading; do not fall back to arbitrary navigation text for Yahoo.
    lines = text.splitlines()
    heads = [
        (i, line.lstrip("# ").strip()) for i, line in enumerate(lines) if line.startswith("# ")
    ]
    matching = [(i, h) for i, h in heads if expected_title and title_match(h, expected_title)]
    strategy = strategy_ref or legacy_strategy_ref(url)
    if not matching and strategy in READER_HEADER_STRATEGIES:
        # These observed reader templates start directly with the article after their headers.
        title_header = next((line[7:].strip() for line in lines if line.startswith("Title: ")), "")
        marker = next((i for i, line in enumerate(lines) if line == "Markdown Content:"), None)
        source = next((line[12:].strip() for line in lines if line.startswith("URL Source: ")), "")
        trusted_header = strategy != "builtin:benzinga@1" or source == url
        if (
            marker is not None
            and expected_title
            and title_match(title_header, expected_title)
            and trusted_header
        ):
            matching = [(marker, title_header)]
    if not matching:
        if WALL.search(text):
            result.access_reason = "subscription_required"
        return result
    start, result.headline = matching[0]
    selected: list[str] = []
    in_ad = False
    for line in lines[start + 1 :]:
        plain = clean(re.sub(r"^[#>*\s]+", "", line))
        if re.fullmatch(r"View Comments|Terms and Privacy Policy|About the authors?", plain, re.I):
            break
        if WALL.search(plain):
            result.access_reason = "subscription_required"
            return result
        if re.match(
            r"^(?:Sign up for our daily news|Sign up to the newsletter|Related Company Profiles|"
            r"More Relevant|Privacy Preference Center|Manage Consent Preferences)",
            plain,
            re.I,
        ):
            break
        if plain == "Share" and sum(map(len, selected)) > 600:
            break
        if re.match(
            r"^(?:#+\s*)?(?:recommended|related articles|more from|daily brief|read next)\b",
            line,
            re.I,
        ):
            break
        if re.match(
            r"^(?:this article was written by|analyst.s disclosure|seeking alpha.s disclosure)",
            line,
            re.I,
        ):
            break
        if re.match(r"^\[continue reading\]", line, re.I):
            match = re.search(r"\]\((https?://[^)]+)\)", line)
            if match:
                result.publisher_links.append(match[1])
            continue
        if "![" in line or line.startswith(("Title:", "URL Source:", "Warning:")):
            continue
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = clean(re.sub(r"^[#>*\s]+", "", line))
        if line in {
            "Go deeper with GlobalData",
            "Discover B2B Marketing That Performs",
            "Access deeper industry intelligence",
        }:
            in_ad = True
        if line == "Find out more":
            in_ad = False
            continue
        if in_ad:
            continue
        if re.fullmatch(r"Share|Copy Link|Share on (?:X|Linkedin|Facebook)", line):
            continue
        if len(line) < 100 and re.search(
            r"(?:January|February|March|April|May|June|July|August|September|October|"
            r"November|December) \d{1,2}, 20\d\d$",
            line,
        ):
            continue
        if line and not EXPAND.fullmatch(line):
            selected.append(line)
    result.candidates.append(
        Candidate("\n\n".join(selected), "reader_article", False, result.headline)
    )
    return result


def choose_candidate(
    info: Inspection, expected_title: str | None
) -> tuple[Candidate | None, str, str]:
    if info.access_reason:
        return None, "UNAVAILABLE", info.access_reason
    if info.page_kind in {"quote", "listing"}:
        return None, "UNAVAILABLE", "non_article_target"
    if expected_title and info.headline and not title_match(info.headline, expected_title):
        return None, "UNAVAILABLE", "publisher_identity_mismatch"
    if expected_title and not info.headline and info.candidates:
        if not any(title_match(c.headline, expected_title) for c in info.candidates):
            return None, "PARTIAL", "article_identity_unknown"
    accepted = []
    for candidate in info.candidates:
        body = candidate.text
        if re.match(r"(?:Search results for|1\.\s*News\s*[•·])", body.strip(), re.I):
            continue
        if WALL.search(body) or body.rstrip().endswith(("...", "…")):
            continue
        if info.publisher_links:
            continue
        if info.expansion_required:
            continue
        if info.page_kind == "media" and not re.search(r"transcript|逐字稿", body, re.I):
            continue
        if len(body) >= 800 and len(re.findall(r"[.!?](?:\s|$)|[。！？]", body)) >= 4:
            accepted.append(candidate)
        elif (
            candidate.structured
            and len(body) >= 120
            and re.search(r'[.!?。！？]["\u201d\u2019)]?$', body)
        ):
            if not info.expansion_required and not info.subscription_article:
                accepted.append(candidate)
    if accepted:
        # Prefer an explicit body over recall-oriented generic extraction; never the whole page.
        best = max(accepted, key=lambda c: (c.structured, c.scope_priority, len(c.text)))
        return best, "FULL" if len(best.text) >= 800 else "SHORT_FULL", "complete_article"
    if info.page_kind == "media":
        return None, "UNAVAILABLE", "unsupported_media"
    if info.subscription_article:
        return None, "PARTIAL", "subscription_required"
    if info.publisher_links:
        return None, "PARTIAL", "source_summary_only"
    if info.expansion_required:
        return None, "PARTIAL", "expand_required"
    if info.page_kind == "app_shell":
        return None, "UNAVAILABLE", "render_required"
    return (
        None,
        "PARTIAL" if info.candidates else "UNAVAILABLE",
        ("incomplete_extract" if info.candidates else "empty_extract"),
    )
