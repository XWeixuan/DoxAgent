"""Bounded, owner-scoped deferred reads; never used for business mutations."""
import asyncio
import hashlib
import time
from datetime import datetime, UTC
from uuid import uuid4
from fastapi.responses import Response, JSONResponse
from .errors import ApiFailure
from .query_runner import QueryRunner


class DeferredQueries:
    def __init__(self):
        self.runner = QueryRunner(workers=1, queue_limit=2)
        self.start_lock = asyncio.Lock()
        self.started = False
        self.jobs = {}

    def key(self, job):
        principal = job["principal"]
        return hashlib.sha256((principal.user_id+":"+principal.tier+":"+job["url"]).encode()).hexdigest()

    def response(self, entry):
        if entry["result"] is not None:
            status,headers,body = entry["result"]
            return Response(body,status_code=status,headers=headers)
        return JSONResponse({"query_id":entry["id"],"state":entry["state"],"retry_after_seconds":2,
                             "expires_at":datetime.fromtimestamp(entry["expires"],UTC).isoformat().replace("+00:00","Z"),
                             "result_path":"/queries/"+entry["id"]},status_code=202,
                            headers={"Retry-After":"2","Cache-Control":"no-store"})

    def existing(self, job):
        now = time.time()
        self.jobs = {key:entry for key,entry in self.jobs.items() if entry["expires"]>now or not entry["task"].done()}
        entry = self.jobs.get(self.key(job))
        return self.response(entry) if entry else None

    async def submit(self, job):
        if job.get("method","GET") != "GET":
            raise ApiFailure("VALIDATION_FAILED",422)
        job = {**job,"headers":{key:value for key,value in job.get("headers",{}).items() if key != "if-none-match"}}
        existing = self.existing(job)
        if existing is not None:
            return existing
        if len(self.jobs)>=16 or sum(not entry["task"].done() for entry in self.jobs.values())>=2:
            raise ApiFailure("STORE_UNAVAILABLE",503,retryable=True)
        entry = {"id":uuid4().hex,"owner":job["principal"].user_id,"tier":job["principal"].tier,
                 "expires":time.time()+300,"result":None,"state":"QUEUED"}
        self.jobs[self.key(job)] = entry
        async def execute():
            try:
                async with self.start_lock:
                    if not self.started:
                        await self.runner.start()
                        self.started = True
                entry["pin"] = await self.runner.run({**job,"kind":"query_pin","query_id":entry["id"]},timeout=2)
                entry["expires"] = min(entry["expires"],int(entry["pin"].rsplit(".",1)[1]))
                entry["state"] = "RUNNING"
                entry["result"] = await self.runner.run(job,timeout=30)
                entry["state"] = "SUCCEEDED" if entry["result"][0] < 400 else "FAILED"
            except ApiFailure as exc:
                entry["state"] = "FAILED"
                import json
                entry["result"] = (exc.status,{"content-type":"application/json"},json.dumps(exc.payload(entry["id"])).encode())
            except Exception:
                entry["state"] = "FAILED"
                import json
                exc = ApiFailure("STORE_UNAVAILABLE",503,retryable=True)
                entry["result"] = (503,{"content-type":"application/json"},json.dumps(exc.payload(entry["id"])).encode())
        entry["task"] = asyncio.create_task(execute())
        return self.response(entry)

    def poll(self, principal, identity):
        entry = next((entry for entry in self.jobs.values() if entry["id"]==identity
                      and entry["owner"]==principal.user_id and entry["tier"]==principal.tier),None)
        if not entry or entry["expires"]<=time.time():
            raise ApiFailure("QUERY_EXPIRED",410,retryable=True)
        return self.response(entry)

    async def close(self):
        for entry in self.jobs.values():
            entry["task"].cancel()
        await asyncio.gather(*(entry["task"] for entry in self.jobs.values()),return_exceptions=True)
        await self.runner.close()
