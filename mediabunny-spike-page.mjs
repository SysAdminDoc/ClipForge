import * as Mediabunny from './vendor/mediabunny/mediabunny-1.53.0.min.mjs';
import {
    browserCapabilityReport,
    buildEvaluationReport,
    compareBenchmarkResults,
    probeMediabunnyCodecs,
    runFfmpegBenchmark,
    runMediabunnyBenchmark,
} from './browser/mediabunny-spike.mjs';

window.Mediabunny = Mediabunny;
window.clipforgeMediabunnySpikeReady = true;

let ffmpegPromise = null;

function status(message) {
    document.getElementById('status').textContent = message;
}

async function loadFfmpeg() {
    if (!ffmpegPromise) {
        ffmpegPromise = (async () => {
            await window.coiReady;
            const [{ FFmpeg }, { fetchFile }] = await Promise.all([
                import('./vendor/ffmpeg/ffmpeg/index.js'),
                import('./vendor/ffmpeg/util/index.js'),
            ]);
            const ffmpeg = new FFmpeg();
            ffmpeg.on('log', ({message}) => console.debug('[ffmpeg-spike]', message));
            const coreURL = new URL('./vendor/ffmpeg/core/ffmpeg-core.js', window.location.href).href;
            const wasmURL = new URL('./vendor/ffmpeg/core/ffmpeg-core.wasm', window.location.href).href;
            await ffmpeg.load({coreURL, wasmURL});
            return {ffmpeg, fetchFile};
        })();
    }
    return ffmpegPromise;
}

async function runBenchmark() {
    const file = document.getElementById('sourceFile').files[0];
    if (!file) throw new Error('Choose a source video first');
    const start = Number(document.getElementById('trimStart').value) || 0;
    const endValue = document.getElementById('trimEnd').value;
    const end = endValue === '' ? null : Number(endValue);
    status('Probing WebCodecs and Mediabunny codecs...');
    const capabilities = browserCapabilityReport(window);
    const codecs = await probeMediabunnyCodecs(Mediabunny);
    let mediabunny;
    let ffmpeg;
    const errors = {};
    try {
        status('Running Mediabunny + WebCodecs...');
        mediabunny = await runMediabunnyBenchmark({
            api: Mediabunny,
            file,
            start,
            end,
            onProgress: progress => status(`Mediabunny + WebCodecs: ${Math.round(progress * 100)}%`),
        });
    } catch (error) {
        errors.mediabunny = String(error?.message || error);
    }
    try {
        status('Loading and running pinned FFmpeg.wasm...');
        const loaded = await loadFfmpeg();
        ffmpeg = await runFfmpegBenchmark({
            ...loaded,
            file,
            start,
            end: end ?? await (async () => {
                const input = new Mediabunny.Input({source: new Mediabunny.BlobSource(file), formats: Mediabunny.ALL_FORMATS});
                try { return await input.computeDuration(); } finally { input.dispose?.(); }
            })(),
            onProgress: progress => status(`FFmpeg.wasm: ${Math.round(progress * 100)}%`),
        });
    } catch (error) {
        errors.ffmpeg = String(error?.message || error);
    }
    const comparison = mediabunny && ffmpeg
        ? compareBenchmarkResults(mediabunny, ffmpeg)
        : null;
    const report = buildEvaluationReport({
        capabilities,
        codecs,
        mediabunny,
        ffmpeg,
        comparison,
    });
    if (Object.keys(errors).length) report.errors = errors;
    document.getElementById('results').textContent = JSON.stringify(report, null, 2);
    status(comparison?.parity ? 'Benchmark complete: parity checks passed.' : 'Benchmark complete: inspect results and errors.');
    return report;
}

document.getElementById('runBenchmark').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
        await runBenchmark();
    } catch (error) {
        status(`Benchmark failed: ${error.message}`);
        document.getElementById('results').textContent = JSON.stringify({error: error.message}, null, 2);
    } finally {
        button.disabled = false;
    }
});

window.clipforgeMediabunnyRun = runBenchmark;
