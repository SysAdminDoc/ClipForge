import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_mediabunny_artifact_manifest_matches_vendored_bundle():
    manifest = json.loads(
        (ROOT / "vendor" / "mediabunny" / "manifest.json").read_text(encoding="utf-8")
    )
    artifact = ROOT / manifest["artifact"]
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    assert manifest["version"] == "1.53.0"
    assert manifest["license"] == "MPL-2.0"
    assert digest == manifest["artifactSha256"]
    assert (ROOT / manifest["licenseFile"]).is_file()


def test_mediabunny_spike_module_exposes_fallback_and_parity_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Mediabunny spike contract test")
    runner = r"""
const spike = await import('./browser/mediabunny-spike.mjs');
const capabilities = spike.browserCapabilityReport({
    navigator: { userAgent: 'Mozilla/5.0 Firefox/145.0' },
    VideoEncoder: undefined,
    VideoDecoder: undefined,
    AudioEncoder: undefined,
});
const codecs = await spike.probeMediabunnyCodecs({
    getEncodableVideoCodecs: async () => ['avc', 'vp9'],
    getEncodableAudioCodecs: async () => ['aac'],
});
const comparison = spike.compareBenchmarkResults(
    { outputBytes: 10, metadata: { duration: 2, tracks: [{ type: 'video' }] }, outputSha256: 'a' },
    { outputBytes: 20, metadata: { duration: 2.01, tracks: [{ type: 'video' }] }, outputSha256: 'b' },
);
process.stdout.write(JSON.stringify({ capabilities, codecs, comparison }));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", runner],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    payload = json.loads(result.stdout)

    assert payload["capabilities"]["browser"] == "Firefox"
    assert payload["capabilities"]["fallback"].startswith("FFmpeg.wasm required")
    assert payload["codecs"]["video"][0] == {"codec": "avc", "supported": True}
    assert payload["comparison"]["parity"] is True


def test_mediabunny_spike_is_isolated_from_production_editor():
    html = (ROOT / "mediabunny-spike.html").read_text(encoding="utf-8")
    page_script = (ROOT / "mediabunny-spike-page.mjs").read_text(encoding="utf-8")
    editor = (ROOT / "editor.js").read_text(encoding="utf-8")

    assert 'src="mediabunny-spike-page.mjs"' in html
    assert "vendor/mediabunny/mediabunny-1.53.0.min.mjs" in page_script
    assert "runMediabunnyBenchmark" in page_script
    assert "Mediabunny" not in editor
