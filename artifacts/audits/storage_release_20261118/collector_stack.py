import hashlib,io,json,pathlib,shutil,subprocess,tempfile,urllib.request,zipfile
work=pathlib.Path(tempfile.mkdtemp(prefix='dox-release-collector-stack-'))
try:
    raw=urllib.request.urlopen('https://files.pythonhosted.org/packages/78/1f/651389a861f6148754443834a50dd576993257af5f645d4316ab94505267/py_spy-0.4.3-py2.py3-none-manylinux1_x86_64.manylinux_2_5_x86_64.whl',timeout=20).read()
    assert hashlib.sha256(raw).hexdigest()=='8a4277225f470d394a85cca0cdd944bef87f0097b6f20150a6dd3ac2e92806b8'
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        binary=archive.read(next(n for n in archive.namelist() if n.endswith('/py-spy')))
    assert hashlib.sha256(binary).hexdigest()=='a41205d4925beafd108e1ace85e66c0a1a7adbfcbebcd4441f07848b608886c0'
    target=work/'py-spy'; target.write_bytes(binary);target.chmod(0o700)
    table=subprocess.check_output(['docker','top','doxagent-v2-v2-message-collector-1','-eo','pid,args'],text=True)
    pids=[r.split()[0] for r in table.splitlines()[1:] if 'doxagent.message_bus_v2.cli run-collector' in r and '/sbin/docker-init' not in r]
    assert len(pids)==1, table
    subprocess.run([str(target),'dump','--pid',pids[0]],check=True,timeout=15)
finally:
    assert work.resolve().parent==pathlib.Path('/tmp') and work.name.startswith('dox-release-collector-stack-')
    shutil.rmtree(work)
