import json
from pathlib import Path
r=Path('/data/workspaces/adopt-v21-rklb-20261013-b30ac7c1ddf4-d3/artifacts/document3/releases/13/document3.json')
s=Path('/data/rklb-v21-test/workspaces/rklb-v21-20261012-01-d3/artifacts/document3/releases/12/document3.json')
a=json.loads(r.read_text());b=json.loads(s.read_text());assert a['policies']==b['policies'];assert a['document2_ref']==b['document2_ref'];print('policies_exact_match',len(a['policies']),'publication_state',a['publication_state'])
