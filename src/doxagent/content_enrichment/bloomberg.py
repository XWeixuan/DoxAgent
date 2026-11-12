"""Quality evidence from the authorized browser's visible Bloomberg article."""

import json
import re
from datetime import datetime
from urllib.parse import urlsplit

from .publisher_documents import same_article


def inspect_document(root, url, info, *, candidate_type, node_text):
    path = urlsplit(url).path
    if path.startswith('/account/'):
        info.access_reason = 'login_required'
        return info
    if not re.match(r'^/(?:news/(?:articles|features)|opinion/articles)/', path):
        info.page_kind = 'media' if '/video' in path or '/audio' in path else 'listing'
        info.access_reason = 'unsupported_media' if info.page_kind == 'media' else 'non_article_target'
        return info
    info.page_kind = 'article'
    info.subscription_article = True
    info.candidates = []  # Never use a page-wide or hidden-HTML fallback for paid text.
    if not info.canonical or not same_article(info.canonical, url):
        info.access_reason = 'publisher_identity_mismatch'
        return info
    info.canonical_article_match = True
    for script in root.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            article = json.loads(script)
        except (ValueError, TypeError):
            continue
        if isinstance(article, dict) and article.get('@type') == 'NewsArticle':
            info.subscription_article = article.get('isAccessibleForFree') not in {True, 'true', 'True'}
            stamp = article.get('datePublished')
            try:
                date = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                if date.tzinfo is not None:
                    info.publisher_published_at = date.isoformat()
            except (AttributeError, ValueError):
                pass
    nodes = root.xpath('//article[@data-dox-bloomberg-snapshot="visible-v1"]'
                       '[string-length(@data-story-id)=14]'
                       '//*[contains(concat(" ",normalize-space(@class)," ")," body-content ")]')
    if len(nodes) != 1:
        info.access_reason = 'subscription_required' if info.subscription_article else 'render_required'
        return info
    body = nodes[0]
    article = body.xpath('ancestor::article')[0]
    if article.get('data-dox-bloomberg-complete') != 'true':
        info.access_reason = 'subscription_required'
        return info
    text = node_text(body)
    if not text or len(text) < 120:
        info.access_reason = 'incomplete_extract'
        return info
    info.access_reason = None
    info.candidates = [candidate_type(text, 'bloomberg_visible_dom', True,
                                      info.headline, 50, complete=True)]
    return info
