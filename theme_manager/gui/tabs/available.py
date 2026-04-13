"""
Available Themes tab – search bar, kind filter, source selector, scrollable
theme cards with Install and Preview actions.
"""

import os
import tempfile
import tkinter as tk
import webbrowser
from urllib.parse import urlsplit
from tkinter import messagebox, ttk

from ...installer import install_from_archive, install_from_package
from ...logger import get_logger
from ...network import download_to_file, try_fetch_sha256_sidecar, verify_sha256
from ..api import MOCK_THEMES, ThemeRecord
from ..preview import PreviewDialog
from ..sources import get_sources, search_source, sort_records
from ..widgets.scrolled_frame import ScrolledFrame
from ..widgets.theme_card import ThemeCard

log = get_logger(__name__)

_BG    = "#f0f2f5"
_TEXT2 = "#5f6368"

_APP_TOOLING_CATEGORIES = [
    "all",
    "appearance",
    "icons & cursors",
    "shell & panel",
    "wallpaper",
    "settings",
    "utilities",
]

_APP_TOOLING_INSTALL_FILTERS = [
    "all",
    "package manager",
    "source build",
    "direct download",
]

_RANK_LABEL_TO_MODE = {
    "Relevance": "relevance",
    "Highest Rated": "highest-rated",
    "Popular": "popular",
    "Trending": "trending",
}


