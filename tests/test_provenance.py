import copy
import sys

import pytest

from clipforge.provenance import (
    build_provenance,
    executable_identity,
    parse_lock_file,
    validate_provenance,
)


def test_lock_parser_retains_pins_markers_and_hashes():
    entries = parse_lock_file("requirements.lock")
    pyqt = next(entry for entry in entries if entry["normalized_name"] == "pyqt6")

    assert pyqt["locked_version"] == "6.11.0"
    assert pyqt["marker"] == ""
    assert len(pyqt["lock_hashes"]) >= 2


def test_executable_identity_records_exact_runtime():
    identity = executable_identity(sys.executable, name="python")

    assert identity["status"] == "available"
    assert identity["version"].startswith("Python")
    assert len(identity["sha256"]) == 64
    assert identity["size"] > 0


def test_provenance_covers_every_execution_surface():
    manifest = build_provenance()
    validate_provenance(manifest)

    assert manifest["schema"] == "clipforge.provenance"
    assert manifest["browser"]["artifacts"]
    assert manifest["browser"]["components"]
    assert manifest["media"]["ffmpeg"]["sha256"]
    assert manifest["media"]["ffprobe"]["license"]
    assert manifest["python"]["groups"]
    assert {tool["tool_id"] for tool in manifest["ai_tools"]["tools"]} == {
        "realesrgan",
        "span",
        "rife",
    }
    assert "native" in manifest["libmpv"]


def test_stale_required_python_provenance_fails():
    manifest = build_provenance()
    stale = copy.deepcopy(manifest)
    dependency = stale["python"]["groups"][0]["dependencies"][0]
    dependency["installed_version"] = "0.0.0"
    dependency["version_match"] = False

    with pytest.raises(RuntimeError, match="Python dependency"):
        validate_provenance(stale)

