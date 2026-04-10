"""
Theme Preview Dialog.

Renders a photorealistic mock desktop scene using Pillow, adapting colours to
the theme name (dark/light detection) and type (GTK, icons, shell, cursors).
For installed GTK themes the actual CSS variables are parsed to derive the
exact palette.
"""

from __future__ import annotations

import re
import tkinter as tk
import urllib.request
import webbrowser
import base64
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

from .api import ThemeRecord
from ..logger import get_logger

log = get_logger(__name__)

# ── Palette constants ──────────────────────────────────────────────────────────
_BG      = "#f0f2f5"
_SURFACE = "#ffffff"
_TEXT    = "#202124"
_TEXT2   = "#5f6368"

_KIND_COLORS: dict[str, tuple[str, str]] = {
    "gtk":     ("#1a73e8", "#ffffff"),
    "icons":   ("#0f9d58", "#ffffff"),
    "shell":   ("#7b1fa2", "#ffffff"),
    "cursors": ("#e64a19", "#ffffff"),
}

# Font paths tried in order (first found wins)
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
]
_FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
]

_DARK_KEYWORDS = frozenset({
    "dark", "black", "night", "nord", "nord-dark", "dracula", "monokai",
    "dim", "deep", "sombre", "sombra", "carbon", "slate", "abyss",
    "catppuccin-mocha", "gruvbox",
})


def _pil_font(size: int, bold: bool = False):
    try:
        from PIL import ImageFont  # type: ignore[import]
    except ImportError:
        return None
    candidates = _FONT_BOLD_CANDIDATES if bold else _FONT_CANDIDATES
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _hex_ok(value: str, default: str) -> str:
    """Return *value* if it looks like a hex colour, else *default*."""
    if value and value.startswith("#") and len(value) in (4, 7):
        return value
    return default


def _parse_gtk_css_colors(theme_name: str) -> dict[str, str]:
    """
    Try to extract ``@define-color`` variables from an installed GTK theme.
    Returns an empty dict if theme is not installed or CSS is unreadable.
    """
    search_paths = [
        Path.home() / ".themes" / theme_name / "gtk-3.0" / "gtk.css",
        Path.home() / ".themes" / theme_name / "gtk-4.0" / "gtk.css",
        Path("/usr/share/themes") / theme_name / "gtk-3.0" / "gtk.css",
    ]
    pattern = re.compile(r"@define-color\s+(\w+)\s+(#[0-9a-fA-F]{3,6})\s*;")
    for css_path in search_paths:
        if not css_path.exists():
            continue
        try:
            text = css_path.read_text(errors="ignore")
            colors = {m.group(1): m.group(2) for m in pattern.finditer(text)}
            if colors:
                return colors
        except OSError:
            pass
    return {}


# ── Preview image generator ────────────────────────────────────────────────────

