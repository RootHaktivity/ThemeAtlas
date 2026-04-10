"""
Main application window – builds the Notebook, status bar, menu, and header.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..environment import detect_environment
from ..logger import get_logger
from .tabs.available import AvailableTab
from .tabs.installed import InstalledTab
from .tabs.settings import SettingsTab
from .worker import BackgroundWorker

log = get_logger(__name__)

_TITLE   = "Linux Theme Manager"
_WIN_W   = 960
_WIN_H   = 700
_MIN_W   = 720
_MIN_H   = 500

# Palette
_BG      = "#eef2f7"
_SURFACE = "#ffffff"
_PRIMARY = "#0f6dff"
_ACCENT  = "#11b89d"
_TEXT    = "#172033"
_TEXT2   = "#6b7385"
_BORDER  = "#d7dde8"

_FONT_UI = "Cantarell"


class ThemeManagerApp(tk.Tk):
    """Root Tkinter window for Linux Theme Manager."""

    def __init__(self) -> None:
        super().__init__()
        self.title(_TITLE)
        self.geometry(f"{_WIN_W}x{_WIN_H}")
        self.minsize(_MIN_W, _MIN_H)
        self.configure(bg=_BG)

        self.env    = detect_environment()
        self.worker = BackgroundWorker(num_threads=2)

        self._apply_style()
        self._build_menu()
        self._build_header()
        self._build_notebook()
        self._build_status_bar()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        log.info(
            "GUI started — desktop=%s, distro=%s, session=%s",
            self.env.desktop,
            self.env.distro,
            "Wayland" if self.env.is_wayland else "X11",
        )

    # ── style ──────────────────────────────────────────────────────────────────

    def _apply_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".",
            background=_BG,
            foreground=_TEXT,
            font=(_FONT_UI, 10),
        )
        style.configure("TFrame", background=_BG)
        style.configure("TLabel", background=_BG, foreground=_TEXT)
        style.configure("TButton", padding=(10, 5), font=(_FONT_UI, 10))
        style.configure("TCheckbutton", background=_BG)
        style.configure("TCombobox", fieldbackground=_SURFACE)

        style.configure(
            "TNotebook",
            background=_BG,
            borderwidth=0,
            tabmargins=[0, 0, 0, 0],
        )
        style.configure(
            "TNotebook.Tab",
            background="#dde5f1",
            foreground=_TEXT2,
            padding=(18, 10),
            font=(_FONT_UI, 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", _SURFACE)],
            foreground=[("selected", _PRIMARY)],
            font=[("selected", (_FONT_UI, 10, "bold"))],
        )

        style.configure(
            "Treeview",
            background=_SURFACE,
            fieldbackground=_SURFACE,
            rowheight=28,
        )
        style.configure(
            "Treeview.Heading",
            background="#f1f3f4",
            font=(_FONT_UI, 9, "bold"),
        )
        style.map("Treeview", background=[("selected", "#d2e3fc")])

    # ── menu bar ───────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        menubar = tk.Menu(self, bg=_SURFACE, fg=_TEXT, tearoff=False)
        self.configure(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=False, bg=_SURFACE, fg=_TEXT)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(
            label="Install from archive…",
            accelerator="Ctrl+O",
            command=self._install_from_file,
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        self.bind_all("<Control-o>", lambda _e: self._install_from_file())

        help_menu = tk.Menu(menubar, tearoff=False, bg=_SURFACE, fg=_TEXT)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About…", command=self._show_about)

    # ── header bar ─────────────────────────────────────────────────────────────

    def _build_header(self) -> None:
        header = tk.Frame(self, bg="#12203d", pady=14, padx=18)
        header.pack(fill="x")

        tk.Label(
            header, text="Linux Theme Manager",
            font=(_FONT_UI, 17, "bold"),
            bg="#12203d", fg="#f7fbff",
        ).pack(side="left", anchor="w")

        tk.Label(
            header,
            text="Curate, preview, and install desktop themes with confidence",
            font=(_FONT_UI, 10),
            bg="#12203d", fg="#a8b8d8",
        ).pack(side="left", padx=(14, 0), anchor="w")

        info = f"{self.env.desktop.upper()}  ·  {self.env.distro}"
        tk.Label(
            header, text=info,
            font=(_FONT_UI, 9, "bold"),
            bg=_ACCENT, fg="#ffffff",
            padx=10, pady=4,
        ).pack(side="right", padx=(0, 4), pady=2)

        # Thin separator below header
        tk.Frame(self, bg=_BORDER, height=1).pack(fill="x")

    # ── notebook ───────────────────────────────────────────────────────────────

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        self.available_tab = AvailableTab(self.notebook, self)
        self.installed_tab = InstalledTab(self.notebook, self)
        self.settings_tab  = SettingsTab(self.notebook, self)

        self.notebook.add(self.available_tab, text="   Available Themes   ")
        self.notebook.add(self.installed_tab, text="   Installed   ")
        self.notebook.add(self.settings_tab,  text="   Settings   ")

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

    # ── status bar ─────────────────────────────────────────────────────────────

    def _build_status_bar(self) -> None:
        tk.Frame(self, bg=_BORDER, height=1).pack(fill="x", side="bottom")
        self._status_var = tk.StringVar(value="Ready")
        tk.Label(
            self,
            textvariable=self._status_var,
            font=(_FONT_UI, 9),
            bg="#ffffff", fg=_TEXT2,
            anchor="w", padx=14, pady=4,
        ).pack(fill="x", side="bottom")

    # ── public helpers ─────────────────────────────────────────────────────────

    def set_status(self, message: str) -> None:
        """Update the status bar text (call from any thread via after())."""
        self._status_var.set(message)
        self.update_idletasks()

    # ── event handlers ─────────────────────────────────────────────────────────

    def _on_tab_change(self, _event: tk.Event) -> None:
        idx = self.notebook.index(self.notebook.select())
        if idx == 1:   # Installed tab
            self.installed_tab.refresh()

    def _on_close(self) -> None:
        self.worker.shutdown()
        self.destroy()

    # ── File menu actions ──────────────────────────────────────────────────────

    def _install_from_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Theme Archive",
            filetypes=[
                ("Theme archives", "*.zip *.tar.gz *.tgz *.tar.bz2 *.tar.xz"),
                ("All files",      "*.*"),
            ],
        )
        if not path:
            return

        self.set_status(f"Installing {path} …")

        def _do() -> list[str]:
            from ..installer import install_from_archive
            return install_from_archive(path)

        self.worker.submit(
            _do,
            on_done=lambda names: self.after(0, self._on_file_install_done, names),
            on_error=lambda e:    self.after(0, self._on_file_install_error, e),
        )

    def _on_file_install_done(self, names: list[str]) -> None:
        label = "\n• ".join(names) if names else "(unknown)"
        self.set_status(f"Installed: {', '.join(names)}")
        self.installed_tab.refresh()
        messagebox.showinfo("Installed", f"Successfully installed:\n• {label}")

    def _on_file_install_error(self, exc: Exception) -> None:
        self.set_status(f"Installation failed: {exc}")
        messagebox.showerror("Installation Failed", str(exc))

    # ── About ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _show_about() -> None:
        messagebox.showinfo(
            "About Linux Theme Manager",
            "Linux Theme Manager  v1.0.0\n\n"
            "Cross-distro installer and manager for GTK,\n"
            "icon, cursor, and GNOME Shell themes.\n\n"
            "Built with Python + Tkinter\n"
            "MIT License",
        )
