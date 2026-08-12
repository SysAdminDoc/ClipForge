"""Safe, metadata-only checks for the published ClipForge release."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from . import APP_VERSION


RELEASES_API_URL = (
    "https://api.github.com/repos/SysAdminDoc/ClipForge/releases/latest"
)
RELEASE_API_HOST = "api.github.com"
RELEASE_HOST = "github.com"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ASSETS = 100
MANIFEST_ASSET_NAME = "build-provenance.json"
SIGNATURE_ASSET_NAME = "build-provenance.json.sig"
_VERSION_RE = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)


class UpdateCheckCancelled(Exception):
    """Raised when an update request is cancelled before parsing completes."""


@dataclass(frozen=True)
class UpdateInfo:
    """Validated release metadata; it never contains an installer decision."""

    current_version: str
    latest_version: str | None = None
    available: bool = False
    release_url: str | None = None
    tag_name: str | None = None
    release_name: str | None = None
    published_at: str | None = None
    manifest_status: str = "missing"
    manifest_url: str | None = None
    signature_url: str | None = None
    error: str | None = None


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse the release version format used by the single app version source."""
    match = _VERSION_RE.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"unsupported release version: {value!r}")
    return tuple(int(part) for part in match.groups())


def normalize_version(value: str) -> str:
    return ".".join(str(part) for part in parse_version(value))


def _trusted_github_url(value, *, prefix):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != RELEASE_HOST
        or port is not None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(prefix)
    ):
        return None
    return value


def _asset_digest(asset):
    digest = asset.get("digest") if isinstance(asset, dict) else None
    return isinstance(digest, str) and bool(_SHA256_RE.fullmatch(digest))


def _manifest_policy(assets):
    by_name = {
        asset.get("name"): asset
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    manifest = by_name.get(MANIFEST_ASSET_NAME)
    signature = by_name.get(SIGNATURE_ASSET_NAME)
    manifest_url = _trusted_github_url(
        manifest.get("browser_download_url") if manifest else None,
        prefix="/SysAdminDoc/ClipForge/releases/download/",
    )
    signature_url = _trusted_github_url(
        signature.get("browser_download_url") if signature else None,
        prefix="/SysAdminDoc/ClipForge/releases/download/",
    )
    if not manifest:
        return "missing", None, None
    if not _asset_digest(manifest):
        return "unverified", manifest_url, signature_url
    if signature and _asset_digest(signature):
        return "verified", manifest_url, signature_url
    return "digest-only", manifest_url, signature_url


def parse_release_payload(payload, *, current_version=APP_VERSION) -> UpdateInfo:
    """Validate the GitHub ``releases/latest`` shape and compare versions."""
    current = normalize_version(current_version)
    if not isinstance(payload, dict):
        raise ValueError("release response must be a JSON object")
    if payload.get("draft") is True or payload.get("prerelease") is True:
        raise ValueError("release response is not a stable published release")
    latest = normalize_version(payload.get("tag_name", ""))
    release_url = _trusted_github_url(
        payload.get("html_url"),
        prefix="/SysAdminDoc/ClipForge/releases/tag/",
    )
    if not release_url:
        raise ValueError("release response has no trusted release URL")
    raw_assets = payload.get("assets", [])
    if not isinstance(raw_assets, list):
        raise ValueError("release assets must be a list")
    assets = raw_assets[:MAX_ASSETS]
    manifest_status, manifest_url, signature_url = _manifest_policy(assets)
    return UpdateInfo(
        current_version=current,
        latest_version=latest,
        available=parse_version(latest) > parse_version(current),
        release_url=release_url,
        tag_name=str(payload.get("tag_name"))[:100],
        release_name=(
            str(payload.get("name"))[:200]
            if payload.get("name") is not None
            else None
        ),
        published_at=(
            str(payload.get("published_at"))[:40]
            if payload.get("published_at") is not None
            else None
        ),
        manifest_status=manifest_status,
        manifest_url=manifest_url,
        signature_url=signature_url,
    )


def _check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise UpdateCheckCancelled()


def check_for_update(
    *,
    current_version=APP_VERSION,
    opener=None,
    timeout=5,
    cancel_event=None,
) -> UpdateInfo:
    """Fetch and validate release metadata without downloading any artifact."""
    _check_cancelled(cancel_event)
    timeout = max(1.0, min(30.0, float(timeout)))
    request = Request(
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"ClipForge/{normalize_version(current_version)}",
        },
    )
    opener = opener or urlopen
    with opener(request, timeout=timeout) as response:
        final_url = response.geturl()
        parsed = urlsplit(final_url)
        try:
            api_port = parsed.port
        except ValueError as exc:
            raise ValueError("release API redirected to an untrusted URL") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname != RELEASE_API_HOST
            or api_port is not None
            or parsed.query
            or parsed.fragment
            or parsed.path != "/repos/SysAdminDoc/ClipForge/releases/latest"
        ):
            raise ValueError("release API redirected to an untrusted URL")
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                content_length = int(content_length)
            except ValueError as exc:
                raise ValueError("release response has an invalid size header") from exc
            if content_length > MAX_RESPONSE_BYTES:
                raise ValueError("release response exceeds the size limit")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    _check_cancelled(cancel_event)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("release response exceeds the size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("release response is not valid UTF-8 JSON") from exc
    return parse_release_payload(payload, current_version=current_version)
