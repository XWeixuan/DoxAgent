from pathlib import Path
from doxagent.message_bus_v2.corporate_ir.body import inspect_article
from xml.etree import ElementTree as E
r=Path('eval/rss_http_race_20261107')
for n in ['mu','be','rklb','onds']:
 xml=E.fromstring((r/(n+'-ir-rss.body')).read_text(encoding='utf8'));it=xml.find('.//item');u=it.findtext('link');title=it.findtext('title')
 p=r/(n+'-ir-article-us.body' if n in ['mu','be'] else n+'-article-html.body')
 c,q,reason=inspect_article(p.read_text(encoding='utf8'),u,title)
 print(n,q,reason,len(c.text) if c else 0)
