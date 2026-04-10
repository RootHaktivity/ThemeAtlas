"""
Archive extraction and theme-file placement.

Supports .zip, .tar.gz, .tgz, .tar.bz2, .tar.xz archives.
Detects theme type (gtk / icons / cursors / shell) and copies files to the
correct user or system directory.  Optionally runs an embedded install script
and propagates GTK-4.0 assets to ~/.config/gtk-4.0.
"""

import os
import json
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Optional

from .dependencies import _run_install_steps
from .environment import detect_environment
from .extensions import _EXTENSIONS_DIRS
from .logger import get_logger

log = get_logger(__name__)

# ── Directory constants ────────────────────────────────────────────────────────
USER_LOCAL = Path.home() / ".local" / "share"
USER_THEMES_DIR  = USER_LOCAL / "themes"
USER_ICONS_DIR   = USER_LOCAL / "icons"
# GNOME User Themes extension discovers shell themes under ~/.themes/<name>/gnome-shell
USER_SHELL_THEMES_DIR = Path.home() / ".themes"
SYS_THEMES_DIR   = Path("/usr/share/themes")
SYS_ICONS_DIR    = Path("/usr/share/icons")
SYS_SHELL_THEMES_DIR = Path("/usr/share/themes")
GTK4_CONFIG_DIR  = Path.home() / ".config" / "gtk-4.0"

# Marker files/dirs that identify a theme root
_THEME_MARKERS = frozenset({
    "index.theme", "cursors",
    "gtk-2.0", "gtk-3.0", "gtk-4.0", "gnome-shell",
})

_GENERIC_SOURCE_DIRS = frozenset({
    "src", "source", "sources", "assets", "images", "img",
    "scripts", "dist", "build", "scss", "sass",
})

# Recognised install-script file names
_INSTALL_SCRIPTS = ("install.sh", "install", "setup.sh")
_SOURCE_BUILD_MARKERS = ("meson.build",)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _classify_theme(root: Path) -> str:
    """Return 'gtk', 'icons', 'cursors', or 'shell' for a theme root dir."""
    child_names = {p.name.lower() for p in root.iterdir()}

    if _extension_uuid(root):
        return "extension"

    if "cursors" in child_names:
        return "cursors"

    if "gnome-shell" in child_names:
        return "shell"

    if "index.theme" in child_names:
        index = root / "index.theme"
        try:
            if "[Icon Theme]" in index.read_text(errors="ignore"):
                return "icons"
        except OSError:
            pass

    # GTK theme if it has at least one GTK sub-directory
    for d in ("gtk-2.0", "gtk-3.0", "gtk-4.0"):
        if d in child_names:
            return "gtk"

    return "gtk"  # safe default


def _extension_uuid(path: Path) -> Optional[str]:
    """Return GNOME extension UUID from metadata.json if this looks like an extension."""
    meta = path / "metadata.json"
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None
    uuid = data.get("uuid")
    if isinstance(uuid, str) and uuid and data.get("shell-version"):
        return uuid
    return None


def _has_theme_markers(path: Path) -> bool:
    """Return True if *path* contains at least one recognised theme marker."""
    try:
        children = {p.name.lower() for p in path.iterdir()}
        if _extension_uuid(path):
            return True
        markers = children & _THEME_MARKERS
        if not markers:
            return False

        # GitHub source archives often contain build/source folders named "src"
        # with gtk/gnome-shell assets that are not directly installable themes.
        # Avoid treating these generic directories as theme roots unless they
        # include an actual theme manifest.
        if path.name.lower() in _GENERIC_SOURCE_DIRS and "index.theme" not in children:
            return False

        return True
    except OSError:
        return False


def _find_theme_roots(extract_dir: Path) -> list[Path]:
    """Walk up to three levels inside extract_dir to locate theme root dirs."""
    roots: list[Path] = []

    for level1 in extract_dir.iterdir():
        if not level1.is_dir():
            continue
        if _has_theme_markers(level1):
            roots.append(level1)
        else:
            try:
                for level2 in level1.iterdir():
                    if not level2.is_dir():
                        continue
                    if _has_theme_markers(level2):
                        roots.append(level2)
                    else:
                        try:
                            for level3 in level2.iterdir():
                                if level3.is_dir() and _has_theme_markers(level3):
                                    roots.append(level3)
                        except OSError:
                            pass
            except OSError:
                pass

    if roots:
        return roots
    if _has_theme_markers(extract_dir):
        return [extract_dir]
    return []


def _find_shell_theme_roots(extract_dir: Path) -> list[Path]:
    """Find shell themes by looking for gnome-shell/ dirs."""
    roots: list[Path] = []
    
    for level1 in extract_dir.iterdir():
        if not level1.is_dir():
            continue
        if (level1 / "gnome-shell").is_dir():
            roots.append(level1)
        else:
            try:
                for level2 in level1.iterdir():
                    if level2.is_dir() and (level2 / "gnome-shell").is_dir():
                        roots.append(level2)
            except OSError:
                pass
    
    return roots


