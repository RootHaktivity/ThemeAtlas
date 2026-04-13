from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

_CONFIG_DIR = Path.home() / ".config" / "themeatlas"
_STATE_FILE = _CONFIG_DIR / "ui_state.json"

_DEFAULT_STATE: dict[str, object] = {
    "favorites": [],
    "recent": [],
    "collections": {
        "minimal": [],
        "gaming": [],
        "light": [],
    },
    "onboarding_complete": False,
    "install_policy": {
        "allow_install_scripts": False,
        "sandbox_install_scripts": True,
    },
    "install_history": [],
    "recent_actions": [],
    "active_install": {},
}


def _unique_strings(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _normalize_action_list(values: object, max_items: int = 80) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    out: list[dict[str, str]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        action = str(value.get("action") or "").strip()
        detail = str(value.get("detail") or "").strip()
        timestamp = str(value.get("timestamp") or "").strip()
        if not action:
            continue
        out.append({"action": action, "detail": detail, "timestamp": timestamp})
    return out[:max_items]


def normalize_ui_state(state: object) -> dict[str, object]:
    normalized = deepcopy(_DEFAULT_STATE)
    if not isinstance(state, dict):
        return normalized

    normalized["favorites"] = _unique_strings(state.get("favorites"))
    normalized["recent"] = _unique_strings(state.get("recent"))[:20]

    collections: dict[str, list[str]] = {
        name: list(values)
        for name, values in deepcopy(_DEFAULT_STATE["collections"]).items()  # type: ignore[union-attr]
    }
    raw_collections = state.get("collections")
    if isinstance(raw_collections, dict):
        for name, values in raw_collections.items():
            if not isinstance(name, str):
                continue
            clean_name = name.strip().lower()
            if not clean_name:
                continue
            collections[clean_name] = _unique_strings(values)
    normalized["collections"] = collections
    normalized["onboarding_complete"] = bool(state.get("onboarding_complete", False))

    policy = state.get("install_policy") if isinstance(state, dict) else {}
    if not isinstance(policy, dict):
        policy = {}
    normalized["install_policy"] = {
        "allow_install_scripts": bool(policy.get("allow_install_scripts", False)),
        "sandbox_install_scripts": bool(policy.get("sandbox_install_scripts", True)),
    }

    normalized["install_history"] = _normalize_action_list(state.get("install_history"), max_items=120)
    normalized["recent_actions"] = _normalize_action_list(state.get("recent_actions"), max_items=120)

    active = state.get("active_install")
    if isinstance(active, dict):
        normalized["active_install"] = {
            "name": str(active.get("name") or "").strip(),
            "phase": str(active.get("phase") or "").strip(),
            "started_at": str(active.get("started_at") or "").strip(),
        }
    else:
        normalized["active_install"] = {}
    return normalized


def load_ui_state() -> dict[str, object]:
    if not _STATE_FILE.exists():
        return deepcopy(_DEFAULT_STATE)
    try:
        return normalize_ui_state(json.loads(_STATE_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return deepcopy(_DEFAULT_STATE)


def save_ui_state(state: dict[str, object]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    normalized = normalize_ui_state(state)
    _STATE_FILE.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")