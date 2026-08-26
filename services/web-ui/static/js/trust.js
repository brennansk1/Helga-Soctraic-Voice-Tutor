/* The trust surface: where this lesson actually came from.
 *
 * Helga's whole claim is that it writes a course from real material rather
 * than from whatever the model remembers. That claim is only worth anything if
 * a learner can check it, so every concept can show its sources: what was
 * read, how strongly each one matched, and how much of the lesson rests on
 * supplementary material rather than primary.
 *
 * Everything here is built with textContent and createElement. Source titles
 * and URLs come from scraped pages, which makes them exactly the kind of value
 * that must never reach innerHTML.
 *
 * The panel is supporting detail, never the lesson. Every failure degrades to
 * a named state and leaves the session running.
 */
(function () {
    "use strict";

    var state = { course: null, concept: null, data: null, open: false, req: 0 };

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    /* ---------------------------------------------------------------- fetch */
    function load(courseUid, conceptUid) {
        state.course = courseUid;
        state.concept = conceptUid;
        state.data = null;
        var mine = ++state.req;
        setPillLoading();
        if (!courseUid || !conceptUid) { setPillHidden(); return; }

        fetch("/api/concept_sources?uid=" + encodeURIComponent(conceptUid) +
              "&course_uid=" + encodeURIComponent(courseUid))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (mine !== state.req) return;      // a newer concept won
                state.data = d;
                render();
            })
            .catch(function () {
                if (mine !== state.req) return;
                state.data = { available: false, sources: [], error: "unreachable" };
                render();
            });
    }

    /* ----------------------------------------------------------------- pill */
    function pill() { return document.getElementById("trust-pill"); }
    function panel() { return document.getElementById("trust-panel"); }

    function setPillLoading() {
        var p = pill();
        if (!p) return;
        p.classList.remove("hidden");
        p.disabled = true;
        p.textContent = "Sources…";
    }
    function setPillHidden() {
        var p = pill();
        if (p) p.classList.add("hidden");
    }

    function render() {
        var p = pill(), d = state.data;
        if (!p || !d) return;
        p.disabled = false;
        p.textContent = "";

        var n = (d.sources || []).length;
        if (!d.available || !n) {
            // Not an error: courses built before sources were recorded are
            // still perfectly good courses. Say which it is.
            p.appendChild(el("span", "trust-pill-label",
                d.error ? "Sources unavailable" : "Sources not recorded"));
            p.classList.add("is-empty");
        } else {
            p.classList.remove("is-empty");
            p.appendChild(el("span", "trust-pill-count", String(n)));
            // A SPACE. Two adjacent spans with no gap render as "3sources",
            // which is what the pill has always said. Put in the text rather
            // than left to CSS so it survives however the pill is styled.
            p.appendChild(el("span", "trust-pill-label",
                n === 1 ? " source" : " sources"));
            // The supplementary share is the number the build policy caps, so
            // it is the number worth surfacing rather than a generic score.
            if (d.supplementary_share >= 0.2) {
                p.appendChild(el("span", "trust-pill-warn",
                    Math.round(d.supplementary_share * 100) + "% supplementary"));
            }
        }
        if (state.open) renderPanel();
    }

    /* ---------------------------------------------------------------- panel */
    function tierLabel(t) {
        return ({ primary: "Primary", textbook: "Textbook", reference: "Reference",
                  encyclopedia: "Encyclopedia", supplementary: "Supplementary"
                })[t] || (t || "Unclassified");
    }

    function sourceRow(s) {
        var row = el("li", "trust-source");

        var head = el("div", "trust-source-head");
        // A source title is scraped text: textContent, never innerHTML.
        if (s.url) {
            var a = el("a", "trust-source-title", s.title);
            a.href = s.url;
            a.target = "_blank";
            // noopener because these are third-party pages opened from the app.
            a.rel = "noopener noreferrer";
            head.appendChild(a);
        } else {
            head.appendChild(el("span", "trust-source-title", s.title));
        }
        head.appendChild(el("span", "trust-tier trust-tier-" +
            String(s.domain_tier || "none").replace(/[^a-z]/gi, ""),
            tierLabel(s.domain_tier)));
        // Only when the tier does not already say so — a source tiered
        // "supplementary" was rendering the word twice, side by side.
        if (s.supplementary && s.domain_tier !== "supplementary") {
            head.appendChild(el("span", "trust-tag", "supplementary"));
        }
        if (s.degraded) head.appendChild(el("span", "trust-tag trust-tag-warn", "degraded"));
        row.appendChild(head);

        var meta = el("div", "trust-source-meta");
        if (s.grounding != null) {
            var bar = el("span", "trust-ground");
            var fill = el("span", "trust-ground-fill");
            // Grounding is a relevance score on roughly 0..10; clamp so an
            // out-of-range value cannot draw a bar wider than its track.
            var pct = Math.max(0, Math.min(100, (s.grounding / 10) * 100));
            fill.style.width = pct + "%";
            bar.appendChild(fill);
            bar.title = "Grounding relevance " + s.grounding;
            meta.appendChild(bar);
            meta.appendChild(el("span", "trust-ground-num", s.grounding.toFixed(2)));
        }
        if (s.claims) {
            meta.appendChild(el("span", "trust-claims",
                s.claims + (s.claims === 1 ? " claim" : " claims")));
        }
        row.appendChild(meta);

        if (s.excerpt) row.appendChild(el("p", "trust-excerpt", s.excerpt));
        return row;
    }

    function renderPanel() {
        var box = panel(), d = state.data;
        if (!box) return;
        box.textContent = "";

        var head = el("div", "trust-panel-head");
        head.appendChild(el("h2", null, "Where this lesson came from"));
        var close = el("button", "trust-close", "×");
        close.setAttribute("aria-label", "Close sources");
        close.addEventListener("click", function () { toggle(false); });
        head.appendChild(close);
        box.appendChild(head);

        if (!d) { box.appendChild(el("p", "trust-note", "Loading…")); return; }

        if (!d.available || !(d.sources || []).length) {
            box.appendChild(el("p", "trust-note", d.error
                ? "The source list could not be loaded. The lesson itself is unaffected."
                : "This course was built before Helga recorded its sources, so " +
                  "there is nothing to show here. Newer courses list every " +
                  "source they were written from."));
            return;
        }

        if (d.claims_total) {
            var sum = el("p", "trust-summary");
            sum.appendChild(el("strong", null, String(d.claims_total)));
            sum.appendChild(document.createTextNode(
                (d.claims_total === 1 ? " claim" : " claims") + " traced to " +
                d.sources.length + (d.sources.length === 1 ? " source" : " sources") + ". "));
            var share = Math.round((d.supplementary_share || 0) * 100);
            sum.appendChild(document.createTextNode(
                share === 0
                    ? "All of it rests on primary material."
                    : share + "% rests only on supplementary material" +
                      (share >= 20 ? " — above the 20% the build aims to stay under." : ".")));
            box.appendChild(sum);
        }

        var list = el("ul", "trust-list");
        d.sources.forEach(function (s) { list.appendChild(sourceRow(s)); });
        box.appendChild(list);
    }

    function toggle(open) {
        var box = panel(), p = pill();
        if (!box) return;
        state.open = (open == null) ? !state.open : open;
        box.classList.toggle("hidden", !state.open);
        if (p) p.setAttribute("aria-expanded", state.open ? "true" : "false");
        if (state.open) {
            renderPanel();
            var c = box.querySelector(".trust-close");
            if (c) c.focus();
        } else if (p) { p.focus(); }
    }

    document.addEventListener("DOMContentLoaded", function () {
        var p = pill();
        if (p) p.addEventListener("click", function () { toggle(); });
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && state.open) toggle(false);
        });
    });

    // The panel in session.js needs the same answer this pill already has.
    //
    // It was deriving its own from `graph_node.text`, a field the FSM does not
    // send — its node carries analogies, bloom_level, misconceptions, title and
    // uid, and no body. So the parse always returned zero sources and the panel
    // told every learner on every concept "no sources cited · Mostly the
    // model's own knowledge", six inches from this pill reading "1 source".
    //
    // One fetch, one answer, no second opinion to disagree with.
    function current() {
        return {
            course: state.course,
            concept: state.concept,
            data: state.data || null,
        };
    }

    window.HelgaTrust = { load: load, toggle: toggle, current: current };
})();
