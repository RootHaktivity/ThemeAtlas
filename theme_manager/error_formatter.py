"""
Error message formatter — translates raw build/install logs into actionable user messages.

Maps common build failures and package errors to user-friendly explanations with
specific remediation steps.
"""

import re
from typing import Optional, Tuple


# ── Error pattern matchers ─────────────────────────────────────────────────────

_ERROR_PATTERNS: list[Tuple[str, str, str]] = [
    # (pattern_regex, user_message, remediation)
    
    # Build tool detection failures
    (
        r"meson.*not found|meson.*command not found",
        "Meson build system is not installed.",
        "Install meson: `sudo apt install meson` (Debian/Ubuntu) or `sudo pacman -S meson` (Arch)"
    ),
    (
        r"ninja.*not found|ninja.*command not found",
        "Ninja build tool is not installed.",
        "Install ninja: `sudo apt install ninja-build` (Debian/Ubuntu) or `sudo pacman -S ninja` (Arch)"
    ),
    (
        r"cmake.*not found|cmake.*command not found",
        "CMake build system is not installed.",
        "Install cmake: `sudo apt install cmake` (Debian/Ubuntu) or `sudo pacman -S cmake` (Arch)"
    ),
    (
        r"autoconf|configure.*not found|./configure.*no such",
        "GNU Autotools (autoconf) is not installed.",
        "Install autotools: `sudo apt install autoconf automake libtool` (Debian/Ubuntu)"
    ),
    (
        r"pkg-config.*not found|pkg-config.*command not found",
        "pkg-config is not installed (needed for build dependency detection).",
        "Install pkg-config: `sudo apt install pkg-config` (Debian/Ubuntu) or `sudo pacman -S pkg-config` (Arch)"
    ),
    
    # Compiler failures
    (
        r"gcc.*not found|no c compiler|command.*gcc.*not found",
        "C compiler is not installed.",
        "Install build-essential: `sudo apt install build-essential` (Debian/Ubuntu) or `sudo pacman -S base-devel` (Arch)"
    ),
    (
        r"g\+\+.*not found|c\+\+ compiler|clang.*not found",
        "C++ compiler is not installed.",
        "Install build-essential: `sudo apt install build-essential` (Debian/Ubuntu) or `sudo pacman -S base-devel` (Arch)"
    ),
    
    # Missing dependencies
    (
        r"dependency.*not found|missing.*dependency|pkg-config.*--cflags-only",
        "A required build dependency is missing.",
        "Check the project's README or INSTALL for dependencies, then install them with your package manager."
    ),
    (
        r"libssl|openssl.*not found",
        "OpenSSL development files are not installed.",
        "Install libssl-dev: `sudo apt install libssl-dev` (Debian/Ubuntu)"
    ),
    (
        r"gtk.*but.*not found|glib.*not found|gio.*not found",
        "GTK/GLib development files are missing.",
        "Install GTK development headers: `sudo apt install libgtk-3-dev libglib2.0-dev` (Debian/Ubuntu)"
    ),
    
    # SASS/Dart sass failures (must come before generic "command not found")
    (
        r"sass|dart.*sass.*not found|can't find executable|command.*not found.*sass",
        "SASS (CSS preprocessor) is not installed.",
        "Install sass: `sudo apt install sass` (Debian/Ubuntu) or `sudo pacman -S sass` (Arch), or via npm: `npm install -g sass`"
    ),
    
    # Generic "command not found" for missing tools/libraries (after specific patterns)
    (
        r"(^|\n)[a-z\-0-9]+:.*command not found",
        "A required build dependency is missing.",
        "Check the project's README or INSTALL for dependencies, then install them with your package manager."
    ),
    
    # Configuration failures
    (
        r"meson setup.*failed|meson subprojects.*failed",
        "Meson project configuration failed. Check the theme's meson.build file for issues.",
        "Ensure all build dependencies are installed, or check the project's issue tracker."
    ),
    (
        r"configure.*failed|config.status.*error",
        "Project configuration (./configure) failed. There may be missing dependencies.",
        "Review the configure output above and install any missing -dev packages."
    ),
    
    # Compilation failures
    (
        r"fatal error|error:.*undefined reference|ld returned",
        "Compilation or linking failed. There may be missing headers or libraries.",
        "Check if all -dev packages for the project's dependencies are installed."
    ),
    
    # Installation failures
    (
        r"permission denied|cannot create directory|read-only file system",
        "Permission denied during install. Cannot write to target directory.",
        "Try installing to a user directory instead of system paths, or use pkexec/sudo if needed."
    ),
    
    # Submodule failures
    (
        r"missing required submodule files|submodule.*failed",
        "Git submodules are missing. The archive was not downloaded with submodule content.",
        "ThemeAtlas should attempt a recursive Git clone automatically. If this persists, the repository may need manual setup."
    ),
]


def format_error(raw_message: str, context: Optional[str] = None) -> str:
    """
    Translate a raw build/install error message into user-friendly output.
    
    Parameters
    ----------
    raw_message : str
        Raw error output from build system or installer.
    context : str, optional
        Additional context (e.g. "meson build", "package install").
    
    Returns
    -------
    str
        Formatted, actionable error message.
    """
    if not raw_message:
        return "An unknown error occurred."
    
    message_lower = raw_message.lower()
    
    # Try to find a matching pattern
    for pattern, user_msg, remediation in _ERROR_PATTERNS:
        if re.search(pattern, message_lower):
            return f"{user_msg}\n\nTo fix: {remediation}"
    
    # Fallback: try to extract a meaningful line from the raw message
    lines = raw_message.strip().split("\n")
    last_error_line = ""
    for line in reversed(lines):
        line_lower = line.lower()
        if any(kw in line_lower for kw in ("error", "failed", "fatal", "undefined", "not found")):
            last_error_line = line.strip()
            break
    
    if last_error_line:
        return f"Build error: {last_error_line}\n\nRefer to the full build log for details."
    
    # Ultimate fallback
    return f"Build failed.\n\nRaw output:\n{raw_message[:500]}"


def format_package_install_error(package_name: str, package_manager: str, returncode: int) -> str:
    """Format a package installation error with remediation steps."""
    if returncode == 1 and package_manager == "apt":
        return (
            f"Failed to install package '{package_name}' via apt.\n\n"
            "To fix: Try running `sudo apt update` and `sudo apt install -y {package_name}` manually."
        )
    elif returncode == 1 and package_manager == "pacman":
        return (
            f"Failed to install package '{package_name}' via pacman.\n\n"
            "To fix: Try running `sudo pacman -Syu` then `sudo pacman -S {package_name}` manually."
        )
    else:
        return (
            f"Failed to install package '{package_name}' via {package_manager} (exit code {returncode}).\n\n"
            "Check your system package manager and try installing manually."
        )


def format_extension_error(extension_uuid: str, reason: str) -> str:
    """Format an extension operation error."""
    return (
        f"GNOME Shell extension operation failed: {extension_uuid}\n\n"
        f"Reason: {reason}\n\n"
        "Try enabling/disabling the extension manually via GNOME Settings."
    )
