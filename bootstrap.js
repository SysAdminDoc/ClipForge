const COI_RELOAD_KEY = 'clipforge-coi-reloaded-v2';

window.coiReady = (async () => {
    if (window.crossOriginIsolated) {
        sessionStorage.removeItem(COI_RELOAD_KEY);
        return true;
    }
    if (!('serviceWorker' in navigator)) {
        return false;
    }
    try {
        const registration = await navigator.serviceWorker.register(
            './coi-serviceworker.js',
            { updateViaCache: 'none' },
        );
        await registration.update();
        await navigator.serviceWorker.ready;
        if (!sessionStorage.getItem(COI_RELOAD_KEY)) {
            sessionStorage.setItem(COI_RELOAD_KEY, '1');
            window.location.reload();
            return new Promise(() => {});
        }
    } catch (error) {
        console.error('ClipForge service worker setup failed:', error);
    }
    sessionStorage.removeItem(COI_RELOAD_KEY);
    return window.crossOriginIsolated;
})();
