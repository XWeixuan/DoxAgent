"""Immutable W3 research views, explicit frozen scopes, and bounded complete reads.

No model, provider, or broker dependency is constructed here. Shared versions are
installed once; a Case owns references, not copies of the research documents.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

ENTRY = "w3-entry-v5"
RENDERER = "w3-research-v2"
PARTS = {
    "state": "state",
    "baseline": "expectation_baseline",
    "factors": "realization_factors",
    "gaps": "potential_gaps",
}


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        pending.write_bytes(data)
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def business(value, refs=None, pointer=""):
    """Move citations, never drop unknown business fields or coerce missing/null."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            child = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key == "ref":
                if refs is not None:
                    refs[child] = item
            else:
                result[key] = business(item, refs, child)
        return result
    if isinstance(value, list):
        return [business(v, refs, pointer + "/" + str(i)) for i, v in enumerate(value)]
    return value


def columnar(value):
    """Lossless repeated-key reduction; absent columns remain distinguishable."""
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        columns = list(dict.fromkeys(k for v in value for k in v))
        return {
            "columns": columns,
            "rows": [[columnar(v.get(k)) for k in columns] for v in value],
            "missing": [[i for i, k in enumerate(columns) if k not in v] for v in value],
        }
    if isinstance(value, dict):
        return {k: columnar(v) for k, v in value.items()}
    if isinstance(value, list):
        return [columnar(v) for v in value]
    return value


def compile_objects(kind, raw, ticker):
    objects = []

    def add(k, identity, body, *, shell=None, scope="", sources=None):
        objects.append(
            dict(kind=k, id=identity, shell=shell, scope=scope, body=body, sources=sources or {})
        )

    if kind == "d1":

        def d1_body(text):
            sources = {}

            def citation(match):
                sources.setdefault("markers", []).append(match[0])
                return ""

            def link(match):
                sources.setdefault("links", []).append({"label": match[1], "url": match[2]})
                return match[1]

            clean = re.sub(r"【cite:[^】]+】", citation, text)
            clean = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", link, clean)
            return clean, sources

        def add_d1(identity, text):
            body, sources = d1_body(text)
            add("d1_section", identity, body, sources=sources)

        # Published C1/C3/C5 and their internal headings share markdown depth.
        starts = list(re.finditer(r"^#{1,3}\s+(C\d+)\b[^\n]*", raw, re.M))
        if not starts:
            starts = list(re.finditer(r"^#{1,2}\s+([^\n]+)", raw, re.M))
        if not starts:
            add_d1("document1", raw)
        else:
            if starts[0].start():
                add_d1("前言", raw[: starts[0].start()])
            for i, match in enumerate(starts):
                end = starts[i + 1].start() if i + 1 < len(starts) else len(raw)
                add_d1(match[1], raw[match.start() : end])
    elif kind == "d2":
        value = json.loads(raw)
        add("source", "d2-publication-inputs", value.get("input_manifest", {}))
        for shell in value.get("shells", []):
            parent = {k: v for k, v in shell.items() if k not in {"units", "ref"}}
            for unit in shell.get("units", []):
                refs = {"shell/ref": shell.get("ref", [])}
                clean = business(unit, refs)
                body = dict(
                    ticker=ticker,
                    research_as_of=value.get("as_of"),
                    parent_shell=parent,
                    unit=clean,
                )
                add(
                    "d2_unit",
                    unit["name"],
                    body,
                    shell=shell["name"],
                    scope=unit.get("scope", ""),
                    sources=refs,
                )
    elif kind == "policy":
        value = json.loads(raw)
        for policy in value.get("policies", []):
            refs = {}
            add(
                "policy",
                policy["policy_id"],
                business(policy, refs),
                scope=policy.get("title", ""),
                sources=refs,
            )
            objects[-1]["research_origin"] = policy.get("ref") or {}
    elif kind == "reference":
        add("reference", "reference", raw)
        for match in re.finditer(r"^#{1,6}\s+.*?\b(E\d+)\b[^\n]*", raw, re.M):
            next_heading = re.search(r"^#{1,6}\s", raw[match.end() :], re.M)
            end = match.end() + next_heading.start() if next_heading else len(raw)
            add("reference_event", match[1], raw[match.start() : end])
    else:
        raise ValueError("unknown research component kind")
    add("raw_document", kind, raw)
    return objects


