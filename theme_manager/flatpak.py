"""
Flatpak theme integration.

Grants Flatpak applications access to user theme directories and sets
the GTK_THEME environment variable override so sandboxed apps pick up
the active theme without manual configuration.
"""

import shutil
import subprocess

from .logger import get_logger

log = get_logger(__name__)


def is_flatpak_available() -> bool:
    return bool(shutil.which("flatpak"))


def apply_flatpak_theme_overrides(gtk_theme: str, icon_theme: str) -> bool:
    """
    Configure Flatpak so that all user-installed apps can see the chosen themes.

    Grants read-only filesystem access to theme/icon directories and sets
    GTK_THEME so the theme is active inside sandboxes.

    Returns True if all overrides succeeded.
    """
    if not is_flatpak_available():
        log.warning("Flatpak is not installed; skipping Flatpak theme integration.")
        return False

    overrides: list[list[str]] = []

    if gtk_theme:
        overrides.append(["flatpak", "override", "--user", f"--env=GTK_THEME={gtk_theme}"])

    # Filesystem access for theme lookup paths
    for fs_path in (
        "~/.themes:ro",
        "~/.icons:ro",
        "xdg-data/themes:ro",
        "xdg-data/icons:ro",
    ):
        overrides.append(["flatpak", "override", "--user", f"--filesystem={fs_path}"])

    success = True
    for cmd in overrides:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                log.error("Flatpak override failed (%s): %s", " ".join(cmd[3:]), result.stderr.strip())
                success = False
        except subprocess.TimeoutExpired:
            log.error("Flatpak override timed out: %s", " ".join(cmd))
            success = False

    if success:
        log.info(
            "Flatpak overrides applied. "
            "Restart running Flatpak apps to see theme changes."
        )
    return success
