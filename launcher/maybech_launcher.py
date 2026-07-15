"""Maybech desktop launcher.

Lightweight Tk GUI that starts/stops the backend (FastAPI) and frontend
(Next.js dev server) via the existing start_backend.ps1 / start_frontend.ps1
scripts, streams their combined logs, and opens the dashboard in the default
browser once the frontend port is reachable. Packaged into maybech.exe via
launcher/build_exe.ps1 so it can be placed and double-clicked from anywhere
on the machine.
"""

from __future__ import annotations

import json
import queue
import socket
import subprocess
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

CONFIG_DIR = Path.home() / "AppData" / "Local" / "maybech-launcher"
CONFIG_PATH = CONFIG_DIR / "config.json"
MODES = ["simulation", "demo", "live_safe", "live_armed"]
BACKEND_PORT = 8000
FRONTEND_PORT = 3000


def is_valid_project_root(path: Path) -> bool:
    return (
        (path / "start_backend.ps1").is_file()
        and (path / "start_frontend.ps1").is_file()
        and (path / "frontend" / "package.json").is_file()
    )


def load_project_root() -> Optional[Path]:
    if not CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = data.get("project_root")
    if not raw:
        return None
    path = Path(raw)
    return path if is_valid_project_root(path) else None


def save_project_root(path: Path) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"project_root": str(path)}), encoding="utf-8")


