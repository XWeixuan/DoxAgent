from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml


def module():
    path = Path(__file__).parents[1] / "deploy" / "refresh-egress-providers.py"
    spec = importlib.util.spec_from_file_location("egress_refresh", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_provider_namespace_and_endpoint_fingerprint():
    refresh = module()
    body, hashes = refresh.normalize(
        "Flower_SS",
        b"proxies:\n- name: DE\n  server: host\n  port: 123\n  type: ss\n  password: secret\n",
    )
    assert yaml.safe_load(body)["proxies"][0]["name"] == "Flower_SS:DE"
    assert len(hashes) == 1 and len(hashes[0]) == 64
    with pytest.raises(ValueError):
        refresh.normalize("SSRDOG", b"not a subscription")


def test_fetch_failure_keeps_last_good_snapshot_and_redacts_url(tmp_path, monkeypatch):
    refresh = module()
    (tmp_path / "sources.json").write_text(
        json.dumps(
            {
                "SSRDOG": "https://example.com/?secret=token",
                "Flower_SS": "https://example.com/?secret=other",
            }
        )
    )
    previous = b"proxies: [last-good]"
    (tmp_path / "SSRDOG.yaml").write_bytes(previous)
    (tmp_path / "status.json").write_text(json.dumps({"SSRDOG": {"node_count": 7}}))

    def fail(*args, **kwargs):
        raise OSError("https://example.com/?secret=token")

    monkeypatch.setattr(refresh, "urlopen", fail)
    monkeypatch.setattr(refresh, "atomic_write", lambda path, body: path.write_bytes(body))
    report = refresh.refresh(tmp_path)
    assert (tmp_path / "SSRDOG.yaml").read_bytes() == previous
    assert report["SSRDOG"]["node_count"] == 7
    assert report["SSRDOG"]["stale"]
    assert "secret" not in json.dumps(report)
