"""Extract and verify source checkpoint coordinates without expanding a large backup.

The complete compressed and uncompressed SHA-256 values must match its frozen
backup manifest. Only ordinary, non-overflow SQLite leaf records are interpreted;
the source state must be on the root page named by its actual schema record.
"""
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path


def varint(raw, position):
    value = 0
    for n in range(9):
        byte = raw[position]
        position += 1
        if n == 8:
            return (value << 8) | byte, position
        value = (value << 7) | (byte & 127)
        if byte < 128:
            return value, position
    raise ValueError('invalid varint')


def records(page, number):
    base = 100 if number == 1 else 0
    if page[base] != 13:
        return
    count = int.from_bytes(page[base + 3:base + 5], 'big')
    for n in range(count):
        pointer = base + 8 + 2 * n
        offset = int.from_bytes(page[pointer:pointer + 2], 'big')
        try:
            size, start = varint(page, offset)
            _, start = varint(page, start)
            if start + size > len(page):
                continue  # Overflow records are never a checkpoint state row.
            payload = page[start:start + size]
            header, position = varint(payload, 0)
            serials = []
            while position < header:
                serial, position = varint(payload, position)
                serials.append(serial)
            position = header
            values = []
            for serial in serials:
                if serial == 0:
                    values.append(None)
                elif serial in {8, 9}:
                    values.append(serial - 8)
                else:
                    length = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8, 7: 8}.get(serial)
                    if length is None:
                        if serial < 12:
                            raise ValueError('reserved serial type')
                        length = (serial - 12) // 2
                    value = payload[position:position + length]
                    if len(value) != length:
                        raise ValueError('truncated record')
                    position += length
                    if 1 <= serial <= 6:
                        value = int.from_bytes(value, 'big', signed=True)
                    elif serial >= 13 and serial % 2:
                        value = value.decode('utf-8')
                    values.append(value)
            yield values
        except (ValueError, IndexError, UnicodeError):
            continue


def extract(archive, expected, *, source, epoch, destination):
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected['archive_sha256']:
            raise ValueError('compressed backup checksum mismatch')
    root = None
    states = []
    digest = hashlib.sha256()
    total = 0
    needle = epoch.encode('ascii')
    with gzip.open(archive, 'rb') as stream:
        header = stream.read(100)
        if header[:16] != b'SQLite format 3\x00':
            raise ValueError('not a SQLite backup')
        page_size = int.from_bytes(header[16:18], 'big')
        page_size = 65536 if page_size == 1 else page_size
        if page_size < 512 or page_size & (page_size - 1):
            raise ValueError('invalid SQLite page size')
        page = header + stream.read(page_size - 100)
        number = 1
        while page:
            if len(page) != page_size:
                raise ValueError('truncated backup page')
            digest.update(page)
            total += len(page)
            if b'v2_source_state' in page or needle in page:
                for row in records(page, number):
                    if len(row) == 5 and row[:3] == ['table', 'v2_source_state', 'v2_source_state']:
                        if not isinstance(row[3], int) or 'epoch TEXT' not in row[4]:
                            raise ValueError('unexpected source state schema')
                        root = row[3]
                    if len(row) == 4 and row[:2] == [source, epoch] and all(isinstance(x, int) and x >= 0 for x in row[2:]):
                        states.append((number, row))
            number += 1
            page = stream.read(page_size)
    if total != expected['size'] or digest.hexdigest() != expected['sha256']:
        raise ValueError('uncompressed frozen backup checksum mismatch')
    found = [row for page, row in states if page == root]
    if len(found) != 1:
        raise ValueError('unambiguous source checkpoint not found; use a full restore')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('checkpoint target must be new')
    with sqlite3.connect(destination) as db:
        db.execute('CREATE TABLE v2_source_state(source TEXT PRIMARY KEY,epoch TEXT,head INTEGER,retained_floor INTEGER)')
        db.execute('INSERT INTO v2_source_state VALUES(?,?,?,?)', found[0])
        db.execute('CREATE TABLE checkpoint_proof(archive TEXT,archive_sha256 TEXT,source_sha256 TEXT)')
        db.execute('INSERT INTO checkpoint_proof VALUES(?,?,?)', (str(archive.resolve()), expected['archive_sha256'], expected['sha256']))
    return dict(source=source, epoch=epoch, head=found[0][2], retained_floor=found[0][3],
                checkpoint=str(destination), archive=str(archive.resolve()),
                verified_compressed_sha256=expected['archive_sha256'], verified_source_sha256=expected['sha256'])
