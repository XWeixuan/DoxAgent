"""Real patched app-server + official SDK; all model responses are local fixtures."""

import json
import os
import re
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

pytestmark = pytest.mark.skipif(
    not os.environ.get("W3_TEST_CODEX_BIN"), reason="requires the built patched Codex binary"
)


def messages(body):
    return [
        part.get("text", "")
        for item in body.get("input", [])
        if item.get("role") == "developer"
        for part in item.get("content", [])
    ]


def reminders(body):
    return [text for text in messages(body) if "<w3_time_awareness>" in text]


@pytest.fixture
def native(tmp_path, request):
    state = SimpleNamespace(requests=[], actions=deque(), compacting=False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            payload = json.dumps({"data": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "zstd":
                import zstandard

                raw = zstandard.ZstdDecompressor().decompress(raw, max_output_size=16 * 1024 * 1024)
            body = json.loads(raw)
            state.requests.append(body)
            number = len(state.requests)
            action = state.actions.popleft() if state.actions else "final"
            events = [{"type": "response.created", "response": {"id": f"response-{number}"}}]
            if action == "code_mode":
                events.append(
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "custom_tool_call",
                            "id": f"item-{number}",
                            "call_id": f"call-{number}",
                            "name": "exec",
                            "input": 'text("synthetic code-mode host works");',
                        },
                    }
                )
            elif action == "tool":
                events.append(
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "function_call",
                            "call_id": f"call-{number}",
                            "name": "exec_command",
                            "arguments": json.dumps({"cmd": "echo synthetic"}),
                        },
                    }
                )
            elif action != "retry":
                if any(item.get("type") == "compaction_trigger" for item in body.get("input", [])):
                    item = {"type": "compaction", "encrypted_content": "SYNTHETIC_SUMMARY"}
                else:
                    item = {
                        "type": "message",
                        "id": f"message-{number}",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Synthetic result."}],
                    }
                events.append({"type": "response.output_item.done", "item": item})
            if action != "retry":
                events.append(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": f"response-{number}",
                            "usage": {
                                "input_tokens": 10,
                                "output_tokens": 2,
                                "total_tokens": 12,
                            },
                        },
                    }
                )
            payload = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    control = tmp_path / "time-context.json"
    home = tmp_path / "home"
    home.mkdir()
    config = CodexConfig(
        codex_bin=os.environ["W3_TEST_CODEX_BIN"],
        env={"CODEX_HOME": str(home), "DOXAGENT_W3_TIME_CONTEXT_FILE": str(control)},
        config_overrides=(
            'model_provider="synthetic"',
            'model_providers.synthetic={name="Synthetic",'
            f'base_url="http://127.0.0.1:{server.server_port}",wire_api="responses",'
            "requires_openai_auth=false,request_max_retries=0,stream_max_retries=2}",
            'web_search="disabled"',
            "features.responses_websockets=false",
            "features.responses_websockets_v2=false",
            "features.multi_agent=false",
            "features.memories=false",
            "features.apps=false",
        ),
    )
    try:
        with Codex(config) as client:
            thread = client.thread_start(
                cwd=str(tmp_path),
                model="test-model",
                sandbox=Sandbox.full_access,
                approval_mode=ApprovalMode.deny_all,
                base_instructions="Return the synthetic result.",
            )
            state.client, state.thread, state.control = client, thread, control
            yield state
    finally:
        server.shutdown()
        server.server_close()
        serving.join()
        if destination := os.environ.get("W3_NATIVE_RECEIPTS"):
            folder = Path(destination)
            folder.mkdir(parents=True, exist_ok=True)
            rows = [
                {
                    "request": index,
                    "input_items": len(body.get("input", [])),
                    "reminders": reminders(body),
                }
                for index, body in enumerate(state.requests)
            ]
            (folder / f"{request.node.name}.json").write_text(json.dumps(rows, indent=2))


