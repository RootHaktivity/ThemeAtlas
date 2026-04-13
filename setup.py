from setuptools import find_packages, setup
from setuptools.command.develop import develop
from setuptools.command.install import install
import sys


if len(sys.argv) == 1:
    # Convenience mode: running `python3 setup.py` performs an install.
    sys.argv.append("install")


def _post_install_bootstrap() -> None:
    """Best-effort runtime dependency bootstrap for GUI support."""
    try:
        from theme_manager.dependencies import ensure_gui_dependencies
    except Exception as exc:  # noqa: BLE001
        print(f"[themeatlas] Skipping GUI dependency bootstrap: {exc}")
        return

    ok = ensure_gui_dependencies(auto_install=True)
    if ok:
        print("[themeatlas] GUI dependencies are ready.")
    else:
        print(
            "[themeatlas] GUI dependencies could not be fully installed automatically.\n"
            "Run 'python3 main.py gui' and follow the prompted install step, or install PySide6 manually."
        )


class InstallCommand(install):
    """Custom install command that bootstraps GUI dependencies."""

    def run(self):
        super().run()
        _post_install_bootstrap()


class DevelopCommand(develop):
    """Custom develop command that bootstraps GUI dependencies."""

    def run(self):
        super().run()
        _post_install_bootstrap()

setup(
    name="themeatlas",
    version="1.0.0",
    description="Cross-distro Linux theme installer and manager",
    long_description=(
        "A CLI tool that automates the installation, management, and switching "
        "of complete desktop themes (GTK, icons, cursors, GNOME shell) on "
        "Ubuntu and other major Linux distributions."
    ),
    python_requires=">=3.10",
    packages=find_packages(),
    install_requires=[
        # default GUI dependencies
        "PySide6>=6.6",
        "Pillow>=9.0",
    ],
    entry_points={
        "console_scripts": [
            "themeatlas=theme_manager.cli:main",
            "theme-manager=theme_manager.cli:main",
        ],
    },
    cmdclass={
        "install": InstallCommand,
        "develop": DevelopCommand,
    },
    extras_require={
        # pip install themeatlas[thumbnails]
        # kept as compatibility alias; Pillow is already in install_requires
        "thumbnails": [],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: POSIX :: Linux",
        "Environment :: Console",
        "Topic :: Desktop Environment",
        "Topic :: System :: Systems Administration",
        "License :: OSI Approved :: MIT License",
    ],
)
