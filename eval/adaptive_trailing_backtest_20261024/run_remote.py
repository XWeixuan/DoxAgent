"""Run an audit script via SSH stdin without writing production files."""
import pathlib,subprocess,sys
root=pathlib.Path(__file__).parent
script=root/sys.argv[1]
command='sudo -n docker exec -i doxagent-v2-v2-api-1 python -'
result=subprocess.run(['ssh','-o','BatchMode=yes','-o','PasswordAuthentication=no','doxagent-sg',command],input=script.read_bytes(),capture_output=True)
(root/sys.argv[2]).write_bytes(result.stdout)
(root/(sys.argv[2]+'.stderr')).write_bytes(result.stderr)
print('exit',result.returncode,'stdout_bytes',len(result.stdout),'stderr',result.stderr.decode(errors='replace')[-1200:])
sys.exit(result.returncode)
