# ThemeAtlas

ThemeAtlas is a cross-distro desktop theming tool with a modern Qt GUI and CLI support.
It helps users discover, install, apply, and manage themes from multiple sources in one place.

## Features

- Unified search across multiple sources (GNOME Look, GitHub, distro package sources)
- Install support for archives and distro package records
- Variant selection for themes with multiple downloadable files
- Preview pipeline with real image fallbacks before generated preview
- Installed manager with grouped tabs and per-item actions
- Extension visibility and enabled/disabled detection
- Compatibility filtering based on the current environment
- Rollback-safe apply checkpoints when apply fails
- CI + automated tests + release workflow

## Sources and Compatibility

The app uses adapters for different theme sources.
Records are normalized into one install model, then filtered for compatibility with the detected distro/package manager.

## Requirements

- Python 3.10+
- Linux desktop environment (best experience on GNOME)
- Runtime tools as needed:
  - gsettings
  - gnome-extensions
  - pkexec
  - apt or pacman (for package installs)

Python dependencies are listed in requirements.txt.

## Installation

### Option 1: Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py gui
```

### Option 2: Package install

```bash
pip install .
```

## Usage

### GUI

```bash
python3 main.py gui
```

For a dock/app-menu launch (recommended), install the desktop launcher:

```bash
bash scripts/install_desktop_entry.sh
```

The script creates a user-local launcher at `~/.local/bin/themeatlas-launcher` and
updates the desktop entry to use it. This avoids `pip --user` issues on distros that
enforce PEP 668 (externally-managed Python environments).

### CLI

```bash
themeatlas --help
```

If you want the `themeatlas` command system-wide for your user, prefer one of:

```bash
pipx install .
```

or

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Development

Run tests:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

Compile check:

```bash
python3 -m compileall -q theme_manager
```

## Desktop Launcher and Icons

ThemeAtlas includes branded icon assets and a desktop entry template.

- SVG masters: `assets/icons/themeatlas-icon.svg`, `assets/icons/themeatlas-mark.svg`
- PNG exports: `assets/icons/png/`
- Desktop entry: `assets/themeatlas.desktop`

Install launcher and icons for your user:

```bash
bash scripts/install_desktop_entry.sh
```

After running the script, ThemeAtlas should appear in your app menu.

## Packaging

ThemeAtlas now includes starter packaging scaffolding for desktop distribution:

- Flatpak manifest: `packaging/flatpak/io.themeatlas.ThemeAtlas.yaml`
- AppImage staging helper: `scripts/build_appimage.sh`

Flatpak build example:

```bash
flatpak-builder build-dir packaging/flatpak/io.themeatlas.ThemeAtlas.yaml --force-clean
```

AppImage staging example:

```bash
bash scripts/build_appimage.sh
```

The AppImage helper prepares an AppDir and prints the `appimagetool` command to finish the bundle if that tool is installed.

## Release

A GitHub Actions workflow publishes releases on version tags matching `v*`.

Example:

```bash
git tag v1.0.1
git push origin v1.0.1
```

## License

Choose and add your preferred license file before public release.
