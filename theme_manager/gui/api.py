"""
Theme search API — tries the opendesktop/pling OCS API for gnome-look.org
content, and falls back gracefully to a curated set of mock records.
"""

import json
import ssl
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlencode

from ..logger import get_logger

log = get_logger(__name__)

_API_BASE = "https://api.pling.com/ocs/v1/content/data"
_TIMEOUT  = 8   # seconds

# gnome-look.org content category IDs (pling/opendesktop)
_CATEGORY_IDS: dict[str, str] = {
    "gtk":     "135",
    "icons":   "132",
    "shell":   "134",
    "cursors": "107",
}


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class ThemeRecord:
    id:            str
    name:          str
    summary:       str
    description:   str
    kind:          str   # "gtk" | "icons" | "shell" | "cursors"
    score:         float
    downloads:     int
    author:        str
    thumbnail_url: str
    download_url:  str   # direct archive URL; may be empty
    detail_url:    str
    updated:       str
    source:        str = "mock"   # "api" or "mock"
    artifact_type: str = "theme"  # "theme" | "extension" | "source"
    variants:      list | None = None  # [(name, url), ...] for multi-variant themes
    compatibility: str = ""        # e.g. "GNOME/XFCE" or distro label
    install_verified: bool = False  # True when install path is known reliable
    package_name: str = ""         # package identifier for package-manager installs
    install_method: str = "archive"  # archive | package-manager | manual
    supported: bool = True          # computed at runtime for current environment
    support_note: str = ""         # short reason shown in UI


# ── Built-in mock themes ────────────────────────────────────────────────────────

