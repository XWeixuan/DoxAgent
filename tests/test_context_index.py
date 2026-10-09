import hashlib
import json
import subprocess
import sys

from doxagent.codex_runtime.context_index import build_index


def test_utf8_bounded_pages_exact_body_headings_and_standalone_reader(tmp_path):
    report = (
        "# 总览\r\n" + "中文正文🙂\n" * 2500 + "```md\n# 不是标题\n```\n## 次章\n" + "长行🙂" * 5000
    )
    context = {
        "reports": {"c1": report},
        "canonical_shell": {
            "units": [{"name": "单元", "state": {"values": [{"name": "值", "value": "完整正文"}]}}]
        },
        "a/b~c": "特殊键",
    }
    files = build_index(context, root="index")
    assert files == build_index(context, root="index")
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
    key = hashlib.sha256(b"/reports/c1").hexdigest()[:20]
    entry = json.loads(files[f"index/pointers/{key}.json"])
    assert [h["title"] for h in entry["sections"]] == ["总览", "次章"]
    assert "".join(files["index/" + p] for p in entry["pages"]) == report
    assert all(len(files["index/" + p]) <= 6000 for p in entry["pages"])

    def read(*args):
        value = subprocess.run(
            [sys.executable, str(tmp_path / "index/read_context.py"), *args],
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
        return json.loads(value.stdout)

    assert read("read", "--pointer", "/reports/c1")["has_more"]
    assert read("read", "--pointer", "/reports/c1")["content"].startswith("# 总览\r\n")
    assert read("read", "--asset", entry["id"], "--section", "s2")["content"].startswith("## 次章")
    assert read("read", "--pointer", "/canonical_shell/units/0/state/values")["content"]
    assert "特殊键" in read("read", "--pointer", "/a~1b~0c")["content"]


def test_large_record_catalog_is_paged_and_original_refs_retained():
    context = {
        "source.txt": "# C3\n" + "正文" * 1000,
        "open_discovery_late_additions": [
            dict(unit="u", name=f"n{i}", discovered_during="STATE") for i in range(300)
        ],
    }
    files = build_index(
        context,
        root="index",
        sources={"source.txt": dict(role="C3", original_ref="remote/c3.md", sha256="original-sha")},
    )
    index = json.loads(files["index/index.json"])
    assert index["groups"]["records"]["total_pages"] > 1
    assert len(files["index/index.json"]) < 6000
    assert all(
        len(files[f"index/catalog/records/{n}.json"]) <= 6000
        for n in range(1, index["groups"]["records"]["total_pages"] + 1)
    )
    entry = json.loads(
        files["index/pointers/" + hashlib.sha256(b"/source.txt").hexdigest()[:20] + ".json"]
    )
    assert entry["role"] == "C3" and entry["source"]["original_ref"] == "remote/c3.md"


def test_empty_report_catalog_lists_without_page_error(tmp_path):
    files = build_index({"canonical_shell": {"units": [{"name": "U"}]}}, root="index")
    for path, content in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(tmp_path / "index/read_context.py"), "list", "--group", "reports"],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    assert json.loads(result.stdout) == {
        "page": 1,
        "total_pages": 0,
        "has_more": False,
        "entries": [],
    }


def test_index_failure_keeps_original_inputs(monkeypatch):
    from doxagent.codex_runtime import context_index

    def fails(*args, **kwargs):
        raise OSError("derived storage unavailable")

    monkeypatch.setattr(context_index, "build_index", fails)
    files = {"input/context.json": '{"primary_source":"正文"}'}
    original = dict(files)
    navigation = context_index.attach_index(files, root="index")
    assert files == original and "context_index_unavailable" in navigation["warning"]


def test_short_network_report_and_identical_product_aliases(tmp_path):
    products = {
        "future_nodes": [{"name": "future"}],
        "entity_relations": [],
        "entity_network_report": "# Short network\nOnly network body",
    }
    context = {
        **products,
        "global_research": dict(products),
        "research_asset_sources": {
            "entity_network_report": {
                "role": "c4e_network_build",
                "original_ref": "accepted/report.md",
            }
        },
    }
    files = build_index(context, root="index")
    index = json.loads(files["index/index.json"])
    assert index["aliases"]["/global_research/entity_network_report"] == "/entity_network_report"
    catalog = json.loads(files["index/catalog/reports/1.json"])["entries"]
    assert [(e["pointer"], e["role"]) for e in catalog] == [
        ("/entity_network_report", "c4e_network_build")
    ]
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf8", newline="")
    for pointer in ("/entity_network_report", "/global_research/entity_network_report"):
        result = subprocess.run(
            [sys.executable, str(tmp_path / "index/read_context.py"), "read", "--pointer", pointer],
            check=True,
            capture_output=True,
            encoding="utf8",
        )
        assert json.loads(result.stdout)["content"] == products["entity_network_report"]
