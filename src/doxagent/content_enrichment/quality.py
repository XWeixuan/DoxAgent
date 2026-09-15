"""Article-scoped candidates and deterministic completeness evidence for V2 only."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import html as html_parser  # type: ignore[import-untyped]

from doxagent.content_enrichment.publishers import seeking_alpha_original_title
from doxagent.monitoring.media_enrichment import _default_extractor


def clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower())) - {
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
    expansion_required: bool = False
    subscription_article: bool = False


CHALLENGE = re.compile(
    r"verify (?:that )?you are human|press\s*(?:&|and)\s*hold|checking your browser|"
    r"access to this page has been denied|datadome device check|px-captcha|"
    r"this page maybe requiring captcha|prove you are not a robot|人工验证挑战",
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
        if street_body and paragraph.tag in {"h2", "h3"} and re.fullmatch(
            r"About the authors?", text, re.I
        ):
            break
        if sa_body and paragraph.tag in {"h2", "h3"} and re.fullmatch(
            r"More on my IG service", text, re.I
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


def inspect_html(html: str, url: str, expected_title: str | None) -> Inspection:
    result = Inspection()
    if not html.strip():
        return result
    try:
        root = html_parser.fromstring(html)
    except (ValueError, html_parser.etree.ParserError):
        return result
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
    has_article_body = bool(root.xpath(
        '//article//p|//*[@itemprop="articleBody"]//p|'
        '//*[contains(@class,"article-content") or contains(@class,"article-body")]//p'
    ))
    if not has_article_body:
        if "awsWafCookieDomainList" in html and (
            "challenge.js" in html or "gokuProps" in html
        ):
            result.page_kind, result.access_reason = "app_shell", "render_required"
            return result
        if (
            ("captcha-delivery.com" in html and "Please enable JS" in visible)
            or ("/cdn-cgi/challenge-platform/" in html and "_cf_chl_opt" in html)
            or re.search(r"access denied.*cloudflare|cloudflare to restrict access", title, re.I)
        ):
            result.page_kind, result.access_reason = "challenge", "challenge_required"
            return result
    if CHALLENGE.search(title) or (CHALLENGE.search(visible) and not root.xpath("//article//p")):
        result.page_kind, result.access_reason = "challenge", "challenge_required"
        return result
    path = urlparse(url).path.lower()
    host = (urlparse(url).hostname or "").removeprefix("www.")
    if host == "seekingalpha.com":
        original = seeking_alpha_original_title(root.xpath("//script/text()"), url)
        if original and expected_title and title_match(original, expected_title):
            result.headline = original
    if (urlparse(url).hostname or "").removeprefix("www.") == "finnhub.io":
        targets = []
        for value in root.xpath(
            '//meta[translate(@http-equiv,"REFSH","refsh")="refresh"]/@content'
        ):
            match = re.search(r"url\s*=\s*['\"]?([^'\"]+)", value, re.I)
            if match:
                targets.append(match[1].strip())
        for script in root.xpath("//script/text()"):
            targets.extend(
                re.findall(r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)", script)
            )
        result.publisher_links = list(dict.fromkeys(urljoin(url, target) for target in targets))
        result.page_kind = "redirect"
        if result.publisher_links:
            return result
    if re.search(r"/(?:quote|quotes|search)(?:/|$)", path) or path.rstrip("/") == "/market-news":
        result.page_kind = "quote" if "/quote" in path else "listing"
        return result
    article_nodes = root.xpath(
        '//*[@data-testid="article-body"]|//*[@itemprop="articleBody"]|'
        '//*[contains(concat(" ",normalize-space(@class)," ")," caas-body ")]|'
        '//*[contains(@class,"article-body") or contains(@class,"articleBody") '
        'or contains(@class,"body-content") or contains(@class,"article-content")]|//article'
    )
    sa_bodies = root.xpath('//*[@data-test-id="content-container"]') if (
        host == "seekingalpha.com" and "/article/" in path
    ) else []
    if sa_bodies:
        article_nodes = sa_bodies[:1]
    wsj_bodies = root.xpath(
        '//article//*[contains(concat(" ",normalize-space(@class)," ")," paywall ")]'
    ) if host == "wsj.com" else []
    if wsj_bodies:
        article_nodes = wsj_bodies[:1]
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
        text = _node_text(
            node,
            street_body=(urlparse(url).hostname or "").removeprefix("www.") == "thestreet.com",
        )
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
    if host == "reuters.com":
        paragraphs = root.xpath(
            '//*[@data-testid="ArticleBody"]//*[starts-with(@data-testid,"paragraph-")]'
        )
        if paragraphs:
            text = "\n\n".join(
                clean(re.sub(r",? opens new tab", "", n.text_content())) for n in paragraphs
            )
            result.candidates = [Candidate(text, "reuters_article_body", True, result.headline, 30)]
    if host == "barrons.com" and "/livecoverage/" in path and "/card/" in path:
        cards = root.xpath('//*[@data-id="LiveCoverageCard_index_CardWrapper"][.//h1]')
        result.candidates = []
        for card in cards:
            headline = clean(" ".join(card.xpath('.//h1//text()')))
            if expected_title and not title_match(headline, expected_title):
                continue
            paragraphs = card.xpath(
                './/*[@data-id="LiveCoverageCard_index_CardBlock"]'
                '//p[contains(@class,"FormattedText")]'
            )
            if paragraphs:
                text = "\n\n".join(clean(n.text_content()) for n in paragraphs)
                result.candidates.append(Candidate(text, "barrons_live_card", True, headline, 30))
    if wsj_bodies:
        blocks = wsj_bodies[0].xpath('./p[@data-type="paragraph"]|./h2|./h3')
        text = "\n\n".join(clean(n.text_content()) for n in blocks)
        result.candidates = [Candidate(text, "wsj_article_body", True, result.headline, 30)]
    expansion_root = root
    if (urlparse(url).hostname or "").removeprefix("www.") == "seekingalpha.com":
        # SA's outer <article> also contains summary, biography and disclosures.
        # Only its first content container is the article, even on a long regwall page.
        bodies = sa_bodies
        if bodies:
            expansion_root = bodies[0]
            result.candidates = [
                Candidate(
                    _node_text(bodies[0], sa_body=True),
                    "sa_article_body", True, result.headline, 20,
                )
            ]
        elif "/article/" in path:
            result.candidates = []
        result.subscription_article |= bool(
            root.xpath(
                '//script[@type="application/ld+json" and contains(text(),"isAccessibleForFree")]'
            )
        ) and '"isAccessibleForFree":"False"' in html.replace(" ", "")
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


def inspect_reader(text: str, url: str, expected_title: str | None) -> Inspection:
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
    if not matching and (urlparse(url).hostname or "").removeprefix("www.") in {
        "chartmill.com",
        "fool.com",
        "benzinga.com",
    }:
        # These observed reader templates start directly with the article after their headers.
        title_header = next((line[7:].strip() for line in lines if line.startswith("Title: ")), "")
        marker = next((i for i, line in enumerate(lines) if line == "Markdown Content:"), None)
        source = next((line[12:].strip() for line in lines if line.startswith("URL Source: ")), "")
        trusted_header = (urlparse(url).hostname or "").removeprefix("www.") != "benzinga.com" or (
            source == url
        )
        if (
            marker is not None and expected_title and title_match(title_header, expected_title)
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
        if re.fullmatch(
            r"View Comments|Terms and Privacy Policy|About the authors?", plain, re.I
        ):
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
