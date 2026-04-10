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
import re
import shutil
import ssl
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from abc import ABC, abstractmethod
from urllib.parse import urlencode

from .api import (
    MOCK_THEMES, ThemeRecord,
    _API_BASE, _CATEGORY_IDS, _TIMEOUT,
    _parse_response, _safe_float, _safe_int,
)
from ..logger import get_logger

log = get_logger(__name__)

_GITHUB_API = "https://api.github.com/search/repositories"
_CFG_DIR = Path.home() / ".config" / "linux-theme-manager"
_CUSTOM_SOURCES_FILE = _CFG_DIR / "custom_sources.json"

# Map theme kind → GitHub topic tag
_KIND_TO_TOPIC: dict[str, str] = {
    "gtk":     "gtk-theme",
    "icons":   "icon-theme",
    "shell":   "gnome-shell-theme",
    "cursors": "cursor-theme",
}
_TOPIC_TO_KIND: dict[str, str] = {v: k for k, v in _KIND_TO_TOPIC.items()}


# ── Shared HTTP helper ─────────────────────────────────────────────────────────

def _http_get(url: str, extra_headers: dict[str, str] | None = None) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "linux-theme-manager/1.0",
            **(extra_headers or {}),
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


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

    def search(self, query: str, kind: str = "all", page: int = 1) -> list[ThemeRecord]:
        # For "all" just search the most popular gtk-theme topic to avoid hammering
        # the API with four requests; specific kinds do a targeted topic search.
        topic = _KIND_TO_TOPIC.get(kind, "gtk-theme")

        q_parts: list[str] = [query.strip()] if query.strip() else []
        q_parts.append(f"topic:{topic}")
        q_parts.append("stars:>50")

        params = {
            "q":        " ".join(q_parts),
            "sort":     "stars",
            "order":    "desc",
            "per_page": "20",
            "page":     str(page),
        }
        url = _GITHUB_API + "?" + urlencode(params)
        log.debug("GitHub request: %s", url)

        raw = _http_get(url, extra_headers={"Accept": "application/vnd.github+json"})

        # GitHub may return 403 if rate-limited
        if "items" not in raw:
            msg = raw.get("message", "Unknown GitHub API error")
            raise RuntimeError(f"GitHub API: {msg}")

        results: list[ThemeRecord] = []
        for item in raw.get("items", []):
            results.append(self._to_record(item, kind))
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

        return ThemeRecord(
            id           = f"gh-{item.get('id', '')}",
            name         = display,
            summary      = description or f"GitHub · {raw_name}",
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

            def health_check(self) -> tuple[str, str]:
                return _check_github_health()
        return results


_PACKAGE_THEME_HINTS: dict[str, tuple[str, ...]] = {
    "gtk": ("gtk", "gnome", "adwaita", "materia", "arc-theme"),
    "icons": ("icon", "papirus", "numix", "breeze-icon"),
    "shell": ("gnome-shell", "shell-theme", "user-theme"),
    "cursors": ("cursor", "xcursor", "bibata"),
}


def _infer_kind_from_text(text: str) -> str:
    t = text.lower()
    if any(k in t for k in _PACKAGE_THEME_HINTS["cursors"]):
        return "cursors"
    if any(k in t for k in _PACKAGE_THEME_HINTS["icons"]):
        return "icons"
    if any(k in t for k in _PACKAGE_THEME_HINTS["shell"]):
        return "shell"
    return "gtk"


def _matches_kind(text: str, kind: str) -> bool:
    if kind in ("", "all"):
        return True
    return any(k in text.lower() for k in _PACKAGE_THEME_HINTS.get(kind, ()))


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
            if not any(token in text for token in ("theme", "icon", "cursor", "gnome-shell", "gtk")):
                continue
            if not _matches_kind(text, kind):
                continue
            k = _infer_kind_from_text(text)
            display = re.sub(r"[-_]+", " ", pkg).strip().title()
            out.append(ThemeRecord(
                id=f"apt-{pkg}",
                name=display,
                summary=desc,
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
            if not any(token in text for token in ("theme", "icon", "cursor", "gnome-shell", "gtk")):
                continue
            if not _matches_kind(text, kind):
                continue
            k = _infer_kind_from_text(text)
            display = re.sub(r"[-_]+", " ", pkg).strip().title()
            out.append(ThemeRecord(
                id=f"pacman-{pkg}",
                name=display,
                summary=desc,
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
        kind=kind,
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

    if unique or successful_queries > 0:
        return unique

    # All live sources failed — use built-in mock data
    log.info("All sources failed; using built-in mock data.")
    q = query.lower()
    return [
        t for t in MOCK_THEMES
        if (kind == "all" or t.kind == kind)
        and (not q or q in t.name.lower() or q in t.summary.lower())
    ]
