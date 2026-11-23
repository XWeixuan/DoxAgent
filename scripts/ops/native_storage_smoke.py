"""Offline native lifecycle smoke: local synthetic Responses SSE, no paid calls."""
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Provider(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length','0')))
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        message={'id':'msg_smoke','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'storage smoke accepted','annotations':[]}]}
        response={'id':'resp_smoke','object':'response','status':'completed','model':'gpt-5','output':[message],'usage':{'input_tokens':10,'output_tokens':4,'total_tokens':14}}
        events=[('response.created',{'response':{**response,'status':'in_progress','output':[]}}),
                ('response.output_item.added',{'output_index':0,'item':{**message,'status':'in_progress','content':[]}}),
                ('response.content_part.added',{'item_id':'msg_smoke','output_index':0,'content_index':0,'part':{'type':'output_text','text':'','annotations':[]}}),
                ('response.output_text.delta',{'item_id':'msg_smoke','output_index':0,'content_index':0,'delta':'storage smoke accepted'}),
                ('response.output_text.done',{'item_id':'msg_smoke','output_index':0,'content_index':0,'text':'storage smoke accepted'}),
                ('response.output_item.done',{'output_index':0,'item':message}),
                ('response.completed',{'response':response})]
        for name,data in events:
            self.wfile.write(('event: '+name+'\ndata: '+json.dumps({'type':name,**data})+'\n\n').encode());self.wfile.flush()


def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='native-storage-smoke-') as directory:
        home=Path(directory);(home/'config.toml').write_text(f'''model = "gpt-5"
model_provider = "local_smoke"
approval_policy = "never"
sandbox_mode = "danger-full-access"
[model_providers.local_smoke]
name = "Offline smoke"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
''')
        env={**os.environ,'CODEX_HOME':str(home),'RUST_LOG':'trace'}
        stderr=open(home/'stderr.log','w')
        process=subprocess.Popen(['/native/codex','app-server','--listen','stdio://'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,env=env)
        received=queue.Queue()
        def reader():
            for line in process.stdout:
                try:received.put(json.loads(line))
                except ValueError:pass
        threading.Thread(target=reader,daemon=True).start()
        counter=0;notifications=[]
        def send(value):process.stdin.write(json.dumps(value)+'\n');process.stdin.flush()
        def rpc(method,params,timeout=60):
            nonlocal counter
            counter+=1;identity=counter;send({'jsonrpc':'2.0','id':identity,'method':method,'params':params})
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                msg=received.get(timeout=max(.1,deadline-time.monotonic()))
                if msg.get('id')==identity:
                    if 'error' in msg:raise RuntimeError(str(msg['error']))
                    return msg.get('result')
                notifications.append(msg)
            raise TimeoutError(method)
        try:
            rpc('initialize',{'clientInfo':{'name':'storage-offline-smoke','version':'1'},'capabilities':{'experimentalApi':True}})
            send({'jsonrpc':'2.0','method':'initialized'})
            thread=rpc('thread/start',{'model':'gpt-5','modelProvider':'local_smoke','cwd':'/tmp','approvalPolicy':'never','sandbox':'danger-full-access'})['thread']
            identity=thread['id']
            rpc('turn/start',{'threadId':identity,'input':[{'type':'text','text':'Reply with storage smoke accepted.','text_elements':[]}]})
            deadline=time.monotonic()+90
            while not any(n.get('method')=='turn/completed' for n in notifications):
                if time.monotonic()>deadline:raise TimeoutError('turn/completed')
                notifications.append(received.get(timeout=10))
            completed=[n for n in notifications if n.get('method')=='turn/completed'][-1]
            assert completed['params']['turn']['status']=='completed',completed
            meta=rpc('thread/read',{'threadId':identity,'includeTurns':False})['thread']
            time.sleep(2)
            rpc('thread/delete',{'threadId':identity})
            path=meta.get('path');assert not path or not Path(path).exists(),path
            counts={}
            for database in home.glob('*history*.sqlite'):
                with sqlite3.connect(database) as db:
                    for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                        columns={r[1] for r in db.execute('PRAGMA table_info("'+table+'")')}
                        if 'thread_id' in columns:
                            count=db.execute('SELECT count(*) FROM "'+table+'" WHERE thread_id=?',(identity,)).fetchone()[0]
                            counts[table]=count;assert count==0,(table,count)
            logs={}
            for database in home.glob('logs*.sqlite'):
                with sqlite3.connect(database) as db:
                    logs=dict(db.execute('SELECT level,count(*) FROM logs GROUP BY level'))
                    assert not (set(logs)-{'WARN','ERROR'}),logs
                    assert db.execute('SELECT coalesce(max(length(CAST(feedback_log_body AS BLOB))),0) FROM logs').fetchone()[0]<=2100
            print(json.dumps({'version':subprocess.check_output(['/native/codex','--version'],text=True).strip(),'turn_completed':True,'rollout_deleted':True,'history_remaining':counts,'log_levels_under_trace':logs,'network':'Docker --network none; loopback synthetic provider only'},sort_keys=True))
        except Exception:
            print(json.dumps({'recent_notifications':notifications[-6:],'stderr_tail':(home/'stderr.log').read_text()[-4000:]}));raise
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            stderr.close()
    server.shutdown()


if __name__=='__main__':main()
