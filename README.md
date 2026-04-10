# Linux Theme Manager

Linux Theme Manager is a cross-distro desktop theming tool with a modern Qt GUI and CLI support.
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

### CLI

```bash
theme-manager --help
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

## Release

A GitHub Actions workflow publishes releases on version tags matching `v*`.

Example:

```bash
git tag v1.0.1
git push origin v1.0.1
```

## License

Choose and add your preferred license file before public release.
