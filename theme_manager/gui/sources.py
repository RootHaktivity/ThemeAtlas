"""
Multi-source theme registry.

Sources
-------
gnome-look  –  pling/opendesktop OCS API (api.pling.com)
               Live scores, download counts, direct archive downloads.
github      –  GitHub Repositories API (api.github.com)
               Popular theme repos filtered by topic tag.
               No direct download URL; opens repo page in browser.

The registry searches all enabled sources and deduplicates by name.
"""

from __future__ import annotations

import json
import base64
import re
import shutil
import subprocess
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from abc import ABC, abstractmethod
from urllib.parse import urlencode, urlsplit

from .api import (
    MOCK_THEMES, ThemeRecord,
    _API_BASE, _CATEGORY_IDS, _TIMEOUT,
    _parse_response, _safe_float, _safe_int,
)
from ..logger import get_logger
from ..network import fetch_json

log = get_logger(__name__)

_GITHUB_API = "https://api.github.com/search/repositories"
_CFG_DIR = Path.home() / ".config" / "themeatlas"
_CUSTOM_SOURCES_FILE = _CFG_DIR / "custom_sources.json"

# Map theme kind → GitHub topic tag
_KIND_TO_TOPIC: dict[str, str] = {
    "gtk":     "gtk-theme",
    "icons":   "icon-theme",
    "shell":   "gnome-shell-theme",
    "cursors": "cursor-theme",
}
_TOPIC_TO_KIND: dict[str, str] = {v: k for k, v in _KIND_TO_TOPIC.items()}
_VALID_KINDS: set[str] = {"all", "gtk", "icons", "shell", "cursors", "app/tooling"}

_GITHUB_THEME_HINTS: dict[str, tuple[str, ...]] = {
    "gtk": ("gtk theme", "theme", "adwaita", "orchis", "whitesur", "materia", "arc"),
    "icons": ("icon theme", "icons", "papirus", "numix", "breeze"),
    "shell": ("gnome shell theme", "shell theme", "user-theme", "user theme"),
    "cursors": ("cursor theme", "cursors", "xcursor", "bibata"),
}

_GITHUB_NON_THEME_HINTS: tuple[str, ...] = (
    "application", "app", "gtk app", "desktop app", "tool", "manager", "editor",
    "plugin", "daemon", "service", "library", "sdk", "cli",
)

_GITHUB_APP_HINTS: tuple[str, ...] = (
    "application", "app", "desktop app", "tool", "utility", "cli", "terminal",
    "manager", "editor", "extension", "gtk4", "qt6", "libadwaita",
)

_DESKTOP_CUSTOMIZATION_ALLOWLIST: tuple[str, ...] = (
    "gradience",
    "gnome-tweaks",
    "gnome-tweak-tool",
    "gnome-control-center",
    "nwg-look",
    "lxappearance",
    "qt5ct",
    "qt6ct",
    "kvantum-manager",
    "ocs-url",
    "plasma-browser-integration",
)

_DESKTOP_CUSTOMIZATION_CONTEXT_HINTS: tuple[str, ...] = (
    "theme", "theming", "appearance", "style", "accent", "adwaita",
    "icon", "cursor", "wallpaper", "desktop customization", "customization",
    "gnome", "kde", "plasma", "xfce", "cinnamon", "mate", "budgie",
    "shell", "gtk", "libadwaita", "kvantum", "qt5ct", "qt6ct", "lxappearance", "nwg-look",
)

_DESKTOP_CUSTOMIZATION_ACTION_HINTS: tuple[str, ...] = (
    "customize", "customizer", "tweak", "tweaks", "switch", "switcher", "manager",
    "installer", "editor", "configurator", "chooser", "picker", "settings", "control center",
    "theme editor", "color scheme", "palette",
)

_DESKTOP_CUSTOMIZATION_GUI_HINTS: tuple[str, ...] = (
    "gui", "gtk", "gtk3", "gtk4", "qt", "qt5", "qt6", "libadwaita",
    "desktop app", "control center", "settings", "gnome", "kde", "plasma", "xfce",
)

_GITHUB_THEMING_TOOL_HINTS: tuple[str, ...] = (
    "tool", "utility", "manager", "switcher", "installer", "tweak", "tweaks",
    "editor", "generator", "builder", "configurator", "customizer", "patcher", "extension",
)

SORT_MODES: tuple[str, ...] = (
    "relevance",
    "highest-rated",
    "popular",
    "trending",
)

