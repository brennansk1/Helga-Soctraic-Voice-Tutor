/* First-run setup — the page that turns a downloaded repository into a working
 * tutor.
 *
 * The behaviour is deliberately borrowed from resources.js rather than
 * reinvented, because the two surfaces describe the same machine and a user
 * who sees them disagree stops believing either:
 *
 *   - No dismiss control anywhere. A step is a fact about the installation,
 *     not a message to acknowledge away. It clears when the machine changes.
 *   - It re-measures on a timer and shows a COUNTER, never a bare spinner:
 *     every wait here is a wait on a person typing something in a terminal,
 *     and how long that has been is the only honest thing to display.
 *   - A failure to measure is rendered as "not measured", never as a pass.
 *     An unreachable status endpoint leaves the page saying so.
 *
 * Everything rendered here comes from the server or from a subprocess Ollama
 * ran, so every value goes in through textContent and every node through
 * createElement. There is no innerHTML in this file on purpose.
 */
(function () {
    "use strict";

    /* Cadence. Blocked means the user is standing at a terminal fixing
       something and wants to see it clear; ready means eight HTTP probes for
       nothing, so it slows right down. */
    var POLL_BLOCKED_MS = 10000;
    var POLL_READY_MS = 60000;
    var POLL_PULLING_MS = 30000;

    var STATE_WORDS = {
        ok: "Done",
        degraded: "Worth fixing",
        blocked: "Needs you",
        unknown: "Not measured"
    };

    var statusTimer = null;
    var nextCheckAt = 0;
    var checkStartedAt = 0;
    var checking = false;
    var last = null;         // most recent status report
    var pull = null;         // most recent pull record (SSE or status)
    var pullSource = null;   // live EventSource, if any
    var pullPoll = null;     // fallback interval when EventSource is unusable

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) { e.className = cls; }
        if (text != null) { e.textContent = text; }
        return e;
    }

    function csrf() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return (m && m.content) || "";
    }

    /* One decimal, matching the rest of the product. "1.4 GB" is a decision;
       "1.43829 GB" is arithmetic the reader has to do themselves. */
    function bytes(n) {
        if (n == null || isNaN(n)) { return "—"; }
        var u = ["B", "KB", "MB", "GB", "TB"], i = 0, v = Number(n);
        while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
        return (i === 0 ? Math.round(v) : v.toFixed(1)) + " " + u[i];
    }

    function duration(s) {
        if (s == null || isNaN(s) || s < 0) { return null; }
        s = Math.round(s);
        if (s < 60) { return s + "s"; }
        var m = Math.floor(s / 60);
        if (m < 60) { return m + "m " + (s % 60) + "s"; }
        return Math.floor(m / 60) + "h " + (m % 60) + "m";
    }

    /* ------------------------------------------------------------- pieces */

    function pill(state) {
        var s = state || "unknown";
        return el("span", "setup-pill is-" + s, STATE_WORDS[s] || s);
    }

    /* The commands are the point of the whole page for any step that cannot be
       fixed from here, so they get the weight of a code block and a copy
       button rather than being buried in a sentence. */
    function commandBlock(commands) {
        if (!commands || !commands.length) { return null; }
        var box = el("div", "setup-cmds");
        var pre = el("pre", "setup-cmd");
        pre.appendChild(el("code", null, commands.join("\n")));
        box.appendChild(pre);

        var copy = el("button", "setup-copy", "Copy");
        copy.type = "button";
        copy.addEventListener("click", function () {
            var text = commands.join("\n");
            var done = function () {
                copy.textContent = "Copied";
                setTimeout(function () { copy.textContent = "Copy"; }, 1500);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(done, function () {
                    // Clipboard access can be refused outright (insecure
                    // origin, denied permission). Selecting the text is a real
                    // fallback; a silently dead button is not.
                    selectNode(pre);
                    copy.textContent = "Press ⌘C";
                });
            } else {
                selectNode(pre);
                copy.textContent = "Press ⌘C";
            }
        });
        box.appendChild(copy);
        return box;
    }

    function selectNode(node) {
        try {
            var range = document.createRange();
            range.selectNodeContents(node);
            var sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
        } catch (e) { /* selection is a nicety; never worth an exception */ }
    }

    function subList(sub) {
        if (!sub || !sub.length) { return null; }
        var ul = el("ul", "setup-sub");
        sub.forEach(function (s) {
            var li = el("li", "setup-sub-item is-" + (s.state || "unknown"));
            li.appendChild(el("span", "setup-sub-dot"));
            var body = el("div", "setup-sub-body");
            body.appendChild(el("span", "setup-sub-label", s.label || ""));
            if (s.reason) { body.appendChild(el("span", "setup-sub-reason", s.reason)); }
            if (s.remedy) { body.appendChild(el("span", "setup-sub-remedy", s.remedy)); }
            li.appendChild(body);
            ul.appendChild(li);
        });
        return ul;
    }

    /* ---------------------------------------------------------- the pull */

    /* A 12.7 GB download is the reason the house rule about spinners exists.
       This shows bytes, a percentage, a rate and an estimate, all of them the
       server's own figures — including the fact that the percentage can move
       backwards as Ollama announces further layers, which is what actually
       happened rather than a number we smoothed. */
    function pullBlock(p) {
        var box = el("div", "setup-pull is-" + (p.state || "idle"));

        if (p.state === "error") {
            box.appendChild(el("p", "setup-pull-error",
                "The download failed. " + (p.error || "No reason was given.")));
            box.appendChild(pullButton("Try the download again"));
            return box;
        }
        if (p.state === "done") {
            box.appendChild(el("p", "setup-pull-done",
                "Download finished. Re-checking…"));
            return box;
        }

        var pct = (typeof p.percent === "number") ? p.percent : null;
        var track = el("div", "setup-pull-track");
        var fill = el("span", "setup-pull-fill");
        // With no percentage yet we still refuse a bare spinner: the bar sits
        // at zero and the line below counts the seconds elapsed, which says
        // more than an animation ever does.
        fill.style.width = (pct == null ? 0 : Math.max(0, Math.min(100, pct))) + "%";
        track.appendChild(fill);
        box.appendChild(track);

        var bits = [];
        if (pct != null) { bits.push(pct.toFixed(1) + "%"); }
        if (p.total) { bits.push(bytes(p.completed) + " of " + bytes(p.total)); }
        else if (p.completed) { bits.push(bytes(p.completed)); }
        if (p.bytes_per_sec) { bits.push(bytes(p.bytes_per_sec) + "/s"); }
        var eta = duration(p.eta_seconds);
        if (eta) { bits.push(eta + " left"); }
        if (!bits.length && p.started_at) {
            bits.push(duration((Date.now() / 1000) - p.started_at) + " elapsed");
        }
        box.appendChild(el("p", "setup-pull-figures", bits.join("  ·  ")));

        // Ollama's own phase string, verbatim. Paraphrasing it would drift
        // from what the server is actually doing.
        box.appendChild(el("p", "setup-pull-status",
            p.status ? ("Ollama: " + p.status) : "Waiting for Ollama…"));
        box.appendChild(el("p", "setup-pull-note",
            "This keeps going if you close the tab, and resumes where it "
            + "stopped if the connection drops."));
        return box;
    }

    function pullButton(label) {
        var b = el("button", "setup-btn setup-btn-primary", label);
        b.type = "button";
        b.addEventListener("click", function () {
            b.disabled = true;
            b.textContent = "Starting…";
            startPull(b);
        });
        return b;
    }

    function startPull(button) {
        fetch("/api/setup/model/pull", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf()
            },
            body: "{}"
        }).then(function (r) {
            return r.json().then(function (body) {
                return { ok: r.ok, status: r.status, body: body };
            });
        }).then(function (res) {
            if (!res.ok) {
                // The server refuses for reasons the user can act on — Ollama
                // is not there, the disk is nearly full. Those are shown as
                // themselves, not as "something went wrong".
                pull = {
                    state: "error",
                    error: (res.body && res.body.error)
                        || ("The server refused the download (HTTP " + res.status + ").")
                };
                render();
                return;
            }
            pull = (res.body && res.body.pull) || { state: "running" };
            render();
            watchPull();
        }).catch(function (e) {
            pull = {
                state: "error",
                error: "The request to start the download did not reach the "
                    + "server. Helga's web service may have stopped."
            };
            if (window.console) { console.warn("pull start failed:", e); }
            render();
        }).then(function () {
            if (button) { button.disabled = false; }
        });
    }

    /* Streaming first, polling as the safety net. EventSource reconnects on
       its own, but it is not available everywhere and a proxy can buffer it
       into uselessness — and a download this long must not lose its progress
       display to either. */
    function watchPull() {
        stopWatching();
        if (typeof window.EventSource === "function") {
            try {
                pullSource = new EventSource("/api/setup/model/pull/events");
                pullSource.onmessage = function (ev) {
                    try { pull = JSON.parse(ev.data); } catch (e) { return; }
                    render();
                    if (pull.state === "done") { onPullFinished(); }
                    else if (pull.state === "error") { stopWatching(); }
                };
                pullSource.onerror = function () {
                    // Do not declare the download failed: the stream broke,
                    // not the pull. Fall back to polling the same record.
                    stopWatching();
                    pollPull();
                };
                return;
            } catch (e) {
                if (window.console) { console.warn("EventSource failed:", e); }
            }
        }
        pollPull();
    }

    function pollPull() {
        if (pullPoll) { return; }
        pullPoll = setInterval(function () {
            fetch("/api/setup/model/pull")
                .then(function (r) { return r.json(); })
                .then(function (p) {
                    pull = p;
                    render();
                    if (p.state === "done") { onPullFinished(); }
                    else if (p.state === "error" || p.state === "idle") {
                        stopWatching();
                    }
                })
                .catch(function () { /* the next tick tries again */ });
        }, 2000);
    }

    function stopWatching() {
        if (pullSource) { try { pullSource.close(); } catch (e) {} pullSource = null; }
        if (pullPoll) { clearInterval(pullPoll); pullPoll = null; }
    }

    function onPullFinished() {
        stopWatching();
        // Re-measure rather than assume. The step goes green because Ollama
        // now lists the model, not because a download said it was finished.
        checkNow();
    }

    /* --------------------------------------------------------- one step */

    function stepItem(step, index) {
        var li = el("li", "setup-step is-" + (step.state || "unknown"));

        var head = el("div", "setup-step-head");
        head.appendChild(el("span", "setup-step-num", String(index + 1)));
        head.appendChild(el("h2", "setup-step-title", step.title || step.id));
        head.appendChild(pill(step.state));
        li.appendChild(head);

        li.appendChild(el("p", "setup-step-headline", step.headline || ""));
        if (step.detail) {
            li.appendChild(el("p", "setup-step-detail", step.detail));
        }

        // Naming the step this one is waiting on, so "not measured" never
        // reads as "fine" or as "missing".
        if (step.blocked_by) {
            var w = last && (last.steps || []).filter(function (s) {
                return s.id === step.blocked_by;
            })[0];
            li.appendChild(el("p", "setup-step-waiting",
                "Waiting on step: " + ((w && w.title) || step.blocked_by)));
        }

        var sub = subList(step.sub);
        if (sub) { li.appendChild(sub); }

        // The model step is the only one this page can fix by itself, and it
        // owns the download regardless of what the step currently reports.
        // Keyed on the step rather than on `fixable`, because a download in
        // flight must stay on screen even when the step flips to `unknown`:
        // seen live — Ollama stopped answering mid-pull, the step became "not
        // measured", and the progress bar the user was watching vanished with
        // it, which reads as the download having been silently abandoned.
        if (step.id === "model") {
            var p = pull || (last && last.pull);
            // "Download finished. Re-checking…" is worth saying in the gap
            // between the last byte and the next status call, and stale the
            // moment the step itself turns green: at that point the green step
            // IS the confirmation, and leaving the note under it made a
            // finished install look like it was still working.
            var showPull = p && p.state && p.state !== "idle"
                && !(p.state === "done" && step.state === "ok");
            if (showPull) {
                li.appendChild(pullBlock(p));
            } else if (step.fixable === "pull") {
                var act = el("div", "setup-step-action");
                act.appendChild(pullButton("Download it now"));
                act.appendChild(el("span", "setup-step-action-note",
                    "Several gigabytes. You can also run the command below in a "
                    + "terminal — either way ends up in the same place."));
                li.appendChild(act);
            }
        }

        if (step.state !== "ok") {
            var cmds = commandBlock(step.commands);
            if (cmds) { li.appendChild(cmds); }
        }

        if (step.why) {
            var why = el("details", "setup-why");
            why.appendChild(el("summary", null, "Why this matters"));
            why.appendChild(el("p", null, step.why));
            li.appendChild(why);
        }
        return li;
    }

    /* ---------------------------------------------------------- the page */

    function renderReady(v) {
        var box = document.getElementById("setup-ready");
        if (!box) { return; }
        box.textContent = "";
        if (!v || !v.ready) {
            box.hidden = true;
            return;
        }
        box.hidden = false;

        var h = el("h2", "setup-ready-title", "Helga is ready to teach");
        h.id = "setup-ready-title";
        box.appendChild(h);

        var rough = (v.steps || []).filter(function (s) { return s.state !== "ok"; });
        box.appendChild(el("p", "setup-ready-text", rough.length
            ? ("Nothing is blocking you. " + rough.length + " step"
               + (rough.length === 1 ? " is" : "s are")
               + " still worth fixing — they are listed below and will not stop "
               + "you starting.")
            : "Every check passed. Build a course and Helga will start teaching "
              + "it."));

        var actions = el("div", "setup-ready-actions");
        var a = el("a", "setup-btn setup-btn-primary", "Create your first course");
        a.href = "/create";
        actions.appendChild(a);
        var b = el("a", "setup-btn", "Open Helga");
        b.href = "/";
        actions.appendChild(b);
        box.appendChild(actions);
    }


    /* START HERE.
     *
     * The blocking step is not necessarily the first one on the page -- the
     * cards are in a fixed order so the numbering means something, and on a
     * fresh machine the one thing standing between the user and a working
     * install ("docker compose up -d") was card 5, 1400px down, below the
     * fold. The top of the page restated the problem and offered no action.
     *
     * A first-run screen that shows five red things and no starting point is
     * how someone decides this is not worth it. So when something blocks, the
     * single next command comes to the top with its own copy button, and says
     * which card it belongs to for anyone who wants the detail.
     */
    function renderNextStep(v) {
        var host = document.getElementById("setup-next");
        if (!host) { return; }
        host.textContent = "";
        var blocked = (v.steps || []).filter(function (s) {
            return s.state === "blocked" && (s.commands || []).length;
        });
        if (v.ready || !blocked.length) { host.hidden = true; return; }
        host.hidden = false;

        var first = blocked[0];
        var n = (v.steps || []).indexOf(first) + 1;
        host.appendChild(el("h2", "setup-next-title", "Start here"));
        host.appendChild(el("p", "setup-next-text",
            (blocked.length === 1
                ? "One thing is stopping Helga from teaching"
                : blocked.length + " things are stopping Helga from teaching, "
                  + "and this is the first")
            + ". Run this in a terminal, in the Helga folder — step " + n
            + " below has the detail."));
        var cmd = commandBlock(first.commands);
        if (cmd) { host.appendChild(cmd); }
        host.appendChild(el("p", "setup-next-note",
            "This page re-checks by itself; you do not need to reload it."));
    }

    function render() {
        var v = last;
        var list = document.getElementById("setup-steps");
        var fill = document.getElementById("setup-progress-fill");
        var text = document.getElementById("setup-progress-text");
        var summary = document.getElementById("setup-summary");
        if (!list) { return; }

        if (!v) { return; }

        var done = Number(v.done) || 0;
        var total = Number(v.total) || 0;
        if (fill) {
            fill.style.width = (total ? (100 * done / total) : 0) + "%";
            fill.className = "setup-progress-fill is-" + (v.state || "unknown");
        }
        if (text) {
            text.textContent = total
                ? (done + " of " + total + " ready"
                   + (v.ready
                      ? (done === total ? "  ·  nothing left to do"
                                        : "  ·  you can start")
                      : "  ·  " + v.blocking.length
                        + (v.blocking.length === 1 ? " needs you" : " need you")))
                : "Nothing could be checked.";
        }
        renderNextStep(v);

        if (summary) {
            // When "Start here" is up it says the same thing and adds the fix,
            // so the red restatement above it is one alarming sentence the
            // reader has to get past to reach the useful one.
            var hasNext = !document.getElementById("setup-next").hidden;
            summary.textContent = hasNext ? "" : (v.summary || "");
            summary.className = "setup-summary is-" + (v.state || "unknown");
        }

        renderReady(v);

        list.textContent = "";
        (v.steps || []).forEach(function (s, i) {
            list.appendChild(stepItem(s, i));
        });

        (v.notes || []).forEach(function (n) {
            list.appendChild(el("li", "setup-note", String(n)));
        });
    }

    /* ------------------------------------------------------------ driver */

    function tick() {
        var cd = document.getElementById("setup-countdown");
        if (!cd) { return; }
        if (checking) {
            cd.textContent = "Checking… "
                + Math.max(0, Math.round((Date.now() - checkStartedAt) / 1000)) + "s";
        } else if (nextCheckAt) {
            cd.textContent = "Checking again in "
                + Math.max(0, Math.ceil((nextCheckAt - Date.now()) / 1000)) + "s";
        }
    }

    function schedule() {
        clearTimeout(statusTimer);
        var wait = POLL_READY_MS;
        if (pull && pull.state === "running") { wait = POLL_PULLING_MS; }
        else if (last && !last.ready) { wait = POLL_BLOCKED_MS; }
        nextCheckAt = Date.now() + wait;
        statusTimer = setTimeout(function () { checkNow(); }, wait);
    }

    function checkNow(manual) {
        if (checking) { return; }
        checking = true;
        checkStartedAt = Date.now();
        if (manual) { clearTimeout(statusTimer); }
        tick();

        fetch("/api/setup/status")
            .then(function (r) { return r.json(); })
            .then(function (v) {
                last = v;
                // The server's pull record wins on a fresh load; a live stream
                // takes over from there.
                if (v && v.pull && (!pull || !pullSource)) { pull = v.pull; }
                if (pull && pull.state === "running" && !pullSource && !pullPoll) {
                    watchPull();
                }
            })
            .catch(function (e) {
                if (window.console) { console.warn("setup status:", e); }
                // Naming the failure. An unreachable endpoint is not a pass,
                // and the raw parser complaint ("Unexpected token '<'") tells
                // a first-time user nothing they can act on.
                last = {
                    state: "unknown", ready: false, done: 0, total: 0,
                    blocking: [], summary: "The setup check could not be reached.",
                    steps: [{
                        id: "status", title: "Setup check", state: "unknown",
                        headline: "This page could not reach Helga's web service.",
                        detail: "Nothing here was measured, so nothing here is a "
                            + "pass. If the containers are still starting, this "
                            + "clears on its own.",
                        commands: ["docker compose up -d",
                                   "docker compose logs -f web-ui"],
                        sub: [], why: null, blocked_by: null, fixable: null,
                        measured: {}
                    }],
                    notes: []
                };
            })
            .then(function () {
                checking = false;
                render();
                schedule();
                tick();
            });
    }

    document.addEventListener("DOMContentLoaded", function () {
        var btn = document.getElementById("setup-recheck");
        if (btn) {
            btn.addEventListener("click", function () { checkNow(true); });
        }
        checkNow();
        setInterval(tick, 1000);
    });

    document.addEventListener("visibilitychange", function () {
        if (document.hidden) {
            clearTimeout(statusTimer);
            statusTimer = null;
        } else {
            // A verdict that was right when the tab was hidden may be stale
            // now, and this page is read precisely while things are changing.
            checkNow(true);
        }
    });
})();
