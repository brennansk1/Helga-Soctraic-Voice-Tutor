/* Storage, hardware, the memory safeguard, and the startup preflight.
 *
 * Surfaces, from two endpoints:
 *
 *   1. The Settings panel — where the disk went, broken down by course, what
 *      this machine actually is, and every preflight check with its state, so
 *      the answer is inspectable when nothing is wrong.
 *   2. A safeguard card that appears anywhere in the app when the machine is
 *      out of memory, and LEAVES BY ITSELF once there is room again. A warning
 *      the user has to dismiss by hand is a warning that outlives the problem
 *      and teaches people to ignore it.
 *   3. A startup gate that covers the app when the preflight comes back
 *      `blocked` — the machine genuinely cannot run Helga — and a quieter
 *      strip when it comes back `degraded`.
 *
 * The card is driven by memory_guard's own verdict rather than a threshold
 * invented here: the guard already distinguishes "throttle background work"
 * from "stop", and those are genuinely different messages.
 *
 * The gate follows the card's rule rather than inventing its own: no dismiss
 * button, because the condition is not something a user can acknowledge away,
 * and it removes itself the moment the machine has room. The one control it
 * offers — "Check again" — re-measures; it does not close anything.
 */
(function () {
    "use strict";

    var POLL_MS = 20000;          // the card must clear promptly, not instantly
    var timer = null;

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    /* Bytes at human scale. Deliberately 1 decimal: "1.4 GB" is actionable,
       "1.43829 GB" is noise. */
    function human(b) {
        if (b == null || isNaN(b)) return "—";
        var u = ["B", "KB", "MB", "GB", "TB"], i = 0, n = Number(b);
        while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
        return (i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)) + " " + u[i];
    }

    /* ------------------------------------------------------- settings panel */
    function renderSettings(d) {
        var body = document.getElementById("res-body");
        var loading = document.getElementById("res-loading");
        var errBox = document.getElementById("res-error");
        if (!body) return;                     // not on the settings page
        if (loading) loading.hidden = true;

        if (!d || d.error || (d.storage && d.storage.error)) {
            if (errBox) {
                errBox.hidden = false;
                errBox.textContent = "Could not measure storage — " +
                    ((d && (d.error || (d.storage && d.storage.error))) ||
                     "the service did not answer") + ".";
            }
            return;
        }
        body.hidden = false;

        var st = d.storage || {}, mem = d.memory || {};
        document.getElementById("res-total").textContent = human(st.total_bytes);
        document.getElementById("res-free").textContent =
            st.disk ? human(st.disk.free_bytes) : "—";
        document.getElementById("res-mem").textContent =
            mem.available_gb != null
                ? mem.available_gb.toFixed(1) + " / " + mem.total_gb.toFixed(0) + " GB"
                : "—";

        // Segments: the courses, then the database, then everything else.
        var segs = (st.courses || []).map(function (c, i) {
            return { label: c.title, bytes: c.bytes, cls: "res-seg-" + (i % 6) };
        });
        if (st.database_bytes) {
            segs.push({ label: "Database", bytes: st.database_bytes, cls: "res-seg-db" });
        }
        if (st.other_bytes) {
            segs.push({ label: "Uploads, assets and logs",
                        bytes: st.other_bytes, cls: "res-seg-other" });
        }
        var total = segs.reduce(function (a, s) { return a + s.bytes; }, 0) || 1;

        var bar = document.getElementById("res-bar");
        var legend = document.getElementById("res-legend");
        bar.textContent = ""; legend.textContent = "";

        if (!segs.length) {
            legend.appendChild(el("li", "res-legend-empty",
                "Nothing stored yet — build a course and it will show up here."));
            return;
        }

        segs.forEach(function (s) {
            var seg = el("span", "res-seg " + s.cls);
            seg.style.width = (100 * s.bytes / total) + "%";
            seg.title = s.label + " — " + human(s.bytes);
            bar.appendChild(seg);

            var li = el("li", "res-legend-item");
            li.appendChild(el("span", "res-swatch " + s.cls));
            li.appendChild(el("span", "res-legend-label", s.label));
            li.appendChild(el("span", "res-legend-bytes", human(s.bytes)));
            legend.appendChild(li);
        });

        /* Machine internals, behind the "Technical details" disclosure in
           settings.html. Two of the labels were also wrong for what they
           printed: `processor` is the CPU architecture ("aarch64"), and
           `model` is the LANGUAGE model — sitting between "Cores" and
           "Platform" it read as the make of the machine. */
        var hw = d.hardware || {}, tb = document.getElementById("res-hw-body");
        var tech = document.getElementById("res-tech");
        tb.textContent = "";
        var rows = 0;
        [["Language model", hw.model],
         ["Processor cores", hw.cpu_count],
         ["Architecture", hw.processor || hw.machine],
         ["Memory installed", mem.total_gb ? mem.total_gb.toFixed(0) + " GB" : null],
         ["Operating system", hw.platform],
         ["Memory reading taken by", mem.source]
        ].forEach(function (row) {
            if (!row[1]) return;
            var tr = document.createElement("tr");
            tr.appendChild(el("th", null, row[0]));
            tr.appendChild(el("td", null, String(row[1])));
            tb.appendChild(tr);
            rows++;
        });
        // Nothing measurable: don't offer a door onto an empty room.
        if (tech) tech.hidden = rows === 0;
    }

    /* ---------------------------------------------------------- the card */
    function card() {
        var c = document.getElementById("mem-guard-card");
        if (c) return c;
        c = el("div", "mem-guard hidden");
        c.id = "mem-guard-card";
        c.setAttribute("role", "status");
        c.setAttribute("aria-live", "polite");
        document.body.appendChild(c);
        return c;
    }

    function renderCard(mem) {
        var c = card();

        // No verdict, or a good one: the card goes away on its own. This is
        // the whole point — it tracks the machine, not a dismissal.
        if (!mem || mem.error || !mem.under_pressure) {
            c.classList.add("hidden");
            c.textContent = "";
            return;
        }

        var stopped = mem.allow_background === false;
        c.classList.remove("hidden");
        c.classList.toggle("is-critical", stopped);
        c.textContent = "";

        c.appendChild(el("span", "mem-guard-mark i i-warning"));
        var b = el("div", "mem-guard-body");
        b.appendChild(el("h3", null, stopped
            ? "Not enough memory to keep building"
            : "Running low on memory"));

        // The guard's own reason, verbatim -- it knows whether the problem is
        // free pages or swap, and paraphrasing it here would drift.
        b.appendChild(el("p", null,
            (mem.reason ? mem.reason + ". " : "") +
            (stopped
                ? "Helga has paused background course building. Closing other " +
                  "applications will free memory and it will resume on its own."
                : "Helga is building more slowly to stay out of the way. " +
                  "Closing other applications will speed it back up.")));

        b.appendChild(el("p", "mem-guard-figures",
            mem.available_gb.toFixed(1) + " GB free of " +
            mem.total_gb.toFixed(0) + " GB" +
            (mem.swap_used_frac >= 0.5
                ? " · swap " + Math.round(mem.swap_used_frac * 100) + "% used"
                : "")));
        c.appendChild(b);
    }

    /* --------------------------------------------------- startup preflight */

    /* Cadence. When the machine is blocked the gate is in the user's way, so
       it re-measures often enough to get out of the way quickly. When nothing
       is wrong the check costs two HTTP calls to Ollama and deserves a slow
       heartbeat. */
    var PRE_MS_BLOCKED = 10000;
    var PRE_MS_OK = 60000;

    var preTimer = null;      // setTimeout to the next check
    var preNextAt = 0;        // epoch ms of the next check, for the countdown
    var preStartedAt = 0;     // epoch ms the in-flight check began
    var preBusy = false;
    var preLast = null;

    var STATE_WORDS = { ok: "Ready", degraded: "Working around it",
                        blocked: "Cannot start", unknown: "Not measured" };

    /* The measured numbers, restated compactly under the prose — for the
       person who wants the figures without the sentence.

       EVERY ROW USED TO SAY ITSELF TWICE. The disk check's prose is
       "20.8 GB free." and this function then printed "20.8 GB free on disk"
       directly underneath it; the memory rows did the same with their own
       numbers. So a figure is only restated when the prose does not already
       contain it, and a line with nothing new left in it is not drawn at all. */
    function figuresFor(check) {
        var m = check.measured || {}, bits = [];
        var prose = String(check.reason || "") + " " + String(check.remedy || "");

        function add(value, text) {
            // The number is the thing that would repeat; if the prose already
            // states it, the phrasing around it is noise.
            if (value != null && prose.indexOf(String(value)) !== -1) return;
            bits.push(text);
        }
        if (m.available_gb != null)
            add(m.available_gb.toFixed(1), m.available_gb.toFixed(1) + " GB free");
        if (m.total_gb != null)
            add(m.total_gb.toFixed(1), m.total_gb.toFixed(1) + " GB installed");
        if (m.required_gb != null)
            add(m.required_gb.toFixed(1), m.required_gb.toFixed(1) + " GB needed");
        if (m.free_gb != null)
            add(m.free_gb.toFixed(1), m.free_gb.toFixed(1) + " GB free on disk");
        if (m.model) add(m.model, String(m.model));
        if (m.weights_gb != null)
            add(m.weights_gb.toFixed(1), m.weights_gb.toFixed(1) + " GB of weights");
        if (m.pressure_level != null)
            bits.push("pressure level " + m.pressure_level);
        return bits.join(" · ");
    }

    /* Remedies come from the preflight module, which is also a command-line
       tool, and one of them tells the reader to run
       `python3 -m services.common.startup_preflight`. That is an instruction to
       a developer with a checkout and a shell, not to the person looking at
       Settings — and it is offered at exactly the moment they are told
       something might be wrong. The fact it is reporting (Docker can only see
       the container's memory) is kept; the shell command is replaced by the
       thing a person can actually do, which is open the setup guide. */
    function humanRemedy(text) {
        if (!text) return "";
        if (text.indexOf("services.common.") !== -1) {
            return "Helga is running inside Docker, so it can only measure the " +
                   "container. The setup guide shows how to read this machine's " +
                   "own memory.";
        }
        return text;
    }

    /* One check, rendered as prose then remedy then figures. Every value comes
       from the server, so every one of them goes in via textContent.

       `pill` — when a state word is supplied it goes on the same line as the
       label. It used to be inserted as the item's first child, in front of a
       block-level heading, so it sat ABOVE the title while the panel's own
       badge sat beside its title: the same badge, two placements, one panel.

       `seen` — an optional set of sentences already printed elsewhere in this
       panel. The summary line is verbatim one of the check reasons, so without
       it the panel opens by saying the same sentence twice. */
    function checkItem(check, withFigures, pill, seen) {
        var li = el("li", "preflight-item is-" + (check.state || "unknown"));

        var head = el("div", "preflight-item-head");
        head.appendChild(el("h3", "preflight-item-label", check.label || check.id));
        if (pill) head.appendChild(pill);
        li.appendChild(head);

        var reason = check.reason || "";
        if (reason && (!seen || seen.take(reason))) {
            li.appendChild(el("p", "preflight-item-reason", reason));
        }
        var remedy = check.state !== "ok" ? humanRemedy(check.remedy) : "";
        if (remedy && (!seen || seen.take(remedy))) {
            li.appendChild(el("p", "preflight-item-remedy", remedy));
        }
        var fig = withFigures ? figuresFor(check) : "";
        if (fig) li.appendChild(el("p", "preflight-item-figures", fig));
        return li;
    }

    /* Say-it-once ledger for one render pass. Normalises whitespace and case so
       a sentence that differs only in punctuation still counts as a repeat. */
    function ledger() {
        var seen = {};
        return {
            take: function (text) {
                var k = String(text).toLowerCase().replace(/\s+/g, " ")
                        .replace(/[.\s]+$/, "");
                if (!k || seen[k]) return false;
                seen[k] = true;
                return true;
            }
        };
    }

    /* ---- the blocking gate ---- */
    function gate() {
        var g = document.getElementById("preflight-gate");
        if (g) return g;
        g = el("div", "preflight-gate hidden");
        g.id = "preflight-gate";
        g.setAttribute("role", "alertdialog");
        g.setAttribute("aria-modal", "true");
        g.setAttribute("aria-labelledby", "preflight-gate-title");
        document.body.appendChild(g);
        return g;
    }

    function renderGate(v) {
        var g = gate();
        var blocking = (v && v.checks || []).filter(function (c) {
            return c.state === "blocked";
        });

        // Not blocked any more: the gate leaves, exactly like the card. There
        // is nothing to acknowledge — the machine either has room or it does not.
        if (!v || v.state !== "blocked" || !blocking.length) {
            g.classList.add("hidden");
            g.textContent = "";
            document.body.classList.remove("preflight-blocked");
            return;
        }

        g.classList.remove("hidden");
        document.body.classList.add("preflight-blocked");
        g.textContent = "";

        var p = el("div", "preflight-gate-panel");
        p.appendChild(el("span", "preflight-gate-mark i i-warning i-danger"));

        var h = el("h2", "preflight-gate-title", "Helga cannot start on this machine right now");
        h.id = "preflight-gate-title";
        p.appendChild(h);
        p.appendChild(el("p", "preflight-gate-lead",
            "Starting anyway would not be slow — past this point the machine " +
            "stops producing usable output at all. Here is what is wrong:"));

        var list = el("ul", "preflight-list");
        var seen = ledger();
        blocking.forEach(function (c) {
            list.appendChild(checkItem(c, true, null, seen));
        });
        p.appendChild(list);

        var foot = el("div", "preflight-gate-foot");
        var btn = el("button", "preflight-recheck", "Check again");
        btn.type = "button";
        btn.addEventListener("click", function () { preflightPoll(true); });
        foot.appendChild(btn);

        // /setup is the page built to walk somebody through fixing exactly
        // this, and NOTHING in the app linked to it -- a crawl of every
        // internal link found it orphaned. The one screen that tells a person
        // their machine is blocked was not offering them the one screen that
        // helps, so the only way there was knowing the URL.
        var help = el("a", "preflight-gate-help", "Open the setup guide");
        help.href = "/setup";
        foot.appendChild(help);

        // A counter, not a spinner: this waits on someone closing an
        // application, and a spinner would say nothing about how long that is.
        var cd = el("span", "preflight-countdown", "");
        cd.id = "preflight-countdown";
        cd.setAttribute("role", "status");
        cd.setAttribute("aria-live", "polite");
        foot.appendChild(cd);
        p.appendChild(foot);

        g.appendChild(p);
        tickCountdown();
    }

    /* ---- the non-blocking strip ---- */
    function note() {
        var n = document.getElementById("preflight-note");
        if (n) return n;
        n = el("div", "preflight-note hidden");
        n.id = "preflight-note";
        n.setAttribute("role", "status");
        n.setAttribute("aria-live", "polite");
        // IN THE FLOW, at the top of <main> — not floating over it. Fixed
        // positioning cleared the navigation but still sat on top of whatever
        // the page's own first element was: the course title on /learn, the
        // programme name on /degree. A strip that covers the thing you opened
        // the page to read has stopped informing and started interrupting.
        var main = document.querySelector(".app-content") || document.body;
        main.insertBefore(n, main.firstChild);
        return n;
    }

    function renderNote(v) {
        var n = note();
        var items = (v && v.checks || []).filter(function (c) {
            // `unknown` means we could not measure, which is inspectable in
            // Settings and not something anyone can act on. Inside Docker the
            // memory checks are permanently unknown, so putting them here
            // would pin a banner to every page forever — which is how a user
            // learns to ignore this strip and the safeguard card with it.
            // The one exception is the preflight failing wholesale: then
            // NOTHING was measured, and silence would read as a pass.
            if (c.state === "unknown") return c.id === "preflight";
            if (c.state !== "degraded") return false;
            // Current memory pressure already has a surface — the safeguard
            // card — and it says more about it than a strip could. Two
            // notifications for one condition is how people learn to ignore both.
            return c.id !== "available_memory";
        });

        if (!v || v.state === "blocked" || !items.length) {
            n.classList.add("hidden");
            n.textContent = "";
            return;
        }

        n.classList.remove("hidden");
        n.textContent = "";
        n.appendChild(el("span", "preflight-note-mark i i-warning i-muted"));
        var body = el("div", "preflight-note-body");
        items.forEach(function (c) {
            var line = el("p", "preflight-note-line");
            line.appendChild(el("strong", null, (c.label || c.id) + ": "));
            var fix = humanRemedy(c.remedy);
            line.appendChild(document.createTextNode(
                c.reason + (fix ? " " + fix : "")));
            body.appendChild(line);
        });
        n.appendChild(body);
    }

    /* ---- the settings panel section ---- */
    function renderPreflightPanel(v) {
        // Deliberately a sibling of #res-body rather than a child of it.
        // #res-body is hidden whenever the storage measurement fails, and the
        // preflight is measured separately — hiding a verdict we did take
        // because a different one failed is how a panel starts lying.
        var host = document.getElementById("section-resources");
        if (!host) return;                     // not on the settings page

        var box = document.getElementById("res-preflight");
        if (!box) {
            box = el("section", "res-preflight");
            box.id = "res-preflight";
            host.appendChild(box);
        }
        box.textContent = "";

        var head = el("div", "res-preflight-head");
        head.appendChild(el("h3", null, "Startup check"));
        var st = (v && v.state) || "unknown";
        head.appendChild(el("span", "preflight-pill is-" + st,
                            STATE_WORDS[st] || st));
        box.appendChild(head);

        /* One ledger for the whole panel. The summary is verbatim one of the
           check reasons and the scope note said the same thing a third time in
           different words, so a Docker deployment opened this panel with the
           identical sentence three times over. */
        var seen = ledger();
        var summary = (v && v.summary) || "Not measured.";
        seen.take(summary);
        box.appendChild(el("p", "res-preflight-summary", summary));

        // Only when the summary has not already said it — on a Docker
        // deployment the summary IS this sentence, and a caveat repeated
        // immediately underneath itself reads as a stutter, not as emphasis.
        if (v && v.scope === "container" && !/docker/i.test(summary)) {
            var scopeNote = "Memory was measured inside Docker, so it describes " +
                            "the container rather than this machine.";
            if (seen.take(scopeNote)) {
                box.appendChild(el("p", "res-preflight-scope", scopeNote));
            }
        }

        var list = el("ul", "preflight-list is-compact");
        ((v && v.checks) || []).forEach(function (c) {
            var pill = el("span", "preflight-pill is-" + (c.state || "unknown"),
                          STATE_WORDS[c.state] || c.state);
            list.appendChild(checkItem(c, true, pill, seen));
        });
        box.appendChild(list);

        // Same reason as the gate: this panel reports the verdict and the
        // page that acts on it was unreachable from anywhere.
        var more = el("p", "res-preflight-more");
        var a = el("a", null, "Open the setup guide");
        a.href = "/setup";
        more.appendChild(a);
        more.appendChild(document.createTextNode(
            " — every check with the exact command to fix it."));
        box.appendChild(more);

        ((v && v.notes) || []).forEach(function (t) {
            box.appendChild(el("p", "res-preflight-scope", String(t)));
        });
    }

    /* ---- driver for the preflight ---- */
    function tickCountdown() {
        var cd = document.getElementById("preflight-countdown");
        if (!cd) return;
        if (preBusy) {
            cd.textContent = "Checking… " +
                Math.max(0, Math.round((Date.now() - preStartedAt) / 1000)) + "s";
        } else {
            cd.textContent = "Checking again in " +
                Math.max(0, Math.ceil((preNextAt - Date.now()) / 1000)) + "s";
        }
    }

    function schedulePreflight() {
        clearTimeout(preTimer);
        var wait = (preLast && preLast.state === "blocked")
            ? PRE_MS_BLOCKED : PRE_MS_OK;
        preNextAt = Date.now() + wait;
        preTimer = setTimeout(function () { preflightPoll(); }, wait);
    }

    function preflightPoll(manual) {
        if (preBusy) return;
        preBusy = true;
        preStartedAt = Date.now();
        if (manual) clearTimeout(preTimer);
        tickCountdown();

        fetch("/api/system/preflight")
            .then(function (r) { return r.json(); })
            .then(function (v) { preLast = v; })
            .catch(function (e) {
                if (window.console) {
                    console.warn("preflight unreachable:", e && e.message);
                }
                // Naming the failure rather than treating an unreachable
                // endpoint as a pass. The gate stays open in this state,
                // because we did not measure anything that says it should not.
                preLast = {
                    state: "degraded",
                    summary: "The startup check could not be reached.",
                    checks: [{ id: "preflight", label: "Startup check",
                               state: "unknown",
                               // The raw exception is for the console, not the
                               // page. A learner mid-lesson was being shown
                               // `Unexpected token '<', "<!doctype "... is not
                               // valid JSON` — a JSON parser's internal
                               // complaint, which tells them nothing they can
                               // act on and reads as a broken product.
                               reason: "The startup check could not reach the " +
                                       "server, so this machine was not " +
                                       "measured. Helga is running normally; " +
                                       "only the hardware check is missing.",
                               remedy: null, measured: {} }],
                    blocking: [], notes: []
                };
            })
            .then(function () {
                preBusy = false;
                renderGate(preLast);
                renderNote(preLast);
                renderPreflightPanel(preLast);
                schedulePreflight();
                tickCountdown();
            });
    }


    /* ----------------------------------------------------- system health
     * Answers "is anything broken right now" without a navigation. The data
     * comes from /api/health/all, which has been collected since the
     * beginning and displayed nowhere.
     *
     * Names, not service ids: "helga-rag-engine" is a container, "Course
     * library" is the thing a person loses when it is down. And the reason it
     * matters is stated per service, because "rag: offline" tells somebody
     * nothing about what they can still do.
     */
    var SERVICE_NAMES = {
        core: ["Core logic", "the tutor itself"],
        rag: ["Course library", "your courses, search and flashcards"],
        tts: ["Speech out", "Helga reading aloud"],
        stt: ["Speech in", "the microphone"],
        research: ["Research", "grounding new courses in real sources"],
        searxng: ["Search", "the index course building draws on"],
        ollama: ["Language model", "every question and every build"]
    };

    // Voice is genuinely optional -- text teaching is unaffected -- so it must
    // not be counted alongside the services that stop the product working.
    var OPTIONAL = { tts: true, stt: true };

    function renderHealth(d) {
        var body = document.getElementById("health-body");
        var loading = document.getElementById("health-loading");
        var errBox = document.getElementById("health-error");
        if (!body) return;                     // not on the settings page
        if (loading) loading.hidden = true;

        if (!d || d.__error) {
            if (errBox) {
                errBox.hidden = false;
                errBox.textContent = "Could not check the services — " +
                    ((d && d.__error) || "no answer") + ".";
            }
            body.hidden = true;
            return;
        }
        body.hidden = false;

        var list = document.getElementById("health-list");
        list.textContent = "";
        var downRequired = 0, downOptional = 0, total = 0, required = 0;
        var downOptionalNames = [];

        Object.keys(SERVICE_NAMES).forEach(function (key) {
            var info = d[key];
            if (!info) return;                 // not part of this deployment
            total++;
            if (!OPTIONAL[key]) required++;
            var ok = info.status === "healthy";
            if (!ok) {
                if (OPTIONAL[key]) {
                    downOptional++;
                    downOptionalNames.push(SERVICE_NAMES[key][0].toLowerCase());
                } else { downRequired++; }
            }

            var li = document.createElement("li");
            li.className = "health-item " + (ok ? "is-ok" : "is-down");
            var dot = document.createElement("span");
            dot.className = "health-dot";
            dot.setAttribute("aria-hidden", "true");
            var name = document.createElement("span");
            name.className = "health-name";
            name.textContent = SERVICE_NAMES[key][0];
            var state = document.createElement("span");
            state.className = "health-state";
            // The word carries the state; the colour only reinforces it.
            state.textContent = ok ? "running"
                : (OPTIONAL[key] ? "not running — optional" : "not running");
            var why = document.createElement("span");
            why.className = "health-why";
            why.textContent = SERVICE_NAMES[key][1];
            li.appendChild(dot); li.appendChild(name);
            li.appendChild(state); li.appendChild(why);
            list.appendChild(li);
        });

        var sum = document.getElementById("health-summary");
        if (downRequired) {
            // Counted against the services Helga NEEDS, not against every
            // service listed: "4 of 6 are not running" was printed while all
            // six rows said not running, because the two optional ones were
            // in the denominator and not the numerator.
            sum.textContent = downRequired + " of the " + required +
                " services Helga needs " + (downRequired === 1 ? "is" : "are") +
                " not running. It cannot teach until " +
                (downRequired === 1 ? "that one is" : "they are") + " back.";
            sum.className = "health-summary is-down";
        } else if (downOptional) {
            /* Name what is actually missing. "Voice is not running" was
               printed whenever ANY optional service was down, including when
               only the microphone was — while Helga could still read every
               lesson aloud, which is the opposite of what the sentence said. */
            sum.textContent = "Everything needed to teach is running. " +
                (downOptionalNames.length === 1
                    ? "The " + downOptionalNames[0] + " is not"
                    : downOptionalNames.join(" and ") + " are not") +
                ", so that part is unavailable.";
            sum.className = "health-summary is-degraded";
        } else {
            sum.textContent = "All " + total + " services are running.";
            sum.className = "health-summary is-ok";
        }
    }

    function healthPoll() {
        fetch("/api/health/all")
            .then(function (r) { return r.json(); })
            .then(renderHealth)
            .catch(function (e) {
                renderHealth({ __error: String(e && e.message || e).slice(0, 80) });
            });
    }

    /* ------------------------------------------------------------- driver */
    function poll() {
        fetch("/api/system/resources")
            .then(function (r) { return r.json(); })
            .then(function (d) {
                renderCard(d && d.memory);
                renderSettings(d);
            })
            .catch(function () {
                // A card that cannot measure must not assert pressure.
                renderCard(null);
                renderSettings({ error: "unreachable" });
            });
    }

    document.addEventListener("DOMContentLoaded", function () {
        poll();
        timer = setInterval(poll, POLL_MS);
        // Only on the settings page: no reason to probe six services from
        // every other page in the app.
        if (document.getElementById("health-body")) {
            healthPoll();
            setInterval(healthPoll, POLL_MS);
        }
        preflightPoll();
        setInterval(tickCountdown, 1000);
    });
    // Stop polling while the tab is hidden; resume with a fresh reading.
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) {
            clearInterval(timer); timer = null;
            clearTimeout(preTimer); preTimer = null;
        } else {
            if (!timer) { poll(); timer = setInterval(poll, POLL_MS); }
            // A gate that was correct when the tab was hidden may be stale
            // now; re-measure before the user acts on it.
            preflightPoll(true);
        }
    });
})();
