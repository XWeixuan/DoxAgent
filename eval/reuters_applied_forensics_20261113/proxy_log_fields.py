"""Add destination-only fields to existing bounded Squid log. Never changes ACL/egress."""
import json,subprocess,hashlib,os
from pathlib import Path
p=Path('/var/lib/doxagent/captcha-proxy/squid.conf');before=p.read_bytes();old=b'logformat captcha %ts.%03tu %>a %Ss/%03>Hs %<st %rm %un';new=old+b' %>rd %<a:%<p';out={'changed':False}
assert before.count(old)==1
if new not in before:
 backup=p.with_name('squid.conf.before-forensics-20261113')
 if backup.exists():assert backup.read_bytes()==before
 else:
  fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as stream:stream.write(before)
 p.write_bytes(before.replace(old,new))
 try:
  subprocess.run(['docker','exec','doxagent-captcha-proxy','squid','-k','parse','-f','/config/squid.conf'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  assert subprocess.check_output(['docker','exec','doxagent-captcha-proxy','cat','/proc/1/comm'],text=True).strip()=='squid'
  subprocess.run(['docker','kill','--signal=HUP','doxagent-captcha-proxy'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 except Exception:
  p.write_bytes(before);raise
 out['changed']=True
out['format_domain_only']=new in p.read_bytes();out['other_config_unchanged']=p.read_bytes().replace(new,old)==before.replace(new,old);print(json.dumps(out,indent=2))
