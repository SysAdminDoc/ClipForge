/*! Derived from coi-serviceworker v0.1.7 (MIT); ClipForge cache extensions. */
const CACHE_NAME = 'clipforge-browser-runtime-v1';
const STATIC_ASSETS = [
    './',
    './index.html',
    './favicon.svg',
    './bootstrap.js',
    './editor.js',
    './coi-serviceworker.js',
    './vendor/ffmpeg/sbom.json',
    './vendor/ffmpeg/ffmpeg/index.js',
    './vendor/ffmpeg/ffmpeg/worker.js',
    './vendor/ffmpeg/ffmpeg/classes.js',
    './vendor/ffmpeg/ffmpeg/const.js',
    './vendor/ffmpeg/ffmpeg/errors.js',
    './vendor/ffmpeg/ffmpeg/types.js',
    './vendor/ffmpeg/ffmpeg/utils.js',
    './vendor/ffmpeg/util/index.js',
    './vendor/ffmpeg/util/const.js',
    './vendor/ffmpeg/util/errors.js',
    './vendor/ffmpeg/util/types.js',
    './vendor/ffmpeg/core/ffmpeg-core.js',
    './vendor/ffmpeg/core/ffmpeg-core.wasm',
];

function isolatedResponse(response) {
    if (!response || response.status === 0) {
        return response;
    }
    const headers = new Headers(response.headers);
    headers.set('Cross-Origin-Embedder-Policy', 'require-corp');
    headers.set('Cross-Origin-Opener-Policy', 'same-origin');
    headers.set('Cross-Origin-Resource-Policy', 'same-origin');
    return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers,
    });
}

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(STATIC_ASSETS))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const request = event.request;
    if (
        request.method !== 'GET'
        || (request.cache === 'only-if-cached' && request.mode !== 'same-origin')
    ) {
        return;
    }
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) {
        return;
    }
    event.respondWith((async () => {
        const cache = await caches.open(CACHE_NAME);
        try {
            const response = await fetch(request);
            if (response.ok) {
                await cache.put(request, response.clone());
            }
            return isolatedResponse(response);
        } catch (error) {
            const cached = await cache.match(request, {
                ignoreSearch: request.mode === 'navigate',
            });
            if (cached) {
                return isolatedResponse(cached);
            }
            if (request.mode === 'navigate') {
                const fallback = await cache.match('./index.html');
                if (fallback) return isolatedResponse(fallback);
            }
            throw error;
        }
    })());
});

self.addEventListener('message', event => {
    if (event.data?.type === 'deregister') {
        event.waitUntil(
            self.registration.unregister()
                .then(() => self.clients.matchAll())
                .then(clients => Promise.all(
                    clients.map(client => client.navigate(client.url))
                ))
        );
    }
});
