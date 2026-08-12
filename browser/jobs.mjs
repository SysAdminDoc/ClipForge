const JOB_LABELS = {
    waveform: 'Waveform',
    proxy: 'Proxy',
    export: 'Export',
};

export function browserJobLabel(type) {
    return JOB_LABELS[type] || 'Media job';
}

export function browserJobId(type, token) {
    return `cf_${type}_${String(token || '').replace(/[^a-z0-9]/gi, '')}`;
}

export function browserJobProgress(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 && number <= 100
        ? number
        : fallback;
}

export function summarizeBrowserJob(job, fallbackProgress = 0) {
    if (!job) return null;
    return {
        id: job.id,
        type: job.type,
        state: job.state,
        progress: browserJobProgress(job.progress, fallbackProgress),
        stage: job.stage || null,
        error: job.error || null,
        engineReusable: job.engineReusable ?? null,
        startedAt: job.startedAt || null,
        finishedAt: job.finishedAt || null,
    };
}
