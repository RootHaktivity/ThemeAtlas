"""
GNOME Shell extension management.

Handles detection, installation, and enabling of GNOME extensions –
primarily the 'User Themes' extension required for custom shell themes.
"""

import shutil
import subprocess
import ast
import os
import json
import re
from pathlib import Path
from typing import Optional

from .logger import get_logger

log = get_logger(__name__)

USER_THEMES_UUID = "user-theme@gnome-shell-extensions.gcampax.github.com"

_EXTENSIONS_DIRS = [
    Path.home() / ".local" / "share" / "gnome-shell" / "extensions",
    Path("/usr/share/gnome-shell/extensions"),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _gnome_extensions_cli() -> Optional[str]:
    """Return path to the gnome-extensions CLI tool, or None if absent."""
    return shutil.which("gnome-extensions")


def _run_silent(*cmd: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _read_extension_metadata(extension_dir: Path) -> dict:
    meta = extension_dir / "metadata.json"
    if not meta.is_file():
        return {}
    try:
        data = json.loads(meta.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def get_current_gnome_shell_major() -> Optional[str]:
    """Return current GNOME Shell major version (e.g. '46'), or None if unknown."""
    try:
        result = _run_silent("gnome-shell", "--version")
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"GNOME Shell\s+(\d+)", result.stdout or "")
    if not match:
        return None
    return match.group(1)


def extension_is_compatible_with_shell(extension_dir: Path) -> tuple[bool, str]:
    """Return (is_compatible, reason) for a local extension directory."""
    metadata = _read_extension_metadata(extension_dir)
    if not metadata:
        return True, ""

    supported_raw = metadata.get("shell-version")
    if not isinstance(supported_raw, list) or not supported_raw:
        return True, ""

    current_major = get_current_gnome_shell_major()
    if not current_major:
        return True, ""

    supported_majors: list[str] = []
    for value in supported_raw:
        major = str(value).strip().split(".", 1)[0]
        if major and major not in supported_majors:
            supported_majors.append(major)

    if not supported_majors or current_major in supported_majors:
        return True, ""

    uuid = str(metadata.get("uuid") or extension_dir.name)
    supported_text = ", ".join(supported_majors)
    return False, (
        f"Extension '{uuid}' supports GNOME Shell {supported_text}, "
        f"but current GNOME Shell is {current_major}."
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def is_extension_installed(uuid: str) -> bool:
    """Return True if the extension directory exists in any known location."""
    return any((d / uuid).is_dir() for d in _EXTENSIONS_DIRS)


def is_extension_enabled(uuid: str) -> bool:
    """Return True if the extension is currently enabled."""
    cli = _gnome_extensions_cli()
    if not cli:
        return False

    # Primary path: GNOME-maintained enabled set.
    try:
        enabled = _run_silent("gnome-extensions", "list", "--enabled")
        if enabled.returncode == 0:
            enabled_ids = {line.strip() for line in enabled.stdout.splitlines() if line.strip()}
            if uuid in enabled_ids:
                return True
    except subprocess.TimeoutExpired:
        pass

    # Fallback: parse per-extension info for older/newer output formats.
    try:
        result = _run_silent("gnome-extensions", "info", uuid)
        out = result.stdout or ""
        lowered = out.lower()
        return (
            "enabled: yes" in lowered
            or "state: enabled" in lowered
            or "state: active" in lowered
        )
    except subprocess.TimeoutExpired:
        return False


def enable_extension(uuid: str) -> bool:
    """Enable a GNOME extension by UUID. Returns True on success."""
    ok, _msg = enable_extension_with_reason(uuid)
    return ok


def _append_enabled_extension(uuid: str) -> bool:
    """Fallback: append UUID to org.gnome.shell enabled-extensions."""
    try:
        get_res = _run_silent("gsettings", "get", "org.gnome.shell", "enabled-extensions")
    except subprocess.TimeoutExpired:
        return False
    if get_res.returncode != 0:
        return False

    try:
        current = ast.literal_eval(get_res.stdout.strip())
        if not isinstance(current, list):
            current = []
    except (ValueError, SyntaxError):
        current = []

    if uuid in current:
        return True

    current.append(uuid)
    value = "[" + ", ".join(f"'{item}'" for item in current) + "]"
    try:
        set_res = _run_silent("gsettings", "set", "org.gnome.shell", "enabled-extensions", value)
    except subprocess.TimeoutExpired:
        return False
    return set_res.returncode == 0


def enable_extension_with_reason(uuid: str) -> tuple[bool, str]:
    """Enable a GNOME extension and return (success, message)."""
    if not _gnome_extensions_cli():
        msg = "gnome-extensions CLI not found"
        log.warning("%s. Cannot enable '%s' automatically.", msg, uuid)
        return False, msg

    installed = is_extension_installed(uuid)
    try:
        result = _run_silent("gnome-extensions", "enable", uuid)
    except subprocess.TimeoutExpired:
        msg = f"Timed out while enabling '{uuid}'"
        log.error(msg)
        return False, msg

    if result.returncode == 0:
        log.info("Extension '%s' enabled.", uuid)
        return True, "enabled"

    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    detail = stderr or stdout or "unknown error"

    if installed and ("does not exist" in detail.lower() or "doesn't exist" in detail.lower()):
        # GNOME Shell can lag discovering freshly copied extension dirs.
        if _append_enabled_extension(uuid):
            msg = (
                "Extension directory installed. Added UUID to enabled-extensions; "
                "reload GNOME Shell (or log out/in) to activate."
            )
            log.warning(msg)
            return True, msg
        msg = (
            "Extension is installed but GNOME Shell did not detect it yet. "
            "Try logging out/in, then run: gnome-extensions enable " + uuid
        )
        log.warning(msg)
        return False, msg

    log.error("Could not enable '%s': %s", uuid, detail)
    return False, detail


def list_extensions(include_system: bool = True) -> dict[str, list[str]]:
    """Return installed GNOME extension UUIDs grouped by location."""
    result: dict[str, list[str]] = {"extensions (user)": []}

    user_dir = _EXTENSIONS_DIRS[0]
    if user_dir.is_dir():
        result["extensions (user)"] = sorted(
            p.name for p in user_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
        )

    if include_system:
        sys_dir = _EXTENSIONS_DIRS[1]
        result["extensions (system)"] = []
        if sys_dir.is_dir():
            result["extensions (system)"] = sorted(
                p.name for p in sys_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
            )

    return result


def remove_extension(uuid: str, system_wide: bool = False) -> bool:
    """Remove an installed GNOME extension directory by UUID."""
    target_base = _EXTENSIONS_DIRS[1] if system_wide else _EXTENSIONS_DIRS[0]
    target = target_base / uuid

    if system_wide and os.getuid() != 0:
        log.error("Removing a system extension requires root privileges.")
        return False

    if not target.exists():
        log.error("Extension '%s' not found at %s", uuid, target)
        return False

    try:
        shutil.rmtree(target)
    except OSError as exc:
        log.error("Could not remove extension '%s': %s", uuid, exc)
        return False

    log.info("Removed GNOME extension '%s' from %s", uuid, target_base)
    return True


def install_user_themes_extension() -> bool:
    """
    Ensure the User Themes GNOME extension is installed and enabled.

    Attempts installation via the system package manager (apt-based distros)
    before falling back to a manual prompt.

    Returns True if the extension ends up enabled.
    """
    if is_extension_installed(USER_THEMES_UUID):
        log.info("User Themes extension is already installed.")
        if not is_extension_enabled(USER_THEMES_UUID):
            return enable_extension(USER_THEMES_UUID)
        log.info("User Themes extension is already enabled.")
        return True

    # Try apt (Ubuntu / Debian)
    if shutil.which("apt"):
        log.info("Installing gnome-shell-extensions via apt …")
        try:
            result = subprocess.run(
                ["pkexec", "apt-get", "install", "-y", "gnome-shell-extensions"],
                timeout=180,
                check=False,
            )
            if result.returncode == 0 and is_extension_installed(USER_THEMES_UUID):
                return enable_extension(USER_THEMES_UUID)
        except subprocess.TimeoutExpired:
            log.error("apt install timed out.")

    log.warning(
        "Could not install User Themes extension automatically.\n"
        "  Install it manually from:\n"
        "  https://extensions.gnome.org/extension/19/user-themes/\n"
        "  Then log out and back in, or run:\n"
        "    gnome-extensions enable %s",
        USER_THEMES_UUID,
    )
    return False
