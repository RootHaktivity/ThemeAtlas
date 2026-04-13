"""PySide6-based GUI for ThemeAtlas."""

from __future__ import annotations

import io
import html as html_lib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import traceback
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from typing import Callable, Optional

def _configure_qt_runtime() -> None:
    """Set conservative Qt runtime defaults before Qt modules are imported."""
    os.environ["QT_QPA_PLATFORMTHEME"] = os.environ.get("LTM_QT_PLATFORMTHEME", "xdgdesktopportal")
    os.environ["QT_IM_MODULE"] = os.environ.get("LTM_QT_IM_MODULE", "compose")
    os.environ["QT_STYLE_OVERRIDE"] = os.environ.get("LTM_QT_STYLE", "Fusion")

    # Use native Wayland on Wayland sessions unless user explicitly overrides.
    if "LTM_QT_PLATFORM" in os.environ:
        os.environ["QT_QPA_PLATFORM"] = os.environ["LTM_QT_PLATFORM"]
    elif os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        os.environ["QT_QPA_PLATFORM"] = "wayland"


_configure_qt_runtime()


from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..environment import detect_environment
from ..extensions import (
    enable_extension_with_reason,
    get_current_gnome_shell_major,
    is_extension_enabled,
    list_extensions,
    remove_extension,
)
from ..installer import install_from_archive, install_from_package, install_from_source_tree, preview_archive_changes
from ..logger import get_logger
from ..logger import LOG_DIR
from ..network import download_to_file, fetch_bytes, try_fetch_sha256_sidecar, verify_sha256
from ..manager import list_themes, remove_theme, list_installed_apps, uninstall_app
from ..switcher import (
    get_current_themes,
    switch_cursor_theme,
    switch_gtk_theme,
    switch_icon_theme,
    switch_shell_theme,
)
from ..gui.api import MOCK_THEMES, ThemeRecord
from ..gui.sources import (
    add_custom_github_source,
    get_sources,
    list_custom_sources,
    remove_custom_source,
    search_source,
    sort_records,
)
from .state import load_ui_state, save_ui_state

log = get_logger(__name__)

_PREVIEW_CANDIDATE_CACHE: dict[str, list[str]] = {}
_REMOTE_PIXMAP_CACHE: dict[tuple[str, int, int], QPixmap] = {}
_GENERATED_PREVIEW_CACHE: dict[tuple[str, int, int], QPixmap] = {}


def _should_prompt_source_build(error_message: str) -> bool:
    lowered = (error_message or "").lower()
    return (
        "source build required" in lowered
        or "source files rather than a packaged theme release" in lowered
    )


def _is_likely_github_source_only(record: ThemeRecord) -> bool:
    """
    Heuristic to detect if a record appears to be a GitHub source-only theme.
    Returns True if the record has GitHub repo info but no clear pre-built artifact.
    """
    if record.source != "github":
        return False
    # If download_url is missing or points to a GitHub repo page, likely source-only
    url = (record.download_url or "").strip().lower()
    has_download = url and not url.endswith("/") and ".github.com" not in url
    return not has_download


def _github_clone_url(record: ThemeRecord) -> str:
    """Best-effort extraction of a GitHub repo clone URL from record metadata."""
    candidates = [record.detail_url, record.download_url]
    for candidate in candidates:
        parsed = urlsplit((candidate or "").strip())
        host = parsed.netloc.lower()
        parts = [p for p in parsed.path.split("/") if p]
        if host == "github.com" and len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            if owner and repo:
                return f"https://github.com/{owner}/{repo}.git"
        if host == "codeload.github.com" and len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            if owner and repo:
                return f"https://github.com/{owner}/{repo}.git"
    return ""

APP_STYLE = """
QWidget {
    background: #121826;
    color: #e7ecff;
    font-family: Cantarell, 'Noto Sans', sans-serif;
    font-size: 10pt;
}
QMainWindow {
    background: #121826;
}
QFrame#Header {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1b2438,
        stop:0.55 #172235,
        stop:1 #1f2b44);
    border-bottom: 1px solid #293553;
}
QLabel#HeaderTitle {
    color: #f3f6ff;
    font-size: 18pt;
    font-weight: 700;
}
QLabel#HeaderSub {
    color: #9eabc7;
    font-size: 10pt;
}
QLabel#Chip {
    color: #f8fbff;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2f6fff,
        stop:1 #19b7d8);
    border-radius: 10px;
    padding: 6px 12px;
    font-weight: 700;
}
QTabWidget::pane {
    border: 0;
    background: #121826;
}
QTabBar::tab {
    background: #1d273b;
    color: #9fb0cd;
    padding: 8px 14px;
    margin-right: 6px;
    border-radius: 8px;
    font-weight: 700;
    border: 1px solid #2a3854;
}
QTabBar::tab:selected {
    background: #26395d;
    color: #eaf2ff;
    border: 1px solid #4d74bf;
}
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4b74ff,
        stop:1 #2ec4de);
    color: white;
    border: 0;
    border-radius: 8px;
    padding: 7px 12px;
    font-weight: 700;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #6287ff,
        stop:1 #46d0e7);
}
QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3a60d1,
        stop:1 #27a8c0);
    border: 1px solid #85b3ff;
}
QPushButton:disabled {
    background: #30384f;
    color: #8f9ab8;
}
QPushButton[variant='subtle'] {
    background: #1c2538;
    color: #d7def0;
    border: 1px solid #31405f;
}
QPushButton[variant='danger'] {
    background: #b72a52;
    color: #ffffff;
    border: 1px solid #ff9abb;
}
QPushButton:focus {
    border: 2px solid #9ec7ff;
}
QPushButton[variant='subtle']:pressed {
    background: #273148;
    border: 1px solid #506a98;
}
QLineEdit, QComboBox, QListWidget, QTextEdit {
    background: #171f30;
    border: 1px solid #2d3a56;
    border-radius: 8px;
    padding: 6px;
    selection-background-color: #3d6fd4;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
    border: 1px solid #5e8fe0;
}
QComboBox QAbstractItemView,
QListWidget {
    background: #171f30;
    border: 1px solid #2d3a56;
    color: #e7ecff;
}
QComboBox::drop-down {
    border: 0;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
}
QFrame#Card {
    background: #182133;
    border: 1px solid #2c3a57;
    border-radius: 10px;
}
QLabel#Muted {
    color: #97a8c7;
}
QLabel#KindBadge {
    background: #3e73da;
    color: white;
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 8pt;
    font-weight: 700;
}
QLabel#SourceBadge {
    background: #243247;
    color: #88d3ff;
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 8pt;
    font-weight: 700;
}
QLabel#ArtifactBadge {
    background: #2a3348;
    color: #b7c8ea;
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 8pt;
    font-weight: 700;
}
QLabel#StatusBar {
    background: #141b2b;
    color: #9aaccc;
    border-top: 1px solid #2a3652;
    padding: 6px 12px;
}
QFrame#HealthStrip {
    background: #191c2c;
    border: 1px solid #2a2f4a;
    border-radius: 8px;
}

QFrame#PanelCard {
    background: #172133;
    border: 1px solid #2f3e5d;
    border-radius: 12px;
}

QLabel#SectionTitle {
    color: #ecf3ff;
    font-size: 11pt;
    font-weight: 700;
}
"""


def _kind_color(kind: str) -> QColor:
    return {
        "gtk": QColor("#1a73e8"),
        "icons": QColor("#0f9d58"),
        "shell": QColor("#7b1fa2"),
        "cursors": QColor("#e64a19"),
        "app/tooling": QColor("#00838f"),
    }.get(kind, QColor("#65758b"))


def _themeatlas_icon() -> QIcon:
    """Best-effort icon loader for source and user-local installs."""
    candidates = [
        # Source checkout paths
        Path(__file__).resolve().parents[2] / "assets" / "icons" / "png" / "themeatlas-icon-256.png",
        Path(__file__).resolve().parents[2] / "assets" / "icons" / "themeatlas-icon.svg",
        # User-local desktop install path
        Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "themeatlas.png",
    ]
    for path in candidates:
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return QIcon()


def _themeatlas_pixmap(size: int = 28) -> QPixmap | None:
    """Return a scaled ThemeAtlas pixmap for in-app branding widgets."""
    icon = _themeatlas_icon()
    if icon.isNull():
        return None
    pix = icon.pixmap(size, size)
    if pix.isNull():
        return None
    return pix


def _pil_to_pixmap(img) -> QPixmap | None:
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qimg = QImage.fromData(buf.getvalue(), "PNG")
    if qimg.isNull():
        return None
    return QPixmap.fromImage(qimg)


def _normalize_preview_url(url: str, base_url: str = "") -> str:
    value = html_lib.unescape((url or "").strip())
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/") and base_url:
        return urljoin(base_url, value)
    parsed = urlsplit(value)
    if parsed.scheme in {"https", "http"} and parsed.netloc:
        return value
    return ""


def _extract_image_candidates(html: str, base_url: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        r'<img[^>]+src=["\']([^"\']+)["\']',
        r'<img[^>]+data-src=["\']([^"\']+)["\']',
        r'<img[^>]+srcset=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            raw = (match.group(1) or "").strip()
            if not raw:
                continue
            first = raw.split(",", 1)[0].strip().split(" ", 1)[0].strip()
            url = _normalize_preview_url(first, base_url)
            if not url:
                continue
            low = url.lower()
            if any(ext in low for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", "images.pling.com", "opengraph.githubassets.com")):
                candidates.append(url)

    seen: set[str] = set()
    deduped: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _discover_preview_candidates(record: ThemeRecord) -> list[str]:
    detail_url = (record.detail_url or "").strip()
    cache_key = f"{record.id}|{detail_url}|{record.thumbnail_url}"
    cached = _PREVIEW_CANDIDATE_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)

    candidates: list[str] = []
    thumb = _normalize_preview_url(record.thumbnail_url or "", detail_url)
    if thumb:
        candidates.append(thumb)

    if detail_url:
        try:
            html_bytes = fetch_bytes(
                detail_url,
                extra_headers={"Accept": "text/html,application/xhtml+xml"},
                timeout=20,
                max_bytes=4 * 1024 * 1024,
                retries=1,
                cache_ttl_seconds=600,
            )
            html = html_bytes.decode("utf-8", errors="replace")
            candidates.extend(_extract_image_candidates(html, detail_url))
        except Exception:  # noqa: BLE001
            pass

    seen: set[str] = set()
    out: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    _PREVIEW_CANDIDATE_CACHE[cache_key] = list(out)
    return out


def _load_remote_pixmap(url: str, referer: str = "", width: int = 720, height: int = 380) -> QPixmap | None:
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return None
    cache_key = (url, width, height)
    cached = _REMOTE_PIXMAP_CACHE.get(cache_key)
    if cached is not None:
        return cached
    data = fetch_bytes(
        url,
        extra_headers={"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
        referer=referer,
        timeout=20,
        max_bytes=6 * 1024 * 1024,
        retries=1,
        cache_ttl_seconds=2 * 24 * 3600,
    )
    qimg = QImage.fromData(data)
    if qimg.isNull():
        return None
    pix = QPixmap.fromImage(qimg)
    scaled = pix.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    _REMOTE_PIXMAP_CACHE[cache_key] = scaled
    return scaled


def _load_source_pixmap(record: ThemeRecord, image_url: str = "", width: int = 720, height: int = 380) -> QPixmap | None:
    candidates = [image_url] if image_url else _discover_preview_candidates(record)
    detail_url = (record.detail_url or "").strip()
    for candidate in candidates:
        try:
            pix = _load_remote_pixmap(candidate, referer=detail_url, width=width, height=height)
            if pix is not None:
                return pix
        except Exception:  # noqa: BLE001
            continue
    return None


def _generate_preview_pixmap(record: ThemeRecord, width: int = 720, height: int = 380) -> QPixmap | None:
    cache_key = (record.id, width, height)
    cached = _GENERATED_PREVIEW_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        from PIL import Image, ImageDraw, ImageOps
    except Exception:
        return None

    dark = any(k in (record.name + " " + record.summary).lower() for k in ("dark", "nord", "dracula", "black"))
    bg = "#171927" if dark else "#2a2d43"
    panel = "#201537" if dark else "#31224d"
    card = "#232944" if dark else "#2f3554"
    text = "#eef0ff" if dark else "#f4f2ff"

    img = Image.new("RGB", (width, height), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, 38], fill=panel)

    accent = _kind_color(record.kind).name()
    d.rounded_rectangle([40, 58, width - 40, height - 40], radius=14, fill=card, outline="#4d4f73")
    d.rectangle([40, 58, width - 40, 98], fill=accent)
    d.text((60, 70), record.name[:58], fill="white")
    d.text((60, 116), "Theme preview (generated)", fill=text)
    d.text((60, 138), (record.summary or "No summary")[:95], fill=text)

    for i in range(6):
        x = 60 + i * 98
        d.rounded_rectangle([x, 188, x + 72, 248], radius=10, fill=ImageOps.colorize(Image.new("L", (1, 1), 110), black="#4f3a73", white=accent).getpixel((0, 0)))

    pil_pix = _pil_to_pixmap(img)
    if pil_pix is None:
        return None
    scaled = pil_pix.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    _GENERATED_PREVIEW_CACHE[cache_key] = scaled
    return scaled


def _install_method_label(record: ThemeRecord) -> str:
    if record.artifact_type == "package" or record.install_method == "package-manager":
        return "Distro package"
    if record.artifact_type == "extension":
        return "GNOME extension"
    if record.download_url:
        return "Direct archive"
    return "Manual source review"


def _supported_desktops(record: ThemeRecord) -> str:
    if record.kind == "shell" or record.artifact_type == "extension":
        return "GNOME"
    combined = f"{record.compatibility} {record.support_note} {record.description}".lower()
    found = [
        label
        for key, label in (
            ("gnome", "GNOME"),
            ("kde", "KDE Plasma"),
            ("xfce", "XFCE"),
            ("cinnamon", "Cinnamon"),
            ("mate", "MATE"),
            ("budgie", "Budgie"),
        )
        if key in combined
    ]
    if found:
        return ", ".join(dict.fromkeys(found))
    if record.kind == "gtk":
        return "GNOME, XFCE, Cinnamon, MATE, Budgie"
    if record.kind in {"icons", "cursors"}:
        return "GNOME, KDE Plasma, XFCE, Cinnamon, MATE"
    return "Most Linux desktops"


def _change_summary(record: ThemeRecord) -> str:
    target = {
        "gtk": "GTK application theme",
        "icons": "icon theme",
        "shell": "GNOME Shell theme",
        "cursors": "cursor theme",
        "app/tooling": "application or desktop tooling package",
    }.get(record.kind, "desktop theme assets")
    method = _install_method_label(record)
    variant = " Multiple variants available before install." if record.variants and len(record.variants) > 1 else ""
    return f"ThemeAtlas will install and register the {target} using {method.lower()}.{variant}"


def _trust_score(record: ThemeRecord, screenshot_count: int) -> tuple[int, list[str]]:
    score = 35
    reasons: list[str] = []
    source_name = (record.source or "").lower()
    if source_name in {"gnome-look", "github", "apt", "pacman"}:
        score += 10
        reasons.append(f"trusted source: {source_name.replace('-', ' ')}")
    if screenshot_count:
        score += 15
        reasons.append(f"{screenshot_count} screenshot{'s' if screenshot_count != 1 else ''} found")
    if record.detail_url:
        score += 10
        reasons.append("source page available")
    if record.download_url:
        score += 15
        reasons.append("direct download available")
    if record.install_verified:
        score += 15
        reasons.append("install path verified")
    if record.supported:
        score += 10
        reasons.append("compatible with this system")
    if record.updated:
        score += 5
        reasons.append("last updated listed")
    return min(score, 100), reasons


def _apply_theme_value(kind: str, value: str, desktop: str) -> bool:
    if kind == "gtk":
        return switch_gtk_theme(value)
    if kind == "icons":
        return switch_icon_theme(value)
    if kind in {"cursor", "cursors"}:
        return switch_cursor_theme(value)
    if kind == "shell":
        return switch_shell_theme(value, desktop)
    return False


def _record_visual_mode(record: ThemeRecord) -> str:
    combined = f"{record.name} {record.summary} {record.description}".lower()
    if any(token in combined for token in ("dark", "night", "black", "nord", "dracula", "mocha", "dim")):
        return "dark"
    if any(token in combined for token in ("light", "latte", "day", "white", "bright")):
        return "light"
    return "all"


def _record_supports_desktop(record: ThemeRecord, desktop: str) -> bool:
    if desktop == "all":
        return True
    supported = _supported_desktops(record).lower()
    return desktop.lower() in supported


def _record_recently_updated(record: ThemeRecord, max_age_days: int = 540) -> bool:
    updated = (record.updated or "").strip()
    if not updated:
        return False
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return (datetime.now() - datetime.strptime(updated, fmt)).days <= max_age_days
        except ValueError:
            continue
    return False


def _record_matches_quality(record: ThemeRecord, quality: str) -> bool:
    combined = f"{record.name} {record.summary} {record.description}".lower()
    if quality == "all":
        return True
    if quality == "recent updates":
        return _record_recently_updated(record)
    if quality == "preview available":
        return bool(record.thumbnail_url or record.detail_url)
    if quality == "install ready":
        return bool(record.install_verified or record.download_url or record.install_method == "package-manager")
    if quality == "shell ready":
        return record.kind == "shell" or "shell" in combined or "gnome shell" in combined
    if quality == "gtk 3":
        return "gtk 3" in combined or "gtk3" in combined
    if quality == "gtk 4":
        return "gtk 4" in combined or "gtk4" in combined
    return True


_APP_TOOLING_CATEGORIES: list[str] = [
    "all",
    "appearance",
    "icons & cursors",
    "shell & panel",
    "wallpaper",
    "settings",
    "utilities",
]

_RANK_LABEL_TO_MODE: dict[str, str] = {
    "Relevance": "relevance",
    "Highest Rated": "highest-rated",
    "Popular": "popular",
    "Trending": "trending",
}


def _record_app_tooling_category(record: ThemeRecord) -> str:
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


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)


