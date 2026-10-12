"""Read-only Docker unique-size inventory; never remove rollback images."""
import http.client,json,socket
c=http.client.HTTPConnection('localhost',timeout=120)
sock=socket.socket(socket.AF_UNIX);sock.connect('/var/run/docker.sock');c.sock=sock
c.request('GET','/system/df');r=c.getresponse();p=json.loads(r.read())
rows=[{'id':v['Id'],'tags':v.get('RepoTags'), 'containers':v.get('Containers'),
       'unique_bytes':max(0,v.get('Size',0)-v.get('SharedSize',0))} for v in p['Images']]
print(json.dumps(sorted(rows,key=lambda x:x['unique_bytes'],reverse=True)[:35]))
