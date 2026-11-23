"""Object sharing in the existing Native CAS; no independent history chain."""
import json
from pathlib import Path

from doxagent.v2_read.native_content import MARKER, NativeContent


def shared_ready(codec):
    # Roll readers before enabling a format which old Policy repositories
    # cannot decode. Checked only on publication, never during W3 admission.
    return (Path(codec.database).parent/'.d3-shared-policy-ready').is_file()


def pack_policy_set(codec: NativeContent, data):
    """Publish immutable objects before taking a SQLite writer.

    Each version keeps its order/identity/metadata; unchanged policy objects
    share content across staged/formal/history. Small objects stay inline.
    """
    if not shared_ready(codec):
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    value = dict(data)
    policies = []
    for policy in value.get('policies', []):
        policies.append(pack_object(codec,policy))
    value['policies'] = policies
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def pack_object(codec, value):
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return {MARKER:codec.files.put(raw),'bytes':len(raw)} if len(raw)>=2048 else value


def install_refs(db, table, columns, identity):
    from doxagent.v2_read.artifact_registry import migrate, native_triggers

    if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", ('v2_artifact_'+table+'_INSERT',)).fetchone():
        migrate(db)
        native_triggers(db, table, columns, identity)
