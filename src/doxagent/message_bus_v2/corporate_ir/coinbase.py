"""Coinbase's three official disclosure channels, with publisher date precision."""
import json
from datetime import datetime
from urllib.parse import urljoin, urlencode
from zoneinfo import ZoneInfo

from lxml import html

from ..fintech_extract import walk

Q4_URL = ('https://investor.coinbase.com/feed/PressRelease.svc/GetPressReleaseList'
          '?LanguageId=1&bodyType=0&pressReleaseDateFilter=1&categoryId=00000000-0000-0000-0000-000000000000'
          '&year=-1&pageNumber=0&pageSize=30&includeTags=true')
CMS_URL = 'https://contentful.coinbase.com/spaces/sygt3q11s4a9/environments/master/entries?'+urlencode({
    'content_type':'page','fields.content.sys.contentType.sys.id':'CDXV2_EditorialTemplate',
    'order':'-fields.publicationDate','limit':30,'include':0,'locale':'en',
    'select':'sys.id,fields.title,fields.slug,fields.publicationDate',
})


def rows(parser, raw):
    values=[]
    if parser=='coinbase_q4':
        for row in json.loads(raw)['GetPressReleaseListResult']:
            date=datetime.strptime(row['PressReleaseDate'],'%m/%d/%Y %H:%M:%S').replace(tzinfo=ZoneInfo('America/New_York'))
            values.append((row['Headline'],urljoin('https://investor.coinbase.com',row['LinkToDetailPage']),date.isoformat(),True,'','',str(row['PressReleaseId'])))
    elif parser=='coinbase_blog':
        if raw.lstrip().startswith('{'):
            for row in json.loads(raw)['items']:
                fields=row['fields']
                if fields.get('publicationDate') and fields.get('slug','').startswith('/blog/'):
                    values.append((fields['title'],urljoin('https://www.coinbase.com',fields['slug']),
                        fields['publicationDate'][:10],False,'','',row['sys']['id']))
            if not values:raise ValueError('coinbase_cms_listing_empty_or_changed')
            return values
        doc=html.fromstring(raw)
        nodes=doc.xpath('//script[@id="server-app-state"]')
        if not nodes:
            raise ValueError('coinbase_blog_state_missing')
        state=json.loads(nodes[0].text)
        bridge=json.loads(state['suspenseBridgeData'])
        for row in walk(bridge):
            if row.get('contentTypeId')=='page' and row.get('publicationDate') and row.get('slug','').startswith('/blog/'):
                # The listing and CMS field publish a calendar day, not a known
                # release instant; midnight must never turn into exact precision.
                values.append((row['title'],urljoin('https://www.coinbase.com',row['slug']),row['publicationDate'][:10],False,'','',row['id']))
    elif parser=='deribit':
        doc=html.fromstring(raw)
        for time in doc.xpath('//main//time[@datetime]'):
            card=next((p for p in time.iterancestors() if p.xpath('self::a[@href]|.//a[@href]') and p.xpath('.//h3')),None)
            if card is None:
                continue
            links=card.xpath('self::a[starts-with(@href,"/exchange-updates/")]|.//a[starts-with(@href,"/exchange-updates/")]')
            titles=card.xpath('.//h3')
            if links and titles:
                values.append((' '.join(titles[0].itertext()),urljoin('https://insights.deribit.com',links[0].get('href')),time.get('datetime')[:10],False,'','',None))
    if not values:
        raise ValueError('coinbase_ir_listing_empty_or_changed:'+parser)
    return values