MOCK_THEMES: list[ThemeRecord] = [
    # GTK themes
    ThemeRecord(
        id="mock-gtk-001", kind="gtk",
        name="Orchis",
        summary="Material Design theme for GNOME",
        description=(
            "Orchis is a Material Design GTK theme supporting GTK 3, GTK 4, and GNOME Shell. "
            "Multiple colour variants (dark, light, manjaro, ubuntu …) are included."
        ),
        score=92.0, downloads=520_000, author="vinceliuice",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1357889/",
        updated="2024-03-01",
    ),
    ThemeRecord(
        id="mock-gtk-002", kind="gtk",
        name="WhiteSur GTK",
        summary="macOS Big Sur-like theme for GTK 3 & 4",
        description=(
            "WhiteSur brings a macOS Big Sur aesthetic to GNOME. "
            "Ships with light and dark variants and optional Firefox/GDM tweaks."
        ),
        score=88.0, downloads=380_000, author="vinceliuice",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1405756/",
        updated="2024-02-15",
    ),
    ThemeRecord(
        id="mock-gtk-003", kind="gtk",
        name="Nordic",
        summary="Dark GTK theme using the Nord colour palette",
        description=(
            "Nordic is a dark GTK theme inspired by the arctic Nordic landscapes "
            "and built around the popular Nord colour scheme."
        ),
        score=86.0, downloads=290_000, author="EliverLara",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1267246/",
        updated="2023-12-01",
    ),
    ThemeRecord(
        id="mock-gtk-004", kind="gtk",
        name="Fluent",
        summary="Windows 11 Fluent Design inspired GTK theme",
        description=(
            "Fluent brings the Windows 11 Fluent Design language to Linux desktops "
            "with a translucent, Mica-like appearance."
        ),
        score=84.0, downloads=210_000, author="vinceliuice",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1477941/",
        updated="2024-01-10",
    ),
    # Icon themes
    ThemeRecord(
        id="mock-icons-001", kind="icons",
        name="Papirus",
        summary="Flat material-style icon theme with 10 000+ icons",
        description=(
            "Papirus is a flat icon theme featuring a huge library of application-specific icons. "
            "Available in multiple colour variants and for KDE/GNOME/XFCE."
        ),
        score=95.0, downloads=750_000, author="PapirusDev",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1166289/",
        updated="2024-03-10",
    ),
    ThemeRecord(
        id="mock-icons-002", kind="icons",
        name="WhiteSur Icons",
        summary="macOS Big Sur-inspired icon theme",
        description=(
            "WhiteSur icon theme pairs perfectly with the WhiteSur GTK theme, "
            "providing macOS-style icons for common Linux applications."
        ),
        score=87.0, downloads=310_000, author="vinceliuice",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1405756/",
        updated="2024-02-10",
    ),
    ThemeRecord(
        id="mock-icons-003", kind="icons",
        name="Tela Circle",
        summary="Colourful circular icon theme",
        description=(
            "Tela Circle provides vibrant, circular icons based on the popular Numix style. "
            "Many folder colour variants are available."
        ),
        score=85.0, downloads=260_000, author="vinceliuice",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1279924/",
        updated="2023-11-20",
    ),
    ThemeRecord(
        id="mock-icons-004", kind="icons",
        name="Numix Circle",
        summary="Round vibrant icon theme (original)",
        description=(
            "Numix Circle is one of the most popular Linux icon themes, "
            "offering round, colourful icons for hundreds of applications."
        ),
        score=82.0, downloads=420_000, author="numixproject",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1167579/",
        updated="2023-09-01",
    ),
    # Shell themes
    ThemeRecord(
        id="mock-shell-001", kind="shell",
        name="Orchis Shell",
        summary="GNOME Shell theme matching the Orchis GTK theme",
        description=(
            "The GNOME Shell component included in the Orchis theme bundle. "
            "Requires the User Themes GNOME extension."
        ),
        score=90.0, downloads=200_000, author="vinceliuice",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1357889/",
        updated="2024-03-01",
    ),
    ThemeRecord(
        id="mock-shell-002", kind="shell",
        name="WhiteSur Shell",
        summary="macOS-like GNOME Shell theme",
        description=(
            "WhiteSur Shell mimics the macOS Big Sur top bar for GNOME. "
            "Pairs perfectly with the WhiteSur GTK and icon themes."
        ),
        score=86.0, downloads=170_000, author="vinceliuice",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1405756/",
        updated="2024-02-15",
    ),
    # Cursor themes
    ThemeRecord(
        id="mock-cursors-001", kind="cursors",
        name="Bibata Modern",
        summary="Material Design cursor theme (multiple colours)",
        description=(
            "Bibata is a small, material-style cursor theme available in Ice (blue), "
            "Classic (black), and Amber variants."
        ),
        score=91.0, downloads=340_000, author="ful1e5",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1197198/",
        updated="2024-02-28",
    ),
    ThemeRecord(
        id="mock-cursors-002", kind="cursors",
        name="McMojave Cursors",
        summary="macOS Mojave-inspired cursor set",
        description=(
            "A clean macOS Mojave-style cursor theme for Linux, "
            "available in light and dark colour variants."
        ),
        score=85.0, downloads=180_000, author="vinceliuice",
        thumbnail_url="", download_url="",
        detail_url="https://www.gnome-look.org/p/1355701/",
        updated="2023-10-15",
    ),
]


# ── API helpers ────────────────────────────────────────────────────────────────

_ARCHIVE_EXTS = (".tar.xz", ".tar.gz", ".zip", ".tgz", ".tar.bz2")


def _pick_download_url(files: list | dict) -> str:
    """Find the best direct download URL from a pling files list/dict."""
    if isinstance(files, dict):
        files = list(files.values())
    for f in files:
        name = (f.get("name") or "").lower()
        if any(name.endswith(ext) for ext in _ARCHIVE_EXTS):
            url = f.get("download_url") or f.get("downloadlink") or ""
            if url:
                return url
    return ""


