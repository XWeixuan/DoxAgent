from pathlib import Path
import urllib.request
url='https://www.nrc.gov/documents-reports/document-collections/events-reports-associated-with/event-notification-reports/en'
with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'DoxAgent-IR-Evaluation/1.0'}),timeout=15) as r:
 raw=r.read(2000000);Path('/tmp/nrc-report-repair-20261111.html').write_bytes(raw);print({'status':r.status,'bytes':len(raw)})
