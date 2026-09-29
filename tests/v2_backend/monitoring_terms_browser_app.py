"""Disposable native-Bus backend for the browser integration fixture."""

import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.middleware.cors import CORSMiddleware

from doxagent.api_v2.app import create_app
from doxagent.api_v2.auth import Principal
from doxagent.api_v2.errors import ApiFailure
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.monitoring_terms import MonitoringTermsControl
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.repository import ReadStore


class FixtureAuth:
    async def authenticate(self, token):
        if token != "fixture":
            raise ApiFailure("UNAUTHORIZED", 401)
        return Principal("browser-fixture", "DEVELOPER", time.time() + 3600)


temporary = tempfile.TemporaryDirectory(prefix="doxagent-terms-browser-")
root = Path(temporary.name)
bus = MessageBusV2Repository(root / "bus.db")
service = MessageBusV2Service(bus)
service.bootstrap()
service.start_ticker("MU")
terms = MonitoringTermsControl(bus.path)
value = {
    "l1_concepts": [{"concept_id": "memory", "expressions": {"en": "Micron memory", "ko": "마이크론 메모리", "zh-Hant": "美光 記憶體"}}],
    "l2": {lang: {"groups": [{"id": "memory-" + lang, "any": [{"literal": word}]}]}
           for lang, word in {"en": "Micron", "ko": "마이크론", "zh-Hant": "美光"}.items()},
    "definition": {"relevant": "Micron business", "irrelevant": "Unrelated products"},
}
terms.put_terms("MU", value, "fixture", "seed-terms", terms.get_terms("MU")["control_etag"])
service.configure_binding(ticker="MU", source_id="google_news_search_rss", source_parameters={"search_terms": ["legacy"]}, actor="user")

store = ReadStore(root / "read.db")
store.migrate()
control = ControlRepository(RuntimeJournal(root / "runtime.db"))
control.migrate()
os.environ["DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH"] = str(bus.path)
app = create_app(store=store, control=control, auth=FixtureAuth())
app.state.control = SimpleNamespace(get=lambda ticker: {} if ticker == "MU" else None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5174"],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type", "If-Match", "Idempotency-Key"],
    expose_headers=["ETag"],
)