class W3ResearchStore:
    def __init__(self, root, *, page_bytes=16384, page_tokens=4096):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "worker-jobs.sqlite3"
        self.packs = self.root / ".w3-packs" / "v2"
        self.contexts = self.root / ".w3-contexts"
        self.page_bytes, self.page_tokens = page_bytes, page_tokens
        self._lock = threading.RLock()
        self._cache = {}
        self._encoding = None
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS w3_asset_builds (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, ticker TEXT NOT NULL,
                  source_sha TEXT NOT NULL, source_path TEXT NOT NULL,
                  state TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT);
                CREATE TABLE IF NOT EXISTS w3_case_contexts (
                  token_hash TEXT PRIMARY KEY, case_id TEXT NOT NULL, phase TEXT NOT NULL,
                  manifest_sha TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL,
                  UNIQUE(case_id,phase));
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def initialize(self):
        import tiktoken

        self._encoding = tiktoken.get_encoding("o200k_base")

    def enqueue(self, kind, ticker, raw=None, source_path=None, expected_sha=None):
        if raw is not None:
            data = raw.encode()
            source_sha = sha(data)
            path = self.root / ".w3-sources" / source_sha
            for canonical in (
                self.root.parent / "research" / "published-contents" / source_sha,
                self.root.parent / "published-contents" / source_sha,
            ):
                if canonical.is_file() and sha(canonical.read_bytes()) == source_sha:
                    path = canonical
                    break
            if not path.exists():
                atomic(path, data)
        else:
            path = Path(source_path).resolve()
            # A source locator is host-owned, never an Agent tool argument.
            if not path.is_file():
                raise FileNotFoundError(path)
            source_sha = expected_sha or sha(path.read_bytes())
        identity = sha(compact([source_sha, kind, ticker, RENDERER]))
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO w3_asset_builds VALUES(?,?,?,?,?,'PENDING',?,NULL)",
                (identity, kind, ticker, source_sha, str(path), datetime.now(UTC).isoformat()),
            )
        return identity

    def build(self, identity):
        with self._lock:
            with closing(self.connect()) as db:
                row = db.execute("SELECT * FROM w3_asset_builds WHERE id=?", (identity,)).fetchone()
            if not row:
                raise FileNotFoundError("research source not registered")
            target = self.packs / identity
            if (target / "manifest.json").is_file():
                return identity
            try:
                raw = Path(row["source_path"]).read_bytes()
                if sha(raw) != row["source_sha"]:
                    raise ValueError("research source checksum mismatch")
                text = raw.decode()
                index_error = None
                try:
                    objects = compile_objects(row["kind"], text, row["ticker"])
                except (ValueError, KeyError, TypeError) as exc:
                    # Indexing optional content is not a Case admission gate.
                    # Exact original stays readable; do not substitute another pin.
                    index_error = type(exc).__name__ + ": " + str(exc)
                    objects = [
                        dict(kind="raw_document", id=row["kind"], body=text, sources={}),
                        dict(
                            kind="source",
                            id=row["kind"] + "-index-status",
                            body={
                                "status": "INDEX_UNAVAILABLE",
                                "error": index_error,
                                "read_original": row["kind"],
                            },
                            sources={},
                        ),
                    ]
                content = compact(objects).encode()
                atomic(target / "objects.json", content)
                index = [
                    {k: v for k, v in obj.items() if k not in {"body", "sources"}}
                    | {"characters": len(compact(obj["body"]))}
                    for obj in objects
                ]
                atomic(target / "index.json", compact(index).encode())
                atomic(
                    target / "manifest.json",
                    compact(
                        dict(
                            source_sha=row["source_sha"],
                            renderer=RENDERER,
                            objects_sha=sha(content),
                            source_path=row["source_path"],
                            index_status="READY" if index_error is None else "INDEX_UNAVAILABLE",
                            index_error=index_error,
                        )
                    ).encode(),
                )
                state, error = "READY", None
            except Exception as exc:
                state, error = "FAILED", str(exc)
                raise
            finally:
                with closing(self.connect()) as db, db:
                    db.execute(
                        "UPDATE w3_asset_builds SET state=?,error=?,updated_at=? WHERE id=?",
                        (state, error, datetime.now(UTC).isoformat(), identity),
                    )
        return identity

    def reconcile(self):
        with closing(self.connect()) as db:
            pending = db.execute(
                "SELECT id FROM w3_asset_builds WHERE state IN ('PENDING','BUILDING') LIMIT 8"
            ).fetchall()
        for row in pending:
            try:
                self.build(row[0])
            except (OSError, ValueError):
                continue

    def install(self, components, ticker):
        return {
            kind: self.build(self.enqueue(kind, ticker, raw=raw))
            for kind, raw in components.items()
        }

    def bind(self, case_id, phase, manifest):
        manifest = dict(manifest)
        refs = {}
        for key in ("policy_usage", "history", "state", "events", "evidence", "market_view"):
            if key not in manifest:
                continue
            content = compact(manifest.pop(key)).encode()
            identity = sha(content)
            target = self.contexts / "snapshots" / (identity + ".json")
            if not target.exists():
                atomic(target, content)
            refs[key] = identity
        manifest["snapshot_refs"] = refs
        data = compact(manifest).encode()
        identity = sha(data)
        # Token is deterministic for this immutable manifest, reusable after crash.
        seed_path = self.root / ".w3-context-secret"
        with self._lock:
            if not seed_path.exists():
                atomic(seed_path, secrets.token_bytes(32))
            import hmac

            token = "w3ctx_" + hmac.new(seed_path.read_bytes(), data, hashlib.sha256).hexdigest()
            atomic(self.contexts / (identity + ".json"), data)
            with closing(self.connect()) as db, db:
                previous = db.execute(
                    "SELECT manifest_sha FROM w3_case_contexts WHERE case_id=? AND phase=?",
                    (case_id, phase),
                ).fetchone()
                if previous and previous[0] != identity:
                    raise ValueError("frozen W3 context cannot change during a phase")
                db.execute(
                    "INSERT OR IGNORE INTO w3_case_contexts VALUES(?,?,?,?,?,'ACTIVE')",
                    (
                        sha(token),
                        case_id,
                        phase,
                        identity,
                        (datetime.now(UTC) + timedelta(days=7)).isoformat(),
                    ),
                )
        direct = [
            {"kind": "policy", "id": pid, "provided_inline": True}
            for pid in manifest.get("inline_policy_ids", [])
        ]
        indices = {}
        for kind, component in manifest.get("components", {}).items():
            index = self.packs / component / "index.json"
            if index.exists() and kind in {"d2", "policy"}:
                indices[kind] = json.loads(index.read_bytes())
        for policy in indices.get("policy", []):
            if policy["id"] not in manifest.get("inline_policy_ids", []):
                continue
            origin = policy.get("research_origin", {})
            if isinstance(origin, dict):
                name = origin.get("expectation_id") or origin.get("unit")
                shell = origin.get("shell_id") or origin.get("shell")
                for unit in indices.get("d2", []):
                    if (
                        unit["kind"] == "d2_unit"
                        and unit["id"] == name
                        and unit.get("shell") == shell
                    ):
                        direct.append({"kind": "d2_unit", "id": name, "shell": shell})
        return {"context_ref": token, "context_manifest_sha": identity, "direct_objects": direct}

    def context(self, token):
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT * FROM w3_case_contexts WHERE token_hash=?", (sha(token),)
            ).fetchone()
        if not row:
            raise PermissionError("unknown research context")
        if row["expires_at"] < datetime.now(UTC).isoformat():
            raise PermissionError("research context expired; restore its frozen manifest")
        data = (self.contexts / (row["manifest_sha"] + ".json")).read_bytes()
        if sha(data) != row["manifest_sha"]:
            raise ValueError("context checksum mismatch")
        manifest = json.loads(data)
        for key, identity in manifest.pop("snapshot_refs", {}).items():
            raw = (self.contexts / "snapshots" / (identity + ".json")).read_bytes()
            if sha(raw) != identity:
                raise ValueError("frozen state checksum mismatch")
            manifest[key] = json.loads(raw)
        return row["manifest_sha"], manifest

    def objects(self, manifest):
        result = []
        for identity in manifest.get("components", {}).values():
            if identity not in self._cache:
                self.build(identity)
                root = self.packs / identity
                data = (root / "objects.json").read_bytes()
                meta = json.loads((root / "manifest.json").read_bytes())
                if sha(data) != meta["objects_sha"]:
                    raise ValueError("compiled research checksum mismatch")
                if len(self._cache) >= 16:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[identity] = json.loads(data)
            result.extend(self._cache[identity])
        usage = manifest.get("policy_usage", [])
        result.extend(dict(kind="policy_usage", id=v["policy_id"], body=v) for v in usage)
        for key, val in manifest.get("state", {}).items():
            result.append(dict(kind="runtime_state", id=key, body=val))
        for key, val in manifest.get("events", {}).items():
            result.append(dict(kind="reference_event", id=key, body=val))
        for val in manifest.get("history", []):
            result.append(
                dict(kind="trade_intent", id=val.get("case_id") or sha(compact(val)), body=val)
            )
            message = val.get("message", {})
            result.append(
                dict(
                    kind="historical_message",
                    id=message.get("source_message_id") or sha(compact(message)),
                    body=message,
                )
            )
        for key, val in manifest.get("evidence", {}).items():
            result.append(dict(kind="raw_document", id=key, body=val))
        return result

    def page(self, context_sha, identity, text, *, cursor=None, max_bytes=None, reserved_bytes=0):
        signature = sha(compact([context_sha, identity, sha(text)]))
        start = 0
        if cursor:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor))
                if value["signature"] != signature:
                    raise ValueError("cursor does not match this immutable read")
                start = value["offset"]
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError("invalid or mismatched research cursor") from exc
        if not isinstance(start, int) or not 0 <= start <= len(text):
            raise ValueError("cursor offset out of range")
        budget = min(max_bytes or self.page_bytes, self.page_bytes)
        if budget < 1024:
            raise ValueError("max_bytes must be at least 1024")
        budget -= reserved_bytes

        def envelope(end):
            body = text[start:end]
            next_cursor = (
                None
                if end == len(text)
                else base64.urlsafe_b64encode(
                    compact(dict(signature=signature, offset=end, resource=identity)).encode()
                ).decode()
            )
            return dict(
                context_manifest_sha=context_sha,
                renderer=RENDERER,
                page=dict(id=signature, range=[start, end], complete=end == len(text)),
                body=body,
                next_cursor=next_cursor,
                receipt=f"END {signature} {sha(body)} {start}:{end}",
            )

        lo, hi = start, min(len(text), start + budget)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            wire = compact(envelope(mid))
            tokens = (
                len(self._encoding.encode(wire, disallowed_special=()))
                if self._encoding
                else len(wire.encode())
            )
            if len(wire.encode()) <= budget and tokens <= self.page_tokens:
                lo = mid
            else:
                hi = mid - 1
        if lo == start and lo != len(text):
            raise ValueError("response budget cannot fit page envelope")
        return envelope(lo)

    def call(self, name, arguments):
        context_sha, manifest = self.context(arguments["context_ref"])
        objects = self.objects(manifest)
        if name == "read":
            selected = []
            for wanted in arguments.get("objects", []):
                matches = [
                    obj
                    for obj in objects
                    if obj["kind"] == wanted["kind"]
                    and obj["id"] == wanted["id"]
                    and (not wanted.get("shell") or obj.get("shell") == wanted["shell"])
                ]
                if not matches:
                    raise FileNotFoundError("research object unavailable: " + compact(wanted))
                if len(matches) > 1:
                    # Exact inline Event wins over a duplicate Reference heading.
                    if wanted["kind"] != "reference_event":
                        raise ValueError("ambiguous object; include shell: " + compact(wanted))
                    matches = matches[-1:]
                obj = matches[0]
                body = obj.get("sources", {}) if arguments.get("view") == "sources" else obj["body"]
                if arguments.get("view") == "sources":
                    body = {
                        **body,
                        "__resolution": "Original markers; unresolved markers are not URLs",
                        "__namespace": [context_sha, obj.get("shell"), obj["id"]],
                        "__raw_document": {
                            "d2_unit": "d2",
                            "d1_section": "d1",
                            "reference_event": "reference",
                            "reference": "reference",
                        }.get(wanted["kind"], "policy"),
                    }
                if arguments.get("view") == "raw" and wanted["kind"] in {"policy", "d2_unit"}:
                    raw_kind = "policy" if wanted["kind"] == "policy" else "d2"
                    raw = json.loads(
                        next(
                            o["body"]
                            for o in objects
                            if o["kind"] == "raw_document" and o["id"] == raw_kind
                        )
                    )
                    if raw_kind == "policy":
                        body = next(p for p in raw["policies"] if p["policy_id"] == wanted["id"])
                    else:
                        body = next(
                            u
                            for s in raw["shells"]
                            for u in s["units"]
                            if u["name"] == wanted["id"]
                            and (not wanted.get("shell") or s["name"] == wanted["shell"])
                        )
                if (
                    wanted["kind"] == "d2_unit"
                    and wanted.get("parts")
                    and arguments.get("view", "business") == "business"
                ):
                    fields = [PARTS[p] for p in wanted["parts"]]
                    body = {
                        **body,
                        "unit": {
                            k: v
                            for k, v in body["unit"].items()
                            if k in {"name", "scope", "horizon", *fields}
                        },
                    }
                item = {
                    "object": wanted,
                    "body": body if arguments.get("view") == "raw" else columnar(body),
                }
                if wanted["kind"] == "policy":
                    item["frozen_usage"] = next(
                        (
                            u
                            for u in manifest.get("policy_usage", [])
                            if u["policy_id"] == wanted["id"]
                        ),
                        {"status": "UNKNOWN"},
                    )
                selected.append(item)
            text = compact(selected)
        elif name in {"catalog", "search"}:
            selected = []
            query = arguments.get("query", "").casefold()
            for obj in objects:
                if arguments.get("kind") and obj["kind"] != arguments["kind"]:
                    continue
                if arguments.get("shell") and obj.get("shell") != arguments["shell"]:
                    continue
                body = compact(obj["body"])
                if name == "search" and query not in compact(obj).casefold():
                    continue
                meta = {k: v for k, v in obj.items() if k not in {"body", "sources"}}
                if name == "search":
                    pos = max(0, body.casefold().find(query))
                    meta["snippet"] = body[max(0, pos - 80) : pos + 250]
                selected.append(meta)
            text = compact(selected)
        else:
            raise ValueError("unknown research tool")
        return self.page(
            context_sha,
            compact(
                [
                    name,
                    {
                        k: v
                        for k, v in arguments.items()
                        if k not in {"cursor", "max_bytes", "context_ref"}
                    },
                ]
            ),
            text,
            cursor=arguments.get("cursor"),
            max_bytes=arguments.get("max_bytes"),
        )
