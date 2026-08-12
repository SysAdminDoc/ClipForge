const DEFAULT_LOCALE = 'en';
const PROOF_LOCALE = 'en-XA';

// Stable ids make HTML attributes and runtime messages reviewable by translators.
const MESSAGES = Object.freeze({
    appName: 'ClipForge',
    openProject: 'Open project',
    edit: 'Edit',
    editActions: 'Edit actions',
    undo: 'Undo',
    redo: 'Redo',
    cut: 'Cut',
    copy: 'Copy',
    paste: 'Paste',
    selectAll: 'Select all',
    delete: 'Delete',
    diagnostics: 'Diagnostics',
    loading: 'Loading...',
    ready: 'Ready',
    engineUnavailable: 'Engine unavailable',
    cancelJob: 'Cancel job',
    open: 'Open',
    save: 'Save',
    export: 'Export',
    project: 'Project',
    import: 'Import',
    importMediaFiles: 'Import media files',
    dropFiles: 'Drop files here or click to browse',
    mediaFiles: 'Video, audio, and image files',
    recoverableProject: 'A recoverable browser project is available.',
    recover: 'Recover',
    discard: 'Discard',
    relinkHint: 'Project files store media references, not media bytes. Relink moved or reopened local sources here.',
    relink: 'Relink',
    purge: 'Purge',
    media: 'Media',
    effects: 'Effects',
    noMedia: 'No media imported',
    previewHint: 'Drop media files here or use the import panel to get started',
    selectionTool: 'Selection tool',
    razorTool: 'Razor tool',
    slipTool: 'Slip tool',
    handTool: 'Hand tool',
    split: 'Split',
    dissolve: 'Dissolve',
    timelineZoom: 'Timeline zoom',
    properties: 'Properties',
    clip: 'Clip',
    transitions: 'Transitions',
    audio: 'Audio',
    noClip: 'No Clip Selected',
    selectClip: 'Select a clip on the timeline to view its properties',
    videoEffects: 'Video Effects',
    colorCorrection: 'Color Correction',
    opacity: 'Opacity',
    scale: 'Scale',
    rotation: 'Rotation',
    brightness: 'Brightness',
    contrast: 'Contrast',
    saturation: 'Saturation',
    exportVideo: 'Export video',
    closeExport: 'Close export dialog',
    cancel: 'Cancel',
    cinematicContrast: 'Cinematic contrast',
    brighten: 'Brighten',
    monochrome: 'Monochrome',
    resetColor: 'Reset color',
    goToStart: 'Go to start',
    stepBack: 'Step back',
    stepBackward: 'Step backward',
    playPreview: 'Play preview',
    stepForward: 'Step forward',
    goToEnd: 'Go to end',
    mutePreview: 'Mute preview audio',
    previewVolume: 'Preview volume',
    selectionShortcut: 'Selection (V)',
    razorShortcut: 'Razor (C)',
    slipShortcut: 'Slip (Y)',
    handShortcut: 'Hand (H)',
    splitShortcut: 'Split at playhead (S)',
    deleteShortcut: 'Delete (Del)',
    addDissolve: 'Add dissolve',
    mediaJob: 'Media job',
    restartingAfterCancel: 'Restarting FFmpeg after {job} cancellation...',
    cancellingExport: 'Cancelling export...',
    loadingFfmpegModules: 'Loading FFmpeg modules...',
    loadingFfmpegCore: 'Loading local FFmpeg core (~31 MB)...',
    initializingFfmpeg: 'Initializing FFmpeg...',
    clipforgeReady: 'ClipForge ready!',
    failedLoadEngine: 'Failed to load FFmpeg engine',
    retry: 'Retry',
    importedFiles: 'Imported {count} file(s)',
    importFailed: 'Could not import {name}: {error}',
    proxyCacheUnavailable: 'Proxy cache unavailable: {error}',
    purgeActiveProxy: 'Finish or cancel the active proxy job before purging its cache',
    purgedProxyRecords: 'Purged {count} browser proxy record(s)',
    purgeProxyFailed: 'Could not purge browser proxy cache: {error}',
    ffmpegNotReady: 'The FFmpeg engine is not ready',
    clipSplit: 'Clip split',
    deletedClips: 'Deleted selected clips',
    copiedClipboard: 'Copied to clipboard',
    pastedClips: 'Pasted clips',
    exportingVideo: 'Exporting video...',
    joiningClips: 'Joining clips...',
});