class AvailableTab(ttk.Frame):
    """Tab that lets users search for and install themes from multiple sources."""

    def __init__(self, parent: tk.Widget, app, *, fixed_kind: str = "", show_category_filter: bool = False) -> None:
        super().__init__(parent)
        self._app = app
        self._fixed_kind = fixed_kind.strip()
        self._show_category_filter = bool(show_category_filter)
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
        placeholder = "Search desktop customization tools…" if self._fixed_kind == "app/tooling" else "Search themes…"
        self._entry.insert(0, placeholder)
        self._entry.bind("<FocusIn>",  self._clear_placeholder)
        self._entry.bind("<FocusOut>", self._restore_placeholder)
        self._entry.bind("<Return>",   lambda _e: self._do_search())
        self._entry.pack(side="left", fill="x", expand=True, ipady=6)
        self._placeholder_active = True
        self._placeholder_text = placeholder

        self._kind_var = tk.StringVar(value="all")
        kind_combo = ttk.Combobox(
            bar, textvariable=self._kind_var,
            values=["all", "gtk", "icons", "shell", "cursors", "app/tooling"],
            state="readonly", width=9,
            font=("Cantarell", 10),
        )
        kind_combo.pack(side="left", padx=(8, 0), ipady=4)
        if self._fixed_kind:
            self._kind_var.set(self._fixed_kind)
            kind_combo.pack_forget()

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

        self._category_var = tk.StringVar(value="all")
        self._category_combo = None
        if self._show_category_filter:
            self._category_combo = ttk.Combobox(
                bar,
                textvariable=self._category_var,
                values=_APP_TOOLING_CATEGORIES,
                state="readonly",
                width=13,
                font=("Cantarell", 10),
            )
            self._category_combo.pack(side="left", padx=(8, 0), ipady=4)

        self._install_filter_var = tk.StringVar(value="all")
        self._install_filter_combo = None
        if self._fixed_kind == "app/tooling":
            self._install_filter_combo = ttk.Combobox(
                bar,
                textvariable=self._install_filter_var,
                values=_APP_TOOLING_INSTALL_FILTERS,
                state="readonly",
                width=15,
                font=("Cantarell", 10),
            )
            self._install_filter_combo.pack(side="left", padx=(8, 0), ipady=4)

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

        self._rank_label_var = tk.StringVar(value="Trending")
        ttk.Combobox(
            bar,
            textvariable=self._rank_label_var,
            values=list(_RANK_LABEL_TO_MODE.keys()),
            state="readonly",
            width=12,
            font=("Cantarell", 10),
        ).pack(side="left", padx=(8, 0), ipady=4)

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
            self._entry.insert(0, self._placeholder_text)
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
        kind   = self._fixed_kind or self._kind_var.get()
        source = self._active_source_name()
        self._show_loading("Searching…")

        self._app.worker.submit(
            search_source,
            source, query, kind, 1,
            on_done=lambda r: self.after(0, self._render_results, r, query, source),
            on_error=lambda e: self.after(0, self._on_search_error, e),
        )

    def _load_default(self) -> None:
        kind = self._fixed_kind or "all"
        self._show_loading("Loading desktop customization tools from sources…" if kind == "app/tooling" else "Loading popular themes from sources…")
        self._app.worker.submit(
            search_source,
            "github", "", kind, 1,
            on_done=lambda r: self.after(0, self._render_default_results, r),
            on_error=lambda e: self.after(0, self._on_search_error, e),
        )

    def _render_default_results(self, records: list[ThemeRecord]) -> None:
        if records:
            self._render_results(records, "", "github")
            noun = "desktop customization tools" if self._fixed_kind == "app/tooling" else "themes"
            self._status_lbl.configure(text=f"{len(records)} popular {noun} from GitHub")
            return
        self._render_results(MOCK_THEMES, "", "sample")
        self._status_lbl.configure(
            text=("Popular desktop customization tools (sample) — source list unavailable right now" if self._fixed_kind == "app/tooling" else "Popular themes (sample) — source list unavailable right now")
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

        shown_records = list(records)
        if self._show_category_filter:
            selected = self._category_var.get().strip().lower()
            if selected and selected != "all":
                shown_records = [r for r in shown_records if self._record_app_category(r) == selected]

        install_filter = self._install_filter_var.get().strip().lower()
        if install_filter == "package manager":
            shown_records = [r for r in shown_records if r.install_method == "package-manager"]
        elif install_filter == "source build":
            shown_records = [r for r in shown_records if r.install_method == "source"]
        elif install_filter == "direct download":
            shown_records = [r for r in shown_records if bool(r.download_url)]

        rank_mode = _RANK_LABEL_TO_MODE.get(self._rank_label_var.get(), "relevance")
        shown_records = sort_records(shown_records, rank_mode)

        if not shown_records:
            tk.Label(
                self._sf.inner,
                text=("No desktop customization tools found." if self._fixed_kind == "app/tooling" else "No themes found."),
                font=("Segoe UI", 12), fg=_TEXT2, bg=_BG,
            ).pack(pady=60)
            self._status_lbl.configure(text="No results.")
            return

        live = any(r.source in ("gnome-look", "github") for r in shown_records)
        src_lbl = "sample data" if not live else source.replace("-", " ").title()
        q_part  = f' for "{query}"' if query else ""
        noun = "desktop customization tools" if self._fixed_kind == "app/tooling" else "themes"
        self._status_lbl.configure(
            text=f"{len(shown_records)} {noun} from {src_lbl}{q_part}"
        )

        for record in shown_records:
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
        if record.install_method == "package-manager" or record.artifact_type == "package":
            self._app.set_status(f"Installing package {record.package_name or record.name}…")
            self._app.worker.submit(
                self._install_package_record,
                record,
                on_done=lambda names: self.after(0, self._on_install_done, record, card, names),
                on_error=lambda e: self.after(0, self._on_install_error, record, card, False, e),
            )
            return

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
            False,
            on_done=lambda names: self.after(0, self._on_install_done, record, card, names),
            on_error=lambda e: self.after(0, self._on_install_error, record, card, False, e),
        )

    def _start_headless_install(self, record: ThemeRecord) -> None:
        if record.install_method == "package-manager" or record.artifact_type == "package":
            self._app.set_status(f"Installing package {record.package_name or record.name}…")
            self._app.worker.submit(
                self._install_package_record,
                record,
                on_done=lambda names: self.after(0, self._on_install_done, record, None, names),
                on_error=lambda e: self.after(0, self._on_install_error, record, None, False, e),
            )
            return

        if not record.download_url:
            webbrowser.open(record.detail_url)
            return
        self._app.set_status(f"Downloading {record.name}…")
        self._app.worker.submit(
            self._download_and_install,
            record,
            False,
            on_done=lambda names: self.after(0, self._on_install_done, record, None, names),
            on_error=lambda e: self.after(0, self._on_install_error, record, None, False, e),
        )

    @staticmethod
    def _download_and_install(record: ThemeRecord, allow_source_build: bool = False) -> list[str]:
        parsed = urlsplit(record.download_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError(f"Unsupported download URL scheme: {record.download_url}")
        ext = record.download_url.rsplit(".", 1)[-1] if "." in record.download_url else "bin"
        fd, tmp_path = tempfile.mkstemp(suffix=f".{ext}")
        os.close(fd)
        try:
            download_to_file(record.download_url, tmp_path, timeout=90, max_bytes=300 * 1024 * 1024, retries=2)
            expected_sha = (record.checksum_sha256 or "").strip().lower() or try_fetch_sha256_sidecar(record.download_url)
            if expected_sha:
                ok, actual, _expected = verify_sha256(tmp_path, expected_sha)
                if not ok:
                    raise ValueError(
                        "Archive integrity verification failed. "
                        f"Expected sha256={expected_sha}, actual sha256={actual}."
                    )
            progress_log: list[str] = []
            names = install_from_archive(
                tmp_path,
                allow_source_build=allow_source_build,
                progress_callback=progress_log.append,
            )
            if not names:
                source_required = next(
                    (line for line in progress_log if "Source build required" in line),
                    "",
                )
                if source_required:
                    raise ValueError(source_required)

                # Check progress log for build failure messages (more lenient matching)
                error_keywords = (
                    "meson setup failed", "meson install failed",
                    "build failed", "install failed",
                    "configured failed", "autoconf",
                    "dart-sass", "sass",
                )
                source_build_failed = None
                for line in reversed(progress_log):
                    line_lower = line.lower()
                    if any(keyword in line_lower for keyword in error_keywords):
                        source_build_failed = line
                        break

                if source_build_failed:
                    raise ValueError(source_build_failed)

                raise ValueError(
                    "No installable theme directories were found in this archive. "
                    "The repository may contain source files rather than a packaged theme release."
                )
            return names
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _install_package_record(record: ThemeRecord) -> list[str]:
        package_name = (record.package_name or "").strip()
        if not package_name:
            raise ValueError("Missing package name for package-manager install")
        package_manager = (record.source or "").strip().lower()
        if package_manager not in {"apt", "pacman"}:
            raise ValueError(f"Unsupported package source for direct install: {package_manager or 'unknown'}")
        ok = install_from_package(package_name, package_manager)
        if not ok:
            raise RuntimeError(f"Package install failed: {package_name} ({package_manager})")
        return [record.name]

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_install_done(
        self, record: ThemeRecord, card, names: list[str]
    ) -> None:
        if card is not None:
            card.mark_installed()
        label = ", ".join(names) if names else record.name
        self._app.set_status(f"Installed: {label}")
        self._app.installed_tab.refresh()

    def _on_install_error(self, record: ThemeRecord, card, allow_source_build: bool, exc: Exception) -> None:
        msg = str(exc)
        source_hint = (
            "Source build required" in msg
            or "source files rather than a packaged theme release" in msg.lower()
            or "no installable theme directories" in msg.lower()
        )
        if (not allow_source_build) and source_hint:
            consent = messagebox.askyesno(
                "Source Build Required",
                f"'{record.name}' appears to be source-only and needs a local build step.\n\n"
                "This can run project build tools and may install build dependencies. "
                "Only continue if you trust this source.\n\n"
                "Proceed with source build?",
            )
            if consent:
                self._app.set_status(f"Retrying {record.name} with source build enabled…")
                self._app.worker.submit(
                    self._download_and_install,
                    record,
                    True,
                    on_done=lambda names: self.after(0, self._on_install_done, record, card, names),
                    on_error=lambda e: self.after(0, self._on_install_error, record, card, True, e),
                )
                return

        if card is not None:
            card.mark_error("Retry")
        self._app.set_status(f"Error installing {record.name}: {msg}")
        log.error("Installation failed for '%s': %s", record.name, msg)
        
        error_guidance = msg
        if "dart-sass" in msg.lower() or ("sass" in msg.lower() and "requires" in msg.lower()):
            error_guidance = (
                f"{msg}\n\n"
                "FIX: Install dart-sass using your package manager:\n"
                "  sudo apt install dart-sass  # Ubuntu/Debian\n"
                "  sudo dnf install dart-sass  # Fedora\n"
                "  sudo pacman -S dart-sass    # Arch\n\n"
                "Then try installing again."
            )
        elif "build failed" in msg.lower():
            error_guidance = (
                f"{msg}\n\n"
                "This project requires build tools that may be missing.\n"
                "Common solutions:\n"
                "1. Install build essentials: sudo apt install build-essential\n"
                "2. Install meson: sudo apt install meson\n"
                "3. Try a pre-built version from your package manager\n\n"
                "For specific help, search the project's GitHub issues."
            )
        
        messagebox.showerror(
            "Installation Failed",
            f"Could not install '{record.name}':\n\n{error_guidance}",
        )

    def _on_search_error(self, exc: Exception) -> None:
        self._sf.clear()
        self._status_lbl.configure(text="Search error – showing sample data")
        self._render_results(MOCK_THEMES, "", "sample")
        log.warning("Search error: %s", exc)

    @staticmethod
    def _record_app_category(record: ThemeRecord) -> str:
        category = (getattr(record, "category", "") or "").strip().lower()
        if category:
            return category
        text = f"{record.name} {record.summary} {record.description}".lower()
        if any(token in text for token in ("theme", "appearance", "style", "accent", "color", "palette", "adwaita", "kvantum")):
            return "appearance"
        if any(token in text for token in ("icon", "icons", "cursor", "cursors", "pointer")):
            return "icons & cursors"
        if any(token in text for token in ("shell", "panel", "dock", "launcher", "plasma", "kwin")):
            return "shell & panel"
        if any(token in text for token in ("wallpaper", "background", "slideshow")):
            return "wallpaper"
        if any(token in text for token in ("gnome", "kde", "xfce", "desktop", "settings", "tweak")):
            return "settings"
        if any(token in text for token in ("utility", "tool", "manager", "editor", "installer", "chooser", "picker", "customizer")):
            return "utilities"
        return "utilities"


class AppToolingTab(AvailableTab):
    def __init__(self, parent: tk.Widget, app) -> None:
        super().__init__(parent, app, fixed_kind="app/tooling", show_category_filter=True)