def enable(native, seconds, case_id="case-1"):
    native.control.write_text(
        json.dumps(
            dict(
                enabled=True,
                case_id=case_id,
                thread_id=native.thread.id,
                task_phase="trade_decision",
                started_at_utc=(datetime.now(UTC) - timedelta(seconds=seconds)).isoformat(),
            )
        )
    )


def test_native_loop_retry_refreshes_and_operations_exclude_history(native):
    native.actions.extend(["tool", "final"])
    enable(native, 73)
    assert native.thread.run("Trade Decision").final_response == "Synthetic result."
    assert len(native.requests) == 2
    assert any(
        item.get("type") == "function_call_output" and "synthetic" in str(item.get("output"))
        for item in native.requests[1]["input"]
    )
    assert [len(reminders(body)) for body in native.requests] == [1, 2]
    elapsed = [
        int(re.search(r"Elapsed: (\d+)s", reminders(body)[-1])[1]) for body in native.requests
    ]
    assert 73 <= elapsed[0] <= elapsed[1] < 90
    first = native.requests[0]["input"]
    assert native.requests[1]["input"][: len(first)] == first

    native.actions.extend(["retry", "final"])
    enable(native, 180, "case-retry")
    assert native.thread.run("Trade Decision retry").final_response == "Synthetic result."
    retry = native.requests[-2:]
    assert [len(reminders(body)) for body in retry] == [1, 2]
    assert "高价值事实或变量" in reminders(retry[-1])[-1]

    native.control.write_text('{"enabled":false}')
    native.thread.run("Post-Decision Operations")
    assert reminders(native.requests[-1]) == []


def test_native_code_mode_companion_executes_before_research(native):
    native.thread = native.client.thread_start(
        cwd=str(native.control.parent),
        model="gpt-6-luna",
        sandbox=Sandbox.full_access,
        approval_mode=ApprovalMode.deny_all,
        base_instructions="Synthetic code-mode verification.",
    )
    native.actions.extend(["code_mode", "final"])
    enable(native, 125)
    assert native.thread.run("Trade Decision").final_response == "Synthetic result."
    assert len(native.requests) == 2
    outputs = [
        item.get("output", "")
        for item in native.requests[1]["input"]
        if item.get("type") == "custom_tool_call_output"
    ]
    assert any("synthetic code-mode host works" in str(output) for output in outputs), outputs
    assert all("failed to spawn code-mode host" not in str(output) for output in outputs)


def test_native_stages_fork_resume_and_compaction_are_scoped(native):
    for seconds, text in [
        (0, None),
        (120, "任务耗时较长"),
        (180, "后续几轮"),
        (240, "停止新增调查"),
    ]:
        enable(native, seconds, f"case-{seconds}")
        native.thread.run("Trade Decision")
        current = reminders(native.requests[-1])
        assert len(current) == 1
        elapsed = int(re.search(r"Elapsed: (\d+)s", current[0])[1])
        assert seconds <= elapsed < seconds + 10
        assert ("任务耗时" in current[0]) is (seconds in (120, 180))
        if text:
            assert text in current[0]

    native.thread = native.client.thread_fork(native.thread.id)
    enable(native, 0, "new-case-on-fork")
    native.thread.run("Trade Decision on fork")
    assert len(reminders(native.requests[-1])) == 1
    native.thread = native.client.thread_resume(native.thread.id)
    native.control.write_text('{"enabled":false}')
    native.thread.run("Post-Decision Operations after resume")
    assert reminders(native.requests[-1]) == []

    count = len(native.requests)
    native.thread.compact()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = native.thread.read().thread.status
        status = getattr(status, "root", status)
        if len(native.requests) > count and getattr(status, "type", None) == "idle":
            break
        time.sleep(0.05)
    assert len(native.requests) > count
    assert all(not reminders(body) for body in native.requests[count:])
    enable(native, 240, "post-compact-case")
    native.thread.run("Trade Decision after compaction")
    assert len(reminders(native.requests[-1])) == 1
    native.control.write_text('{"enabled":false}')
    native.thread.run("Post-Decision Operations after compaction")
    assert reminders(native.requests[-1]) == []
