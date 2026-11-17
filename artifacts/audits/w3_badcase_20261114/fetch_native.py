import pathlib,json,gzip,base64
paths={'01a1c97b-c51d-7820-92f8-1617e60a3134': '/data/codex-home/sessions/2026/11/11/rollout-2026-11-11T07-21-34-01a1c97b-c51d-7820-92f8-1617e60a3134.jsonl', '01a1ae33-8a7f-7333-b738-f9c97c819cf2': '/data/codex-home/sessions/2026/11/06/rollout-2026-11-06T00-12-55-01a1ae33-8a7f-7333-b738-f9c97c819cf2.jsonl', '01a1ba20-d1bb-7e30-a55a-7c287b0411bb': '/data/codex-home/sessions/2026/11/08/rollout-2026-11-08T07-47-55-01a1ba20-d1bb-7e30-a55a-7c287b0411bb.jsonl', '01a1cfc1-4aee-7070-a866-5555b57408d7': '/data/codex-home/sessions/2026/11/12/rollout-2026-11-12T12-35-13-01a1cfc1-4aee-7070-a866-5555b57408d7.jsonl'}
for tid,p in paths.items():
 q=pathlib.Path("/var/lib/docker/volumes/doxagent-v2_v2-data/_data")/p.removeprefix("/data/")
 print(json.dumps({"id":tid,"path":p,"exists":q.exists(),"data":base64.b64encode(gzip.compress(q.read_bytes())).decode() if q.exists() else None}),flush=True)
