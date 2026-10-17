#!/usr/bin/python3
"""Responsive xRDP maintenance UI: human tasks are not browser business leases."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from uuid import uuid4

ADMIN = "/usr/local/sbin/doxagent-site-login-admin"
VNC_VIEWER = "/usr/bin/vncviewer"
CLIPBOARD_BRIDGE = "/usr/local/bin/doxagent-site-login-clipboard-bridge"
TERMINAL = {"DONE", "FAILED", "CANCELLED", "INTERRUPTED"}


def viewer_binding_matches(expected, sessions):
    return any(
        session["state"] == "OPEN"
        and all(
            session.get(key) == expected.get(key)
            for key in ("session_id", "generation", "identity_id")
        )
        for session in sessions
    )


def call_admin(arguments):
    try:
        reply = subprocess.run(
            ["/usr/bin/sudo", "-n", ADMIN, *arguments],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        result = json.loads(reply.stdout)
        if not isinstance(result, dict):
            raise ValueError("invalid result")
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"ok": False, "message": "Maintenance service unavailable. Pages are preserved."}


def region_label(egress_id, node):
    regions = {
        "🇩🇪": "Germany",
        "🇳🇱": "Netherlands",
        "🇺🇸": "United States",
        "🇯🇵": "Japan",
        "🇹🇷": "Turkey",
        "🇸🇬": "Singapore",
    }
    region = next((name for flag, name in regions.items() if flag in node), None)
    if region is None:
        region = {
            "de": "Germany",
            "nl": "Netherlands",
            "us": "United States",
            "jp": "Japan",
            "tr": "Turkey",
        }.get(egress_id.split("-", 1)[0], "Unknown region")
    return f"{region} ({egress_id})"


class SiteLoginApp:
    def __init__(self, root):
        self.root = root
        root.title("Site Login Maintenance")
        root.geometry("1180x650")
        self.mailbox = queue.Queue()
        self._closed = False
        self._pending = set()
        self._generations = {}
        self.rows = {}
        self.challenges = {}
        self.session = None
        self.operation = None
        self.viewer = None
        self._viewer_pending = None
        self._status_failures = 0
        self.busy = False  # Legacy inspection compatibility; never gates readonly actions.
        self.status_value = tk.StringVar(value="Loading source inventory...")
        self.session_value = tk.StringVar(value="No Viewer session selected.")
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        root.after(50, self._drain)
        self.refresh()

    def _build(self):
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Site Login Maintenance", font=("Sans", 18, "bold")).pack(anchor="w")
        ttk.Label(
            frame, text="Credentials and MFA stay in the website. Later preserves challenge pages."
        ).pack(anchor="w", pady=6)
        controls = ttk.Frame(frame)
        controls.pack(fill="x")
        ttk.Button(controls, text="Refresh", command=self.refresh).pack(side="right")
        ttk.Button(controls, text="Cancel Operation", command=self.cancel_operation).pack(
            side="right", padx=6
        )
        ttk.Label(controls, textvariable=self.status_value).pack(side="left")
        self.tabs = ttk.Notebook(frame)
        self.tabs.pack(fill="both", expand=True, pady=8)
        challenge_frame = ttk.Frame(self.tabs)
        login_frame = ttk.Frame(self.tabs)
        self.tabs.add(challenge_frame, text="Challenges")
        self.tabs.add(login_frame, text="Login & Access")
        self.challenge_tree = self._tree(
            challenge_frame,
            ("source", "identity", "state", "target", "opened"),
            ("Source", "Identity", "Task state", "Original target", "Opened"),
        )
        self.tree = self._tree(
            login_frame,
            ("source", "identity", "role", "egress", "auth", "crawler", "body"),
            ("Source", "Identity", "Role", "Observed egress", "Authentication", "Crawler", "Body"),
        )
        buttons = ttk.Frame(challenge_frame)
        buttons.pack(fill="x", pady=6)
        for text, action in (
            ("Open Original", "OPEN_CHALLENGE"),
            ("Verify Challenge", "VERIFY_CHALLENGE"),
            ("Later", "RELEASE_VIEWER"),
            ("Reopen Lost Page", "REOPEN_LOST_CHALLENGE"),
        ):
            ttk.Button(buttons, text=text, command=lambda a=action: self._action(a)).pack(
                side="left", padx=3
            )
        buttons = ttk.Frame(login_frame)
        ttk.Button(challenge_frame, text="Discard Page...", command=self._discard_challenge).pack(
            anchor="e"
        )
        buttons.pack(fill="x", pady=6)
        for text, action in (
            ("Open Login", "OPEN_LOGIN"),
            ("Verify Access", "VERIFY_LOGIN"),
            ("Close Login", "CLOSE_LOGIN"),
            ("Later", "RELEASE_VIEWER"),
            ("Activate Primary", "ACTIVATE_PRIMARY"),
        ):
            ttk.Button(buttons, text=text, command=lambda a=action: self._action(a)).pack(
                side="left", padx=3
            )
        ttk.Button(
            buttons, text="Verification Article...", command=self.verify_with_custom_url
        ).pack(side="left")
        ttk.Label(frame, textvariable=self.session_value).pack(anchor="w")
        ttk.Button(frame, text="Continue / Open Viewer", command=self._continue_viewer).pack(
            anchor="e"
        )
        ttk.Button(frame, text="Retry Close", command=lambda: self._action("CLOSE_LOGIN")).pack(
            anchor="e"
        )

    @staticmethod
    def _tree(parent, columns, headings):
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=15)
        for column, title in zip(columns, headings, strict=True):
            tree.heading(column, text=title)
            tree.column(column, width=150, minwidth=70)
        tree.pack(fill="both", expand=True)
        return tree

    def _run(self, operation, done, *, channel="command"):
        if self._closed or channel in self._pending:
            return
        generation = self._generations.get(channel, 0) + 1
        self._generations[channel] = generation
        self._pending.add(channel)

        def worker():
            try:
                result = operation()
            except Exception:
                result = {
                    "ok": False,
                    "message": "Operation failed. Refresh the session status before retrying.",
                }
            finally:
                self.mailbox.put((channel, generation, done, result))

        threading.Thread(target=worker, daemon=True).start()

    def _drain(self):
        if self._closed:
            return
        while True:
            try:
                channel, generation, callback, result = self.mailbox.get_nowait()
            except queue.Empty:
                break
            if generation != self._generations.get(channel):
                continue
            self._pending.discard(channel)
            try:
                callback(result)
            except Exception:
                self.status_value.set("Display update failed. Refresh; pages are preserved.")
        self.root.after(50, self._drain)

    def refresh(self):
        self._run(lambda: call_admin(["list"]), self._apply_inventory, channel="inventory")

    def _apply_inventory(self, result):
        if not result.get("ok"):
            self.status_value.set(result.get("message", "Inventory unavailable"))
            return
        if result.get("status") == "INITIALIZING":
            self.status_value.set("Registry snapshot initializing...")
            self.root.after(2000, self.refresh)
            return
        if self.operation is None and result.get("active_operations"):
            self.operation = result["active_operations"][0]
            self.root.after(1000, self._poll_operation)
        elif (
            self.operation
            and self.operation.get("state") not in TERMINAL
            and self._status_failures >= 2
        ):
            self._status_failures = 0
            self.root.after(1000, self._poll_operation)
        self.rows = {row["row_id"]: row for row in result.get("profiles", [])}
        self.challenges = {task["task_id"]: task for task in result.get("challenges", [])}
        for tree, records in ((self.tree, self.rows), (self.challenge_tree, self.challenges)):
            selected = tree.selection()
            tree.delete(*tree.get_children())
            for key, row in records.items():
                if tree is self.tree:
                    values = (
                        row.get("scope_id"),
                        row["identity_id"],
                        row["profile_role"] + (" (active)" if row.get("active") else ""),
                        str(row.get("observed_ip") or "")
                        + " "
                        + region_label(row["egress_id"], row["egress_node"]),
                        row.get("auth_state"),
                        row.get("crawler_ready", "NOT_TESTED"),
                        row.get("body_ready", "NOT_TESTED"),
                    )
                else:
                    values = (
                        row["scope_id"],
                        row["identity_id"],
                        row["state"],
                        row.get("original_target_id") or "TARGET_LOST",
                        row["opened_at"],
                    )
                tree.insert("", "end", iid=key, values=values)
            if selected and tree.exists(selected[0]):
                tree.selection_set(selected[0])
        sessions = result.get("sessions", [])
        if self._viewer_pending:
            expected, deadline = self._viewer_pending
            if viewer_binding_matches(expected, sessions):
                self._stop_owned_viewer()
                self._launch_viewer()
                self._viewer_pending = None
            elif time.monotonic() > deadline:
                self._viewer_pending = None
                self.status_value.set(
                    "Viewer selection changed or is not confirmed. Refresh before Continue."
                )
            else:
                self.root.after(500, self.refresh)
        if self.session:
            self.session = next(
                (s for s in sessions if s["session_id"] == self.session["session_id"]), None
            )
        if not self.session:
            self.session = next((s for s in sessions if s["state"] == "OPEN"), None)
        self.session_value.set(
            f"Viewer: {self.session['identity_id']} / {self.session['kind']} / "
            f"{self.session['session_id']}"
            if self.session
            else "No Viewer session selected."
        )
        if not self.operation:
            self.status_value.set(
                f"Loaded {len(self.rows)} identities; {len(self.challenges)} pending tasks"
                + (" (stale snapshot)" if result.get("stale") else "")
            )
        if not result.get("vnc_ready", False):
            self.status_value.set("Viewer service unavailable. Tasks and profiles are preserved.")

    def _selected(self):
        keys = self.tree.selection()
        return self.rows.get(keys[0]) if keys else None

    def _action(self, action, url=None):
        if self.operation and self.operation.get("state") not in TERMINAL:
            self.status_value.set("An operation is active. Refresh/status/cancel remain available.")
            return
        row = self._selected()
        args = ["action", action, "--idempotency-key", uuid4().hex]
        if action in {"OPEN_CHALLENGE", "REOPEN_LOST_CHALLENGE"}:
            selected = self.challenge_tree.selection()
            if not selected:
                return
            args.extend(["--task-id", selected[0]])
        elif action in {"OPEN_LOGIN", "ACTIVATE_PRIMARY"}:
            if not row:
                return
            if row["profile_role"] == "backup" and not messagebox.askokcancel(
                "Backup Identity",
                "This uses an independent login profile and egress:\n"
                + str(row.get("egress_node"))
                + "\nThe account may restrict concurrent logins.",
            ):
                return
            args.extend(["--scope-id", row["scope_id"], "--identity-id", row["identity_id"]])
        else:
            if not self.session:
                return
            args.extend(["--session-id", self.session["session_id"]])
        if url:
            args.extend(["--url", url])
        self.status_value.set(action + ": submitting...")
        self._run(lambda: call_admin(args), self._submitted)

    def _submitted(self, result):
        if not result.get("ok"):
            self.status_value.set(result.get("message", "Submission failed; page preserved"))
            return
        self.operation = result
        self._status_failures = 0
        self.status_value.set(result["action"] + ": " + result["stage"])
        self.root.after(1000, self._poll_operation)

    def _discard_challenge(self):
        if self.operation and self.operation.get("state") not in TERMINAL:
            return
        selected = self.challenge_tree.selection()
        if selected and messagebox.askyesno(
            "Discard Challenge Page",
            "Close this task's original page? The source remains blocked. "
            "The original challenge cannot be restored later.",
        ):
            arguments = [
                "action",
                "CLOSE_LOGIN",
                "--task-id",
                selected[0],
                "--confirm-discard",
                "--idempotency-key",
                uuid4().hex,
            ]
            self._run(lambda: call_admin(arguments), self._submitted)

    def _poll_operation(self):
        if self.operation:
            operation_id = self.operation["operation_id"]
            self._run(
                lambda: call_admin(["status", operation_id]),
                self._operation_status,
                channel="status",
            )

    def _operation_status(self, result):
        if self.operation and result.get("operation_id") not in {
            None,
            self.operation["operation_id"],
        }:
            return
        if (
            self.operation
            and self.operation.get("state") in TERMINAL
            and result.get("state") not in TERMINAL
        ):
            return
        if not result.get("ok"):
            self._status_failures += 1
            self.status_value.set("Status unavailable; operation reference preserved.")
            if self._status_failures < 2:
                self.root.after(3000, self._poll_operation)
            return
        self._status_failures = 0
        self.operation = result
        self.status_value.set(result["action"] + ": " + result["stage"])
        if result["state"] not in TERMINAL:
            self.root.after(1000, self._poll_operation)
            return
        details = result.get("result") or {}
        if details.get("session_id"):
            self.session = {**(self.session or {}), **details}
        if details.get("stage") == "VIEWER_READY":
            self._viewer_pending = (details, time.monotonic() + 5)
        if details.get("session_closed") or details.get("released"):
            self._viewer_pending = None
            self._stop_owned_viewer()
            self.session = None
        if result["state"] == "FAILED":
            self.status_value.set(
                result.get("error") or "Operation failed; original page preserved"
            )
        elif details:
            self.status_value.set(json.dumps(details, ensure_ascii=True))
        self.refresh()
        self.root.after(2100, self.refresh)

    def cancel_operation(self):
        if self.operation:
            operation_id = self.operation["operation_id"]
            self._run(
                lambda: call_admin(["cancel-operation", operation_id]),
                self._operation_status,
                channel="cancel",
            )

    def verify_with_custom_url(self):
        url = simpledialog.askstring(
            "Verification Article", "HTTPS article URL belonging to this source:"
        )
        if url:
            kind = (
                "VERIFY_CHALLENGE"
                if self.session and self.session.get("kind") == "CHALLENGE"
                else "VERIFY_LOGIN"
            )
            self._action(kind, url)

    def _launch_viewer(self):
        try:
            self.viewer = subprocess.Popen(
                [VNC_VIEWER, "-Shared", "127.0.0.1::5900"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.Popen(
                ["/usr/bin/python3", CLIPBOARD_BRIDGE, "--viewer-pid", str(self.viewer.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self.status_value.set("Viewer unavailable. Original page/session is preserved.")

    def _continue_viewer(self):
        if self.session:
            action = "OPEN_CHALLENGE" if self.session.get("kind") == "CHALLENGE" else "OPEN_LOGIN"
            arguments = [
                "action",
                action,
                "--idempotency-key",
                uuid4().hex,
                "--session-id",
                self.session["session_id"],
            ]
            if self.session.get("task_id"):
                arguments += ["--task-id", self.session["task_id"]]
            self._run(lambda: call_admin(arguments), self._submitted)

    def _stop_owned_viewer(self):
        viewer, self.viewer = self.viewer, None
        if viewer and viewer.poll() is None:
            try:
                viewer.terminate()
            except OSError:
                pass

    def _on_window_close(self):
        if self.session and not messagebox.askyesno(
            "Session Remains Active",
            "Leave this browser session active for later? No page will be closed.",
        ):
            return
        self._closed = True
        self._stop_owned_viewer()
        self.root.destroy()


def main():
    root = tk.Tk()
    SiteLoginApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