_DESKTOP_CUSTOMIZATION_NEGATIVE_HINTS: tuple[str, ...] = (
    "monitor cpu", "gpu usage", "network usage", "system monitor", "video player", "music player",
    "browser", "mail client", "messaging", "diff and merge", "ide", "compiler", "sdk",
    "dotfiles", "ansible role", "ansible-role",
)

_DESKTOP_CUSTOMIZATION_CLI_HINTS: tuple[str, ...] = (
    " cli", "command line", "terminal", "shell script", "bash script", "daemon", "library", "headless",
)


def _slug_parts(*values: str) -> set[str]:
    parts: set[str] = set()
    for value in values:
        for raw in re.split(r"[^a-z0-9]+", (value or "").lower()):
            if raw:
                parts.add(raw)
    return parts


def _matches_customization_allowlist(*values: str) -> bool:
    joined = " ".join((value or "").lower() for value in values)
    if any(token in joined for token in _DESKTOP_CUSTOMIZATION_ALLOWLIST):
        return True

    slugged = _slug_parts(*values)
    return any(token in slugged for token in {"gradience", "qt5ct", "qt6ct", "lxappearance"})


def _is_probably_installable_theme_repo(item: dict, kind: str) -> bool:
    """Heuristic filter to keep likely installable theme repos and skip app/code repos."""
    name = str(item.get("name") or "").lower()
    desc = str(item.get("description") or "").lower()
    topics = {str(t).lower() for t in (item.get("topics") or [])}
    name_desc_text = f"{name} {desc}".strip()
    text = f"{name_desc_text} {' '.join(sorted(topics))}".strip()

    if not text:
        return False

    non_theme_hit = any(token in text for token in _GITHUB_NON_THEME_HINTS)

    hints = _GITHUB_THEME_HINTS.get(kind, ())
    theme_hit = any(token in text for token in hints)
    name_desc_theme_hit = any(token in name_desc_text for token in hints)

    # Topic hit helps, but should not override clear app/tool signals.
    requested_topic = _KIND_TO_TOPIC.get(kind)
    topic_hit = bool(requested_topic and requested_topic in topics)

    # If it looks like a generic app/tool and has no theme markers, skip it.
    if non_theme_hit and not theme_hit:
        return False

    # Shell repos are noisy on GitHub; require an explicit shell-theme marker.
    if kind == "shell":
        return name_desc_theme_hit

    return theme_hit or topic_hit


def _is_probably_app_tool_repo(item: dict) -> bool:
    """Heuristic filter for app/tool repositories when searching app/tooling kind."""
    name = str(item.get("name") or "").lower()
    desc = str(item.get("description") or "").lower()
    topics = {str(t).lower() for t in (item.get("topics") or [])}
    text = f"{name} {desc} {' '.join(sorted(topics))}".strip()
    if not text:
        return False

    full_name = str(item.get("full_name") or "").lower()
    if _matches_customization_allowlist(name, full_name, desc, " ".join(sorted(topics))):
        return True

    # Extension repositories should be eligible for Apps/Extensions discovery.
    if "gnome-shell-extension" in topics:
        return True
    if "gnome shell extension" in desc or "shell extension" in desc:
        return True

    if any(token in text for token in ("propaganda", "community", "discussion", "anime", "game")):
        return False

    if any(token in text for token in _DESKTOP_CUSTOMIZATION_NEGATIVE_HINTS):
        return False

    if any(token in text for token in _DESKTOP_CUSTOMIZATION_CLI_HINTS):
        return False

    theming_hit = any(token in text for token in _DESKTOP_CUSTOMIZATION_CONTEXT_HINTS)
    theming_action_hit = any(token in text for token in _DESKTOP_CUSTOMIZATION_ACTION_HINTS)
    gui_hit = any(token in text for token in _DESKTOP_CUSTOMIZATION_GUI_HINTS)
    app_hit = any(token in text for token in _GITHUB_APP_HINTS)
    tool_hit = any(token in text for token in _GITHUB_THEMING_TOOL_HINTS)

    looks_like_theme_pack = (
        name.endswith("-theme")
        or "icon-theme" in name
        or "cursor-theme" in name
    ) and not tool_hit
    if looks_like_theme_pack:
        return False

    return theming_hit and theming_action_hit and gui_hit and (app_hit or tool_hit)


# ── Shared HTTP helper ─────────────────────────────────────────────────────────

def _http_get(url: str, extra_headers: dict[str, str] | None = None) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError(f"Unsupported URL scheme for source fetch: {url}")
    return fetch_json(
        url,
        extra_headers=extra_headers,
        timeout=_TIMEOUT,
        retries=2,
        cache_ttl_seconds=90,
    )


