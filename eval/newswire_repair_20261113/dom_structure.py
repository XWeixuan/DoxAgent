from pathlib import Path
from lxml import html
from doxagent.content_enrichment.quality import EXPAND,clean
for label in ['bw-post','prn-body']:
 root=html.fromstring(Path('/tmp/newswire-'+label+'.html').read_text())
 print(label)
 for n in root.xpath('//*[@id="bw-release-story"]|//section[contains(@class,"release-body")]'):
  print('body',len(n.text_content()),'start',n.text_content().strip()[:300],'end',n.text_content().strip()[-220:])
 for n in root.xpath('//button|//a'):
  if EXPAND.fullmatch(clean(n.text_content())):print('expand',n.tag,n.get('href'),n.get('class'),'parents',[(x.tag,x.get('class')) for x in list(n.iterancestors())[:4]])
