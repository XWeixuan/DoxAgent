"""Own one test window/viewer; never touch pre-existing desktop processes."""
import importlib.machinery
import importlib.util
import json
import subprocess
import tkinter as tk

loader = importlib.machinery.SourceFileLoader('login_ui', '/usr/local/bin/doxagent-site-login-ui')
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
root = tk.Tk()
app = module.SiteLoginApp(root)

def check():
    payload = module.call_admin(['recover'])
    assert payload.get('session'), payload
    app._launch_viewer()
    root.after(2500, finish)

def finish():
    windows = subprocess.check_output(['xwininfo', '-root', '-tree'], text=True)
    print(json.dumps({'gui_window': 'Site Login Maintenance' in windows,
        'viewer_alive': app.viewer is not None and app.viewer.poll() is None,
        'viewer_window': 'TigerVNC' in windows or 'VNC' in windows}), flush=True)
    app._stop_owned_viewer()
    print(json.dumps(module.call_admin(['close'])), flush=True)
    root.destroy()

root.after(1800, check)
root.mainloop()
