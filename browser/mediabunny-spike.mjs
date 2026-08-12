export const MEDIABUNNY_SPIKE_SCHEMA = 'clipforge.mediabunny-spike';
export const MEDIABUNNY_SPIKE_VERSION = 1;
export const MEDIABUNNY_VERSION = '1.53.0';

const VIDEO_CODECS = ['avc', 'vp8', 'vp9', 'av1', 'hevc'];
const AUDIO_CODECS = ['aac', 'opus', 'vorbis', 'mp3', 'flac'];

function finiteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function memoryBytes() {
    const value = Number(globalThis.performance?.memory?.usedJSHeapSize);
    return Number.isFinite(value) ? value : null;
}

function createMemoryRecorder() {
    let peak = null;
    let timer = null;
    const sample = () => {
        const value = memoryBytes();
        if (value !== null) peak = Math.max(peak ?? value, value);
    };
    return {
        start() {
            sample();
            timer = globalThis.setInterval(sample, 25);
        },
        sample,
        stop() {
            if (timer !== null) globalThis.clearInterval(timer);
            timer = null;
            sample();
            return peak;
        },
    };
}

async function sha256Hex(bytes) {
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return [...new Uint8Array(digest)]
        .map(value => value.toString(16).padStart(2, '0'))
        .join('');
}

export function browserCapabilityReport(environment = globalThis) {
    const userAgent = String(environment.navigator?.userAgent || 'unknown');
    const webCodecs = Boolean(
        typeof environment.VideoEncoder === 'function'
        && typeof environment.VideoDecoder === 'function'
        && typeof environment.AudioEncoder === 'function',
    );
    return {
        userAgent,
        browser: /Firefox/i.test(userAgent)
            ? 'Firefox'
            : /Safari/i.test(userAgent) && !/Chrome|Chromium/i.test(userAgent)
                ? 'Safari'
                : /Chrome|Chromium/i.test(userAgent)
                    ? 'Chromium'
                    : 'Other',
        crossOriginIsolated: Boolean(environment.crossOriginIsolated),
        webCodecs,
        preciseMemory: Number.isFinite(Number(environment.performance?.memory?.usedJSHeapSize)),
        fallback: webCodecs
            ? 'Mediabunny candidate; retain FFmpeg.wasm for unsupported codecs and filters.'
            : 'FFmpeg.wasm required because WebCodecs is unavailable.',
    };
}

async function probeCodecList(probe, codecs) {
    if (typeof probe !== 'function') return [];
    const supported = await probe();
    return codecs.map(codec => ({codec, supported: supported.includes(codec)}));
}

export async function probeMediabunnyCodecs(api) {
    const [video, audio] = await Promise.all([
        probeCodecList(api.getEncodableVideoCodecs, VIDEO_CODECS),
        probeCodecList(api.getEncodableAudioCodecs, AUDIO_CODECS),
    ]);
    return {video, audio};
}

async function inspectMedia(api, blob) {
    const input = new api.Input({
        source: new api.BlobSource(blob),
        formats: api.ALL_FORMATS,
    });
    try {
        const tracks = await input.getTracks();
        return {
            duration: await input.computeDuration(),
            tracks: tracks.map(track => ({
                type: track.type || null,
                codec: track.codec || null,
                number: track.number ?? null,
            })),
        };
    } finally {
        input.dispose?.();
    }
}

function outputResult({engine, startedAt, outputBytes, outputBlob, peakMemoryBytes, progress, metadata}) {
    return {
        engine,
        elapsedMs: Math.round(performance.now() - startedAt),
        outputBytes,
        outputSha256: null,
        peakMemoryBytes,
        progress,
        metadata,
        outputBlob,
    };
}

async function finalizeResult(api, result) {
    const bytes = await result.outputBlob.arrayBuffer();
    result.outputSha256 = await sha256Hex(bytes);
    result.metadata = await inspectMedia(api, result.outputBlob);
    return result;
}

export async function runMediabunnyBenchmark({
    api,
    file,
    start = 0,
    end = null,
    transcode = true,
    onProgress = () => {},
} = {}) {
    if (!api?.Input || !api?.Conversion) throw new Error('Mediabunny API is unavailable');
    const input = new api.Input({
        source: new api.BlobSource(file),
        formats: api.ALL_FORMATS,
    });
    const duration = await input.computeDuration();
    const effectiveStart = Math.max(0, finiteNumber(start));
    const effectiveEnd = Math.min(
        duration,
        end == null ? duration : finiteNumber(end, duration),
    );
    if (effectiveEnd <= effectiveStart) {
        input.dispose?.();
        throw new Error('Benchmark end must be after start');
    }
    const output = new api.Output({
        format: new api.Mp4OutputFormat(),
        target: new api.BufferTarget(),
    });
    const options = {
        input,
        output,
        trim: {start: effectiveStart, end: effectiveEnd},
    };
    if (transcode) {
        options.video = {
            codec: 'avc',
            quality: new api.Quality('high'),
            hardwareAcceleration: 'no-preference',
        };
        options.audio = {
            codec: 'aac',
            quality: new api.Quality('high'),
        };
    }
    const conversion = await api.Conversion.init(options);
    if (!conversion.isValid) {
        input.dispose?.();
        throw new Error(
            `Mediabunny discarded required tracks: ${JSON.stringify(conversion.discardedTracks)}`,
        );
    }
    conversion.onProgress = progress => {
        onProgress(progress);
    };
    const recorder = createMemoryRecorder();
    const startedAt = performance.now();
    let peakMemoryBytes = null;
    recorder.start();
    try {
        await conversion.execute();
    } finally {
        peakMemoryBytes = recorder.stop();
        input.dispose?.();
    }
    const buffer = output.target.buffer;
    if (!buffer) throw new Error('Mediabunny returned no output buffer');
    const result = outputResult({
        engine: 'mediabunny',
        startedAt,
        outputBytes: buffer.byteLength,
        outputBlob: new Blob([buffer], {type: 'video/mp4'}),
        peakMemoryBytes,
        progress: 1,
        metadata: {duration: effectiveEnd - effectiveStart, tracks: []},
    });
    return finalizeResult(api, result);
}

