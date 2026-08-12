import json

import pytest

from clipforge.update import (
    MANIFEST_ASSET_NAME,
    RELEASES_API_URL,
    SIGNATURE_ASSET_NAME,
    check_for_update,
    parse_release_payload,
)


def _asset(name, *, digest=True):
    return {
        "name": name,
        "digest": "sha256:" + "a" * 64 if digest else None,
        "browser_download_url": (
            "https://github.com/SysAdminDoc/ClipForge/releases/download/"
            f"v0.6.0/{name}"
        ),
    }


def _payload(tag="v0.6.0", assets=None):
    return {
        "tag_name": tag,
        "name": "ClipForge release",
        "html_url": f"https://github.com/SysAdminDoc/ClipForge/releases/tag/{tag}",
        "published_at": "2026-08-12T12:00:00Z",
        "draft": False,
        "prerelease": False,
        "assets": assets if assets is not None else [
            _asset(MANIFEST_ASSET_NAME),
            _asset(SIGNATURE_ASSET_NAME),
        ],
    }


class _Response:
    def __init__(self, body, *, headers=None, url=RELEASES_API_URL):
        self._body = body
        self.headers = headers or {}
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self._url

    def read(self, _limit):
        return self._body


def test_parse_release_payload_reports_new_version_and_manifest_policy():
    result = parse_release_payload(_payload(), current_version="0.5.2")

    assert result.available is True
    assert result.latest_version == "0.6.0"
    assert result.manifest_status == "verified"
    assert result.signature_url.endswith(SIGNATURE_ASSET_NAME)


@pytest.mark.parametrize("tag", ["v0.5.2", "v0.4.9"])
def test_parse_release_payload_does_not_report_equal_or_older_version(tag):
    result = parse_release_payload(_payload(tag), current_version="0.5.2")

    assert result.available is False


def test_check_for_update_validates_api_redirect_and_request_headers():
    calls = []
    body = json.dumps(_payload()).encode("utf-8")

    def opener(request, *, timeout):
        calls.append((request.full_url, request.headers, timeout))
        return _Response(body, headers={"Content-Length": str(len(body))})

    result = check_for_update(current_version="0.5.2", opener=opener)

    assert result.available is True
    assert calls[0][0] == RELEASES_API_URL
    assert calls[0][1]["Accept"] == "application/vnd.github+json"
    assert calls[0][1]["User-agent"] == "ClipForge/0.5.2"
    assert calls[0][2] == 5


def test_check_for_update_rejects_untrusted_redirect_and_oversized_response():
    body = json.dumps(_payload()).encode("utf-8")

    def redirected(_request, *, timeout):
        return _Response(body, url="https://example.com/releases/latest")

    with pytest.raises(ValueError, match="untrusted URL"):
        check_for_update(opener=redirected)

    def oversized(_request, *, timeout):
        return _Response(body, headers={"Content-Length": str(2 * 1024 * 1024)})

    with pytest.raises(ValueError, match="size limit"):
        check_for_update(opener=oversized)
