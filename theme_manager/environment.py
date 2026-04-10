import os
import shutil
from dataclasses import dataclass, field

from .logger import get_logger

log = get_logger(__name__)


@dataclass
class Environment:
    desktop: str = "unknown"
    distro: str = "unknown"
    distro_like: list = field(default_factory=list)
    package_manager: str = "unknown"
    is_wayland: bool = False
    has_gsettings: bool = False
    has_gnome_tweaks: bool = False
    has_flatpak: bool = False


def detect_environment() -> Environment:
    env = Environment()

    # ── Desktop environment ────────────────────────────────────────────────────
    xdg = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    session = os.environ.get("DESKTOP_SESSION", "").lower()
    raw = xdg or session

    if "gnome" in raw:
        env.desktop = "gnome"
    elif "kde" in raw or "plasma" in raw:
        env.desktop = "kde"
    elif "xfce" in raw:
        env.desktop = "xfce"
    elif "mate" in raw:
        env.desktop = "mate"
    elif "cinnamon" in raw:
        env.desktop = "cinnamon"
    elif "lxqt" in raw:
        env.desktop = "lxqt"
    else:
        env.desktop = raw or "unknown"

    env.is_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))

    # ── Distro ─────────────────────────────────────────────────────────────────
    os_release = "/etc/os-release"
    if os.path.exists(os_release):
        info: dict[str, str] = {}
        with open(os_release, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if "=" in line:
                    k, _, v = line.partition("=")
                    info[k] = v.strip('"')
        env.distro = info.get("ID", "unknown").lower()
        env.distro_like = [d.lower() for d in info.get("ID_LIKE", "").split() if d]

    # ── Package manager ────────────────────────────────────────────────────────
    for cmd, name in (
        ("apt",    "apt"),
        ("dnf",    "dnf"),
        ("yum",    "yum"),
        ("pacman", "pacman"),
        ("zypper", "zypper"),
        ("emerge", "portage"),
    ):
        if shutil.which(cmd):
            env.package_manager = name
            break

    # ── Tool availability ──────────────────────────────────────────────────────
    env.has_gsettings    = bool(shutil.which("gsettings"))
    env.has_gnome_tweaks = bool(shutil.which("gnome-tweaks"))
    env.has_flatpak      = bool(shutil.which("flatpak"))

    log.debug(
        "Detected: desktop=%s distro=%s pm=%s wayland=%s",
        env.desktop, env.distro, env.package_manager, env.is_wayland,
    )
    return env
