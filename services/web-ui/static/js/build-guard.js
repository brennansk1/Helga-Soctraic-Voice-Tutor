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
        // The pipeline's own last words, success and failure. Nothing else says
        // a build is over: there is no server event for it (the 'course_ready'
        // listener that used to live here was never emitted by anything), so
        // without these the lock could only ever expire on the 4h timer.
        // Anchored, and to terminal failures only. The test used to include a
        // bare /failed|FAILED/, which matches mid-build warnings the builder
        // emits routinely ("…after failed attempt", "STRUCT:WARN:…failed") and
        // dropped the lock while the build was still running. A failure this
        // list misses is still caught by the reconcile below.
        if (/^Course built successfully/i.test(msg)) clear();
        if (/^(BOOK:UNREADABLE|ERROR:|Error creating course|Skeleton generation failed|CHECK:PREFLIGHT:FAIL)/
                .test(msg)) clear();
    }

    if (window.io) {
        try {
            var socket = window.io();
            socket.on("status_update", function (d) {
                observe(d && (d.message || d.msg || d));
            });
        } catch (e) { /* no socket on this page: the pill just reads storage */ }
    }

    /* --- ask the server whether the build is over ----------------------------
       The status stream reaches only a browser that is open at the moment a
       message is sent. Miss the last one \u2014 close the tab, drop the socket, let
       core restart mid-build \u2014 and the lock survived until MAX_AGE_MS: the
       create page refused to build anything for four hours because of a build
       that ended in minute three. The server knows; ask it.

       ONLY POSITIVE EVIDENCE OF AN ENDING RELEASES THE LOCK. Neither endpoint
       can say "nothing is running" reliably, and each is wrong in a different
       direction, so a bare negative is read as "do not know" and the lock stays:

       * /api/creation_status is the pipeline's own phase, and it goes
         complete/error exactly once per build. But web-ui proxies it without a
         student_id, so a multi-student install answers about the legacy FSM;
         a null phase there means "asked the wrong one", not "no build".
       * /api/build/status is the durable, student-agnostic record, but the
         builder marks it finished when the SKELETON is done, minutes before
         hydration ends \u2014 so `active: false` there is not an ending either. Its
         `stale` flag is real evidence: the server itself calls that build dead
         after 15 minutes of silence, which is the crashed-build case.

       Hence: ended = (pipeline says complete/error, and no build is recording
       progress) or (the durable record has gone stale). A proxy failure carries
       an `error` field or an HTTP status and is ignored outright.

       A FRESH LOCK IS NEVER CLEARED: creation_status keeps the PREVIOUS build's
       phase until the new pipeline thread overwrites it, a second or two after
       /api/event returns. Inside the grace window we believe the lock we just
       armed, not the phase left over from last time. */
    var GRACE_MS = 60 * 1000;
    var reconcileTimer = null;

    function getJson(url) {
        return fetch(url)
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; });
    }

    /* Shared verdict, so the build page and the nav pill can never disagree
       about whether a build is still running. Calls back with one of
       "running" | "ended" | "unknown", plus the course uid when it has one. */
    function probe(cb) {
        Promise.all([getJson("/api/creation_status"), getJson("/api/build/status")])
            .then(function (r) {
                var c = r[0], b = r[1];
                var recording = !!(b && b.active);
                if (b && b.stale) return cb("ended", c && c.course_uid);
                if (c && !c.error && (c.phase === "complete" || c.phase === "error")
                        && !recording) {
                    return cb("ended", c.course_uid, c.phase);
                }
                if (recording || (c && !c.error && c.active)) return cb("running");
                cb("unknown");
            });
    }

    function reconcile() {
        var v = get();
        if (!v) {                        // nothing locked: nothing to reconcile
            if (reconcileTimer) { clearInterval(reconcileTimer); reconcileTimer = null; }
            return;
        }
        if (Date.now() - v.started < GRACE_MS) return;
        probe(function (verdict) {
            if (verdict === "ended") clear();
            else if (verdict === "running") set();
        });
    }

    if (window.fetch) {
        reconcile();
        reconcileTimer = setInterval(reconcile, 30000);
    }

    // Cross-tab: another tab's build shows here too.
    window.addEventListener("storage", function (e) {
        if (e.key === KEY) paint();
    });

    paint();

    // --- Continue (resume last session) ---------------------------------
    // learn.html records the last opened course; this surfaces it everywhere.
    // Reads at paint time, so a new session in another tab appears on the
    // next navigation rather than requiring a reload dance.
    try {
        var last = JSON.parse(localStorage.getItem("helga_last_course") || "null");
        var cp = document.getElementById("continue-pill");
        if (cp && last && last.uid) {
            cp.href = "/learn?course_uid=" + encodeURIComponent(last.uid);
            cp.classList.remove("hidden");
            var cl = document.getElementById("continue-label");
            if (cl && last.title) cl.textContent = "Continue: " +
                (last.title.length > 24 ? last.title.slice(0, 23) + "\u2026" : last.title);
        }
    } catch (e) {}

    window.HelgaBuildGuard = { active: get, set: set, clear: clear, probe: probe };
})();
