"""Hardened network and download helpers for ThemeAtlas."""

from __future__ import annotations

import hashlib
import json
import os
import random
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = {"https", "http"}
_DEFAULT_TIMEOUT = 20
_DEFAULT_RETRIES = 2
_DEFAULT_RETRY_BACKOFF = 0.65
_DEFAULT_MAX_BYTES = 32 * 1024 * 1024

_CACHE_DIR = Path.home() / ".cache" / "themeatlas" / "http"
_CACHE_META_SUFFIX = ".meta.json"
_CLEANUP_PROBABILITY = 0.08


class FetchError(RuntimeError):
    """Raised when a network fetch fails after retries."""


def validate_network_url(url: str) -> None:
    parsed = urlsplit((url or "").strip())
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError(f"Unsupported URL scheme: {url}")


def _cache_base_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8", errors="ignore")).hexdigest()
    return _CACHE_DIR / digest


def _cache_read(base: Path, ttl_seconds: int) -> bytes | None:
    data_path = base
    meta_path = base.with_name(base.name + _CACHE_META_SUFFIX)
    if not data_path.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        fetched_at = int(meta.get("fetched_at", 0))
        if fetched_at <= 0:
            return None
        if int(time.time()) - fetched_at > ttl_seconds:
            return None
        return data_path.read_bytes()
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def _cache_write(base: Path, data: bytes, url: str) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = base.with_name(base.name + _CACHE_META_SUFFIX)
    base.write_bytes(data)
    meta_path.write_text(
        json.dumps({
            "url": url,
            "size": len(data),
            "fetched_at": int(time.time()),
        }, sort_keys=True),
        encoding="utf-8",
    )


def _cleanup_cache(max_age_seconds: int = 14 * 24 * 3600) -> None:
    if not _CACHE_DIR.exists():
        return
    cutoff = int(time.time()) - max_age_seconds
    try:
        for path in _CACHE_DIR.iterdir():
            if path.is_dir():
                continue
            try:
                if int(path.stat().st_mtime) < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        return


