"""
Theme inventory management: listing and removal.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .logger import get_logger

log = get_logger(__name__)

# ── Directory constants (mirrors extractor.py, kept separate for clean imports)
USER_LOCAL = Path.home() / ".local" / "share"
USER_THEMES_DIR = USER_LOCAL / "themes"
USER_ICONS_DIR  = USER_LOCAL / "icons"
# GNOME shell themes are primarily discovered under ~/.themes/<name>/gnome-shell.
USER_SHELL_THEMES_DIR = Path.home() / ".themes"
USER_SHELL_THEMES_XDG_DIR = USER_LOCAL / "gnome-shell" / "themes"
SYS_THEMES_DIR  = Path("/usr/share/themes")
SYS_ICONS_DIR   = Path("/usr/share/icons")
SYS_SHELL_THEMES_DIR = Path("/usr/share/themes")
GTK4_CONFIG_DIR = Path.home() / ".config" / "gtk-4.0"

# ── Installed-app manifest ─────────────────────────────────────────────────────
_APPS_MANIFEST = Path.home() / ".local" / "share" / "themeatlas" / "installed_apps.json"


def _load_manifest() -> list[dict]:
    try:
        return json.loads(_APPS_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_manifest(entries: list[dict]) -> None:
    _APPS_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    _APPS_MANIFEST.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def record_installed_app(
    name: str,
    *,
    binaries: list[str] | None = None,
    share_dirs: list[str] | None = None,
    lib_dirs: list[str] | None = None,
) -> None:
    """Write an entry for a source-built app to the installed-apps manifest.

    Parameters
    ----------
    name:       Display name of the app (e.g. "Gradience").
    binaries:   Absolute paths of installed executables.
    share_dirs: Absolute paths of installed share/ subdirs.
    lib_dirs:   Absolute paths of installed lib/ subtrees.
    """
    entries = _load_manifest()
    # Remove any existing entry with the same name so reinstalls are clean.
    entries = [e for e in entries if e.get("name", "").lower() != name.lower()]
    entries.append({
        "name": name,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "binaries": binaries or [],
        "share_dirs": share_dirs or [],
        "lib_dirs": lib_dirs or [],
    })
    _save_manifest(entries)
    log.info("Recorded installed app: %s", name)


def list_installed_apps() -> list[dict]:
    """Return all entries from the installed-apps manifest.

    Each entry is a dict with keys: name, installed_at, binaries, share_dirs, lib_dirs.
    Non-existent entries are pruned from the manifest automatically.
    """
    entries = _load_manifest()
    valid = []
    for entry in entries:
        # Keep entry if at least one tracked file or directory still exists.
        paths = entry.get("binaries", []) + entry.get("share_dirs", []) + entry.get("lib_dirs", [])
        if any(Path(p).exists() for p in paths):
            valid.append(entry)
    if len(valid) != len(entries):
        _save_manifest(valid)
    return valid


def uninstall_app(name: str) -> tuple[bool, str]:
    """Remove all files recorded for a source-built app and drop its manifest entry.

    Returns (success, message).
    """
    entries = _load_manifest()
    target = next((e for e in entries if e.get("name", "").lower() == name.lower()), None)
    if target is None:
        return False, f"No installed-app record found for '{name}'"

    errors: list[str] = []

    for path_str in target.get("binaries", []):
        p = Path(path_str)
        try:
            if p.is_file():
                p.unlink()
                log.info("Removed binary: %s", p)
        except OSError as exc:
            errors.append(str(exc))

    for path_str in target.get("share_dirs", []):
        p = Path(path_str)
        try:
            if p.is_dir():
                shutil.rmtree(p)
                log.info("Removed share dir: %s", p)
        except OSError as exc:
            errors.append(str(exc))

    for path_str in target.get("lib_dirs", []):
        p = Path(path_str)
        try:
            if p.is_dir():
                shutil.rmtree(p)
                log.info("Removed lib dir: %s", p)
        except OSError as exc:
            errors.append(str(exc))

    entries = [e for e in entries if e.get("name", "").lower() != name.lower()]
    _save_manifest(entries)

    if errors:
        return False, f"Partially removed '{name}'; errors: {'; '.join(errors)}"
    return True, f"Uninstalled '{name}' successfully"


def _ls(path: Path) -> list[str]:
    """Return sorted list of non-hidden subdirectory names, or [] if absent."""
    if not path.is_dir():
        return []
    return sorted(p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith("."))


def _ls_shell(path: Path) -> list[str]:
    """Return shell theme dirs that contain a gnome-shell subdirectory."""
    if not path.is_dir():
        return []
    names: list[str] = []
    for entry in path.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if (entry / "gnome-shell").is_dir():
            names.append(entry.name)
    return sorted(names)


# ── Public API ─────────────────────────────────────────────────────────────────

def list_themes(include_system: bool = True) -> dict[str, list[str]]:
    """
    Return a mapping of category label → sorted theme-name list.

    Categories always include user directories; system directories are
    included unless *include_system* is False.
    """
    user_shell = sorted(set(_ls_shell(USER_SHELL_THEMES_DIR) + _ls_shell(USER_SHELL_THEMES_XDG_DIR)))
    result: dict[str, list[str]] = {
        "gtk   (user)":   _ls(USER_THEMES_DIR),
        "icons (user)":   _ls(USER_ICONS_DIR),
        "shell (user)":   user_shell,
    }
    if include_system:
        result["gtk   (system)"] = _ls(SYS_THEMES_DIR)
        result["icons (system)"] = _ls(SYS_ICONS_DIR)
        result["shell (system)"] = _ls_shell(SYS_SHELL_THEMES_DIR)
    return result


def remove_theme(name: str, kind: str = "gtk", system_wide: bool = False) -> bool:
    """
    Remove a theme by name.

    Parameters
    ----------
    name:        Theme directory name.
    kind:        One of 'gtk', 'icons', 'cursors', 'shell'.
    system_wide: If True, target system directories (requires root).

    Returns True on success.
    """
    if kind == "shell":
        if system_wide:
            candidates = [SYS_SHELL_THEMES_DIR / name]
        else:
            candidates = [USER_SHELL_THEMES_DIR / name, USER_SHELL_THEMES_XDG_DIR / name]
    elif kind in ("icons", "cursors"):
        base = SYS_ICONS_DIR if system_wide else USER_ICONS_DIR
        candidates = [base / name]
    else:
        base = SYS_THEMES_DIR if system_wide else USER_THEMES_DIR
        candidates = [base / name]

    if system_wide and os.getuid() != 0:
        log.error(
            "Removing a system theme requires root privileges. "
            "Re-run with sudo or omit --system."
        )
        return False

    removed_any = False
    for target in candidates:
        if not target.exists():
            continue
        shutil.rmtree(target)
        removed_any = True
        log.info("Removed theme '%s' from %s", name, target.parent)

    if not removed_any:
        log.error("Theme '%s' not found in expected locations", name)
        return False
    return True


def theme_exists(name: str, kind: str = "gtk") -> bool:
    """Return True if a theme with the given name is installed."""
    if kind == "shell":
        return (
            (USER_SHELL_THEMES_DIR / name).is_dir()
            or (USER_SHELL_THEMES_XDG_DIR / name).is_dir()
            or (SYS_SHELL_THEMES_DIR / name).is_dir()
        )
    elif kind in ("icons", "cursors"):
        search_dirs = [USER_ICONS_DIR, SYS_ICONS_DIR]
    else:
        search_dirs = [USER_THEMES_DIR, SYS_THEMES_DIR]
    return any((d / name).is_dir() for d in search_dirs)
