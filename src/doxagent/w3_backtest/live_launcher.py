"""One-command host launcher. Only the history endpoint can reach the broker."""
import json
import os
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .live_evaluation import acquire
from .live_history import serve


def evaluate(args):
    import subprocess

    if args.rounds < 1 or args.parallel < 1:
        raise ValueError("rounds and parallel must be positive")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    native = acquire(args.case_id, root / "acquisition", args.host)
    source = root / "source"
    source.mkdir()
    safe = dict(native)
    safe["case"] = {key: native["case"][key] for key in
                    ("case_id", "source", "version_pin", "w1_final", "w2_final", "created_at")
                    if key in native["case"]}
    (source / "native.json").write_text(json.dumps(safe, ensure_ascii=False), encoding="utf-8")
    shutil.copyfile(root / "acquisition/context.json", source / "context.json")
    prompts = root / "prompt_source"
    prompts.mkdir()
    prompt_source = args.prompt_root or root / "acquisition/production_prompts"
    for name in ("AGENTS.md", "skills/skill.md", "skill.md", "contract.json"):
        if (prompt_source / name).is_file():
            target = prompts / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(prompt_source / name, target)
    ticker = native["case"]["source"]["snapshot"]["ticker"]
    token = secrets.token_hex(24)
    servers = []
    thread = threading.Thread(target=serve, args=(
        ticker, token, 0, root / "history_requests.jsonl", args.host, servers
    ), daemon=True)
    thread.start()
    while not servers:
        if not thread.is_alive():
            raise RuntimeError("history bridge did not start")
        time.sleep(0.01)
    port = servers[0].server_port
    repo = Path(__file__).resolve().parents[3]
    auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"

    def one(number):
        output = root / "rounds" / str(number)
        output.mkdir(parents=True)
        command = [
            "docker", "run", "--rm", "--init",
            "--mount", f"type=bind,source={repo / 'src'},target=/execution/src,readonly",
            "--mount", f"type=bind,source={source},target=/source,readonly",
            "--mount", f"type=bind,source={prompts},target=/prompts,readonly",
            "--mount", f"type=bind,source={output},target=/results",
            "--mount", f"type=bind,source={args.runtime_dir.resolve()},target=/runtime,readonly",
            "--mount", f"type=bind,source={auth},target=/auth/auth.json,readonly",
            "--workdir", "/execution", "--env", "PYTHONPATH=/execution/src",
            "--env", "CODEX_HOME=/auth", "--env", "LOCALAPPDATA=/results/private",
            "--env", "DOXAGENT_CODEX_RUNTIME_BIN=/runtime/codex",
            "--env", f"LOCAL_W3_HISTORY_URL=http://host.docker.internal:{port}/history",
            "--env", f"LOCAL_W3_HISTORY_TOKEN={token}",
            "--entrypoint", "/app/.venv/bin/python", args.image,
            "-m", "doxagent.w3_backtest.live_evaluation", "run",
            "--source", "/source", "--output", "/results/execution", "--prompt-root", "/prompts",
        ]
        for name in ("model", "effort", "service_tier"):
            value = getattr(args, name)
            if value:
                command.extend(["--" + name.replace("_", "-"), value])
        with (output / "launch.log").open("w", encoding="utf-8") as stream:
            code = subprocess.call(command, stdout=stream, stderr=subprocess.STDOUT)
        result = output / "execution/result.json"
        if code or not result.exists():
            return dict(round=number, status="FAILED", log=str(output / "launch.log"))
        return dict(round=number, **json.loads(result.read_text(encoding="utf-8")))

    try:
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            results = list(pool.map(one, range(1, args.rounds + 1)))
        (root / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(cases=len(results), succeeded=sum(x["status"]=="SUCCEEDED" for x in results))))
    finally:
        servers[0].shutdown()
        servers[0].server_close()
