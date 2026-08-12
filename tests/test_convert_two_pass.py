import os
import subprocess
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QTextEdit

from clipforge.panels.convert import ConvertPanel
from clipforge.processes import validate_output
from clipforge.tools import FFMPEG, FFPROBE
from clipforge import tools


_QT_APP = QApplication.instance() or QApplication([])


def _panel():
    panel = ConvertPanel(QTextEdit())
    panel.load_file(
        "source.mp4",
        {
            "duration": 10,
            "width": 1920,
            "height": 1080,
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        },
    )
    return panel


def test_two_pass_is_gated_to_target_bitrate_software_combinations():
    panel = _panel()
    assert not panel.chk_two_pass.isEnabled()

    panel.cmb_rate_control.setCurrentText("Target Bitrate")
    assert panel.chk_two_pass.isEnabled()
    command = panel._build_cmd("output.mp4")
    assert command[command.index("-b:v") + 1] == "5000k"
    assert "-crf" not in command

    panel.cmb_vcodec.setCurrentText("H.265 (libx265)")
    assert not panel.chk_two_pass.isEnabled()
    assert not panel.chk_two_pass.isChecked()

    panel.cmb_vcodec.setCurrentText("Copy (no re-encode)")
    assert panel.cmb_rate_control.currentText() == "Constant Quality"
    assert not panel.cmb_rate_control.isEnabled()
    panel.close()


def test_unusable_hardware_encoder_is_disabled_with_probe_reason(monkeypatch):
    monkeypatch.setitem(tools.HW_ENCODERS, "H.264 NVENC (NVIDIA)", "h264_nvenc")
    monkeypatch.setitem(
        tools.HW_ENCODER_CAPABILITIES,
        "h264_nvenc",
        {"status": "unavailable", "reason": "Cannot load NVENC driver"},
    )
    panel = _panel()
    panel.refresh_hw_encoders()
    index = panel.cmb_vcodec.findText("H.264 NVENC (NVIDIA)")
    item = panel.cmb_vcodec.model().item(index)

    assert item is not None
    assert item.isEnabled() is False
    assert "NVENC driver" in item.toolTip()
    panel.cmb_vcodec.setCurrentIndex(index)
    assert "NVENC driver" in panel._conversion_preflight()[0]
    panel.close()


def test_two_pass_uses_owned_unique_workspace_and_atomic_second_worker(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "clipforge.panels.convert.FFmpegWorker.start",
        lambda _worker: None,
    )
    first = _panel()
    second = _panel()
    for panel in (first, second):
        panel.cmb_rate_control.setCurrentText("Target Bitrate")
        panel.chk_two_pass.setChecked(True)

    first._do_two_pass(str(tmp_path / "first.mp4"), 10, False)
    second._do_two_pass(str(tmp_path / "second.mp4"), 10, False)

    assert first._two_pass_workspace != second._two_pass_workspace
    assert first._two_pass_workspace.is_dir()
    assert second._two_pass_workspace.is_dir()
    first_log = Path(first._two_pass_log)
    assert first_log.parent == first._two_pass_workspace
    assert first._worker.cmd[first._worker.cmd.index("-pass") + 1] == "1"
    assert first._worker.output_path is None

    first._on_two_pass_1_done(True, "complete")
    assert first._worker.cmd[first._worker.cmd.index("-pass") + 1] == "2"
    assert first._worker.output_path == str(tmp_path / "first.mp4")
    assert first._worker.output_contract is not None

    first._cleanup_passlog()
    assert not first_log.parent.exists()
    assert second._two_pass_workspace.is_dir()
    second._cleanup_passlog()
    first.close()
    second.close()


@pytest.mark.skipif(
    not FFMPEG or not FFPROBE,
    reason="FFmpeg and ffprobe are required for two-pass integration coverage",
)
def test_two_pass_produces_validated_output_and_removes_workspace(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    result = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    panel = ConvertPanel(QTextEdit())
    panel.load_file(
        str(source),
        {
            "duration": 1,
            "width": 160,
            "height": 90,
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        },
    )
    panel.cmb_rate_control.setCurrentText("Target Bitrate")
    panel.spn_video_bitrate.setValue(300)
    panel.chk_two_pass.setChecked(True)
    panel._do_two_pass(str(output), 1, False)

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        _QT_APP.processEvents()
        if (
            output.is_file()
            and panel._worker is not None
            and not panel._worker.isRunning()
        ):
            break
        time.sleep(0.02)

    ok, reason = validate_output(output, ffprobe_path=FFPROBE)
    assert ok, reason
    assert panel._two_pass_workspace is None
    panel.close()
