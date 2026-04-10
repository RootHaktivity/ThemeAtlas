"""
ThemeCard – a single-theme display widget used in the Available Themes tab.

Layout
------
  ┌─── card (white bg, subtle border) ─────────────────────────────────────┐
  │  [thumb]  Name                                       [KIND]             │
  │           Summary text                               [ Install ]        │
  │           by Author  ★ Score  ↓ Downloads                              │
  └────────────────────────────────────────────────────────────────────────┘
"""

import threading
import tkinter as tk
from typing import Callable, Optional

from ..api import ThemeRecord

# Colours
_BG_CARD = "#ffffff"
_BG_APP  = "#f0f2f5"
_BORDER  = "#e2e6ea"
_BORDER_HOVER = "#b9c8e8"
_TEXT    = "#202124"
_TEXT2   = "#5f6368"

_KIND_COLORS: dict[str, tuple[str, str]] = {
    "gtk":     ("#1a73e8", "#ffffff"),
    "icons":   ("#0f9d58", "#ffffff"),
    "shell":   ("#7b1fa2", "#ffffff"),
    "cursors": ("#e64a19", "#ffffff"),
}

_KIND_ICONS: dict[str, str] = {
    "gtk":     "🎨",
    "icons":   "🖼",
    "shell":   "🐚",
    "cursors": "🖱",
}


