from pathlib import Path
import json
from doxagent.production_v21 import upgrade_v21
locations={k:Path('/data')/v for k,v in dict(research='research/research.sqlite3',initialization='initialization/control.sqlite3',runtime='runtime/runtime.sqlite3',bus='bus/bus.sqlite3',read='read/v2.sqlite3',scheduler='scheduler/scheduler.sqlite3').items()}
print(json.dumps(upgrade_v21(locations,Path('/data/production-backups/v21-20261012/backup-manifest.json')),indent=2))
