"""Immutable, individually compressed chunks; hashes identify uncompressed bytes."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

CHUNK = 131072


class ContentFiles:
    CHUNK = CHUNK

    def __init__(self, root: Path):
        self.root = root

    def _path(self, identity: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("invalid content identity")
        return self.root / identity[:2] / identity

    def put(self, raw: bytes) -> str:
        identity = hashlib.sha256(raw).hexdigest()
        target = self._path(identity)
        if target.exists():
            return identity
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(identity + "." + uuid4().hex)
        temporary.mkdir()
        try:
            blocks = []
            for offset in range(0, len(raw), CHUNK):
                chunk = raw[offset:offset + CHUNK]
                blocks.append(hashlib.sha256(chunk).hexdigest())
                with (temporary / str(offset // CHUNK)).open("xb") as stream:
                    stream.write(gzip.compress(chunk, mtime=0))
                    stream.flush()
                    os.fsync(stream.fileno())
            with (temporary / "manifest.json").open("x", encoding="utf-8") as stream:
                json.dump({"version": 1, "size": len(raw), "sha256": identity, "chunks": blocks}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                temporary.rename(target)
            except OSError:
                if not target.exists():
                    raise
            if os.name != "nt":
                fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return identity
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def read(self, identity: str, offset: int, size: int) -> bytes:
        if offset < 0 or not 0 < size <= CHUNK:
            raise ValueError("invalid content range")
        path = self._path(identity)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if manifest["version"] != 1 or manifest["sha256"] != identity:
            raise ValueError("content manifest mismatch")
        end = min(manifest["size"], offset + size)
        if end <= offset:
            return b""
        chunks = []
        for index in range(offset // CHUNK, (end - 1) // CHUNK + 1):
            chunk = gzip.decompress((path / str(index)).read_bytes())
            if hashlib.sha256(chunk).hexdigest() != manifest["chunks"][index]:
                raise ValueError("content checksum mismatch")
            chunks.append(chunk)
        start = offset % CHUNK
        return b"".join(chunks)[start:start + end - offset]

    def backup(self, database: Path, destination: Path) -> None:
        """Copy precisely the content referenced by an already consistent DB backup."""
        import sqlite3
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='content_locations'").fetchone():
                return
            identities = [row[0] for row in db.execute("SELECT DISTINCT sha256 FROM content_locations")]
        self.copy(identities, destination)

    def copy(self, identities, destination: Path) -> None:
        for identity in identities:
            source = self._path(identity)
            manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            digest = hashlib.sha256()
            for offset in range(0, manifest["size"], CHUNK):
                digest.update(self.read(identity, offset, CHUNK))
            if digest.hexdigest() != identity:
                raise ValueError("backup content verification failed")
            target = ContentFiles(destination)._path(identity)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != target.resolve():
                shutil.copytree(source, target, dirs_exist_ok=True)
            copied = ContentFiles(destination)
            verified = hashlib.sha256()
            for offset in range(0, manifest["size"], CHUNK):
                verified.update(copied.read(identity, offset, CHUNK))
            if verified.hexdigest() != identity:
                raise ValueError("copied content verification failed")
