import hashlib
import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from clipforge.ai_tools import (
    AI_TOOL_SPECS,
    AIFrameCache,
    AIToolInstallWorker,
    AIToolManager,
)


def test_ai_manifest_has_pinned_supply_chain_metadata():
    assert set(AI_TOOL_SPECS) == {"realesrgan", "span", "rife"}
    for spec in AI_TOOL_SPECS.values():
        assert spec.url.startswith("https://github.com/")
        assert len(spec.sha256) == 64
        int(spec.sha256, 16)
        assert spec.archive_size > 0
        assert spec.unpacked_size > 0
        assert spec.license
        assert spec.models


def test_ai_manager_rejects_unknown_tools_and_verifies_managed_executable(tmp_path):
    manager = AIToolManager(tmp_path / "tools")
    with pytest.raises(ValueError, match="not in the ClipForge install manifest"):
        manager.spec("arbitrary-download")

    spec = manager.spec("realesrgan")
    install = manager.install_dir("realesrgan")
    executable = install / "package" / spec.executable
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"verified executable")
    executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    (install / "install.json").write_text(
        json.dumps(
            {
                "tool_id": spec.tool_id,
                "version": spec.version,
                "archive_sha256": spec.sha256,
                "executable": executable.relative_to(install).as_posix(),
                "executable_sha256": executable_hash,
            }
        ),
        encoding="utf-8",
    )

    assert manager.managed_path("realesrgan") == executable
    assert manager.status("realesrgan")["verified"] is True
    executable.write_bytes(b"tampered")
    assert manager.managed_path("realesrgan") is None


def test_ai_installer_blocks_zip_slip(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.exe", b"bad")
    destination = tmp_path / "extract"
    destination.mkdir()

    with pytest.raises(ValueError, match="Unsafe archive path"):
        AIToolInstallWorker._safe_extract(
            archive,
            destination,
            cancel_event=type("Event", (), {"is_set": lambda self: False})(),
            progress=lambda _value: None,
        )
    assert not (tmp_path / "escape.exe").exists()


def test_ai_download_resumes_partial_archive(tmp_path, monkeypatch):
    payload = b"verified-package-bytes"
    manager = AIToolManager(tmp_path / "tools")
    worker = AIToolInstallWorker(manager, "span")
    spec = replace(
        AI_TOOL_SPECS["span"],
        archive_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    archive = tmp_path / "package.zip"
    partial = archive.with_suffix(".zip.part")
    partial.write_bytes(payload[:8])
    requests = []

    class Response(io.BytesIO):
        status = 206

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response(payload[8:])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    worker._download(spec, archive)

    assert archive.read_bytes() == payload
    assert requests[0][0].headers["Range"] == "bytes=8-"


def test_ai_frame_cache_is_atomic_reusable_and_source_keyed(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-v1")
    cache = AIFrameCache(tmp_path / "frames")
    staging = cache.staging_dir(source)
    (staging / "frame_000001.png").write_bytes(b"one")
    (staging / "frame_000002.png").write_bytes(b"two")
    committed = cache.commit(source, staging, 2)

    assert cache.lookup(source) == committed
    assert cache.estimate_required_bytes(
        {"width": 1920, "height": 1080, "fps": 30, "duration": 10}
    ) > 0

    source.write_bytes(b"source-v2-longer")
    assert cache.lookup(source) is None
