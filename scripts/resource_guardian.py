#!/usr/bin/env python3
"""Host-only cgroup/Docker quota guardian; never execs containers or opens business data."""

import argparse
import json
import logging
import os
import socket
import socketserver
import struct
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

MIB = 1024**2
NORMAL, HARD, SAFETY = 6144 * MIB, 6656 * MIB, 1024 * MIB
PROJECTION_RESERVE = 128 * MIB
QUOTAS = {
    "v2-scheduler": (1024, 2048, 512),
    "v2-delivery": (256, 384, 128),
    "v2-projector": (384, 640, 256),
}
SERVICES = [
    "v2-api",
    "v2-control",
    "codex-worker",
    "v2-initialization",
    "v2-message-bus",
    "v2-content-enrichment",
    "v2-o4",
    "v2-scheduler",
    "v2-projector",
    "v2-delivery",
    "v2-executor",
    "v2-web",
    "v2-migrate",
]
# Estimated additional working memory, independent of individual hard ceilings.
WORK = {
    "initialization": (512, 3),
    "realtime": (128, 1),
    "maintenance": (512, 2),
    "codex_runtime": (1024, 1),
    "codex_maintenance": (1024, 2),
    "codex_initialization": (1024, 3),
    "projection": (128, 4),
}
ALLOWED_WORK = {
    "v2-initialization": {"initialization"},
    "v2-scheduler": {"realtime", "maintenance"},
    "codex-worker": {"codex_runtime", "codex_maintenance", "codex_initialization"},
    "v2-projector": {"projection"},
}


def docker(*args):
    # Inspect fixed application names; update only three quota-allowlisted services.
    return subprocess.check_output(["docker", *args], timeout=5, text=True)


