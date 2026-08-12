export function proxyFingerprintMatches(left, right) {
    return Boolean(
        left
        && right
        && left.name === right.name
        && Number(left.size) === Number(right.size)
        && Number(left.lastModified) === Number(right.lastModified)
        && left.sampleBytes === right.sampleBytes
        && left.sampleSha256 === right.sampleSha256,
    );
}

export function proxyKeyFromFingerprint(fingerprint, profile = 2) {
    return [
        `v${profile}`,
        fingerprint.size,
        fingerprint.lastModified,
        fingerprint.sampleSha256,
        encodeURIComponent(fingerprint.name),
    ].join(':');
}

export function proxyRecordSize(record) {
    const size = Number(record?.size ?? record?.blob?.size ?? 0);
    return Number.isFinite(size) && size > 0 ? size : 0;
}

export function proxyRecordIsComplete(record, profile = 2, BlobType = Blob) {
    return Boolean(
        record
        && record.profile === profile
        && record.complete === true
        && typeof record.key === 'string'
        && record.blob instanceof BlobType
        && proxyRecordSize(record) === record.blob.size
        && record.blob.size > 0,
    );
}

export function proxyRecordIsValid(record, source, profile = 2, BlobType = Blob) {
    return Boolean(
        proxyRecordIsComplete(record, profile, BlobType)
        && proxyFingerprintMatches(record.source, source),
    );
}
