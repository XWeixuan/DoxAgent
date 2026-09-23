#!/usr/bin/python3
"""Small xRDP desktop UI for maintaining Site Access browser identities."""

from __future__ import annotations

import json
import subprocess
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, simpledialog, ttk
from typing import Any

ADMIN = "/usr/local/sbin/doxagent-site-login-admin"
VNC_VIEWER = "/usr/bin/vncviewer"
CLIPBOARD_BRIDGE = "/usr/local/bin/doxagent-site-login-clipboard-bridge"
STATE_TEXT = {
    "VALID": "Signed in",
    "REAUTH_REQUIRED": "Sign-in required",
    "ENTITLEMENT_MISSING": "No subscription access",
    "MAINTENANCE": "Maintenance in progress",
    "UNKNOWN": "Not verified",
}
REGION_TEXT = {
    "jp": "Japan",
    "us": "United States",
    "nl": "Netherlands",
    "de": "Germany",
}


def call_admin(arguments: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["/usr/bin/sudo", "-n", ADMIN, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=75,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "ok": False,
            "message": "The login maintenance service did not respond.",
            "suggestion": "Try again later or contact the administrator.",
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "message": "The login maintenance service returned an invalid response.",
            "suggestion": "Contact the administrator to check the desktop installation.",
        }
    return (
        payload
        if isinstance(payload, dict)
        else {"ok": False, "message": "The login maintenance result was not recognized."}
    )


def region_label(egress_id: str, _node: str) -> str:
    prefix = egress_id.split("-", 1)[0].lower()
    return f"{REGION_TEXT.get(prefix, prefix.upper())} ({egress_id})"


class SiteLoginApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Site Login Maintenance")
        self.root.geometry("980x560")
        self.root.minsize(820, 480)
        self.rows: dict[str, dict[str, Any]] = {}
        self.session: dict[str, Any] | None = None
        self.viewer: subprocess.Popen[bytes] | None = None
        self.busy = False
        self.filter_value = tk.StringVar(value="All sites")
        self.status_value = tk.StringVar(value="Loading profile status...")
        self.session_value = tk.StringVar(value="No login maintenance session is active.")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self.refresh()

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)
        title = ttk.Label(outer, text="Site Login Maintenance", font=("Sans", 18, "bold"))
        title.pack(anchor=tk.W)
        ttk.Label(
            outer,
            text=(
                "Select a profile to open its persistent browser. Enter credentials and MFA "
                "only on the website."
            ),
        ).pack(anchor=tk.W, pady=(3, 12))

        controls = ttk.Frame(outer)
        controls.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(controls, text="Site filter:").pack(side=tk.LEFT)
        self.filter_box = ttk.Combobox(
            controls, textvariable=self.filter_value, state="readonly", width=24
        )
        self.filter_box.pack(side=tk.LEFT, padx=(4, 12))
        self.filter_box.bind("<<ComboboxSelected>>", lambda _event: self._render_rows())
        self.refresh_button = ttk.Button(controls, text="Refresh", command=self.refresh)
        self.refresh_button.pack(side=tk.RIGHT)

        columns = ("site", "identity", "runtime", "role", "egress", "state")
        self.tree = ttk.Treeview(outer, columns=columns, show="headings", height=13)
        labels = {
            "site": "Site",
            "identity": "Browser Identity",
            "runtime": "Runtime",
            "role": "Role",
            "egress": "Egress / Region",
            "state": "Login status",
        }
        widths = {
            "site": 130,
            "identity": 155,
            "runtime": 110,
            "role": 80,
            "egress": 280,
            "state": 130,
        }
        for column in columns:
            self.tree.heading(column, text=labels[column])
            self.tree.column(column, width=widths[column], minwidth=80)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_buttons())

        session_frame = ttk.LabelFrame(outer, text="Maintenance session", padding=8)
        session_frame.pack(fill=tk.X, pady=(10, 8))
        ttk.Label(session_frame, textvariable=self.session_value).pack(side=tk.LEFT, fill=tk.X)
        self.continue_button = ttk.Button(
            session_frame, text="Continue / Open Viewer", command=self._continue_viewer
        )
        self.continue_button.pack(side=tk.RIGHT)

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X)
        self.open_button = ttk.Button(actions, text="Open Browser", command=self.open_selected)
        self.open_button.pack(side=tk.LEFT)
        self.verify_button = ttk.Button(
            actions, text="Finish Login and Verify", command=self.verify_selected
        )
        self.verify_button.pack(side=tk.LEFT, padx=8)
        self.cancel_button = ttk.Button(
            actions, text="Cancel Maintenance", command=self.cancel_session
        )
        self.cancel_button.pack(side=tk.LEFT)
        self.advanced_button = ttk.Button(
            actions,
            text="Change Verification Article (Advanced)",
            command=self.verify_with_custom_url,
        )
        self.advanced_button.pack(side=tk.LEFT, padx=8)
        ttk.Label(actions, textvariable=self.status_value).pack(side=tk.RIGHT)
        self._update_buttons()

    def _selected(self) -> dict[str, Any] | None:
        selection = self.tree.selection()
        return self.rows.get(selection[0]) if selection else None

    def _run(
        self,
        operation: Callable[[], dict[str, Any]],
        done: Callable[[dict[str, Any]], None],
    ) -> None:
        if self.busy:
            return
        self.busy = True
        self.status_value.set("Working...")
        self._update_buttons()

        def worker() -> None:
            result = operation()
            self.root.after(0, lambda: self._finish(result, done))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, result: dict[str, Any], done: Callable[[dict[str, Any]], None]) -> None:
        self.busy = False
        if not result.get("ok"):
            message = str(result.get("message") or "The operation failed.")
            suggestion = str(result.get("suggestion") or "")
            messagebox.showerror("Site Login Maintenance", f"{message}\n\n{suggestion}".strip())
            self.status_value.set("Operation failed")
            self._update_buttons()
            return
        done(result)
        self._update_buttons()

    def refresh(self) -> None:
        self._run(lambda: call_admin(["list"]), self._apply_inventory)

    def _apply_inventory(self, payload: dict[str, Any]) -> None:
        profiles = payload.get("profiles") or []
        self.rows = {item.get("row_id", item["profile_id"]): item for item in profiles}
        sites = sorted({item["site_name"] for item in profiles})
        self.filter_box["values"] = ["All sites", *sites]
        if self.filter_value.get() not in self.filter_box["values"]:
            self.filter_value.set("All sites")
        self.session = payload.get("session")
        self._render_rows()
        self._render_session()
        if not payload.get("vnc_ready", False):
            self.status_value.set("VNC service is not ready")
        else:
            self.status_value.set(f"Loaded {len(profiles)} profiles")
        if self.session:
            self.root.after(50, self._recover)

    def _render_rows(self) -> None:
        prior = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        selected_site = self.filter_value.get()
        ordered = sorted(
            self.rows.values(),
            key=lambda row: (row["site_name"].casefold(), row["priority"]),
        )
        for row in ordered:
            if selected_site != "All sites" and row["site_name"] != selected_site:
                continue
            role = "Primary" if row["profile_role"] == "primary" else "Backup"
            state = STATE_TEXT.get(row["auth_state"], row["auth_state"])
            if row.get("verification_kind") == "public_access" and row["auth_state"] == "UNKNOWN":
                state = "Public access only"
            if row.get("manual_attention_required"):
                state = "Needs manual challenge"
            if row.get("operational_state") in {
                "DRAINING",
                "DRAINING_FOR_MAINTENANCE",
                "MAINTENANCE",
            }:
                state = "Maintenance active"
            if row["profile_role"] == "primary" and row["auth_state"] == "VALID":
                state = "Primary login ready"
            if row["profile_role"] == "backup" and row["auth_state"] == "UNKNOWN":
                state = "Backup not configured"
            self.tree.insert(
                "",
                tk.END,
                iid=row.get("row_id", row["profile_id"]),
                values=(
                    row["site_name"],
                    row.get("identity_id", row["profile_id"]),
                    "External Chrome"
                    if row.get("runtime_kind") == "external_chrome"
                    else "Managed",
                    role,
                    region_label(row["egress_id"], row["egress_node"])
                    + (f" / {row['observed_ip']}" if row.get("observed_ip") else ""),
                    state,
                ),
            )
        if prior and self.tree.exists(prior[0]):
            self.tree.selection_set(prior[0])
        elif self.tree.get_children():
            preferred = next(
                (
                    item
                    for item in self.tree.get_children()
                    if self.rows[item]["profile_role"] == "primary"
                ),
                self.tree.get_children()[0],
            )
            self.tree.selection_set(preferred)

    def _render_session(self) -> None:
        if self.session:
            viewer = "viewer open" if self.session.get("viewer_running") else "viewer closed"
            self.session_value.set(
                f"Unfinished maintenance: {self.session.get('site_name')} / "
                f"{self.session.get('profile_id')} ({viewer})."
            )
            row_id = f"{self.session.get('site_id')}:{self.session.get('identity_id')}"
            if self.tree.exists(row_id):
                self.tree.selection_set(row_id)
                self.tree.see(row_id)
        else:
            self.session_value.set("No login maintenance session is active.")

    def _update_buttons(self) -> None:
        selected = self._selected()
        active = bool(self.session)
        matching = bool(
            selected
            and self.session
            and selected.get("identity_id", selected["profile_id"])
            == self.session.get("identity_id", self.session.get("profile_id"))
            and selected["site_id"] == self.session.get("site_id")
        )
        disabled = self.busy
        open_state = tk.NORMAL if selected and not active and not disabled else tk.DISABLED
        self.open_button.configure(state=open_state)
        self.verify_button.configure(state=tk.NORMAL if matching and not disabled else tk.DISABLED)
        advanced_state = tk.NORMAL if matching and not disabled else tk.DISABLED
        self.advanced_button.configure(state=advanced_state)
        self.cancel_button.configure(state=tk.NORMAL if active and not disabled else tk.DISABLED)
        self.continue_button.configure(state=tk.NORMAL if active and not disabled else tk.DISABLED)
        self.refresh_button.configure(state=tk.DISABLED if disabled else tk.NORMAL)

    def open_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        if selected["profile_role"] == "backup":
            region = region_label(selected["egress_id"], selected["egress_node"])
            if not messagebox.askokcancel(
                "Open Backup Profile",
                f"You are opening backup profile {selected['profile_id']} via {region}.\n\n"
                "The website may restrict simultaneous logins from different regions. Continue?",
            ):
                return
        self._run(
            lambda: call_admin(
                [
                    "open",
                    selected.get("identity_id", selected["profile_id"]),
                    "--site-id",
                    selected["site_id"],
                ]
            ),
            self._opened,
        )

    def _opened(self, payload: dict[str, Any]) -> None:
        self.status_value.set(f"Browser opened in {payload.get('opened_in_seconds')} seconds")
        self._launch_viewer()
        messagebox.showinfo(
            "Complete Website Login",
            "The browser viewer is open. Complete the website login or MFA.\n\n"
            "Then return here and click Finish Login and Verify.",
        )
        self.root.after(1000, self._recover)

    def _launch_viewer(self) -> None:
        if self.viewer and self.viewer.poll() is None:
            return
        try:
            self.viewer = subprocess.Popen(
                [VNC_VIEWER, "-Shared", "127.0.0.1::5900"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            messagebox.showerror(
                "Cannot Open Browser Viewer",
                "VNC Viewer could not start. The maintenance session is still active. "
                "Contact the administrator, then continue or cancel it.",
            )
            return
        try:
            subprocess.Popen(
                ["/usr/bin/python3", CLIPBOARD_BRIDGE, "--viewer-pid", str(self.viewer.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            messagebox.showwarning(
                "Clipboard Unavailable",
                "The browser viewer is open, but clipboard forwarding could not start.",
            )
        self.root.after(1500, self._watch_viewer)

    def _watch_viewer(self) -> None:
        if not self.session:
            return
        if self.viewer and self.viewer.poll() is not None:
            self.status_value.set("VNC Viewer closed; you can still verify or cancel")
        else:
            self.root.after(1500, self._watch_viewer)

    def _recover(self) -> None:
        self._run(lambda: call_admin(["recover"]), self._recovered)

    def _recovered(self, payload: dict[str, Any]) -> None:
        self.session = payload.get("session")
        self._render_session()
        self.status_value.set(str(payload.get("message") or "Maintenance session recovered"))

    def _continue_viewer(self) -> None:
        self._launch_viewer()
        self.root.after(1000, self._recover)

    def verify_selected(self) -> None:
        selected = self._selected()
        if selected:
            self._verify(
                selected.get("identity_id", selected["profile_id"]),
                selected["site_id"],
                None,
            )

    def verify_with_custom_url(self) -> None:
        selected = self._selected()
        if not selected:
            return
        value = simpledialog.askstring(
            "Change Verification Article (Advanced)",
            "Use only if the default article has expired. Enter a subscription article "
            "HTTPS URL from the current site:",
            parent=self.root,
        )
        if value:
            self._verify(
                selected.get("identity_id", selected["profile_id"]),
                selected["site_id"],
                value.strip(),
            )

    def _verify(self, profile_id: str, site_id: str, url: str | None) -> None:
        arguments = ["verify", profile_id, "--site-id", site_id]
        if url:
            arguments.extend(["--url", url])
        self._run(lambda: call_admin(arguments), self._verified)

    def _verified(self, payload: dict[str, Any]) -> None:
        self.session = None
        self._render_session()
        message = str(payload.get("message") or "Verification completed.")
        state = str(payload.get("auth_state") or "UNKNOWN")
        if state == "VALID":
            messagebox.showinfo("Login Verified", message)
        elif state == "ENTITLEMENT_MISSING":
            messagebox.showwarning("No Subscription Access", message)
        else:
            messagebox.showwarning("Login Verification Failed", message)
        self.refresh()

    def cancel_session(self) -> None:
        if not messagebox.askyesno(
            "Cancel Maintenance", "Close the current login browser and cancel maintenance?"
        ):
            return
        self._run(lambda: call_admin(["close"]), self._cancelled)

    def _cancelled(self, payload: dict[str, Any]) -> None:
        self.session = None
        self._render_session()
        self.status_value.set(str(payload.get("message") or "Maintenance cancelled"))
        self.refresh()

    def _on_window_close(self) -> None:
        if self.session and not messagebox.askyesno(
            "Maintenance Is Still Active",
            "Closing this tool will leave the browser session active. You can reopen the tool "
            "later to continue.\n\nClose this window?",
        ):
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    SiteLoginApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
