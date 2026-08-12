const finiteNumber = (value, fallback = 0) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
};

export function resolvePreviewState(resolved, mediaItems = []) {
    const video = resolved?.video;
    if (!video?.clip) return null;
    const clip = video.clip;
    const media = mediaItems.find(item => item.id == clip.mediaId);
    const linkedAudio = (resolved.audio || []).find(
        entry => entry.clip.id === clip.linkedTo || entry.clip.linkedTo === clip.id,
    );
    return {
        clip,
        media,
        url: clip.url || media?.proxyUrl || media?.url || null,
        opacity: Math.max(0, Math.min(1, finiteNumber(clip.opacity, 100) / 100)),
        transform: `rotate(${finiteNumber(clip.rotation)}deg) scale(${Math.max(0.01, finiteNumber(clip.scale, 100) / 100)})`,
        filter: `brightness(${1 + finiteNumber(clip.brightness) / 100}) contrast(${1 + finiteNumber(clip.contrast) / 100}) saturate(${1 + finiteNumber(clip.saturation) / 100})`,
        muted: !linkedAudio,
        volume: linkedAudio ? linkedAudio.volume / 100 : 0,
        sourceTime: Math.max(0, video.sourceTime),
    };
}
