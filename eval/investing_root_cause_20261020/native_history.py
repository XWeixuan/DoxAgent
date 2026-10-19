"""Read-only native recovery receipts, including legacy Profile.site_id labels."""
import contextlib
import io
import json
import runpy

with contextlib.redirect_stdout(io.StringIO()):
    ns = runpy.run_path('/tmp/investing-snapshot.py')
print(json.dumps([dict(r) for r in ns['site'].execute(
    "select operation_id,occurred_at,category,payload_json from access_events "
    "where category='NATIVE_GRACEFUL_DRAIN' and "
    "json_extract(payload_json,'$.identity_id') in ('public-markets-nl','investing-backup') "
    "and julianday(occurred_at)>=julianday('now','-1 day') order by julianday(occurred_at)"
)]))