def _pick_download_url_from_item(item: dict) -> str:
    """Pick download URL from flat pling item fields (downloadlinkN / downloadnameN).

    GNOME Look list responses use ``downloadlinkN`` / ``downloadnameN`` flat
    fields rather than a nested ``files`` list.  Prefer archive files over
    images; return the first matching non-image file URL.
    """
    # First try nested files list (older OCS format)
    files = item.get("files")
    if files:
        url = _pick_download_url(files)
        if url:
            return url

    # Flat downloadlinkN fields (current pling format)
    for i in range(1, 32):
        name = str(item.get(f"downloadname{i}") or "").lower().strip()
        link = str(item.get(f"downloadlink{i}") or "").strip()
        if not link:
            break
        # skip image-only files
        if any(name.endswith(img) for img in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")):
            continue
        if any(name.endswith(ext) for ext in _ARCHIVE_EXTS) or name:
            return link

    return ""


def _looks_like_image_url(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u.startswith(("http://", "https://", "//")):
        return False
    return any(ext in u for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")) or "images.pling.com" in u


def _pick_preview_url(item: dict) -> str:
    """Pick the best preview image URL from pling payload fields."""
    candidates = [
        item.get("previewpic2"),
        item.get("previewpic1"),
        item.get("smallpreviewpic2"),
        item.get("smallpreviewpic1"),
        item.get("preview2"),
        item.get("preview1"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and _looks_like_image_url(candidate):
            return candidate
    return ""


def _collect_download_variants(item: dict) -> list[tuple[str, str]]:
    """Collect all available downloadable file variants from a pling item.
    
    Returns [(filename, url), ...] tuples for all non-image downloadable files.
    """
    variants: list[tuple[str, str]] = []
    
    # First try nested files list (older OCS format)
    files = item.get("files")
    if files:
        if isinstance(files, dict):
            files = list(files.values())
        for f in files:
            name = (f.get("name") or "").lower()
            if any(name.endswith(img) for img in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")):
                continue
            if any(name.endswith(ext) for ext in _ARCHIVE_EXTS):
                url = f.get("download_url") or f.get("downloadlink") or ""
                if url:
                    variants.append((f.get("name", "Unknown"), url))
    
    # Flat downloadlinkN fields (current pling format)
    for i in range(1, 32):
        name = str(item.get(f"downloadname{i}") or "").lower().strip()
        link = str(item.get(f"downloadlink{i}") or "").strip()
        if not link:
            break
        # skip image-only files
        if any(name.endswith(img) for img in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")):
            continue
        if any(name.endswith(ext) for ext in _ARCHIVE_EXTS):
            display_name = str(item.get(f"downloadname{i}") or "Unknown").strip()
            variants.append((display_name, link))
    
    return variants


def _parse_response(items: list[dict], kind_hint: str) -> list[ThemeRecord]:
    """Parse API response items into ThemeRecord objects with variant information."""
    results: list[ThemeRecord] = []
    for item in items:
        variants = _collect_download_variants(item)
        # Only show variants list if there are multiple options
        variants_to_store = variants if len(variants) > 1 else None
        results.append(ThemeRecord(
            id=str(item.get("id", "")),
            name=item.get("name", "Unknown"),
            summary=item.get("summary", ""),
            description=item.get("description", ""),
            kind=kind_hint,
            score=_safe_float(item.get("score")),
            downloads=_safe_int(item.get("downloads")),
            author=item.get("owner", ""),
            thumbnail_url=_pick_preview_url(item),
            download_url=_pick_download_url_from_item(item),
            detail_url=item.get("detailpage") or "",
            updated=item.get("changed") or item.get("created") or "",
            source="api",
            variants=variants_to_store,
        ))
    return results


def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ── Public API ─────────────────────────────────────────────────────────────────

def search_themes(query: str, kind: str = "all", page: int = 1) -> list[ThemeRecord]:
    """
    Search for themes.

    1. Attempts the pling/opendesktop OCS API (gnome-look.org content).
    2. Falls back to filtering the built-in mock list on any error.
    """
    kind_hint = kind if kind != "all" else "gtk"
    params: dict[str, str] = {
        "format":   "json",
        "search":   query,
        "pagesize": "20",
        "page":     str(page),
    }
    cat_id = _CATEGORY_IDS.get(kind)
    if cat_id:
        params["categories"] = cat_id

    url = _API_BASE + "?" + urlencode(params)
    log.debug("API request: %s", url)

    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "linux-theme-manager/1.0"},
        )
        with urllib.request.urlopen(req, context=ctx, timeout=_TIMEOUT) as resp:
            raw: dict = json.loads(resp.read().decode("utf-8", errors="replace"))

        items = raw.get("ocs", {}).get("data") or []
        if isinstance(items, dict):
            items = list(items.values())
        if items:
            return _parse_response(items, kind_hint)

    except Exception as exc:  # noqa: BLE001
        log.warning("API search failed (%s) – using mock data.", exc)

    # Fallback: filter from mock list
    q = query.lower()
    return [
        t for t in MOCK_THEMES
        if (kind == "all" or t.kind == kind)
        and (not q or q in t.name.lower() or q in t.summary.lower())
    ]