export async function runFfmpegBenchmark({
    ffmpeg,
    fetchFile,
    file,
    start = 0,
    end,
    onProgress = () => {},
} = {}) {
    if (!ffmpeg || typeof fetchFile !== 'function') throw new Error('FFmpeg API is unavailable');
    const token = `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
    const inputName = `mediabunny_spike_${token}.input`;
    const outputName = `mediabunny_spike_${token}.mp4`;
    const effectiveDuration = Math.max(0.01, finiteNumber(end) - finiteNumber(start));
    const recorder = createMemoryRecorder();
    const startedAt = performance.now();
    let peakMemoryBytes = null;
    const progressHandler = ({progress}) => {
        const value = Number(progress);
        if (Number.isFinite(value)) onProgress(Math.max(0, Math.min(1, value)));
        recorder.sample();
    };
    ffmpeg.on('progress', progressHandler);
    try {
        await ffmpeg.writeFile(inputName, await fetchFile(file));
        recorder.start();
        const exitCode = await ffmpeg.exec([
            '-ss', String(Math.max(0, finiteNumber(start))),
            '-i', inputName,
            '-t', String(effectiveDuration),
            '-map', '0:v:0',
            '-map', '0:a:0?',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-movflags', '+faststart',
            outputName,
        ]);
        if (exitCode !== 0) throw new Error(`FFmpeg exited with code ${exitCode}`);
        const bytes = await ffmpeg.readFile(outputName);
        peakMemoryBytes = recorder.stop();
        const result = outputResult({
            engine: 'ffmpeg.wasm',
            startedAt,
            outputBytes: bytes.byteLength,
            outputBlob: new Blob([bytes], {type: 'video/mp4'}),
            peakMemoryBytes,
            progress: 1,
            metadata: {duration: effectiveDuration, tracks: []},
        });
        return finalizeResult({
            Input: globalThis.Mediabunny?.Input,
            BlobSource: globalThis.Mediabunny?.BlobSource,
            ALL_FORMATS: globalThis.Mediabunny?.ALL_FORMATS,
        }, result);
    } finally {
        ffmpeg.off?.('progress', progressHandler);
        try { await ffmpeg.deleteFile(inputName); } catch (_) {}
        try { await ffmpeg.deleteFile(outputName); } catch (_) {}
    }
}

export function compareBenchmarkResults(mediabunny, ffmpeg) {
    const durationDelta = Math.abs(
        finiteNumber(mediabunny?.metadata?.duration)
        - finiteNumber(ffmpeg?.metadata?.duration),
    );
    const trackTypes = result => (result?.metadata?.tracks || []).map(track => track.type);
    const sameTrackTypes = JSON.stringify(trackTypes(mediabunny)) === JSON.stringify(trackTypes(ffmpeg));
    return {
        durationDeltaSeconds: durationDelta,
        outputSizeRatio: ffmpeg?.outputBytes
            ? mediabunny.outputBytes / ffmpeg.outputBytes
            : null,
        outputHashesEqual: Boolean(
            mediabunny?.outputSha256
            && ffmpeg?.outputSha256
            && mediabunny.outputSha256 === ffmpeg.outputSha256,
        ),
        sameTrackTypes,
        durationWithinOneFrame: durationDelta <= 1 / 30,
        parity: durationDelta <= 1 / 30 && sameTrackTypes,
    };
}

export function buildEvaluationReport({capabilities, codecs, mediabunny, ffmpeg, comparison}) {
    const publicResult = result => {
        if (!result) return null;
        const {outputBlob: _outputBlob, ...summary} = result;
        return summary;
    };
    return {
        schema: MEDIABUNNY_SPIKE_SCHEMA,
        version: MEDIABUNNY_SPIKE_VERSION,
        mediabunnyVersion: MEDIABUNNY_VERSION,
        generatedAt: new Date().toISOString(),
        capabilities,
        codecs,
        benchmarks: {
            mediabunny: publicResult(mediabunny),
            ffmpeg: publicResult(ffmpeg),
        },
        comparison,
        trimLimitations: [
            'Mediabunny copy-style trimming and FFmpeg stream-copy trimming can begin at keyframe boundaries; exact non-keyframe cuts require the transcode mode measured here.',
            'Output hashes are expected to differ across engines; duration, track types, and container readability are the parity checks.',
            'Peak heap is unavailable unless Chromium exposes performance.memory; repeat with precise-memory-info when memory is a decision gate.',
        ],
    };
}