def generate_preview_image(
    record: ThemeRecord,
    width: int  = 440,
    height: int = 260,
):
    """
    Return a ``PIL.ImageTk.PhotoImage`` mock-desktop preview, or *None* if
    Pillow is not available.
    """
    try:
        from PIL import Image, ImageDraw  # type: ignore[import]
    except ImportError:
        return None

    badge_bg, _ = _KIND_COLORS.get(record.kind, ("#757575", "#ffffff"))

    # Detect dark/light from name + summary
    combined = (record.name + " " + record.summary).lower()
    is_dark = any(kw in combined for kw in _DARK_KEYWORDS)

    # Resolve palette (prefer CSS colours for installed GTK themes)
    css: dict[str, str] = {}
    if record.kind in ("gtk", "shell"):
        css = _parse_gtk_css_colors(record.name)

    win_bg       = _hex_ok(css.get("bg_color",          ""), "#3c3f41" if is_dark else "#f6f6f6")
    text_fg      = _hex_ok(css.get("fg_color",          ""), "#eeeeee" if is_dark else "#333333")
    accent       = _hex_ok(css.get("selected_bg_color", ""), badge_bg)
    panel_bg     = "#1e1e1e"   if is_dark else "#2e2e2e"
    desktop_bg   = "#282828"   if is_dark else "#d4d4d4"
    border_col   = "#555555"   if is_dark else "#cccccc"
    text2_fg     = "#9a9a9a"   if is_dark else "#777777"

    img  = Image.new("RGB", (width, height), desktop_bg)
    draw = ImageDraw.Draw(img)

    font_sm   = _pil_font(11)
    font_bold = _pil_font(12, bold=True) or font_sm

    # ── Top panel ──────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, width, 28], fill=panel_bg)
    if font_sm:
        draw.text((10, 8),           "Activities",          fill="#bbbbbb", font=font_sm)
        draw.text((width - 100, 8),  "Wed 09 Apr  9:41",    fill="#bbbbbb", font=font_sm)
    # Panel icons (dots)
    for ix in range(4):
        draw.rectangle([width // 2 - 24 + ix * 12, 10, width // 2 - 16 + ix * 12, 18],
                       fill="#888888")

    # ── Window shadow ─────────────────────────────────────────────────────────
    wx, wy   = 32, 42
    ww, wh   = width - 64, height - 56
    shadow_c = "#1a1a1a" if is_dark else "#aaaaaa"
    draw.rounded_rectangle([wx+4, wy+4, wx+ww+4, wy+wh+4], radius=6, fill=shadow_c)

    # ── Window body ───────────────────────────────────────────────────────────
    draw.rounded_rectangle([wx, wy, wx + ww, wy + wh], radius=6, fill=win_bg, outline=border_col)

    # ── Title bar ─────────────────────────────────────────────────────────────
    tb_h = 30
    draw.rounded_rectangle([wx, wy, wx + ww, wy + tb_h], radius=6, fill=accent)
    # Square off bottom of titlebar
    draw.rectangle([wx, wy + tb_h - 6, wx + ww, wy + tb_h], fill=accent)
    if font_bold:
        draw.text((wx + 56, wy + 8), record.name, fill="#ffffff", font=font_bold)

    # Window traffic-light controls (macOS style)
    for idx, ctrl_col in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        cx = wx + 14 + idx * 20
        draw.ellipse([cx - 7, wy + 8, cx + 7, wy + 22], fill=ctrl_col)

    content_y  = wy + tb_h + 8
    content_x  = wx + 8

    # ── Sidebar ───────────────────────────────────────────────────────────────
    if record.kind in ("gtk", "shell"):
        sb_w = 86
        sidebar_bg = _hex_ok(css.get("sidebar_bg_color", ""), "#3a3a3a" if is_dark else "#eaeaea")
        draw.rectangle([wx + 1, wy + tb_h, wx + sb_w, wy + wh - 1], fill=sidebar_bg)
        items = ["Files", "Music", "Photos", "Videos", "Trash"]
        for idx, label in enumerate(items):
            iy = content_y + idx * 22
            if idx == 0:
                draw.rounded_rectangle([wx + 2, iy - 2, wx + sb_w - 2, iy + 18],
                                       radius=4, fill=accent)
                if font_sm:
                    draw.text((wx + 10, iy + 2), label, fill="#ffffff", font=font_sm)
            else:
                if font_sm:
                    draw.text((wx + 10, iy + 2), label, fill=text2_fg, font=font_sm)
        content_x = wx + sb_w + 10

    available_w = (wx + ww) - content_x - 8

    # ── Icon grid (icons / cursors) ───────────────────────────────────────────
    if record.kind in ("icons", "cursors"):
        grid_colors = [accent, "#e57373", "#64b5f6", "#81c784",
                       "#ffb74d", "#ba68c8", "#4dd0e1", "#aed581"]
        grid_labels = ["Files", "Music", "Web", "Photos",
                       "Video", "Store", "Term", "Trash"]
        for idx in range(8):
            gx = content_x + (idx % 4) * 46
            gy = content_y + (idx // 4) * 48
            draw.rounded_rectangle(
                [gx, gy, gx + 34, gy + 34],
                radius=8, fill=grid_colors[idx % len(grid_colors)],
            )
            if font_sm:
                draw.text((gx + 2, gy + 37), grid_labels[idx], fill=text2_fg, font=font_sm)

    else:
        # ── Content area (GTK / shell mockup) ─────────────────────────────────
        if font_sm:
            draw.text((content_x, content_y),
                      "Application Window Content", fill=text_fg, font=font_sm)
            draw.text((content_x, content_y + 18),
                      "Secondary info – subtitle line", fill=text2_fg, font=font_sm)

        draw.line(
            [content_x, content_y + 36, content_x + available_w, content_y + 36],
            fill=border_col,
        )

        # Text input field
        tf_y = content_y + 46
        tf_w = min(available_w, 200)
        draw.rounded_rectangle(
            [content_x, tf_y, content_x + tf_w, tf_y + 22],
            radius=4, fill=win_bg, outline=accent, width=2,
        )
        if font_sm:
            draw.text((content_x + 7, tf_y + 5), "Type to search …",
                      fill=text2_fg, font=font_sm)

        # Buttons
        btn_y = content_y + 80
        draw.rounded_rectangle(
            [content_x, btn_y, content_x + 88, btn_y + 26],
            radius=4, fill=accent,
        )
        if font_sm:
            draw.text((content_x + 16, btn_y + 7), "Accept", fill="#ffffff", font=font_sm)
        draw.rounded_rectangle(
            [content_x + 98, btn_y, content_x + 186, btn_y + 26],
            radius=4, fill=win_bg, outline=border_col,
        )
        if font_sm:
            draw.text((content_x + 112, btn_y + 7), "Cancel", fill=text_fg, font=font_sm)

    # ── Watermark label ───────────────────────────────────────────────────────
    if font_sm:
        draw.text(
            (width - 4, height - 4),
            f"{record.kind.upper()} theme preview",
            fill=text2_fg, font=font_sm, anchor="rb",
        )

    return img   # caller converts to PhotoImage on the main thread


def load_source_preview_image(
    record: ThemeRecord,
    width: int = 440,
    height: int = 260,
):
    """
    Try to fetch a real screenshot from the source (thumbnail_url) and fit it
    into the preview frame with subtle letterboxing.

    Returns a PIL image or None if unavailable.
    """
    image_url = (record.thumbnail_url or "").strip()
    if not image_url and record.detail_url:
        image_url = _discover_preview_image_url(record.detail_url)
    if not image_url:
        return None

    if image_url.startswith("//"):
        image_url = "https:" + image_url

    try:
        from PIL import Image, ImageOps  # type: ignore[import]
    except ImportError:
        return None

    try:
        req = urllib.request.Request(
            image_url,
            headers={"User-Agent": "linux-theme-manager/1.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            data = resp.read()

        src = Image.open(BytesIO(data)).convert("RGB")
        fitted = ImageOps.contain(src, (width, height), method=Image.Resampling.LANCZOS)

        canvas = Image.new("RGB", (width, height), "#202124")
        ox = (width - fitted.width) // 2
        oy = (height - fitted.height) // 2
        canvas.paste(fitted, (ox, oy))
        return canvas
    except Exception as exc:  # noqa: BLE001
        log.debug("Could not load source preview image for '%s': %s", record.name, exc)
        return None


def _discover_preview_image_url(detail_url: str) -> str:
    """Best-effort extract of a screenshot URL from a theme detail web page."""
    try:
        req = urllib.request.Request(
            detail_url,
            headers={"User-Agent": "linux-theme-manager/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""

    patterns = [
        r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
        r'<meta\s+name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']twitter:image["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


# ── Preview dialog ─────────────────────────────────────────────────────────────

class PreviewDialog(tk.Toplevel):
    """
    Modal window that shows a generated mock-desktop preview and full
    theme metadata.  Optionally provides an Install action.
    """

    def __init__(
        self,
        parent: tk.Widget,
        record: ThemeRecord,
        on_install: Optional[Callable[[ThemeRecord], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.title(f"Preview – {record.name}")
        self.geometry("660x580")
        self.minsize(540, 460)
        self.configure(bg=_BG)
        self.transient(parent)
        self.resizable(True, True)

        self._record    = record
        self._on_install = on_install
        self._preview_photo = None  # keep PhotoImage alive

        self._build()

        # Centre on parent
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, px)}+{max(0, py)}")
        self.grab_set()

        # Generate preview on the Tk main thread to avoid PhotoImage thread issues.
        self.after(10, self._generate_preview)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        r = self._record
        badge_bg, _ = _KIND_COLORS.get(r.kind, ("#757575", "#ffffff"))

        # ── Coloured header ───────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=badge_bg, padx=16, pady=10)
        hdr.pack(fill="x")

        tk.Label(
            hdr, text=r.name,
            font=("Segoe UI", 15, "bold"),
            bg=badge_bg, fg="white",
        ).pack(side="left")

        meta_parts = [p for p in [
            f"★ {r.score:.0f}" if r.score  else "",
            f"↓ {r.downloads:,}" if r.downloads else "",
            f"by {r.author}"    if r.author else "",
        ] if p]
        tk.Label(
            hdr,
            text="   ".join(meta_parts),
            font=("Segoe UI", 9),
            bg=badge_bg, fg="white",
        ).pack(side="left", padx=(14, 0))

        tk.Label(
            hdr,
            text=f"  {r.kind.upper()}  ",
            font=("Segoe UI", 8, "bold"),
            bg="white", fg=badge_bg,
            padx=4, pady=2,
        ).pack(side="right")

        # ── Source badge ──────────────────────────────────────────────────────
        src_colors = {"gnome-look": "#e8f0fe", "github": "#f6f8fa", "mock": "#f1f3f4"}
        src_bg = src_colors.get(r.source, "#f1f3f4")
        tk.Label(
            self,
            text=f"  Source: {r.source}  ",
            font=("Segoe UI", 8),
            bg=src_bg, fg=_TEXT2,
            anchor="w", padx=6, pady=3,
        ).pack(fill="x")

        # ── Preview canvas ────────────────────────────────────────────────────
        canvas_wrap = tk.Frame(self, bg="#cccccc", padx=1, pady=1)
        canvas_wrap.pack(fill="x", padx=14, pady=(8, 0))

        self._canvas = tk.Canvas(
            canvas_wrap, width=440, height=260,
            bg="#cccccc", highlightthickness=0,
        )
        self._canvas.pack(anchor="center")
        self._loading_id = self._canvas.create_text(
            220, 130,
            text="Generating preview…",
            fill="#888888",
            font=("Segoe UI", 11),
        )
        self._mode_var = tk.StringVar(value="Preview mode: loading")
        tk.Label(
            self,
            textvariable=self._mode_var,
            font=("Segoe UI", 8),
            bg=_BG, fg=_TEXT2,
            anchor="w", padx=14,
        ).pack(fill="x", pady=(4, 0))

        # ── Description ───────────────────────────────────────────────────────
        desc_frame = tk.Frame(self, bg=_BG, padx=14, pady=8)
        desc_frame.pack(fill="both", expand=True)

        tk.Label(
            desc_frame,
            text=r.description or r.summary or "No description available.",
            font=("Segoe UI", 9),
            bg=_BG, fg=_TEXT2,
            wraplength=620, justify="left", anchor="nw",
        ).pack(fill="both", expand=True)

        footer_parts = [p for p in [
            f"Updated: {r.updated[:10]}" if r.updated else "",
            f"Source: {r.source}",
            f"Kind: {r.kind}",
        ] if p]
        tk.Label(
            desc_frame,
            text="   ·   ".join(footer_parts),
            font=("Segoe UI", 8),
            bg=_BG, fg=_TEXT2,
        ).pack(anchor="w", pady=(4, 0))

        # ── Action bar ────────────────────────────────────────────────────────
        tk.Frame(self, bg="#dadce0", height=1).pack(fill="x")
        bar = tk.Frame(self, bg=_SURFACE, padx=14, pady=10)
        bar.pack(fill="x")

        if self._on_install:
            tk.Button(
                bar, text=" Install ",
                command=self._do_install,
                bg="#1a73e8", fg="white",
                activebackground="#1557b0", activeforeground="white",
                relief="flat", padx=14, pady=6,
                font=("Segoe UI", 9, "bold"),
                cursor="hand2", bd=0,
            ).pack(side="left")

        if r.detail_url:
            tk.Button(
                bar, text="Open in Browser",
                command=lambda: webbrowser.open(r.detail_url),
                bg="#f8f9fa", fg=_TEXT,
                activebackground="#e8eaed",
                relief="flat", padx=14, pady=6,
                font=("Segoe UI", 9),
                cursor="hand2", bd=1,
            ).pack(side="left", padx=(8, 0))

        tk.Button(
            bar, text="Close",
            command=self.destroy,
            bg="#f8f9fa", fg=_TEXT,
            activebackground="#e8eaed",
            relief="flat", padx=14, pady=6,
            font=("Segoe UI", 9),
            cursor="hand2", bd=1,
        ).pack(side="right")

    # ── Preview generation ────────────────────────────────────────────────────

    def _generate_preview(self) -> None:
        try:
            # Prefer real source screenshot; fall back to generated mock preview.
            img = load_source_preview_image(self._record)
            if img is not None:
                self._mode_var.set("Preview mode: Source screenshot")
            else:
                img = generate_preview_image(self._record)
                if img is not None:
                    self._mode_var.set("Preview mode: Generated preview")
        except Exception as exc:  # noqa: BLE001
            log.exception("Preview generation failed for '%s'", self._record.name)
            self._set_preview_error(f"Preview failed: {exc}")
            return

        if img is None:
            self._set_preview_error("Preview unavailable: Pillow is required")
            return

        self._set_preview(img)

    def _set_preview(self, pil_img) -> None:
        try:
            from PIL import ImageTk  # type: ignore[import]
            photo = ImageTk.PhotoImage(pil_img)
        except ImportError:
            # Some distros split PIL.ImageTk into a separate package.
            # Fallback: encode PNG bytes and load via Tk PhotoImage directly.
            try:
                buf = BytesIO()
                pil_img.save(buf, format="PNG")
                png_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                photo = tk.PhotoImage(data=png_b64, format="png")
            except Exception as exc:  # noqa: BLE001
                self._set_preview_error(f"Preview unavailable: Image backend failed ({exc})")
                return
        self._preview_photo = photo  # prevent garbage collection
        self._canvas.delete(self._loading_id)
        self._canvas.configure(width=440, height=260)
        self._canvas.create_image(0, 0, anchor="nw", image=photo)

    def _set_preview_error(self, message: str) -> None:
        self._canvas.itemconfigure(self._loading_id, text=message, fill="#d93025")
        self._mode_var.set("Preview mode: Error")

    # ── Install action ────────────────────────────────────────────────────────

    def _do_install(self) -> None:
        if self._on_install:
            self._on_install(self._record)
        self.destroy()
