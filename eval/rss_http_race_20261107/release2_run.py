import subprocess
from pathlib import Path
r=subprocess.run(['ssh','doxagent-sg','sudo -n python3 - db08791f'],input=Path('eval/rss_http_race_20261107/release2.py').read_bytes(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
Path('eval/rss_http_race_20261107/release2.log').write_bytes(r.stdout)
print(r.stdout.decode('utf8',errors='replace')[-3000:]);r.check_returncode()
