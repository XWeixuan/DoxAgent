"""Public AEM endpoints advertised by Snowflake's own press-release frontend."""
from html import escape
from urllib.parse import urlsplit

HOST = 'publish-p57963-e462109.adobeaemcloud.com'
ORIGIN = 'https://' + HOST
CONTENT = '/content/snowflake-site/global'
LISTING_URL = (ORIGIN + CONTENT + '/en/news/press-releases/_jcr_content/root/responsivegrid/'
               'container/container/flexible_column_cont/flexible_column_content_container_1/'
               'filterable_resources.filter.json?offset=0&pageSize=12&orderBy=customPublicationDate&sortOrder=desc')


def model_url(article_url):
    parsed = urlsplit(article_url)
    if parsed.hostname not in {'www.snowflake.com', 'snowflake.com'} or not parsed.path.startswith('/en/news/press-releases/'):
        raise ValueError('Snowflake article URL mismatch')
    return ORIGIN + CONTENT + parsed.path.rstrip('/') + '.model.json'


def components(node):
    if not isinstance(node, dict) or 'experiencefragment' in node.get(':type', ''):
        return
    yield node
    children = node.get(':items', {})
    for key in node.get(':itemsOrder', children.keys()):
        if key not in children or 'header' in key.lower() or 'footer' in key.lower():
            continue
        yield from components(children[key])
    # AEM columns are named child components, rather than :items members.
    if 'flexible_column_content_container_1' in node:
        yield from components(node['flexible_column_content_container_1'])


def article_html(payload, article_url):
    from doxagent.content_enrichment.publisher_documents import same_article

    if payload.get('isPasswordProtected') or not same_article(payload.get('canonicalLink', ''), article_url):
        raise ValueError('Snowflake model article identity mismatch')
    nodes = list(components(payload))
    headline = next((' '.join(n.get('headline', {}).get('lines', []))
                     for n in nodes if n.get(':type', '').endswith('/hero-system')), '')
    paragraphs = [n['text'] if n.get('richText') else '<p>' + escape(n['text']) + '</p>'
                  for n in nodes if n.get(':type') == 'snowflake-site/components/text'
                  and isinstance(n.get('text'), str) and n['text'].strip()]
    if not headline or not paragraphs:
        raise ValueError('Snowflake article model body missing')
    return ('<html><head><link rel="canonical" href="' + escape(article_url, quote=True)
            + '"></head><body><article><h1>' + escape(headline) + '</h1>'
            + ''.join(paragraphs) + '</article></body></html>')
