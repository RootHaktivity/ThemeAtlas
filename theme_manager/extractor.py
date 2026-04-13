"""
Archive extraction and theme-file placement.

Supports .zip, .tar.gz, .tgz, .tar.bz2, .tar.xz archives.
Detects theme type (gtk / icons / cursors / shell) and copies files to the
correct user or system directory.  Optionally runs an embedded install script
and propagates GTK-4.0 assets to ~/.config/gtk-4.0.

Source Build Fallback
======================
When an archive contains theme source code instead of pre-built releases,
ThemeAtlas can attempt to build and install directly from source.

Supported build systems:
  - Meson (via 'meson setup' and 'meson install')
  - GNU Autotools (via './configure && make install')
  - CMake (via 'cmake --build && cmake --install')

Security Model:
  • Source builds require explicit user consent (--allow-source-build flag).
  • Build tools are automatically installed only if permission to build was given.
  • Builds run in temporary prefixes to isolate theme files.
  • Build output is restricted to theme artifacts (icons, gtk-X.X, cursors, etc.).
  • All build commands are logged for audit purposes.
  • Builds timeout after 30 minutes to prevent infinite loops.

Examples:
  # Install a source theme repository:
  themeatlas install --archive ~/github-theme.tar.xz --allow-source-build

  # Preview before building:
  themeatlas install --archive ~/github-theme.tar.xz --dry-run
  # (will show if source build is needed)
"""

import os
import json
import configparser
import re
import shutil
import site
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .dependencies import _run_install_steps
from .manager import record_installed_app
from .environment import detect_environment
from .extensions import _EXTENSIONS_DIRS, extension_is_compatible_with_shell
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
_SOURCE_BUILD_MARKERS = ("meson.build", "configure.ac", "configure", "CMakeLists.txt")
_BUILD_SYSTEM_DETECTION = {
    "meson.build": "meson",
    "configure.ac": "autoconf",
    "configure": "autoconf",
    "CMakeLists.txt": "cmake",
}

# Stock icon bases that can appear in build prefixes but are not user theme names.
_SKIP_BUILT_ICON_BASES = frozenset({"hicolor", "adwaita", "default", "gnome", "highcontrast"})


