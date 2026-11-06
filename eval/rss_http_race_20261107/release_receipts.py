import json
from pathlib import Path
print(json.dumps({name:json.loads((Path('/var/lib/doxagent')/name/'release.json').read_text()) for name in ['rss-http-race-20261107','rss-http-race-20261107-round2']}))
