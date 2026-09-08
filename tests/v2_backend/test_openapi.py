from doxagent.api_v2.app import create_app
from doxagent.api_v2.openapi import ROUTES
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.repository import ReadStore


def test_implemented_openapi_responses_and_requests_are_closed(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    app = create_app(store=store, control=control, auth=object())
    spec = app.openapi()
    schema = spec["components"]["schemas"]["StartTickerRequest"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["ticker", "monitor_mode", "initialization"]
    for route in ROUTES:
        operation = (
            spec["paths"].get("/api/doxagent/v2" + route["path"], {}).get(route["method"].lower())
        )
        if operation and route["response_schema"] and not route["path"].endswith("/download"):
            status = (
                "202"
                if route["response_schema"] == "Operation" and route["method"] != "GET"
                else "200"
            )
            if route["method"] == "POST" and route["path"].endswith("/bindings"):
                status = "201"
            response = operation["responses"][status]["content"]["application/json"]["schema"]
            assert response["additionalProperties"] is False
            assert operation["responses"]["401"]["content"]["application/json"]["schema"][
                "$ref"
            ].endswith("/ErrorResponse")
