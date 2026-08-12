const SECRET_KEY_PATTERN = /(?:password|passwd|token|secret|api[_-]?key|authorization|credential|cookie|signature|session)/i;
const PRIVATE_KEY_PATTERN = /^(?:file(?:name|path)?|local_path|media(?:_name|_path)?|private_metadata|title|artist|album|comment|location|tags)$/i;
const URL_PATTERN = /\b(?:https?|ftp):\/\/[^\s"'<>]+/gi;
const PATH_PATTERN = /(?<![\w])(?:[a-z]:\\|\\\\)[^\r\n"']+|(?<![\w:/])\/(?:[^\/\r\n"']+\/)+[^\/\r\n"']*/gi;

function redactUrlMatch(match) {
    let raw = match;
    let trailing = '';
    while (raw && '.,;:)]}>'.includes(raw.at(-1))) {
        trailing = raw.at(-1) + trailing;
        raw = raw.slice(0, -1);
    }
    try {
        const url = new URL(raw);
        if (url.username || url.password) {
            url.username = '<redacted-secret>';
            url.password = '<redacted-secret>';
        }
        for (const key of [...url.searchParams.keys()]) {
            if (SECRET_KEY_PATTERN.test(key)) {
                url.searchParams.set(key, '<redacted-secret>');
            }
        }
        if (url.hash) url.hash = '#<redacted-secret>';
        return url.toString() + trailing;
    } catch (_error) {
        return '<redacted-url>' + trailing;
    }
}

export function redactBrowserText(value) {
    return String(value)
        .replace(URL_PATTERN, redactUrlMatch)
        .replace(PATH_PATTERN, '<redacted-path>');
}

export function redactBrowserValue(value, key = '') {
    const keyText = String(key);
    const controlKey = /(?:redacted|included)$/i.test(keyText);
    if (!controlKey && SECRET_KEY_PATTERN.test(keyText)) return '<redacted-secret>';
    if (!controlKey && PRIVATE_KEY_PATTERN.test(keyText)) return '<redacted-private-metadata>';
    if (typeof value === 'string') return redactBrowserText(value);
    if (Array.isArray(value)) return value.map(item => redactBrowserValue(item));
    if (value && typeof value === 'object') {
        return Object.fromEntries(
            Object.entries(value).map(([itemKey, item]) => [
                itemKey,
                redactBrowserValue(item, itemKey),
            ]),
        );
    }
    return value;
}
