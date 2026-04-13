"""
Installed Themes tab – lists all installed themes with Switch and Remove actions.
"""

import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ...logger import get_logger
from ...manager import list_installed_apps, list_themes, remove_theme, uninstall_app
from ...switcher import (
    switch_cursor_theme,
    switch_gtk_theme,
    switch_icon_theme,
    switch_shell_theme,
)

log = get_logger(__name__)

_BG = "#f0f2f5"


class InstalledTab(ttk.Frame):
    """Tab showing all themes installed in user and system directories."""

    def __init__(self, parent: tk.Widget, app) -> None:
        super().__init__(parent)
        self._app = app
        self._build()

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Toolbar
        toolbar = tk.Frame(self, bg=_BG, padx=14, pady=10)
        toolbar.pack(fill="x")

        tk.Label(
            toolbar, text="Installed Themes",
            font=("Segoe UI", 13, "bold"),
            bg=_BG, fg="#202124",
        ).pack(side="left")

        tk.Button(
            toolbar, text="⟳  Refresh",
            command=self.refresh,
            bg="#f8f9fa", fg="#202124",
            activebackground="#e8eaed",
            relief="flat", padx=10, pady=4,
            font=("Segoe UI", 9),
            cursor="hand2", bd=1,
        ).pack(side="right")

        # Treeview + scrollbar
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=14, pady=(0, 4))

        cols = ("kind", "location")
        self._tree = ttk.Treeview(
            tree_frame, columns=cols,
            show="tree headings",
            selectmode="browse",
        )
        self._tree.heading("#0",       text="Theme Name",  anchor="w")
        self._tree.heading("kind",     text="Type",        anchor="w")
        self._tree.heading("location", text="Location",    anchor="w")

        self._tree.column("#0",       width=240, minwidth=140, stretch=True)
        self._tree.column("kind",     width=90,  minwidth=70,  stretch=False)
        self._tree.column("location", width=240, minwidth=120, stretch=True)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)

        vsb.pack(side="right", fill="y")
        self._tree.pack(fill="both", expand=True)

        # Double-click → switch
        self._tree.bind("<Double-1>", lambda _e: self._switch_selected())

        # Action bar (themes)
        action_bar = tk.Frame(self, bg=_BG, padx=14, pady=8)
        action_bar.pack(fill="x")

        tk.Button(
            action_bar, text="Switch Theme",
            command=self._switch_selected,
            bg="#1a73e8", fg="white",
            activebackground="#1557b0", activeforeground="white",
            relief="flat", padx=14, pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2", bd=0,
        ).pack(side="left")

        tk.Button(
            action_bar, text="Remove",
            command=self._remove_selected,
            bg="#d93025", fg="white",
            activebackground="#b31412", activeforeground="white",
            relief="flat", padx=14, pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2", bd=0,
        ).pack(side="left", padx=(8, 0))

        # ── Desktop Customization Apps section ────────────────────────────────
        sep = ttk.Separator(self, orient="horizontal")
        sep.pack(fill="x", padx=14, pady=(6, 2))

        tk.Label(
            self, text="Desktop Customization Apps",
            font=("Segoe UI", 11, "bold"),
            bg=_BG, fg="#202124",
            anchor="w",
        ).pack(fill="x", padx=14, pady=(4, 2))

        apps_frame = ttk.Frame(self)
        apps_frame.pack(fill="both", padx=14, pady=(0, 4))

        app_cols = ("binaries", "installed_at")
        self._apps_tree = ttk.Treeview(
            apps_frame, columns=app_cols,
            show="tree headings",
            selectmode="browse",
            height=5,
        )
        self._apps_tree.heading("#0",          text="App Name",    anchor="w")
        self._apps_tree.heading("binaries",    text="Binary",      anchor="w")
        self._apps_tree.heading("installed_at", text="Installed",  anchor="w")
        self._apps_tree.column("#0",           width=200, minwidth=120, stretch=True)
        self._apps_tree.column("binaries",     width=160, minwidth=80,  stretch=True)
        self._apps_tree.column("installed_at", width=110, minwidth=80,  stretch=False)

        apps_vsb = ttk.Scrollbar(apps_frame, orient="vertical", command=self._apps_tree.yview)
        self._apps_tree.configure(yscrollcommand=apps_vsb.set)
        apps_vsb.pack(side="right", fill="y")
        self._apps_tree.pack(fill="both", expand=True)

        apps_action_bar = tk.Frame(self, bg=_BG, padx=14, pady=6)
        apps_action_bar.pack(fill="x")

        tk.Button(
            apps_action_bar, text="Launch",
            command=self._launch_selected_app,
            bg="#1a73e8", fg="white",
            activebackground="#1557b0", activeforeground="white",
            relief="flat", padx=14, pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2", bd=0,
        ).pack(side="left")

        tk.Button(
            apps_action_bar, text="Uninstall",
            command=self._uninstall_selected_app,
            bg="#d93025", fg="white",
            activebackground="#b31412", activeforeground="white",
            relief="flat", padx=14, pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2", bd=0,
        ).pack(side="left", padx=(8, 0))

        self.refresh()

    # ── data ──────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        self._tree.delete(*self._tree.get_children())
        themes = list_themes(include_system=True)

        for category, names in themes.items():
            kind     = "icons" if "icons" in category else "gtk"
            location = self._category_to_path(category)
            cat_node = self._tree.insert(
                "", "end",
                text=f"  {category}",
                values=("", ""),
                open=bool(names),
                tags=("category",),
            )
            for name in names:
                self._tree.insert(
                    cat_node, "end",
                    text=name,
                    values=(kind, location),
                )

        self._tree.tag_configure("category", font=("Segoe UI", 9, "bold"))

        # Refresh apps tree
        self._apps_tree.delete(*self._apps_tree.get_children())
        for app_entry in list_installed_apps():
            name = app_entry.get("name", "Unknown")
            binaries = app_entry.get("binaries", [])
            bin_names = ", ".join(Path(b).name for b in binaries[:2])
            installed_at = (app_entry.get("installed_at") or "")[:10]
            self._apps_tree.insert("", "end", text=name, values=(bin_names, installed_at))

    @staticmethod
    def _category_to_path(category: str) -> str:
        system = "system" in category
        if "icons" in category:
            return "/usr/share/icons" if system else "~/.icons"
        return "/usr/share/themes" if system else "~/.themes"

    # ── actions ───────────────────────────────────────────────────────────────

    def _selected_name_and_kind(self) -> tuple[str, str] | tuple[None, None]:
        sel = self._tree.selection()
        if not sel:
            return None, None
        item = sel[0]
        if not self._tree.parent(item):
            return None, None   # category header selected
        name   = self._tree.item(item, "text")
        values = self._tree.item(item, "values")
        kind   = values[0] if values else "gtk"
        return name, kind

    def _switch_selected(self) -> None:
        name, kind = self._selected_name_and_kind()
        if not name:
            messagebox.showwarning("No Selection", "Select a theme to switch to.")
            return

        desktop = self._app.env.desktop
        if kind == "icons":
            ok = switch_icon_theme(name)
        elif kind == "cursors":
            ok = switch_cursor_theme(name)
        elif kind == "shell":
            ok = switch_shell_theme(name, desktop)
        else:
            ok = switch_gtk_theme(name)

        if ok:
            self._app.set_status(f"Switched {kind} theme → {name}")
        else:
            messagebox.showerror(
                "Switch Failed",
                f"Could not switch to '{name}'.\n"
                "Make sure gsettings is available and you are in a GNOME session.",
            )

    def _remove_selected(self) -> None:
        name, kind = self._selected_name_and_kind()
        if not name:
            messagebox.showwarning("No Selection", "Select a theme to remove.")
            return

        if not messagebox.askyesno(
            "Confirm Removal",
            f"Remove '{name}' ({kind} theme)?\n\nThis cannot be undone.",
        ):
            return

        if remove_theme(name, kind=kind):
            self._app.set_status(f"Removed: {name}")
            self.refresh()
        else:
            messagebox.showerror(
                "Remove Failed",
                f"Could not remove '{name}'.\n"
                "Check the log for details.",
            )

    def _launch_selected_app(self) -> None:
        sel = self._apps_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select an app to launch.")
            return
        name = self._apps_tree.item(sel[0], "text")
        apps = list_installed_apps()
        target = next((a for a in apps if a.get("name") == name), None)
        if not target:
            return
        for bin_path in target.get("binaries", []):
            p = Path(bin_path)
            if p.is_file() and "cli" not in p.name.lower():
                try:
                    subprocess.Popen([str(p)], start_new_session=True)
                    self._app.set_status(f"Launched {p.name}")
                    return
                except OSError as exc:
                    messagebox.showerror("Launch Failed", str(exc))
                    return
        messagebox.showwarning("No Launcher", f"No launchable binary found for '{name}'.")

    def _uninstall_selected_app(self) -> None:
        sel = self._apps_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select an app to uninstall.")
            return
        name = self._apps_tree.item(sel[0], "text")
        if not messagebox.askyesno(
            "Confirm Uninstall",
            f"Uninstall '{name}'?\n\nThis removes the binary and data files.",
        ):
            return
        ok, message = uninstall_app(name)
        self._app.set_status(message)
        if ok:
            self.refresh()
        else:
            messagebox.showerror("Uninstall Failed", message)