const PLACEHOLDER_PATTERN = /\{([A-Za-z_][A-Za-z0-9_.-]*)\}/g;

function normalizeLocale(locale) {
    const value = String(locale || DEFAULT_LOCALE).trim().replaceAll('_', '-').toLowerCase();
    if (!value || value === 'en' || value === 'en-us' || value === 'en-gb') return DEFAULT_LOCALE;
    if (value === 'pseudo' || value === 'proof' || value === 'en-xa') return PROOF_LOCALE;
    return value;
}

function placeholders(template) {
    return new Set([...String(template).matchAll(PLACEHOLDER_PATTERN)].map(match => match[1]));
}

function pseudoLocalize(template) {
    const placeholdersByToken = [];
    const protectedTemplate = String(template).replace(PLACEHOLDER_PATTERN, match => {
        const token = `\u0000${placeholdersByToken.length}\u0000`;
        placeholdersByToken.push(match);
        return token;
    });
    const expanded = protectedTemplate.replace(/[A-Za-z]/g, char => `${char}${char.toLowerCase()}`);
    return `⟦${expanded.replace(/\u0000(\d+)\u0000/g, (_match, index) => placeholdersByToken[Number(index)])}⟧`;
}

function formatMessage(template, values, key) {
    const expected = placeholders(template);
    const actual = new Set(Object.keys(values || {}));
    const missing = [...expected].filter(name => !actual.has(name));
    const extra = [...actual].filter(name => !expected.has(name));
    if (missing.length || extra.length) {
        throw new TypeError(`Invalid placeholders for ${key}: missing=${missing.join(',')} extra=${extra.join(',')}`);
    }
    return String(template).replace(PLACEHOLDER_PATTERN, (_match, name) => String(values[name]));
}

export function createBrowserI18n(locale = globalThis.CLIPFORGE_LOCALE || DEFAULT_LOCALE) {
    const normalized = normalizeLocale(locale);
    return Object.freeze({
        locale: normalized,
        messages: MESSAGES,
        t(key, values = {}) {
            const source = Object.prototype.hasOwnProperty.call(MESSAGES, key) ? MESSAGES[key] : String(key);
            const template = normalized === PROOF_LOCALE ? pseudoLocalize(source) : source;
            return formatMessage(template, values, key);
        },
    });
}

export function validateBrowserCatalog() {
    for (const [key, source] of Object.entries(MESSAGES)) {
        const proof = pseudoLocalize(source);
        const expected = [...placeholders(source)].sort();
        const actual = [...placeholders(proof)].sort();
        if (JSON.stringify(expected) !== JSON.stringify(actual)) {
            throw new TypeError(`Proof locale changed placeholders for ${key}`);
        }
    }
    return true;
}

export function applyBrowserTranslations(root = globalThis.document, i18n = createBrowserI18n()) {
    if (!root?.querySelectorAll) return i18n;
    const translate = (selector, attribute, setter) => {
        for (const element of root.querySelectorAll(selector)) {
            const key = element.getAttribute(attribute);
            if (key) setter(element, i18n.t(key));
        }
    };
    translate('[data-i18n]', 'data-i18n', (element, value) => { element.textContent = value; });
    translate('[data-i18n-aria-label]', 'data-i18n-aria-label', (element, value) => { element.setAttribute('aria-label', value); });
    translate('[data-i18n-title]', 'data-i18n-title', (element, value) => { element.setAttribute('title', value); });
    translate('[data-i18n-placeholder]', 'data-i18n-placeholder', (element, value) => { element.setAttribute('placeholder', value); });
    if (root.documentElement) {
        root.documentElement.lang = i18n.locale === PROOF_LOCALE ? 'en' : i18n.locale;
        root.documentElement.dataset.locale = i18n.locale;
    }
    return i18n;
}

export { DEFAULT_LOCALE, MESSAGES, PROOF_LOCALE, normalizeLocale, pseudoLocalize };