def _extract_pling_items(raw: dict) -> list[dict]:
    """Return content items from either OCS-nested or flat pling responses."""
    if not isinstance(raw, dict):
        return []

    items = None
    ocs = raw.get("ocs")
    if isinstance(ocs, dict):
        items = ocs.get("data")
    elif "data" in raw:
        items = raw.get("data")

    if isinstance(items, dict):
        return [v for v in items.values() if isinstance(v, dict)]
    if isinstance(items, list):
        return [v for v in items if isinstance(v, dict)]
    return []


def _check_github_health() -> tuple[str, str]:
    """Lightweight GitHub API health check using the rate_limit endpoint."""
    try:
        raw = _http_get(
            "https://api.github.com/rate_limit",
            extra_headers={"Accept": "application/vnd.github+json"},
        )
        core = raw.get("rate", raw.get("resources", {}).get("core", {}))
        remaining = int(core.get("remaining", 0))
        limit = int(core.get("limit", 60))
        if remaining == 0:
            return ("rate_limited", "0 req remaining — try later")
        status = "rate_limited" if remaining < 10 else "online"
        return (status, f"{remaining}/{limit} req/hr")
    except Exception as exc:  # noqa: BLE001
        return ("offline", str(exc)[:120])


def _decode_github_contents_json(raw: dict) -> dict:
    """Decode a GitHub contents API response containing a JSON file."""
    encoded = raw.get("content")
    if not isinstance(encoded, str) or not encoded.strip():
        return {}
    try:
        decoded = base64.b64decode(encoded.encode("utf-8"), validate=False)
        data = json.loads(decoded.decode("utf-8", errors="ignore"))
    except (ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _fetch_github_extension_shell_versions(full_name: str, branch: str) -> list[str]:
    """Best-effort fetch of extension shell-version values from repository metadata.json."""
    if not full_name:
        return []

    candidate_paths = (
        "metadata.json",
        "src/metadata.json",
        "extension/metadata.json",
    )

    for rel_path in candidate_paths:
        url = f"https://api.github.com/repos/{full_name}/contents/{rel_path}?ref={branch or 'main'}"
        try:
            raw = _http_get(url, extra_headers={"Accept": "application/vnd.github+json"})
        except Exception:
            continue
        data = _decode_github_contents_json(raw)
        versions = data.get("shell-version")
        if isinstance(versions, list):
            normalized: list[str] = []
            for value in versions:
                major = str(value).strip().split(".", 1)[0]
                if major and major not in normalized:
                    normalized.append(major)
            if normalized:
                return normalized
    return []


def _looks_like_gnome_extension_repo(name: str, description: str, topics_lower: set[str]) -> bool:
    """Heuristic to decide whether probing metadata.json for extension compatibility is worthwhile."""
    if "gnome-shell-extension" in topics_lower:
        return True

    text = f"{name} {description}".lower()
    hints = (
        "gnome shell extension",
        "shell extension",
        "quick settings",
        "extension manager",
        "metadata.json",
    )
    if any(token in text for token in hints):
        return True

    return name.lower().endswith("-extension")


# ── Source base class ──────────────────────────────────────────────────────────

class ThemeSource(ABC):
    name: str   # internal identifier used as combobox value
    label: str  # human-readable display name

    @abstractmethod
    def search(self, query: str, kind: str = "all", page: int = 1) -> list[ThemeRecord]:
        """Return a list of ThemeRecords matching *query* and *kind*."""
        ...

    def health_check(self) -> tuple[str, str]:
        """Probe source reachability. Returns (status, message).

        *status* is one of ``'online'``, ``'rate_limited'``, or ``'offline'``.
        Subclasses should override with a lightweight, dedicated check.
        """
        try:
            self.search("", page=1)
            return ("online", "Ready")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "rate" in msg.lower():
                return ("rate_limited", msg[:120])
            return ("offline", msg[:120])


# ── GNOME Look (pling/opendesktop OCS) ────────────────────────────────────────

class GnomeLookSource(ThemeSource):
    name  = "gnome-look"
    label = "GNOME Look"

    def search(self, query: str, kind: str = "all", page: int = 1) -> list[ThemeRecord]:
        kind_hint = kind if kind not in ("", "all") else "gtk"
        params: dict[str, str] = {
            "format":   "json",
            "search":   query,
            "pagesize": "24",
            "page":     str(page),
        }
        cat_id = _CATEGORY_IDS.get(kind)
        if cat_id:
            params["categories"] = cat_id

        url = _API_BASE + "?" + urlencode(params)
        log.debug("GnomeLook request: %s", url)

        raw = _http_get(url)
        items = _extract_pling_items(raw)

        if not items:
            if isinstance(raw, dict):
                status = str(raw.get("status", "")).lower()
                statuscode = str(raw.get("statuscode", ""))
                if status == "ok" or statuscode == "100":
                    return []
                msg = str(raw.get("message") or "").strip()
                if msg:
                    raise ValueError(f"GNOME Look API: {msg}")
            raise ValueError("Empty response from GNOME Look API")

        records = _parse_response(items, kind_hint)
        for r in records:
            r.source = "gnome-look"
        return records

    def health_check(self) -> tuple[str, str]:
        try:
            params = {"format": "json", "search": "gtk", "pagesize": "1", "page": "1"}
            raw = _http_get(_API_BASE + "?" + urlencode(params))

            items = _extract_pling_items(raw)
            if items:
                return ("online", "Ready")

            # Flat pling response style: {status, statuscode, message, totalitems, data}
            if isinstance(raw, dict):
                status = str(raw.get("status", "")).lower()
                statuscode = str(raw.get("statuscode", ""))
                if status == "ok" or statuscode == "100":
                    return ("online", "API reachable")
                msg = str(raw.get("message") or "").strip()
                if msg:
                    return ("offline", msg[:120])

            return ("offline", "API returned no content")
        except Exception as exc:  # noqa: BLE001
            return ("offline", str(exc)[:120])


# ── GitHub Repositories ────────────────────────────────────────────────────────

class GitHubSource(ThemeSource):
    name  = "github"
    label = "GitHub"

    @staticmethod
    def _fetch_search_items(query_text: str, *, sort: str = "stars", per_page: int = 20, page: int = 1) -> list[dict]:
        params = {
            "q": query_text,
            "sort": sort,
            "order": "desc",
            "per_page": str(per_page),
            "page": str(page),
        }
        url = _GITHUB_API + "?" + urlencode(params)
        log.debug("GitHub request: %s", url)
        raw = _http_get(url, extra_headers={"Accept": "application/vnd.github+json"})
        if "items" not in raw:
            msg = raw.get("message", "Unknown GitHub API error")
            raise RuntimeError(f"GitHub API: {msg}")
        return list(raw.get("items", []))

    @staticmethod
    def _default_app_tooling_queries() -> list[str]:
        return [
            "topic:gnome-shell-extension stars:>10",
            "gnome shell extension stars:>50",
            "gtk theme manager stars:>20",
            "gnome tweaks stars:>20",
            "nwg-look stars:>10",
            "lxappearance stars:>10",
            "qt5ct stars:>10",
            "qt6ct stars:>10",
            "kvantum-manager stars:>10",
            "icon theme manager linux stars:>10",
        ]

    def search(self, query: str, kind: str = "all", page: int = 1) -> list[ThemeRecord]:
        raw_items: list[dict] = []
        q_parts: list[str] = [query.strip()] if query.strip() else []
        if kind == "app/tooling":
            if not q_parts:
                for focused_query in self._default_app_tooling_queries():
                    raw_items.extend(self._fetch_search_items(focused_query, per_page=8, page=page))
            else:
                q_parts.append("stars:>20")
        else:
            # For "all" just search the most popular gtk-theme topic to avoid hammering
            # the API with four requests; specific kinds do a targeted topic search.
            topic = _KIND_TO_TOPIC.get(kind, "gtk-theme")
            q_parts.append(f"topic:{topic}")
            q_parts.append("stars:>50")
        if not raw_items:
            raw_items = self._fetch_search_items(" ".join(q_parts), per_page=20, page=page)

        results: list[ThemeRecord] = []
        seen_ids: set[str] = set()
        for item in raw_items:
            item_id = str(item.get("id") or "")
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            record = self._to_record(item, kind)
            if record.kind == "app/tooling":
                if _is_probably_app_tool_repo(item):
                    results.append(record)
                else:
                    log.debug(
                        "GitHub app/tool candidate filtered: %s",
                        item.get("full_name") or item.get("name") or "(unknown)",
                    )
            elif _is_probably_installable_theme_repo(item, record.kind):
                results.append(record)
            else:
                log.debug(
                    "GitHub candidate filtered as likely non-theme repo: %s",
                    item.get("full_name") or item.get("name") or "(unknown)",
                )
        return results

    def health_check(self) -> tuple[str, str]:
        return _check_github_health()

    @staticmethod
    def _to_record(item: dict, kind_hint: str) -> ThemeRecord:
        # Infer kind from repo topics
        topics = item.get("topics", [])
        kind = kind_hint if kind_hint != "all" else "gtk"
        for t in topics:
            if t in _TOPIC_TO_KIND:
                kind = _TOPIC_TO_KIND[t]
                break
        if kind_hint == "app/tooling":
            kind = "app/tooling"

        raw_name = item.get("name", "unknown")
        display   = raw_name.replace("-", " ").replace("_", " ").title()
        full_name = item.get("full_name", "")
        og_image = f"https://opengraph.githubassets.com/1/{full_name}" if full_name else ""
        default_branch = item.get("default_branch") or "main"
        archive_url = (
            f"https://github.com/{full_name}/archive/refs/heads/{default_branch}.zip"
            if full_name else ""
        )
        stars     = _safe_int(item.get("stargazers_count"))
        description = item.get("description") or ""

        artifact_type = "theme"
        topics_lower = {str(t).lower() for t in topics}
        if "gnome-shell-extension" in topics_lower or "extension" in description.lower():
            artifact_type = "extension"
        if kind == "app/tooling" and artifact_type != "extension":
            artifact_type = "application"
            # Keep archive_url (GitHub source ZIP) so the source-build install flow can use it

        category = ""
        compatibility = ""
        support_note = ""
        if kind == "app/tooling":
            category = _infer_app_tooling_category(f"{raw_name} {description} {' '.join(sorted(topics_lower))}")
            if artifact_type == "application":
                support_note = "Build from source"

        should_probe_extension_metadata = (
            bool(full_name)
            and (
                artifact_type == "extension"
                or (kind == "app/tooling" and _looks_like_gnome_extension_repo(raw_name, description, topics_lower))
            )
        )

        shell_versions: list[str] = []
        if should_probe_extension_metadata:
            shell_versions = _fetch_github_extension_shell_versions(full_name, default_branch)
            if shell_versions:
                artifact_type = "extension"
                compatibility = f"GNOME Shell {', '.join(shell_versions)}"
            elif artifact_type == "extension":
                compatibility = "GNOME Shell (version not declared)"

        return ThemeRecord(
            id           = f"gh-{item.get('id', '')}",
            name         = display,
            summary      = _compact_summary(description or f"GitHub · {raw_name}"),
            description  = description,
            kind         = kind,
            score        = round(stars / 1000, 1),   # scale ★ to ~0-100
            downloads    = _safe_int(item.get("forks_count")),
            author       = item.get("owner", {}).get("login", ""),
            thumbnail_url = og_image,
            download_url  = archive_url,
            detail_url    = item.get("html_url", ""),
            updated       = (item.get("pushed_at") or "")[:10],
            source        = "github",
            artifact_type = artifact_type,
            category      = category,
            compatibility = compatibility,
            install_method = (
                "source"
                if (kind == "app/tooling" and artifact_type == "application")
                else "archive"
            ),
            install_verified = True,
            support_note = support_note,
        )


class GitHubOwnerSource(ThemeSource):
    """Custom source that searches a specific GitHub user/org."""

    def __init__(self, name: str, label: str, owner: str, default_kind: str = "all") -> None:
        self.name = name
        self.label = label
        self.owner = owner
        self.default_kind = default_kind if default_kind in _KIND_TO_TOPIC or default_kind == "all" else "all"

    def search(self, query: str, kind: str = "all", page: int = 1) -> list[ThemeRecord]:
        effective_kind = kind if kind != "all" else self.default_kind
        topic = _KIND_TO_TOPIC.get(effective_kind, "gtk-theme")

        q_parts: list[str] = []
        if query.strip():
            q_parts.append(query.strip())
        q_parts.append(f"user:{self.owner}")
        q_parts.append(f"topic:{topic}")
        q_parts.append("stars:>2")

        params = {
            "q": " ".join(q_parts),
            "sort": "updated",
            "order": "desc",
            "per_page": "20",
            "page": str(page),
        }
        url = _GITHUB_API + "?" + urlencode(params)
        log.debug("GitHub owner source request (%s): %s", self.owner, url)

        raw = _http_get(url, extra_headers={"Accept": "application/vnd.github+json"})
        if "items" not in raw:
            msg = raw.get("message", "Unknown GitHub API error")
            raise RuntimeError(f"GitHub API: {msg}")

        results: list[ThemeRecord] = []
        for item in raw.get("items", []):
            rec = GitHubSource._to_record(item, effective_kind)
            rec.source = self.name
            results.append(rec)
        return results

    def health_check(self) -> tuple[str, str]:
        return _check_github_health()


_PACKAGE_THEME_HINTS: dict[str, tuple[str, ...]] = {
    "gtk": ("gtk", "gnome", "adwaita", "materia", "arc-theme"),
    "icons": ("icon", "papirus", "numix", "breeze-icon"),
    "shell": ("gnome-shell", "shell-theme", "user-theme"),
    "cursors": ("cursor", "xcursor", "bibata"),
    "app/tooling": (
        "tweak", "tweaks", "appearance", "theme manager", "theme switcher", "theme editor",
        "desktop customization", "customization", "wallpaper", "icon picker", "cursor picker",
        "kvantum", "qt5ct", "qt6ct", "lxappearance", "nwg-look", "control center", "settings",
    ),
}

_APP_TOOLING_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "appearance": ("theme", "appearance", "style", "accent", "color", "palette", "adwaita", "kvantum"),
    "icons & cursors": ("icon", "icons", "cursor", "cursors", "pointer"),
    "shell & panel": ("shell", "panel", "dock", "launcher", "plasma", "kwin"),
    "wallpaper": ("wallpaper", "background", "slideshow"),
    "settings": ("tweak", "tweaks", "settings", "control center", "desktop", "gnome", "kde", "xfce"),
    "utilities": ("switcher", "manager", "editor", "installer", "chooser", "picker", "customizer", "tool"),
}


