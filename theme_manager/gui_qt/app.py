"""PySide6-based GUI for Linux Theme Manager."""

from __future__ import annotations

import io
import html as html_lib
import os
import re
import tempfile
import traceback
import urllib.request
import webbrowser
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


from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..environment import detect_environment
from ..extensions import enable_extension_with_reason, is_extension_enabled, list_extensions, remove_extension
from ..installer import install_from_archive, install_from_package
from ..logger import get_logger
from ..logger import LOG_DIR
from ..manager import list_themes, remove_theme
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
)

log = get_logger(__name__)

APP_STYLE = """
QWidget {
    background: #141625;
    color: #e8ebff;
    font-family: Cantarell, 'Noto Sans', sans-serif;
    font-size: 10pt;
}
QMainWindow {
    background: #141625;
}
QFrame#Header {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #211535,
        stop:0.55 #1a1730,
        stop:1 #2a1540);
    border-bottom: 1px solid #3a305a;
}
QLabel#HeaderTitle {
    color: #f4f1ff;
    font-size: 18pt;
    font-weight: 700;
}
QLabel#HeaderSub {
    color: #b8afd8;
    font-size: 10pt;
}
QLabel#Chip {
    color: #fef7ff;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #6f3bff,
        stop:1 #ff2ea6);
    border-radius: 8px;
    padding: 5px 10px;
    font-weight: 700;
}
QTabWidget::pane {
    border: 0;
    background: #141625;
}
QTabBar::tab {
    background: #21253a;
    color: #9da5c1;
    padding: 9px 16px;
    margin-right: 6px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 700;
    border: 1px solid #313756;
}
QTabBar::tab:selected {
    background: #2a1f43;
    color: #ff50b8;
    border: 1px solid #7343ff;
}
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #6f3bff,
        stop:1 #ff2ea6);
    color: white;
    border: 0;
    border-radius: 8px;
    padding: 7px 12px;
    font-weight: 700;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #8356ff,
        stop:1 #ff48b3);
}
QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #5b31d8,
        stop:1 #d82588);
    border: 1px solid #ff73c8;
}
QPushButton:disabled {
    background: #333857;
    color: #9198b7;
}
QPushButton[variant='subtle'] {
    background: #23263b;
    color: #d9ddf3;
    border: 1px solid #3a3f60;
}
QPushButton[variant='danger'] {
    background: #d8377b;
    color: white;
}
QPushButton[variant='subtle']:pressed {
    background: #2e3150;
    border: 1px solid #56608d;
}
QLineEdit, QComboBox, QListWidget, QTextEdit {
    background: #1c2031;
    border: 1px solid #373d5d;
    border-radius: 8px;
    padding: 6px;
    selection-background-color: #6f3bff;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
    border: 1px solid #ff2ea6;
}
QComboBox QAbstractItemView,
QListWidget {
    background: #1b1f31;
    border: 1px solid #373d5d;
    color: #e8ebff;
}
QComboBox::drop-down {
    border: 0;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
}
QFrame#Card {
    background: #1c2033;
    border: 1px solid #333a5a;
    border-radius: 10px;
}
QLabel#Muted {
    color: #98a0bf;
}
QLabel#KindBadge {
    background: #6f3bff;
    color: white;
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 8pt;
    font-weight: 700;
}
QLabel#SourceBadge {
    background: #2b2642;
    color: #ff73c8;
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 8pt;
    font-weight: 700;
}
QLabel#ArtifactBadge {
    background: #34264d;
    color: #c89cff;
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 8pt;
    font-weight: 700;
}
QLabel#StatusBar {
    background: #181b2a;
    color: #a6add0;
    border-top: 1px solid #343a58;
    padding: 6px 12px;
QFrame#HealthStrip {
    background: #191c2c;
    border: 1px solid #2a2f4a;
    border-radius: 8px;
}
}
"""


def _kind_color(kind: str) -> QColor:
    return {
        "gtk": QColor("#1a73e8"),
        "icons": QColor("#0f9d58"),
        "shell": QColor("#7b1fa2"),
        "cursors": QColor("#e64a19"),
    }.get(kind, QColor("#65758b"))


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