class UiDispatcher(QObject):
    dispatch = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.dispatch.connect(self._run)

    @staticmethod
    def _run(fn: Callable[[], None]) -> None:
        fn()


class Worker(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            if "progress_callback" in self._kwargs:
                self._kwargs["progress_callback"] = self.signals.progress.emit
            result = self._fn(*self._args, **self._kwargs)
            self.signals.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            log.error("Worker failed: %s", traceback.format_exc())
            self.signals.failed.emit(str(exc))


class InstallProgressDialog(QDialog):
    """Install output dialog with running log and dual progress bars."""

    def __init__(self, parent: QWidget, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 420)
        self.setWindowModality(Qt.WindowModal)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        prefs_tabs = QTabWidget()
        prefs_tabs.setDocumentMode(True)
        prefs_tabs.setObjectName("PreferencesSections")
        root.addWidget(prefs_tabs, 1)

        self.download_label = QLabel("Downloading:")
        self.download_label.setObjectName("Muted")
        root.addWidget(self.download_label)

        self.download_bar = QProgressBar()
        self.download_bar.setRange(0, 100)
        self.download_bar.setValue(0)
        root.addWidget(self.download_bar)

        self.total_label = QLabel("Total")
        self.total_label.setObjectName("Muted")
        root.addWidget(self.total_label)

        self.total_bar = QProgressBar()
        self.total_bar.setRange(0, 100)
        self.total_bar.setValue(0)
        root.addWidget(self.total_bar)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(220)
        root.addWidget(self.output, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setProperty("variant", "subtle")
        self.close_btn.style().unpolish(self.close_btn)
        self.close_btn.style().polish(self.close_btn)
        self.close_btn.clicked.connect(self.close)
        self.close_btn.setEnabled(False)
        row.addWidget(self.close_btn)
        root.addLayout(row)

    def append_line(self, message: str) -> None:
        self.output.append(message)

    def set_download_percent(self, value: int) -> None:
        self.download_bar.setValue(max(0, min(100, value)))

    def set_total_percent(self, value: int) -> None:
        self.total_bar.setValue(max(0, min(100, value)))

    def mark_done(self) -> None:
        self.set_download_percent(100)
        self.set_total_percent(100)
        self.close_btn.setEnabled(True)


class PreviewDialog(QDialog):
    def __init__(self, parent: QWidget, record: ThemeRecord, on_install: Callable[[ThemeRecord, Optional["ThemeCardWidget"]], None]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Preview - {record.name}")
        self.resize(980, 720)
        self._record = record
        self._on_install = on_install
        self._image_candidates = _discover_preview_candidates(record)
        self._active_image_index = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel(record.name)
        title.setFont(QFont("Cantarell", 16, QFont.Bold))
        title_row.addWidget(title)

        for label_text, object_name in (
            (record.kind.upper(), "KindBadge"),
            (((record.source or "unknown").replace("-", " ").title()), "SourceBadge"),
            ((_install_method_label(record)), "ArtifactBadge"),
        ):
            badge = QLabel(label_text)
            badge.setObjectName(object_name)
            title_row.addWidget(badge)
        title_row.addStretch(1)
        root.addLayout(title_row)

        trust_score, trust_reasons = _trust_score(record, len(self._image_candidates))
        subtitle = QLabel(
            "   |   ".join(
                part
                for part in (
                    f"By {record.author}" if record.author else "",
                    f"Updated {record.updated}" if record.updated else "",
                    f"Confidence {trust_score}/100",
                )
                if part
            )
        )
        subtitle.setObjectName("Muted")
        root.addWidget(subtitle)

        content = QHBoxLayout()
        content.setSpacing(12)

        preview_col = QVBoxLayout()

        self.mode_label = QLabel("Preview mode: loading")
        self.mode_label.setObjectName("Muted")
        preview_col.addWidget(self.mode_label)

        self.image_label = QLabel("Loading preview...")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumHeight(390)
        self.image_label.setStyleSheet(
            "background:#1b2033;"
            "border-radius:10px;"
            "border:1px solid #384061;"
            "color:#9aa3c6;"
        )
        preview_col.addWidget(self.image_label)

        shots = QHBoxLayout()
        self.prev_shot_btn = QPushButton("Previous Shot")
        self.prev_shot_btn.setProperty("variant", "subtle")
        self.prev_shot_btn.style().unpolish(self.prev_shot_btn)
        self.prev_shot_btn.style().polish(self.prev_shot_btn)
        self.prev_shot_btn.clicked.connect(lambda: self._step_screenshot(-1))
        shots.addWidget(self.prev_shot_btn)

        self.screenshot_label = QLabel("Searching screenshots...")
        self.screenshot_label.setObjectName("Muted")
        shots.addWidget(self.screenshot_label, 1)

        self.next_shot_btn = QPushButton("Next Shot")
        self.next_shot_btn.setProperty("variant", "subtle")
        self.next_shot_btn.style().unpolish(self.next_shot_btn)
        self.next_shot_btn.style().polish(self.next_shot_btn)
        self.next_shot_btn.clicked.connect(lambda: self._step_screenshot(1))
        shots.addWidget(self.next_shot_btn)
        preview_col.addLayout(shots)
        content.addLayout(preview_col, 3)

        side = QFrame()
        side.setObjectName("PanelCard")
        side.setMinimumWidth(290)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 12, 12, 12)
        side_layout.setSpacing(10)

        facts_title = QLabel("Theme details")
        facts_title.setObjectName("SectionTitle")
        side_layout.addWidget(facts_title)

        facts = QGridLayout()
        facts.setHorizontalSpacing(10)
        facts.setVerticalSpacing(8)
        detail_rows = [
            ("Supported desktops", _supported_desktops(record)),
            ("Last updated", record.updated or "Not listed"),
            ("Install type", _install_method_label(record)),
            ("What changes", _change_summary(record)),
            ("Compatibility", record.compatibility or "Universal"),
        ]
        for row, (label_text, value_text) in enumerate(detail_rows):
            key = QLabel(label_text)
            key.setObjectName("Muted")
            key.setAlignment(Qt.AlignTop)
            facts.addWidget(key, row, 0)

            value = QLabel(value_text)
            value.setWordWrap(True)
            facts.addWidget(value, row, 1)
        side_layout.addLayout(facts)

        install_title = QLabel("Why this feels safe")
        install_title.setObjectName("SectionTitle")
        side_layout.addWidget(install_title)

        confidence = QLabel("\n".join(f"• {reason}" for reason in trust_reasons) or "• Source metadata available")
        confidence.setWordWrap(True)
        confidence.setStyleSheet(
            "background:#1a2339;"
            "border:1px solid #31405f;"
            "border-radius:8px;"
            "padding:10px;"
        )
        side_layout.addWidget(confidence)

        side_layout.addStretch(1)
        content.addWidget(side, 2)
        root.addLayout(content)

        desc = QTextEdit(record.description or record.summary or "No description available")
        desc.setReadOnly(True)
        desc.setMinimumHeight(120)
        root.addWidget(desc)

        btns = QHBoxLayout()
        install_btn = QPushButton("Install")
        install_btn.clicked.connect(self._install)
        btns.addWidget(install_btn)

        if record.detail_url:
            open_btn = QPushButton("Open in Browser")
            open_btn.setProperty("variant", "subtle")
            open_btn.style().unpolish(open_btn)
            open_btn.style().polish(open_btn)
            open_btn.clicked.connect(lambda: webbrowser.open(record.detail_url))
            btns.addWidget(open_btn)

        btns.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setProperty("variant", "subtle")
        close_btn.style().unpolish(close_btn)
        close_btn.style().polish(close_btn)
        close_btn.clicked.connect(self.close)
        btns.addWidget(close_btn)
        root.addLayout(btns)

        self._refresh_screenshot_controls()
        self._load_preview()

    def _load_preview(self) -> None:
        try:
            if self._image_candidates:
                total = len(self._image_candidates)
                start = self._active_image_index
                for offset in range(total):
                    idx = (start + offset) % total
                    pix = _load_source_pixmap(self._record, self._image_candidates[idx])
                    if pix is not None:
                        self._active_image_index = idx
                        self.mode_label.setText("Preview mode: Source screenshot")
                        self.image_label.setPixmap(pix)
                        self._refresh_screenshot_controls()
                        return

            pix = _generate_preview_pixmap(self._record)
            if pix is not None:
                self.mode_label.setText("Preview mode: Generated preview")
                self.image_label.setPixmap(pix)
                self._refresh_screenshot_controls(generated=True)
                return

            self.mode_label.setText("Preview mode: Unavailable")
            self.image_label.setText("Could not render preview")
            self._refresh_screenshot_controls(generated=True)
        except Exception as exc:  # noqa: BLE001
            self.mode_label.setText("Preview mode: Error")
            self.image_label.setText(f"Preview failed: {exc}")
            self._refresh_screenshot_controls(generated=True)

    def _refresh_screenshot_controls(self, generated: bool = False) -> None:
        total = len(self._image_candidates)
        has_multiple = total > 1
        self.prev_shot_btn.setEnabled(has_multiple)
        self.next_shot_btn.setEnabled(has_multiple)
        if generated:
            self.screenshot_label.setText("Generated preview only")
        elif total:
            self.screenshot_label.setText(f"Screenshot {self._active_image_index + 1} of {total}")
        else:
            self.screenshot_label.setText("No live screenshots found")

    def _step_screenshot(self, delta: int) -> None:
        if not self._image_candidates:
            return
        self._active_image_index = (self._active_image_index + delta) % len(self._image_candidates)
        self._load_preview()

    def _install(self) -> None:
        self._on_install(self._record, None)
        self.close()


class VariantSelectDialog(QDialog):
    """Dialog for selecting variant when a theme has multiple file options."""
    def __init__(self, parent: QWidget, record: ThemeRecord) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Select Theme Variant - {record.name}")
        self.resize(500, 400)
        self._selected_variant: tuple[str, str] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel(f"Available variants for {record.name}")
        title.setFont(QFont("Cantarell", 12, QFont.Bold))
        root.addWidget(title)

        info = QLabel("This theme has multiple variants available. Select the one you want to download and install:")
        info.setWordWrap(True)
        root.addWidget(info)

        self.list_widget = QListWidget()
        if record.variants:
            for variant_name, variant_url in record.variants:
                item = QListWidgetItem(variant_name)
                item.setData(Qt.UserRole, variant_url)
                self.list_widget.addItem(item)
            if self.list_widget.count() > 0:
                self.list_widget.setCurrentRow(0)
        root.addWidget(self.list_widget)

        btns = QHBoxLayout()
        ok_btn = QPushButton("Select")
        ok_btn.clicked.connect(self._on_select)
        btns.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("variant", "subtle")
        cancel_btn.style().unpolish(cancel_btn)
        cancel_btn.style().polish(cancel_btn)
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        root.addLayout(btns)

    def _on_select(self) -> None:
        if self.list_widget.count() > 0 and self.list_widget.currentItem():
            current_item = self.list_widget.currentItem()
            variant_name = current_item.text()
            variant_url = current_item.data(Qt.UserRole)
            self._selected_variant = (variant_name, variant_url)
            self.accept()

    def get_selected_variant(self) -> tuple[str, str] | None:
        """Return (variant_name, variant_url) or None if cancelled."""
        return self._selected_variant


class DesktopSetupDialog(QDialog):
    def __init__(self, parent: QWidget, choices: dict[str, list[str]], current: dict[str, Optional[str]]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Build Desktop Setup")
        self.resize(520, 360)
        self._combos: dict[str, QComboBox] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Create a full desktop setup")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        intro = QLabel(
            "Pick a GTK theme, icon theme, cursor theme, and GNOME Shell theme to apply together. "
            "Anything left on Keep current will be unchanged."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        kinds = [
            ("gtk", "GTK theme"),
            ("icons", "Icon theme"),
            ("cursor", "Cursor theme"),
            ("shell", "Shell theme"),
        ]
        for row, (key, label_text) in enumerate(kinds):
            label = QLabel(label_text)
            grid.addWidget(label, row, 0)
            combo = QComboBox()
            combo.addItem("Keep current", "")
            for item in sorted(choices.get(key, []), key=str.lower):
                combo.addItem(item, item)
            current_value = current.get(key) or ""
            if current_value:
                idx = combo.findData(current_value)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            self._combos[key] = combo
            grid.addWidget(combo, row, 1)
        root.addLayout(grid)

        btns = QHBoxLayout()
        apply_btn = QPushButton("Apply Setup")
        apply_btn.clicked.connect(self.accept)
        btns.addWidget(apply_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("variant", "subtle")
        cancel_btn.style().unpolish(cancel_btn)
        cancel_btn.style().polish(cancel_btn)
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        btns.addStretch(1)
        root.addLayout(btns)

    def selected_setup(self) -> dict[str, str]:
        return {
            key: str(combo.currentData() or "")
            for key, combo in self._combos.items()
            if combo.currentData()
        }


class WelcomeDialog(QDialog):
    def __init__(self, parent: QWidget, env) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to ThemeAtlas")
        self.resize(620, 440)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("Welcome to ThemeAtlas")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        intro = QLabel(
            f"ThemeAtlas detected {env.desktop.upper()} on {env.distro}. "
            "It can preview, install, and apply GTK, icon, cursor, and GNOME Shell themes from one place."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        info = QTextEdit()
        info.setReadOnly(True)
        info.setMinimumHeight(220)
        info.setPlainText(
            "What to expect on first run:\n\n"
            "• Themes tab: browse visual themes and inspect screenshots before installing.\n"
            "• Apps tab: discover desktop customization tools and utilities.\n"
            "• Extensions tab: browse GNOME Shell extensions with compatibility details.\n"
            "• Installed tab: manage what is already installed and restore a known-good setup.\n"
            "• Preferences tab: inspect diagnostics and manage source/policy settings.\n\n"
            "Helpful notes:\n"
            "• GNOME Shell themes need the User Themes extension.\n"
            "• Package records are filtered to your current distro/package manager.\n"
            "• Safer starter picks are usually icon themes and cursor themes, then GTK themes, then Shell themes.\n\n"
            "Recommended first search:\n"
            "• GTK: WhiteSur, Orchis, Materia\n"
            "• Icons: Papirus, Tela, WhiteSur Icons\n"
            "• Cursors: Bibata Modern\n"
        )
        root.addWidget(info)

        self.hide_next_time = QCheckBox("Don’t show this again")
        self.hide_next_time.setChecked(True)
        root.addWidget(self.hide_next_time)

        btns = QHBoxLayout()
        start_btn = QPushButton("Start Exploring")
        start_btn.clicked.connect(self.accept)
        btns.addWidget(start_btn)

        later_btn = QPushButton("Close")
        later_btn.setProperty("variant", "subtle")
        later_btn.style().unpolish(later_btn)
        later_btn.style().polish(later_btn)
        later_btn.clicked.connect(self.reject)
        btns.addWidget(later_btn)
        btns.addStretch(1)
        root.addLayout(btns)


class ThemeCardWidget(QFrame):
    def __init__(self, record: ThemeRecord, on_install: Callable[[ThemeRecord, Optional["ThemeCardWidget"]], None], on_preview: Callable[[ThemeRecord], None]) -> None:
        super().__init__()
        self.record = record
        self._on_install = on_install
        self._on_preview = on_preview
        self._summary_expanded = False
        self.setObjectName("Card")

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(12)

        self.thumb = QLabel()
        self.thumb.setFixedSize(56, 56)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(f"background:{_kind_color(record.kind).name()};border-radius:8px;color:white;")
        self.thumb.setText("".join(part[:1] for part in record.name.split()[:2]).upper() or "TH")
        root.addWidget(self.thumb)

        center = QVBoxLayout()
        top = QHBoxLayout()
        title = QLabel(record.name)
        title.setFont(QFont("Cantarell", 11, QFont.Bold))
        top.addWidget(title)

        kind = QLabel(record.kind.upper())
        kind.setObjectName("KindBadge")
        top.addWidget(kind)

        src = QLabel((record.source or "unknown").replace("-", " ").title())
        src.setObjectName("SourceBadge")
        top.addWidget(src)

        artifact = QLabel(record.artifact_type.replace("_", " ").title())
        artifact.setObjectName("ArtifactBadge")
        top.addWidget(artifact)

        if record.compatibility:
            compat = QLabel(record.compatibility)
            compat.setObjectName("SourceBadge")
            top.addWidget(compat)

        if record.install_verified:
            verified = QLabel("Verified")
            verified.setObjectName("ArtifactBadge")
            top.addWidget(verified)

        trust_score, trust_reasons = _trust_score(record, 1 if record.thumbnail_url or record.detail_url else 0)
        trust = QLabel(f"Trust {trust_score}")
        trust.setObjectName("ArtifactBadge")
        trust.setToolTip("\n".join(trust_reasons) or "ThemeAtlas confidence score")
        top.addWidget(trust)

        support_badge = QLabel("✔ Supported" if record.supported else "✖ Unsupported")
        support_badge.setStyleSheet(
            "background:#173f2b; color:#8af0ba; border-radius:6px; padding:2px 6px; font-size:8pt; font-weight:700;"
            if record.supported else
            "background:#3f1c2d; color:#ff97bf; border-radius:6px; padding:2px 6px; font-size:8pt; font-weight:700;"
        )
        if record.support_note:
            support_badge.setToolTip(record.support_note)
        top.addWidget(support_badge)
        top.addStretch(1)
        center.addLayout(top)

        self._summary_full = " ".join((record.summary or record.description or "No summary").split())
        self._summary_compact = self._summary_full
        if len(self._summary_compact) > 240:
            cut = 239
            boundary = self._summary_compact.rfind(" ", 0, cut)
            if boundary < 140:
                boundary = cut
            self._summary_compact = self._summary_compact[:boundary].rstrip(" ,.;:-") + "..."

        self.summary = QLabel(self._summary_compact)
        self.summary.setWordWrap(True)
        center.addWidget(self.summary)

        self.summary_toggle = QPushButton("Read more")
        self.summary_toggle.setProperty("variant", "subtle")
        self.summary_toggle.style().unpolish(self.summary_toggle)
        self.summary_toggle.style().polish(self.summary_toggle)
        self.summary_toggle.setVisible(self._summary_compact != self._summary_full)
        self.summary_toggle.clicked.connect(self._toggle_summary)
        center.addWidget(self.summary_toggle, alignment=Qt.AlignLeft)

        parts: list[str] = []
        if record.author:
            parts.append(f"by {record.author}")
        if record.score:
            parts.append(f"star {record.score:.0f}")
        if record.downloads:
            parts.append(f"downloads {record.downloads:,}")
        meta = QLabel("   |   ".join(parts) if parts else "")
        meta.setObjectName("Muted")
        center.addWidget(meta)
        root.addLayout(center, 1)

        right = QVBoxLayout()
        action_text = "Install"
        if record.artifact_type == "package":
            action_text = "Install Pkg"
        elif not record.download_url:
            action_text = "Open in Browser"
        elif record.artifact_type == "extension":
            action_text = "Install Ext"
        self.install_btn = QPushButton(action_text)
        self.install_btn.clicked.connect(lambda: self._on_install(self.record, self))
        right.addWidget(self.install_btn)

        prev_btn = QPushButton("Preview")
        prev_btn.setProperty("variant", "subtle")
        prev_btn.style().unpolish(prev_btn)
        prev_btn.style().polish(prev_btn)
        prev_btn.clicked.connect(lambda: self._on_preview(self.record))
        right.addWidget(prev_btn)
        right.addStretch(1)
        root.addLayout(right)

    def _toggle_summary(self) -> None:
        self._summary_expanded = not self._summary_expanded
        if self._summary_expanded:
            self.summary.setText(self._summary_full)
            self.summary_toggle.setText("Read less")
            return
        self.summary.setText(self._summary_compact)
        self.summary_toggle.setText("Read more")

    def mark_installing(self) -> None:
        self.install_btn.setText("Installing...")
        self.install_btn.setEnabled(False)

    def mark_installed(self) -> None:
        self.install_btn.setText("Installed")
        self.install_btn.setEnabled(False)

    def mark_error(self) -> None:
        self.install_btn.setText("Retry")
        self.install_btn.setEnabled(True)


class AvailableTab(QWidget):
    def __init__(
        self,
        app: "ThemeManagerQtApp",
        *,
        fixed_kind: str | None = None,
        show_category_filter: bool = False,
        artifact_filter: str | None = None,
    ) -> None:
        super().__init__()
        self.app = app
        self.fixed_kind = fixed_kind
        self.artifact_filter = artifact_filter
        self._app_mode = self.fixed_kind == "app/tooling"
        self._extension_mode = self.artifact_filter == "extension"
        self.show_category_filter = bool(show_category_filter)
        self.thread_pool = app.thread_pool
        self._workers: list[Worker] = []
        self._install_progress: Optional[InstallProgressDialog] = None
        self._last_results: list[ThemeRecord] = []
        self._last_query = ""
        self._last_source = "github"
        self._search_cache: dict[tuple[str, str, str], list[ThemeRecord]] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        bar_wrap = QFrame()
        bar_wrap.setStyleSheet(
            "QFrame {"
            "background:#1a1f32;"
            "border:1px solid #333a5a;"
            "border-radius:10px;"
            "}"
        )
        bar = QHBoxLayout(bar_wrap)
        bar.setContentsMargins(10, 10, 10, 10)
        bar.setSpacing(8)

        def _labeled_widget(label_text: str, control: QWidget) -> QWidget:
            host = QWidget()
            lay = QVBoxLayout(host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            lbl = QLabel(label_text)
            lbl.setObjectName("Muted")
            lbl.setStyleSheet("font-size:8pt;")
            lay.addWidget(lbl)
            lay.addWidget(control)
            return host

        def _search_placeholder() -> str:
            if self._extension_mode:
                return "Search GNOME Shell extensions..."
            if self._app_mode:
                return "Search desktop tools and apps..."
            return "Search themes..."

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(_search_placeholder())
        self.search_edit.returnPressed.connect(self.search)
        bar.addWidget(self.search_edit, 1)

        self.kind_combo = QComboBox()
        self.kind_combo.addItems(["all", "gtk", "icons", "shell", "cursors", "app/tooling"])
        if self.fixed_kind:
            self.kind_combo.setCurrentText(self.fixed_kind)
        else:
            kind_widget = _labeled_widget("Kind", self.kind_combo)
            bar.addWidget(kind_widget)

        self.source_combo = QComboBox()
        self.sources: list[str] = []
        self.reload_sources(preferred="github")
        bar.addWidget(_labeled_widget("Source", self.source_combo))

        self.appearance_combo: QComboBox | None = None
        self.desktop_combo: QComboBox | None = None
        self.quality_combo: QComboBox | None = None
        self.install_path_combo: QComboBox | None = None
        if self._app_mode:
            self.install_path_combo = QComboBox()
            self.install_path_combo.addItems(["all", "package manager", "source build", "direct download"])
            self.install_path_combo.currentIndexChanged.connect(lambda _i: self._rerender_last_results())
            bar.addWidget(_labeled_widget("Install Path", self.install_path_combo))
        else:
            self.appearance_combo = QComboBox()
            self.appearance_combo.addItems(["all", "dark", "light"])
            self.appearance_combo.currentIndexChanged.connect(lambda _i: self._rerender_last_results())
            bar.addWidget(_labeled_widget("Tone", self.appearance_combo))

            self.desktop_combo = QComboBox()
            self.desktop_combo.addItems(["all", "gnome", "kde plasma", "xfce", "cinnamon", "mate", "budgie"])
            self.desktop_combo.currentIndexChanged.connect(lambda _i: self._rerender_last_results())
            bar.addWidget(_labeled_widget("Desktop", self.desktop_combo))

            self.quality_combo = QComboBox()
            self.quality_combo.addItems(["all", "recent updates", "preview available", "install ready", "shell ready", "gtk 3", "gtk 4"])
            self.quality_combo.currentIndexChanged.connect(lambda _i: self._rerender_last_results())
            bar.addWidget(_labeled_widget("Quality", self.quality_combo))

        self.category_combo: QComboBox | None = None
        if self.show_category_filter:
            self.category_combo = QComboBox()
            self.category_combo.addItems(_APP_TOOLING_CATEGORIES)
            self.category_combo.currentIndexChanged.connect(lambda _i: self._rerender_last_results())
            bar.addWidget(_labeled_widget("Category", self.category_combo))

        self.rank_combo = QComboBox()
        self.rank_combo.addItems(list(_RANK_LABEL_TO_MODE.keys()))
        self.rank_combo.setCurrentText("Trending")
        self.rank_combo.currentIndexChanged.connect(lambda _i: self._rerender_last_results())
        bar.addWidget(_labeled_widget("Rank", self.rank_combo))

        search_btn = QPushButton("Search")
        search_btn.setShortcut("Ctrl+Return")
        search_btn.setToolTip("Run search (Ctrl+Enter)")
        search_btn.clicked.connect(self.search)
        bar.addWidget(search_btn)

        root.addWidget(bar_wrap)

        if self._extension_mode:
            initial_status = "Loading GNOME Shell extensions..."
        elif self._app_mode:
            initial_status = "Loading desktop tools and apps..."
        else:
            initial_status = "Loading popular themes..."
        self.status = QLabel(initial_status)
        self.status.setObjectName("Muted")
        root.addWidget(self.status)

        self._health_dots: dict[str, QLabel] = {}
        self._health_strip = self._build_health_strip()
        root.addWidget(self._health_strip)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        root.addWidget(self.scroll, 1)

        self.load_default()
        self._probe_health()

    def _clear_cards(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _show_loading_placeholders(self, message: str) -> None:
        self._clear_cards()
        self.status.setText(message)
        for _ in range(4):
            frame = QFrame()
            frame.setObjectName("Card")
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(12, 12, 12, 12)
            lay.setSpacing(8)

            if self._extension_mode:
                line1 = QLabel("Loading extension metadata...")
            elif self._app_mode:
                line1 = QLabel("Loading app/tool metadata...")
            else:
                line1 = QLabel("Loading theme metadata...")
            line1.setStyleSheet("background:#232944; border-radius:6px; padding:6px; color:#97a3c9;")
            lay.addWidget(line1)

            if self._extension_mode:
                line2 = QLabel("Fetching shell compatibility and installation metadata")
            elif self._app_mode:
                line2 = QLabel("Fetching source, compatibility, and install info")
            else:
                line2 = QLabel("Fetching screenshots, compatibility, and install info")
            line2.setObjectName("Muted")
            lay.addWidget(line2)
            self.list_layout.insertWidget(self.list_layout.count() - 1, frame)

    def load_default(self) -> None:
        default_kind = self.fixed_kind or "all"
        cache_key = ("github", "", default_kind)
        cached = self._search_cache.get(cache_key)
        if cached:
            if self._extension_mode:
                self.status.setText("Loaded popular extensions from cache")
            elif default_kind == "app/tooling":
                self.status.setText("Loaded popular apps and tools from cache")
            else:
                self.status.setText("Loaded popular themes from cache")
            self._render(cached, "", "github")
            return

        if self._extension_mode:
            loading_text = "Loading extensions from sources..."
        elif default_kind == "app/tooling":
            loading_text = "Loading apps and tools from sources..."
        else:
            loading_text = "Loading popular themes from sources..."
        self._show_loading_placeholders(loading_text)
        self._run_worker(
            search_source,
            "github", "", default_kind, 1,
            done=lambda recs: self._cache_and_render("github", "", default_kind, recs, "github"),
            failed=self._on_search_error,
        )

    def active_source(self) -> str:
        idx = self.source_combo.currentIndex()
        return self.sources[idx] if 0 <= idx < len(self.sources) else "all"

    def reload_sources(self, preferred: str | None = None) -> None:
        current = preferred or self.active_source()
        srcs = get_sources()
        self.sources = ["all"] + [s.name for s in srcs]
        labels = ["All Sources"] + [s.label for s in srcs]

        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItems(labels)
        try:
            idx = self.sources.index(current)
        except ValueError:
            idx = self.sources.index("github") if "github" in self.sources else 0
        self.source_combo.setCurrentIndex(idx)
        self.source_combo.blockSignals(False)
        if hasattr(self, "_health_strip"):
            self._refresh_health_dots()
            self._probe_health()

    def search(self) -> None:
        query = self.search_edit.text().strip()
        kind = self.fixed_kind or self.kind_combo.currentText()
        source = self.active_source()
        cache_key = (source, query, kind)
        cached = self._search_cache.get(cache_key)
        # Do not pin an empty default result in cache; retry network/source fetch.
        if cached and (query or source != "github"):
            self.status.setText("Showing cached search results")
            self._render(cached, query, source)
            return

        self._show_loading_placeholders("Searching...")
        self._run_worker(
            search_source,
            source, query, kind, 1,
            done=lambda recs: self._cache_and_render(source, query, kind, recs, source),
            failed=self._on_search_error,
        )

    def _cache_and_render(self, source: str, query: str, kind: str, records: list[ThemeRecord], render_source: str) -> None:
        if (
            self._app_mode
            and source == "github"
            and not query
            and not records
        ):
            self.status.setText("GitHub returned no app results. Trying all sources...")
            self._run_worker(
                search_source,
                "all",
                query,
                kind,
                1,
                done=lambda recs: self._cache_and_render("all", query, kind, recs, "all"),
                failed=self._on_search_error,
            )
            return

        self._search_cache[(source, query, kind)] = records
        self._render(records, query, render_source)

    # ── Source health strip ────────────────────────────────────────────────────

    _HEALTH_COLORS: dict[str, str] = {
        "online":       "#4ade80",
        "rate_limited": "#f59e0b",
        "offline":      "#f87171",
    }

    def _build_health_strip(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("HealthStrip")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(14)

        lbl = QLabel("Sources:")
        lbl.setObjectName("Muted")
        lbl.setStyleSheet("font-size:8pt;")
        lay.addWidget(lbl)

        for src in get_sources():
            dot = QLabel(f"● {src.label}")
            dot.setStyleSheet("color:#6b7493; font-size:8pt;")
            dot.setToolTip("Checking\u2026")
            lay.addWidget(dot)
            self._health_dots[src.name] = dot

        lay.addStretch(1)
        return frame

    def _refresh_health_dots(self) -> None:
        """Rebuild health dots after the source list changes."""
        lay = self._health_strip.layout()
        while lay.count() > 1:
            item = lay.takeAt(1)
            if item and item.widget():
                item.widget().deleteLater()

        self._health_dots = {}
        for src in get_sources():
            dot = QLabel(f"● {src.label}")
            dot.setStyleSheet("color:#6b7493; font-size:8pt;")
            dot.setToolTip("Checking\u2026")
            lay.insertWidget(lay.count(), dot)
            self._health_dots[src.name] = dot

        lay.addStretch(1)

    def _probe_health(self) -> None:
        """Start background health checks for every registered source."""
        for src in get_sources():
            name = src.name
            self._run_worker(
                src.health_check,
                done=lambda result, n=name: self._on_health_result(n, result[0], result[1]),
                failed=lambda err, n=name: self._on_health_result(n, "offline", str(err)[:80]),
            )

    def _on_health_result(self, source_name: str, status: str, message: str) -> None:
        dot = self._health_dots.get(source_name)
        if dot is None:
            return
        color = self._HEALTH_COLORS.get(status, "#6b7493")
        src_label = next((s.label for s in get_sources() if s.name == source_name), source_name)
        suffix = f" ({message})" if message and message != "Ready" else ""
        dot.setText(f"● {src_label}{suffix}")
        dot.setStyleSheet(f"color:{color}; font-size:8pt;")
        tip = status.replace("_", " ").title()
        if message:
            tip += f": {message}"
        dot.setToolTip(tip)

    def _render(self, records: list[ThemeRecord], query: str, source: str) -> None:
        self._clear_cards()
        if self.artifact_filter:
            records = [record for record in records if record.artifact_type == self.artifact_filter]
        if not records:
            if self._extension_mode:
                self.status.setText("No extensions found")
            elif self._app_mode:
                self.status.setText("No desktop tools found")
            else:
                self.status.setText("No themes found")
            self._last_results = []
            return

        compatible: list[ThemeRecord] = []
        for record in records:
            self._mark_support(record)
            compatible.append(record)

        self._last_results = compatible
        self._last_query = query
        self._last_source = source

        filtered = [record for record in compatible if self._matches_active_filters(record)]
        rank_mode = _RANK_LABEL_TO_MODE.get(self.rank_combo.currentText(), "relevance")
        filtered = sort_records(filtered, rank_mode)

        if not filtered:
            distro = (self.app.env.distro or "this distro").title()
            if self._extension_mode:
                self.status.setText(f"No extensions match the active filters for {distro}")
            elif self._app_mode:
                self.status.setText(f"No desktop tools match the active filters for {distro}")
            else:
                self.status.setText(f"No themes match the active filters for {distro}")
            return

        src_lbl = source.replace("-", " ").title()
        q = f' for "{query}"' if query else ""
        if self._extension_mode:
            noun = "extensions"
        elif self._app_mode:
            noun = "desktop tools"
        else:
            noun = "themes"
        self.status.setText(f"{len(filtered)} {noun} from {src_lbl}{q}")

        for record in filtered:
            card = ThemeCardWidget(record, self.install_record, self.open_preview)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)

    def _matches_active_filters(self, record: ThemeRecord) -> bool:
        if self.category_combo is not None and record.kind == "app/tooling":
            selected = self.category_combo.currentText().strip().lower()
            if selected and selected != "all" and _record_app_tooling_category(record) != selected:
                return False

        if self.install_path_combo is not None:
            selected_path = self.install_path_combo.currentText().strip().lower()
            if selected_path == "package manager" and record.install_method != "package-manager":
                return False
            if selected_path == "source build" and record.install_method != "source":
                return False
            if selected_path == "direct download" and not bool(record.download_url):
                return False

        if self._app_mode:
            return True

        appearance = self.appearance_combo.currentText() if self.appearance_combo is not None else "all"
        if appearance != "all" and _record_visual_mode(record) != appearance:
            return False

        desktop = self.desktop_combo.currentText() if self.desktop_combo is not None else "all"
        if desktop != "all" and not _record_supports_desktop(record, desktop):
            return False

        quality = self.quality_combo.currentText() if self.quality_combo is not None else "all"
        if not _record_matches_quality(record, quality):
            return False
        return True

    def _rerender_last_results(self) -> None:
        if self._last_results:
            self._render(self._last_results, self._last_query, self._last_source)

    def _mark_support(self, record: ThemeRecord) -> bool:
        """Mark support for current environment and return whether it should be shown."""
        env = self.app.env
        pm = (env.package_manager or "").lower()

        if not record.compatibility:
            if record.install_method == "package-manager" or record.artifact_type == "package":
                record.compatibility = (record.source or pm or "distro package").upper()
            else:
                record.compatibility = "Universal"

        # Package-manager records are distro-specific.
        if record.install_method == "package-manager" or record.artifact_type == "package":
            required_pm = (record.source or "").lower()
            supported = bool(required_pm) and required_pm == pm
            record.supported = supported
            record.support_note = (
                f"Supported on {pm}" if supported else f"Requires {required_pm}, current system uses {pm or 'unknown'}"
            )
            return True

        # GNOME extensions must match desktop and declared shell compatibility.
        if record.artifact_type == "extension" or record.kind == "shell":
            if env.desktop != "gnome":
                record.supported = False
                record.support_note = "Requires GNOME desktop session"
                return True

            # Example compatibility text: "GNOME Shell 46, 47"
            declared = [major for major in re.findall(r"\b(\d{2})\b", record.compatibility or "")]
            current_shell_major = get_current_gnome_shell_major()
            if declared and current_shell_major:
                if current_shell_major in declared:
                    record.supported = True
                    record.support_note = f"Supports GNOME Shell {', '.join(declared)}"
                else:
                    record.supported = False
                    record.support_note = (
                        f"Supports GNOME Shell {', '.join(declared)}; current shell is {current_shell_major}"
                    )
                return True

            if not declared:
                record.supported = False
                record.support_note = "GNOME Shell version compatibility is not declared"
                return True

            record.supported = True
            record.support_note = f"Declared for GNOME Shell {', '.join(declared)}"
            return True

        # Archive/manual/theme sources are generally distro-agnostic.
        record.supported = True
        record.support_note = f"Compatible with {env.distro or 'most distros'}"
        return True

    def open_preview(self, record: ThemeRecord) -> None:
        dlg = PreviewDialog(self, record, self.install_record)
        dlg.exec()

    def install_record(self, record: ThemeRecord, card: Optional[ThemeCardWidget] = None) -> None:
        # If theme has multiple variants, ask user to select one
        if record.variants and len(record.variants) > 1:
            variant_dlg = VariantSelectDialog(self, record)
            if variant_dlg.exec() == QDialog.Accepted:
                selected = variant_dlg.get_selected_variant()
                if selected and selected[1]:
                    # Create a modified record with the selected variant URL
                    variant_name, variant_url = selected
                    record = ThemeRecord(
                        id=record.id,
                        name=record.name,
                        summary=record.summary,
                        description=record.description,
                        kind=record.kind,
                        score=record.score,
                        downloads=record.downloads,
                        author=record.author,
                        thumbnail_url=record.thumbnail_url,
                        download_url=variant_url,
                        detail_url=record.detail_url,
                        updated=record.updated,
                        source=record.source,
                        artifact_type=record.artifact_type,
                        variants=record.variants,
                        checksum_sha256=record.checksum_sha256,
                        signature_url=record.signature_url,
                    )
                else:
                    return
            else:
                return
        
        if record.artifact_type == "package":
            if card is not None:
                card.mark_installing()
            self.app.mark_install_active(record.name, "package-install")
            self.app.record_recent_action("install-request", f"{record.name} ({record.source})")
            self.app.set_status(f"Installing package {record.package_name or record.name}...")
            self._show_install_progress(record)
            self._run_worker(
                self._install_package_record,
                record,
                done=lambda names: self._on_install_done(record, names, card),
                failed=lambda err: self._on_install_error(record, err, card),
                progress=self._update_install_progress,
            )
            return

        if not record.download_url:
            if record.detail_url:
                webbrowser.open(record.detail_url)
            self.app.set_status(f"No direct archive for {record.name}; opened source page")
            QMessageBox.information(
                self,
                "Open Source Page",
                f"{record.name} does not expose a direct archive download.\n\n"
                "The source page was opened so you can inspect or download it manually.",
            )
            return

        policy = self.app.install_policy()
        allow_scripts = bool(policy.get("allow_install_scripts", False))
        sandbox_scripts = bool(policy.get("sandbox_install_scripts", True))

        consent = QMessageBox.question(
            self,
            "Install Safety Options",
            "Install scripts are disabled by default for safety.\n\n"
            "Do you want to allow archive install scripts for this install if they are present?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if consent == QMessageBox.Yes:
            allow_scripts = True
            self.app.record_recent_action("install-policy", f"scripts enabled for {record.name}")

        # Proactive source-build consent for GitHub source-only themes
        allow_source_build = False
        if _is_likely_github_source_only(record):
            source_choice = QMessageBox.question(
                self,
                "Source Build Required",
                f"'{record.name}' appears to be source-only and will need a local build step.\n\n"
                "This may run project build tools (meson, cmake, autoconf, etc.) "
                "and install build dependencies. "
                "Only continue if you trust this source.\n\n"
                "Proceed with source build?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if source_choice != QMessageBox.Yes:
                self.app.set_status(f"Cancelled source build for {record.name}")
                return
            allow_source_build = True
            self.app.record_recent_action("install-policy", f"source build enabled for {record.name}")

        if card is not None:
            card.mark_installing()

        log.info("Install requested for '%s' from %s", record.name, record.download_url)
        self.app.mark_install_active(record.name, "downloading")
        self.app.record_recent_action("install-request", f"{record.name} ({record.source})")
        self.app.set_status(f"Downloading {record.name}...")
        self._show_install_progress(record)
        self._run_worker(
            self._download_and_install,
            record,
            allow_scripts,
            sandbox_scripts,
            allow_source_build,
            done=lambda names: self._on_install_done(record, names, card),
            failed=lambda err: self._on_install_error(
                record,
                err,
                card,
                allow_source_build=allow_source_build,
                allow_scripts=allow_scripts,
                sandbox_scripts=sandbox_scripts,
            ),
            progress=self._update_install_progress,
        )

    def _show_install_progress(self, record: ThemeRecord) -> None:
        if self._install_progress is not None:
            self._install_progress.close()
            self._install_progress.deleteLater()
        dialog = InstallProgressDialog(self, f"Install - {record.name}")
        dialog.append_line(f"starting package maintenance for {record.name}...")
        dialog.show()
        self._install_progress = dialog

    def _update_install_progress(self, message: str) -> None:
        self.app.set_status(message)
        log.info(message)
        if self._install_progress is not None:
            self._install_progress.append_line(message)
            lower = message.lower()
            if "download" in lower and self._install_progress.download_bar.value() < 95:
                self._install_progress.set_download_percent(self._install_progress.download_bar.value() + 8)
            if any(token in lower for token in ("extract", "install", "complete")):
                self._install_progress.set_total_percent(min(95, self._install_progress.total_bar.value() + 12))

    @staticmethod
    def _download_and_install(
        record: ThemeRecord,
        allow_scripts: bool,
        sandbox_scripts: bool,
        allow_source_build: bool = False,
        progress_callback=None,
    ) -> list[str]:
        parsed = urlsplit(record.download_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError(f"Unsupported download URL scheme: {record.download_url}")
        url_name = os.path.basename(unquote(parsed.path))
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", url_name).strip("._")
        if not safe_name:
            fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", record.name).strip("._") or "theme"
            safe_name = f"{fallback}.bin"

        tmp_dir = tempfile.mkdtemp(prefix="ltm_dl_")
        tmp_path = os.path.join(tmp_dir, safe_name)
        try:
            if progress_callback:
                progress_callback(f"Starting package maintenance for {record.name}...")
                progress_callback(f"Downloading archive from: {record.download_url}")

            total = download_to_file(
                record.download_url,
                tmp_path,
                timeout=90,
                max_bytes=300 * 1024 * 1024,
                retries=2,
            )
            if progress_callback:
                progress_callback(f"Downloaded {total / (1024 * 1024):.1f} MB. Verifying integrity...")

            expected_sha = (record.checksum_sha256 or "").strip().lower()
            if not expected_sha:
                expected_sha = try_fetch_sha256_sidecar(record.download_url)
                if expected_sha and progress_callback:
                    progress_callback("Found SHA256 sidecar; validating archive...")

            if expected_sha:
                ok, actual, _expected = verify_sha256(tmp_path, expected_sha)
                if not ok:
                    raise ValueError(
                        "Archive integrity verification failed. "
                        f"Expected sha256={expected_sha}, actual sha256={actual}."
                    )
                if progress_callback:
                    progress_callback(f"SHA256 verified: {actual[:12]}...")
            elif progress_callback:
                progress_callback("No SHA256 metadata found; proceeding with unsigned archive.")

            preview = preview_archive_changes(tmp_path)
            preview_ops = preview.get("operations", []) if isinstance(preview, dict) else []
            if progress_callback:
                progress_callback("Dry run preview (planned file/path changes):")
                for op in preview_ops[:30]:
                    progress_callback(
                        f"  - [{op.get('kind')}] {op.get('name')} -> {op.get('destination')}"
                    )
                if len(preview_ops) > 30:
                    progress_callback(f"  ... {len(preview_ops) - 30} more paths")

                script_roots = preview.get("script_roots", []) if isinstance(preview, dict) else []
                if script_roots:
                    progress_callback(f"Install scripts detected in {len(script_roots)} location(s).")
                    progress_callback(
                        "Scripts are "
                        + ("enabled" if allow_scripts else "disabled")
                        + (" with sandbox" if allow_scripts and sandbox_scripts else "")
                        + "."
                    )

                progress_callback(f"Extracting and installing {record.name}...")

            progress_log = []
            def dual_callback(msg: str) -> None:
                progress_log.append(msg)
                if progress_callback:
                    progress_callback(msg)

            names = install_from_archive(
                tmp_path,
                allow_install_scripts=allow_scripts,
                sandbox_install_scripts=sandbox_scripts,
                allow_source_build=allow_source_build,
                progress_callback=dual_callback,
            )
            if not names:
                source_required = next(
                    (line for line in progress_log if "Source build required" in line),
                    "",
                )
                if source_required:
                    raise ValueError(source_required)

                error_keywords = (
                    "meson setup failed", "meson install failed",
                    "build failed", "install failed",
                    "configured failed", "autoconf",
                    "dart-sass", "sass", "missing dependency",
                    "not found", "command not found",
                )
                source_build_failed = None
                for line in reversed(progress_log):
                    line_lower = line.lower()
                    if any(keyword in line_lower for keyword in error_keywords):
                        source_build_failed = line
                        break

                if source_build_failed:
                    if (
                        allow_source_build
                        and "missing required submodule files" in source_build_failed.lower()
                        and shutil.which("git")
                    ):
                        clone_url = _github_clone_url(record)
                        if clone_url:
                            clone_dir = os.path.join(tmp_dir, "_ltm_repo_clone")
                            if progress_callback:
                                progress_callback(
                                    "Archive is missing submodule content; retrying via recursive Git clone..."
                                )
                            clone = subprocess.run(
                                ["git", "clone", "--depth", "1", "--recurse-submodules", clone_url, clone_dir],
                                timeout=900,
                                check=False,
                                capture_output=True,
                                text=True,
                            )
                            if clone.returncode == 0:
                                if progress_callback:
                                    progress_callback("Recursive Git clone completed; retrying install from source tree...")
                                names = install_from_source_tree(
                                    clone_dir,
                                    allow_install_scripts=allow_scripts,
                                    sandbox_install_scripts=sandbox_scripts,
                                    allow_source_build=True,
                                    progress_callback=dual_callback,
                                )
                                if names:
                                    return names
                            elif progress_callback:
                                detail = (clone.stderr or clone.stdout or "").strip()
                                if detail:
                                    progress_callback(f"Recursive clone failed: {detail[:180]}")
                    raise ValueError(source_build_failed)

                raise ValueError(
                    f"No installable theme directories were found in {record.name}. "
                    "This may be a source-only release that requires building. "
                    "Ensure the archive is not corrupted."
                )
            if progress_callback:
                progress_callback(f"Installation complete: {', '.join(names)}")
            return names
        finally:
            if progress_callback:
                progress_callback(f"Cleaning up temporary files for {record.name}...")
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    @staticmethod
    def _install_package_record(record: ThemeRecord, progress_callback=None) -> list[str]:
        package_name = (record.package_name or "").strip()
        if not package_name:
            raise ValueError("Missing package name for package-manager install")

        package_manager = record.source if record.source in ("apt", "pacman") else detect_environment().package_manager
        if progress_callback:
            progress_callback(f"Installing package {package_name} via {package_manager}...")
        ok = install_from_package(package_name, package_manager)
        if not ok:
            raise RuntimeError(f"Package install failed: {package_name} ({package_manager})")
        if progress_callback:
            progress_callback(f"Package install complete: {package_name}")
        return [record.name]

    def _on_install_done(self, record: ThemeRecord, names: list[str], card: Optional[ThemeCardWidget]) -> None:
        if self._install_progress is not None:
            self._install_progress.append_line("installation completed successfully")
            self._install_progress.mark_done()
        if card is not None:
            card.mark_installed()
        label = ", ".join(names) if names else record.name
        self.app.clear_install_active(record.name, "success")
        self.app.record_recent_action("install-complete", label)
        log.info("Install completed for '%s': %s", record.name, label)
        self.app.set_status(f"Installed: {label}")
        self.app.installed_tab.refresh()
        QMessageBox.information(
            self,
            "Theme Installed",
            f"Installed successfully:\n\n{label}",
        )
        self._prompt_apply_theme(record, names)

    def _on_install_error(
        self,
        record: ThemeRecord,
        message: str,
        card: Optional[ThemeCardWidget],
        *,
        allow_source_build: bool = False,
        allow_scripts: bool = False,
        sandbox_scripts: bool = True,
    ) -> None:
        msg_lower = str(message).lower()
        source_hint = _should_prompt_source_build(msg_lower)
        if (not allow_source_build) and source_hint:
            choice = QMessageBox.question(
                self,
                "Source Build Required",
                f"'{record.name}' appears to be source-only and needs a local build step.\n\n"
                "This can run project build tools and may install build dependencies. "
                "Only continue if you trust this source.\n\n"
                "Proceed with source build?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if choice == QMessageBox.Yes:
                if self._install_progress is not None:
                    self._install_progress.append_line("retrying with source build enabled...")
                self.app.set_status(f"Retrying {record.name} with source build enabled...")
                self._run_worker(
                    self._download_and_install,
                    record,
                    allow_scripts,
                    sandbox_scripts,
                    True,
                    done=lambda names: self._on_install_done(record, names, card),
                    failed=lambda err: self._on_install_error(
                        record,
                        err,
                        card,
                        allow_source_build=True,
                        allow_scripts=allow_scripts,
                        sandbox_scripts=sandbox_scripts,
                    ),
                    progress=self._update_install_progress,
                )
                return

        if self._install_progress is not None:
            self._install_progress.append_line(f"installation failed: {message}")
            self._install_progress.close_btn.setEnabled(True)
        if card is not None:
            card.mark_error()
        self.app.clear_install_active(record.name, "failed")
        self.app.record_recent_action("install-failed", f"{record.name}: {message}")
        log.error("Install failed for '%s': %s", record.name, message)
        self.app.set_status(f"Install failed for {record.name}: {message}")
        
        error_guidance = message
        if "dart-sass" in message.lower() or ("sass" in message.lower() and "requires" in message.lower()):
            error_guidance = (
                f"{message}\n\n"
                "FIX: Install dart-sass using your package manager:\n"
                "  sudo apt install dart-sass  # Ubuntu/Debian\n"
                "  sudo dnf install dart-sass  # Fedora\n"
                "  sudo pacman -S dart-sass    # Arch\n\n"
                "Then try installing again."
            )
        elif "build failed" in message.lower():
            error_guidance = (
                f"{message}\n\n"
                "This project requires build tools that may be missing.\n"
                "Common solutions:\n"
                "1. Install build essentials: sudo apt install build-essential\n"
                "2. Install meson: sudo apt install meson\n"
                "3. Try a pre-built version from your package manager\n\n"
                "For specific help, search the project's GitHub issues."
            )
        
        QMessageBox.critical(self, "Installation Failed", f"Could not install {record.name}\n\n{error_guidance}")

    def _prompt_apply_theme(self, record: ThemeRecord, names: list[str]) -> None:
        if not names:
            return
        if record.kind == "app/tooling":
            self.app.set_status(f"Installed package: {names[0]}")
            return
        candidate = names[0]
        is_extension = "@" in candidate
        choice = QMessageBox.question(
            self,
            "Apply Theme Now?" if not is_extension else "Enable Extension Now?",
            (
                f"{candidate} was installed successfully.\n\nApply it now?"
                if not is_extension else
                f"{candidate} was installed successfully.\n\nEnable this GNOME extension now?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if choice != QMessageBox.Yes:
            log.info("User chose not to apply '%s' immediately", candidate)
            return

        checkpoint = get_current_themes() if not is_extension else {}
        if checkpoint:
            self.app.record_restore_point(checkpoint, f"Before applying {candidate}")
        ok = False
        extension_msg = ""
        log.info("Applying installed artifact '%s' as %s", candidate, record.kind)
        if is_extension:
            ok, extension_msg = enable_extension_with_reason(candidate)
        else:
            ok = _apply_theme_value(record.kind, candidate, self.app.env.desktop)

        if ok:
            log.info("Applied theme successfully: %s", candidate)
            self.app.set_status(f"Applied theme: {candidate}")
            if is_extension and extension_msg and extension_msg != "enabled":
                QMessageBox.information(
                    self,
                    "Extension Installed",
                    f"{candidate} installed.\n\n{extension_msg}",
                )
            else:
                QMessageBox.information(self, "Theme Applied", f"Applied {candidate} successfully.")
            self.app.settings_tab.refresh()
        else:
            rollback_ok = False
            rollback_msg = ""
            if not is_extension:
                rollback_ok, rollback_msg = self._rollback_failed_apply(record.kind, candidate, checkpoint)

            log.warning("Installed theme could not be applied automatically: %s", candidate)
            if rollback_ok:
                self.app.set_status(f"Apply failed for {candidate}; restored previous theme")
            else:
                self.app.set_status(f"Installed but could not apply: {candidate}")

            details = ""
            if rollback_msg:
                details = f"\n\nRollback: {rollback_msg}"
            QMessageBox.warning(
                self,
                "Apply Failed",
                (
                    f"{candidate} was installed, but it could not be applied automatically.\n\n{extension_msg}{details}"
                    if is_extension and extension_msg else
                    f"{candidate} was installed, but it could not be applied automatically.{details}"
                ),
            )

    def _rollback_failed_apply(self, kind: str, attempted: str, checkpoint: dict[str, Optional[str]]) -> tuple[bool, str]:
        """Attempt to restore previously active theme after apply failure."""
        key = "cursor" if kind == "cursors" else kind
        previous = (checkpoint or {}).get(key)
        if not previous:
            return False, "No previous value to restore"
        if previous == attempted:
            return False, "Previous value matches attempted theme"

        ok = _apply_theme_value(kind, previous, self.app.env.desktop)

        if ok:
            log.info("Rolled back %s apply from '%s' to '%s'", kind, attempted, previous)
            self.app.settings_tab.refresh()
            return True, f"Restored previous {kind} theme: {previous}"
        return False, f"Failed to restore previous {kind} theme: {previous}"

    def _on_search_error(self, message: str) -> None:
        self.status.setText("Search error - showing sample data")
        self._render(MOCK_THEMES, "", "sample")

    def _run_worker(self, fn: Callable, *args, done: Callable[[object], None], failed: Callable[[str], None], progress: Optional[Callable[[str], None]] = None) -> None:
        worker_kwargs = {"progress_callback": None} if progress is not None else {}
        worker = Worker(fn, *args, **worker_kwargs)
        self._workers.append(worker)

        def _finish(result: object, w: Worker = worker) -> None:
            self.app.ui_dispatcher.dispatch.emit(lambda: self._complete_worker(w, done, result))

        def _fail(message: str, w: Worker = worker) -> None:
            self.app.ui_dispatcher.dispatch.emit(lambda: self._fail_worker(w, failed, message))

        def _progress(message: str) -> None:
            if progress is not None:
                self.app.ui_dispatcher.dispatch.emit(lambda: progress(message))

        worker.signals.finished.connect(_finish)
        worker.signals.failed.connect(_fail)
        if progress is not None:
            worker.signals.progress.connect(_progress)
        self.thread_pool.start(worker)

    def _complete_worker(self, worker: Worker, done: Callable[[object], None], result: object) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        done(result)

    def _fail_worker(self, worker: Worker, failed: Callable[[str], None], message: str) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        failed(message)


class InstalledTab(QWidget):
    def __init__(self, app: "ThemeManagerQtApp") -> None:
        super().__init__()
        self.app = app
        self.env = app.env
        self._entries: list[dict[str, str]] = []
        self._app_entries: list[dict] = []
        self._tab_layouts: dict[str, QVBoxLayout] = {}
        self._tab_widgets: dict[str, QWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        row = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.setShortcut("F5")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter installed themes and extensions...")
        self.filter_edit.textChanged.connect(lambda _t: self._render_entries())
        row.addWidget(self.filter_edit, 1)

        self.show_extensions = QCheckBox("Show Extensions")
        self.show_extensions.setChecked(True)
        self.show_extensions.toggled.connect(lambda _v: self.refresh())
        row.addWidget(self.show_extensions)

        self.saved_view_combo = QComboBox()
        self.saved_view_combo.currentIndexChanged.connect(lambda _i: self._render_entries())
        row.addWidget(self.saved_view_combo)

        row.addStretch(1)

        self.restore_hint = QLabel("No restore point yet")
        self.restore_hint.setObjectName("Muted")
        row.addWidget(self.restore_hint)

        self.restore_btn = QPushButton("Restore Last Setup")
        self.restore_btn.setProperty("variant", "subtle")
        self.restore_btn.style().unpolish(self.restore_btn)
        self.restore_btn.style().polish(self.restore_btn)
        self.restore_btn.clicked.connect(self._restore_last_setup)
        row.addWidget(self.restore_btn)

        self.setup_btn = QPushButton("Build Desktop Setup")
        self.setup_btn.setProperty("variant", "subtle")
        self.setup_btn.style().unpolish(self.setup_btn)
        self.setup_btn.style().polish(self.setup_btn)
        self.setup_btn.clicked.connect(self._open_setup_dialog)
        row.addWidget(self.setup_btn)
        root.addLayout(row)

        self.summary = QLabel("Installed items")
        self.summary.setObjectName("Muted")
        root.addWidget(self.summary)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)

        self._create_tab("shell", "Shell Themes")
        self._create_tab("gtk", "GTK Themes")
        self._create_tab("icons", "Icon Themes")
        self._create_tab("extensions", "Extensions")
        self._create_tab("apps", "Desktop Customization Apps")

        self.refresh()

    def _create_tab(self, key: str, title: str) -> None:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(host)

        tab = QWidget()
        tab_lay = QVBoxLayout(tab)
        tab_lay.setContentsMargins(0, 0, 0, 0)
        tab_lay.addWidget(scroll)
        self.tabs.addTab(tab, title)
        self._tab_layouts[key] = lay
        self._tab_widgets[key] = tab

    @staticmethod
    def _entry_key(category: str) -> str:
        if category.startswith("gtk"):
            return "gtk"
        if category.startswith("icons"):
            return "icons"
        if category.startswith("extensions"):
            return "extensions"
        if "shell" in category:
            return "shell"
        return "gtk"

    @staticmethod
    def _entry_scope(category: str) -> str:
        return "System" if "system" in category else "User"

    def refresh(self) -> None:
        data = list_themes(include_system=True)
        if self.show_extensions.isChecked():
            data.update(list_extensions(include_system=True))

        self._entries = []
        total = 0
        for category, names in data.items():
            for name in names:
                entry: dict[str, str] = {"category": category, "name": name, "key": self._entry_key(category)}
                if category.startswith("extensions"):
                    entry["enabled"] = "1" if is_extension_enabled(name) else "0"
                self._entries.append(entry)
                total += 1

        # Installed Desktop Customization apps (source-built)
        self._app_entries: list[dict] = list_installed_apps()
        total += len(self._app_entries)

        self.summary.setText(f"{total} installed items")
        ext_tab = self._tab_widgets.get("extensions")
        if ext_tab is not None:
            ext_index = self.tabs.indexOf(ext_tab)
            if ext_index >= 0:
                self.tabs.setTabVisible(ext_index, self.show_extensions.isChecked())
        self._refresh_saved_views()
        self._update_restore_ui()
        self._render_entries()

    def _refresh_saved_views(self) -> None:
        current = self.saved_view_combo.currentText() or "All Items"
        options = ["All Items", "Favorites", "Recent"] + self.app.collection_names()
        self.saved_view_combo.blockSignals(True)
        self.saved_view_combo.clear()
        self.saved_view_combo.addItems(options)
        idx = self.saved_view_combo.findText(current)
        self.saved_view_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.saved_view_combo.blockSignals(False)

    def _update_restore_ui(self) -> None:
        self.restore_btn.setEnabled(self.app.has_restore_point())
        self.restore_hint.setText(self.app.latest_restore_point_label())

    def _render_entries(self) -> None:
        text = self.filter_edit.text().strip().lower()
        saved_view = self.saved_view_combo.currentText().strip()

        for lay in self._tab_layouts.values():
            while lay.count() > 1:
                item = lay.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()

        shown = 0
        for entry in self._entries:
            category = entry["category"]
            name = entry["name"]
            entry_id = self.app.theme_entry_id(entry["key"], name)
            if text and text not in name.lower() and text not in category.lower():
                continue
            if not self._matches_saved_view(saved_view, entry_id):
                continue
            key = entry["key"]
            lay = self._tab_layouts.get(key)
            if lay is None:
                continue
            enabled = entry.get("enabled") == "1"
            lay.insertWidget(lay.count() - 1, self._build_item_card(category, name, enabled))
            shown += 1

        for key, lay in self._tab_layouts.items():
            if key == "apps":
                continue  # handled separately below
            if lay.count() == 1:
                empty = QLabel("No items in this section")
                empty.setObjectName("Muted")
                lay.insertWidget(0, empty)

        # ── Apps tab ──────────────────────────────────────────────────────────
        apps_lay = self._tab_layouts.get("apps")
        if apps_lay is not None:
            filtered_apps = [a for a in self._app_entries if not text or text in a.get("name", "").lower()]
            for app_entry in filtered_apps:
                apps_lay.insertWidget(apps_lay.count() - 1, self._build_app_card(app_entry))
                shown += 1
            if apps_lay.count() == 1:
                empty = QLabel("No Desktop Customization apps installed yet")
                empty.setObjectName("Muted")
                apps_lay.insertWidget(0, empty)

        suffix = " (themes only)" if not self.show_extensions.isChecked() else ""
        self.summary.setText(
            f"{shown} items shown{suffix}" if text else f"{len(self._entries) + len(self._app_entries)} installed items{suffix}"
        )

    def _matches_saved_view(self, view: str, entry_id: str) -> bool:
        if view == "All Items":
            return True
        if view == "Favorites":
            return self.app.is_favorite_entry(entry_id)
        if view == "Recent":
            return self.app.is_recent_entry(entry_id)
        return self.app.entry_in_collection(entry_id, view)

    def _build_item_card(self, category: str, name: str, enabled: bool = False) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)

        meta = QVBoxLayout()
        display_name = name
        detail_name = ""
        if category.startswith("extensions"):
            # GNOME extension directories are UUIDs. Show a friendlier title.
            short = name.split("@", 1)[0].replace("-", " ").replace("_", " ").strip()
            display_name = short.title() if short else name
            detail_name = name

        title = QLabel(display_name)
        title.setFont(QFont("Cantarell", 11, QFont.Bold))
        meta.addWidget(title)

        if detail_name:
            detail = QLabel(detail_name)
            detail.setObjectName("Muted")
            meta.addWidget(detail)

        badges = QHBoxLayout()
        entry_key = self._entry_key(category)
        entry_id = self.app.theme_entry_id(entry_key, name)

        kind = QLabel(entry_key.upper())
        kind.setObjectName("KindBadge")
        badges.addWidget(kind)

        scope = QLabel(self._entry_scope(category))
        scope.setObjectName("SourceBadge")
        badges.addWidget(scope)

        if category.startswith("extensions"):
            state = QLabel("Enabled" if enabled else "Disabled")
            state.setStyleSheet(
                "background:#183a2a; color:#6be0a0; border-radius:6px; padding:2px 6px; font-size:8pt; font-weight:700;"
                if enabled else
                "background:#3a1d2a; color:#ff9bbb; border-radius:6px; padding:2px 6px; font-size:8pt; font-weight:700;"
            )
            badges.addWidget(state)

        if self.app.is_favorite_entry(entry_id):
            favorite = QLabel("Favorite")
            favorite.setObjectName("ArtifactBadge")
            badges.addWidget(favorite)

        if self.app.is_recent_entry(entry_id):
            recent = QLabel("Recent")
            recent.setObjectName("SourceBadge")
            badges.addWidget(recent)

        for collection_name in self.app.entry_collections(entry_id)[:2]:
            collection = QLabel(collection_name.title())
            collection.setObjectName("ArtifactBadge")
            badges.addWidget(collection)
        badges.addStretch(1)
        meta.addLayout(badges)

        row.addLayout(meta, 1)

        btns = QHBoxLayout()
        favorite_btn = QPushButton("Unfavorite" if self.app.is_favorite_entry(entry_id) else "Favorite")
        favorite_btn.setProperty("variant", "subtle")
        favorite_btn.style().unpolish(favorite_btn)
        favorite_btn.style().polish(favorite_btn)
        favorite_btn.clicked.connect(lambda _=False, e=entry_id, n=name: self._toggle_favorite(e, n))
        btns.addWidget(favorite_btn)

        collect_btn = QPushButton("Collections")
        collect_btn.setProperty("variant", "subtle")
        collect_btn.style().unpolish(collect_btn)
        collect_btn.style().polish(collect_btn)
        collect_btn.clicked.connect(lambda _=False, e=entry_id, n=name: self._manage_collections(e, n))
        btns.addWidget(collect_btn)

        apply_btn = QPushButton("Enabled" if category.startswith("extensions") and enabled else ("Enable" if category.startswith("extensions") else "Apply"))
        if category.startswith("extensions") and enabled:
            apply_btn.setEnabled(False)
            apply_btn.setProperty("variant", "subtle")
            apply_btn.style().unpolish(apply_btn)
            apply_btn.style().polish(apply_btn)
        apply_btn.clicked.connect(lambda _=False, c=category, n=name: self._apply_item(c, n))
        btns.addWidget(apply_btn)

        rm_btn = QPushButton("Uninstall")
        rm_btn.setProperty("variant", "danger")
        rm_btn.style().unpolish(rm_btn)
        rm_btn.style().polish(rm_btn)
        rm_btn.clicked.connect(lambda _=False, c=category, n=name: self._remove_item(c, n))
        btns.addWidget(rm_btn)
        row.addLayout(btns)

        return frame

    def _build_app_card(self, app_entry: dict) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)

        meta = QVBoxLayout()
        name = app_entry.get("name", "Unknown App")
        installed_at = (app_entry.get("installed_at") or "")[:10]

        title = QLabel(name)
        title.setFont(QFont("Cantarell", 11, QFont.Bold))
        meta.addWidget(title)

        binaries = app_entry.get("binaries", [])
        if binaries:
            bin_label = QLabel("  ".join(Path(b).name for b in binaries[:3]))
            bin_label.setObjectName("Muted")
            meta.addWidget(bin_label)

        badges = QHBoxLayout()
        kind_badge = QLabel("APP")
        kind_badge.setObjectName("KindBadge")
        badges.addWidget(kind_badge)

        if installed_at:
            date_badge = QLabel(f"Installed {installed_at}")
            date_badge.setObjectName("SourceBadge")
            badges.addWidget(date_badge)

        badges.addStretch(1)
        meta.addLayout(badges)
        row.addLayout(meta, 1)

        btns = QHBoxLayout()

        launch_btn = QPushButton("Launch")
        launch_btn.setProperty("variant", "subtle")
        launch_btn.style().unpolish(launch_btn)
        launch_btn.style().polish(launch_btn)
        launch_btn.clicked.connect(lambda _=False, b=binaries: self._launch_app(b))
        btns.addWidget(launch_btn)

        rm_btn = QPushButton("Uninstall")
        rm_btn.setProperty("variant", "danger")
        rm_btn.style().unpolish(rm_btn)
        rm_btn.style().polish(rm_btn)
        rm_btn.clicked.connect(lambda _=False, n=name: self._remove_app(n))
        btns.addWidget(rm_btn)

        row.addLayout(btns)
        return frame

    def _launch_app(self, binaries: list[str]) -> None:
        for bin_path in binaries:
            p = Path(bin_path)
            # Skip CLI helpers (gradience-cli, etc.) — prefer the GUI binary
            if p.is_file() and "cli" not in p.name.lower():
                import subprocess as _sp
                try:
                    _sp.Popen([str(p)], start_new_session=True)
                    self.app.set_status(f"Launched {p.name}")
                    return
                except OSError as exc:
                    self.app.set_status(f"Could not launch {p.name}: {exc}")
                    return
        # Fallback: try first binary
        if binaries:
            p = Path(binaries[0])
            try:
                import subprocess as _sp
                _sp.Popen([str(p)], start_new_session=True)
                self.app.set_status(f"Launched {p.name}")
            except OSError as exc:
                self.app.set_status(f"Could not launch: {exc}")

    def _remove_app(self, name: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Uninstall App",
            f"Uninstall '{name}'?\n\nThis will remove the installed binary and data files.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        ok, message = uninstall_app(name)
        self.app.set_status(message)
        if ok:
            self.refresh()
        else:
            QMessageBox.critical(self, "Uninstall Failed", message)

    def _apply_item(self, category: str, name: str) -> None:
        ok = False
        if category.startswith("extensions"):
            ok, _ = enable_extension_with_reason(name)
        else:
            current = get_current_themes()
            self.app.record_restore_point(current, f"Before applying {name}")
            if category.startswith("gtk"):
                ok = _apply_theme_value("gtk", name, self.env.desktop)
            elif category.startswith("icons"):
                ok = _apply_theme_value("icons", name, self.env.desktop)
            elif "cursor" in category:
                ok = _apply_theme_value("cursors", name, self.env.desktop)
            elif "shell" in category:
                ok = _apply_theme_value("shell", name, self.env.desktop)

        if ok:
            if not category.startswith("extensions"):
                key = "cursor" if "cursor" in category else ("shell" if "shell" in category else ("icons" if category.startswith("icons") else "gtk"))
                self.app.remember_recent_theme(key, name)
            self.app.set_status(f"Switched to {name}")
            self.app.settings_tab.refresh()
            self._update_restore_ui()
            if category.startswith("extensions"):
                self.refresh()
            else:
                self._render_entries()
        else:
            self.app.set_status(f"Could not switch to {name}")

    def _restore_last_setup(self) -> None:
        ok, message = self.app.restore_last_snapshot()
        self.app.set_status(message)
        if ok:
            self.refresh()
            self.app.settings_tab.refresh()
            QMessageBox.information(self, "Restore Complete", message)
        else:
            QMessageBox.warning(self, "Restore Failed", message)

    def _toggle_favorite(self, entry_id: str, name: str) -> None:
        enabled = self.app.toggle_favorite_entry(entry_id)
        self.app.set_status(f"{'Saved' if enabled else 'Removed'} favorite: {name}")
        self._refresh_saved_views()
        self._render_entries()

    def _manage_collections(self, entry_id: str, name: str) -> None:
        options = self.app.collection_names() or ["minimal", "gaming", "light"]
        selected, ok = QInputDialog.getItem(
            self,
            "Collections",
            "Choose or type a collection name:",
            options,
            0,
            True,
        )
        if not ok:
            return
        collection_name = selected.strip().lower()
        if not collection_name:
            return
        added = self.app.toggle_entry_collection(entry_id, collection_name)
        action = "Saved to" if added else "Removed from"
        self.app.set_status(f"{action} {collection_name.title()}: {name}")
        self._refresh_saved_views()
        self._render_entries()

    def _open_setup_dialog(self) -> None:
        data = list_themes(include_system=True)
        choices = {
            "gtk": sorted(set(data.get("gtk_user", []) + data.get("gtk_system", [])), key=str.lower),
            "icons": sorted(set(data.get("icons_user", []) + data.get("icons_system", [])), key=str.lower),
            "cursor": sorted(set(data.get("cursors_user", []) + data.get("cursors_system", [])), key=str.lower),
            "shell": sorted(set(data.get("shell_user", []) + data.get("shell_system", [])), key=str.lower),
        }
        dialog = DesktopSetupDialog(self, choices, get_current_themes())
        if dialog.exec() != QDialog.Accepted:
            return
        setup = dialog.selected_setup()
        if not setup:
            QMessageBox.information(self, "Desktop Setup", "Choose at least one theme to apply.")
            return
        ok, message = self.app.apply_desktop_setup(setup)
        self.app.set_status(message)
        if ok:
            self.refresh()
            QMessageBox.information(self, "Desktop Setup Applied", message)
        else:
            QMessageBox.warning(self, "Desktop Setup Failed", message)

    def _remove_item(self, category: str, name: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Uninstall Item",
            f"Uninstall '{name}' from {category}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        if category.startswith("extensions"):
            ok = remove_extension(name, system_wide="system" in category)
        else:
            if "shell" in category:
                kind = "shell"
            elif category.startswith("icons"):
                kind = "icons"
            else:
                kind = "gtk"
            ok = remove_theme(name, kind=kind, system_wide="system" in category)
        if ok:
            self.app.set_status(f"Removed {name}")
            self.refresh()
            self.app.settings_tab.refresh()
        else:
            self.app.set_status(f"Could not remove {name}")


class SettingsTab(QWidget):
    def __init__(self, app: "ThemeManagerQtApp") -> None:
        super().__init__()
        self.app = app
        self.env = app.env
        self._status_refresh_in_flight = False
        self._status_refresh_pending = False
        self._status_refresh_ttl_seconds = 2.5
        self._last_status_refresh_monotonic = 0.0
        self._status_worker: Optional[Worker] = None
        self._current_theme_cache: dict[str, Optional[str]] = {
            "gtk": None,
            "icons": None,
            "cursor": None,
            "shell": None,
        }

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        prefs_tabs = QTabWidget()
        prefs_tabs.setDocumentMode(True)
        prefs_tabs.setObjectName("PreferencesSections")
        root.addWidget(prefs_tabs, 1)

        overview_wrap = QFrame()
        overview_wrap.setObjectName("PanelCard")
        overview_layout = QVBoxLayout(overview_wrap)
        overview_layout.setContentsMargins(8, 8, 8, 8)
        overview_layout.setSpacing(6)

        overview_title = QLabel("System Overview")
        overview_title.setFont(QFont("Cantarell", 11, QFont.Bold))
        overview_layout.addWidget(overview_title)

        self.info = QTextEdit()
        self.info.setReadOnly(True)
        self.info.setMinimumHeight(170)
        self.info.setStyleSheet(
            "QTextEdit {"
            "background:#13182a;"
            "border:1px solid #2a3150;"
            "border-radius:8px;"
            "padding:8px;"
            "font-family:'DejaVu Sans Mono';"
            "font-size:9pt;"
            "}"
        )
        overview_layout.addWidget(self.info)
        prefs_tabs.addTab(overview_wrap, "System Overview")

        source_wrap = QFrame()
        source_wrap.setObjectName("PanelCard")
        source_layout = QVBoxLayout(source_wrap)
        source_layout.setContentsMargins(8, 8, 8, 8)
        source_layout.setSpacing(6)

        source_title = QLabel("Source Integrations")
        source_title.setFont(QFont("Cantarell", 11, QFont.Bold))
        source_layout.addWidget(source_title)

        source_hint = QLabel("Add GitHub owners/orgs as trusted catalogs for Themes, Apps, or Extensions.")
        source_hint.setObjectName("Muted")
        source_hint.setWordWrap(True)
        source_layout.addWidget(source_hint)

        source_row = QHBoxLayout()
        self.src_owner = QLineEdit()
        self.src_owner.setPlaceholderText("GitHub owner/org (e.g. vinceliuice)")
        source_row.addWidget(self.src_owner, 2)

        self.src_label = QLineEdit()
        self.src_label.setPlaceholderText("Label (optional)")
        source_row.addWidget(self.src_label, 2)

        self.src_kind = QComboBox()
        self.src_kind.addItems(["all", "gtk", "icons", "shell", "cursors", "app/tooling"])
        source_row.addWidget(self.src_kind, 1)

        add_src = QPushButton("Add Source")
        add_src.clicked.connect(self._add_source)
        source_row.addWidget(add_src)
        source_layout.addLayout(source_row)

        self.source_list = QListWidget()
        self.source_list.setMinimumHeight(110)
        source_layout.addWidget(self.source_list)

        source_actions = QHBoxLayout()
        refresh_sources = QPushButton("Refresh Sources")
        refresh_sources.setProperty("variant", "subtle")
        refresh_sources.style().unpolish(refresh_sources)
        refresh_sources.style().polish(refresh_sources)
        refresh_sources.clicked.connect(self._refresh_sources)
        source_actions.addWidget(refresh_sources)

        remove_src = QPushButton("Remove Selected")
        remove_src.setProperty("variant", "danger")
        remove_src.style().unpolish(remove_src)
        remove_src.style().polish(remove_src)
        remove_src.clicked.connect(self._remove_source)
        source_actions.addWidget(remove_src)
        source_actions.addStretch(1)
        source_layout.addLayout(source_actions)

        source_page = QWidget()
        source_page_layout = QVBoxLayout(source_page)
        source_page_layout.setContentsMargins(0, 0, 0, 0)
        source_page_layout.setSpacing(8)
        source_page_layout.addWidget(source_wrap)
        source_page_layout.addStretch(1)

        prefs_tabs.addTab(source_page, "Source Integrations")

        policy_wrap = QFrame()
        policy_wrap.setObjectName("PanelCard")
        policy_layout = QVBoxLayout(policy_wrap)
        policy_layout.setContentsMargins(8, 8, 8, 8)
        policy_layout.setSpacing(6)
        policy_layout.setAlignment(Qt.AlignTop)

        policy_title = QLabel("Installation Policy")
        policy_title.setFont(QFont("Cantarell", 11, QFont.Bold))
        policy_layout.addWidget(policy_title)

        self.allow_scripts_cb = QCheckBox("Allow install scripts embedded in archives")
        policy_layout.addWidget(self.allow_scripts_cb)

        self.sandbox_scripts_cb = QCheckBox("Run install scripts in sandbox mode when available")
        policy_layout.addWidget(self.sandbox_scripts_cb)

        save_policy_btn = QPushButton("Save Install Policy")
        save_policy_btn.setProperty("variant", "subtle")
        save_policy_btn.style().unpolish(save_policy_btn)
        save_policy_btn.style().polish(save_policy_btn)
        save_policy_btn.clicked.connect(self._save_install_policy)
        policy_layout.addWidget(save_policy_btn)

        policy_page = QWidget()
        policy_page_layout = QVBoxLayout(policy_page)
        policy_page_layout.setContentsMargins(0, 0, 0, 0)
        policy_page_layout.setSpacing(8)
        policy_page_layout.addWidget(policy_wrap)
        policy_page_layout.addStretch(1)

        prefs_tabs.addTab(policy_page, "Installation")

        diagnostics_wrap = QFrame()
        diagnostics_wrap.setObjectName("PanelCard")
        diagnostics_layout = QVBoxLayout(diagnostics_wrap)
        diagnostics_layout.setContentsMargins(8, 8, 8, 8)
        diagnostics_layout.setSpacing(6)

        diagnostics_title = QLabel("Diagnostics")
        diagnostics_title.setFont(QFont("Cantarell", 11, QFont.Bold))
        diagnostics_layout.addWidget(diagnostics_title)

        diagnostics_hint = QLabel("Refresh runtime state, copy quick diagnostics, or export a support bundle.")
        diagnostics_hint.setObjectName("Muted")
        diagnostics_hint.setWordWrap(True)
        diagnostics_layout.addWidget(diagnostics_hint)

        diagnostics_actions = QHBoxLayout()

        refresh = QPushButton("Refresh Overview")
        refresh.setShortcut("Ctrl+R")
        refresh.clicked.connect(self.refresh)
        diagnostics_actions.addWidget(refresh)

        open_logs = QPushButton("Open Logs")
        open_logs.setProperty("variant", "subtle")
        open_logs.style().unpolish(open_logs)
        open_logs.style().polish(open_logs)
        open_logs.clicked.connect(self._open_logs_folder)
        diagnostics_actions.addWidget(open_logs)

        copy_diag = QPushButton("Copy Summary")
        copy_diag.setProperty("variant", "subtle")
        copy_diag.style().unpolish(copy_diag)
        copy_diag.style().polish(copy_diag)
        copy_diag.clicked.connect(self._copy_diagnostics)
        diagnostics_actions.addWidget(copy_diag)

        export_diag = QPushButton("Export Bundle")
        export_diag.setProperty("variant", "subtle")
        export_diag.style().unpolish(export_diag)
        export_diag.style().polish(export_diag)
        export_diag.clicked.connect(self._export_diagnostics_bundle)
        diagnostics_actions.addWidget(export_diag)

        diagnostics_actions.addStretch(1)
        diagnostics_layout.addLayout(diagnostics_actions)

        diagnostics_page = QWidget()
        diagnostics_page_layout = QVBoxLayout(diagnostics_page)
        diagnostics_page_layout.setContentsMargins(0, 0, 0, 0)
        diagnostics_page_layout.setSpacing(8)
        diagnostics_page_layout.addWidget(diagnostics_wrap)
        diagnostics_page_layout.addStretch(1)
        prefs_tabs.addTab(diagnostics_page, "Diagnostics")

        self._refresh_sources()
        self.refresh(force=True)

    def _render_info(self, current: dict[str, Optional[str]]) -> None:
        policy = self.app.install_policy()
        self.allow_scripts_cb.setChecked(bool(policy.get("allow_install_scripts", False)))
        self.sandbox_scripts_cb.setChecked(bool(policy.get("sandbox_install_scripts", True)))

        recent_actions = self.app.ui_state.get("recent_actions", [])
        recent_lines: list[str] = []
        if isinstance(recent_actions, list):
            for item in recent_actions[:6]:
                if not isinstance(item, dict):
                    continue
                recent_lines.append(
                    f"  - {item.get('timestamp') or ''} {item.get('action') or ''}: {item.get('detail') or ''}".rstrip()
                )

        text = (
            f"Desktop: {self.env.desktop}\n"
            f"Distro: {self.env.distro}\n"
            f"Session: {'Wayland' if self.env.is_wayland else 'X11'}\n"
            f"Package manager: {self.env.package_manager}\n"
            f"gsettings: {'yes' if self.env.has_gsettings else 'no'}\n"
            f"Flatpak: {'yes' if self.env.has_flatpak else 'no'}\n"
            f"Install scripts allowed: {'yes' if policy.get('allow_install_scripts') else 'no'}\n"
            f"Script sandbox: {'yes' if policy.get('sandbox_install_scripts') else 'no'}\n\n"
            "Current themes:\n"
            f"  GTK: {current.get('gtk') or '(not set)'}\n"
            f"  Icons: {current.get('icons') or '(not set)'}\n"
            f"  Cursor: {current.get('cursor') or '(not set)'}\n"
            f"  Shell: {current.get('shell') or '(not set)'}\n\n"
            "Recent actions:\n"
            + ("\n".join(recent_lines) if recent_lines else "  (none)")
        )
        self.info.setPlainText(text)

    def refresh(self, force: bool = False) -> None:
        self._render_info(self._current_theme_cache)

        now = time.monotonic()
        if self._status_refresh_in_flight:
            self._status_refresh_pending = True
            return
        if not force and (now - self._last_status_refresh_monotonic) < self._status_refresh_ttl_seconds:
            return

        self._status_refresh_in_flight = True
        self._last_status_refresh_monotonic = now
        worker = Worker(get_current_themes)
        self._status_worker = worker

        def _finish(result: object, w: Worker = worker) -> None:
            self.app.ui_dispatcher.dispatch.emit(lambda: self._on_status_refresh_done(w, result))

        def _fail(message: str, w: Worker = worker) -> None:
            self.app.ui_dispatcher.dispatch.emit(lambda: self._on_status_refresh_failed(w, message))

        worker.signals.finished.connect(_finish)
        worker.signals.failed.connect(_fail)
        self.app.thread_pool.start(worker)

    def _on_status_refresh_done(self, worker: Worker, result: object) -> None:
        if self._status_worker is worker:
            self._status_worker = None
        self._status_refresh_in_flight = False

        if isinstance(result, dict):
            self._current_theme_cache = {
                "gtk": result.get("gtk"),
                "icons": result.get("icons"),
                "cursor": result.get("cursor"),
                "shell": result.get("shell"),
            }
        self._render_info(self._current_theme_cache)

        if self._status_refresh_pending:
            self._status_refresh_pending = False
            self.refresh(force=True)

    def _on_status_refresh_failed(self, worker: Worker, _message: str) -> None:
        if self._status_worker is worker:
            self._status_worker = None
        self._status_refresh_in_flight = False
        self._render_info(self._current_theme_cache)

        if self._status_refresh_pending:
            self._status_refresh_pending = False
            self.refresh(force=True)

    def _open_logs_folder(self) -> None:
        os.makedirs(LOG_DIR, exist_ok=True)
        webbrowser.open(f"file://{LOG_DIR}")

    def _copy_diagnostics(self) -> None:
        current = get_current_themes()
        text = (
            f"desktop={self.env.desktop}\n"
            f"distro={self.env.distro}\n"
            f"session={'wayland' if self.env.is_wayland else 'x11'}\n"
            f"package_manager={self.env.package_manager}\n"
            f"gtk={current.get('gtk') or ''}\n"
            f"icons={current.get('icons') or ''}\n"
            f"cursor={current.get('cursor') or ''}\n"
            f"shell={current.get('shell') or ''}\n"
        )
        app = QApplication.instance()
        if app is not None:
            app.clipboard().setText(text)
        self.app.set_status("Copied diagnostics to clipboard")

    def _save_install_policy(self) -> None:
        self.app.set_install_policy(
            allow_install_scripts=self.allow_scripts_cb.isChecked(),
            sandbox_install_scripts=self.sandbox_scripts_cb.isChecked(),
        )
        self.app.record_recent_action(
            "install-policy",
            "allow_scripts="
            + ("yes" if self.allow_scripts_cb.isChecked() else "no")
            + ", sandbox="
            + ("yes" if self.sandbox_scripts_cb.isChecked() else "no"),
        )
        self.app.set_status("Saved install safety policy")
        self.refresh(force=True)

    def _export_diagnostics_bundle(self) -> None:
        target, _selected = QFileDialog.getSaveFileName(
            self,
            "Export Diagnostic Bundle",
            str(Path.home() / "themeatlas-diagnostics.zip"),
            "Zip files (*.zip)",
        )
        if not target:
            return

        current = get_current_themes()
        safe_payload = {
            "app": "themeatlas",
            "version": "1.0.0",
            "python": platform.python_version(),
            "desktop": self.env.desktop,
            "distro": self.env.distro,
            "session": "wayland" if self.env.is_wayland else "x11",
            "package_manager": self.env.package_manager,
            "themes": current,
            "recent_actions": self.app.ui_state.get("recent_actions", [])[:30],
            "install_history": self.app.ui_state.get("install_history", [])[:30],
        }

        logs: list[Path] = []
        log_dir = Path(LOG_DIR)
        if log_dir.exists():
            logs = sorted((p for p in log_dir.glob("*.log") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)[:4]

        try:
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("diagnostics/environment.json", json.dumps(safe_payload, indent=2, sort_keys=True))
                for path in logs:
                    zf.write(path, arcname=f"diagnostics/logs/{path.name}")
            self.app.record_recent_action("diagnostics-export", Path(target).name)
            self.app.set_status(f"Exported diagnostics bundle: {target}")
            QMessageBox.information(self, "Diagnostics Exported", f"Saved diagnostics bundle:\n\n{target}")
        except OSError as exc:
            QMessageBox.warning(self, "Diagnostics Export Failed", str(exc))

    def _refresh_sources(self) -> None:
        self.source_list.clear()
        for spec in list_custom_sources():
            item = QListWidgetItem(f"{spec.label}  ({spec.owner})  [{spec.kind}]")
            item.setData(Qt.UserRole, spec.name)
            self.source_list.addItem(item)

        self.app.available_tab.reload_sources()
        self.app.apps_tab.reload_sources()
        if hasattr(self.app, "extensions_tab") and self.app.extensions_tab is not None:
            self.app.extensions_tab.reload_sources()

    def _add_source(self) -> None:
        owner = self.src_owner.text().strip()
        label = self.src_label.text().strip()
        kind = self.src_kind.currentText()
        if not owner:
            QMessageBox.information(self, "Add Source", "Enter a GitHub owner/org first.")
            return

        try:
            name = add_custom_github_source(label, owner, kind)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Add Source Failed", str(exc))
            return

        self.src_owner.clear()
        self.src_label.clear()
        self._refresh_sources()
        self.app.set_status(f"Added source: {name}")

    def _remove_source(self) -> None:
        item = self.source_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Remove Source", "Select a source first.")
            return
        name = item.data(Qt.UserRole)
        if not name:
            return
        if remove_custom_source(str(name)):
            self._refresh_sources()
            self.app.set_status(f"Removed source: {name}")
        else:
            QMessageBox.warning(self, "Remove Source", "Could not remove selected source.")


class ThemeManagerQtApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.env = detect_environment()
        self.thread_pool = QThreadPool.globalInstance()
        self.ui_dispatcher = UiDispatcher()
        self._restore_points: list[dict[str, object]] = []
        self._welcome_dialog: Optional[WelcomeDialog] = None
        self.ui_state = load_ui_state()

        self.setWindowTitle("ThemeAtlas")
        app_icon = _themeatlas_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.resize(1100, 760)
        self.setMinimumSize(860, 560)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("Header")
        h = QHBoxLayout(header)
        h.setContentsMargins(18, 12, 18, 12)

        icon_lbl = QLabel()
        icon_pix = _themeatlas_pixmap(30)
        if icon_pix is not None:
            icon_lbl.setPixmap(icon_pix)
            icon_lbl.setFixedSize(34, 34)
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setStyleSheet(
                "background:#eef2ff;"
                "border:1px solid #7a4bff;"
                "border-radius:8px;"
                "padding:2px;"
            )
            h.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title = QLabel("ThemeAtlas")
        title.setObjectName("HeaderTitle")
        subtitle = QLabel("Cross-distro theme browser, previewer, and installer")
        subtitle.setObjectName("HeaderSub")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        h.addLayout(title_col)
        h.addStretch(1)

        chip = QLabel(f"{self.env.desktop.upper()} - {self.env.distro}")
        chip.setObjectName("Chip")
        h.addWidget(chip)

        root.addWidget(header)

        tabs = QTabWidget()
        self.available_tab = AvailableTab(self)
        self.apps_tab = AvailableTab(self, fixed_kind="app/tooling", show_category_filter=True)
        self.extensions_tab = AvailableTab(self, fixed_kind="app/tooling", artifact_filter="extension")
        self.installed_tab = InstalledTab(self)
        self.settings_tab = SettingsTab(self)
        tabs.addTab(self.available_tab, "Themes")
        tabs.addTab(self.apps_tab, "Apps")
        tabs.addTab(self.extensions_tab, "Extensions")
        tabs.addTab(self.installed_tab, "Installed")
        tabs.addTab(self.settings_tab, "Preferences")
        tabs.currentChanged.connect(self._tab_changed)
        root.addWidget(tabs, 1)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("StatusBar")
        root.addWidget(self.status_label)

        self.setCentralWidget(central)
        QTimer.singleShot(0, self._maybe_show_onboarding)
        QTimer.singleShot(120, self._check_interrupted_install)

    def record_restore_point(self, snapshot: dict[str, Optional[str]], label: str) -> None:
        normalized = {
            "gtk": snapshot.get("gtk"),
            "icons": snapshot.get("icons"),
            "cursor": snapshot.get("cursor"),
            "shell": snapshot.get("shell"),
        }
        if self._restore_points and self._restore_points[-1].get("snapshot") == normalized:
            self._restore_points[-1]["label"] = label
            return
        self._restore_points.append({"snapshot": normalized, "label": label})
        self._restore_points = self._restore_points[-8:]

    @staticmethod
    def theme_entry_id(kind: str, name: str) -> str:
        return f"{kind}:{name.strip().lower()}"

    def _favorites(self) -> list[str]:
        return list(self.ui_state.setdefault("favorites", []))  # type: ignore[arg-type]

    def _recent(self) -> list[str]:
        return list(self.ui_state.setdefault("recent", []))  # type: ignore[arg-type]

    def _collections(self) -> dict[str, list[str]]:
        return dict(self.ui_state.setdefault("collections", {}))  # type: ignore[arg-type]

    def _save_ui_state(self) -> None:
        save_ui_state(self.ui_state)

    def install_policy(self) -> dict[str, bool]:
        policy = self.ui_state.setdefault("install_policy", {})
        if not isinstance(policy, dict):
            policy = {}
        return {
            "allow_install_scripts": bool(policy.get("allow_install_scripts", False)),
            "sandbox_install_scripts": bool(policy.get("sandbox_install_scripts", True)),
        }

    def set_install_policy(self, *, allow_install_scripts: bool, sandbox_install_scripts: bool) -> None:
        self.ui_state["install_policy"] = {
            "allow_install_scripts": bool(allow_install_scripts),
            "sandbox_install_scripts": bool(sandbox_install_scripts),
        }
        self._save_ui_state()

    def record_recent_action(self, action: str, detail: str) -> None:
        actions = self.ui_state.setdefault("recent_actions", [])
        if not isinstance(actions, list):
            actions = []
        actions.insert(0, {
            "action": action,
            "detail": detail,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })
        self.ui_state["recent_actions"] = actions[:120]
        self._save_ui_state()

    def mark_install_active(self, name: str, phase: str) -> None:
        self.ui_state["active_install"] = {
            "name": name,
            "phase": phase,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_ui_state()

    def clear_install_active(self, name: str, outcome: str) -> None:
        active = self.ui_state.get("active_install")
        if isinstance(active, dict) and active.get("name"):
            history = self.ui_state.setdefault("install_history", [])
            if not isinstance(history, list):
                history = []
            history.insert(0, {
                "action": "install",
                "detail": f"{active.get('name')} -> {outcome}",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })
            self.ui_state["install_history"] = history[:120]
        self.ui_state["active_install"] = {}
        self._save_ui_state()

    def is_favorite_entry(self, entry_id: str) -> bool:
        return entry_id in self._favorites()

    def is_recent_entry(self, entry_id: str) -> bool:
        return entry_id in self._recent()

    def toggle_favorite_entry(self, entry_id: str) -> bool:
        favorites = self._favorites()
        if entry_id in favorites:
            favorites = [item for item in favorites if item != entry_id]
            enabled = False
        else:
            favorites.append(entry_id)
            enabled = True
        self.ui_state["favorites"] = favorites
        self._save_ui_state()
        return enabled

    def remember_recent_theme(self, kind: str, name: str) -> None:
        entry_id = self.theme_entry_id(kind, name)
        recent = [item for item in self._recent() if item != entry_id]
        recent.insert(0, entry_id)
        self.ui_state["recent"] = recent[:12]
        self._save_ui_state()

    def collection_names(self) -> list[str]:
        return sorted(self._collections().keys())

    def entry_collections(self, entry_id: str) -> list[str]:
        return sorted(name for name, entries in self._collections().items() if entry_id in entries)

    def entry_in_collection(self, entry_id: str, collection_name: str) -> bool:
        return entry_id in self._collections().get(collection_name.strip().lower(), [])

    def toggle_entry_collection(self, entry_id: str, collection_name: str) -> bool:
        clean_name = collection_name.strip().lower()
        collections = self._collections()
        entries = [item for item in collections.get(clean_name, []) if item != entry_id]
        if len(entries) == len(collections.get(clean_name, [])):
            entries.append(entry_id)
            added = True
        else:
            added = False
        collections[clean_name] = entries
        self.ui_state["collections"] = collections
        self._save_ui_state()
        return added

    def _maybe_show_onboarding(self) -> None:
        if self.ui_state.get("onboarding_complete"):
            return
        existing_dialog = getattr(self, "_welcome_dialog", None)
        if existing_dialog is not None:
            existing_dialog.raise_()
            existing_dialog.activateWindow()
            return

        dialog = WelcomeDialog(self, self.env)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        if dialog.hide_next_time.isChecked():
            self.ui_state["onboarding_complete"] = True
            self._save_ui_state()

        def _finish_onboarding(_result: int) -> None:
            self.ui_state["onboarding_complete"] = bool(dialog.hide_next_time.isChecked())
            self._save_ui_state()
            self._welcome_dialog = None

        dialog.finished.connect(_finish_onboarding)
        self._welcome_dialog = dialog
        dialog.open()
        dialog.raise_()
        dialog.activateWindow()

    def _check_interrupted_install(self) -> None:
        active = self.ui_state.get("active_install")
        if not isinstance(active, dict):
            return
        name = str(active.get("name") or "").strip()
        phase = str(active.get("phase") or "").strip() or "unknown phase"
        if not name:
            return
        self.clear_install_active(name, "interrupted")
        self.set_status(f"Recovered from interrupted install: {name}")
        QMessageBox.warning(
            self,
            "Interrupted Install Recovered",
            f"ThemeAtlas detected an interrupted install for {name}.\n\n"
            f"Last known phase: {phase}\n"
            "No filesystem rollback was needed, but you may re-run install safely.",
        )

    def has_restore_point(self) -> bool:
        return bool(self._restore_points)

    def latest_restore_point_label(self) -> str:
        if not self._restore_points:
            return "No restore point yet"
        label = str(self._restore_points[-1].get("label") or "Last restore point")
        snapshot = self._restore_points[-1].get("snapshot") or {}
        gtk_name = (snapshot or {}).get("gtk") or "current"
        return f"{label} - GTK {gtk_name}"

    def restore_last_snapshot(self) -> tuple[bool, str]:
        if not self._restore_points:
            return False, "No restore point recorded yet"

        entry = self._restore_points.pop()
        snapshot = dict(entry.get("snapshot") or {})
        label = str(entry.get("label") or "Last restore point")

        failures: list[str] = []
        restored: list[str] = []
        for key in ("gtk", "icons", "cursor", "shell"):
            value = snapshot.get(key)
            if not value:
                continue
            if _apply_theme_value(key, str(value), self.env.desktop):
                restored.append(f"{key}={value}")
            else:
                failures.append(f"{key}={value}")

        if failures:
            return False, f"Could not fully restore {label}: {', '.join(failures)}"

        self.settings_tab.refresh()
        self.installed_tab._update_restore_ui()
        return True, f"Restored {label}: {', '.join(restored)}"

    def apply_desktop_setup(self, selection: dict[str, str]) -> tuple[bool, str]:
        checkpoint = get_current_themes()
        self.record_restore_point(checkpoint, "Before desktop setup")

        applied: list[str] = []
        for key in ("gtk", "icons", "cursor", "shell"):
            value = selection.get(key)
            if not value:
                continue
            if _apply_theme_value(key, value, self.env.desktop):
                applied.append(f"{key}={value}")
                self.remember_recent_theme(key, value)
                continue

            self._restore_points.pop() if self._restore_points else None
            for restore_key in ("gtk", "icons", "cursor", "shell"):
                restore_value = checkpoint.get(restore_key)
                if restore_value:
                    _apply_theme_value(restore_key, str(restore_value), self.env.desktop)
            self.settings_tab.refresh()
            self.installed_tab._update_restore_ui()
            return False, f"Failed while applying {key}={value}; previous setup restored"

        self.settings_tab.refresh()
        self.installed_tab._update_restore_ui()
        return True, f"Applied desktop setup: {', '.join(applied)}"

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _tab_changed(self, idx: int) -> None:
        if idx == 3:
            self.installed_tab.refresh()
        elif idx == 4:
            self.settings_tab.refresh()


def launch_gui() -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("ThemeAtlas")
    app.setApplicationDisplayName("ThemeAtlas")
    # Helps GNOME/Wayland map the running app to themeatlas.desktop icon.
    # Qt expects the desktop file id without the ".desktop" suffix.
    app.setDesktopFileName("themeatlas")
    icon = _themeatlas_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    app.setStyleSheet(APP_STYLE)
    window = ThemeManagerQtApp()
    window.show()
    window.raise_()
    window.activateWindow()
    QTimer.singleShot(0, window.raise_)
    QTimer.singleShot(0, window.activateWindow)
    app.exec()
