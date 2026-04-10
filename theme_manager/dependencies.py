"""Runtime dependency checks and best-effort auto-install helpers."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys

from .environment import detect_environment
from .logger import get_logger

log = get_logger(__name__)


def _has_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _as_root_command(cmd: list[str]) -> list[str] | None:
    if os.getuid() == 0:
        return cmd
    if shutil.which("pkexec"):
        return ["pkexec", *cmd]
    if shutil.which("sudo"):
        return ["sudo", *cmd]
    return None


def _run_install_steps(steps: list[list[str]]) -> bool:
    for step in steps:
        full = _as_root_command(step)
        if full is None:
            log.error(
                "Need root privileges to install dependencies, but neither pkexec nor sudo is available."
            )
            return False

        log.info("Running dependency install step: %s", " ".join(full))
        try:
            result = subprocess.run(full, timeout=300, check=False)
        except subprocess.TimeoutExpired:
            log.error("Dependency install step timed out: %s", " ".join(full))
            return False

        if result.returncode != 0:
            log.error("Install step failed with exit code %d", result.returncode)
            return False

    return True


def _in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _install_pillow(package_manager: str) -> bool:
    """Install Pillow using distro packages on system Python, pip in venvs."""
    if _has_module("PIL"):
        return True

    if not _in_virtualenv():
        install_steps: list[list[str]] = []

        if package_manager == "apt":
            install_steps = [
                ["apt-get", "update"],
                ["apt-get", "install", "-y", "python3-pil", "python3-pil.imagetk"],
            ]
        elif package_manager in ("dnf", "yum"):
            install_steps = [[package_manager, "install", "-y", "python3-pillow"]]
        elif package_manager == "pacman":
            install_steps = [["pacman", "-Sy", "--noconfirm", "python-pillow"]]
        elif package_manager == "zypper":
            install_steps = [["zypper", "--non-interactive", "install", "python3-Pillow"]]

        if install_steps:
            log.info("Installing Pillow via %s package manager ...", package_manager)
            if _run_install_steps(install_steps) and _has_module("PIL"):
                return True

    # venv mode (or distro package failed): try pip
    pip_cmd = [sys.executable, "-m", "pip", "install", "Pillow>=9.0"]
    log.info("Installing Pillow via pip ...")

    try:
        result = subprocess.run(pip_cmd, timeout=180, check=False)
        if result.returncode == 0 and _has_module("PIL"):
            return True
    except subprocess.TimeoutExpired:
        log.error("Pillow installation timed out.")
        return False

    # Fallback for environments without write permissions
    try:
        result = subprocess.run([*pip_cmd, "--user"], timeout=180, check=False)
        if result.returncode == 0 and _has_module("PIL"):
            return True
    except subprocess.TimeoutExpired:
        log.error("Pillow installation (--user) timed out.")

    # PEP 668 fallback (externally managed Python): explicit override
    try:
        result = subprocess.run([*pip_cmd, "--break-system-packages"], timeout=180, check=False)
        if result.returncode == 0 and _has_module("PIL"):
            return True
    except subprocess.TimeoutExpired:
        log.error("Pillow installation (--break-system-packages) timed out.")

    return False


def _install_pyside6(package_manager: str) -> bool:
    """Install PySide6 with distro packages when possible, otherwise pip."""
    if _has_module("PySide6"):
        return True

    if not _in_virtualenv():
        install_steps: list[list[str]] = []

        if package_manager == "apt":
            install_steps = [
                ["apt-get", "update"],
                ["apt-get", "install", "-y", "python3-pyside6.qtwidgets", "python3-pyside6.qtgui", "python3-pyside6.qtcore"],
            ]
        elif package_manager in ("dnf", "yum"):
            install_steps = [[package_manager, "install", "-y", "python3-pyside6"]]
        elif package_manager == "pacman":
            install_steps = [["pacman", "-Sy", "--noconfirm", "python-pyside6"]]
        elif package_manager == "zypper":
            install_steps = [["zypper", "--non-interactive", "install", "python3-PySide6"]]

        if install_steps:
            log.info("Installing PySide6 via %s package manager ...", package_manager)
            if _run_install_steps(install_steps) and _has_module("PySide6"):
                return True

    pip_cmd = [sys.executable, "-m", "pip", "install", "PySide6>=6.6"]
    log.info("Installing PySide6 via pip ...")

    try:
        result = subprocess.run(pip_cmd, timeout=300, check=False)
        if result.returncode == 0 and _has_module("PySide6"):
            return True
    except subprocess.TimeoutExpired:
        log.error("PySide6 installation timed out.")

    try:
        result = subprocess.run([*pip_cmd, "--user"], timeout=300, check=False)
        if result.returncode == 0 and _has_module("PySide6"):
            return True
    except subprocess.TimeoutExpired:
        log.error("PySide6 installation (--user) timed out.")

    try:
        result = subprocess.run([*pip_cmd, "--break-system-packages"], timeout=300, check=False)
        if result.returncode == 0 and _has_module("PySide6"):
            return True
    except subprocess.TimeoutExpired:
        log.error("PySide6 installation (--break-system-packages) timed out.")

    return False


def ensure_gui_dependencies(auto_install: bool = True, require_pillow: bool = True) -> bool:
    """
    Ensure GUI runtime requirements are present.

    Currently required:
    - PySide6 module (Qt widgets runtime)
    - Pillow (for generated preview images)

    Returns True if ready, False otherwise.
    """
    missing_qt = not _has_module("PySide6")
    missing_pillow = not _has_module("PIL")

    if not missing_qt and not missing_pillow:
        return True

    if missing_qt:
        log.warning("Missing Python module: PySide6")
    if missing_pillow:
        log.warning("Missing Python module: Pillow (PIL)")

    if not auto_install:
        if require_pillow:
            log.error("PySide6 and Pillow are required for GUI mode.")
        else:
            log.error("PySide6 is required for GUI mode.")
        return False

    env = detect_environment()
    pm = env.package_manager

    if missing_qt and not _install_pyside6(pm):
        log.error(
            "Automatic PySide6 installation failed. "
            "Install it manually (pip install PySide6) and retry."
        )
        return False

    if missing_pillow and not _install_pillow(pm):
        if require_pillow:
            log.error("Automatic Pillow installation failed.")
            return False
        log.warning("Automatic Pillow installation failed; continuing without Pillow.")

    if _has_module("PySide6") and _has_module("PIL"):
        log.info("Dependency check passed: PySide6 and Pillow are available.")
        return True

    if _has_module("PySide6") and not require_pillow:
        log.info("Dependency check passed: PySide6 available (Pillow optional).")
        return True

    log.error(
        "Dependencies are still missing after installation attempts. "
        "Restart your shell/session and try again."
    )
    return False
