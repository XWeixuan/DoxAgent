import subprocess
from pathlib import Path
repo='/home/ubuntu/doxagent';commit='9fedb4ea9';root=Path('/var/lib/doxagent/projector-recovery');root.mkdir(parents=True,exist_ok=True)
raw=subprocess.check_output(['sudo','-u','ubuntu','git','-C',repo,'show',commit+':scripts/ops/projector_recovery.py'])
p=root/'projector_recovery.py';p.write_bytes(raw);p.chmod(0o644)
Path('/etc/systemd/system/doxagent-projector-recovery.service').write_text('''[Unit]
Description=DoxAgent desired Projector ENOSPC startup recovery
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /var/lib/doxagent/projector-recovery/projector_recovery.py
TimeoutStartSec=50
MemoryMax=64M
Nice=10
''')
Path('/etc/systemd/system/doxagent-projector-recovery.timer').write_text('''[Unit]
Description=Check desired Projector recovery every minute
[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=5
[Install]
WantedBy=timers.target
''')
(root/'enabled').touch()
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','doxagent-projector-recovery.timer'],check=True)
subprocess.run(['systemctl','start','doxagent-projector-recovery.service'],check=True)
print('Guard installed and first check succeeded; pause marker:',root/'paused')
