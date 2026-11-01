"""Pass a hidden credential to a child process environment, never a file or log."""
import getpass
import os
import subprocess
import sys
key = getpass.getpass('Zyte credential (hidden): ')
if not key:
    raise SystemExit('No credential supplied; no request sent')
raise SystemExit(subprocess.call([sys.executable, *sys.argv[1:]], env=dict(os.environ, ZYTE_API_KEY=key)))