class ThemeCard(tk.Frame):
    """Card widget displaying a single ThemeRecord."""

    def __init__(
        self,
        parent: tk.Widget,
        record: ThemeRecord,
        on_install: Callable[["ThemeRecord", "ThemeCard"], None],
        on_preview: Optional[Callable[["ThemeRecord"], None]] = None,
        **kwargs,
    ) -> None:
        # Outer frame acts as a 1-pixel border
        super().__init__(parent, bg=_BORDER, **kwargs)
        self._record = record
        self._on_install = on_install
        self._on_preview = on_preview
        self._photo = None  # keep PIL image reference alive
        self._body = None

        # Inner card body (white)
        body = tk.Frame(self, bg=_BG_CARD, padx=12, pady=10)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        self._body = body

        self._build(body)
        self._bind_hover_state(body)

    # ── build ──────────────────────────────────────────────────────────────────

    def _build(self, body: tk.Frame) -> None:
        r = self._record

        # ── thumbnail / icon placeholder (left) ───────────────────────────────
        badge_bg, badge_fg = _KIND_COLORS.get(r.kind, ("#757575", "#ffffff"))
        icon_char = _KIND_ICONS.get(r.kind, "📦")

        thumb_frame = tk.Frame(body, bg=badge_bg, width=56, height=56)
        thumb_frame.pack_propagate(False)
        thumb_frame.pack(side="left", padx=(0, 12))

        self._thumb_label = tk.Label(
            thumb_frame, text=icon_char, font=("Cantarell", 22),
            bg=badge_bg, fg=badge_fg,
        )
        self._thumb_label.place(relx=0.5, rely=0.5, anchor="center")

        # Always create a local placeholder thumbnail first.
        self._set_local_placeholder_thumbnail(badge_bg)

        # Kick off async thumbnail loading if a URL is available
        if r.thumbnail_url:
            threading.Thread(
                target=self._fetch_thumbnail,
                args=(r.thumbnail_url,),
                daemon=True,
            ).start()

        # ── centre column (name, summary, meta) ───────────────────────────────
        centre = tk.Frame(body, bg=_BG_CARD)
        centre.pack(side="left", fill="both", expand=True)

        # Name row ---
        name_row = tk.Frame(centre, bg=_BG_CARD)
        name_row.pack(fill="x", anchor="w")

        tk.Label(
            name_row, text=r.name,
            font=("Cantarell", 11, "bold"),
            bg=_BG_CARD, fg=_TEXT,
        ).pack(side="left")

        tk.Label(
            name_row,
            text=f"  {r.kind.upper()}  ",
            font=("Cantarell", 7, "bold"),
            bg=badge_bg, fg=badge_fg,
            padx=4, pady=2,
        ).pack(side="left", padx=(8, 0))

        source_label = r.source.replace("-", " ").title() if r.source else "Unknown"
        tk.Label(
            name_row,
            text=f" {source_label} ",
            font=("Cantarell", 7, "bold"),
            bg="#eef3fb", fg="#49628e",
            padx=4, pady=2,
        ).pack(side="left", padx=(6, 0))

        # Summary ---
        tk.Label(
            centre, text=r.summary,
            font=("Cantarell", 9),
            bg=_BG_CARD, fg=_TEXT,
            wraplength=380, justify="left",
        ).pack(anchor="w", pady=(2, 0))

        # Meta row ---
        score_str = f"★ {r.score:.0f}" if r.score else ""
        dl_str    = f"↓ {r.downloads:,}" if r.downloads else ""
        meta_parts = [p for p in [f"by {r.author}", score_str, dl_str] if p]
        tk.Label(
            centre, text="   ".join(meta_parts),
            font=("Cantarell", 8),
            bg=_BG_CARD, fg=_TEXT2,
        ).pack(anchor="w", pady=(2, 0))

        # ── right column (install button) ──────────────────────────────────────
        right = tk.Frame(body, bg=_BG_CARD)
        right.pack(side="right", padx=(12, 0))

        self._btn_text = tk.StringVar(value="Install")
        self._btn = tk.Button(
            right,
            textvariable=self._btn_text,
            command=self._handle_install,
            bg="#0f6dff", fg="white",
            activebackground="#0b57d0", activeforeground="white",
            relief="flat", padx=14, pady=6,
            font=("Cantarell", 9, "bold"),
            cursor="hand2",
            bd=0,
        )
        self._btn.pack()

        if self._on_preview is not None:
            tk.Button(
                right,
                text="Preview",
                command=self._handle_preview,
                bg=_BG_CARD, fg="#1a73e8",
                activebackground="#e8f0fe", activeforeground="#1a73e8",
                relief="flat", padx=6, pady=2,
                font=("Cantarell", 8),
                cursor="hand2", bd=0,
            ).pack(pady=(4, 0))

    def _bind_hover_state(self, body: tk.Frame) -> None:
        def _enter(_event=None):
            self.configure(bg=_BORDER_HOVER)
            if self._body is not None:
                self._body.configure(bg="#fbfdff")

        def _leave(_event=None):
            self.configure(bg=_BORDER)
            if self._body is not None:
                self._body.configure(bg=_BG_CARD)

        self.bind("<Enter>", _enter)
        self.bind("<Leave>", _leave)
        for widget in self.winfo_children():
            widget.bind("<Enter>", _enter)
            widget.bind("<Leave>", _leave)

    # ── button state helpers ───────────────────────────────────────────────────

    def _handle_install(self) -> None:
        self._btn.configure(state="disabled", bg="#9aa0a6")
        self._btn_text.set("Installing…")
        self._on_install(self._record, self)

    def _handle_preview(self) -> None:
        if self._on_preview is not None:
            self._on_preview(self._record)

    def mark_installed(self) -> None:
        self._btn_text.set("Installed ✓")
        self._btn.configure(state="disabled", bg="#1e8e3e")

    def mark_error(self, label: str = "Retry") -> None:
        self._btn_text.set(label)
        self._btn.configure(state="normal", bg="#d93025", activebackground="#b31412")

    # ── thumbnail loading ──────────────────────────────────────────────────────

    def _set_local_placeholder_thumbnail(self, badge_bg: str) -> None:
        """Generate a local fallback thumbnail so cards always show an image."""
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk  # type: ignore[import]
        except ImportError:
            return

        initials = "".join(word[:1] for word in self._record.name.split()[:2]).upper() or "TH"
        img = Image.new("RGB", (56, 56), badge_bg)
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), initials, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (56 - tw) // 2
        y = (56 - th) // 2
        draw.text((x, y), initials, fill="white", font=font)
        self._set_thumbnail(ImageTk.PhotoImage(img))

    def _fetch_thumbnail(self, url: str) -> None:
        """Download and set thumbnail in a daemon thread; silently skips on failure."""
        try:
            import io
            import urllib.request

            try:
                from PIL import Image, ImageTk  # type: ignore[import]
            except ImportError:
                return  # Pillow not installed – keep the placeholder

            with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
                data = resp.read()

            img = Image.open(io.BytesIO(data)).resize((56, 56), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)

            # Schedule UI update on the main thread
            try:
                self._thumb_label.after(0, self._set_thumbnail, photo)
            except RuntimeError:
                pass  # widget already destroyed
        except Exception:  # noqa: BLE001
            pass  # thumbnails are best-effort

    def _set_thumbnail(self, photo) -> None:
        self._photo = photo  # prevent garbage collection
        self._thumb_label.configure(image=photo, text="")
