"""
Command-line interface for Linux Theme Manager.

Commands
--------
  install  – Install a theme from an archive, .deb package, or PPA.
  list     – List all installed themes.
  switch   – Switch the active GTK / icon / cursor / shell theme.
  remove   – Remove an installed theme.
  status   – Show current environment info and active themes.
"""

import argparse
import sys

from .dependencies import ensure_gui_dependencies
from .environment import detect_environment
from .extensions import install_user_themes_extension
from .flatpak import apply_flatpak_theme_overrides, is_flatpak_available
from .installer import install_from_archive, install_from_deb, install_from_ppa
from .logger import get_logger
from .manager import list_themes, remove_theme
from .switcher import (
    get_current_themes,
    switch_cursor_theme,
    switch_gtk_theme,
    switch_icon_theme,
    switch_shell_theme,
)

log = get_logger(__name__)


# ── Sub-command handlers ───────────────────────────────────────────────────────

def _cmd_install(args: argparse.Namespace) -> int:
    if args.archive:
        names = install_from_archive(args.archive, system_wide=args.system)
        if names:
            log.info("Installed theme(s): %s", ", ".join(names))
            return 0
        log.error("No themes were installed from the archive.")
        return 1

    if args.deb:
        return 0 if install_from_deb(args.deb) else 1

    # PPA
    if not args.packages:
        log.error("--packages is required when using --ppa.")
        return 2
    return 0 if install_from_ppa(args.ppa, args.packages) else 1


def _cmd_list(_args: argparse.Namespace) -> int:
    themes = list_themes(include_system=True)
    any_found = False
    for category, names in themes.items():
        if names:
            any_found = True
            print(f"\n[{category}]")
            for name in names:
                print(f"  {name}")
        else:
            print(f"\n[{category}]\n  (none)")
    if not any_found:
        log.info("No themes installed yet.")
    return 0


def _cmd_switch(args: argparse.Namespace) -> int:
    env = detect_environment()

    if not env.has_gsettings:
        log.error(
            "gsettings not found. "
            "Theme switching requires a GNOME-based session with gsettings available."
        )
        return 1

    switched: list[str] = []

    if args.gtk:
        if switch_gtk_theme(args.gtk):
            switched.append(f"GTK → {args.gtk}")

    if args.icons:
        if switch_icon_theme(args.icons):
            switched.append(f"Icons → {args.icons}")

    if args.cursor:
        if switch_cursor_theme(args.cursor):
            switched.append(f"Cursor → {args.cursor}")

    if args.shell:
        if env.desktop == "gnome":
            install_user_themes_extension()
        if switch_shell_theme(args.shell, env.desktop):
            switched.append(f"Shell → {args.shell}")

    if args.flatpak:
        if is_flatpak_available():
            current = get_current_themes()
            gtk_t   = args.gtk   or current.get("gtk")   or ""
            icon_t  = args.icons or current.get("icons") or ""
            apply_flatpak_theme_overrides(gtk_t, icon_t)
        else:
            log.warning("Flatpak is not installed; skipping Flatpak integration.")

    if not switched:
        log.warning(
            "No theme type specified. "
            "Use one or more of: --gtk, --icons, --cursor, --shell"
        )
        _print_current_themes()
        return 1

    log.info("Switched: %s", " | ".join(switched))
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    success = remove_theme(args.name, kind=args.type, system_wide=args.system)
    return 0 if success else 1


def _cmd_status(_args: argparse.Namespace) -> int:
    env = detect_environment()
    print(f"Desktop          : {env.desktop}")
    print(f"Distro           : {env.distro}")
    print(f"Session          : {'Wayland' if env.is_wayland else 'X11'}")
    print(f"Package manager  : {env.package_manager}")
    print(f"gsettings        : {'available' if env.has_gsettings else 'not found'}")
    print(f"Flatpak          : {'available' if env.has_flatpak else 'not found'}")
    print()
    _print_current_themes()
    return 0


