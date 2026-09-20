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
STATE_TEXT = {
    "VALID": "已登录",
    "REAUTH_REQUIRED": "需要重新登录",
    "ENTITLEMENT_MISSING": "无订阅权限",
    "MAINTENANCE": "维护中",
    "UNKNOWN": "未验证",
}
REGION_TEXT = {
    "jp": "日本",
    "us": "美国",
    "nl": "荷兰",
    "de": "德国",
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
            "message": "登录维护服务没有响应。",
            "suggestion": "请稍后重试，或联系管理员。",
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "message": "登录维护服务返回异常。",
            "suggestion": "请联系管理员检查桌面工具安装。",
        }
    return (
        payload
        if isinstance(payload, dict)
        else {"ok": False, "message": "登录维护服务返回了无法识别的结果。"}
    )


def region_label(egress_id: str, node: str) -> str:
    prefix = egress_id.split("-", 1)[0].lower()
    return f"{REGION_TEXT.get(prefix, prefix.upper())} · {node}"


class SiteLoginApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("消息源登录维护")
        self.root.geometry("980x560")
        self.root.minsize(820, 480)
        self.rows: dict[str, dict[str, Any]] = {}
        self.session: dict[str, Any] | None = None
        self.viewer: subprocess.Popen[bytes] | None = None
        self.busy = False
        self.filter_value = tk.StringVar(value="全部网站")
        self.status_value = tk.StringVar(value="正在读取消息源状态……")
        self.session_value = tk.StringVar(value="当前没有登录维护会话。")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self.refresh()

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)
        title = ttk.Label(outer, text="消息源登录维护", font=("Sans", 18, "bold"))
        title.pack(anchor=tk.W)
        ttk.Label(
            outer,
            text="选择一个 Profile 打开独立的持久浏览器；账号、密码和验证码只输入网站页面。",
        ).pack(anchor=tk.W, pady=(3, 12))

        controls = ttk.Frame(outer)
        controls.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(controls, text="网站筛选：").pack(side=tk.LEFT)
        self.filter_box = ttk.Combobox(
            controls, textvariable=self.filter_value, state="readonly", width=24
        )
        self.filter_box.pack(side=tk.LEFT, padx=(4, 12))
        self.filter_box.bind("<<ComboboxSelected>>", lambda _event: self._render_rows())
        self.refresh_button = ttk.Button(controls, text="刷新状态", command=self.refresh)
        self.refresh_button.pack(side=tk.RIGHT)

        columns = ("site", "profile", "role", "egress", "state")
        self.tree = ttk.Treeview(outer, columns=columns, show="headings", height=13)
        labels = {
            "site": "网站",
            "profile": "Profile",
            "role": "用途",
            "egress": "出口节点 / 地区",
            "state": "登录状态",
        }
        widths = {"site": 145, "profile": 155, "role": 110, "egress": 360, "state": 135}
        for column in columns:
            self.tree.heading(column, text=labels[column])
            self.tree.column(column, width=widths[column], minwidth=80)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_buttons())

        session_frame = ttk.LabelFrame(outer, text="维护会话", padding=8)
        session_frame.pack(fill=tk.X, pady=(10, 8))
        ttk.Label(session_frame, textvariable=self.session_value).pack(side=tk.LEFT, fill=tk.X)
        self.continue_button = ttk.Button(
            session_frame, text="继续维护 / 打开查看器", command=self._continue_viewer
        )
        self.continue_button.pack(side=tk.RIGHT)

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X)
        self.open_button = ttk.Button(actions, text="打开登录页面", command=self.open_selected)
        self.open_button.pack(side=tk.LEFT)
        self.verify_button = ttk.Button(
            actions, text="登录完成并验证", command=self.verify_selected
        )
        self.verify_button.pack(side=tk.LEFT, padx=8)
        self.cancel_button = ttk.Button(
            actions, text="取消并退出维护", command=self.cancel_session
        )
        self.cancel_button.pack(side=tk.LEFT)
        self.advanced_button = ttk.Button(
            actions, text="更换验证文章（高级）", command=self.verify_with_custom_url
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
        self.status_value.set("正在处理，请稍候……")
        self._update_buttons()

        def worker() -> None:
            result = operation()
            self.root.after(0, lambda: self._finish(result, done))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, result: dict[str, Any], done: Callable[[dict[str, Any]], None]) -> None:
        self.busy = False
        if not result.get("ok"):
            message = str(result.get("message") or "操作失败。")
            suggestion = str(result.get("suggestion") or "")
            messagebox.showerror("消息源登录维护", f"{message}\n\n{suggestion}".strip())
            self.status_value.set("操作失败")
            self._update_buttons()
            return
        done(result)
        self._update_buttons()

    def refresh(self) -> None:
        self._run(lambda: call_admin(["list"]), self._apply_inventory)

    def _apply_inventory(self, payload: dict[str, Any]) -> None:
        profiles = payload.get("profiles") or []
        self.rows = {item["profile_id"]: item for item in profiles}
        sites = sorted({item["site_name"] for item in profiles})
        self.filter_box["values"] = ["全部网站", *sites]
        if self.filter_value.get() not in self.filter_box["values"]:
            self.filter_value.set("全部网站")
        self.session = payload.get("session")
        self._render_rows()
        self._render_session()
        if not payload.get("vnc_ready", False):
            self.status_value.set("VNC 服务未就绪")
        else:
            self.status_value.set(f"已读取 {len(profiles)} 个 Profile")
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
            if selected_site != "全部网站" and row["site_name"] != selected_site:
                continue
            role = "主 Profile" if row["profile_role"] == "primary" else "备用 Profile"
            state = STATE_TEXT.get(row["auth_state"], row["auth_state"])
            if row["profile_role"] == "primary" and row["auth_state"] == "VALID":
                state = "主登录身份可用"
            if row["profile_role"] == "backup" and row["auth_state"] == "UNKNOWN":
                state = "备用身份，尚未配置"
            self.tree.insert(
                "",
                tk.END,
                iid=row["profile_id"],
                values=(
                    row["site_name"],
                    row["profile_id"],
                    role,
                    region_label(row["egress_id"], row["egress_node"]),
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
            viewer = "查看器已打开" if self.session.get("viewer_running") else "查看器未打开"
            self.session_value.set(
                f"存在未完成的 {self.session.get('site_name')} / "
                f"{self.session.get('profile_id')} 登录维护（{viewer}）。"
            )
            profile_id = self.session.get("profile_id")
            if profile_id and self.tree.exists(profile_id):
                self.tree.selection_set(profile_id)
                self.tree.see(profile_id)
        else:
            self.session_value.set("当前没有登录维护会话。")

    def _update_buttons(self) -> None:
        selected = self._selected()
        active = bool(self.session)
        matching = bool(
            selected
            and self.session
            and selected["profile_id"] == self.session.get("profile_id")
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
                "打开备用 Profile",
                f"即将打开备用身份 {selected['profile_id']}，出口为 {region}。\n\n"
                "同一网站账号可能限制跨地区并发登录。是否继续？",
            ):
                return
        self._run(
            lambda: call_admin(["open", selected["profile_id"]]),
            self._opened,
        )

    def _opened(self, payload: dict[str, Any]) -> None:
        self.status_value.set(f"浏览器已启动（{payload.get('opened_in_seconds')} 秒）")
        self._launch_viewer()
        messagebox.showinfo(
            "请完成网站登录",
            "浏览器查看器已打开。请在网站页面完成账号登录或 MFA。\n\n"
            "完成后回到本窗口，点击“登录完成并验证”。",
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
                "无法打开浏览器查看器",
                "VNC Viewer 未安装或无法启动。维护会话仍然保留，请联系管理员后继续维护或取消。",
            )
            return
        self.root.after(1500, self._watch_viewer)

    def _watch_viewer(self) -> None:
        if not self.session:
            return
        if self.viewer and self.viewer.poll() is not None:
            self.status_value.set("VNC Viewer 已关闭；仍可验证或取消维护")
        else:
            self.root.after(1500, self._watch_viewer)

    def _recover(self) -> None:
        self._run(lambda: call_admin(["recover"]), self._recovered)

    def _recovered(self, payload: dict[str, Any]) -> None:
        self.session = payload.get("session")
        self._render_session()
        self.status_value.set(str(payload.get("message") or "维护会话已恢复"))

    def _continue_viewer(self) -> None:
        self._launch_viewer()
        self.root.after(1000, self._recover)

    def verify_selected(self) -> None:
        selected = self._selected()
        if selected:
            self._verify(selected["profile_id"], None)

    def verify_with_custom_url(self) -> None:
        selected = self._selected()
        if not selected:
            return
        value = simpledialog.askstring(
            "更换验证文章（高级）",
            "仅在默认文章失效时使用。请输入当前网站的一条订阅文章 HTTPS 地址：",
            parent=self.root,
        )
        if value:
            self._verify(selected["profile_id"], value.strip())

    def _verify(self, profile_id: str, url: str | None) -> None:
        arguments = ["verify", profile_id]
        if url:
            arguments.extend(["--url", url])
        self._run(lambda: call_admin(arguments), self._verified)

    def _verified(self, payload: dict[str, Any]) -> None:
        self.session = None
        self._render_session()
        message = str(payload.get("message") or "验证完成。")
        state = str(payload.get("auth_state") or "UNKNOWN")
        if state == "VALID":
            messagebox.showinfo("登录验证成功", message)
        elif state == "ENTITLEMENT_MISSING":
            messagebox.showwarning("没有订阅权限", message)
        else:
            messagebox.showwarning("登录验证未通过", message)
        self.refresh()

    def cancel_session(self) -> None:
        if not messagebox.askyesno("取消维护", "确认关闭当前登录浏览器并取消维护吗？"):
            return
        self._run(lambda: call_admin(["close"]), self._cancelled)

    def _cancelled(self, payload: dict[str, Any]) -> None:
        self.session = None
        self._render_session()
        self.status_value.set(str(payload.get("message") or "维护已取消"))
        self.refresh()

    def _on_window_close(self) -> None:
        if self.session and not messagebox.askyesno(
            "维护仍在进行",
            "当前登录维护尚未完成。关闭工具不会关闭浏览器会话，稍后可重新打开继续。\n\n"
            "确定关闭窗口吗？",
        ):
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    SiteLoginApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