def _is_probably_desktop_customization_tool_text(text: str) -> bool:
    value = text.lower()
    if _matches_customization_allowlist(value):
        return True
    if any(token in value for token in _DESKTOP_CUSTOMIZATION_NEGATIVE_HINTS):
        return False
    if any(token in value for token in _DESKTOP_CUSTOMIZATION_CLI_HINTS):
        return False
    context_hit = any(token in value for token in _DESKTOP_CUSTOMIZATION_CONTEXT_HINTS)
    action_hit = any(token in value for token in _DESKTOP_CUSTOMIZATION_ACTION_HINTS)
    gui_hit = any(token in value for token in _DESKTOP_CUSTOMIZATION_GUI_HINTS)
    return context_hit and action_hit and gui_hit


def _infer_kind_from_text(text: str) -> str:
    t = text.lower()
    if any(k in t for k in _PACKAGE_THEME_HINTS["cursors"]):
        return "cursors"
    if any(k in t for k in _PACKAGE_THEME_HINTS["icons"]):
        return "icons"
    if any(k in t for k in _PACKAGE_THEME_HINTS["shell"]):
        return "shell"
    if _is_probably_desktop_customization_tool_text(t):
        return "app/tooling"
    return "gtk"


def _matches_kind(text: str, kind: str) -> bool:
    if kind in ("", "all"):
        return True
    if kind == "app/tooling":
        return _is_probably_desktop_customization_tool_text(text)
    return any(k in text.lower() for k in _PACKAGE_THEME_HINTS.get(kind, ()))


