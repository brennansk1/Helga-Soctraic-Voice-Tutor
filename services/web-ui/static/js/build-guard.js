/* Build guard — one build at a time, never lost by navigation.
 *
 * Three jobs, all client-visible versions of server truths:
 *   1. THE PILL: while a build runs, a pulsing "Building..." pill sits in the
 *      nav of every page and returns to /build. Clicking away loses nothing.
 *   2. THE LOCK: the create page checks the flag and routes to /build instead
 *      of offering a second build — the hardware runs one model, so a second
 *      build would queue behind the first and look like a hang.
 *   3. HONEST EXPIRY: the flag carries a timestamp and expires after 4 hours,
 *      because a stale "building" lock that outlives a crashed build would
 *      wall off course creation forever.
 *
 * State lives in localStorage (build_active, build_started, build_label) and
 * is refreshed by the same Socket.IO status stream the build view consumes.
 */
(function () {
    "use strict";
    var KEY = "helga_build_active";
    var MAX_AGE_MS = 4 * 60 * 60 * 1000;

    function get() {
        try {
            var raw = localStorage.getItem(KEY);
            if (!raw) return null;
            var v = JSON.parse(raw);
            if (!v.started || Date.now() - v.started > MAX_AGE_MS) {
                localStorage.removeItem(KEY);
                return null;
            }
            return v;
        } catch (e) { return null; }
    }
    function set(label) {
        var cur = get();
        try {
            localStorage.setItem(KEY, JSON.stringify({
                started: (cur && cur.started) || Date.now(),
                label: label || (cur && cur.label) || "Building\u2026",
            }));
        } catch (e) {}
        paint();
    }
    function clear() {
        try { localStorage.removeItem(KEY); } catch (e) {}
        paint();
    }

    function paint() {
        var pill = document.getElementById("build-pill");
        if (!pill) return;
        var v = get();
        pill.classList.toggle("hidden", !v);
        if (v) {
            var lbl = document.getElementById("build-pill-label");
            if (lbl) lbl.textContent = v.label;
        }
    }

    // Any build-vocabulary status marks a build active; completion clears it.
    function observe(msg) {
        msg = String(msg == null ? "" : msg);
        if (/^(BOOK:|STRUCT:|RESEARCH:|ASSET:|CHECK:)/.test(msg)) {
            var m = msg.match(/^BOOK:READING:(\d+):(\d+)/);
            set(m ? "Building \u2014 chapter " + m[1] + " of " + m[2]
                  : undefined);
        }
        if (/^BOOK:UNREADABLE|failed|FAILED/.test(msg)) clear();
    }

    if (window.io) {
        try {
            var socket = window.io();
            socket.on("status_update", function (d) {
                observe(d && (d.message || d.msg || d));
            });
            socket.on("course_ready", function () { clear(); });
        } catch (e) { /* no socket on this page: the pill just reads storage */ }
    }

    // Cross-tab: another tab's build shows here too.
    window.addEventListener("storage", function (e) {
        if (e.key === KEY) paint();
    });

    paint();

    window.HelgaBuildGuard = { active: get, set: set, clear: clear };
})();
