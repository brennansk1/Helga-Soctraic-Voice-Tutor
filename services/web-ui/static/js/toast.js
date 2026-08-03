function showToast(message, type = 'error') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    // Show
    setTimeout(() => toast.classList.add('show'), 10);
    // Hide after 5s
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => {
            if (container.contains(toast)) container.removeChild(toast);
        }, 300);
    }, 5000);
}

// UX-3: Global fetch wrapper with automatic error toast
async function safeFetch(url, options = {}) {
    try {
        const res = await fetch(url, options);
        if (!res.ok && res.status >= 500) {
            // Show the learner something they can act on, not our status code
            // and internal path. The detail stays in the console.
            console.error(`[safeFetch] ${res.status} ${res.statusText} — ${url}`);
            showToast('Something went wrong on our side. Your work is saved — ' +
                      'try that again in a moment.', 'error');
        }
        return res;
    } catch (e) {
        console.error(`[safeFetch] network failure — ${url}`, e);
        showToast("Can't reach Helga right now. Check that the services are " +
                  'running, then try again.', 'error');
        throw e;
    }
}

// Safety net for uncaught promise rejections
window.addEventListener('unhandledrejection', (event) => {
    if (event.reason && event.reason.message) {
        console.error('Unhandled rejection:', event.reason);
    }
});