def _infer_app_tooling_category(text: str) -> str:
    value = text.lower()
    for category, hints in _APP_TOOLING_CATEGORY_HINTS.items():
        if any(token in value for token in hints):
            return category
    return "utilities"


def _compact_summary(text: str, max_chars: int = 220) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_chars:
        return compact

    cut = max_chars - 1
    boundary = compact.rfind(" ", 0, cut)
    if boundary < int(max_chars * 0.6):
        boundary = cut
    return compact[:boundary].rstrip(" ,.;:-") + "..."


def _parse_record_date(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _trending_score(record: ThemeRecord) -> float:
    now = datetime.now()
    updated_dt = _parse_record_date(record.updated)
    days_old = (now - updated_dt).days if updated_dt else 9999
    recency_boost = max(0.0, min(60.0, 60.0 - float(days_old)))

    stars_estimate = max(0.0, float(record.score or 0.0) * 1000.0)
    downloads = max(0.0, float(record.downloads or 0.0))
    return (stars_estimate * 0.65) + (downloads * 0.35) + (recency_boost * 5.0)


def sort_records(records: list[ThemeRecord], mode: str = "relevance") -> list[ThemeRecord]:
    """Return a sorted copy of records according to the selected ranking mode."""
    normalized = (mode or "relevance").strip().lower()
    if normalized not in SORT_MODES:
        normalized = "relevance"

    out = list(records)
    if normalized == "relevance":
        return out

    if normalized == "highest-rated":
        return sorted(
            out,
            key=lambda r: (
                float(r.score or 0.0),
                int(r.downloads or 0),
                _parse_record_date(r.updated) or datetime.min,
                (r.name or "").lower(),
            ),
            reverse=True,
        )

    if normalized == "popular":
        return sorted(
            out,
            key=lambda r: (
                int(r.downloads or 0),
                float(r.score or 0.0),
                _parse_record_date(r.updated) or datetime.min,
                (r.name or "").lower(),
            ),
            reverse=True,
        )

    # trending
    return sorted(
        out,
        key=lambda r: (
            _trending_score(r),
            float(r.score or 0.0),
            int(r.downloads or 0),
            _parse_record_date(r.updated) or datetime.min,
            (r.name or "").lower(),
        ),
        reverse=True,
    )


class AptSource(ThemeSource):
    name = "apt"
    label = "APT Packages"

    def search(self, query: str, kind: str = "all", page: int = 1) -> list[ThemeRecord]:
        if not shutil.which("apt-cache"):
            return []

        term = query.strip() or "theme"
        try:
            proc = subprocess.run(
                ["apt-cache", "search", term],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return []

        if proc.returncode != 0:
            return []

        out: list[ThemeRecord] = []
        for idx, line in enumerate(proc.stdout.splitlines()[:120], start=1):
            if " - " not in line:
                continue
            pkg, desc = line.split(" - ", 1)
            text = f"{pkg} {desc}".lower()
            if not any(token in text for token in ("theme", "icon", "cursor", "gnome-shell", "gtk")) and not _is_probably_desktop_customization_tool_text(text):
                continue
            if not _matches_kind(text, kind):
                continue
            k = _infer_kind_from_text(text)
            category = _infer_app_tooling_category(text) if k == "app/tooling" else ""
            display = re.sub(r"[-_]+", " ", pkg).strip().title()
            out.append(ThemeRecord(
                id=f"apt-{pkg}",
                name=display,
                summary=_compact_summary(desc),
                description=desc,
                kind=k,
                score=0.0,
                downloads=0,
                author="distro",
                thumbnail_url="",
                download_url="",
                detail_url="",
                updated="",
                source="apt",
                artifact_type="package",
                category=category,
                compatibility="Debian/Ubuntu",
                install_verified=True,
                package_name=pkg,
                install_method="package-manager",
            ))
        return out

    def health_check(self) -> tuple[str, str]:
        return ("online", "apt-cache") if shutil.which("apt-cache") else ("offline", "apt-cache missing")


class PacmanSource(ThemeSource):
    name = "pacman"
    label = "Pacman Packages"

    def search(self, query: str, kind: str = "all", page: int = 1) -> list[ThemeRecord]:
        if not shutil.which("pacman"):
            return []

        term = query.strip() or "theme"
        try:
            proc = subprocess.run(
                ["pacman", "-Ss", term],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return []

        if proc.returncode != 0:
            return []

        out: list[ThemeRecord] = []
        lines = proc.stdout.splitlines()
        i = 0
        while i < len(lines) and len(out) < 120:
            header = lines[i].strip()
            desc = lines[i + 1].strip() if i + 1 < len(lines) else ""
            i += 2
            if "/" not in header:
                continue
            left = header.split()[0]
            if "/" not in left:
                continue
            _repo, pkg = left.split("/", 1)
            text = f"{pkg} {desc}".lower()
            if not any(token in text for token in ("theme", "icon", "cursor", "gnome-shell", "gtk")) and not _is_probably_desktop_customization_tool_text(text):
                continue
            if not _matches_kind(text, kind):
                continue
            k = _infer_kind_from_text(text)
            category = _infer_app_tooling_category(text) if k == "app/tooling" else ""
            display = re.sub(r"[-_]+", " ", pkg).strip().title()
            out.append(ThemeRecord(
                id=f"pacman-{pkg}",
                name=display,
                summary=_compact_summary(desc),
                description=desc,
                kind=k,
                score=0.0,
                downloads=0,
                author="distro",
                thumbnail_url="",
                download_url="",
                detail_url="",
                updated="",
                source="pacman",
                artifact_type="package",
                category=category,
                compatibility="Arch",
                install_verified=True,
                package_name=pkg,
                install_method="package-manager",
            ))
        return out

    def health_check(self) -> tuple[str, str]:
        return ("online", "pacman") if shutil.which("pacman") else ("offline", "pacman missing")


@dataclass
class CustomSourceSpec:
    name: str
    label: str
    source_type: str
    owner: str
    kind: str = "all"
    enabled: bool = True


def _slug(value: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean.strip("-") or "source"


def _load_custom_specs() -> list[CustomSourceSpec]:
    if not _CUSTOM_SOURCES_FILE.exists():
        return []
    try:
        raw = json.loads(_CUSTOM_SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read custom sources file: %s", exc)
        return []

    specs: list[CustomSourceSpec] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            spec = CustomSourceSpec(
                name=str(item.get("name", "")),
                label=str(item.get("label", "")),
                source_type=str(item.get("source_type", "")),
                owner=str(item.get("owner", "")),
                kind=str(item.get("kind", "all")),
                enabled=bool(item.get("enabled", True)),
            )
            if not spec.name or not spec.label or not spec.source_type:
                continue
            specs.append(spec)
        except Exception:  # noqa: BLE001
            continue
    return specs


def _save_custom_specs(specs: list[CustomSourceSpec]) -> None:
    _CFG_DIR.mkdir(parents=True, exist_ok=True)
    payload = [spec.__dict__ for spec in specs]
    _CUSTOM_SOURCES_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def list_custom_sources() -> list[CustomSourceSpec]:
    """Return persisted user-defined sources."""
    return _load_custom_specs()


def add_custom_github_source(label: str, owner: str, kind: str = "all") -> str:
    """Persist a custom GitHub owner source and return its generated source name."""
    owner_clean = owner.strip().lstrip("@")
    if not owner_clean:
        raise ValueError("Owner cannot be empty")

    label_clean = label.strip() or owner_clean
    specs = _load_custom_specs()

    base = _slug(f"github-{owner_clean}")
    name = base
    taken = {s.name for s in specs}
    index = 2
    while name in taken:
        name = f"{base}-{index}"
        index += 1

    specs.append(CustomSourceSpec(
        name=name,
        label=label_clean,
        source_type="github-owner",
        owner=owner_clean,
        kind=kind if kind in _VALID_KINDS else "all",
        enabled=True,
    ))
    _save_custom_specs(specs)
    return name


def remove_custom_source(name: str) -> bool:
    """Remove a persisted custom source by name."""
    specs = _load_custom_specs()
    remaining = [s for s in specs if s.name != name]
    if len(remaining) == len(specs):
        return False
    _save_custom_specs(remaining)
    return True


def _custom_sources() -> list[ThemeSource]:
    out: list[ThemeSource] = []
    for spec in _load_custom_specs():
        if not spec.enabled:
            continue
        if spec.source_type == "github-owner":
            out.append(GitHubOwnerSource(spec.name, spec.label, spec.owner, spec.kind))
    return out


# ── Registry ───────────────────────────────────────────────────────────────────

def _builtin_sources() -> list[ThemeSource]:
    sources: list[ThemeSource] = [GnomeLookSource(), GitHubSource()]
    if shutil.which("apt-cache"):
        sources.append(AptSource())
    if shutil.which("pacman"):
        sources.append(PacmanSource())
    return sources


def get_sources() -> list[ThemeSource]:
    """Return all registered sources."""
    return [*_builtin_sources(), *_custom_sources()]


def search_source(
    source_name: str,
    query: str,
    kind: str = "all",
    page: int = 1,
) -> list[ThemeRecord]:
    """
    Search one named source, or all sources when *source_name* == ``"all"``.

    Falls back to built-in mock data if every live source fails.
    """
    all_sources = get_sources()
    targets = (
        all_sources
        if source_name == "all"
        else [s for s in all_sources if s.name == source_name]
        if any(s.name == source_name for s in all_sources)
        else all_sources
    )

    results: list[ThemeRecord] = []
    successful_queries = 0
    for src in targets:
        try:
            records = src.search(query, kind, page)
            results.extend(records)
            successful_queries += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("Source '%s' search failed: %s", src.label, exc)

    # Deduplicate by lowercase name (gnome-look takes priority over github)
    seen: set[str] = set()
    unique: list[ThemeRecord] = []
    for r in results:
        key = r.name.lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)

    if unique:
        return unique

    if successful_queries > 0:
        if not query.strip() and kind == "app/tooling":
            fallback_extensions = [
                t for t in MOCK_THEMES
                if t.kind == "app/tooling" and t.artifact_type == "extension"
            ]
            if fallback_extensions:
                log.info("No live app/tooling results; using curated extension fallback.")
                return fallback_extensions
        return unique

    # All live sources failed — use built-in mock data
    log.info("All sources failed; using built-in mock data.")
    q = query.lower()
    return [
        t for t in MOCK_THEMES
        if (kind == "all" or t.kind == kind)
        and (not q or q in t.name.lower() or q in t.summary.lower())
    ]
