/* Storage, hardware, and the memory safeguard.
 *
 * Two surfaces from one endpoint:
 *
 *   1. The Settings panel — where the disk went, broken down by course, and
 *      what this machine actually is.
 *   2. A safeguard card that appears anywhere in the app when the machine is
 *      out of memory, and LEAVES BY ITSELF once there is room again. A warning
 *      the user has to dismiss by hand is a warning that outlives the problem
 *      and teaches people to ignore it.
 *
 * The card is driven by memory_guard's own verdict rather than a threshold
 * invented here: the guard already distinguishes "throttle background work"
 * from "stop", and those are genuinely different messages.
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

        var hw = d.hardware || {}, tb = document.getElementById("res-hw-body");
        tb.textContent = "";
        [["Processor", hw.processor], ["Cores", hw.cpu_count],
         ["Memory", mem.total_gb ? mem.total_gb.toFixed(0) + " GB" : null],
         ["Model", hw.model], ["Platform", hw.platform],
         ["Measured by", mem.source]
        ].forEach(function (row) {
            if (!row[1]) return;
            var tr = document.createElement("tr");
            tr.appendChild(el("th", null, row[0]));
            tr.appendChild(el("td", null, String(row[1])));
            tb.appendChild(tr);
        });
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
    });
    // Stop polling while the tab is hidden; resume with a fresh reading.
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) { clearInterval(timer); timer = null; }
        else if (!timer) { poll(); timer = setInterval(poll, POLL_MS); }
    });
})();