def _load_source_pixmap(record: ThemeRecord, width: int = 720, height: int = 380) -> QPixmap | None:
    def _normalize_url(url: str, base_url: str = "") -> str:
        u = html_lib.unescape((url or "").strip())
        if not u:
            return ""
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("/") and base_url:
            return urljoin(base_url, u)
        return u

    def _try_load(url: str, referer: str = "") -> QPixmap | None:
        if not url:
            return None
        headers = {
            "User-Agent": "linux-theme-manager/1.0",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            data = resp.read()
        qimg = QImage.fromData(data)
        if qimg.isNull():
            return None
        pix = QPixmap.fromImage(qimg)
        return pix.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)

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
                # srcset may contain comma-separated values with descriptors.
                first = raw.split(",", 1)[0].strip().split(" ", 1)[0].strip()
                u = _normalize_url(first, base_url)
                if not u:
                    continue
                low = u.lower()
                if any(ext in low for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", "images.pling.com", "opengraph.githubassets.com")):
                    candidates.append(u)

        # Keep order, remove duplicates.
        seen: set[str] = set()
        deduped: list[str] = []
        for c in candidates:
            if c in seen:
                continue
            seen.add(c)
            deduped.append(c)
        return deduped

    detail_url = (record.detail_url or "").strip()
    image_url = _normalize_url(record.thumbnail_url or "", detail_url)
    if image_url:
        try:
            pix = _try_load(image_url, referer=detail_url)
            if pix is not None:
                return pix
        except Exception:  # noqa: BLE001
            pass

    # Fallback: scrape detail page metadata for a real preview image.
    # Many sources expose screenshots through og:image/twitter:image even when
    # API thumbnail fields are missing or low quality.
    if detail_url:
        try:
            req = urllib.request.Request(
                detail_url,
                headers={
                    "User-Agent": "linux-theme-manager/1.0",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
                html = resp.read().decode("utf-8", errors="replace")

            for candidate in _extract_image_candidates(html, detail_url):
                try:
                    pix = _try_load(candidate, referer=detail_url)
                    if pix is not None:
                        return pix
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

    return None


def _generate_preview_pixmap(record: ThemeRecord, width: int = 720, height: int = 380) -> QPixmap | None:
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
    return pil_pix.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)


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


class PreviewDialog(QDialog):
    def __init__(self, parent: QWidget, record: ThemeRecord, on_install: Callable[[ThemeRecord, Optional["ThemeCardWidget"]], None]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Preview - {record.name}")
        self.resize(780, 640)
        self._record = record
        self._on_install = on_install

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel(record.name)
        title.setFont(QFont("Cantarell", 16, QFont.Bold))
        root.addWidget(title)

        self.mode_label = QLabel("Preview mode: loading")
        self.mode_label.setObjectName("Muted")
        root.addWidget(self.mode_label)

        self.image_label = QLabel("Loading preview...")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumHeight(390)
        self.image_label.setStyleSheet(
            "background:#1b2033;"
            "border-radius:10px;"
            "border:1px solid #384061;"
            "color:#9aa3c6;"
        )
        root.addWidget(self.image_label)

        desc = QTextEdit(record.description or record.summary or "No description available")
        desc.setReadOnly(True)
        desc.setMinimumHeight(90)
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

        self._load_preview()

    def _load_preview(self) -> None:
        try:
            pix = _load_source_pixmap(self._record)
            if pix is not None:
                self.mode_label.setText("Preview mode: Source screenshot")
                self.image_label.setPixmap(pix)
                return

            pix = _generate_preview_pixmap(self._record)
            if pix is not None:
                self.mode_label.setText("Preview mode: Generated preview")
                self.image_label.setPixmap(pix)
                return

            self.mode_label.setText("Preview mode: Unavailable")
            self.image_label.setText("Could not render preview")
        except Exception as exc:  # noqa: BLE001
            self.mode_label.setText("Preview mode: Error")
            self.image_label.setText(f"Preview failed: {exc}")

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


class ThemeCardWidget(QFrame):
    def __init__(self, record: ThemeRecord, on_install: Callable[[ThemeRecord, Optional["ThemeCardWidget"]], None], on_preview: Callable[[ThemeRecord], None]) -> None:
        super().__init__()
        self.record = record
        self._on_install = on_install
        self._on_preview = on_preview
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

        summary = QLabel(record.summary or "No summary")
        summary.setWordWrap(True)
        center.addWidget(summary)

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
            action_text = "Open Source"
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
    def __init__(self, app: "ThemeManagerQtApp") -> None:
        super().__init__()
        self.app = app
        self.thread_pool = app.thread_pool
        self._workers: list[Worker] = []
        self._install_progress: Optional[QProgressDialog] = None

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

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search themes...")
        self.search_edit.returnPressed.connect(self.search)
        bar.addWidget(self.search_edit, 1)

        self.kind_combo = QComboBox()
        self.kind_combo.addItems(["all", "gtk", "icons", "shell", "cursors"])
        bar.addWidget(self.kind_combo)

        self.source_combo = QComboBox()
        self.sources: list[str] = []
        self.reload_sources(preferred="github")
        bar.addWidget(self.source_combo)

        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self.search)
        bar.addWidget(search_btn)

        root.addWidget(bar_wrap)

        self.status = QLabel("Loading popular themes...")
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

    def load_default(self) -> None:
        self.status.setText("Loading popular themes from sources...")
        self._run_worker(
            search_source,
            "github", "", "all", 1,
            done=lambda recs: self._render(recs, "", "github"),
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
        self.status.setText("Searching...")
        self._run_worker(
            search_source,
            self.active_source(), self.search_edit.text().strip(), self.kind_combo.currentText(), 1,
            done=lambda recs: self._render(recs, self.search_edit.text().strip(), self.active_source()),
            failed=self._on_search_error,
        )

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
        if not records:
            self.status.setText("No themes found")
            return

        compatible: list[ThemeRecord] = []
        for record in records:
            if self._mark_support(record):
                compatible.append(record)

        if not compatible:
            distro = (self.app.env.distro or "this distro").title()
            self.status.setText(f"No compatible themes found for {distro}")
            return

        src_lbl = source.replace("-", " ").title()
        q = f' for "{query}"' if query else ""
        self.status.setText(f"{len(compatible)} compatible themes from {src_lbl}{q}")

        for record in compatible:
            card = ThemeCardWidget(record, self.install_record, self.open_preview)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)

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
            return supported

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
                    )
                else:
                    return
            else:
                return
        
        if record.artifact_type == "package":
            if card is not None:
                card.mark_installing()
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

        if card is not None:
            card.mark_installing()

        log.info("Install requested for '%s' from %s", record.name, record.download_url)
        self.app.set_status(f"Downloading {record.name}...")
        self._show_install_progress(record)
        self._run_worker(
            self._download_and_install,
            record,
            done=lambda names: self._on_install_done(record, names, card),
            failed=lambda err: self._on_install_error(record, err, card),
            progress=self._update_install_progress,
        )

    def _show_install_progress(self, record: ThemeRecord) -> None:
        if self._install_progress is not None:
            self._install_progress.close()
            self._install_progress.deleteLater()
        dialog = QProgressDialog(f"Downloading and installing {record.name}...", None, 0, 0, self)
        dialog.setWindowTitle("Installing Theme")
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setLabelText(f"Preparing install for {record.name}...")
        dialog.show()
        self._install_progress = dialog

    def _update_install_progress(self, message: str) -> None:
        self.app.set_status(message)
        log.info(message)
        if self._install_progress is not None:
            self._install_progress.setLabelText(message)

    @staticmethod
    def _download_and_install(record: ThemeRecord, progress_callback=None) -> list[str]:
        parsed = urlsplit(record.download_url)
        url_name = os.path.basename(unquote(parsed.path))
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", url_name).strip("._")
        if not safe_name:
            fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", record.name).strip("._") or "theme"
            safe_name = f"{fallback}.bin"

        tmp_dir = tempfile.mkdtemp(prefix="ltm_dl_")
        tmp_path = os.path.join(tmp_dir, safe_name)
        try:
            if progress_callback:
                progress_callback(f"Downloading {record.name} archive...")
            req = urllib.request.Request(record.download_url, headers={"User-Agent": "linux-theme-manager/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                with open(tmp_path, "wb") as handle:
                    handle.write(resp.read())
            if progress_callback:
                progress_callback(f"Download complete for {record.name}; extracting archive...")
            names = install_from_archive(tmp_path)
            if not names:
                raise ValueError(
                    "No installable theme directories were found in this archive. "
                    "The repository may contain source files rather than a packaged theme release."
                )
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
            self._install_progress.close()
            self._install_progress.deleteLater()
            self._install_progress = None
        if card is not None:
            card.mark_installed()
        label = ", ".join(names) if names else record.name
        log.info("Install completed for '%s': %s", record.name, label)
        self.app.set_status(f"Installed: {label}")
        self.app.installed_tab.refresh()
        QMessageBox.information(
            self,
            "Theme Installed",
            f"Installed successfully:\n\n{label}",
        )
        self._prompt_apply_theme(record, names)

    def _on_install_error(self, record: ThemeRecord, message: str, card: Optional[ThemeCardWidget]) -> None:
        if self._install_progress is not None:
            self._install_progress.close()
            self._install_progress.deleteLater()
            self._install_progress = None
        if card is not None:
            card.mark_error()
        log.error("Install failed for '%s': %s", record.name, message)
        self.app.set_status(f"Install failed for {record.name}: {message}")
        QMessageBox.critical(self, "Installation Failed", f"Could not install {record.name}\n\n{message}")

    def _prompt_apply_theme(self, record: ThemeRecord, names: list[str]) -> None:
        if not names:
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
        ok = False
        extension_msg = ""
        log.info("Applying installed artifact '%s' as %s", candidate, record.kind)
        if is_extension:
            ok, extension_msg = enable_extension_with_reason(candidate)
        elif record.kind == "gtk":
            ok = switch_gtk_theme(candidate)
        elif record.kind == "icons":
            ok = switch_icon_theme(candidate)
        elif record.kind == "cursors":
            ok = switch_cursor_theme(candidate)
        elif record.kind == "shell":
            ok = switch_shell_theme(candidate, self.app.env.desktop)

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

        ok = False
        if kind == "gtk":
            ok = switch_gtk_theme(previous)
        elif kind == "icons":
            ok = switch_icon_theme(previous)
        elif kind == "cursors":
            ok = switch_cursor_theme(previous)
        elif kind == "shell":
            ok = switch_shell_theme(previous, self.app.env.desktop)

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
        self._tab_layouts: dict[str, QVBoxLayout] = {}
        self._tab_widgets: dict[str, QWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        row = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter installed themes and extensions...")
        self.filter_edit.textChanged.connect(lambda _t: self._render_entries())
        row.addWidget(self.filter_edit, 1)

        self.show_extensions = QCheckBox("Show Extensions")
        self.show_extensions.setChecked(False)
        self.show_extensions.toggled.connect(lambda _v: self.refresh())
        row.addWidget(self.show_extensions)

        row.addStretch(1)
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

        self.summary.setText(f"{total} installed items")
        ext_tab = self._tab_widgets.get("extensions")
        if ext_tab is not None:
            ext_index = self.tabs.indexOf(ext_tab)
            if ext_index >= 0:
                self.tabs.setTabVisible(ext_index, self.show_extensions.isChecked())
        self._render_entries()

    def _render_entries(self) -> None:
        text = self.filter_edit.text().strip().lower()

        for lay in self._tab_layouts.values():
            while lay.count() > 1:
                item = lay.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()

        shown = 0
        for entry in self._entries:
            category = entry["category"]
            name = entry["name"]
            if text and text not in name.lower() and text not in category.lower():
                continue
            key = entry["key"]
            lay = self._tab_layouts.get(key)
            if lay is None:
                continue
            enabled = entry.get("enabled") == "1"
            lay.insertWidget(lay.count() - 1, self._build_item_card(category, name, enabled))
            shown += 1

        for key, lay in self._tab_layouts.items():
            if lay.count() == 1:
                empty = QLabel("No items in this section")
                empty.setObjectName("Muted")
                lay.insertWidget(0, empty)

        suffix = " (themes only)" if not self.show_extensions.isChecked() else ""
        self.summary.setText(
            f"{shown} items shown{suffix}" if text else f"{len(self._entries)} installed items{suffix}"
        )

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
        kind = QLabel(self._entry_key(category).upper())
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
        badges.addStretch(1)
        meta.addLayout(badges)

        row.addLayout(meta, 1)

        btns = QHBoxLayout()
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

    def _apply_item(self, category: str, name: str) -> None:
        ok = False
        if category.startswith("gtk"):
            ok = switch_gtk_theme(name)
        elif category.startswith("icons"):
            ok = switch_icon_theme(name)
        elif "cursor" in category:
            ok = switch_cursor_theme(name)
        elif category.startswith("extensions"):
            ok, _ = enable_extension_with_reason(name)
        elif "shell" in category:
            ok = switch_shell_theme(name, self.env.desktop)

        if ok:
            self.app.set_status(f"Switched to {name}")
            self.app.settings_tab.refresh()
            if category.startswith("extensions"):
                self.refresh()
        else:
            self.app.set_status(f"Could not switch to {name}")

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

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.info = QTextEdit()
        self.info.setReadOnly(True)
        root.addWidget(self.info, 1)

        source_wrap = QFrame()
        source_wrap.setStyleSheet(
            "QFrame {"
            "background:#1a1f32;"
            "border:1px solid #333a5a;"
            "border-radius:10px;"
            "padding:6px;"
            "}"
        )
        source_layout = QVBoxLayout(source_wrap)
        source_layout.setContentsMargins(8, 8, 8, 8)
        source_layout.setSpacing(6)

        source_title = QLabel("Custom Sources")
        source_title.setFont(QFont("Cantarell", 11, QFont.Bold))
        source_layout.addWidget(source_title)

        source_row = QHBoxLayout()
        self.src_owner = QLineEdit()
        self.src_owner.setPlaceholderText("GitHub owner/org (e.g. vinceliuice)")
        source_row.addWidget(self.src_owner, 2)

        self.src_label = QLineEdit()
        self.src_label.setPlaceholderText("Label (optional)")
        source_row.addWidget(self.src_label, 2)

        self.src_kind = QComboBox()
        self.src_kind.addItems(["all", "gtk", "icons", "shell", "cursors"])
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

        root.addWidget(source_wrap)

        refresh = QPushButton("Refresh Status")
        refresh.clicked.connect(self.refresh)
        root.addWidget(refresh)

        open_logs = QPushButton("Open Logs Folder")
        open_logs.setProperty("variant", "subtle")
        open_logs.style().unpolish(open_logs)
        open_logs.style().polish(open_logs)
        open_logs.clicked.connect(self._open_logs_folder)
        root.addWidget(open_logs)

        copy_diag = QPushButton("Copy Diagnostics")
        copy_diag.setProperty("variant", "subtle")
        copy_diag.style().unpolish(copy_diag)
        copy_diag.style().polish(copy_diag)
        copy_diag.clicked.connect(self._copy_diagnostics)
        root.addWidget(copy_diag)

        root.addStretch(1)

        self._refresh_sources()
        self.refresh()

    def refresh(self) -> None:
        current = get_current_themes()
        text = (
            f"Desktop: {self.env.desktop}\n"
            f"Distro: {self.env.distro}\n"
            f"Session: {'Wayland' if self.env.is_wayland else 'X11'}\n"
            f"Package manager: {self.env.package_manager}\n"
            f"gsettings: {'yes' if self.env.has_gsettings else 'no'}\n"
            f"Flatpak: {'yes' if self.env.has_flatpak else 'no'}\n\n"
            "Current themes:\n"
            f"  GTK: {current.get('gtk') or '(not set)'}\n"
            f"  Icons: {current.get('icons') or '(not set)'}\n"
            f"  Cursor: {current.get('cursor') or '(not set)'}\n"
            f"  Shell: {current.get('shell') or '(not set)'}\n"
        )
        self.info.setPlainText(text)

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

    def _refresh_sources(self) -> None:
        self.source_list.clear()
        for spec in list_custom_sources():
            item = QListWidgetItem(f"{spec.label}  ({spec.owner})  [{spec.kind}]")
            item.setData(Qt.UserRole, spec.name)
            self.source_list.addItem(item)

        self.app.available_tab.reload_sources()

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

        self.setWindowTitle("Linux Theme Manager")
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

        title_col = QVBoxLayout()
        title = QLabel("Linux Theme Manager")
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
        self.installed_tab = InstalledTab(self)
        self.settings_tab = SettingsTab(self)
        tabs.addTab(self.available_tab, "Available")
        tabs.addTab(self.installed_tab, "Installed")
        tabs.addTab(self.settings_tab, "Settings")
        tabs.currentChanged.connect(self._tab_changed)
        root.addWidget(tabs, 1)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("StatusBar")
        root.addWidget(self.status_label)

        self.setCentralWidget(central)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _tab_changed(self, idx: int) -> None:
        if idx == 1:
            self.installed_tab.refresh()
        elif idx == 2:
            self.settings_tab.refresh()


def launch_gui() -> None:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(APP_STYLE)
    window = ThemeManagerQtApp()
    window.show()
    app.exec()