def _name_from_archive(archive: Path) -> str:
    """Derive a stable theme directory name from an archive filename."""
    name = archive.name
    for suffix in (".tar.xz", ".tar.gz", ".tar.bz2", ".tgz", ".zip"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_", ".")).strip("._-")
    return cleaned or "theme"


def _find_project_root(extract_dir: Path) -> Optional[Path]:
    """Return a likely project root for source builds, or None."""
    candidates = [extract_dir]
    children = [path for path in extract_dir.iterdir() if path.is_dir()]
    if len(children) == 1:
        candidates.insert(0, children[0])

    for candidate in candidates:
        if any((candidate / marker).exists() for marker in _SOURCE_BUILD_MARKERS):
            return candidate
    return None


def _install_built_output(prefix_dir: Path, system_wide: bool) -> list[str]:
    """Install theme directories produced by a source build prefix."""
    installed: list[str] = []
    theme_base = prefix_dir / "share" / "themes"
    icon_base = prefix_dir / "share" / "icons"

    for base in (theme_base, icon_base):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            kind = _classify_theme(child)
            name = _install_theme_folder(child, kind, system_wide)
            if name:
                installed.append(name)
                if kind == "gtk":
                    _apply_gtk4(child)
    return installed


def _build_with_meson(project_root: Path, system_wide: bool) -> list[str]:
    """Build and install a meson-based theme project into a temporary prefix."""
    if not shutil.which("meson"):
        env = detect_environment()
        pm = env.package_manager
        install_steps: list[list[str]] = []

        if pm == "apt":
            install_steps = [
                ["apt-get", "update"],
                ["apt-get", "install", "-y", "meson"],
            ]
        elif pm in ("dnf", "yum"):
            install_steps = [[pm, "install", "-y", "meson"]]
        elif pm == "pacman":
            install_steps = [["pacman", "-Sy", "--noconfirm", "meson"]]
        elif pm == "zypper":
            install_steps = [["zypper", "--non-interactive", "install", "meson"]]

        if install_steps:
            log.info("Meson is missing; attempting to install it using %s", pm)
            if not _run_install_steps(install_steps):
                log.warning("Automatic Meson installation failed; cannot build source theme project at %s", project_root)
                return []

        if not shutil.which("meson"):
            log.warning("Meson is not installed; cannot build source theme project at %s", project_root)
            return []

    build_dir = project_root / "_ltm_build"
    prefix_dir = project_root / "_ltm_prefix"

    log.info("No packaged theme found; attempting Meson source build in %s", project_root.name)
    log.info("Running: meson setup %s --prefix=%s", build_dir.name, prefix_dir)
    setup = subprocess.run(
        ["meson", "setup", str(build_dir), f"--prefix={prefix_dir}"],
        cwd=str(project_root),
        timeout=900,
        check=False,
    )
    if setup.returncode != 0:
        log.error("Meson setup failed with exit code %d", setup.returncode)
        return []

    log.info("Running: meson install -C %s", build_dir.name)
    install = subprocess.run(
        ["meson", "install", "-C", str(build_dir)],
        cwd=str(project_root),
        timeout=1800,
        check=False,
    )
    if install.returncode != 0:
        log.error("Meson install failed with exit code %d", install.returncode)
        return []

    return _install_built_output(prefix_dir, system_wide)


def _try_source_build(extract_dir: Path, system_wide: bool) -> list[str]:
    """Try source-build fallbacks for archives that do not contain packaged themes."""
    project_root = _find_project_root(extract_dir)
    if project_root is None:
        return []

    if (project_root / "meson.build").exists():
        return _build_with_meson(project_root, system_wide)

    return []


def _fix_permissions(path: Path) -> None:
    """Set 755 on directories and 644 on files under *path*."""
    for dirpath, dirnames, filenames in os.walk(path):
        os.chmod(dirpath, 0o755)
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                os.chmod(fpath, 0o644)
            except OSError:
                pass


def _run_install_script(theme_root: Path) -> None:
    """Execute the first recognised install script found in *theme_root*."""
    for name in _INSTALL_SCRIPTS:
        script = theme_root / name
        if not script.is_file():
            continue
        log.info("Running install script: %s", script)
        # Ensure executable bit is set
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        try:
            result = subprocess.run(
                ["bash", name],
                cwd=str(theme_root),
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                log.warning("Install script exited with code %d", result.returncode)
        except subprocess.TimeoutExpired:
            log.error("Install script timed out.")
        return


def _apply_gtk4(theme_root: Path) -> None:
    """Copy GTK-4.0 assets from a theme into ~/.config/gtk-4.0."""
    gtk4_src = theme_root / "gtk-4.0"
    if not gtk4_src.is_dir():
        return

    GTK4_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for item in gtk4_src.iterdir():
        dest = GTK4_CONFIG_DIR / item.name
        try:
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        except OSError as exc:
            log.warning("Could not copy %s → %s: %s", item.name, dest, exc)

    log.info("Applied GTK-4.0 theme files to %s", GTK4_CONFIG_DIR)


def _install_theme_folder(src: Path, kind: str, system_wide: bool, install_name: Optional[str] = None) -> Optional[str]:
    """
    Copy a single theme folder *src* to the appropriate base directory.
    Returns the theme name on success, or None on failure.
    """
    if kind == "extension":
        uuid = _extension_uuid(src)
        if not uuid:
            log.error("Extension directory '%s' is missing a valid metadata.json UUID.", src)
            return None
        if system_wide:
            if os.getuid() != 0:
                log.error("System-wide extension installation requires root privileges.")
                return None
            dest_base = _EXTENSIONS_DIRS[1]
        else:
            dest_base = _EXTENSIONS_DIRS[0]
        dest_base.mkdir(parents=True, exist_ok=True)
        dest = dest_base / uuid
        if dest.exists():
            log.warning("Extension '%s' already exists at %s – overwriting.", uuid, dest)
            shutil.rmtree(dest)
        try:
            shutil.copytree(src, dest)
        except OSError as exc:
            log.error("Failed to copy extension '%s': %s", uuid, exc)
            return None
        _fix_permissions(dest)
        log.info("Installed GNOME extension '%s' → %s", uuid, dest)
        return uuid

    if kind == "shell":
        dest_base = SYS_SHELL_THEMES_DIR if system_wide else USER_SHELL_THEMES_DIR
    elif kind in ("icons", "cursors"):
        dest_base = SYS_ICONS_DIR if system_wide else USER_ICONS_DIR
    else:
        dest_base = SYS_THEMES_DIR if system_wide else USER_THEMES_DIR

    if system_wide and os.getuid() != 0:
        log.error(
            "System-wide installation requires root privileges. "
            "Re-run with sudo or omit --system."
        )
        return None

    dest_base.mkdir(parents=True, exist_ok=True)
    final_name = install_name or src.name
    dest = dest_base / final_name

    if dest.exists():
        log.warning("Theme '%s' already exists at %s – overwriting.", final_name, dest)
        shutil.rmtree(dest)

    try:
        shutil.copytree(src, dest)
    except OSError as exc:
        log.error("Failed to copy theme '%s': %s", final_name, exc)
        return None

    _fix_permissions(dest)
    log.info("Installed '%s' (%s) → %s", final_name, kind, dest)
    return final_name


# ── Public API ─────────────────────────────────────────────────────────────────

def extract_archive(archive_path: str, system_wide: bool = False) -> list[str]:
    """
    Extract a theme archive and install the contents into the appropriate
    theme directories.

    Parameters
    ----------
    archive_path:
        Path to a .zip, .tar.gz, .tgz, .tar.bz2, or .tar.xz file.
    system_wide:
        If True, install into /usr/share/… (requires root).

    Returns
    -------
    List of installed theme names.
    """
    archive = Path(archive_path).resolve()
    if not archive.exists():
        raise FileNotFoundError(f"Archive not found: {archive}")

    # Use a simple numeric suffix to avoid exposing archive names in temp folders
    import uuid as uuid_module
    extract_id = str(uuid_module.uuid4())[:8]
    extract_dir = archive.parent / f"_tm_extract_{extract_id}"
    extract_dir.mkdir(exist_ok=True)

    try:
        log.info("Extracting %s …", archive.name)

        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(extract_dir)

        elif tarfile.is_tarfile(archive):
            with tarfile.open(archive, "r:*") as tf:
                # Use 'data' filter (Python ≥ 3.12) to prevent path traversal;
                # fall back gracefully on older interpreters.
                try:
                    tf.extractall(extract_dir, filter="data")  # type: ignore[arg-type]
                except TypeError:
                    tf.extractall(extract_dir)

        else:
            raise ValueError(f"Unsupported archive format: {archive.suffix}")

        installed: list[str] = []
        has_shell_theme = False
        
        roots = _find_theme_roots(extract_dir)
        # Fallback: if no standard theme roots found, try shell-theme-specific detection
        if not roots:
            roots = _find_shell_theme_roots(extract_dir)
            if roots:
                log.info("Found shell themes using specialized detector")

        archive_name = _name_from_archive(archive)

        for root in roots:
            kind = _classify_theme(root)
            install_name = archive_name if root == extract_dir or root.name.startswith("_tm_extract_") else None
            name = _install_theme_folder(root, kind, system_wide, install_name=install_name)
            if name:
                installed.append(name)
                if kind == "shell":
                    has_shell_theme = True
                _run_install_script(root)
                if kind == "gtk":
                    _apply_gtk4(root)

        if not installed:
            installed = _try_source_build(extract_dir, system_wide)

        if not installed:
            log.warning("No recognisable theme directories were found in the archive.")
        
        # Ensure User Themes extension is enabled if shell themes were installed
        if has_shell_theme and not system_wide:
            from .extensions import is_extension_installed, enable_extension_with_reason
            if not is_extension_installed("user-theme@gnome-shell-extensions.gcampax.github.com"):
                log.info("User Themes extension not found; shell theme will require manual installation.")
            else:
                success, msg = enable_extension_with_reason("user-theme@gnome-shell-extensions.gcampax.github.com")
                if success:
                    log.info("User Themes extension enabled: %s", msg)
                else:
                    log.warning("Could not enable User Themes extension: %s", msg)

        return installed

    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
