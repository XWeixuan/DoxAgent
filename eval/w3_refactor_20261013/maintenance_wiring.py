exec(__import__('pathlib').Path('eval/w3_refactor_20261013/finish.py').read_text(encoding='utf-8-sig').split("edit('src/")[0])
edit('src/doxagent/persistent_runtime_v2/coordinator.py',
     'self.runtime.dispatch_pending_effects(limit=20)',
     'self.runtime.dispatch_pending_effects(limit=20)\n        execution = getattr(self.runtime, "_w3_v4_execution", None)\n        if execution is not None:\n            execution.maintain()')
edit('src/doxagent/codex_runtime/client.py',
     'async def fork_thread(self, request) -> dict:',
     '''async def register_legacy_thread(self, job_id: str) -> dict:
        response = await self._client.post("/v1/threads/register-legacy", json={"job_id": job_id})
        response.raise_for_status()
        return response.json()

    async def fork_thread(self, request) -> dict:''')
edit('src/doxagent/codex_worker/local_client.py',
     'async def fork_thread(self, request):',
     '''async def register_legacy_thread(self, job_id):
        return await self.manager.thread_lifecycle.register_legacy(job_id)

    async def fork_thread(self, request):''')
edit('src/doxagent/persistent_runtime_v2/w3.py',
     'def fork_thread(self, request):',
     '''def register_legacy_thread(self, job_id):
        return self._wait_v4(self._worker.register_legacy_thread(job_id))

    def fork_thread(self, request):''')
edit('src/doxagent/codex_worker/app.py',
     '@app.post("/v1/threads/fork",',
     '''@app.post("/v1/threads/register-legacy", dependencies=[Depends(require_service_auth)])
    async def register_legacy(request: ThreadTemplateRegisterRequest):
        try:
            return await jobs.thread_lifecycle.register_legacy(request.job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/threads/fork",''')
edit('src/doxagent/persistent_runtime_v2/projection.py',
     'w3_resolved=case.w3_result is not None,',
     'w3_resolved=case.w3_result is not None or case.w3_trade_decision is not None,')
edit('src/doxagent/persistent_runtime_v2/execution_bundle.py',
     'identity = self.journal.freeze(value)\n        if w3_contract_version',
     'if w3_contract_version')
edit('src/doxagent/persistent_runtime_v2/execution_bundle.py',
     '            identity = self.journal.freeze(value)\n        if activate:',
     '        identity = self.journal.freeze(value)\n        if activate:')
