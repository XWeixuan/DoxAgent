import hashlib,json
from pathlib import Path
print(json.dumps({n:hashlib.sha256(Path('/app/src/doxagent/message_bus_v2/'+n+'.py').read_text().encode()).hexdigest() for n in ['massive_relevance','relevance','monitoring_terms']}))
