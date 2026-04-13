# Contributing to ThemeAtlas

Thanks for your interest in contributing.

## Before You Start

- Open an issue for bug reports or feature proposals
- For large changes, discuss the approach first
- Keep pull requests focused and reviewable

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run tests:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

Run compile check:

```bash
python3 -m compileall -q theme_manager
```

## Pull Request Guidelines

- Use descriptive commit messages
- Add or update tests for behavior changes
- Keep docs in sync with code changes
- Ensure CI passes before requesting review

## Coding Guidelines

- Follow existing project style and structure
- Prefer clear function boundaries and explicit error handling
- Avoid unrelated refactors in feature or fix PRs

## Sign-off

By contributing, you agree your contributions are licensed under the project's MIT License.
