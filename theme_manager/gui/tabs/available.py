"""
Available Themes tab – search bar, kind filter, source selector, scrollable
theme cards with Install and Preview actions.
"""

import os
import tempfile
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import messagebox, ttk

from ...installer import install_from_archive
from ...logger import get_logger
from ..api import MOCK_THEMES, ThemeRecord
from ..preview import PreviewDialog
from ..sources import get_sources, search_source
from ..widgets.scrolled_frame import ScrolledFrame
from ..widgets.theme_card import ThemeCard

log = get_logger(__name__)

_BG    = "#f0f2f5"
_TEXT2 = "#5f6368"


class AvailableTab(ttk.Frame):
    """Tab that lets users search for and install themes from multiple sources."""

    def __init__(self, parent: tk.Widget, app) -> None:
        super().__init__(parent)
        self._app = app
        self._build()
        self.after(200, self._load_default)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        bar = tk.Frame(self, bg="#ffffff", pady=12, padx=14, highlightthickness=1, highlightbackground="#d7dde8")
        bar.pack(fill="x")

        self._search_var = tk.StringVar()
        self._entry = tk.Entry(
            bar, textvariable=self._search_var,
            font=("Cantarell", 11),
            relief="solid", bd=1,
            fg="#9aa0a6",
            bg="#f7f9fc",
            highlightthickness=1,
            highlightbackground="#dbe4f2",
        )
        self._entry.insert(0, "Search themes…")
        self._entry.bind("<FocusIn>",  self._clear_placeholder)
        self._entry.bind("<FocusOut>", self._restore_placeholder)
        self._entry.bind("<Return>",   lambda _e: self._do_search())
        self._entry.pack(side="left", fill="x", expand=True, ipady=6)
        self._placeholder_active = True

        self._kind_var = tk.StringVar(value="all")
        ttk.Combobox(
            bar, textvariable=self._kind_var,
            values=["all", "gtk", "icons", "shell", "cursors"],
            state="readonly", width=9,
            font=("Cantarell", 10),
        ).pack(side="left", padx=(8, 0), ipady=4)

        sources = get_sources()
        self._source_labels = ["All Sources"] + [s.label for s in sources]
        self._source_names  = ["all"]          + [s.name  for s in sources]
        self._source_label_var = tk.StringVar(value="All Sources")
        ttk.Combobox(
            bar, textvariable=self._source_label_var,
            values=self._source_labels,
            state="readonly", width=13,
            font=("Cantarell", 10),
        ).pack(side="left", padx=(8, 0), ipady=4)

        self._search_btn = tk.Button(
            bar, text="  Search  ",
            command=self._do_search,
            bg="#0f6dff", fg="white",
            activebackground="#0b57d0", activeforeground="white",
            relief="flat", padx=10, pady=5,
            font=("Cantarell", 10, "bold"),
            cursor="hand2", bd=0,
        )
        self._search_btn.pack(side="left", padx=(8, 0))
        self._search_btn.bind("<Enter>", lambda _e: self._search_btn.configure(bg="#0b57d0"))
        self._search_btn.bind("<Leave>", lambda _e: self._search_btn.configure(bg="#0f6dff"))

        self._status_lbl = tk.Label(
            self,
            text="Loading popular themes…",
            font=("Cantarell", 9), fg=_TEXT2, bg=_BG, anchor="w",
        )
        self._status_lbl.pack(fill="x", padx=14, pady=(0, 4))

        self._sf = ScrolledFrame(self, bg=_BG)
        self._sf.pack(fill="both", expand=True)

    # ── Placeholder helpers ───────────────────────────────────────────────────

    def _clear_placeholder(self, _event: tk.Event) -> None:
        if self._placeholder_active:
            self._entry.delete(0, tk.END)
            self._entry.configure(fg="#202124")
            self._placeholder_active = False

    def _restore_placeholder(self, _event: tk.Event) -> None:
        if not self._search_var.get():
            self._entry.insert(0, "Search themes…")
            self._entry.configure(fg="#9aa0a6")
            self._placeholder_active = True

    def _active_source_name(self) -> str:
        label = self._source_label_var.get()
        try:
            return self._source_names[self._source_labels.index(label)]
        except (ValueError, IndexError):
            return "all"

    # ── Search ────────────────────────────────────────────────────────────────

    def _do_search(self) -> None:
        query  = "" if self._placeholder_active else self._search_var.get().strip()
        kind   = self._kind_var.get()
        source = self._active_source_name()
        self._show_loading("Searching…")

        self._app.worker.submit(
            search_source,
            source, query, kind, 1,
            on_done=lambda r: self.after(0, self._render_results, r, query, source),
            on_error=lambda e: self.after(0, self._on_search_error, e),
        )

    def _load_default(self) -> None:
        self._show_loading("Loading popular themes from sources…")
        self._app.worker.submit(
            search_source,
            "github", "", "all", 1,
            on_done=lambda r: self.after(0, self._render_default_results, r),
            on_error=lambda e: self.after(0, self._on_search_error, e),
        )

    def _render_default_results(self, records: list[ThemeRecord]) -> None:
        if records:
            self._render_results(records, "", "github")
            self._status_lbl.configure(text=f"{len(records)} popular themes from GitHub")
            return
        self._render_results(MOCK_THEMES, "", "sample")
        self._status_lbl.configure(
            text="Popular themes (sample) — source list unavailable right now"
        )

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _show_loading(self, message: str = "Loading…") -> None:
        self._sf.clear()
        self._status_lbl.configure(text=message)
        tk.Label(
            self._sf.inner,
            text=message, font=("Segoe UI", 12),
            fg=_TEXT2, bg=_BG,
        ).pack(pady=60)

    def _render_results(
        self, records: list[ThemeRecord], query: str, source: str
    ) -> None:
        self._sf.clear()

        if not records:
            tk.Label(
                self._sf.inner,
                text="No themes found.",
                font=("Segoe UI", 12), fg=_TEXT2, bg=_BG,
            ).pack(pady=60)
            self._status_lbl.configure(text="No results.")
            return

        live = any(r.source in ("gnome-look", "github") for r in records)
        src_lbl = "sample data" if not live else source.replace("-", " ").title()
        q_part  = f' for "{query}"' if query else ""
        self._status_lbl.configure(
            text=f"{len(records)} themes from {src_lbl}{q_part}"
        )

        for record in records:
            card = ThemeCard(
                self._sf.inner, record,
                on_install=self._handle_install,
                on_preview=self._open_preview,
            )
            card.pack(fill="x", padx=8, pady=4)

        self._sf.scroll_to_top()

    # ── Preview ───────────────────────────────────────────────────────────────

    def _open_preview(self, record: ThemeRecord) -> None:
        PreviewDialog(
            self.winfo_toplevel(),
            record,
            on_install=lambda r: self._start_headless_install(r),
        )

    # ── Install ───────────────────────────────────────────────────────────────

    def _handle_install(self, record: ThemeRecord, card: ThemeCard) -> None:
        if not record.download_url:
            webbrowser.open(record.detail_url)
            card.mark_error("Install")
            messagebox.showinfo(
                "Open in Browser",
                f"'{record.name}' has been opened in your browser.\n\n"
                "Download the archive, then use:\n"
                "  File → Install from archive…",
            )
            return

        self._app.set_status(f"Downloading {record.name}…")
        self._app.worker.submit(
            self._download_and_install,
            record,
            on_done=lambda names: self.after(0, self._on_install_done, record, card, names),
            on_error=lambda e:    self.after(0, self._on_install_error, record, card, e),
        )

    def _start_headless_install(self, record: ThemeRecord) -> None:
        if not record.download_url:
            webbrowser.open(record.detail_url)
            return
        self._app.set_status(f"Downloading {record.name}…")
        self._app.worker.submit(
            self._download_and_install,
            record,
            on_done=lambda names: self.after(0, self._on_install_done, record, None, names),
            on_error=lambda e:    self.after(0, self._on_install_error, record, None, e),
        )

    @staticmethod
    def _download_and_install(record: ThemeRecord) -> list[str]:
        ext = record.download_url.rsplit(".", 1)[-1] if "." in record.download_url else "bin"
        fd, tmp_path = tempfile.mkstemp(suffix=f".{ext}")
        os.close(fd)
        try:
            req = urllib.request.Request(
                record.download_url,
                headers={"User-Agent": "linux-theme-manager/1.0"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                with open(tmp_path, "wb") as fh:
                    fh.write(resp.read())
            return install_from_archive(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_install_done(
        self, record: ThemeRecord, card, names: list[str]
    ) -> None:
        if card is not None:
            card.mark_installed()
        label = ", ".join(names) if names else record.name
        self._app.set_status(f"Installed: {label}")
        self._app.installed_tab.refresh()

    def _on_install_error(
        self, record: ThemeRecord, card, exc: Exception
    ) -> None:
        if card is not None:
            card.mark_error("Retry")
        msg = str(exc)
        self._app.set_status(f"Error installing {record.name}: {msg}")
        log.error("Installation failed for '%s': %s", record.name, msg)
        messagebox.showerror(
            "Installation Failed",
            f"Could not install '{record.name}':\n\n{msg}",
        )

    def _on_search_error(self, exc: Exception) -> None:
        self._sf.clear()
        self._status_lbl.configure(text="Search error – showing sample data")
        self._render_results(MOCK_THEMES, "", "sample")
        log.warning("Search error: %s", exc)