def _is_within_dir(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _safe_zip_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    for member in zf.infolist():
        member_path = (dest / member.filename).resolve()
        if not _is_within_dir(dest, member_path):
            raise ValueError(f"Unsafe zip path detected: {member.filename}")
        zf.extract(member, dest)


def _safe_tar_extract(tf: tarfile.TarFile, dest: Path) -> None:
    safe_members: list[tarfile.TarInfo] = []
    for member in tf.getmembers():
        if member.issym() or member.islnk():
            raise ValueError(f"Tar archive contains symbolic link: {member.name}")
        member_path = (dest / member.name).resolve()
        if not _is_within_dir(dest, member_path):
            raise ValueError(f"Unsafe tar path detected: {member.name}")
        safe_members.append(member)

    try:
        tf.extractall(dest, members=safe_members, filter="data")  # type: ignore[arg-type]
    except TypeError:
        tf.extractall(dest, members=safe_members)


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


def _detect_build_system(project_root: Path) -> Optional[str]:
    """Detect build system (meson, autoconf, cmake) in project root."""
    for filename, build_type in _BUILD_SYSTEM_DETECTION.items():
        if (project_root / filename).exists():
            return build_type
    return None


def _find_project_root(extract_dir: Path) -> Optional[Path]:
    """Return a likely project root for source builds, or None."""
    candidates = [extract_dir]
    children = [path for path in extract_dir.iterdir() if path.is_dir()]
    if len(children) == 1:
        candidates.insert(0, children[0])

    for candidate in candidates:
        if _detect_build_system(candidate):
            return candidate

    # Fallback for repositories that keep build files below the top-level dir.
    # Limit depth to avoid scanning huge trees and keep behavior predictable.
    stack: list[tuple[Path, int]] = [(extract_dir, 0)]
    seen: set[Path] = set()
    max_depth = 3
    while stack:
        node, depth = stack.pop(0)
        if node in seen:
            continue
        seen.add(node)

        if _detect_build_system(node):
            return node

        if depth >= max_depth:
            continue

        try:
            for child in node.iterdir():
                if child.is_dir() and not child.name.startswith('.'):
                    stack.append((child, depth + 1))
        except OSError:
            continue

    return None


def _install_built_output(prefix_dir: Path, system_wide: bool, app_name: str = "") -> list[str]:
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
            if base == icon_base and child.name.lower() in _SKIP_BUILT_ICON_BASES:
                log.info("Skipping stock icon base from build output: %s", child.name)
                continue
            kind = _classify_theme(child)
            name = _install_theme_folder(child, kind, system_wide)
            if name:
                installed.append(name)
                if kind == "gtk":
                    _apply_gtk4(child, installed_name=name)

    # If no themes or icons were installed, attempt to install as an application
    # (executables, desktop entries, and app data to ~/.local).
    if not installed:
        installed = _install_built_app_from_prefix(prefix_dir, app_name=app_name)

    return installed


def _rewrite_python_shebang(script: Path) -> None:
    """Replace '#!/usr/bin/env python3' with the absolute system Python path.

    Installed GUI apps (e.g. Gradience) use '#!/usr/bin/env python3' which
    resolves to whichever python3 is first on PATH.  When the ThemeAtlas venv
    is active that resolves to the venv interpreter, which never includes
    /usr/lib/python3/dist-packages (where python3-gi lives).  Writing the
    absolute /usr/bin/python3 path instead ensures the script always uses the
    system Python and can see GObject Introspection bindings.
    """
    try:
        raw = script.read_bytes()
        if not raw.startswith(b"#!"):
            return
        # Only touch Python shebangs
        first_line_end = raw.find(b"\n")
        first_line = raw[:first_line_end] if first_line_end != -1 else raw
        if b"python" not in first_line:
            return
        # Locate the real system python3 (not the venv one)
        system_python = shutil.which("python3", path="/usr/bin:/usr/local/bin") or "/usr/bin/python3"
        new_shebang = f"#!{system_python}\n".encode()
        if first_line_end != -1:
            new_content = new_shebang + raw[first_line_end + 1 :]
        else:
            new_content = new_shebang
        script.write_bytes(new_content)
        log.info("Rewrote Python shebang in %s → %s", script.name, system_python)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not rewrite shebang in %s (non-fatal): %s", script.name, exc)


def _rewrite_installed_script_paths(script: Path, prefix_dir: Path, local_root: Path) -> None:
    """Rewrite generated launcher paths from a temporary build prefix to ~/.local."""
    try:
        text = script.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return

    updated = text.replace(str(prefix_dir), str(local_root))
    user_site = site.getusersitepackages()
    updated = re.sub(
        r'sys\.path\.insert\(1,\s*["\'][^"\']*(?:site|dist)-packages["\']\)',
        f'sys.path.insert(1, "{user_site}")',
        updated,
    )
    local_share = str(local_root / "share")
    if 'is_local = False\n\nif is_local:' in updated:
        updated = updated.replace(
            'is_local = False\n\nif is_local:',
            'is_local = False\n\nos.environ["XDG_DATA_DIRS"] = ' + repr(local_share) + ' + ":" + os.environ.get("XDG_DATA_DIRS", "")\n\nif is_local:',
            1,
        )

    if updated == text:
        return

    try:
        script.write_text(updated, encoding="utf-8")
        log.info("Rewrote generated launcher paths in %s", script)
    except OSError as exc:
        log.warning("Could not rewrite generated launcher paths in %s: %s", script, exc)


def _copy_python_packages_to_user_site(lib_src: Path) -> list[str]:
    """Copy Python packages from a build prefix into the active user's site-packages."""
    user_site = Path(site.getusersitepackages())
    copied_any = False

    for py_root in sorted(lib_src.glob("python*")):
        if not py_root.is_dir():
            continue
        for pkg_dir_name in ("site-packages", "dist-packages"):
            pkg_src = py_root / pkg_dir_name
            if not pkg_src.is_dir():
                continue

            user_site.mkdir(parents=True, exist_ok=True)
            for child in sorted(pkg_src.iterdir()):
                dst = user_site / child.name
                try:
                    if child.is_dir():
                        shutil.copytree(str(child), str(dst), dirs_exist_ok=True)
                    else:
                        shutil.copy2(str(child), str(dst))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Could not copy Python package %s to %s: %s", child, dst, exc)
                    continue
                copied_any = True

    return [str(user_site)] if copied_any else []


def _apply_known_app_runtime_patches(user_site: Path) -> None:
    """Apply narrow compatibility patches for known source-built Python apps.

    These are post-install fixes for upstream apps whose generated launchers or
    startup code are not robust on current distros/Python versions.
    """
    gradience_main = user_site / "gradience" / "frontend" / "main.py"
    if gradience_main.is_file():
        try:
            text = gradience_main.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text:
            updated = text
            marker = '            logging.debug(f"Loaded custom CSS variables: {variables}")\n'
            guard = '\n            if "window_bg_color" not in variables:\n                raise KeyError("window_bg_color")\n'
            if marker in updated and 'raise KeyError("window_bg_color")' not in updated:
                updated = updated.replace(marker, marker + guard, 1)

            if '        self.load_preset_from_css()\n' in updated and '        self.win.present()\n        self.load_preset_from_css()\n' not in updated:
                updated = updated.replace(
                    '        self.load_preset_from_css()\n',
                    '        self.win.present()\n        self.load_preset_from_css()\n',
                    1,
                )

            updated = updated.replace(
                '        except OSError:  # fallback to adwaita\n            logging.warning("Custom preset not found. Fallback to Adwaita")\n',
                '        except (OSError, KeyError):  # fallback to adwaita\n            logging.warning("Custom preset is missing required variables. Fallback to Adwaita")\n',
                1,
            )
            if updated != text:
                try:
                    gradience_main.write_text(updated, encoding="utf-8")
                    log.info("Applied Gradience startup compatibility patch in %s", gradience_main)
                except OSError as exc:
                    log.warning("Could not patch Gradience startup compatibility in %s: %s", gradience_main, exc)


def _read_runtime_python_requirements(project_root: Path) -> list[str]:
    """Read simple runtime requirements from a source tree requirements.txt file."""
    req_file = project_root / "requirements.txt"
    if not req_file.is_file():
        return []

    requirements: list[str] = []
    try:
        lines = req_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", "git+", "http://", "https://")):
            continue
        requirements.append(line)
    return requirements


def _install_runtime_python_requirements(
    project_root: Path,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> None:
    """Install a source-built app's declared runtime Python requirements into user site-packages."""
    requirements = _read_runtime_python_requirements(project_root)
    if not requirements:
        return

    system_python = shutil.which("python3", path="/usr/bin:/usr/local/bin") or "/usr/bin/python3"
    pip_probe = subprocess.run(
        [system_python, "-m", "pip", "--version"],
        timeout=20,
        check=False,
        capture_output=True,
        text=True,
    )
    if pip_probe.returncode != 0:
        log.warning("System pip is unavailable; skipping runtime Python dependency install for %s", project_root)
        return

    if progress_callback:
        progress_callback(f"Installing runtime Python dependencies: {', '.join(requirements)}")

    base_cmd = [system_python, "-m", "pip", "install", "--user", *requirements]
    install = subprocess.run(
        base_cmd,
        timeout=1800,
        check=False,
        capture_output=True,
        text=True,
    )
    if install.returncode != 0 and "externally-managed-environment" in ((install.stderr or install.stdout or "").lower()):
        install = subprocess.run(
            [system_python, "-m", "pip", "install", "--user", "--break-system-packages", *requirements],
            timeout=1800,
            check=False,
            capture_output=True,
            text=True,
        )
    if install.returncode != 0:
        detail = (install.stderr or install.stdout or "").strip()
        log.warning("Runtime Python dependency install failed for %s: %s", project_root, detail[:240])
        if progress_callback and detail:
            progress_callback(f"Runtime Python dependency install failed: {detail[:180]}")

    if any(req.lower().startswith("yapsy") for req in requirements):
        user_site = Path(site.getusersitepackages())
        user_site.mkdir(parents=True, exist_ok=True)
        imp_shim = user_site / "imp.py"
        if not imp_shim.exists():
            try:
                imp_shim.write_text(
                    'import importlib.util\n'
                    'import os\n'
                    'import sys\n\n'
                    'PY_SOURCE = 1\n'
                    'PKG_DIRECTORY = 5\n\n'
                    'def load_module(name, file_obj, pathname, description):\n'
                    '    kind = description[2]\n'
                    '    if kind == PKG_DIRECTORY:\n'
                    '        init_path = os.path.join(pathname, "__init__.py")\n'
                    '        spec = importlib.util.spec_from_file_location(name, init_path, submodule_search_locations=[pathname])\n'
                    '    else:\n'
                    '        spec = importlib.util.spec_from_file_location(name, pathname)\n'
                    '    if spec is None or spec.loader is None:\n'
                    '        raise ImportError(f"Cannot load module {name} from {pathname}")\n'
                    '    module = sys.modules.get(name)\n'
                    '    if module is None:\n'
                    '        module = importlib.util.module_from_spec(spec)\n'
                    '        sys.modules[name] = module\n'
                    '    spec.loader.exec_module(module)\n'
                    '    return module\n',
                    encoding="utf-8",
                )
            except OSError as exc:
                log.warning("Could not install imp compatibility shim for yapsy: %s", exc)


# GObject Introspection namespace → apt package name for typelibs needed at runtime.
_GI_NAMESPACE_TO_APT: dict[str, str] = {
    "Gtk":        "gir1.2-gtk-4.0",        # GTK 4
    "Gtk3":       "gir1.2-gtk-3.0",        # GTK 3 (rare alternate alias)
    "Adw":        "gir1.2-adw-1",          # libadwaita
    "Soup":       "gir1.2-soup-3.0",       # libsoup 3
    "Xdp":        "gir1.2-xdp-1.0",       # libportal (XDG portal)
    "XdpGtk4":   "gir1.2-xdpgtk4-1.0",   # libportal-gtk4 typelib
    "XdpGtk3":   "gir1.2-xdpgtk3-1.0",   # libportal-gtk3 typelib
    "AppStream":  "gir1.2-appstream-1.0",  # AppStream
    "Notify":     "gir1.2-notify-0.7",     # libnotify
    "Pango":      "gir1.2-pango-1.0",      # pango
    "GdkPixbuf":  "gir1.2-gdkpixbuf-2.0", # gdk-pixbuf
    "Gio":        "gir1.2-glib-2.0",       # GLib/Gio
    "GLib":       "gir1.2-glib-2.0",
    "GObject":    "gir1.2-glib-2.0",
    "GtkSource":  "gir1.2-gtksource-5",    # GtkSourceView 5
}


def _ensure_gi_typelibs_for_scripts(
    scripts: list[Path],
    progress_callback: Optional[Callable[[str], None]] = None,
) -> None:
    """Scan installed Python scripts for gi.require_version() calls and install missing typelibs.

    Python GUI apps built with meson declare GObject Introspection namespaces at
    runtime via gi.require_version('Xdp', '1.0').  These map to separate typelib
    packages (gir1.2-*) that are not pulled in by the build-time pkg-config deps.
    This function detects and batch-installs any that are not yet present.
    """
    env = detect_environment()
    pm = env.package_manager
    if pm not in ("apt",):
        # Only apt mapping is implemented; other distros skip silently.
        return

    needed: list[tuple[str, Optional[str]]] = []
    for script in scripts:
        try:
            text = script.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r"gi\.require_version\(\s*['\"]([A-Za-z0-9_]+)['\"]", text):
            ns = m.group(1)
            pkg = _GI_NAMESPACE_TO_APT.get(ns)
            if not pkg:
                continue
            # Check if the typelib is already installed by probing its file
            typelib_dir = Path("/usr/lib/x86_64-linux-gnu/girepository-1.0")
            if not typelib_dir.is_dir():
                # Try common ARM/generic path
                typelib_dir = Path("/usr/lib/girepository-1.0")
            # Probe by asking dpkg whether the package is installed
            probe = subprocess.run(
                ["dpkg-query", "-W", "-f=${Status}", pkg],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if "install ok installed" in (probe.stdout or "").lower():
                continue
            log.info("Missing GI typelib package: %s (for namespace %s)", pkg, ns)
            if progress_callback:
                progress_callback(f"Installing missing GObject typelib: {pkg}...")
            needed.append((pkg, None))

    if needed:
        # Deduplicate
        seen: set[str] = set()
        unique = [(p, f) for p, f in needed if p not in seen and not seen.add(p)]  # type: ignore[func-returns-value]
        _batch_install_tools(unique, progress_callback)


def _install_built_app_from_prefix(prefix_dir: Path, app_name: str = "") -> list[str]:
    """Copy application artifacts from a meson build prefix into ~/.local.

    Handles binaries (bin/), desktop entries (share/applications/), app-specific
    data directories, and GLib schemas.  Always installs to the current user's
    ~/.local tree regardless of the system_wide flag so that no elevated
    privileges are required.
    """
    local = Path.home() / ".local"
    installed: list[str] = []
    tracked_binaries: list[str] = []
    tracked_share_dirs: list[str] = []
    tracked_lib_dirs: list[str] = []
    python_scripts: list[Path] = []

    # 1. Copy executables from prefix/bin/ → ~/.local/bin/
    bin_src = prefix_dir / "bin"
    if bin_src.is_dir():
        bin_dst = local / "bin"
        bin_dst.mkdir(parents=True, exist_ok=True)
        for exe in sorted(bin_src.iterdir()):
            if exe.is_file():
                dst = bin_dst / exe.name
                shutil.copy2(str(exe), str(dst))
                dst.chmod(dst.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                _rewrite_python_shebang(dst)
                log.info("Installed application binary: %s", dst)
                installed.append(exe.name)
                tracked_binaries.append(str(dst))
                python_scripts.append(dst)

        # Scan the installed Python scripts for gi.require_version() calls and
        # auto-install any missing GObject Introspection typelib packages.
        _ensure_gi_typelibs_for_scripts(python_scripts)

    # 1b. Copy Python package from prefix/lib/ → ~/.local/lib/
    lib_src = prefix_dir / "lib"
    if lib_src.is_dir():
        lib_dst = local / "lib"
        lib_dst.mkdir(parents=True, exist_ok=True)
        try:
            if lib_dst.exists():
                shutil.copytree(str(lib_src), str(lib_dst), dirs_exist_ok=True)
            else:
                shutil.copytree(str(lib_src), str(lib_dst))
            tracked_lib_dirs.append(str(lib_dst))
            user_site_paths = _copy_python_packages_to_user_site(lib_src)
            tracked_lib_dirs.extend(user_site_paths)
            if user_site_paths:
                _apply_known_app_runtime_patches(Path(user_site_paths[0]))
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not copy lib/ to ~/.local/lib: %s", exc)

    # 2. Copy share/ subdirectories (skip themes/icons which are handled separately)
    share_src = prefix_dir / "share"
    if share_src.is_dir():
        local_share = local / "share"
        local_share.mkdir(parents=True, exist_ok=True)
        _SKIP_SHARE_DIRS = {"themes", "icons"}
        for sub in sorted(share_src.iterdir()):
            if not sub.is_dir() or sub.name in _SKIP_SHARE_DIRS:
                continue
            dst = local_share / sub.name
            try:
                if dst.exists():
                    shutil.copytree(str(sub), str(dst), dirs_exist_ok=True)
                else:
                    shutil.copytree(str(sub), str(dst))
                tracked_share_dirs.append(str(dst))
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not copy share/%s to ~/.local/share: %s", sub.name, exc)

        # Compile GLib schemas so settings-based apps work immediately
        schemas_dir = local_share / "glib-2.0" / "schemas"
        if schemas_dir.is_dir() and shutil.which("glib-compile-schemas"):
            try:
                subprocess.run(
                    ["glib-compile-schemas", str(schemas_dir)],
                    timeout=30,
                    check=False,
                    capture_output=True,
                )
                log.info("Compiled GLib schemas in %s", schemas_dir)
            except Exception as exc:  # noqa: BLE001
                log.warning("glib-compile-schemas failed (non-fatal): %s", exc)

    for script in python_scripts:
        _rewrite_installed_script_paths(script, prefix_dir, local)

    if installed and (tracked_binaries or tracked_share_dirs or tracked_lib_dirs):
        display_name = app_name or (installed[0].replace("-", " ").replace("_", " ").title())
        try:
            record_installed_app(
                display_name,
                binaries=tracked_binaries,
                share_dirs=tracked_share_dirs,
                lib_dirs=tracked_lib_dirs,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not write installed-app manifest (non-fatal): %s", exc)

    return installed


def _batch_install_tools(
    tools: list[tuple[str, Optional[str]]],
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, bool]:
    """
    Install multiple tools in a single elevated session to minimize password prompts.
    
    Args:
        tools: List of (primary_tool, fallback_tool) tuples. fallback can be None.
        progress_callback: Optional callback for progress updates.
    
    Returns:
        Dict mapping tool names to installation success status.
    """
    env = detect_environment()
    pm = env.package_manager
    
    if not pm or pm not in ("apt", "dnf", "yum", "pacman", "zypper"):
        # No supported package manager; fall back to individual installations
        results = {}
        for tool, fallback in tools:
            results[tool] = _ensure_build_tool(tool, fallback, progress_callback)
        return results
    
    # Collect all packages to install
    def _is_safe_package_name(name: str) -> bool:
        if not name:
            return False
        if name.startswith("-"):
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._:@/-]*", name))

    packages_to_install: list[str] = []
    tool_to_package: dict[str, tuple[str, Optional[str]]] = {}  # Maps tool -> (primary_pkg, fallback_pkg)
    npm_tools: list[str] = []  # Tools that should be installed via npm if package manager fails
    invalid_tools: set[str] = set()
    
    for tool, fallback in tools:
        if shutil.which(tool):
            continue  # Already installed
        
        # Special handling for dart-sass: prefer npm installation since it's not in most repos
        if tool in ("dart-sass", "sass"):
            # Check if npm is available for dart-sass fallback
            if shutil.which("npm"):
                npm_tools.append(tool)
                # Still try package manager first, but mark for npm fallback
                primary = "dart-sass"
                fallback_pkg = "sassc" if fallback == "sassc" else fallback
            else:
                primary = "dart-sass"
                fallback_pkg = "sassc" if fallback == "sassc" else fallback
        else:
            primary = tool
            fallback_pkg = fallback
        
        if not _is_safe_package_name(primary):
            log.warning("Skipping unsafe package name for tool %s: %s", tool, primary)
            invalid_tools.add(tool)
            continue

        clean_fallback = fallback_pkg if (fallback_pkg and _is_safe_package_name(fallback_pkg)) else None
        if fallback_pkg and clean_fallback is None:
            log.warning("Ignoring unsafe fallback package for tool %s: %s", tool, fallback_pkg)

        tool_to_package[tool] = (primary, clean_fallback)
        packages_to_install.append(primary)
    
    if not packages_to_install:
        # All tools already installed (except any invalid entries, which fail closed).
        result = {tool: True for tool, _ in tools}
        for tool in invalid_tools:
            result[tool] = False
        return result
    
    # Build installation steps
    install_steps: list[list[str]] = []
    
    if pm == "apt":
        install_steps.append(["apt-get", "update"])
        install_steps.append(["apt-get", "install", "-y", "--"] + packages_to_install)
    elif pm in ("dnf", "yum"):
        install_steps.append([pm, "install", "-y", "--"] + packages_to_install)
    elif pm == "pacman":
        install_steps.append(["pacman", "-Sy", "--noconfirm", "--"] + packages_to_install)
    elif pm == "zypper":
        install_steps.append(["zypper", "--non-interactive", "install", "--"] + packages_to_install)
    
    if progress_callback:
        progress_callback(f"Installing build tools: {', '.join(packages_to_install)}")
    
    log.info("Batch installing tools: %s", ", ".join(packages_to_install))
    success = _run_install_steps(install_steps)
    
    # Check which tools are now available
    results: dict[str, bool] = {}
    for tool, _ in tools:
        if tool in invalid_tools:
            results[tool] = False
            continue

        if shutil.which(tool):
            results[tool] = True
            continue
        
        # Primary failed; try fallback if specified
        primary_pkg, fallback_pkg = tool_to_package.get(tool, (tool, None))
        
        # Special case: dart-sass via npm if package manager doesn't have it
        if tool in npm_tools and tool in ("dart-sass", "sass"):
            if progress_callback:
                progress_callback(f"Installing {tool} via npm (not available in {pm})...")
            log.info("Package %s not available in %s; trying npm install", tool, pm)
            
            # Try to install sass via npm globally
            npm_install_steps = []
            if shutil.which("npm"):
                npm_install_steps.append(["npm", "install", "-g", "sass"])
                
                # npm doesn't usually need sudo for global installs if npm is configured properly,
                # but we'll use pkexec/sudo if we're already in that context
                if _run_install_steps(npm_install_steps):
                    # Verify sass command is now available
                    if shutil.which("sass"):
                        log.info("Successfully installed Dart Sass via npm")
                        results[tool] = True
                        continue
                    log.warning("npm install succeeded but 'sass' command not found; trying with elevated privileges")
                    # Try with elevated privileges
                    elevated_npm_steps = []
                    if shutil.which("pkexec"):
                        elevated_npm_steps.append(["pkexec", "npm", "install", "-g", "sass"])
                    elif shutil.which("sudo"):
                        elevated_npm_steps.append(["sudo", "npm", "install", "-g", "sass"])
                    
                    if elevated_npm_steps and _run_install_steps(elevated_npm_steps):
                        if shutil.which("sass"):
                            log.info("Successfully installed Dart Sass via npm (elevated)")
                            results[tool] = True
                            continue
        
        # If npm didn't work or not applicable, try package manager fallback
        if fallback_pkg and not shutil.which(tool):
            # Install fallback
            fallback_steps: list[list[str]] = []
            if pm == "apt":
                fallback_steps.append(["apt-get", "update"])
                fallback_steps.append(["apt-get", "install", "-y", "--", fallback_pkg])
            elif pm in ("dnf", "yum"):
                fallback_steps.append([pm, "install", "-y", "--", fallback_pkg])
            elif pm == "pacman":
                fallback_steps.append(["pacman", "-Sy", "--noconfirm", "--", fallback_pkg])
            elif pm == "zypper":
                fallback_steps.append(["zypper", "--non-interactive", "install", "--", fallback_pkg])
            
            if progress_callback:
                progress_callback(f"Installing fallback for {tool}: {fallback_pkg}")
            
            log.info("Installing fallback for %s: %s", tool, fallback_pkg)
            if _run_install_steps(fallback_steps):
                results[tool] = shutil.which(tool) is not None or shutil.which(fallback_pkg) is not None
            else:
                results[tool] = False
        else:
            results[tool] = False
    
    return results


def _ensure_build_tool(tool: str, fallback: Optional[str] = None, progress_callback: Optional[Callable[[str], None]] = None) -> bool:
    """Ensure a build tool is installed. Returns True if available. Tries primary tool, then fallback if provided."""
    if shutil.which(tool):
        return True

    env = detect_environment()
    pm = env.package_manager
    install_steps: list[list[str]] = []

    pkg_map = {
        "apt": (["apt-get", "update"], ["apt-get", "install", "-y", "--", tool]),
        "dnf": (None, ["dnf", "install", "-y", "--", tool]),
        "yum": (None, ["yum", "install", "-y", "--", tool]),
        "pacman": (None, ["pacman", "-Sy", "--noconfirm", "--", tool]),
        "zypper": (None, ["zypper", "--non-interactive", "install", "--", tool]),
    }

    if pm in pkg_map:
        init, install = pkg_map[pm]
        if init:
            install_steps.append(init)
        install_steps.append(install)

        log.info("%s is missing; attempting to install package '%s' using %s", tool, tool, pm)
        if progress_callback:
            progress_callback(f"Installing {tool}...")
        if _run_install_steps(install_steps):
            if shutil.which(tool):
                if progress_callback:
                    progress_callback(f"Successfully installed {tool}")
                return True

        # Try fallback if primary failed
        if fallback and fallback != tool:
            log.info("Installation of %s failed; trying fallback %s", tool, fallback)
            if progress_callback:
                progress_callback(f"Trying fallback: {fallback}...")
            
            install_steps = []
            fallback_install = pkg_map[pm][1].copy()
            fallback_install[-1] = fallback  # Replace tool name with fallback
            if pkg_map[pm][0]:
                install_steps.append(pkg_map[pm][0])
            install_steps.append(fallback_install)
            
            if _run_install_steps(install_steps):
                if shutil.which(fallback):
                    if progress_callback:
                        progress_callback(f"Successfully installed fallback {fallback}")
                    return True
        
        log.warning("Automatic installation of %s (and fallback %s) failed", tool, fallback or "none")
    else:
        log.warning("No package manager found to install %s. Install manually: %s", tool, tool)

    return False


def _parse_gitmodules(project_root: Path) -> list[tuple[str, str]]:
    """Parse .gitmodules in *project_root* and return (path, url) entries."""
    gitmodules = project_root / ".gitmodules"
    if not gitmodules.exists():
        return []

    parser = configparser.ConfigParser()
    try:
        parser.read(gitmodules, encoding="utf-8")
    except (OSError, configparser.Error):
        return []

    entries: list[tuple[str, str]] = []
    for section in parser.sections():
        if not section.lower().startswith("submodule "):
            continue
        path = (parser.get(section, "path", fallback="") or "").strip()
        url = (parser.get(section, "url", fallback="") or "").strip()
        if not path or not url:
            continue
        entries.append((path, url))
    return entries


def _hydrate_submodules_from_gitmodules(
    project_root: Path,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Best-effort clone for missing submodule paths declared in .gitmodules."""
    if not shutil.which("git"):
        return False

    entries = _parse_gitmodules(project_root)
    if not entries:
        return False

    cloned_any = False
    for rel_path, url in entries:
        sub_path = (project_root / rel_path).resolve()
        if not _is_within_dir(project_root, sub_path):
            log.warning("Skipping unsafe submodule path outside project root: %s", rel_path)
            continue

        if sub_path.exists():
            try:
                if any(sub_path.iterdir()):
                    continue
            except OSError:
                pass

        # Relative URLs from .gitmodules need parent repo context we do not have in archives.
        if url.startswith("../") or url.startswith("./"):
            log.warning("Skipping relative submodule URL without repository context: %s", url)
            continue

        sub_path.parent.mkdir(parents=True, exist_ok=True)
        if progress_callback:
            progress_callback(f"Fetching submodule content for {rel_path}...")

        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--recurse-submodules", url, str(sub_path)],
                cwd=str(project_root),
                timeout=300,
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            log.warning("Submodule clone failed for %s (%s): %s", rel_path, url, exc)
            continue

        if result.returncode != 0:
            log.warning(
                "Submodule clone failed for %s (%s): %s",
                rel_path,
                url,
                (result.stderr or "").strip()[:200],
            )
            continue

        cloned_any = True
        log.info("Hydrated submodule path: %s", rel_path)

    return cloned_any


def _build_with_meson(project_root: Path, system_wide: bool, progress_callback: Optional[Callable[[str], None]] = None, *, app_name: str = "") -> list[str]:
    """Build and install a meson-based theme project into a temporary prefix.

    Handles three categories of missing dependencies:
      1. Programs  — ``Program 'X' not found``
      2. Libraries — ``Dependency "X" not found`` (pkg-config / dev packages)
      3. Python modules — ``modules: ['X']`` / ``No module named 'X'``

    Each category is resolved iteratively: install what is missing, wipe the
    build directory, re-run ``meson setup``, and repeat until either setup
    succeeds or we exhaust *_MAX_RESOLVE_ATTEMPTS* rounds.
    """

    _MAX_RESOLVE_ATTEMPTS = 8  # hard cap to prevent infinite loops

    # ── Ensure meson itself is available ──────────────────────────────────────
    if not shutil.which("meson"):
        if progress_callback:
            progress_callback("Installing Meson build system...")
        log.info("Meson is missing; installing...")
        _batch_install_tools([("meson", None)], progress_callback)
        if not shutil.which("meson"):
            log.warning("Automatic Meson installation failed; cannot build source project at %s", project_root)
            return []
    if not shutil.which("ninja"):
        if progress_callback:
            progress_callback("Installing Ninja build backend...")
        _batch_install_tools([("ninja-build", "ninja")], progress_callback)
    if not shutil.which("pkg-config"):
        if progress_callback:
            progress_callback("Installing pkg-config...")
        _batch_install_tools([("pkg-config", "pkgconf")], progress_callback)

    build_dir = project_root / "_ltm_build"
    prefix_dir = project_root / "_ltm_prefix"

    # ── pkg-config name → distro package mapping ─────────────────────────────
    # When meson reports  Dependency "gtk4" not found  we need to know that the
    # apt package is "libgtk-4-dev", etc.  This table covers the libraries most
    # commonly required by GNOME/GTK themes & tools.
    _PKGCONFIG_TO_APT: dict[str, str] = {
        # GTK / GNOME core
        "gtk4":            "libgtk-4-dev",
        "gtk+-3.0":        "libgtk-3-dev",
        "gtk+-2.0":        "libgtk2.0-dev",
        "glib-2.0":        "libglib2.0-dev",
        "gio-2.0":         "libglib2.0-dev",
        "gdk-pixbuf-2.0":  "libgdk-pixbuf-2.0-dev",
        "pango":           "libpango1.0-dev",
        "cairo":           "libcairo2-dev",
        "libadwaita-1":    "libadwaita-1-dev",
        "libsoup-3.0":     "libsoup-3.0-dev",
        "libsoup-2.4":     "libsoup2.4-dev",
        "json-glib-1.0":   "libjson-glib-dev",
        "libxml-2.0":      "libxml2-dev",
        "libportal":       "libportal-dev",
        "libportal-gtk4":  "libportal-gtk4-dev",
        "appstream":       "libappstream-dev",
        # GObject introspection / bindings
        "pygobject-3.0":   "python-gi-dev",
        "gobject-introspection-1.0": "gobject-introspection",
        # SCSS / rendering
        "librsvg-2.0":     "librsvg2-dev",
        # misc
        "blueprint-compiler": "blueprint-compiler",
        "sassc":           "sassc",
    }
    _PKGCONFIG_TO_DNF: dict[str, str] = {
        "gtk4":            "gtk4-devel",
        "gtk+-3.0":        "gtk3-devel",
        "gtk+-2.0":        "gtk2-devel",
        "glib-2.0":        "glib2-devel",
        "gio-2.0":         "glib2-devel",
        "gdk-pixbuf-2.0":  "gdk-pixbuf2-devel",
        "pango":           "pango-devel",
        "cairo":           "cairo-devel",
        "libadwaita-1":    "libadwaita-devel",
        "libsoup-3.0":     "libsoup3-devel",
        "libsoup-2.4":     "libsoup-devel",
        "json-glib-1.0":   "json-glib-devel",
        "libxml-2.0":      "libxml2-devel",
        "libportal":       "libportal-devel",
        "libportal-gtk4":  "libportal-gtk4-devel",
        "appstream":       "appstream-devel",
        "pygobject-3.0":   "python3-gobject-devel",
        "gobject-introspection-1.0": "gobject-introspection-devel",
        "librsvg-2.0":     "librsvg2-devel",
        "blueprint-compiler": "blueprint-compiler",
        "sassc":           "sassc",
    }
    _PKGCONFIG_TO_PACMAN: dict[str, str] = {
        "gtk4":            "gtk4",
        "gtk+-3.0":        "gtk3",
        "gtk+-2.0":        "gtk2",
        "glib-2.0":        "glib2",
        "gio-2.0":         "glib2",
        "gdk-pixbuf-2.0":  "gdk-pixbuf2",
        "pango":           "pango",
        "cairo":           "cairo",
        "libadwaita-1":    "libadwaita",
        "libsoup-3.0":     "libsoup3",
        "libsoup-2.4":     "libsoup",
        "json-glib-1.0":   "json-glib",
        "libxml-2.0":      "libxml2",
        "libportal":       "libportal",
        "libportal-gtk4":  "libportal-gtk4",
        "appstream":       "appstream",
        "pygobject-3.0":   "python-gobject",
        "gobject-introspection-1.0": "gobject-introspection",
        "librsvg-2.0":     "librsvg",
        "blueprint-compiler": "blueprint-compiler",
        "sassc":           "sassc",
    }
    _PKGCONFIG_TO_ZYPPER: dict[str, str] = {
        "gtk4":            "gtk4-devel",
        "gtk+-3.0":        "gtk3-devel",
        "glib-2.0":        "glib2-devel",
        "libadwaita-1":    "libadwaita-devel",
        "pygobject-3.0":   "python3-gobject",
        "sassc":           "sassc",
    }

    _PKG_MAPS: dict[str, dict[str, str]] = {
        "apt":    _PKGCONFIG_TO_APT,
        "dnf":    _PKGCONFIG_TO_DNF,
        "yum":    _PKGCONFIG_TO_DNF,       # yum ≈ dnf naming
        "pacman": _PKGCONFIG_TO_PACMAN,
        "zypper": _PKGCONFIG_TO_ZYPPER,
    }

    # Python module → distro package
    _PYMOD_TO_APT: dict[str, str] = {
        "lxml":      "python3-lxml",
        "gi":        "python3-gi",
        "yaml":      "python3-yaml",
        "requests":  "python3-requests",
        "pil":       "python3-pil",
        "PIL":       "python3-pil",
        "cssutils":  "python3-cssutils",
        "svglib":    "python3-svglib",
        "jinja2":    "python3-jinja2",
        "Jinja2":    "python3-jinja2",
    }
    _PYMOD_TO_DNF: dict[str, str] = {
        "lxml":      "python3-lxml",
        "gi":        "python3-gobject",
        "yaml":      "python3-pyyaml",
        "requests":  "python3-requests",
        "PIL":       "python3-pillow",
        "jinja2":    "python3-jinja2",
    }
    _PYMOD_TO_PACMAN: dict[str, str] = {
        "lxml":      "python-lxml",
        "gi":        "python-gobject",
        "yaml":      "python-yaml",
        "requests":  "python-requests",
        "PIL":       "python-pillow",
        "jinja2":    "python-jinja",
    }

    _PYMOD_MAPS: dict[str, dict[str, str]] = {
        "apt":    _PYMOD_TO_APT,
        "dnf":    _PYMOD_TO_DNF,
        "yum":    _PYMOD_TO_DNF,
        "pacman": _PYMOD_TO_PACMAN,
    }

    # Program name → (primary_pkg, fallback_pkg)
    _PROGRAM_PKG_MAP: dict[str, tuple[str, Optional[str]]] = {
        "sass":     ("dart-sass", "sassc"),
        "sassc":    ("sassc", None),
        "glib-compile-schemas":  ("libglib2.0-dev-bin", None),
        "glib-compile-resources": ("libglib2.0-dev-bin", None),
        "gtk-update-icon-cache": ("libgtk-3-dev", None),
        "gtk4-update-icon-cache": ("libgtk-4-dev", None),
        "update-desktop-database": ("desktop-file-utils", None),
        "msgfmt":   ("gettext", None),
        "xgettext":  ("gettext", None),
        "itstool":  ("itstool", None),
        "appstreamcli": ("appstream", None),
        "appstream-util": ("appstream-util", None),
        "blueprint-compiler": ("blueprint-compiler", None),
    }

    env = detect_environment()
    pm = env.package_manager

    log.info("No packaged theme found; attempting Meson source build in %s", project_root.name)

    # Build a clean environment that strips any active Python virtualenv so that
    # meson evaluates Python module availability against the system Python rather
    # than the venv interpreter (which lacks system dist-packages like python3-lxml).
    meson_env: dict[str, str] = os.environ.copy()
    venv_bin = os.environ.get("VIRTUAL_ENV", "")
    if venv_bin:
        venv_bin_path = str(Path(venv_bin) / "bin")
        orig_path = meson_env.get("PATH", "")
        meson_env["PATH"] = ":".join(
            p for p in orig_path.split(":") if p and p != venv_bin_path
        )
        meson_env.pop("VIRTUAL_ENV", None)
        meson_env.pop("PYTHONHOME", None)
    used_sass_shim = False

    # ── Ensure a git repo exists ─────────────────────────────────────────────
    # Many meson.build files call  run_command('git', 'rev-parse', ...)  to
    # embed a VCS tag.  When the project is extracted from an archive there
    # is no .git directory and the command fails with exit code 128.
    # Creating a minimal dummy repo satisfies those calls.
    if not (project_root / ".git").exists() and shutil.which("git"):
        log.info("No .git directory — initialising a dummy git repo for meson compatibility")
        try:
            subprocess.run(
                ["git", "init"],
                cwd=str(project_root), timeout=15,
                check=False, capture_output=True,
            )
            # Set local identity so commit works even without global git config
            subprocess.run(
                ["git", "config", "user.email", "themeatlas@local"],
                cwd=str(project_root), timeout=5,
                check=False, capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "ThemeAtlas"],
                cwd=str(project_root), timeout=5,
                check=False, capture_output=True,
            )
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(project_root), timeout=30,
                check=False, capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "ThemeAtlas source build", "--allow-empty"],
                cwd=str(project_root), timeout=30,
                check=False, capture_output=True,
            )
        except Exception as exc:
            log.debug("Dummy git init failed (non-fatal): %s", exc)

    # ── Pre-scan meson.build files for ALL dependencies ──────────────────────
    # Instead of resolving one-at-a-time (slow, needs password each time),
    # parse every meson.build in the project tree and batch-install everything
    # upfront in a single elevated prompt.
    def _prescan_meson_deps(root: Path) -> tuple[set[str], set[str], set[str]]:
        """Scan all meson.build files for dependency(), find_program(), and modules."""
        deps: set[str] = set()       # pkg-config library names
        progs: set[str] = set()      # program names
        pymods: set[str] = set()     # Python module names

        for meson_file in root.rglob("meson.build"):
            try:
                text = meson_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            # dependency('gtk4', ...)  or  dependency('libadwaita-1')
            for m in re.finditer(r"dependency\(\s*'([^']+)'", text):
                deps.add(m.group(1))

            # find_program('sass', ...)  — skip 'required: false' optionals
            for m in re.finditer(
                r"find_program\(\s*'([^']+)'(?:\s*,\s*([^)]*))?\)", text
            ):
                prog_name = m.group(1)
                rest = m.group(2) or ""
                # Skip if explicitly marked optional
                if "required" in rest and "false" in rest:
                    continue
                progs.add(prog_name)

            # modules: ['lxml', 'yaml']
            for m in re.finditer(r"modules\s*:\s*\[([^\]]+)\]", text):
                for mod_match in re.finditer(r"'([^']+)'", m.group(1)):
                    pymods.add(mod_match.group(1))

        return deps, progs, pymods

    scanned_deps, scanned_progs, scanned_pymods = _prescan_meson_deps(project_root)
    if scanned_deps or scanned_progs or scanned_pymods:
        log.info(
            "Pre-scan found: %d libraries, %d programs, %d Python modules",
            len(scanned_deps), len(scanned_progs), len(scanned_pymods),
        )

        all_packages: list[tuple[str, Optional[str]]] = []

        # Resolve libraries → dev packages
        pkg_map = _PKG_MAPS.get(pm, {})
        for dep in sorted(scanned_deps):
            pkg = pkg_map.get(dep)
            if pkg:
                all_packages.append((pkg, None))
            elif pm == "apt":
                all_packages.append((f"lib{dep}-dev", None))
            elif pm in ("dnf", "yum"):
                all_packages.append((f"{dep}-devel", None))
            else:
                all_packages.append((dep, None))

        # Resolve programs → packages
        for prog in sorted(scanned_progs):
            if shutil.which(prog):
                continue
            if prog in _PROGRAM_PKG_MAP:
                all_packages.append(_PROGRAM_PKG_MAP[prog])
            else:
                all_packages.append((prog, None))

        # Resolve Python modules → packages
        pymod_map = _PYMOD_MAPS.get(pm, {})
        for mod in sorted(scanned_pymods):
            pkg = pymod_map.get(mod)
            if pkg:
                all_packages.append((pkg, None))
            elif pm == "apt":
                all_packages.append((f"python3-{mod.lower()}", None))
            elif pm in ("dnf", "yum"):
                all_packages.append((f"python3-{mod.lower()}", None))
            elif pm == "pacman":
                all_packages.append((f"python-{mod.lower()}", None))

        # Deduplicate while preserving order
        seen_pkgs: set[str] = set()
        unique_packages: list[tuple[str, Optional[str]]] = []
        for pkg, fallback in all_packages:
            if pkg not in seen_pkgs:
                seen_pkgs.add(pkg)
                unique_packages.append((pkg, fallback))

        if unique_packages:
            pkg_names = ", ".join(p for p, _ in unique_packages)
            log.info("Pre-installing all build dependencies: %s", pkg_names)
            if progress_callback:
                progress_callback(f"Installing all build dependencies: {pkg_names}")
            _batch_install_tools(unique_packages, progress_callback)

    def _run_meson_setup() -> subprocess.CompletedProcess:
        log.info("Running: meson setup %s --prefix=%s", build_dir.name, prefix_dir)
        return subprocess.run(
            ["meson", "setup", str(build_dir), f"--prefix={prefix_dir}"],
            cwd=str(project_root),
            timeout=900,
            check=False,
            capture_output=True,
            text=True,
            env=meson_env,
        )

    # ── Helpers to parse meson failure output ─────────────────────────────────

    def _parse_missing_program(output: str) -> Optional[str]:
        """Extract program name from  ``Program 'X' not found``."""
        m = re.search(r"Program '([^']+)' not found", output)
        return m.group(1) if m else None

    def _parse_missing_dependency(output: str) -> Optional[str]:
        """Extract pkg-config name from  ``Dependency "X" not found``."""
        m = re.search(r'Dependency "([^"]+)" not found', output)
        return m.group(1) if m else None

    def _parse_missing_python_module(output: str) -> Optional[str]:
        """Extract module name from meson's Python module check failures."""
        # meson says: modules: ['lxml']  … ERROR
        m = re.search(r"modules:\s*\['([^']+)'\]", output)
        if m:
            return m.group(1)
        # Also catch:  No module named 'X'
        m = re.search(r"No module named '([^']+)'", output)
        return m.group(1) if m else None

    def _parse_missing_submodule_file(output: str) -> Optional[str]:
        """Extract missing submodule file path from meson errors."""
        # Example:
        # ERROR: Nonexistent build file 'data/submodules/meson.build'
        m = re.search(r"Nonexistent build file '([^']*submodules[^']*)'", output, re.IGNORECASE)
        return m.group(1) if m else None

    def _parse_git_command_failure(output: str) -> bool:
        """Return True if meson failed because a git command errored out."""
        # e.g.: ERROR: Command '/usr/bin/git rev-parse --short HEAD' failed with status 128.
        return bool(re.search(r"Command '.*git.*' failed with status \d+", output))

    def _resolve_program(prog: str) -> list[tuple[str, Optional[str]]]:
        """Return (package, fallback) tuples to install for a missing program."""
        if prog in _PROGRAM_PKG_MAP:
            return [_PROGRAM_PKG_MAP[prog]]
        # For unknown programs, install by name (best guess)
        return [(prog, None)]

    def _resolve_dependency(dep_name: str) -> list[tuple[str, Optional[str]]]:
        """Return (package, fallback) tuples to install for a pkg-config dependency."""
        pkg_map = _PKG_MAPS.get(pm, {})
        pkg = pkg_map.get(dep_name)
        if pkg:
            return [(pkg, None)]
        # Fallback heuristic: on apt, lib<name>-dev; on dnf, <name>-devel
        if pm == "apt":
            # e.g. "foo-1.0" → "libfoo-1.0-dev"  or just "libfoo-dev"
            clean = dep_name.replace(".", "")
            return [(f"lib{dep_name}-dev", f"lib{clean}-dev")]
        if pm in ("dnf", "yum"):
            return [(f"{dep_name}-devel", None)]
        if pm == "pacman":
            return [(dep_name, None)]
        return [(dep_name, None)]

    def _resolve_python_module(mod: str) -> list[tuple[str, Optional[str]]]:
        """Return (package, fallback) tuples to install for a missing Python module."""
        pymod_map = _PYMOD_MAPS.get(pm, {})
        pkg = pymod_map.get(mod)
        if pkg:
            return [(pkg, None)]
        # Fallback heuristic
        if pm == "apt":
            return [(f"python3-{mod.lower()}", None)]
        if pm in ("dnf", "yum"):
            return [(f"python3-{mod.lower()}", None)]
        if pm == "pacman":
            return [(f"python-{mod.lower()}", None)]
        return [(f"python3-{mod.lower()}", None)]

    # ── Iterative dependency resolution loop ──────────────────────────────────

    seen_deps: set[str] = set()    # track what we've already tried to install
    attempt = 0

    setup = _run_meson_setup()

    while setup.returncode != 0 and attempt < _MAX_RESOLVE_ATTEMPTS:
        output = f"{setup.stdout}\n{setup.stderr}".strip()
        packages_to_install: list[tuple[str, Optional[str]]] = []
        dep_key: Optional[str] = None   # for cycle detection

        # 0) Check for git command failure (no .git in extracted archive)
        if _parse_git_command_failure(output):
            dep_key = "git:init"
            if dep_key not in seen_deps:
                log.info("Meson failed due to git command — initialising dummy repo (attempt %d/%d)", attempt + 1, _MAX_RESOLVE_ATTEMPTS)
                if progress_callback:
                    progress_callback("Initialising git repo for source build compatibility...")
                if not shutil.which("git"):
                    _batch_install_tools([("git", None)], progress_callback)
                try:
                    subprocess.run(["git", "init"], cwd=str(project_root), timeout=15, check=False, capture_output=True)
                    subprocess.run(["git", "config", "user.email", "themeatlas@local"], cwd=str(project_root), timeout=5, check=False, capture_output=True)
                    subprocess.run(["git", "config", "user.name", "ThemeAtlas"], cwd=str(project_root), timeout=5, check=False, capture_output=True)
                    subprocess.run(["git", "add", "-A"], cwd=str(project_root), timeout=30, check=False, capture_output=True)
                    subprocess.run(
                        ["git", "commit", "-m", "ThemeAtlas source build", "--allow-empty"],
                        cwd=str(project_root), timeout=30, check=False, capture_output=True,
                    )
                except Exception as exc:
                    log.debug("Git init failed: %s", exc)
                seen_deps.add(dep_key)
                attempt += 1
                if build_dir.exists():
                    shutil.rmtree(build_dir)
                setup = _run_meson_setup()
                continue

        missing_submodule_file = _parse_missing_submodule_file(output)
        if missing_submodule_file:
            dep_key = f"submodule:{missing_submodule_file}"
            if dep_key not in seen_deps:
                seen_deps.add(dep_key)
                attempt += 1
                log.info(
                    "Meson reports missing submodule file '%s' (attempt %d/%d)",
                    missing_submodule_file,
                    attempt,
                    _MAX_RESOLVE_ATTEMPTS,
                )
                if progress_callback:
                    progress_callback(
                        "Source archive is missing submodule files; trying to fetch submodule content..."
                    )
                if _hydrate_submodules_from_gitmodules(project_root, progress_callback):
                    if build_dir.exists():
                        shutil.rmtree(build_dir)
                    setup = _run_meson_setup()
                    continue

        # 1) Check for missing program
        prog = _parse_missing_program(output)
        if prog:
            dep_key = f"prog:{prog}"
            if dep_key not in seen_deps:
                packages_to_install = _resolve_program(prog)
                log.info("Meson needs program '%s' (attempt %d/%d)", prog, attempt + 1, _MAX_RESOLVE_ATTEMPTS)

        # 2) Check for missing library dependency
        if not packages_to_install:
            dep = _parse_missing_dependency(output)
            if dep:
                dep_key = f"dep:{dep}"
                if dep_key not in seen_deps:
                    packages_to_install = _resolve_dependency(dep)
                    log.info("Meson needs library '%s' (attempt %d/%d)", dep, attempt + 1, _MAX_RESOLVE_ATTEMPTS)

        # 3) Check for missing Python module
        if not packages_to_install:
            pymod = _parse_missing_python_module(output)
            if pymod:
                dep_key = f"pymod:{pymod}"
                if dep_key not in seen_deps:
                    packages_to_install = _resolve_python_module(pymod)
                    log.info("Meson needs Python module '%s' (attempt %d/%d)", pymod, attempt + 1, _MAX_RESOLVE_ATTEMPTS)

        # Nothing parseable or already tried → stop
        if not packages_to_install or dep_key is None or dep_key in seen_deps:
            break

        seen_deps.add(dep_key)
        attempt += 1

        # Install the resolved packages
        pkg_names = ", ".join(p for p, _ in packages_to_install)
        if progress_callback:
            progress_callback(f"Installing build dependency: {pkg_names}")
        _batch_install_tools(packages_to_install, progress_callback)

        # Wipe build dir and retry
        if build_dir.exists():
            shutil.rmtree(build_dir)
        setup = _run_meson_setup()

    # ── sass / sassc shim fallback ────────────────────────────────────────────
    if setup.returncode != 0:
        output = f"{setup.stdout}\n{setup.stderr}".strip()
        prog = _parse_missing_program(output)
        if prog == "sass" and shutil.which("sassc"):
            log.info("Using sassc shim for sass compatibility")
            if progress_callback:
                progress_callback("Using sassc shim for sass compatibility...")
            shim_dir = project_root / "_ltm_bin"
            shim_dir.mkdir(exist_ok=True)
            shim = shim_dir / "sass"
            shim.write_text("#!/usr/bin/env bash\nexec sassc \"$@\"\n", encoding="utf-8")
            shim.chmod(0o755)
            meson_env["PATH"] = f"{shim_dir}:{meson_env.get('PATH', '')}"
            used_sass_shim = True
            if build_dir.exists():
                shutil.rmtree(build_dir)
            setup = _run_meson_setup()

    # ── Final check: did setup succeed? ───────────────────────────────────────
    if setup.returncode != 0:
        output = f"{setup.stdout}\n{setup.stderr}".strip()
        prog = _parse_missing_program(output)
        dep = _parse_missing_dependency(output)
        pymod = _parse_missing_python_module(output)
        submodule_file = _parse_missing_submodule_file(output)
        if submodule_file:
            msg = (
                "Meson setup failed: source archive is missing required submodule files "
                f"('{submodule_file}'). Use an official release archive or clone with --recurse-submodules."
            )
        elif prog:
            msg = f"Meson setup failed: missing program '{prog}'"
        elif dep:
            msg = f"Meson setup failed: missing library '{dep}'"
        elif pymod:
            msg = f"Meson setup failed: missing Python module '{pymod}'"
        else:
            # Extract last meaningful error line
            detail = ""
            for line in reversed(output.splitlines()):
                stripped = line.strip()
                if stripped and "ERROR" in stripped.upper():
                    detail = stripped
                    break
            if not detail:
                for line in reversed(output.splitlines()):
                    if line.strip():
                        detail = line.strip()
                        break
            if detail and len(detail) > 250:
                detail = detail[:250] + "..."
            msg = f"Meson setup failed with exit code {setup.returncode}"
            if detail:
                msg = f"{msg}: {detail}"
        log.error("%s", msg)
        if progress_callback is not None:
            progress_callback(msg)
        raise RuntimeError(msg)

    # ── meson install ─────────────────────────────────────────────────────────
    log.info("Running: meson install -C %s", build_dir.name)
    install = subprocess.run(
        ["meson", "install", "-C", str(build_dir)],
        cwd=str(project_root),
        timeout=1800,
        check=False,
        capture_output=True,
        text=True,
        env=meson_env,
    )

    # Retry once if install phase itself is missing a tool
    if install.returncode != 0:
        inst_output = f"{install.stdout}\n{install.stderr}".strip()
        prog = _parse_missing_program(inst_output)
        if prog:
            log.info("Install phase needs program '%s'; installing...", prog)
            if progress_callback:
                progress_callback(f"Installing missing tool for install phase: {prog}")
            _batch_install_tools(_resolve_program(prog), progress_callback)
            install = subprocess.run(
                ["meson", "install", "-C", str(build_dir)],
                cwd=str(project_root),
                timeout=1800,
                check=False,
                capture_output=True,
                text=True,
                env=meson_env,
            )

    if install.returncode != 0:
        output = f"{install.stdout}\n{install.stderr}".strip()
        detail = ""
        for line in reversed(output.splitlines()):
            if line.strip():
                detail = line.strip()
                break
        if detail and len(detail) > 220:
            detail = detail[:220] + "..."
        msg = f"Meson install failed with exit code {install.returncode}"
        if detail:
            msg = f"{msg}: {detail}"

        # Improve error message for Dart Sass requirement
        if used_sass_shim and "Could not rebuild" in (detail or ""):
            msg = (
                f"{msg}. The sassc wrapper is not compatible with this project's SCSS code. "
                f"This project requires Dart Sass (the actual 'sass' command)."
            )

        log.error("%s", msg)
        if progress_callback is not None:
            progress_callback(msg)
        raise RuntimeError(msg)

    return _install_built_output(prefix_dir, system_wide, app_name=app_name)


def _build_with_autoconf(project_root: Path, system_wide: bool, progress_callback: Optional[Callable[[str], None]] = None, *, app_name: str = "") -> list[str]:
    """Build and install an autoconf-based theme project into a temporary prefix."""
    # Check for required autotools and batch install if needed
    missing_autotools: list[tuple[str, Optional[str]]] = []
    for tool in ("autoconf", "automake", "libtool"):
        if not shutil.which(tool):
            missing_autotools.append((tool, None))
    
    if missing_autotools:
        if progress_callback:
            tool_names = ", ".join(tool for tool, _ in missing_autotools)
            progress_callback(f"Installing required build tools: {tool_names}")
        log.info("Installing required autotools: %s", ", ".join(t[0] for t in missing_autotools))
        _batch_install_tools(missing_autotools, progress_callback)
        
        # Verify they're now available
        for tool in ("autoconf", "automake", "libtool"):
            if not shutil.which(tool):
                msg = f"Build tool '{tool}' is not installed; cannot build source theme"
                log.error(msg)
                if progress_callback:
                    progress_callback(msg)
                return []

    prefix_dir = project_root / "_ltm_prefix"

    msg = f"Building theme from source using Autoconf in {project_root.name}..."
    log.info(msg)
    if progress_callback:
        progress_callback(msg)

    # Check if configure exists; if not, run autoreconf
    if not (project_root / "configure").exists():
        autoreconf_msg = "Running autoreconf..."
        log.info(autoreconf_msg)
        if progress_callback:
            progress_callback(autoreconf_msg)
        try:
            autoreconf = subprocess.run(
                ["autoreconf", "-if"],
                cwd=str(project_root),
                timeout=600,
                check=False,
                capture_output=True,
                text=True,
            )
            if autoreconf.returncode != 0:
                err = f"autoreconf failed: {autoreconf.stderr}"
                log.error(err)
                if progress_callback:
                    progress_callback(f"Autoreconf failed: {autoreconf.stderr[:200]}")
                return []
        except subprocess.TimeoutExpired:
            msg = "autoreconf timed out"
            log.error(msg)
            if progress_callback:
                progress_callback(msg)
            return []

    configure_msg = f"Running ./configure --prefix={prefix_dir}..."
    log.info(configure_msg)
    if progress_callback:
        progress_callback(configure_msg)
    
    configure_retries = 0
    max_configure_retries = 3
    configure = None
    
    while configure_retries < max_configure_retries:
        try:
            configure = subprocess.run(
                ["./configure", f"--prefix={prefix_dir}"],
                cwd=str(project_root),
                timeout=900,
                check=False,
                capture_output=True,
                text=True,
            )
            if configure.returncode == 0:
                break  # Success
            
            # Check for missing program/package and batch install
            output = f"{configure.stdout}\n{configure.stderr}".strip()
            match = re.search(r"(?:cannot find|not found|checking for .* \.\.\. no)\s+([a-zA-Z0-9_\-]+)", output, re.IGNORECASE)
            if match:
                missing_tool = match.group(1)
                log.info("Configure failed due to missing tool: %s", missing_tool)
                if progress_callback:
                    progress_callback(f"Installing missing dependency: {missing_tool}")
                
                # Use generic mapping for common tools
                tool_map = {
                    "sass": ("dart-sass", "sassc"),
                }
                
                if missing_tool in tool_map:
                    pkg_name, fallback = tool_map[missing_tool]
                    tools_to_install = [(pkg_name, fallback)]
                else:
                    tools_to_install = [(missing_tool, None)]
                
                # Batch install the missing tool
                _batch_install_tools(tools_to_install, progress_callback)
                configure_retries += 1
            else:
                # No missing program detected; break
                break
        except subprocess.TimeoutExpired:
            msg = "configure timed out"
            log.error(msg)
            if progress_callback:
                progress_callback(msg)
            return []
    
    if configure is None or configure.returncode != 0:
        err = f"configure failed: {configure.stderr if configure else 'unknown error'}"
        log.error(err)
        if progress_callback:
            progress_callback(f"Configure failed: {(configure.stderr if configure else 'unknown')[:200]}")
        return []

    make_msg = "Running make..."
    log.info(make_msg)
    if progress_callback:
        progress_callback(make_msg)
    try:
        make = subprocess.run(
            ["make", "-j", str(max(1, os.cpu_count() or 1))],
            cwd=str(project_root),
            timeout=1800,
            check=False,
            capture_output=True,
            text=True,
        )
        if make.returncode != 0:
            err = f"make failed: {make.stderr}"
            log.error(err)
            if progress_callback:
                progress_callback(f"Build failed: {make.stderr[:200]}")
            return []
    except subprocess.TimeoutExpired:
        msg = "make timed out"
        log.error(msg)
        if progress_callback:
            progress_callback(msg)
        return []

    install_msg = "Running make install..."
    log.info(install_msg)
    if progress_callback:
        progress_callback(install_msg)
    try:
        install = subprocess.run(
            ["make", "install"],
            cwd=str(project_root),
            timeout=900,
            check=False,
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            err = f"make install failed: {install.stderr}"
            log.error(err)
            if progress_callback:
                progress_callback(f"Install failed: {install.stderr[:200]}")
            return []
    except subprocess.TimeoutExpired:
        msg = "make install timed out"
        log.error(msg)
        if progress_callback:
            progress_callback(msg)
        return []

    return _install_built_output(prefix_dir, system_wide, app_name=app_name)


def _build_with_cmake(project_root: Path, system_wide: bool, progress_callback: Optional[Callable[[str], None]] = None, *, app_name: str = "") -> list[str]:
    """Build and install a CMake-based theme project into a temporary prefix."""
    if not shutil.which("cmake"):
        if progress_callback:
            progress_callback("Installing CMake...")
        log.info("Installing CMake...")
        result = _batch_install_tools([("cmake", None)], progress_callback)
        if not result.get("cmake", False):
            msg = "CMake is not installed; cannot build source theme project"
            log.error(msg)
            if progress_callback:
                progress_callback(msg)
            return []

    build_dir = project_root / "_ltm_cmake_build"
    prefix_dir = project_root / "_ltm_prefix"

    msg = f"Building theme from source using CMake in {project_root.name}..."
    log.info(msg)
    if progress_callback:
        progress_callback(msg)

    build_dir.mkdir(exist_ok=True)

    cmake_msg = f"Running cmake in {build_dir.name}..."
    log.info(cmake_msg)
    if progress_callback:
        progress_callback(cmake_msg)
    
    cmake_retries = 0
    max_cmake_retries = 3
    cmake = None
    
    while cmake_retries < max_cmake_retries:
        try:
            cmake = subprocess.run(
                ["cmake", str(project_root), f"-DCMAKE_INSTALL_PREFIX={prefix_dir}"],
                cwd=str(build_dir),
                timeout=900,
                check=False,
                capture_output=True,
                text=True,
            )
            if cmake.returncode == 0:
                break  # Success
            
            # Check for missing program/package and batch install
            output = f"{cmake.stdout}\n{cmake.stderr}".strip()
            match = re.search(r"(?:Could not find|not found)\s+([a-zA-Z0-9_\-]+)", output, re.IGNORECASE)
            if match:
                missing_tool = match.group(1)
                log.info("CMake failed due to missing tool: %s", missing_tool)
                if progress_callback:
                    progress_callback(f"Installing missing dependency: {missing_tool}")
                
                # Use generic mapping for common tools
                tool_map = {
                    "sass": ("dart-sass", "sassc"),
                }
                
                if missing_tool in tool_map:
                    pkg_name, fallback = tool_map[missing_tool]
                    tools_to_install = [(pkg_name, fallback)]
                else:
                    tools_to_install = [(missing_tool, None)]
                
                # Batch install the missing tool
                _batch_install_tools(tools_to_install, progress_callback)
                cmake_retries += 1
            else:
                # No missing program detected; break
                break
        except subprocess.TimeoutExpired:
            msg = "CMake timed out"
            log.error(msg)
            if progress_callback:
                progress_callback(msg)
            return []
    
    if cmake is None or cmake.returncode != 0:
        err = f"CMake failed: {cmake.stderr if cmake else 'unknown error'}"
        log.error(err)
        if progress_callback:
            progress_callback(f"CMake failed: {(cmake.stderr if cmake else 'unknown')[:200]}")
        return []

    build_msg = "Running cmake --build..."
    log.info(build_msg)
    if progress_callback:
        progress_callback(build_msg)
    try:
        build = subprocess.run(
            ["cmake", "--build", ".", "--", "-j", str(max(1, os.cpu_count() or 1))],
            cwd=str(build_dir),
            timeout=1800,
            check=False,
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            err = f"Build failed: {build.stderr}"
            log.error(err)
            if progress_callback:
                progress_callback(f"Build failed: {build.stderr[:200]}")
            return []
    except subprocess.TimeoutExpired:
        msg = "Build timed out"
        log.error(msg)
        if progress_callback:
            progress_callback(msg)
        return []

    install_msg = "Running cmake --install..."
    log.info(install_msg)
    if progress_callback:
        progress_callback(install_msg)
    try:
        install = subprocess.run(
            ["cmake", "--install", "."],
            cwd=str(build_dir),
            timeout=900,
            check=False,
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            err = f"Install failed: {install.stderr}"
            log.error(err)
            if progress_callback:
                progress_callback(f"Install failed: {install.stderr[:200]}")
            return []
    except subprocess.TimeoutExpired:
        msg = "Install timed out"
        log.error(msg)
        if progress_callback:
            progress_callback(msg)
        return []

    return _install_built_output(prefix_dir, system_wide, app_name=app_name)


def _try_source_build(
    extract_dir: Path,
    system_wide: bool,
    allow_source_build: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None,
    app_name: str = "",
) -> tuple[list[str], bool, str]:
    """
    Try source-build fallbacks for archives that do not contain packaged themes.
    Returns (installed_themes, was_attempted, status_message).
    """
    project_root = _find_project_root(extract_dir)
    if project_root is None:
        return [], False, "No source code detected"

    build_system = _detect_build_system(project_root)
    if not build_system:
        return [], False, "No supported build system found (meson, autoconf, cmake)"

    if not allow_source_build:
        return [], False, f"Source build required ({build_system}); use --allow-source-build to proceed"

    try:
        if build_system == "meson":
            result = _build_with_meson(project_root, system_wide, progress_callback, app_name=app_name)
        elif build_system == "autoconf":
            result = _build_with_autoconf(project_root, system_wide, progress_callback, app_name=app_name)
        elif build_system == "cmake":
            result = _build_with_cmake(project_root, system_wide, progress_callback, app_name=app_name)
        else:
            return [], False, f"Unsupported build system: {build_system}"
    except RuntimeError as exc:
        return [], True, str(exc)

    if result:
        _install_runtime_python_requirements(project_root, progress_callback)
        return result, True, f"Successfully built and installed using {build_system}"
    return [], True, f"Build attempt completed but no theme files were produced"


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


def _run_install_script(
    theme_root: Path,
    *,
    allow_scripts: bool,
    sandbox_mode: bool,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> None:
    """Execute the first recognised install script found in *theme_root*."""
    for name in _INSTALL_SCRIPTS:
        script = theme_root / name
        if not script.is_file():
            continue

        if not allow_scripts:
            log.warning("Install script detected but execution is disabled by policy: %s", script)
            if progress_callback is not None:
                progress_callback(f"Skipped install script (disabled by policy): {script.name}")
            return

        log.info("Running install script: %s", script)
        if progress_callback is not None:
            progress_callback(f"Running install script: {script.name}")

        # Ensure executable bit is set
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        cmd = ["bash", name]
        if sandbox_mode and shutil.which("bwrap"):
            cmd = [
                "bwrap",
                "--die-with-parent",
                "--unshare-net",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/bin", "/bin",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/lib64", "/lib64",
                "--bind", str(theme_root), str(theme_root),
                "--chdir", str(theme_root),
                "/bin/bash", name,
            ]
        elif sandbox_mode and progress_callback is not None:
            progress_callback("Sandbox requested, but bwrap not found; running script unsandboxed.")

        try:
            result = subprocess.run(
                cmd,
                cwd=str(theme_root),
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                log.warning("Install script exited with code %d", result.returncode)
                if progress_callback is not None:
                    progress_callback(f"Install script exited with code {result.returncode}")
        except subprocess.TimeoutExpired:
            log.error("Install script timed out.")
            if progress_callback is not None:
                progress_callback("Install script timed out")
        return


def _apply_gtk4(theme_root: Path, installed_name: Optional[str] = None) -> None:
    """Copy GTK-4.0 assets from a theme into ~/.config/gtk-4.0.

    Only copies asset directories (images, icons) — NOT raw CSS files.
    Instead of dumping the theme's gtk.css (which often defines light-mode
    defaults that override the user's color-scheme preference and cause
    white-box rendering), we generate a gtk.css that @import's from the
    theme's installed location under ~/.themes or /usr/share/themes.

    Preserves the user's existing settings.ini (dark-theme preference, etc.).
    """
    gtk4_src = theme_root / "gtk-4.0"
    if not gtk4_src.is_dir():
        return

    GTK4_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Back up the user's settings.ini before overwriting anything
    user_settings = GTK4_CONFIG_DIR / "settings.ini"
    saved_settings = None
    if user_settings.exists():
        saved_settings = user_settings.read_text(encoding="utf-8")

    # Only copy asset directories (not CSS files that override libadwaita)
    _SAFE_TO_COPY = {"assets", "icons", "images", "thumbnails"}
    for item in gtk4_src.iterdir():
        # Skip CSS files — they cause white-box issues when they define
        # light-mode colors as defaults and dark-mode uses unsupported
        # CSS functions (oklab, color-mix) that fail silently in GTK4
        if item.is_file() and item.suffix in (".css",):
            log.debug("Skipping CSS file %s (will use @import instead)", item.name)
            continue
        # Skip settings.ini — we preserve the user's own
        if item.is_file() and item.name == "settings.ini":
            continue
        # Copy asset directories and other non-CSS files (e.g. thumbnails)
        dest = GTK4_CONFIG_DIR / item.name
        try:
            if item.is_dir():
                if item.name.lower() in _SAFE_TO_COPY:
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    log.debug("Skipping directory %s (not in safe-copy list)", item.name)
            else:
                # Copy non-CSS files (images, etc.)
                shutil.copy2(item, dest)
        except OSError as exc:
            log.warning("Could not copy %s → %s: %s", item.name, dest, exc)

    # Generate a gtk.css that imports from the theme's installed location.
    # This avoids dumping raw CSS with potentially broken color definitions
    # into the user's config dir.  The theme's gtk.css in its install
    # directory is designed to work as a complete theme, not a user override.
    theme_name = installed_name or theme_root.name
    gtk_css_path = GTK4_CONFIG_DIR / "gtk.css"
    theme_css_candidates = [
        Path.home() / ".themes" / theme_name / "gtk-4.0" / "gtk.css",
        Path.home() / ".local" / "share" / "themes" / theme_name / "gtk-4.0" / "gtk.css",
        Path("/usr/share/themes") / theme_name / "gtk-4.0" / "gtk.css",
    ]
    imported = False
    for candidate in theme_css_candidates:
        if candidate.exists():
            try:
                gtk_css_path.write_text(
                    f"/* Applied by ThemeAtlas for theme: {theme_name} */\n"
                    f"@import url(\"file://{candidate}\");\n",
                    encoding="utf-8",
                )
                imported = True
                log.info("Generated gtk.css importing from %s", candidate)
            except OSError as exc:
                log.warning("Could not write gtk.css: %s", exc)
            break

    if not imported:
        # Theme not found installed yet (will be after install completes),
        # so write an empty gtk.css to avoid leftover overrides
        try:
            gtk_css_path.write_text(
                f"/* Applied by ThemeAtlas — no GTK-4.0 override */\n",
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not write gtk.css: %s", exc)

    # Remove any leftover libadwaita override files from previous installs
    for stale in ("libadwaita.css", "libadwaita-tweaks.css"):
        stale_path = GTK4_CONFIG_DIR / stale
        if stale_path.exists():
            try:
                stale_path.unlink()
                log.info("Removed stale override file: %s", stale_path)
            except OSError as exc:
                log.warning("Could not remove %s: %s", stale_path, exc)

    # Restore the user's settings.ini so dark-theme preference is preserved
    if saved_settings is not None:
        try:
            user_settings.write_text(saved_settings, encoding="utf-8")
            log.debug("Restored user settings.ini in %s", GTK4_CONFIG_DIR)
        except OSError as exc:
            log.warning("Could not restore settings.ini: %s", exc)

    log.info("Applied GTK-4.0 theme assets to %s", GTK4_CONFIG_DIR)


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
        compatible, reason = extension_is_compatible_with_shell(src)
        if not compatible:
            log.error(reason)
            raise ValueError(reason)
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

def _extract_to_temp_dir(archive: Path) -> Path:
    import uuid as uuid_module

    extract_id = str(uuid_module.uuid4())[:8]
    extract_dir = archive.parent / f"_tm_extract_{extract_id}"
    extract_dir.mkdir(exist_ok=True)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive, "r") as zf:
            _safe_zip_extract(zf, extract_dir)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive, "r:*") as tf:
            _safe_tar_extract(tf, extract_dir)
    else:
        raise ValueError(f"Unsupported archive format: {archive.suffix}")
    return extract_dir


def preview_archive_install(archive_path: str, system_wide: bool = False) -> dict[str, object]:
    """Return a dry-run preview describing what install_from_archive would change."""
    archive = Path(archive_path).resolve()
    if not archive.exists():
        raise FileNotFoundError(f"Archive not found: {archive}")

    extract_dir = _extract_to_temp_dir(archive)
    try:
        roots = _find_theme_roots(extract_dir)
        if not roots:
            roots = _find_shell_theme_roots(extract_dir)

        archive_name = _name_from_archive(archive)
        operations: list[dict[str, str]] = []
        script_roots: list[str] = []

        for root in roots:
            kind = _classify_theme(root)
            install_name = archive_name if root == extract_dir or root.name.startswith("_tm_extract_") else root.name
            if kind == "shell":
                dest_base = SYS_SHELL_THEMES_DIR if system_wide else USER_SHELL_THEMES_DIR
            elif kind in ("icons", "cursors"):
                dest_base = SYS_ICONS_DIR if system_wide else USER_ICONS_DIR
            elif kind == "extension":
                uuid = _extension_uuid(root) or root.name
                dest_base = _EXTENSIONS_DIRS[1] if system_wide else _EXTENSIONS_DIRS[0]
                operations.append({
                    "kind": kind,
                    "name": uuid,
                    "source": str(root),
                    "destination": str(dest_base / uuid),
                })
                continue
            else:
                dest_base = SYS_THEMES_DIR if system_wide else USER_THEMES_DIR

            operations.append({
                "kind": kind,
                "name": install_name,
                "source": str(root),
                "destination": str(dest_base / install_name),
            })

            if any((root / script_name).is_file() for script_name in _INSTALL_SCRIPTS):
                script_roots.append(str(root))

        return {
            "archive": str(archive),
            "operations": operations,
            "script_roots": script_roots,
            "system_wide": bool(system_wide),
        }
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def install_from_source_tree(
    source_dir: str,
    system_wide: bool = False,
    *,
    allow_install_scripts: bool = False,
    sandbox_install_scripts: bool = True,
    allow_source_build: bool = True,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> list[str]:
    """Install themes directly from an existing source directory."""
    root_dir = Path(source_dir).resolve()
    if not root_dir.exists() or not root_dir.is_dir():
        raise FileNotFoundError(f"Source directory not found: {root_dir}")

    if progress_callback is not None:
        progress_callback(f"Installing from source tree: {root_dir.name}")

    installed: list[str] = []
    install_failure_reason: Optional[str] = None
    has_shell_theme = False

    roots = _find_theme_roots(root_dir)
    if not roots:
        roots = _find_shell_theme_roots(root_dir)
        if roots:
            log.info("Found shell themes using specialized detector")

    for item_root in roots:
        kind = _classify_theme(item_root)
        if progress_callback is not None:
            progress_callback(f"Installing {kind} assets from {item_root.name}")
        try:
            name = _install_theme_folder(item_root, kind, system_wide)
        except ValueError as exc:
            install_failure_reason = str(exc)
            if progress_callback is not None:
                progress_callback(str(exc))
            log.warning("Skipped install from %s: %s", item_root, exc)
            continue
        if name:
            installed.append(name)
            if kind == "shell":
                has_shell_theme = True
            _run_install_script(
                item_root,
                allow_scripts=allow_install_scripts,
                sandbox_mode=sandbox_install_scripts,
                progress_callback=progress_callback,
            )
            if kind == "gtk":
                _apply_gtk4(item_root, installed_name=name)

    if not installed and install_failure_reason:
        raise ValueError(install_failure_reason)

    if not installed:
        tree_name = Path(root_dir).name.replace("-", " ").replace("_", " ").title()
        installed, was_attempted, status_msg = _try_source_build(
            root_dir, system_wide, allow_source_build, progress_callback,
            app_name=tree_name,
        )
        if was_attempted:
            if progress_callback:
                progress_callback(status_msg)
            log.info("Source build: %s", status_msg)
        elif status_msg and not installed:
            if progress_callback:
                progress_callback(status_msg)
            log.warning("Theme installation: %s", status_msg)

    if not installed:
        log.warning("No recognisable theme directories were found in the source tree.")

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


def extract_archive(
    archive_path: str,
    system_wide: bool = False,
    *,
    allow_install_scripts: bool = False,
    sandbox_install_scripts: bool = True,
    allow_source_build: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> list[str]:
    """Extract archive and install themes into target directories."""
    archive = Path(archive_path).resolve()
    if not archive.exists():
        raise FileNotFoundError(f"Archive not found: {archive}")

    extract_dir = _extract_to_temp_dir(archive)

    try:
        if progress_callback is not None:
            progress_callback(f"Extracting archive: {archive.name}")

        installed: list[str] = []
        install_failure_reason: Optional[str] = None
        has_shell_theme = False

        roots = _find_theme_roots(extract_dir)
        if not roots:
            roots = _find_shell_theme_roots(extract_dir)
            if roots:
                log.info("Found shell themes using specialized detector")

        archive_name = _name_from_archive(archive)

        for root in roots:
            kind = _classify_theme(root)
            install_name = archive_name if root == extract_dir or root.name.startswith("_tm_extract_") else None
            if progress_callback is not None:
                progress_callback(f"Installing {kind} assets from {root.name}")
            try:
                name = _install_theme_folder(root, kind, system_wide, install_name=install_name)
            except ValueError as exc:
                install_failure_reason = str(exc)
                if progress_callback is not None:
                    progress_callback(str(exc))
                log.warning("Skipped install from %s: %s", root, exc)
                continue
            if name:
                installed.append(name)
                if kind == "shell":
                    has_shell_theme = True
                _run_install_script(
                    root,
                    allow_scripts=allow_install_scripts,
                    sandbox_mode=sandbox_install_scripts,
                    progress_callback=progress_callback,
                )
                if kind == "gtk":
                    _apply_gtk4(root, installed_name=name)

        if not installed and install_failure_reason:
            raise ValueError(install_failure_reason)

        if not installed:
            installed, was_attempted, status_msg = _try_source_build(
                extract_dir, system_wide, allow_source_build, progress_callback,
                app_name=archive_name or "",
            )
            if was_attempted:
                if progress_callback:
                    progress_callback(status_msg)
                log.info("Source build: %s", status_msg)
            elif status_msg and not installed:
                # Only show the "source build required" message if this was detected but consent not given
                if progress_callback:
                    progress_callback(status_msg)
                log.warning("Theme installation: %s", status_msg)

        if not installed:
            log.warning("No recognisable theme directories were found in the archive.")

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
