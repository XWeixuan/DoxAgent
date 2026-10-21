"""Run native Vertex replay in an isolated container; never copy production env or DBs."""

import json
import subprocess
from pathlib import Path

ROOT = Path("/home/ubuntu/doxagent-eval/w2r1-vertex-20261022")
CREDENTIAL = Path(
    "/etc/doxagent/credentials/google-cloud/gen-lang-client-0328762493-5d70d08b98dc.json"
)
CONTAINER = "doxagent-eval-w2r1-vertex-adc-20261022"


def main():
    info = json.loads(
        subprocess.check_output(["sudo", "-n", "docker", "inspect", "doxagent-v2-v2-scheduler-1"])
    )[0]
    image = info["Image"]
    args = [
        "sudo",
        "-n",
        "docker",
        "run",
        "--user",
        "0:0",
        "--name",
        CONTAINER,
        "--network",
        "doxagent-v2_default",
        "--read-only",
        "--memory",
        "768m",
        "--cpus",
        "0.75",
        "--pids-limit",
        "128",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,nosuid,size=64m",
        "--entrypoint",
        "python",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "PYTHONPATH=/eval/.deps:/app/src",
        "-e",
        "GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/google-cloud.json",
        "-e",
        "DOXAGENT_VERTEX_PROJECT=gen-lang-client-0328762493",
        "-v",
        str(CREDENTIAL) + ":/run/secrets/google-cloud.json:ro",
        "-v",
        str(ROOT / "eval") + ":/eval",
    ]
    for name in ("vertex.py", "openrouter.py", "transport.py"):
        args.extend(
            [
                "-v",
                str(ROOT / "code" / name)
                + ":/app/src/doxagent/persistent_runtime_v2/"
                + name
                + ":ro",
            ]
        )
    for name in ("pricing.py", "pricing_rules.json"):
        args.extend(
            ["-v", str(ROOT / "code" / name) + ":/app/src/doxagent/v2_read/" + name + ":ro"]
        )
    # Reuse network routing only, never unrelated model/broker/database credentials.
    env = dict(item.split("=", 1) for item in info["Config"]["Env"])
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        if key in env:
            args.extend(["-e", key + "=" + env[key]])
    args.extend(
        [
            image,
            "/eval/w2_r1_haiku_vertex_20261022/remote_entry.py",
            "--project",
            "gen-lang-client-0328762493",
            "--auth",
            "adc",
            "--label",
            "remote-adc",
        ]
    )
    record = dict(
        container=CONTAINER,
        image=image,
        production_mounts=False,
        credential_mount_read_only=True,
        cpu_limit=0.75,
        memory="768m",
        concurrency=1,
        project="gen-lang-client-0328762493",
        auth="service_account_adc",
        proxy_environment_names=[
            k for k in env if k.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
        ],
    )
    (ROOT / "launch.json").write_text(json.dumps(record, indent=2))
    with (ROOT / "run.log").open("w") as log:
        result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT)
    record["exit_code"] = result.returncode
    (ROOT / "launch.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record))


if __name__ == "__main__":
    main()
