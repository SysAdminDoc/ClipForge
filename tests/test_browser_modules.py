import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run_node(source):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser module tests")
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def test_project_module_keeps_schema_normalization_dom_free():
    result = _run_node(
        r"""
const { normalizeProject, serializeProject } = await import('./browser/project.mjs');
const hostile = "x');globalThis.projectImportExecuted=true;//";
const normalized = normalizeProject({
    schema: 'clipforge.project',
    version: 1,
    name: '<img onerror=alert(1)>',
    media: [{ id: hostile, name: 'source.mp4', type: 'video' }],
    clips: [{ id: 'clip-source', mediaId: hostile, duration: 2, track: 'video' }],
    transitions: [],
    timeline: { pixelsPerSecond: 50, trackStates: {} },
});
const serialized = serializeProject({
    mediaItems: normalized.media,
    clips: normalized.clips,
    transitions: normalized.transitions,
    pixelsPerSecond: normalized.timeline.pixelsPerSecond,
    trackStates: normalized.timeline.trackStates,
    name: normalized.name,
    savedAt: '2026-08-12T00:00:00.000Z',
});
process.stdout.write(JSON.stringify({
    normalizedIds: [normalized.media[0].id, normalized.clips[0].id],
    linkedMedia: normalized.clips[0].mediaId,
    serializedSchema: [serialized.schema, serialized.version],
    serializedName: serialized.name,
}));
""",
    )
    assert result == {
        "normalizedIds": ["media-1", "clip-1"],
        "linkedMedia": "media-1",
        "serializedSchema": ["clipforge.project", 1],
        "serializedName": "<img onerror=alert(1)>",
    }


def test_timeline_module_is_the_single_preview_and_export_planner():
    result = _run_node(
        r"""
const { resolveTimelineAtTime, buildResolvedTimelinePlan } = await import('./browser/timeline.mjs');
const clips = [
    { id: 'video-a', track: 'video', startTime: 0, duration: 2, inPoint: 3, outPoint: 5 },
    { id: 'audio-a', track: 'audio', startTime: 0, duration: 2, inPoint: 3, outPoint: 5, volume: 80 },
    { id: 'video-b', track: 'video', startTime: 2, duration: 3, inPoint: 1, outPoint: 4 },
];
const resolved = resolveTimelineAtTime(clips, {}, 0.5);
const plan = buildResolvedTimelinePlan(clips, {});
process.stdout.write(JSON.stringify({
    video: [resolved.video.clip.id, resolved.video.sourceTime],
    audio: [resolved.audio[0].clip.id, resolved.audio[0].sourceTime, resolved.audio[0].volume],
    videoSegments: plan.videoSegments.map(segment => [
        segment.clip.id,
        segment.timelineStart,
        segment.sourceStart,
        segment.duration,
    ]),
}));
""",
    )
    assert result == {
        "video": ["video-a", 3.5],
        "audio": ["audio-a", 3.5, 80],
        "videoSegments": [
            ["video-a", 0, 3, 2],
            ["video-b", 2, 1, 3],
        ],
    }


def test_browser_boundary_modules_cover_jobs_storage_preview_and_export():
    result = _run_node(
        r"""
const { browserJobLabel, browserJobId, summarizeBrowserJob } = await import('./browser/jobs.mjs');
const { proxyKeyFromFingerprint, proxyFingerprintMatches, proxyRecordIsValid } = await import('./browser/storage.mjs');
const { resolvePreviewState } = await import('./browser/preview.mjs');
const { buildExportPreflight } = await import('./browser/export.mjs');
const { redactBrowserValue } = await import('./browser/diagnostics.mjs');
const fingerprint = { name: 'source.mp4', size: 10, lastModified: 4, sampleBytes: 10, sampleSha256: 'abc' };
const blob = new Blob(['proxy']);
const clip = {
    id: 'v', mediaId: 'm', track: 'video', type: 'video', startTime: 0, duration: 2,
    inPoint: 0, outPoint: 2, linkedTo: 'a', volume: 100,
};
const audio = { ...clip, id: 'a', track: 'audio', type: 'audio', linkedTo: 'v' };
const preview = resolvePreviewState({
    video: { clip, sourceTime: 0.5 },
    audio: [{ clip: audio, volume: 80 }],
}, [{ id: 'm', url: 'blob:source' }]);
const exportPlan = buildExportPreflight({
    clips: [clip, audio],
    mediaItems: [{ id: 'm', file: { name: 'source.mp4' }, missing: false, width: 320, height: 180 }],
    ffmpegLoaded: true,
    timelinePlan: { videoSegments: [{ clip, timelineStart: 0, duration: 2 }] },
});
const key = proxyKeyFromFingerprint(fingerprint);
process.stdout.write(JSON.stringify({
    label: browserJobLabel('proxy'),
    id: browserJobId('proxy', 'token-123'),
    summary: summarizeBrowserJob({ id: 'j', type: 'proxy', state: 'running', progress: 140 }),
    key,
    fingerprintMatches: proxyFingerprintMatches(fingerprint, { ...fingerprint }),
    recordValid: proxyRecordIsValid({
        profile: 2, complete: true, key, blob, size: blob.size, source: fingerprint,
    }, fingerprint),
    preview: [preview.url, preview.sourceTime, preview.muted, preview.volume],
    exportSupported: exportPlan.supported,
    redacted: redactBrowserValue({ token: 'secret', path: 'C:\\Users\\name\\source.mp4' }),
}));
""",
    )
    assert result == {
        "label": "Proxy",
        "id": "cf_proxy_token123",
        "summary": {
            "id": "j",
            "type": "proxy",
            "state": "running",
            "progress": 0,
            "stage": None,
            "error": None,
            "engineReusable": None,
            "startedAt": None,
            "finishedAt": None,
        },
        "key": "v2:10:4:abc:source.mp4",
        "fingerprintMatches": True,
        "recordValid": True,
        "preview": ["blob:source", 0.5, False, 0.8],
        "exportSupported": True,
        "redacted": {"token": "<redacted-secret>", "path": "<redacted-path>"},
    }
