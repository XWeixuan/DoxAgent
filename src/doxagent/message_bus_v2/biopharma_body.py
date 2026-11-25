"""Publisher-scoped scientific abstract identity and complete-text extraction."""

from urllib.parse import parse_qs, urljoin, urlsplit

from lxml import html

from doxagent.content_enrichment.quality import (
    Candidate,
    _node_text,
    inspect_html,
    title_match,
)


def inspect_ash(raw, url, title, **kwargs):
    info = inspect_html(raw, url, title, **kwargs)
    if info.access_reason or info.subscription_article:
        return info
    parsed = urlsplit(url)
    pid = parse_qs(parsed.query).get("pid")
    if (
        parsed.hostname != "onlineprogram.hematology.org"
        or parsed.path != "/paper-details.aspx"
        or not pid
    ):
        return info
    doc = html.fromstring(raw)
    forms = [urlsplit(urljoin(url, v)) for v in doc.xpath("//form/@action")]
    verified = any(
        f.hostname == parsed.hostname
        and f.path == parsed.path
        and parse_qs(f.query).get("pid") == pid
        for f in forms
    )
    bodies = doc.xpath('//*[@id="pnlPaperAbstract"]')
    headlines = [
        n
        for n in doc.xpath('//h2[contains(@class,"session-title")]')
        if title_match(n.text_content(), title)
    ]
    if not verified or len(bodies) != 1 or not headlines:
        return info
    # h1 is the generic "Paper Details". Use the native paper heading and full
    # ABSTRACT panel only after independently checking its stable requested pid.
    info.headline = " ".join(headlines[-1].text_content().split())
    info.canonical = url
    info.canonical_article_match = True
    info.page_kind = "article"
    info.candidates = [
        Candidate(_node_text(bodies[0]), "ash_abstract_dom", True, info.headline, 40)
    ]
    return info
