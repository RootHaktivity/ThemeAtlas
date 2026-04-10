"""
Settings tab – preferences, environment info, GNOME extension helper, log access.
"""

import subprocess
import tkinter as tk
from tkinter import messagebox, ttk

from ...logger import LOG_FILE, get_logger

log = get_logger(__name__)

_BG  = "#f0f2f5"
_TEXT = "#202124"
_TEXT2 = "#5f6368"


class SettingsTab(ttk.Frame):
    """Preferences and environment information tab."""

    def __init__(self, parent: tk.Widget, app) -> None:
        super().__init__(parent)
        self._app = app

        # Persistent preference variables
        self.system_wide = tk.BooleanVar(value=False)
        self.apply_flatpak = tk.BooleanVar(value=True)
        self.copy_gtk4 = tk.BooleanVar(value=True)

        self._build()

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        canvas = tk.Canvas(self, bg=_BG, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        content = tk.Frame(canvas, bg=_BG, padx=28, pady=20)
        win_id = canvas.create_window((0, 0), window=content, anchor="nw")

        content.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(win_id, width=e.width),
        )

        # ── Sections ──────────────────────────────────────────────────────────
        self._section(content, "Installation Preferences")
        self._checkbox(content, "Install themes system-wide (/usr/share/) — requires sudo",
                       self.system_wide)
        self._checkbox(content, "Apply Flatpak theme overrides after switching themes",
                       self.apply_flatpak)
        self._checkbox(content, "Copy GTK-4.0 assets to ~/.config/gtk-4.0 when installing",
                       self.copy_gtk4)

        self._divider(content)
        self._section(content, "GNOME Shell")

        tk.Label(
            content,
            text="The 'User Themes' extension is required to apply custom shell themes.",
            font=("Segoe UI", 9), bg=_BG, fg=_TEXT2, wraplength=560, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        tk.Button(
            content,
            text="Enable User Themes Extension",
            command=self._enable_user_themes,
            bg="#1a73e8", fg="white",
            activebackground="#1557b0", activeforeground="white",
            relief="flat", padx=14, pady=6,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2", bd=0,
        ).pack(anchor="w")

        self._divider(content)
        self._section(content, "Active Theme Quick-Switch")
        self._build_quick_switch(content)

        self._divider(content)
        self._section(content, "Environment Information")
        self._build_env_info(content)

        self._divider(content)
        self._section(content, "Logs")

        tk.Label(
            content,
            text=f"Log file: {LOG_FILE}",
            font=("Segoe UI", 9), bg=_BG, fg=_TEXT2,
        ).pack(anchor="w", pady=(0, 6))

        tk.Button(
            content,
            text="Open Log File",
            command=lambda: self._xdg_open(LOG_FILE),
            bg="#f8f9fa", fg=_TEXT,
            activebackground="#e8eaed",
            relief="flat", padx=12, pady=5,
            font=("Segoe UI", 9),
            cursor="hand2", bd=1,
        ).pack(anchor="w")

    # ── helper builders ───────────────────────────────────────────────────────

    @staticmethod
    def _section(parent: tk.Frame, title: str) -> None:
        tk.Label(
            parent, text=title,
            font=("Segoe UI", 11, "bold"),
            bg=_BG, fg=_TEXT,
        ).pack(anchor="w", pady=(0, 6))

    @staticmethod
    def _checkbox(parent: tk.Frame, label: str, variable: tk.BooleanVar) -> None:
        ttk.Checkbutton(parent, text=label, variable=variable).pack(
            anchor="w", padx=16, pady=2,
        )

    @staticmethod
    def _divider(parent: tk.Frame) -> None:
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=14)

    def _build_quick_switch(self, parent: tk.Frame) -> None:
        from ...switcher import get_current_themes

        grid = tk.Frame(parent, bg=_BG, padx=16)
        grid.pack(anchor="w", fill="x", pady=(0, 4))

        labels = [("GTK theme", "gtk"), ("Icon theme", "icons"),
                  ("Cursor theme", "cursor"), ("Shell theme", "shell")]

        try:
            current = get_current_themes()
        except Exception:   # noqa: BLE001
            current = {}

        for row_idx, (lbl_text, key) in enumerate(labels):
            tk.Label(grid, text=lbl_text + ":", font=("Segoe UI", 9, "bold"),
                     bg=_BG, fg=_TEXT, width=14, anchor="w").grid(
                row=row_idx, column=0, sticky="w", pady=2)
            tk.Label(grid, text=current.get(key) or "(not set)",
                     font=("Segoe UI", 9), bg=_BG, fg=_TEXT2).grid(
                row=row_idx, column=1, sticky="w", padx=(8, 0))

        ttk.Button(grid, text="Refresh",
                   command=lambda: self._refresh_quick_switch(grid, labels)).grid(
            row=len(labels), column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _refresh_quick_switch(self, grid: tk.Frame, labels: list[tuple[str, str]]) -> None:
        from ...switcher import get_current_themes
        try:
            current = get_current_themes()
        except Exception:   # noqa: BLE001
            return
        for row_idx, (_lbl, key) in enumerate(labels):
            slave = grid.grid_slaves(row=row_idx, column=1)
            if slave:
                slave[0].configure(text=current.get(key) or "(not set)")

    def _build_env_info(self, parent: tk.Frame) -> None:
        env = self._app.env
        rows = [
            ("Desktop",          env.desktop),
            ("Distribution",     env.distro),
            ("Session",          "Wayland" if env.is_wayland else "X11"),
            ("Package manager",  env.package_manager),
            ("gsettings",        "✓ available" if env.has_gsettings else "✗ not found"),
            ("Flatpak",          "✓ available" if env.has_flatpak  else "✗ not found"),
            ("GNOME Tweaks",     "✓ available" if env.has_gnome_tweaks else "✗ not found"),
        ]

        grid = tk.Frame(parent, bg=_BG, padx=16)
        grid.pack(anchor="w")

        for row_idx, (key, value) in enumerate(rows):
            tk.Label(grid, text=key + ":", font=("Segoe UI", 9, "bold"),
                     bg=_BG, fg=_TEXT, width=18, anchor="w").grid(
                row=row_idx, column=0, sticky="w", pady=1)
            color = "#1e8e3e" if "✓" in value else ("#d93025" if "✗" in value else _TEXT2)
            tk.Label(grid, text=value, font=("Segoe UI", 9),
                     bg=_BG, fg=color).grid(
                row=row_idx, column=1, sticky="w", padx=(8, 0))

    # ── actions ───────────────────────────────────────────────────────────────

    def _enable_user_themes(self) -> None:
        self._app.set_status("Enabling User Themes extension…")
        self._app.worker.submit(
            self._do_enable_user_themes,
            on_done=lambda ok: self.after(0, self._on_extension_result, ok),
            on_error=lambda e: self.after(0, self._on_extension_error, e),
        )

    @staticmethod
    def _do_enable_user_themes() -> bool:
        from ...extensions import install_user_themes_extension
        return install_user_themes_extension()

    def _on_extension_result(self, ok: bool) -> None:
        if ok:
            self._app.set_status("User Themes extension enabled.")
            messagebox.showinfo("Done", "User Themes extension is now enabled.")
        else:
            self._app.set_status("Could not enable User Themes — see log.")
            messagebox.showwarning(
                "Manual Step Required",
                "Could not enable the extension automatically.\n\n"
                "Install it from:\n"
                "https://extensions.gnome.org/extension/19/user-themes/\n\n"
                "Then log out and back in.",
            )

    def _on_extension_error(self, exc: Exception) -> None:
        self._app.set_status(f"Extension error: {exc}")
        messagebox.showerror("Error", str(exc))

    @staticmethod
    def _xdg_open(path: str) -> None:
        try:
            subprocess.Popen(["xdg-open", path])  # noqa: S603,S607
        except OSError as e:
            messagebox.showerror("Cannot Open File", str(e))
