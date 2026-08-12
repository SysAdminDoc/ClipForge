const finiteNumber = (value, fallback = 0) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
};

export function buildExportPreflight({
    format = 'mp4',
    resolution = 'original',
    clips = [],
    transitions = [],
    trackStates = {},
    mediaItems = [],
    ffmpegLoaded = false,
    browserFfmpegJob = null,
    timelinePlan = {videoSegments: []},
} = {}) {
    const reasons = [];
    const notes = [];
    const videoClips = clips
        .filter(clip => clip.track === 'video')
        .sort((a, b) => a.startTime - b.startTime);

    if (!ffmpegLoaded) reasons.push('The FFmpeg engine is not ready.');
    if (browserFfmpegJob && browserFfmpegJob.type !== 'export') {
        reasons.push(
            `${browserFfmpegJob.label} is ${browserFfmpegJob.state}; `
            + 'finish or cancel it before exporting.',
        );
    }
    if (videoClips.length === 0) reasons.push('At least one video clip is required.');
    if (videoClips.some(clip => clip.type !== 'video')) {
        reasons.push('Image timeline clips are not supported by browser export.');
    }
    if (transitions.length > 0) {
        reasons.push('Transitions are visible in the editor but are not yet rendered by browser export.');
    }
    const independentAudio = clips.filter(
        clip => (clip.track === 'audio' || clip.track === 'music') && !clip.linkedTo,
    );
    if (independentAudio.length > 0) {
        reasons.push('Unlinked audio and music tracks are not yet mixed by browser export.');
    }
    if (
        trackStates.video?.visible === false
        || trackStates.audio?.muted
        || trackStates.audio?.solo
        || trackStates.music?.muted
        || trackStates.music?.solo
    ) {
        reasons.push('Track visibility, mute, and solo states are preview-only and must be reset before export.');
    }
    if (videoClips.length > 0 && timelinePlan.videoSegments.length !== videoClips.length) {
        reasons.push('The resolved timeline cannot represent every video clip as a deterministic export segment.');
    }

    videoClips.forEach((clip, index) => {
        const media = mediaItems.find(item => item.id == clip.mediaId);
        if (!media || media.missing || !media.file) {
            reasons.push(`Clip ${index + 1} (${clip.name || 'unnamed'}) needs its source relinked.`);
        }
        if (!(Number(clip.duration) > 0) || Number(clip.inPoint) < 0) {
            reasons.push(`Clip ${index + 1} has invalid trim timing.`);
        }
        if (Number(clip.opacity ?? 100) !== 100 || Number(clip.scale ?? 100) !== 100) {
            reasons.push(`Clip ${index + 1} uses opacity or scale, which browser export does not render yet.`);
        }
        const linkedAudio = clips.find(
            candidate => candidate.id === clip.linkedTo && candidate.track === 'audio',
        );
        if (!linkedAudio) {
            reasons.push(`Clip ${index + 1} has changed audio linkage, which browser export cannot represent safely.`);
        } else if (
            Number(linkedAudio.volume ?? 100) !== 100
            || Math.abs(finiteNumber(linkedAudio.startTime) - finiteNumber(clip.startTime)) > 0.01
            || Math.abs(finiteNumber(linkedAudio.duration) - finiteNumber(clip.duration)) > 0.01
            || Math.abs(finiteNumber(linkedAudio.inPoint) - finiteNumber(clip.inPoint)) > 0.01
        ) {
            reasons.push(`Clip ${index + 1} has edited linked-audio timing or volume, which browser export does not render yet.`);
        }
        if (index === 0 && finiteNumber(clip.startTime) > 0.05) {
            reasons.push(`There is an unrendered ${finiteNumber(clip.startTime).toFixed(2)} second gap before the first clip.`);
        } else if (index > 0) {
            const previous = videoClips[index - 1];
            const gap = Number(clip.startTime) - (Number(previous.startTime) + Number(previous.duration));
            if (Math.abs(gap) > 0.05) {
                reasons.push(
                    gap > 0
                        ? `There is an unrendered ${gap.toFixed(2)} second gap before clip ${index + 1}.`
                        : `Clip ${index + 1} overlaps the preceding clip without a supported transition.`,
                );
            }
        }
    });

    if (resolution === 'original' && videoClips.length > 1) {
        const dimensions = new Set(
            videoClips.map(clip => {
                const media = mediaItems.find(item => item.id == clip.mediaId);
                return media?.width && media?.height ? `${media.width}x${media.height}` : 'unknown';
            }),
        );
        if (dimensions.size > 1 || dimensions.has('unknown')) {
            reasons.push('Multi-clip original-resolution export requires matching, known source dimensions; choose a fixed resolution.');
        }
    }

    notes.push('Video trim, rotation, brightness, contrast, and saturation are rendered.');
    notes.push(
        format === 'gif'
            ? 'GIF has no audio by format; embedded source audio will be omitted.'
            : 'Embedded source audio is preserved when present.',
    );
    return {
        supported: reasons.length === 0,
        reasons: [...new Set(reasons)],
        notes,
        videoClips,
        videoSegments: timelinePlan.videoSegments,
        timelinePlan,
    };
}
