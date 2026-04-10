"""
Theme inventory management: listing and removal.
"""

import os
import shutil
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
