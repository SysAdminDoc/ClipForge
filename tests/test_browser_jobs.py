from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_browser_ffmpeg_jobs_share_one_coordinator():
    script = (ROOT / "editor.js").read_text(encoding="utf-8")
    assert "async function runBrowserFfmpegJob(" in script
    assert "if (browserFfmpegJob)" in script
    assert "BrowserJobConflictError" in script
    assert "cancelBrowserFfmpegJob" in script
    assert "window.clipforgeLastBrowserJob" in script
    assert "engineReusable" in script
    assert "job.engine.load({ coreURL, wasmURL })" in script
    assert "90000" in script


def test_browser_job_paths_are_unique_and_fixed_names_are_gone():
    script = (ROOT / "editor.js").read_text(encoding="utf-8")
    assert "crypto?.randomUUID" in script
    assert "const path = `${job.id}_${safeRole}${safeExtension}`" in script
    assert "'audio.raw'" not in script
    assert "'input_wf'" not in script
    assert "ffmpeg.writeFile(" not in script
    assert "ffmpeg.readFile(" not in script
    assert "ffmpeg.deleteFile(" not in script
    assert "ffmpeg.exec(" not in script


def test_every_browser_media_job_uses_coordinator_and_cancel_ui():
    script = (ROOT / "editor.js").read_text(encoding="utf-8")
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for job_type in ("waveform", "proxy", "export"):
        assert f"runBrowserFfmpegJob(\n            '{job_type}'" in script
    assert 'id="cancelJobButton"' in html
    assert 'data-action="cancel-job"' in html
    assert "browserJobState" in script
    assert "browserJobProgress" in script
    assert "value >= 0 && value <= 1" in script
