export const PROJECT_SCHEMA = 'clipforge.project';
export const PROJECT_SCHEMA_VERSION = 1;
export const BROWSER_PROXY_PROFILE = 2;

const finiteNumber = (value, fallback = 0) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
};

function projectMediaReference(media = {}) {
    const file = media.file;
    return {
        name: String(file?.name || media.name || 'media').slice(0, 255),
        size: finiteNumber(file?.size ?? media.reference?.size, 0),
        lastModified: finiteNumber(file?.lastModified ?? media.reference?.lastModified, 0),
        mime: String(file?.type || media.reference?.mime || '').slice(0, 127),
        relativePath: String(
            file?.webkitRelativePath || media.reference?.relativePath || '',
        ).slice(0, 1024),
    };
}

export function serializeProject({
    mediaItems = [],
    clips = [],
    transitions = [],
    pixelsPerSecond = 50,
    trackStates = {},
    name = 'Untitled Project',
    savedAt = new Date().toISOString(),
    proxyProfile = BROWSER_PROXY_PROFILE,
} = {}) {
    return {
        schema: PROJECT_SCHEMA,
        version: PROJECT_SCHEMA_VERSION,
        savedAt,
        name: String(name || 'Untitled Project'),
        media: mediaItems.map(media => ({
            id: media.id,
            name: String(media.name || '').slice(0, 255),
            type: ['video', 'audio', 'image'].includes(media.type) ? media.type : 'video',
            duration: finiteNumber(media.duration),
            width: finiteNumber(media.width),
            height: finiteNumber(media.height),
            reference: projectMediaReference(media),
            proxy: media.proxyKey ? {
                key: media.proxyKey,
                profile: proxyProfile,
                size: finiteNumber(media.proxySize),
            } : null,
        })),
        clips: clips.map(clip => ({
            id: clip.id,
            mediaId: clip.mediaId,
            track: ['video', 'audio', 'music'].includes(clip.track) ? clip.track : 'video',
            startTime: finiteNumber(clip.startTime),
            duration: finiteNumber(clip.duration),
            inPoint: finiteNumber(clip.inPoint),
            outPoint: finiteNumber(clip.outPoint, finiteNumber(clip.duration)),
            name: String(clip.name || '').slice(0, 255),
            type: ['video', 'audio', 'image'].includes(clip.type) ? clip.type : 'video',
            linkedTo: clip.linkedTo ?? null,
            opacity: finiteNumber(clip.opacity, 100),
            scale: finiteNumber(clip.scale, 100),
            rotation: finiteNumber(clip.rotation),
            brightness: finiteNumber(clip.brightness),
            contrast: finiteNumber(clip.contrast),
            saturation: finiteNumber(clip.saturation),
            volume: finiteNumber(clip.volume, 100),
        })),
        transitions: transitions.map(transition => ({
            id: transition.id,
            time: finiteNumber(transition.time),
            duration: finiteNumber(transition.duration, 1),
            type: ['dissolve', 'fade', 'wipe', 'zoom'].includes(transition.type)
                ? transition.type
                : 'dissolve',
        })),
        timeline: {
            pixelsPerSecond: finiteNumber(pixelsPerSecond, 50),
            trackStates: JSON.parse(JSON.stringify(trackStates)),
        },
    };
}

