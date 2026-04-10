"""
High-level theme installation dispatcher.

Supports three installation sources:
  - Local archive  (.zip / .tar.gz / .tgz / .tar.bz2 / .tar.xz)
  - Local .deb package
  - PPA + apt package names (Ubuntu / Debian only)
"""

import shutil
import subprocess
from pathlib import Path

from .extractor import extract_archive
from .logger import get_logger

log = get_logger(__name__)


def install_from_archive(path: str, system_wide: bool = False) -> list[str]:
    """
    Extract a theme archive and place files in the appropriate directories.

    Returns a list of installed theme names.
    """
    return extract_archive(path, system_wide=system_wide)


def install_from_deb(path: str) -> bool:
    """
    Install a .deb theme package using dpkg (requires pkexec / sudo).

    Returns True on success.
    """
    pkg = Path(path).resolve()
    if not pkg.exists():
        raise FileNotFoundError(f".deb file not found: {pkg}")
    if pkg.suffix != ".deb":
        raise ValueError(f"Expected a .deb file, got: {pkg.suffix}")

    if not shutil.which("dpkg"):
        log.error("dpkg is not available. .deb installation is Debian/Ubuntu-only.")
        return False

    log.info("Installing .deb package: %s", pkg.name)
    try:
        result = subprocess.run(
            ["pkexec", "dpkg", "-i", str(pkg)],
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error("dpkg timed out.")
        return False

    if result.returncode != 0:
        log.error(
            "dpkg installation failed (exit %d). "
            "Run 'sudo dpkg -i %s' manually for details.",
            result.returncode,
            pkg.name,
        )
        return False

    log.info("Successfully installed .deb package '%s'.", pkg.name)
    return True


def install_from_ppa(ppa: str, packages: list[str]) -> bool:
    """
    Add a Launchpad PPA and install one or more packages from it.

    *ppa* may be given with or without the leading ``ppa:`` prefix.
    Requires ``add-apt-repository`` (software-properties-common).

    Returns True on success.
    """
    if not shutil.which("add-apt-repository"):
        log.error(
            "add-apt-repository not found. "
            "Install software-properties-common first:\n"
            "  sudo apt install software-properties-common"
        )
        return False

    if not packages:
        log.error("No packages specified for PPA installation.")
        return False

    ppa_id = ppa.strip()
    if not ppa_id.startswith("ppa:"):
        ppa_id = f"ppa:{ppa_id}"

    log.info("Adding PPA: %s", ppa_id)
    try:
        r1 = subprocess.run(
            ["pkexec", "add-apt-repository", "-y", ppa_id],
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error("add-apt-repository timed out.")
        return False

    if r1.returncode != 0:
        log.error("Failed to add PPA '%s'. Check the PPA name and your internet connection.", ppa_id)
        return False

    log.info("Updating package lists …")
    try:
        subprocess.run(
            ["pkexec", "apt-get", "update", "-q"],
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("apt-get update timed out; proceeding anyway.")

    log.info("Installing: %s", ", ".join(packages))
    try:
        r2 = subprocess.run(
            ["pkexec", "apt-get", "install", "-y", *packages],
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error("apt-get install timed out.")
        return False

    if r2.returncode != 0:
        log.error(
            "Package installation failed (exit %d). "
            "Run 'sudo apt install %s' manually for details.",
            r2.returncode,
            " ".join(packages),
        )
        return False

    log.info("PPA packages installed successfully.")
    return True


def install_from_package(package_name: str, package_manager: str) -> bool:
    """Install a package using the host package manager (currently apt/pacman)."""
    pkg = (package_name or "").strip()
    pm = (package_manager or "").strip().lower()
    if not pkg:
        log.error("Package name cannot be empty.")
        return False

    if pm == "apt":
        if not shutil.which("apt-get"):
            log.error("apt-get not found.")
            return False
        log.info("Installing apt package: %s", pkg)
        try:
            result = subprocess.run(
                ["pkexec", "apt-get", "install", "-y", pkg],
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.error("apt-get install timed out.")
            return False
        if result.returncode != 0:
            log.error("apt package install failed (exit %d): %s", result.returncode, pkg)
            return False
        return True

    if pm == "pacman":
        if not shutil.which("pacman"):
            log.error("pacman not found.")
            return False
        log.info("Installing pacman package: %s", pkg)
        try:
            result = subprocess.run(
                ["pkexec", "pacman", "-S", "--noconfirm", pkg],
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.error("pacman install timed out.")
            return False
        if result.returncode != 0:
            log.error("pacman package install failed (exit %d): %s", result.returncode, pkg)
            return False
        return True

    log.warning("Unsupported package manager for direct package install: %s", pm)
    return False
