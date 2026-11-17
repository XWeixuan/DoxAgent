import tempfile,urllib.request,zipfile,io,hashlib,subprocess,json,os,shutil
from pathlib import Path
meta={'version': '0.4.3', 'wheel': 'py_spy-0.4.3-py2.py3-none-manylinux1_x86_64.manylinux_2_5_x86_64.whl', 'url': 'https://files.pythonhosted.org/packages/78/1f/651389a861f6148754443834a50dd576993257af5f645d4316ab94505267/py_spy-0.4.3-py2.py3-none-manylinux1_x86_64.manylinux_2_5_x86_64.whl', 'sha256': '8a4277225f470d394a85cca0cdd944bef87f0097b6f20150a6dd3ac2e92806b8', 'binary_sha256': 'a41205d4925beafd108e1ace85e66c0a1a7adbfcbebcd4441f07848b608886c0'}
work=Path(tempfile.mkdtemp(prefix='dox-projection-profile-d33-'))
try:
 raw=urllib.request.urlopen(meta['url'],timeout=20).read()
 assert hashlib.sha256(raw).hexdigest()==meta['sha256']
 with zipfile.ZipFile(io.BytesIO(raw)) as archive:
  name=next(n for n in archive.namelist() if n.endswith('/py-spy'))
  binary=archive.read(name)
 assert hashlib.sha256(binary).hexdigest()==meta['binary_sha256']
 target=work/'py-spy';target.write_bytes(binary);target.chmod(0o700)
 container=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-projector-1']))[0]
 pid=container['State']['Pid'];cg=Path('/proc',str(pid),'cgroup').read_text().split('::',1)[1].strip()
 pids=Path('/sys/fs/cgroup'+cg,'cgroup.procs').read_text().split();selected=[]
 for p in pids:
  args=Path('/proc',p,'cmdline').read_bytes().split(bytes([0]))
  if args and args[0].endswith(b'python') and b'doxagent.v2_read.cli' in args and b'project' in args:selected.append(p)
 assert len(selected)==1
 r=subprocess.run([str(target),'record','--pid',selected[0],'--duration','40','--rate','49','--nonblocking','--idle','--format','raw','--output',str(work/'out.raw')],capture_output=True,timeout=55)
 assert r.returncode==0,r.stderr.decode()
 print((work/'out.raw').read_text(),end='')
finally:
 assert work.resolve().parent==Path('/tmp') and work.name.startswith('dox-projection-profile-d33-')
 shutil.rmtree(work)
