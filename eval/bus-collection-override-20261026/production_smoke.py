"""Bounded real control-plane smoke, run in the new production Bus container."""
import json
from datetime import UTC, datetime, timedelta

from doxagent.message_bus_v2.collection_override import CollectionControl
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.settings import DoxAgentSettings

settings = DoxAgentSettings()
repository = MessageBusV2Repository(settings.message_bus_v2_sqlite_path)
control = CollectionControl(repository)
before = control.status()
assert not before['effective'], 'another operator has enabled temporary collection'
try:
    enabled = control.update(
        enabled=True, actor='release-20261026', reason='bounded production control smoke',
        expires_at=datetime.now(UTC)+timedelta(minutes=2),
        tickers=['MU'], sources=['finnhub_company_news'],
    )
    restored = CollectionControl(MessageBusV2Repository(repository.path)).status()
    assert restored['effective'] and restored['revision'] == enabled['revision']
finally:
    disabled = control.update(enabled=False, actor='release-20261026', reason='control smoke finished; restore normal calendar')
assert not disabled['effective']
print(json.dumps({'before': before, 'enabled': enabled, 'restored': restored, 'final': disabled,
                  'history': control.history(2)}, ensure_ascii=False))