class Guardian:
    def __init__(self, root=Path("/sys/fs/cgroup/doxagent.slice/doxagent-app.slice")):
        self.root = root
        self.lock = threading.RLock()
        self.containers, self.metrics, self.leases, self.work, self.waiting = {}, {}, {}, {}, {}
        self.low_since = {}
        self.swap_previous = None
        self.swap_pressure_since = None
        self.state_path = Path("/run/doxagent-resources/leases.json")
        self.peaks = {}
        try:
            self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            self.boot_id = "non-linux-test-boot"
        if self.state_path.is_file():
            state = json.loads(self.state_path.read_text())
            # Legacy state has no boot id and is accepted once so an in-flight
            # deployment is not orphaned. All newly persisted state is scoped to
            # one kernel boot; monotonic expiries must never survive a reboot.
            if state.get("boot_id") in {None, self.boot_id}:
                self.work = state.get("work", {})
                self.leases = state.get("leases", {})
                self.peaks = state.get("peaks", {})

    def persist(self):
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "boot_id": self.boot_id,
                    "work": self.work,
                    "leases": self.leases,
                    "metrics": self.metrics,
                    "peaks": self.peaks,
                }
            )
        )
        temporary.replace(self.state_path)

    def sample(self):
        names = ["doxagent-v2-" + s + "-1" for s in SERVICES]
        containers = json.loads(docker("inspect", *names))
        result = {}
        for c in containers:
            service = c["Config"]["Labels"].get("com.docker.compose.service")
            if service not in SERVICES or not c["State"]["Running"]:
                continue
            root = self.root / ("docker-" + c["Id"] + ".scope")
            result[service] = {
                "id": c["Id"],
                "pid": c["State"]["Pid"],
                "current": int((root / "memory.current").read_text()),
                "limit": c["HostConfig"]["Memory"],
                "swap_limit": c["HostConfig"]["MemorySwap"],
            }
        info = {
            line.split(":")[0]: int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
        }
        vm = dict(line.split() for line in Path("/proc/vmstat").read_text().splitlines())
        swap_io = int(vm["pswpin"]) + int(vm["pswpout"])
        pressure = float(
            (self.root / "memory.pressure")
            .read_text()
            .split("full ")[1]
            .split("avg10=")[1]
            .split()[0]
        )
        now = time.monotonic()
        swapping = self.swap_previous is not None and swap_io - self.swap_previous > 128
        self.swap_previous = swap_io
        self.swap_pressure_since = (
            (self.swap_pressure_since or now) if swapping or pressure >= 1 else None
        )
        self.containers = result
        for service, container in result.items():
            self.peaks[service] = max(self.peaks.get(service, 0), container["current"])
        app_current = int((self.root / "memory.current").read_text())
        app_stat = {
            line.split()[0]: int(line.split()[1])
            for line in (self.root / "memory.stat").read_text().splitlines()
        }
        inactive_file = min(app_current, app_stat.get("inactive_file", 0))
        self.metrics = {
            "app_current": app_current,
            # Inactive file pages are reclaimable by the kernel under memory.high.
            # Keep raw current for the hard guard and use working current only for
            # normal admission.
            "working_current": app_current - inactive_file,
            "inactive_file": inactive_file,
            "app_swap": int((self.root / "memory.swap.current").read_text()),
            "available": info["MemAvailable"],
            "pressure": pressure,
            "swapping": self.swap_pressure_since is not None
            and now - self.swap_pressure_since >= 10,
            "at": time.time(),
        }

    def budget(self, extra, *, emergency=False, basic_projection=False):
        m = self.metrics
        # Fund one basic projection batch before admitting other work/peak borrowing.
        # Its execution consumes this earmark, not another reservation dependent on
        # long-running maintenance's still-outstanding work estimates.
        outstanding_by_service = {}
        for service in {w["service"] for w in self.work.values()}:
            if service == "v2-projector":
                continue
            entries = [w for w in self.work.values() if w["service"] == service]
            total = sum(w["bytes"] for w in entries)
            current = self.containers.get(service, {}).get("current", 0)
            baselines = [w.get("baseline_current") for w in entries]
            baseline = min((b for b in baselines if isinstance(b, int)), default=current)
            observed_growth = max(0, current - baseline)
            outstanding_by_service[service] = max(0, total - observed_growth)
        reserved = PROJECTION_RESERVE + sum(outstanding_by_service.values())
        now = time.monotonic()
        borrowed_by_service = {
            service: max(
                0,
                self.containers.get(service, {}).get("limit", default * MIB)
                - max(
                    default * MIB,
                    self.containers.get(service, {}).get("current", default * MIB),
                ),
            )
            for service, (default, _, _) in QUOTAS.items()
            if self.leases.get(service, 0) > now
        }
        borrowed = sum(borrowed_by_service.values())
        # The fixed projection reserve and the projector's borrowed peak both fund
        # the same future projector growth. Counting their overlap made an idle
        # peak limit consume two reservations even though container limits do not
        # allocate RAM. Keep the larger protection, not their sum.
        borrowed -= min(PROJECTION_RESERVE, borrowed_by_service.get("v2-projector", 0))
        if basic_projection:
            reserved, borrowed, extra = PROJECTION_RESERVE, 0, 0
        working = m.get("working_current", m.get("app_current", 0))
        projected = working + reserved + borrowed + extra
        host_required = SAFETY + reserved + borrowed + extra
        reason = None
        if not m or time.time() - m.get("at", 0) >= 12:
            reason = "RESOURCE_METRICS_STALE"
        elif m.get("swapping"):
            reason = "SWAP_PRESSURE_WAIT"
        elif m.get("pressure", 0) >= 1:
            reason = "PSI_PRESSURE_WAIT"
        elif m.get("app_current", 0) >= HARD:
            reason = "CGROUP_HARD_GUARD_WAIT"
        elif projected > (HARD if emergency else NORMAL):
            reason = "MEMORY_HEADROOM_WAIT"
        elif m.get("available", 0) < host_required:
            reason = "HOST_SAFETY_WAIT"
        return {
            "ok": reason is None,
            "reason": reason,
            "raw_current_mib": m.get("app_current", 0) // MIB,
            "working_current_mib": working // MIB,
            "inactive_file_mib": m.get("inactive_file", 0) // MIB,
            "outstanding_mib": reserved // MIB,
            "borrowed_mib": borrowed // MIB,
            "candidate_mib": extra // MIB,
            "projected_mib": projected // MIB,
            "limit_mib": (HARD if emergency else NORMAL) // MIB,
            "host_available_mib": m.get("available", 0) // MIB,
            "host_required_mib": host_required // MIB,
            "outstanding_by_service_mib": {
                service: value // MIB for service, value in outstanding_by_service.items()
            },
        }

    def fits(self, extra, *, emergency=False, basic_projection=False):
        return self.budget(
            extra, emergency=emergency, basic_projection=basic_projection
        )["ok"]

    def resize(self, service, amount):
        default, peak, swap = QUOTAS[service]
        c = self.containers.get(service)
        if not c or amount not in {default, peak}:
            return False
        if amount * MIB < c["current"]:
            return False
        if c["limit"] != amount * MIB:
            docker(
                "update",
                "--memory",
                str(amount * MIB),
                "--memory-swap",
                str((amount + swap) * MIB),
                "doxagent-v2-" + service + "-1",
            )
            logging.warning(
                "quota service=%s memory_mib=%s extra_swap_mib=%s", service, amount, swap
            )
            c["limit"], c["swap_limit"] = amount * MIB, (amount + swap) * MIB
        return True

    def peak(self, service, *, renewal=True):
        c = self.containers.get(service)
        if not c:
            return False
        default, maximum, _ = QUOTAS[service]
        extra = max(0, maximum * MIB - c["limit"])
        if extra and not self.fits(extra, emergency=True):
            return False
        if not self.resize(service, maximum):
            return False
        if extra:
            # A fresh grant starts a fresh quiet period, not an old idle timer.
            self.low_since.pop(service, None)
        if extra or renewal:
            self.leases[service] = time.monotonic() + 900
        return True

    def handle(self, service, payload):
        command = payload.get("command")
        now = time.monotonic()
        if command == "acquire":
            kind = payload.get("kind")
            if kind not in ALLOWED_WORK.get(service, set()):
                return {"ok": False, "reason": "WORK_NOT_ALLOWED"}
            identity = str(payload.get("identity", ""))[:200]
            batch = str(payload.get("batch") or "")[:200]
            amount, priority = WORK[kind]
            slots = payload.get("slots", 1)
            if not isinstance(slots, int) or slots not in {1, 2}:
                return {"ok": False, "reason": "INVALID_SLOT_WEIGHT"}
            amount *= slots if kind.startswith("codex_") else 1
            for token, w in self.work.items():
                if w["service"] == service and w["identity"] == identity:
                    w["expires"] = now + 120
                    return {"ok": True, "token": token}
            heavy = kind in {
                "initialization",
                "maintenance",
                "codex_maintenance",
                "codex_initialization",
            }
            basic_projection = service == "v2-projector" and kind == "projection"
            if basic_projection and any(w["service"] == service for w in self.work.values()):
                return {"ok": False, "reason": "PROJECTION_BATCH_BUSY"}
            conflict = heavy and any(w["heavy"] and w["batch"] != batch for w in self.work.values())
            higher = not basic_projection and any(
                p < priority and until > now for p, until in self.waiting.values()
            )
            if conflict or higher or not self.fits(amount * MIB, basic_projection=basic_projection):
                self.waiting[(service, identity)] = (priority, now + 10)
                if conflict:
                    return {"ok": False, "reason": "HEAVY_BATCH_CONFLICT"}
                if higher:
                    return {"ok": False, "reason": "PRIORITY_WAIT"}
                return self.budget(amount * MIB, basic_projection=basic_projection)
            # Metadata framing requests a peak; basic projection runs at its default.
            # Projector can still borrow a bounded peak on measured >75% usage.
            if service == "v2-scheduler" and kind == "maintenance":
                if not self.peak(service):
                    self.waiting[(service, identity)] = (priority, now + 10)
                    return {"ok": False, "reason": "PEAK_BUDGET_WAIT"}
            token = uuid4().hex
            self.work[token] = {
                "service": service,
                "identity": identity,
                "batch": batch,
                "heavy": heavy,
                "bytes": amount * MIB,
                "baseline_current": self.containers.get(service, {}).get("current", 0),
                "expires": now + 120,
            }
            self.waiting.pop((service, identity), None)
            return {"ok": True, "token": token}
        token = payload.get("token")
        if token not in self.work or self.work[token]["service"] != service:
            return {"ok": False, "reason": "UNKNOWN_LEASE"}
        if command == "release":
            del self.work[token]
        elif command == "renew":
            self.work[token]["expires"] = now + 120
            if service in self.leases:
                self.leases[service] = now + 900
        else:
            return {"ok": False, "reason": "COMMAND_NOT_ALLOWED"}
        return {"ok": True}

    def tick(self):
        self.sample()
        now = time.monotonic()
        # Expired reservations are not forcibly interrupted. Current RAM remains in the budget.
        self.work = {k: w for k, w in self.work.items() if w["expires"] > now}
        for work in self.work.values():
            work.setdefault(
                "baseline_current", self.containers.get(work["service"], {}).get("current", 0)
            )
        self.waiting = {k: v for k, v in self.waiting.items() if v[1] > now}
        for service, (default, _peak, _) in QUOTAS.items():
            c = self.containers.get(service)
            if not c:
                continue
            if c["current"] > default * MIB * 0.75:
                self.low_since.pop(service, None)
                active = any(w["service"] == service for w in self.work.values())
                self.peak(service, renewal=active)
            elif c["current"] < default * MIB * 0.70:
                self.low_since.setdefault(service, now)
                if now - self.low_since[service] >= 120:
                    self.resize(service, default)
                    self.leases.pop(service, None)
            else:
                self.low_since.pop(service, None)
            # Expired grants await safe reclaim; they do not become perpetual renewed grants.
            if self.leases.get(service, now + 1) <= now and c["current"] >= default * MIB:
                logging.info("expired quota service=%s reclaim=deferred_busy", service)
        budget = self.budget(0)
        logging.info(
            "budget app_mib=%d working_mib=%d available_mib=%d swap_mib=%d "
            "outstanding_mib=%d borrowed_mib=%d reservations=%d",
            self.metrics["app_current"] // MIB,
            self.metrics["working_current"] // MIB,
            self.metrics["available"] // MIB,
            self.metrics["app_swap"] // MIB,
            budget["outstanding_mib"],
            budget["borrowed_mib"],
            len(self.work),
        )
        self.persist()

    def peer(self, connection):
        pid, _, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        cgroup = Path("/proc/" + str(pid) + "/cgroup").read_text()
        return next((s for s, c in self.containers.items() if c["id"] in cgroup), None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/run/doxagent-resources/guardian.sock")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    guard = Guardian()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                self.request.settimeout(1)
                payload = json.loads(self.rfile.readline(8192))
                with guard.lock:
                    service = guard.peer(self.request)
                    response = (
                        guard.handle(service, payload)
                        if service
                        else {"ok": False, "reason": "PEER_NOT_ALLOWED"}
                    )
                    guard.persist()
            except Exception:
                response = {"ok": False, "reason": "RESOURCE_GUARD_ERROR"}
                logging.exception("admission error")
            self.wfile.write(json.dumps(response).encode() + b"\n")

    path = Path(args.socket)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()  # Explicit dedicated Unix socket; never a business file.

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    with Server(str(path), Handler) as server:
        os.chmod(path, 0o666)  # Kernel peer cgroup verification, not caller-supplied service names.
        server.timeout = 1
        tick_at = 0
        while True:
            if time.monotonic() >= tick_at:
                try:
                    with guard.lock:
                        guard.tick()
                except Exception:
                    logging.exception("metric refresh failed; retain quotas, deny stale admissions")
                tick_at = time.monotonic() + 5
            server.handle_request()


if __name__ == "__main__":
    main()
