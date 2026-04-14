"""
Desktop environment utilities — helper functions for desktop-specific operations.

Supports GNOME, KDE Plasma, XFCE, MATE, Cinnamon, and others.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .environment import detect_environment
from .logger import get_logger

log = get_logger(__name__)


def get_desktop_config_dirs(desktop: str) -> list[Path]:
    """
    Return desktop-specific config directories where themes are searched/applied.
    
    Parameters
    ----------
    desktop : str
        Desktop environment name (e.g., "gnome", "kde", "xfce").
    
    Returns
    -------
    list[Path]
        List of config directories for this desktop.
    """
    home = Path.home()
    config = home / ".config"
    local_share = home / ".local" / "share"
    
    dirs = []
    desktop_lower = desktop.lower()
    
    if "gnome" in desktop_lower:
        dirs.extend([
            config / "gtk-3.0",
            config / "gtk-4.0",
            home / ".themes",
            local_share / "gnome-shell" / "themes",
        ])
    elif "kde" in desktop_lower or "plasma" in desktop_lower:
        dirs.extend([
            config / "kdeglobals",
            config / "kcolorschemerc",
            config / "plasmarc",
            local_share / "konsole",
            home / ".local" / "share" / "color-schemes",
        ])
    elif "xfce" in desktop_lower:
        dirs.extend([
            config / "xfce4" / "xfwm4",
            config / "xfce4" / "xfconf",
        ])
    elif "mate" in desktop_lower:
        dirs.extend([
            config / "dconf" / "user",
            local_share / "themes",
        ])
    elif "cinnamon" in desktop_lower:
        dirs.extend([
            config / "cinnamon",
            local_share / "cinnamon" / "themes",
        ])
    
    return dirs


def get_theme_config_tool(desktop: str) -> Optional[str]:
    """
    Get the preferred configuration tool for this desktop.
    
    Returns the command/tool name suitable for querying/setting theme preferences.
    """
    desktop_lower = desktop.lower()
    
    if "gnome" in desktop_lower:
        return "gsettings" if shutil.which("gsettings") else None
    elif "kde" in desktop_lower or "plasma" in desktop_lower:
        return "kconfig" if shutil.which("kconfig") else "kreadconfig"
    elif "xfce" in desktop_lower:
        return "xfconf-query" if shutil.which("xfconf-query") else None
    elif "mate" in desktop_lower:
        return "gsettings" if shutil.which("gsettings") else None
    
    return None


def is_desktop_supported(desktop: str) -> bool:
    """Check if a desktop environment is supported by ThemeAtlas for theme switching."""
    env = detect_environment()
    desktop_lower = (desktop or "").lower()
    
    # Always support GNOME
    if env.desktop == "gnome" or "gnome" in desktop_lower:
        return env.has_gsettings
    
    # Support KDE (basic)
    if env.desktop.startswith("kde") or "kde" in desktop_lower or "plasma" in desktop_lower:
        return bool(shutil.which("kwriteconfig") or shutil.which("kconfig"))
    
    # Support XFCE (basic)
    if env.desktop == "xfce" or "xfce" in desktop_lower:
        return bool(shutil.which("xfconf-query"))
    
    # Support MATE
    if env.desktop == "mate" or "mate" in desktop_lower:
        return env.has_gsettings
    
    # Support Cinnamon
    if env.desktop == "cinnamon" or "cinnamon" in desktop_lower:
        return env.has_gsettings
    
    # Unknown desktops: assume basic support (file copy only)
    return True


def get_supported_theme_kinds(desktop: str) -> list[str]:
    """
    Return list of theme kinds (gtk, icons, shell, cursors) that this desktop supports.
    
    Parameters
    ----------
    desktop : str
        Desktop environment name.
    
    Returns
    -------
    list[str]
        Supported theme types for this desktop.
    """
    desktop_lower = (desktop or "").lower()
    supported = ["gtk", "icons"]  # Nearly all support gtk themes and icons
    
    if "gnome" in desktop_lower or "gnome" in detect_environment().desktop.lower():
        supported.append("shell")  # GNOME Shell themes
    
    if "kde" in desktop_lower or "plasma" in desktop_lower:
        supported.append("plasma")  # KDE Plasma themes
    
    supported.append("cursors")  # Most support cursor themes
    return supported
