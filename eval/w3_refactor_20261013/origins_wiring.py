exec(__import__('pathlib').Path('eval/w3_refactor_20261013/finish.py').read_text(encoding='utf-8-sig').split("edit('src/")[0])
edit('src/doxagent/workflows/codex_document3/orchestrator_v21.py',
     'content = {\n            "document3.json":',
     '''from .research_origins import policy_origins

        inherited = {}
        base_version = run["identity"].get("base_policy_version")
        if base_version is not None:
            base = self.state.get_staged_by_run(self._base_run_id(policy_set.ticker, base_version))
            inherited = base[1].get("policy_origins", {}) if base else {}
        origins = policy_origins(run, self.state.drafts(run_id), inherited)
        content = {
            "policy_origins.json": canonical(origins),
            "document3.json":''')
edit('src/doxagent/workflows/codex_document3/orchestrator_v21.py',
     '"projection": project_policy_set_v3(policy_set),',
     '"projection": project_policy_set_v3(policy_set),\n            "policy_origins": origins,')
edit('src/doxagent/workflows/codex_document3/publication_v21.py',
     'for name in ("document3.json", "runtime_projection.json", "coverage_map.json", "document3.md"):',
     'for name in ("document3.json", "runtime_projection.json", "coverage_map.json", "document3.md",\n+                 *(["policy_origins.json"] if "policy_origins.json" in receipt["files"] else [])):')
edit('src/doxagent/persistent_runtime_v2/w3.py',
     'prepared = W3PreparedContext(',
     '''origins = {}
        original_policy = policy_set.model_dump_json()
        policy_path = getattr(self._policies, "path", None)
        if policy_path:
            import sqlite3
            from contextlib import closing

            with closing(sqlite3.connect(str(policy_path))) as db:
                table = db.execute("SELECT 1 FROM sqlite_master WHERE name='codex_document3_v21_staged_sets'").fetchone()
                row = db.execute("SELECT run_id,receipt FROM codex_document3_v21_staged_sets WHERE ticker=? AND version=?",
                                 (case.ticker, policy_set.policy_set_version)).fetchone() if table else None
            if row:
                receipt = json.loads(row[1])
                origins = receipt.get("policy_origins", {})
                bundle = self._runtime.get_bundle(row[0])
                handoff = getattr(bundle, "handoff", None)
                artifact = getattr(handoff, "published_artifact", None)
                if artifact:
                    published = self._runtime.get_published_document(row[0], artifact.artifact_id)
                    if published:
                        original_policy = (await self._read_published(published)).decode("utf-8")
        prepared = W3PreparedContext(''')
edit('src/doxagent/persistent_runtime_v2/w3.py',
     'original_policy_set=policy_set.model_dump_json(),',
     'original_policy_set=original_policy,\n            policy_origins=origins,')
