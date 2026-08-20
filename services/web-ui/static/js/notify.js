/* The header bell, and the Notebook pill beside Continue.
 *
 * The bell has two sources, because the obvious one turned out to be dry:
 *
 *   1. /api/notifications is real (NotificationStore, schema v8) but nearly
 *      every row it ever gets is addressed to a PARENT — payment failures,
 *      struggle alerts, inactivity nudges. A solo adult session has no
 *      student_id/parent_id in the Flask session, so the endpoint answers
 *      {notifications: [], unread: 0} forever. Real handlers, no rows.
 *   2. /api/due_concepts — the same endpoint the Practice page reads — is
 *      what actually has news for a Mode A learner: reviews that came due.
 *
 * So the bell polls both and shows: one synthesized "reviews due" row driven
 * by (2), plus any genuine rows from (1), so the day the backend grows a
 * learner-facing notification it appears here without a UI change.
 *
 * Polling mirrors resources.js: 20s, paused while the tab is hidden, resumed
 * with a fresh reading on visibility. All strings rendered via textContent.
 */
(function () {
    "use strict";

    var POLL_MS = 20000;
    var timer = null;
    var open = false;

    var state = {
        dueCount: 0,
        dueFailed: false,
        notifications: [],
        unread: 0,
    };

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    /* ----------------------------------------------------- notebook pill */
    // learn.html records the last opened course (same key the Continue pill
    // reads in build-guard.js); the notebook is per-course, so the pill deep
    // links straight into that course's pages.
    function paintNotebookPill() {
        var pill = document.getElementById("notebook-pill");
        if (!pill) return;
        try {
            var last = JSON.parse(
                localStorage.getItem("helga_last_course") || "null");
            if (last && last.uid) {
                pill.href = "/notebook?course_uid=" +
                    encodeURIComponent(last.uid);
                pill.classList.remove("hidden");
            } else {
                // No session yet — the picker route still works.
                pill.href = "/notebook";
            }
        } catch (e) { /* localStorage disabled: pill stays as rendered */ }
    }

    /* -------------------------------------------------------------- bell */
    function fetchJson(url) {
        return fetch(url).then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        });
    }

    function poll() {
        var due = fetchJson("/api/due_concepts").then(function (d) {
            state.dueCount = ((d && d.concepts) || []).length;
            state.dueFailed = false;
        }).catch(function () {
            // An unreachable review service is unknown, not zero; keep the
            // last known count rather than clearing the badge.
            state.dueFailed = true;
        });
        var ntf = fetchJson("/api/notifications").then(function (d) {
            state.notifications = ((d && d.notifications) || []).slice(0, 10);
            state.unread = (d && d.unread) || 0;
        }).catch(function () { /* leave the previous reading standing */ });
        Promise.all([due, ntf]).then(function () {
            paintBadge();
            if (open) renderDropdown();
        });
    }

    function badgeCount() {
        return state.unread + (state.dueCount > 0 ? 1 : 0);
    }

    function paintBadge() {
        var badge = document.getElementById("notify-count");
        if (!badge) return;
        var n = badgeCount();
        badge.textContent = n > 9 ? "9+" : String(n);
        badge.hidden = n === 0;
        var btn = document.getElementById("notify-bell");
        if (btn) {
            btn.setAttribute("aria-label", n === 0
                ? "Notifications — nothing new"
                : "Notifications — " + n + " new");
        }
    }

    function renderDropdown() {
        var box = document.getElementById("notify-dropdown");
        if (!box) return;
        while (box.firstChild) box.removeChild(box.firstChild);

        box.appendChild(el("h2", "notify-heading", "Notifications"));

        var any = false;

        if (state.dueCount > 0) {
            any = true;
            var row = el("a", "notify-item notify-item-due");
            row.href = "/practice";
            row.appendChild(el("span", "notify-item-title",
                state.dueCount === 1
                    ? "1 review is due"
                    : state.dueCount + " reviews are due"));
            row.appendChild(el("span", "notify-item-body",
                "Short retrieval now beats rereading later."));
            box.appendChild(row);
        }

        state.notifications.forEach(function (n) {
            any = true;
            var row = el("div", "notify-item" +
                (n.read_at ? " notify-item-read" : ""));
            row.appendChild(el("span", "notify-item-title",
                n.title || n.kind || "Notification"));
            if (n.body) row.appendChild(el("span", "notify-item-body", n.body));
            if (n.created_at) {
                row.appendChild(el("span", "notify-item-ts",
                    String(n.created_at).replace("T", " ").slice(0, 16)));
            }
            if (!n.read_at && n.id) {
                var mark = el("button", "notify-mark-read", "Mark read");
                mark.type = "button";
                mark.addEventListener("click", function (ev) {
                    ev.stopPropagation();
                    // CSRF header comes from base.html's global fetch wrapper.
                    fetch("/api/notifications/" +
                        encodeURIComponent(n.id) + "/read", { method: "POST" })
                        .then(function () { poll(); })
                        .catch(function () { /* next poll retries the truth */ });
                });
                row.appendChild(mark);
            }
            box.appendChild(row);
        });

        if (!any) {
            var msg = state.dueFailed
                ? "Helga could not reach the review service, so this may " +
                  "not mean nothing is due."
                : "Nothing new. Reviews appear here when they come due.";
            box.appendChild(el("p", "notify-empty", msg));
        }
    }

    function toggle(openWanted) {
        var box = document.getElementById("notify-dropdown");
        var btn = document.getElementById("notify-bell");
        if (!box || !btn) return;
        open = openWanted != null ? openWanted : !open;
        box.hidden = !open;
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) renderDropdown();
    }

    function start() {
        if (timer) return;
        poll();
        timer = setInterval(poll, POLL_MS);
    }

    // Stop polling while the tab is hidden; resume with a fresh reading.
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) {
            if (timer) { clearInterval(timer); timer = null; }
        } else {
            start();
        }
    });

    document.addEventListener("DOMContentLoaded", function () {
        paintNotebookPill();

        var btn = document.getElementById("notify-bell");
        if (!btn) return;
        btn.addEventListener("click", function (ev) {
            ev.stopPropagation();
            toggle();
        });
        document.addEventListener("click", function (ev) {
            var box = document.getElementById("notify-dropdown");
            if (open && box && !box.contains(ev.target)) toggle(false);
        });
        document.addEventListener("keydown", function (ev) {
            if (ev.key === "Escape" && open) { toggle(false); btn.focus(); }
        });

        start();
    });
})();
