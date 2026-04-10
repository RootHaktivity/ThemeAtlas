"""
Theme switching via gsettings.

Supports GTK, icon, cursor, and GNOME shell themes.
"""

import shutil
import subprocess
from typing import Optional

from .logger import get_logger

log = get_logger(__name__)

_INTERFACE_SCHEMA = "org.gnome.desktop.interface"
_SHELL_SCHEMA     = "org.gnome.shell.extensions.user-theme"


# ── gsettings wrappers ─────────────────────────────────────────────────────────

def _gsettings_available() -> bool:
    return bool(shutil.which("gsettings"))


def _gs_set(schema: str, key: str, value: str) -> bool:
    if not _gsettings_available():
        log.error("gsettings not found. Cannot switch themes.")
        return False
    try:
        result = subprocess.run(
            ["gsettings", "set", schema, key, value],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error("gsettings set timed out.")
        return False

    if result.returncode == 0:
        log.info("gsettings: %s %s = %r", schema, key, value)
        return True
    log.error("gsettings error: %s", result.stderr.strip() or result.stdout.strip())
    return False


def _gs_get(schema: str, key: str) -> Optional[str]:
    if not _gsettings_available():
        return None
    try:
        result = subprocess.run(
            ["gsettings", "get", schema, key],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip().strip("'")
    except subprocess.TimeoutExpired:
        pass
    return None


# ── Public switching API ───────────────────────────────────────────────────────

def switch_gtk_theme(name: str) -> bool:
    return _gs_set(_INTERFACE_SCHEMA, "gtk-theme", name)


def switch_icon_theme(name: str) -> bool:
    return _gs_set(_INTERFACE_SCHEMA, "icon-theme", name)


def switch_cursor_theme(name: str) -> bool:
    return _gs_set(_INTERFACE_SCHEMA, "cursor-theme", name)


def switch_shell_theme(name: str, desktop: str) -> bool:
    """Switch the GNOME shell theme. Only works on GNOME with User Themes extension."""
    if desktop != "gnome":
        log.warning("Shell theme switching is only supported on GNOME (detected: %s).", desktop)
        return False
    return _gs_set(_SHELL_SCHEMA, "name", name)


def get_current_themes() -> dict[str, Optional[str]]:
    """Return a mapping of theme type → currently active theme name (or None)."""
    return {
        "gtk":    _gs_get(_INTERFACE_SCHEMA, "gtk-theme"),
        "icons":  _gs_get(_INTERFACE_SCHEMA, "icon-theme"),
        "cursor": _gs_get(_INTERFACE_SCHEMA, "cursor-theme"),
        "shell":  _gs_get(_SHELL_SCHEMA,     "name"),
    }
