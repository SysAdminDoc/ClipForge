"""Cross-surface runtime provenance and release-gate validation."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .runtime_policy import policy_manifest


ROOT = Path(__file__).resolve().parent.parent
PROVENANCE_SCHEMA = "clipforge.provenance"
PROVENANCE_SCHEMA_VERSION = 1
REVIEWED_DATE = "2026-08-12"

LOCK_GROUPS = (
    ("runtime", "requirements.lock", True),
    ("development", "requirements-dev.lock", False),
    ("optional-mpv", "requirements-mpv.lock", False),
)

_PACKAGE_LINE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s\\;]+)(?:\s*;\s*(.*?))?\s*\\?\s*$"
)
_HASH_LINE = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})")
_LICENSE_OVERRIDES = {
    "pyqt6": "GPL-3.0-only OR commercial",
}


def sha256_file(path):
    """Return the SHA-256 digest for a regular file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _creationflags():
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _first_line(output):
    return next((line.strip() for line in output.splitlines() if line.strip()), "")


def _version_output(path, *, name=None):
    try:
        result = subprocess.run(
            [str(path), "--version" if name == "python" else "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "status": "unavailable",
            "version": None,
            "configuration": None,
            "error": str(error),
        }
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return {
        "status": "available" if result.returncode == 0 else "unusable",
        "version": _first_line(output) or f"exit {result.returncode}",
        "configuration": next(
            (line.strip() for line in output.splitlines() if line.startswith("configuration:")),
            None,
        ),
        "returncode": result.returncode,
    }


def executable_identity(path, *, name=None, license=None):
    """Describe the exact executable that a job can invoke."""
    identity = {
        "name": name or Path(str(path)).name if path else name,
        "path": os.fspath(path) if path else None,
        "status": "missing" if not path else "unavailable",
        "version": None,
        "sha256": None,
        "size": None,
        "license": license,
    }
    if not path:
        return identity
    executable = Path(path).expanduser()
    if not executable.is_file():
        return identity
    identity["path"] = os.fspath(executable.resolve())
    identity["size"] = executable.stat().st_size
    identity["sha256"] = sha256_file(executable)
    identity.update(_version_output(executable, name=name))
    return identity


def _ffmpeg_license(identity):
    configuration = identity.get("configuration") or ""
    if "--enable-gpl" in configuration:
        return "GPL-2.0-or-later", "FFmpeg configuration contains --enable-gpl"
    return "LGPL-2.1-or-later", "FFmpeg configuration does not contain --enable-gpl"


def _media_tool_identity(path, name):
    identity = executable_identity(path, name=name)
    license_name, basis = _ffmpeg_license(identity)
    identity["license"] = license_name if identity["status"] != "missing" else None
    identity["license_basis"] = basis if identity["status"] != "missing" else None
    return identity


def _marker_applies(marker):
    marker = str(marker or "").strip()
    if not marker:
        return True
    match = re.search(r"sys_platform\s*==\s*['\"]([^'\"]+)['\"]", marker)
    return not match or sys.platform == match.group(1)


def parse_lock_file(path):
    """Parse pinned versions, applicability markers, and artifact hashes."""
    entries = []
    current = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _PACKAGE_LINE.match(line)
        if match:
            if current is not None:
                entries.append(current)
            current = {
                "name": match.group(1),
                "normalized_name": re.sub(r"[-_.]+", "-", match.group(1).lower()),
                "locked_version": match.group(2),
                "marker": match.group(3) or "",
                "lock_hashes": [],
            }
            continue
        if current is not None:
            current["lock_hashes"].extend(_HASH_LINE.findall(line))
    if current is not None:
        entries.append(current)
    return entries


def _license_files(distribution):
    files = []
    for package_path in distribution.files or ():
        normalized = package_path.as_posix()
        lower = normalized.lower()
        if "dist-info" not in lower or not re.search(r"(?:license|copying|notice)", lower):
            continue
        candidate = distribution.locate_file(package_path)
        if not candidate.is_file():
            continue
        files.append(
            {
                "path": normalized,
                "size": candidate.stat().st_size,
                "sha256": sha256_file(candidate),
            }
        )
    return files


def _license_from_metadata(distribution, normalized_name):
    license_name = distribution.metadata.get("License")
    if license_name:
        return license_name
    if normalized_name in _LICENSE_OVERRIDES:
        return _LICENSE_OVERRIDES[normalized_name]
    for license_file in _license_files(distribution):
        content = distribution.locate_file(license_file["path"]).read_text(
            encoding="utf-8", errors="ignore"
        )
        upper = content.upper()
        if "APACHE LICENSE" in upper and "VERSION 2.0" in upper:
            return "Apache-2.0"
        if "MIT LICENSE" in upper or "THE MIT LICENSE" in upper:
            return "MIT"
        if "BSD 3-CLAUSE" in upper:
            return "BSD-3-Clause"
        if "BSD 2-CLAUSE" in upper:
            return "BSD-2-Clause"
        if "GNU LESSER GENERAL PUBLIC LICENSE" in upper and "VERSION 3" in upper:
            return "LGPL-3.0-only"
        if "GNU GENERAL PUBLIC LICENSE" in upper and "VERSION 3" in upper:
            return "GPL-3.0-only"
    return None


def _installed_dependency(entry):
    try:
        distribution = importlib.metadata.distribution(entry["normalized_name"])
    except importlib.metadata.PackageNotFoundError:
        return {
            "installed": False,
            "installed_version": None,
            "version_match": False,
            "license": None,
            "license_files": [],
        }
    return {
        "installed": True,
        "installed_version": distribution.version,
        "version_match": distribution.version == entry["locked_version"],
        "license": _license_from_metadata(distribution, entry["normalized_name"]),
        "license_files": _license_files(distribution),
    }


def build_python_inventory(*, strict_lock=None):
    strict_name = Path(strict_lock).name if strict_lock else None
    groups = []
    for role, filename, default_required in LOCK_GROUPS:
        lock_path = ROOT / filename
        dependencies = []
        for entry in parse_lock_file(lock_path):
            applicable = _marker_applies(entry["marker"])
            installed = _installed_dependency(entry) if applicable else {
                "installed": False,
                "installed_version": None,
                "version_match": None,
                "license": None,
                "license_files": [],
            }
            dependencies.append(
                {
                    **entry,
                    "applicable": applicable,
                    "required_for_gate": bool(
                        applicable and (default_required or filename == strict_name)
                    ),
                    **installed,
                }
            )
        groups.append(
            {
                "role": role,
                "path": filename,
                "sha256": sha256_file(lock_path),
                "dependencies": dependencies,
            }
        )
    return {
        "interpreter": executable_identity(sys.executable, name="python"),
        "groups": groups,
    }


def _browser_inventory():
    from scripts.verify_browser_runtime import SBOM_PATH, verify_inventory

    verify_inventory()
    sbom = json.loads(SBOM_PATH.read_text(encoding="utf-8"))
    return {
        "path": SBOM_PATH.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(SBOM_PATH),
        "schema": sbom.get("schema"),
        "schema_version": sbom.get("schema_version"),
        "generated_at": sbom.get("generated_at"),
        "runtime_policy": sbom.get("runtime_policy"),
        "components": sbom.get("components", []),
        "artifacts": sbom.get("artifacts", []),
    }


def _media_inventory():
    from .tools import FFMPEG, FFPROBE

    return {
        "ffmpeg": _media_tool_identity(FFMPEG, "ffmpeg"),
        "ffprobe": _media_tool_identity(FFPROBE, "ffprobe"),
    }


def _mpv_inventory():
    from .mpv_backend import find_native_library, probe_mpv

    capability = probe_mpv()
    native_path = getattr(capability, "library_file", None) or find_native_library()
    native = executable_identity(native_path, name="libmpv")
    native["version"] = getattr(capability, "native_version", None) or native.get("version")
    native["version_status"] = getattr(capability, "native_version_status", "unknown")
    native["license"] = "GPL-2.0-or-later" if native_path else None
    native["license_basis"] = "libmpv native runtime" if native_path else None
    wrapper = {
        "status": "available" if capability.wrapper_version else "unavailable",
        "version": capability.wrapper_version,
        "library_directory": capability.library_path,
        "reason": capability.reason or None,
    }
    return {"wrapper": wrapper, "native": native}


def _ai_inventory():
    from .ai_tools import AI_TOOL_SPECS, AIToolManager
    from .tools import find_realesrgan, find_rife, find_span

    manager = AIToolManager()
    discover = {
        "realesrgan": find_realesrgan,
        "span": find_span,
        "rife": find_rife,
    }
    tools = []
    for tool_id, spec in AI_TOOL_SPECS.items():
        status = manager.status(tool_id, discover[tool_id]())
        tools.append(status)
        status["source_url"] = spec.url
        status["license_url"] = spec.license_url
    return {"tools": tools}


def build_provenance(*, strict_lock=None):
    """Build a release or support manifest from the runtimes actually present."""
    return {
        "schema": PROVENANCE_SCHEMA,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "reviewed_date": REVIEWED_DATE,
        "application": {
            "name": "ClipForge",
            "version": importlib.import_module("clipforge").APP_VERSION,
        },
        "runtime_policy": policy_manifest(),
        "browser": _browser_inventory(),
        "python": build_python_inventory(strict_lock=strict_lock),
        "media": _media_inventory(),
        "libmpv": _mpv_inventory(),
        "ai_tools": _ai_inventory(),
    }


def validate_provenance(manifest):
    """Raise when a manifest omits or disagrees with a required identity."""
    errors = []
    if manifest.get("schema") != PROVENANCE_SCHEMA:
        errors.append("provenance schema is missing or unsupported")
    if manifest.get("runtime_policy") != policy_manifest():
        errors.append("runtime policy provenance is stale")
    browser = manifest.get("browser", {})
    if not browser.get("sha256") or not browser.get("artifacts") or not browser.get("components"):
        errors.append("browser runtime provenance is incomplete")
    for name in ("ffmpeg", "ffprobe"):
        identity = manifest.get("media", {}).get(name, {})
        if identity.get("status") != "available":
            errors.append(f"{name} runtime is unavailable")
        for field in ("version", "sha256", "license"):
            if not identity.get(field):
                errors.append(f"{name} provenance lacks {field}")

    for group in manifest.get("python", {}).get("groups", []):
        for dependency in group.get("dependencies", []):
            if not dependency.get("required_for_gate"):
                continue
            if not dependency.get("installed"):
                errors.append(f"missing required Python dependency {dependency['name']}")
            elif not dependency.get("version_match"):
                errors.append(
                    f"Python dependency {dependency['name']} is "
                    f"{dependency.get('installed_version')}, expected {dependency['locked_version']}"
                )
            if not dependency.get("lock_hashes"):
                errors.append(f"Python dependency {dependency['name']} has no lock hash")
            if not dependency.get("license") or not dependency.get("license_files"):
                errors.append(f"Python dependency {dependency['name']} lacks license provenance")

    native = manifest.get("libmpv", {}).get("native", {})
    if native.get("status") == "available":
        for field in ("sha256", "license"):
            if not native.get(field):
                errors.append(f"libmpv provenance lacks {field}")

    for tool in manifest.get("ai_tools", {}).get("tools", []):
        if tool.get("availability") in {"invalid_managed_install", "external-unverified"}:
            errors.append(f"AI tool {tool.get('tool_id')} is not provenance-verified")
        for field in ("archive_sha256", "license"):
            if not tool.get(field):
                errors.append(f"AI tool {tool.get('tool_id')} provenance lacks {field}")

    if errors:
        raise RuntimeError("; ".join(errors))
    return manifest
