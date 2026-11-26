"""Official CFTC filing metadata and all associated public PDF/HTML documents."""
import asyncio
from hashlib import sha256
from io import BytesIO
import re
from urllib.parse import urljoin, urlsplit

from lxml import html
from pypdf import PdfReader

from doxagent.monitoring.media_enrichment import MediaExtractionResult


def inspect_article(spec, raw, url, title, **kwargs):
    from doxagent.content_enrichment.quality import inspect_html, title_match
    from doxagent.content_enrichment.publisher_documents import same_article
    from .fintech_extract import walk
    import json

    info=inspect_html(raw,url,title,**kwargs)
    doc=html.fromstring(raw)
    if spec.source_id=='ledgerinsights_news':
        body=' '.join(doc.xpath('//*[contains(concat(" ",@class," ")," entry-content ")]//text()')).casefold()
        if 'article continues' in body and 'pro subscribers get complete articles' in body:
            info.subscription_article=True
            info.access_reason='subscription_required'
    if spec.source_id=='rg_prediction_markets' and not info.access_reason and not info.subscription_article:
        public=False
        for script in doc.xpath('//script[@type="application/ld+json"]'):
            try:values=json.loads(script.text or '{}')
            except ValueError:continue
            public |= any(row.get('isAccessibleForFree') is True and title_match(row.get('headline',''),title)
                          for row in walk(values))
        bodies=doc.xpath('(//article[.//p and not(ancestor::article)])[1]')
        heads=doc.xpath('//h1')
        head=doc.find('head')
        if public and bodies and heads and head is not None and same_article(info.canonical,url):
            # The author's "Show More" control is outside the public article.
            # Keep original article access/schema evidence when scoping the DOM.
            body=bodies[0]
            for node in body.xpath('.//p'):
                if ' '.join(node.itertext()).strip().startswith(('Anyone affected by problem gambling','Anyone having thoughts')):
                    node.getparent().remove(node)
            scoped='<html>'+html.tostring(head,encoding='unicode')+'<body>'+html.tostring(heads[0],encoding='unicode')+html.tostring(body,encoding='unicode')+'</body></html>'
            info=inspect_html(scoped,url,title,**kwargs)
    return info


def pdf_text(data):
    pages = [(p.extract_text() or '').strip() for p in PdfReader(BytesIO(data)).pages]
    if not pages or any(not p for p in pages):
        raise ValueError('cftc_attachment_ocr_required')
    return '\n\n'.join(pages)


async def document_body(http, spec, record):
    """OCC publishes some speeches/testimony directly as full public PDFs."""
    capture=await http.fetch(record.url,spec,binary=True)
    if capture.status!=200:
        return MediaExtractionResult(record=record,http_status=capture.status,
            reason='public_document_http_'+str(capture.status),diagnostics={'outcome':'UNAVAILABLE'})
    if not capture.raw_bytes or not capture.raw_bytes.startswith(b'%PDF-'):
        return MediaExtractionResult(record=record,reason='public_document_not_pdf',diagnostics={'outcome':'UNAVAILABLE'})
    try:
        text=await asyncio.to_thread(pdf_text,capture.raw_bytes)
    except ValueError:
        return MediaExtractionResult(record=record,reason='public_document_ocr_required',diagnostics={'outcome':'PARTIAL'})
    return MediaExtractionResult(record=record,content=text,final_url=capture.url,http_status=200,
        extraction_method='publisher_public_pdf',diagnostics={'outcome':'FULL',
            'document_sha256':sha256(capture.raw_bytes).hexdigest(),'body_source':'publisher_public_pdf'})


async def filing_body(http, spec, job, record):
    payload = job.message.raw_payload
    if payload.get('organization') not in {'COIN','KEX'} or not re.fullmatch(
        r'/IndustryOversight/IndustryFilings/TradingOrganizationRules/\d+',urlsplit(record.url).path
    ):
        raise ValueError('cftc_filing_identity_mismatch')
    capture = await http.fetch(record.url,spec)
    if capture.status != 200:
        return MediaExtractionResult(record=record,http_status=capture.status,reason='cftc_filing_http_'+str(capture.status))
    doc = html.fromstring(capture.text)
    filing_id=urlsplit(record.url).path.rsplit('/',1)[-1]
    if not any(re.search(r'\b'+re.escape(filing_id)+r'\b',' '.join(n.itertext())) for n in doc.xpath('//h1')):
        raise ValueError('cftc_filing_page_identity_mismatch')
    # Only the filing's associated-document table; never crawl navigation or
    # recursively follow arbitrary links from contractual content.
    tables = doc.xpath('//table')
    links = list(dict.fromkeys(urljoin(record.url,a.get('href')) for table in tables
        for a in table.xpath('.//a[@href]')))
    expected = payload.get('attachment_count')
    if not isinstance(expected,int) or expected <= 0 or len(links) != expected:
        raise ValueError('cftc_filing_attachment_count_mismatch')
    if any(not spec.accepts(url) for url in links):
        raise ValueError('cftc_attachment_host_not_approved')
    # Confirm the page actually belongs to the requested organization.
    page_text=' '.join(doc.itertext())
    if not re.search(r'Organization:\s*'+re.escape(payload['organization'])+r'\b',page_text):
        raise ValueError('cftc_filing_organization_mismatch')
    slots=asyncio.Semaphore(2)

    async def fetch(url):
        async with slots:
            c=await http.fetch(url,spec,binary=True)
            if c.status!=200 or not c.raw_bytes:
                raise ValueError('cftc_attachment_http_'+str(c.status))
            if c.raw_bytes.startswith(b'%PDF-'):
                text=await asyncio.to_thread(pdf_text,c.raw_bytes)
            elif 'html' in c.headers.get('content-type',''):
                page=html.fromstring(c.raw_bytes)
                main=page.xpath('//article|//main')
                if not main:
                    raise ValueError('cftc_attachment_html_contract_changed')
                text='\n'.join(t.strip() for t in main[0].itertext() if t.strip())
            else:
                raise ValueError('cftc_attachment_format_unsupported')
            return dict(url=url,sha256=sha256(c.raw_bytes).hexdigest(),chars=len(text)),text

    results=await asyncio.gather(*(fetch(url) for url in links),return_exceptions=True)
    failures=[str(x) for x in results if isinstance(x,BaseException)]
    receipts=[x[0] for x in results if not isinstance(x,BaseException)]
    if failures:
        return MediaExtractionResult(record=record,reason=failures[0],http_status=200,
            diagnostics={'outcome':'PARTIAL' if receipts else 'UNAVAILABLE','attachments':receipts,'attachment_errors':failures})
    sections=[job.message.title, '\n'.join(k.replace('_',' ').title()+': '+str(payload[k])
        for k in ['organization','description','receipt_date','status','status_date','remarks'])]
    seen=set()
    for receipt,body in results:
        if receipt['sha256'] not in seen:
            seen.add(receipt['sha256']);sections.append('Document: '+receipt['url']+'\n'+body)
    content='\n\n'.join(sections)
    return MediaExtractionResult(record=record,content=content,final_url=record.url,http_status=200,
        extraction_method='cftc_public_filing_documents',diagnostics={
            'outcome':'FULL','body_source':'cftc_public_filing_documents','attachments':receipts,
            'attachment_count':len(links),'unique_documents':len(seen),
        })
