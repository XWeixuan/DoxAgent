import json
import time
from pathlib import Path

def cpu():return [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
before=cpu();time.sleep(1);after=cpu();delta=[b-a for a,b in zip(before,after)]
print(json.dumps(dict(interval_seconds=1,busy_percent=round(100*(sum(delta)-delta[3]-delta[4])/sum(delta),2),
    load_average=Path('/proc/loadavg').read_text().strip())))