def port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class ManagedProcess:
    """Wraps a powershell-launched script, streaming its output into a queue."""

    def __init__(self, name: str, log_queue: "queue.Queue[tuple[str, str]]"):
        self.name = name
        self.log_queue = log_queue
        self.proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, args: list[str], cwd: Path) -> None:
        if self.running:
            return
        self.proc = subprocess.Popen(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self.log_queue.put((self.name, f"[launcher] started (pid {self.proc.pid})\n"))

    def _pump(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            self.log_queue.put((self.name, line))
        self.log_queue.put((self.name, "[launcher] process exited\n"))

    def stop(self) -> None:
        if not self.running:
            return
        pid = self.proc.pid
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        finally:
            self.proc = None
        self.log_queue.put((self.name, "[launcher] stopped\n"))


class LauncherApp:
    def __init__(self, root: tk.Tk, project_root: Path):
        self.root = root
        self.project_root = project_root
        self.log_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.backend = ManagedProcess("backend", self.log_queue)
        self.frontend = ManagedProcess("frontend", self.log_queue)
        self._browser_watch_token = 0

        root.title(f"Maybech Launcher — {project_root}")
        root.geometry("880x580")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.backend_var = tk.BooleanVar(value=True)
        self.frontend_var = tk.BooleanVar(value=True)
        self.mode_var = tk.StringVar(value="simulation")

        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")

        ttk.Checkbutton(top, text="Backend", variable=self.backend_var).grid(row=0, column=0, padx=(0, 12))
        ttk.Checkbutton(top, text="Frontend", variable=self.frontend_var).grid(row=0, column=1, padx=(0, 24))

        ttk.Label(top, text="Mode:").grid(row=0, column=2, padx=(0, 6))
        ttk.OptionMenu(top, self.mode_var, self.mode_var.get(), *MODES).grid(row=0, column=3, padx=(0, 24))

        ttk.Button(top, text="Start", command=self.start_selected).grid(row=0, column=4, padx=4)
        ttk.Button(top, text="Stop", command=self.stop_all).grid(row=0, column=5, padx=4)
        ttk.Button(top, text="Restart", command=self.restart_selected).grid(row=0, column=6, padx=4)
        ttk.Button(top, text="Open browser", command=self.open_browser).grid(row=0, column=7, padx=4)
        ttk.Button(top, text="Change folder…", command=self.change_project_root).grid(row=0, column=8, padx=4)

        status = ttk.Frame(root, padding=(10, 0))
        status.pack(fill="x")
        self.backend_status = ttk.Label(status, text="Backend: stopped")
        self.backend_status.pack(side="left", padx=(0, 24))
        self.frontend_status = ttk.Label(status, text="Frontend: stopped")
        self.frontend_status.pack(side="left")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self.backend_log = self._make_log_tab(notebook, "Backend log")
        self.frontend_log = self._make_log_tab(notebook, "Frontend log")

        self.root.after(200, self._drain_log_queue)
        self.root.after(1000, self._refresh_status)

    def _make_log_tab(self, notebook: ttk.Notebook, title: str) -> scrolledtext.ScrolledText:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        widget = scrolledtext.ScrolledText(frame, state="disabled", wrap="word", font=("Consolas", 9))
        widget.pack(fill="both", expand=True)
        return widget

    def _append_log(self, widget: scrolledtext.ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.insert("end", text)
        widget.see("end")
        widget.configure(state="disabled")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                name, line = self.log_queue.get_nowait()
                self._append_log(self.backend_log if name == "backend" else self.frontend_log, line)
        except queue.Empty:
            pass
        self.root.after(200, self._drain_log_queue)

    def _refresh_status(self) -> None:
        self.backend_status.configure(text=f"Backend: {'running' if self.backend.running else 'stopped'}")
        self.frontend_status.configure(text=f"Frontend: {'running' if self.frontend.running else 'stopped'}")
        self.root.after(1000, self._refresh_status)

    def start_selected(self) -> None:
        if not self.backend_var.get() and not self.frontend_var.get():
            messagebox.showwarning("Maybech Launcher", "Select at least Backend or Frontend to start.")
            return
        if self.backend_var.get() and not self.backend.running:
            self.backend.start(
                [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(self.project_root / "start_backend.ps1"),
                    "-Mode", self.mode_var.get(),
                    "-NoLineWebhookTunnel",
                ],
                cwd=self.project_root,
            )
        if self.frontend_var.get() and not self.frontend.running:
            self.frontend.start(
                [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(self.project_root / "start_frontend.ps1"),
                ],
                cwd=self.project_root,
            )
            self._watch_for_browser()

    def stop_all(self) -> None:
        self.backend.stop()
        self.frontend.stop()

    def restart_selected(self) -> None:
        self.stop_all()
        self.root.after(1500, self.start_selected)

    def open_browser(self) -> None:
        webbrowser.open(f"http://localhost:{FRONTEND_PORT}")

    def _watch_for_browser(self) -> None:
        self._browser_watch_token += 1
        token = self._browser_watch_token

        def poll() -> None:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if token != self._browser_watch_token:
                    return
                if port_is_open(FRONTEND_PORT):
                    self.root.after(500, self.open_browser)
                    return
                time.sleep(1)

        threading.Thread(target=poll, daemon=True).start()

    def change_project_root(self) -> None:
        chosen = filedialog.askdirectory(title="Select the maybech project folder")
        if not chosen:
            return
        path = Path(chosen)
        if not is_valid_project_root(path):
            messagebox.showerror(
                "Maybech Launcher",
                "That folder doesn't look like the maybech project "
                "(missing start_backend.ps1 / start_frontend.ps1 / frontend/package.json).",
            )
            return
        save_project_root(path)
        messagebox.showinfo("Maybech Launcher", "Project folder updated. Restart the launcher to apply it.")

    def _on_close(self) -> None:
        if (self.backend.running or self.frontend.running) and not messagebox.askyesno(
            "Maybech Launcher", "Backend/frontend are still running. Stop them and exit?"
        ):
            return
        self.stop_all()
        self.root.destroy()


def prompt_for_project_root(root: tk.Tk) -> Optional[Path]:
    messagebox.showinfo(
        "Maybech Launcher",
        "Select the maybech project folder (the one containing start_backend.ps1).",
    )
    while True:
        chosen = filedialog.askdirectory(title="Select the maybech project folder")
        if not chosen:
            return None
        path = Path(chosen)
        if is_valid_project_root(path):
            save_project_root(path)
            return path
        messagebox.showerror(
            "Maybech Launcher",
            "That folder doesn't look like the maybech project "
            "(missing start_backend.ps1 / start_frontend.ps1 / frontend/package.json).",
        )


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    project_root = load_project_root()
    if project_root is None:
        project_root = prompt_for_project_root(root)
    if project_root is None:
        root.destroy()
        return
    root.deiconify()
    LauncherApp(root, project_root)
    root.mainloop()


if __name__ == "__main__":
    main()
