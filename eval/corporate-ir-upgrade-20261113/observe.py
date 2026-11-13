"""Bounded read-only IR health receipt from existing collection-plane records."""
import json
from pathlib import Path

from doxagent.message_bus_v2.collection_plane.observability import store_from_environment
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.schema import utc_now

activation = json.loads(Path('/data/bus/ops/ir-upgrade-20261113/activation.json').read_text())
repo = MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path, initialize=False)
store = store_from_environment()
units = []
with store.connect() as db:
    for row in db.execute('SELECT binding_id,unit,head_json,state FROM units WHERE source_id=? AND required=1', ('corporate_ir_press_releases',)):
        head, state = json.loads(row['head_json']), json.loads(row['state'])
        checkpoint = state.get('checkpoint', {})
        if state.get('checkpoint_ref'):
            saved = db.execute('SELECT checkpoint FROM physical_checkpoints WHERE identity=?', (state['checkpoint_ref'],)).fetchone()
            checkpoint = json.loads(saved[0]) if saved else {}
        cycles = db.execute('SELECT status,reason,count(*) n FROM cycles WHERE binding_id=? AND unit=? AND dispatch_at>=? GROUP BY status,reason',
            (row['binding_id'],row['unit'],activation['at'])).fetchall()
        lanes = checkpoint.get('race_lanes', {})
        units.append(dict(ticker=row['binding_id'].split(':')[0], unit=row['unit'],
            status=head.get('status'), last_attempt_at=head.get('last_attempt_at'),
            last_success_at=head.get('last_success_at'), latency_ms=head.get('last_latency_ms'),
            error=head.get('last_error_message'), observation=head.get('race_observation'),
            lane_names=sorted(lanes), cycles=[dict(c) for c in cycles]))
print(json.dumps(dict(at=utc_now().isoformat(), source_version=repo.get_source('corporate_ir_press_releases').version,
                     activation=activation, units=units)), flush=True)