def _read_limited(resp, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Downloaded payload exceeds size limit ({max_bytes} bytes)")
        chunks.append(chunk)
    return b"".join(chunks)


def _get_smart_cache_ttl(url: str) -> int:
    """
    Determine intelligent cache TTL based on URL patterns.
    
    - API responses and metadata: 24 hours
    - Theme previews/thumbnails: 7 days (stable content)
    - Static release metadata: 24 hours
    - Search results: 12 hours (may change)
    - Theme archives: no cache (always fetch fresh)
    """
    url_lower = (url or "").lower()
    
    # Never cache archive downloads
    if any(ext in url_lower for ext in [".zip", ".tar.gz", ".tar.xz", ".tar.bz2", ".tgz"]):
        return 0
    
    # Cache previews/thumbnails for a week
    if any(term in url_lower for term in ["preview", "thumbnail", "screenshot", "image", "/img/", ".png", ".jpg", ".jpeg"]):
        return 7 * 24 * 3600
    
    # Cache API/metadata responses for 24 hours
    if any(term in url_lower for term in ["api", "metadata", "manifest", "json"]):
        return 24 * 3600
    
    # Cache search results for 12 hours (more volatile)
    if any(term in url_lower for term in ["search", "query"]):
        return 12 * 3600
    
    # Default: 24 hours for other content
    return 24 * 3600


def fetch_bytes(
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    retries: int = _DEFAULT_RETRIES,
    retry_backoff: float = _DEFAULT_RETRY_BACKOFF,
    cache_ttl_seconds: int = 0,
    referer: str = "",
) -> bytes:
    """Fetch bytes from URL with URL validation, retries, and optional disk cache."""
    validate_network_url(url)
    if cache_ttl_seconds < 0:
        cache_ttl_seconds = 0

    cache_key = f"{url}|{max_bytes}|{referer}"
    cache_base = _cache_base_path(cache_key)
    if cache_ttl_seconds > 0:
        cached = _cache_read(cache_base, cache_ttl_seconds)
        if cached is not None:
            return cached

    req_headers = {
        "User-Agent": "themeatlas/1.0",
        **(extra_headers or {}),
    }
    if referer:
        req_headers["Referer"] = referer

    last_exc: Exception | None = None
    attempts = max(1, retries + 1)
    ctx = ssl.create_default_context()

    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                data = _read_limited(resp, max_bytes)
            if cache_ttl_seconds > 0:
                try:
                    _cache_write(cache_base, data, url)
                except OSError:
                    pass
            if random.random() < _CLEANUP_PROBABILITY:
                _cleanup_cache()
            return data
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                break
            sleep_s = retry_backoff * (2 ** attempt)
            time.sleep(sleep_s)

    raise FetchError(f"Failed to fetch URL after {attempts} attempts: {url} ({last_exc})")


def fetch_json(
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
    max_bytes: int = 8 * 1024 * 1024,
    retries: int = _DEFAULT_RETRIES,
    cache_ttl_seconds: int = 0,
) -> dict:
    """Fetch and parse JSON payload from URL."""
    data = fetch_bytes(
        url,
        extra_headers=extra_headers,
        timeout=timeout,
        max_bytes=max_bytes,
        retries=retries,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    try:
        raw = json.loads(data.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise FetchError(f"Invalid JSON from {url}: {exc}") from exc
    if not isinstance(raw, dict):
        raise FetchError(f"Expected JSON object from {url}")
    return raw


def download_to_file(
    url: str,
    file_path: str,
    *,
    timeout: int = 60,
    max_bytes: int = 256 * 1024 * 1024,
    retries: int = _DEFAULT_RETRIES,
    progress_callback=None,
) -> int:
    """Download URL to file path with retries and a hard size limit."""
    validate_network_url(url)
    req_headers = {"User-Agent": "themeatlas/1.0"}
    attempts = max(1, retries + 1)
    last_exc: Exception | None = None
    ctx = ssl.create_default_context()

    for attempt in range(attempts):
        total = 0
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                with open(file_path, "wb") as handle:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(f"Download exceeds max allowed size ({max_bytes} bytes)")
                        handle.write(chunk)
                        if progress_callback is not None:
                            progress_callback(total)
            return total
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_exc = exc
            try:
                if os.path.exists(file_path):
                    os.unlink(file_path)
            except OSError:
                pass
            if attempt >= attempts - 1:
                break
            time.sleep(_DEFAULT_RETRY_BACKOFF * (2 ** attempt))

    raise FetchError(f"Failed to download URL after {attempts} attempts: {url} ({last_exc})")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def parse_sha256_sidecar(text: str) -> str:
    """Extract SHA256 value from common sidecar formats."""
    for token in text.replace("\n", " ").split(" "):
        clean = token.strip().lower()
        if len(clean) == 64 and all(c in "0123456789abcdef" for c in clean):
            return clean
    raise ValueError("No SHA256 digest found in sidecar")


def try_fetch_sha256_sidecar(url: str) -> str:
    """Best-effort SHA256 sidecar fetch using .sha256/.sha256sum suffixes."""
    candidates = [f"{url}.sha256", f"{url}.sha256sum"]
    for sidecar_url in candidates:
        try:
            payload = fetch_bytes(sidecar_url, timeout=12, max_bytes=64 * 1024, retries=1, cache_ttl_seconds=600)
            digest = parse_sha256_sidecar(payload.decode("utf-8", errors="replace"))
            return digest
        except Exception:
            continue
    return ""


def verify_sha256(path: str, expected_sha256: str) -> tuple[bool, str, str]:
    expected = (expected_sha256 or "").strip().lower()
    if len(expected) != 64 or not all(c in "0123456789abcdef" for c in expected):
        raise ValueError("Invalid expected SHA256 format")
    actual = sha256_file(path)
    return actual == expected, actual, expected