def _cmd_gui(_args: argparse.Namespace) -> int:
    if not ensure_gui_dependencies(auto_install=True, require_pillow=True):
        log.error("GUI dependencies are not available. Aborting GUI launch.")
        return 1

    try:
        from .gui_qt import launch_gui
        launch_gui()
        return 0
    except ImportError as exc:
        log.error("Could not launch GUI – PySide6 or runtime dependency may be missing: %s", exc)
        return 1


def _print_current_themes() -> None:
    themes = get_current_themes()
    print("Active themes:")
    for kind, value in themes.items():
        print(f"  {kind:<8}: {value or '(not set)'}")


# ── Argument parser ────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="theme-manager",
        description="Cross-distro Linux theme installer and manager.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  theme-manager install --archive ~/downloads/Orchis.tar.xz\n"
            "  theme-manager install --ppa nikos.p/orchis-theme --packages orchis-theme\n"
            "  theme-manager install --deb ~/downloads/papirus-icon-theme.deb\n"
            "  theme-manager list\n"
            "  theme-manager switch --gtk Orchis-Dark --icons Papirus --flatpak\n"
            "  theme-manager remove Orchis-Dark --type gtk\n"
            "  theme-manager status\n"
        ),
    )
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # ── install ────────────────────────────────────────────────────────────────
    p_install = sub.add_parser(
        "install",
        help="Install a theme from an archive, .deb package, or PPA.",
    )
    src = p_install.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--archive", metavar="FILE",
        help="Path to a .zip, .tar.gz, .tgz, .tar.bz2, or .tar.xz archive.",
    )
    src.add_argument(
        "--deb", metavar="FILE",
        help="Path to a .deb package file.",
    )
    src.add_argument(
        "--ppa", metavar="PPA",
        help="Launchpad PPA identifier (e.g. ppa:owner/repo or owner/repo).",
    )
    p_install.add_argument(
        "--packages", nargs="+", metavar="PKG",
        help="One or more package names to install from the PPA (required with --ppa).",
    )
    p_install.add_argument(
        "--system", action="store_true",
        help="Install into system directories instead of user home (requires sudo).",
    )

    # ── list ───────────────────────────────────────────────────────────────────
    sub.add_parser("list", help="List all installed themes.")

    # ── switch ─────────────────────────────────────────────────────────────────
    p_switch = sub.add_parser(
        "switch",
        help="Switch the active theme(s).",
    )
    p_switch.add_argument("--gtk",    metavar="NAME", help="GTK theme name.")
    p_switch.add_argument("--icons",  metavar="NAME", help="Icon theme name.")
    p_switch.add_argument("--cursor", metavar="NAME", help="Cursor theme name.")
    p_switch.add_argument("--shell",  metavar="NAME", help="GNOME shell theme name.")
    p_switch.add_argument(
        "--flatpak", action="store_true",
        help="Also apply theme overrides to Flatpak applications.",
    )

    # ── remove ─────────────────────────────────────────────────────────────────
    p_remove = sub.add_parser("remove", help="Remove an installed theme.")
    p_remove.add_argument("name", help="Name of the theme to remove.")
    p_remove.add_argument(
        "--type",
        choices=["gtk", "icons", "cursors", "shell"],
        default="gtk",
        help="Theme type (default: gtk).",
    )
    p_remove.add_argument(
        "--system", action="store_true",
        help="Remove from system directories (requires sudo).",
    )

    # ── status ─────────────────────────────────────────────────────────────────
    sub.add_parser("status", help="Show current environment and active themes.")

    # ── gui ────────────────────────────────────────────────────────────────────
    sub.add_parser("gui", help="Launch the graphical user interface.")

    return parser


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "install": _cmd_install,
        "list":    _cmd_list,
        "switch":  _cmd_switch,
        "remove":  _cmd_remove,
        "status":  _cmd_status,
        "gui":     _cmd_gui,
    }

    try:
        exit_code = handlers[args.command](args)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        sys.exit(1)
    except ValueError as exc:
        log.error("%s", exc)
        sys.exit(2)
    except KeyboardInterrupt:
        print()
        sys.exit(130)

    sys.exit(exit_code)
