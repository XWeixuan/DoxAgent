from pathlib import Path
from lxml import html
import json
r=html.fromstring(Path('/tmp/newswire-tmx-browser.html').read_text())
h=r.xpath('//h1')[0]
print(json.dumps({'headline':h.text_content(),'parents':[(n.tag,n.get('id'),n.get('class')) for n in list(h.iterancestors())[:5]],'body_candidates':[{'tag':n.tag,'id':n.get('id'),'class':n.get('class'),'chars':len(n.text_content())} for n in r.xpath('//article|//*[@itemprop="articleBody"]|//div[@id]') if len(n.text_content())>300],'canonical':r.xpath('//link[@rel="canonical"]/@href')},ensure_ascii=False))
