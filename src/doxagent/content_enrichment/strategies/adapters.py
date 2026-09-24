"""Trusted site-specific candidate adapters used by the shared quality gate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from doxagent.content_enrichment.publishers import seeking_alpha_original_title


@dataclass
class StrategyDocument:
    article_nodes: list[Any]
    seeking_alpha_bodies: list[Any]
    wsj_bodies: list[Any]


def prepare_document(
    strategy: str,
    root: Any,
    url: str,
    expected_title: str | None,
    result: Any,
    *,
    title_match: Any,
) -> bool:
    if strategy == "builtin:seeking_alpha@1":
        original = seeking_alpha_original_title(root.xpath("//script/text()"), url)
        if original and expected_title and title_match(original, expected_title):
            result.headline = original
    if strategy != "builtin:finnhub_redirect@1":
        return False
    targets: list[str] = []
    for value in root.xpath('//meta[translate(@http-equiv,"REFSH","refsh")="refresh"]/@content'):
        match = re.search(r"url\s*=\s*['\"]?([^'\"]+)", value, re.I)
        if match:
            targets.append(match[1].strip())
    for script in root.xpath("//script/text()"):
        targets.extend(re.findall(r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)", script))
    result.publisher_links = list(dict.fromkeys(urljoin(url, target) for target in targets))
    result.page_kind = "redirect"
    return bool(result.publisher_links)


def select_article_nodes(
    strategy: str,
    root: Any,
    path: str,
    generic_nodes: list[Any],
    configured_nodes: list[Any],
) -> StrategyDocument:
    article_nodes = configured_nodes or generic_nodes
    seeking_alpha = (
        root.xpath('//*[@data-test-id="content-container"]')
        if strategy == "builtin:seeking_alpha@1" and "/article/" in path
        else []
    )
    if seeking_alpha:
        article_nodes = seeking_alpha[:1]
    wsj = (
        root.xpath('//article//*[contains(concat(" ",normalize-space(@class)," ")," paywall ")]')
        if strategy == "builtin:wsj@1"
        else []
    )
    if wsj:
        article_nodes = wsj[:1]
    return StrategyDocument(article_nodes, seeking_alpha, wsj)


def article_node_options(strategy: str) -> dict[str, bool]:
    return {"street_body": strategy == "builtin:thestreet@1"}


def apply_candidate_overrides(
    strategy: str,
    root: Any,
    url: str,
    path: str,
    html: str,
    expected_title: str | None,
    result: Any,
    document: StrategyDocument,
    *,
    clean: Any,
    title_match: Any,
    node_text: Any,
    candidate_type: Any,
) -> Any:
    if strategy == "builtin:reuters@1":
        paragraphs = root.xpath(
            '//*[@data-testid="ArticleBody"]//*[starts-with(@data-testid,"paragraph-")]'
            ' | //*[@data-testid="ArticleBody"]'
            '//*[starts-with(@data-testid,"unordered-") or '
            'starts-with(@data-testid,"ordered-")]/li'
        )
        if paragraphs:
            text = "\n\n".join(
                clean(re.sub(r",? opens new tab", "", node.text_content())) for node in paragraphs
            )
            result.candidates = [
                candidate_type(text, "reuters_article_body", True, result.headline, 30)
            ]
    if strategy == "builtin:etnews@1":
        bodies = root.xpath('//*[@itemprop="articleBody"]')
        if bodies:
            text = node_text(bodies[0])
            text = re.sub(r"\n\n[^\n]{1,80}\s기자(?:\s+\S+@\S+)?$", "", text).strip()
            if text:
                result.candidates = [
                    candidate_type(text, "etnews_article_body", True, result.headline, 30)
                ]
    if strategy == "builtin:barrons@1" and "/livecoverage/" in path and "/card/" in path:
        cards = root.xpath('//*[@data-id="LiveCoverageCard_index_CardWrapper"][.//h1]')
        result.candidates = []
        for card in cards:
            headline = clean(" ".join(card.xpath(".//h1//text()")))
            if expected_title and not title_match(headline, expected_title):
                continue
            paragraphs = card.xpath(
                './/*[@data-id="LiveCoverageCard_index_CardBlock"]'
                '//p[contains(@class,"FormattedText")]'
            )
            if paragraphs:
                text = "\n\n".join(clean(node.text_content()) for node in paragraphs)
                result.candidates.append(
                    candidate_type(text, "barrons_live_card", True, headline, 30)
                )
    if document.wsj_bodies:
        blocks = document.wsj_bodies[0].xpath('./p[@data-type="paragraph"]|./h2|./h3')
        text = "\n\n".join(clean(node.text_content()) for node in blocks)
        result.candidates = [candidate_type(text, "wsj_article_body", True, result.headline, 30)]
    expansion_root = root
    if strategy == "builtin:seeking_alpha@1":
        if document.seeking_alpha_bodies:
            expansion_root = document.seeking_alpha_bodies[0]
            result.candidates = [
                candidate_type(
                    node_text(expansion_root, sa_body=True),
                    "sa_article_body",
                    True,
                    result.headline,
                    20,
                )
            ]
        elif "/article/" in path:
            result.candidates = []
        result.subscription_article |= bool(
            root.xpath(
                '//script[@type="application/ld+json" and contains(text(),"isAccessibleForFree")]'
            )
        ) and '"isAccessibleForFree":"False"' in html.replace(" ", "")
    return expansion_root


READER_HEADER_STRATEGIES = {
    "builtin:chartmill@1",
    "builtin:fool@1",
    "builtin:benzinga@1",
}


__all__ = [
    "READER_HEADER_STRATEGIES",
    "StrategyDocument",
    "apply_candidate_overrides",
    "article_node_options",
    "prepare_document",
    "select_article_nodes",
]
