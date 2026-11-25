"""Publisher-specific section contracts. HTTP success alone is never list validation."""
import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from dateutil.parser import parse as parse_date
from lxml import html

from .fintech_extract import nuxt_data, rsc_data, walk
from .fintech_sources import SOURCES
from .schema import sha256_text
from .technology_sources import article


def text(node):
    return ' '.join(node.text_content().split())


def stamp(value, zone='America/New_York'):
    if not value:
        return None
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return value
    date = parse_date(value, tzinfos={'EST':-18000,'EDT':-14400})
    if date.tzinfo is None:
        date = date.replace(tzinfo=ZoneInfo(zone))
    return date.astimezone(UTC).isoformat()


def parse_listing(recipe, raw, now):
    if recipe.parser == 'fintech_sbc':
        raise ValueError('sbc_http_requires_browser_review')
    spec = next(s for s in SOURCES.values() if recipe.feed in s.urls)
    doc = None if recipe.parser in {'fintech_unchained','fintech_occ'} else html.fromstring(raw)
    rows = []

    def add(url, title, date=None, summary=None, **payload):
        value = article(spec, urljoin(recipe.url,url), title, date, now, summary=summary, payload=payload)
        if value:
            value.metadata.update(provider='fintech_public', discovery_method='http_listing', listing_url=recipe.url)
            value.metadata['identity_evidence'].update({k:v for k,v in {
                'updated_at':payload.get('updated_at'),
                'provider_version':payload.get('content_hash'),
            }.items() if v})
            rows.append(value)

    if recipe.parser == 'fintech_block':
        data = nuxt_data(doc).get('data', {})
        for row in walk(data):
            if row.get('type') == 'post' and row.get('url') and row.get('tbcoPublished'):
                add(row['url'], row['title'], row['tbcoPublished'], updated_at=row.get('modified'),
                    publisher_article_id=row['id'], publisher_pro=bool(row.get('isProNews')))
    elif recipe.parser == 'fintech_coindesk':
        for link in doc.xpath('//a[contains(concat(" ",@class," ")," content-card-title ")][@href]'):
            url = link.get('href')
            if not re.match(r'^/(?:policy|business|tech)/\d{4}/\d{2}/\d{2}/',url):
                continue
            card = next((p for p in link.iterancestors() if p.xpath('.//time[@datetime]')),None)
            if card is not None:
                add(url,text(link),card.xpath('.//time/@datetime')[0])
    elif recipe.parser == 'fintech_rg':
        dates = {row['slug']:row['publishedAt'] for row in walk(rsc_data(doc))
                 if row.get('publishedAt') and row.get('slug') and isinstance(row.get('category'),dict)
                 and row['category'].get('slug') == 'prediction-markets'}
        for link in doc.xpath('//a[starts-with(@href,"/news/prediction-markets/")]'):
            title = link.xpath('.//h2|.//h3|.//h4')
            if title:
                add(link.get('href'),text(title[0]),dates.get(link.get('href').rstrip('/').split('/')[-1]))
    elif recipe.parser == 'fintech_decrypt':
        nodes=doc.xpath('//script[@id="__NEXT_DATA__"]')
        if not nodes:
            raise ValueError('decrypt_page_state_missing')
        page=json.loads(nodes[0].text)['props']['pageProps']
        for query in page['dehydratedState']['queries']:
            if query['queryKey'][0]!='ArticlePreviews':
                continue
            for row in walk(query['state']['data']):
                if row.get('__typename') == 'NewsArticleEntity' and row.get('publishedAt') and row.get('slug'):
                    add('https://decrypt.co/'+str(row['id'])+'/'+row['slug'],row['title'],
                        stamp(row['publishedAt'],'UTC'),row.get('excerpt'),updated_at=row.get('updatedAt'))
    elif recipe.parser == 'fintech_cointelegraph':
        # Hydrated URQL includes menus and unrelated cached queries. Intersect
        # articles with the actual tag page's rendered cards, not the whole cache.
        paths=set(doc.xpath('//article/@data-card-href|//a[@data-title-link]/@href'))
        for entry in nuxt_data(doc).get('state',{}).get('$surql',{}).values():
            if not isinstance(entry,dict) or not isinstance(entry.get('data'),str):
                continue
            for row in walk(json.loads(entry['data'])):
                path='/news/'+str(row.get('slug',''))
                if path in paths and row.get('publishedAt') and row.get('status')=='published':
                    add(path,row['title'],row['publishedAt'],row.get('dek'),updated_at=row.get('updatedAt'))
    elif recipe.parser == 'fintech_unchained':
        value=json.loads(raw)
        doc=html.fromstring('<ul>'+value['html']+'</ul>')
        for card in doc.xpath('//li'):
            links=card.xpath('.//h2/a|.//h3/a')
            if not links:
                continue
            dates=card.xpath('.//time/@datetime')
            add(links[0].get('href'),text(links[0]),stamp(dates[0]) if dates else None)
    elif recipe.parser == 'fintech_banking':
        for card in doc.xpath('//li[contains(concat(" ",normalize-space(@class)," ")," row ")]'):
            links=card.xpath('.//h3/a[@href]|.//h2/a[@href]')
            if links and '/news/' in links[0].get('href',''):
                dates=card.xpath('.//time/@datetime')
                add(links[0].get('href'),text(links[0]),dates[0] if dates else None)
    elif recipe.parser == 'fintech_bleeping':
        for card in doc.xpath('//li[.//div[contains(@class,"bc_latest_news_text")]]'):
            links=card.xpath('.//h4/a[@href]')
            dates=card.xpath('.//li[contains(@class,"bc_news_date")]/text()')
            times=card.xpath('.//li[contains(@class,"bc_news_time")]/text()')
            if links:
                date=stamp(' '.join(dates+times)) if dates else None
                add(links[0].get('href'),text(links[0]),date)
    elif recipe.parser == 'fintech_ledger':
        for card in doc.xpath('//article[contains(concat(" ",@class," ")," category-news ")]'):
            links=card.xpath('.//h2/a[@href]')
            dates=card.xpath('.//time/@datetime')
            if links:
                add(links[0].get('href'),text(links[0]),stamp(dates[0]) if dates else None)
    elif recipe.parser == 'fintech_defiant':
        topic=urlsplit(recipe.url).path.rstrip('/').split('/')[-1]
        sections=doc.xpath('//section[h2[normalize-space(.)="Recent articles"]]')
        if len(sections)!=1:
            raise ValueError('defiant_recent_section_missing')
        for link in sections[0].xpath('.//a[.//h2|.//h3]'):
            url=link.get('href','')
            if re.match(r'^/(?:news|converge)/'+re.escape(topic)+r'/[^/]+/?$',url):
                add(url,text(link))
    elif recipe.parser == 'fintech_cftc_rules':
        requested=parse_qs(urlsplit(recipe.url).query).get('Organization',[])
        if len(requested)!=1 or requested[0] not in {'COIN','KEX'}:
            raise ValueError('cftc_organization_scope_required')
        for row in doc.xpath('//table//tr[td]'):
            cells=row.xpath('./td')
            if len(cells)!=7 or text(cells[0]) not in requested:
                continue
            links=cells[6].xpath('.//a[@href]')
            if not links:
                raise ValueError('cftc_filing_document_link_missing')
            path=links[0].get('href')
            if not re.fullmatch(r'/IndustryOversight/IndustryFilings/TradingOrganizationRules/\d+',path):
                raise ValueError('cftc_filing_identity_changed')
            fields=[text(c) for c in cells]
            date=datetime.strptime(fields[4],'%m/%d/%Y').date().isoformat()
            add(path,'CFTC '+fields[0]+' | '+fields[1]+' | '+fields[3],date,
                organization=fields[0],description=fields[1],receipt_date=fields[2],status=fields[3],
                status_date=fields[4],remarks=fields[5],attachment_count=int(text(links[0])),
                content_hash=sha256_text(json.dumps(fields)))
    elif recipe.parser == 'fintech_cftc_press':
        for row in doc.xpath('//table//tr[td]'):
            links=row.xpath('.//a[contains(@href,"/PressRoom/PressReleases/")]')
            cells=row.xpath('./td')
            if links and cells:
                date=datetime.strptime(text(cells[0]),'%m/%d/%Y').date().isoformat()
                add(links[0].get('href'),text(links[0]),date)
    elif recipe.parser == 'fintech_occ':
        value=json.loads(raw)
        for row in value['NewsItems']:
            # Apply the same five types locally even if the server ignores filters.
            if row['NewsType'] not in {0,1,3,4,5}:
                continue
            add(row['Url'],row['NewsTitle'],row['PubDate'],release_id=row.get('RelId'))
    elif recipe.parser == 'fintech_sbc':
        raise ValueError('sbc_http_requires_browser_review')
    else:
        raise ValueError('unknown_fintech_parser:'+recipe.parser)
    unique={row.url:row for row in rows}
    if not unique:
        raise ValueError('fintech_listing_empty_or_changed:'+recipe.parser)
    return list(unique.values())
