import importlib.util,sys,json
from pathlib import Path
spec=importlib.util.spec_from_file_location('doxagent.content_enrichment.publisher_documents','/tmp/newswire-publisher-documents.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
from doxagent.content_enrichment.quality import inspect_html,choose_candidate
cases=[('bw-post','https://www.businesswire.com/news/home/20261112795782/en/Helen-of-Troy-Issues-Statement/','Helen of Troy Issues Statement'),('prn-body','https://www.prnewswire.com/news-releases/edun-brings-live-rosin-to-the-holiday-kitchen-with-limited-edition-organic-evoo-launch-302938320.html','Edun Brings Live Rosin to the Holiday Kitchen With Limited-edition Organic EVOO Launch')]
out=[]
for label,url,title in cases:
 i=inspect_html(Path('/tmp/newswire-'+label+'.html').read_text(),url,title);c,o,r=choose_candidate(i,title)
 out.append({'case':label,'outcome':o,'reason':r,'chars':len(c.text) if c else 0,'method':c.method if c else None,'expansion':i.expansion_required})
print(json.dumps(out))
