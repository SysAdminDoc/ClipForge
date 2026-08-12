const finiteNumber = (value, fallback = 0) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
};

function trackStateDefaults(trackStates = {}) {
    return {
        video: {visible: true, locked: false, ...(trackStates.video || {})},
        audio: {muted: false, solo: false, ...(trackStates.audio || {})},
        music: {muted: false, solo: false, ...(trackStates.music || {})},
    };
}

function projectDuration(clips) {
    return clips.reduce(
        (end, clip) => Math.max(
            end,
            finiteNumber(clip.startTime) + Math.max(0, finiteNumber(clip.duration)),
        ),
        0,
    );
}

function resolveSourceTime(clip, globalTime) {
    const inPoint = Math.max(0, finiteNumber(clip.inPoint));
    const outPoint = Math.max(
        inPoint,
        finiteNumber(clip.outPoint, inPoint + Math.max(0, finiteNumber(clip.duration))),
    );
    return Math.max(
        inPoint,
        Math.min(
            outPoint,
            inPoint + globalTime - finiteNumber(clip.startTime),
        ),
    );
}

export function resolveTimelineAtTime(clips = [], trackStates = {}, time = 0) {
    const state = trackStateDefaults(trackStates);
    const duration = projectDuration(clips);
    const globalTime = Math.max(0, Math.min(duration, finiteNumber(time)));
    const active = clips.filter(clip => {
        const start = finiteNumber(clip.startTime);
        const end = start + Math.max(0, finiteNumber(clip.duration));
        return start <= globalTime && globalTime < end;
    });
    const videoCandidates = active
        .filter(clip => clip.track === 'video')
        .sort((left, right) => finiteNumber(left.startTime) - finiteNumber(right.startTime));
    const selectedVideo = state.video.visible === false
        ? null
        : videoCandidates[videoCandidates.length - 1] || null;
    const video = selectedVideo
        ? {clip: selectedVideo, sourceTime: resolveSourceTime(selectedVideo, globalTime)}
        : null;
    const audioCandidates = active.filter(
        clip => clip.track === 'audio' || clip.track === 'music',
    );
    const soloTrack = state.audio.solo ? 'audio' : (state.music.solo ? 'music' : null);
    const audio = audioCandidates
        .filter(clip => !soloTrack || clip.track === soloTrack)
        .filter(clip => !state[clip.track]?.muted)
        .map(clip => ({
            clip,
            sourceTime: resolveSourceTime(clip, globalTime),
            volume: Math.max(0, Math.min(100, finiteNumber(clip.volume, 100))),
        }));
    return {
        time: globalTime,
        video,
        audio,
        active,
        trackStates: JSON.parse(JSON.stringify(state)),
    };
}

export function buildResolvedTimelinePlan(clips = [], trackStates = {}) {
    const duration = projectDuration(clips);
    const boundaries = [...new Set([
        0,
        duration,
        ...clips.flatMap(clip => [
            Math.max(0, finiteNumber(clip.startTime)),
            Math.max(0, finiteNumber(clip.startTime) + finiteNumber(clip.duration)),
        ]),
    ])].sort((left, right) => left - right);
    const videoSegments = [];
    const audioSegments = [];
    for (let index = 0; index < boundaries.length - 1; index++) {
        const start = boundaries[index];
        const end = boundaries[index + 1];
        if (end - start <= 0.0001) continue;
        const resolved = resolveTimelineAtTime(clips, trackStates, (start + end) / 2);
        if (resolved.video) {
            const previous = videoSegments[videoSegments.length - 1];
            if (
                previous
                && previous.clip.id === resolved.video.clip.id
                && Math.abs(previous.timelineStart + previous.duration - start) < 0.0001
            ) {
                previous.duration = end - previous.timelineStart;
            } else {
                videoSegments.push({
                    clip: resolved.video.clip,
                    timelineStart: start,
                    sourceStart: finiteNumber(resolved.video.clip.inPoint),
                    duration: end - start,
                });
            }
        }
        audioSegments.push({
            timelineStart: start,
            duration: end - start,
            clips: resolved.audio,
        });
    }
    return {duration, boundaries, videoSegments, audioSegments};
}