export function normalizeProject(raw) {
    if (!raw || typeof raw !== 'object') {
        throw new Error('Project file must contain a JSON object');
    }
    let source = raw;
    if (raw.schema !== PROJECT_SCHEMA) {
        if (!Array.isArray(raw.mediaItems) || !Array.isArray(raw.clips)) {
            throw new Error('This is not a ClipForge project file');
        }
        source = {
            schema: PROJECT_SCHEMA,
            version: PROJECT_SCHEMA_VERSION,
            name: 'Imported legacy project',
            media: raw.mediaItems.map(media => ({
                ...media,
                reference: {
                    name: media.name,
                    size: media.size || 0,
                    lastModified: media.lastModified || 0,
                    mime: media.type || '',
                },
            })),
            clips: raw.clips,
            transitions: raw.transitions || [],
            timeline: {pixelsPerSecond: 50, trackStates: {}},
        };
    }
    if (finiteNumber(source.version) > PROJECT_SCHEMA_VERSION) {
        throw new Error(`Project schema v${source.version} is newer than this editor supports`);
    }
    if (
        !Array.isArray(source.media)
        || !Array.isArray(source.clips)
        || !Array.isArray(source.transitions || [])
    ) {
        throw new Error('Project media, clips, and transitions must be arrays');
    }
    if (
        source.media.length > 5000
        || source.clips.length > 10000
        || source.transitions.length > 5000
    ) {
        throw new Error('Project exceeds the browser editor safety limits');
    }

    const canonicalIdMap = (items, prefix) => {
        const ids = new Map();
        items.forEach((item, index) => {
            if (!item || typeof item !== 'object') {
                throw new Error(`Project ${prefix} ${index + 1} must be an object`);
            }
            const sourceId = String(item.id ?? `${prefix}-source-${index + 1}`);
            if (ids.has(sourceId)) {
                throw new Error(`Project contains duplicate ${prefix} identifiers`);
            }
            ids.set(sourceId, `${prefix}-${index + 1}`);
        });
        return ids;
    };
    const mediaIdMap = canonicalIdMap(source.media, 'media');
    const clipIdMap = canonicalIdMap(source.clips, 'clip');
    const transitionIdMap = canonicalIdMap(source.transitions || [], 'transition');

    const media = source.media.map((item, index) => ({
        id: mediaIdMap.get(String(item.id ?? `media-source-${index + 1}`)),
        name: String(item.name || item.reference?.name || `Media ${index + 1}`).slice(0, 255),
        type: ['video', 'audio', 'image'].includes(item.type) ? item.type : 'video',
        duration: Math.max(0, finiteNumber(item.duration)),
        width: Math.max(0, finiteNumber(item.width)),
        height: Math.max(0, finiteNumber(item.height)),
        reference: {
            name: String(item.reference?.name || item.name || '').slice(0, 255),
            size: Math.max(0, finiteNumber(item.reference?.size)),
            lastModified: Math.max(0, finiteNumber(item.reference?.lastModified)),
            mime: String(item.reference?.mime || '').slice(0, 127),
            relativePath: String(item.reference?.relativePath || '').slice(0, 1024),
        },
        proxy: item.proxy && typeof item.proxy === 'object' ? {
            key: String(item.proxy.key || '').slice(0, 1024),
            profile: finiteNumber(item.proxy.profile),
            size: Math.max(0, finiteNumber(item.proxy.size)),
        } : null,
        file: null,
        url: null,
        proxyUrl: null,
        proxyKey: null,
        proxySize: 0,
        proxyActive: false,
        thumbnail: null,
        waveform: null,
        missing: true,
    }));
    const normalizedClips = source.clips.map((clip, index) => {
        const mediaId = mediaIdMap.get(String(clip.mediaId));
        if (!mediaId) {
            throw new Error(`Clip ${index + 1} references media that is not in the project`);
        }
        const linkedTo = clip.linkedTo == null
            ? null
            : clipIdMap.get(String(clip.linkedTo));
        if (clip.linkedTo != null && !linkedTo) {
            throw new Error(`Clip ${index + 1} links to a clip that is not in the project`);
        }
        return {
            id: clipIdMap.get(String(clip.id ?? `clip-source-${index + 1}`)),
            mediaId,
            track: ['video', 'audio', 'music'].includes(clip.track) ? clip.track : 'video',
            startTime: Math.max(0, finiteNumber(clip.startTime)),
            duration: Math.max(0, finiteNumber(clip.duration)),
            inPoint: Math.max(0, finiteNumber(clip.inPoint)),
            outPoint: Math.max(0, finiteNumber(clip.outPoint, finiteNumber(clip.duration))),
            name: String(clip.name || `Clip ${index + 1}`).slice(0, 255),
            type: ['video', 'audio', 'image'].includes(clip.type) ? clip.type : 'video',
            linkedTo,
            opacity: finiteNumber(clip.opacity, 100),
            scale: finiteNumber(clip.scale, 100),
            rotation: finiteNumber(clip.rotation),
            brightness: finiteNumber(clip.brightness),
            contrast: finiteNumber(clip.contrast),
            saturation: finiteNumber(clip.saturation),
            volume: finiteNumber(clip.volume, 100),
            thumbnail: null,
            waveform: null,
            url: null,
        };
    });
    return {
        schema: PROJECT_SCHEMA,
        version: PROJECT_SCHEMA_VERSION,
        name: String(source.name || 'Untitled Project').slice(0, 100),
        media,
        clips: normalizedClips,
        transitions: (source.transitions || []).map((transition, index) => ({
            id: transitionIdMap.get(String(transition.id ?? `transition-source-${index + 1}`)),
            time: Math.max(0, finiteNumber(transition.time)),
            duration: Math.max(0.01, finiteNumber(transition.duration, 1)),
            type: ['dissolve', 'fade', 'wipe', 'zoom'].includes(transition.type)
                ? transition.type
                : 'dissolve',
        })),
        timeline: {
            pixelsPerSecond: Math.min(
                200,
                Math.max(10, finiteNumber(source.timeline?.pixelsPerSecond, 50)),
            ),
            trackStates: source.timeline?.trackStates || {},
        },
    };
}
