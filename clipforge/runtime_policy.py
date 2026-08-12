"""Security policy for external runtimes used by ClipForge.

This module is deliberately data-driven and dependency-free so the same policy
can be used by runtime detection, diagnostics, and release verification.
Update ``POLICY_REVIEWED_AT`` and the release-branch floors together when the
upstream security pages publish a new supported baseline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


POLICY_SCHEMA = "clipforge.runtime-policy"
POLICY_VERSION = 1
POLICY_REVIEWED_AT = "2026-08-11"

# Latest patched release currently listed by the official FFmpeg security page
# for each maintained release branch. Older branches are not accepted because
# the application processes user-selected media through the external binary.
FFMPEG_SECURITY_BRANCH_FLOORS = {
    (6, 0): (6, 0, 1),
    (6, 1): (6, 1, 3),
    (7, 0): (7, 0, 3),
    (7, 1): (7, 1, 2),
    (8, 0): (8, 0, 3),
    (8, 1): (8, 1, 2),
}

# NVDEC remains an optional acceleration path. Until its upstream advisory
# mapping is maintained here, keep the old fail-closed boundary and allow a
# git build only when it advertises the exact reviewed fix commit.
NVDEC_RELEASE_BOUNDARY = (8, 1, 2)
NVDEC_FIX_COMMIT = "4c6217477fc64305055b37d9d1d0d76d30e37f97"

# Qt's current SVG security fix is available in the 6.11 line at 6.11.1.
# The dependency floor intentionally selects this line rather than relying on
# the alternate fixed branch, so the lock and runtime policy have one baseline.
QT_SECURITY_MINIMUM = (6, 11, 1)

_FFMPEG_VERSION_PATTERN = re.compile(
    r"\bffmpeg version (?:n)?(\d+)\.(\d+)(?:\.(\d+))?",
    re.IGNORECASE,
)
_VERSION_PATTERN = re.compile(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?")


@dataclass(frozen=True)
class PolicyResult:
    """A serializable decision for one runtime policy check."""

    component: str
    status: str
    version: tuple[int, int, int] | None
    reason: str

    @property
    def accepted(self):
        return self.status == "supported"

    def as_dict(self):
        return {
            "component": self.component,
            "status": self.status,
            "version": format_version(self.version) if self.version else None,
            "reason": self.reason,
        }


def format_version(version):
    """Return a stable three-part version string for a parsed tuple."""
    if version is None:
        return "unknown"
    return ".".join(str(part) for part in version)


def parse_ffmpeg_version(version_output):
    """Parse an FFmpeg release banner without accepting arbitrary git text."""
    match = _FFMPEG_VERSION_PATTERN.search(str(version_output or ""))
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def parse_runtime_version(version_text):
    """Parse a conventional ``major.minor.patch`` runtime version."""
    match = _VERSION_PATTERN.search(str(version_text or ""))
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def evaluate_ffmpeg_runtime(version_output):
    """Classify an external FFmpeg banner against the maintained floors."""
    version = parse_ffmpeg_version(version_output)
    if version is None:
        return PolicyResult(
            "ffmpeg",
            "unknown",
            None,
            "FFmpeg release banner is missing or is an unreviewed git build.",
        )

    branch = version[:2]
    floor = FFMPEG_SECURITY_BRANCH_FLOORS.get(branch)
    if floor is None:
        if version > max(FFMPEG_SECURITY_BRANCH_FLOORS.values()):
            return PolicyResult(
                "ffmpeg",
                "supported",
                version,
                "FFmpeg is newer than the reviewed release branches; re-review before the next release.",
            )
        return PolicyResult(
            "ffmpeg",
            "blocked",
            version,
            f"FFmpeg {format_version(version)} is not a maintained security branch.",
        )
    if version < floor:
        return PolicyResult(
            "ffmpeg",
            "blocked",
            version,
            f"FFmpeg {format_version(version)} is below the patched {format_version(floor)} branch floor.",
        )
    return PolicyResult(
        "ffmpeg",
        "supported",
        version,
        f"FFmpeg branch meets the patched floor {format_version(floor)}.",
    )


def evaluate_nvdec(version_output):
    """Classify the optional CUDA decode path, failing closed when unsure."""
    output = str(version_output or "")
    if NVDEC_FIX_COMMIT in output.lower():
        return PolicyResult(
            "nvdec",
            "supported",
            parse_ffmpeg_version(output),
            "FFmpeg banner includes the reviewed NVDEC fix commit.",
        )

    version = parse_ffmpeg_version(output)
    if version is None:
        return PolicyResult(
            "nvdec",
            "unknown",
            None,
            "NVDEC is disabled because the FFmpeg build cannot be identified.",
        )
    if version <= NVDEC_RELEASE_BOUNDARY:
        return PolicyResult(
            "nvdec",
            "blocked",
            version,
            "NVDEC remains disabled through the reviewed 8.1.2 boundary.",
        )

    ffmpeg = evaluate_ffmpeg_runtime(output)
    if not ffmpeg.accepted:
        return PolicyResult(
            "nvdec",
            "blocked",
            version,
            f"NVDEC requires an accepted FFmpeg runtime: {ffmpeg.reason}",
        )
    return PolicyResult(
        "nvdec",
        "supported",
        version,
        "FFmpeg is newer than the conservative NVDEC boundary.",
    )


def evaluate_qt_runtime(version_text):
    """Classify the Qt runtime bundled by PyQt6."""
    version = parse_runtime_version(version_text)
    if version is None:
        return PolicyResult(
            "qt",
            "unknown",
            None,
            "Qt runtime version is unavailable.",
        )
    if version < QT_SECURITY_MINIMUM:
        return PolicyResult(
            "qt",
            "blocked",
            version,
            f"Qt {format_version(version)} is below the security minimum {format_version(QT_SECURITY_MINIMUM)}.",
        )
    return PolicyResult(
        "qt",
        "supported",
        version,
        f"Qt meets the security minimum {format_version(QT_SECURITY_MINIMUM)}.",
    )


def qt_runtime_identity():
    """Return the actual PyQt and Qt versions without requiring a GUI."""
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    except ImportError:
        return {"pyqt": "unavailable", "qt": "unavailable"}
    return {"pyqt": PYQT_VERSION_STR, "qt": QT_VERSION_STR}


def policy_manifest():
    """Return the manifest embedded in release/runtime inventories."""
    return {
        "schema": POLICY_SCHEMA,
        "version": POLICY_VERSION,
        "reviewed_at": POLICY_REVIEWED_AT,
        "ffmpeg": {
            "security_branch_floors": {
                f"{major}.{minor}": format_version(floor)
                for (major, minor), floor in sorted(
                    FFMPEG_SECURITY_BRANCH_FLOORS.items()
                )
            },
            "unknown_build": "blocked",
            "future_release_branch": "supported_with_re_review_warning",
        },
        "nvdec": {
            "release_boundary_exclusive": format_version(NVDEC_RELEASE_BOUNDARY),
            "known_fix_commits": [NVDEC_FIX_COMMIT],
            "unknown_build": "blocked",
        },
        "qt": {
            "security_minimum": format_version(QT_SECURITY_MINIMUM),
            "unknown_runtime": "blocked",
        },
    }
