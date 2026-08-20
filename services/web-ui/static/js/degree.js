/* The degree map — a pannable, zoomable prerequisite DAG.
 *
 * Terms are columns, courses are nodes, prerequisite edges are drawn as real
 * curves. Status is colour: complete, ready, building (with a pulse), locked,
 * and elective-choice. The generation story is ON the map — a node that is
 * being built says so, because "the university builds itself as you go" is the
 * product's most marketable sentence and it should be visible rather than
 * asserted.
 *
 * Data: GET /api/program/<uid>, the JSON plan_degree() emits
 * ({subject, template, terms, courses:[{title, term, slot, requires[], status}]}).
 * A demo plan renders when the API is absent so the page is designable and
 * testable without a backend — clearly labelled as a preview, never silently.
 */
(function () {
    "use strict";

    // The programme currently rendered. Read from the URL when one is named,
    // otherwise resolved from /api/programs — either way the ONE place that
    // knows which programme an action applies to.
    var currentUid = null;

    var svg = document.getElementById("deg-svg");
    var viewport = document.getElementById("deg-viewport");
    var NS = "http://www.w3.org/2000/svg";

    // Layout constants (SVG user units; zoom scales everything together).
    var COL_W = 260, ROW_H = 92, NODE_W = 220, NODE_H = 68, PAD = 60;

    /* ---------------------------------------------------------- data */
    function demoPlan() {
        // The real Economics associate plan from tools testing — real titles,
        // real prerequisite edges — so the design is exercised by real shape.
        return {
            subject: "Economics", template: "associate", terms: 4,
            demo: true,
            courses: [
                { title: "English Composition I", term: 1, slot: "gen_ed", status: "complete" },
                { title: "Introduction to Statistics", term: 1, slot: "gen_ed", status: "complete" },
                { title: "Principles of Microeconomics", term: 1, slot: "core", status: "complete" },
                { title: "Principles of Macroeconomics", term: 1, slot: "core", status: "ready" },
                { title: "Calculus for Business I", term: 1, slot: "core", status: "ready" },
                { title: "Financial Accounting", term: 1, slot: "core", status: "building" },
                { title: "English Composition II", term: 2, slot: "gen_ed", requires: ["English Composition I"], status: "locked" },
                { title: "College Algebra", term: 2, slot: "gen_ed", status: "locked" },
                { title: "American Government", term: 2, slot: "gen_ed", status: "locked" },
                { title: "Introduction to Psychology", term: 2, slot: "gen_ed", status: "locked" },
                { title: "Calculus for Business II", term: 3, slot: "core", requires: ["Calculus for Business I"], status: "locked" },
                { title: "Managerial Accounting", term: 3, slot: "core", requires: ["Financial Accounting"], status: "locked" },
                { title: "Business Law", term: 3, slot: "core", status: "locked" },
                { title: "Economic Statistics", term: 3, slot: "core", requires: ["Introduction to Statistics"], status: "locked" },
                { title: "Money and Banking", term: 4, slot: "elective", status: "choice" },
                { title: "International Economics", term: 4, slot: "elective", status: "choice" },
                { title: "Environmental Economics", term: 4, slot: "elective", status: "choice" },
                { title: "Economics Research Seminar", term: 4, slot: "capstone",
                  requires: ["Introduction to Statistics", "Principles of Microeconomics",
                             "Principles of Macroeconomics"], status: "locked" },
            ],
        };
    }

    /* The planner stores what it decided (chosen, built, course_uid); the map
       speaks in states. Deriving one from the other HERE keeps the derivation
       in one place and stops the UI inventing a status the data cannot support
       — notably "complete", which needs progress the plan does not carry, and
       "building", which would claim work is under way when nothing said so. */
    function withStatus(plan) {
        (plan.courses || []).forEach(function (c) {
            if (c.status) return;                     // demo plans set it directly
            if (c.chosen === false)      c.status = "choice";
            else if (c.built)            c.status = "ready";
            else                         c.status = "locked";
        });
        return plan;
    }

    function showEmpty(msg) {
        var e = document.getElementById("deg-empty");
        if (msg) e.textContent = msg;
        e.classList.remove("hidden");
        viewport.classList.add("hidden");
    }

    function loadPlan(uid) {
        currentUid = uid;
        fetch("/api/program/" + encodeURIComponent(uid))
            .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
            .then(function (p) { render(withStatus(p)); })
            .catch(function (err) {
                showEmpty("That programme could not be loaded (" + err.message +
                          "). Nothing has been lost — try again, or pick another " +
                          "from Courses.");
            });
    }

    function load() {
        var uid = new URLSearchParams(location.search).get("uid");
        if (uid) { loadPlan(uid); return; }

        // No programme named: open the most recent real one. The example plan
        // is only ever shown when there is genuinely nothing to show, and it
        // says so on the page — a preview that appears over a learner's real
        // programmes would be a lie about their own data.
        fetch("/api/programs")
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var list = (d && d.programs) || [];
                if (list.length) { loadPlan(list[0].uid); return; }
                render(demoPlan());
            })
            .catch(function () { render(demoPlan()); });
    }

    /* ---------------------------------------------------------- render */
    function el(tag, attrs) {
        var e = document.createElementNS(NS, tag);
        for (var k in attrs) e.setAttribute(k, attrs[k]);
        return e;
    }

    function render(plan) {
        var title = document.getElementById("deg-title");
        title.textContent = plan.subject + " — " +
            (plan.template === "bachelors" ? "Bachelor's" : "Associate");
        if (plan.demo) {
            document.getElementById("deg-sub").textContent =
                "Preview with example data — create a degree to see your own. " +
                "Terms run left to right; arrows are prerequisites.";
        }

        var byTerm = {};
        plan.courses.forEach(function (c) {
            (byTerm[c.term] = byTerm[c.term] || []).push(c);
        });
        var pos = {};   // title -> {x,y}
        var maxRows = 0;

        Object.keys(byTerm).forEach(function (t) {
            byTerm[t].forEach(function (c, row) {
                pos[c.title] = {
                    x: PAD + (c.term - 1) * COL_W,
                    y: PAD + 40 + row * ROW_H,
                };
                maxRows = Math.max(maxRows, row + 1);
            });
        });

        var W = PAD * 2 + plan.terms * COL_W;
        var H = PAD * 2 + 40 + maxRows * ROW_H;
        svg.setAttribute("viewBox", "0 0 " + W + " " + H);
        svg.textContent = "";

        // Term column headers
        for (var t = 1; t <= plan.terms; t++) {
            var th = el("text", { x: PAD + (t - 1) * COL_W + NODE_W / 2,
                                  y: PAD, class: "deg-term-label",
                                  "text-anchor": "middle" });
            th.textContent = "Term " + t;
            svg.appendChild(th);
        }

        // Edges first, under the nodes. A cubic curve from the right edge of
        // the prerequisite to the left edge of the dependent.
        plan.courses.forEach(function (c) {
            (c.requires || []).forEach(function (req) {
                var a = pos[req], b = pos[c.title];
                if (!a || !b) return;
                var x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
                var x2 = b.x, y2 = b.y + NODE_H / 2;
                var mx = (x1 + x2) / 2;
                svg.appendChild(el("path", {
                    d: "M" + x1 + " " + y1 + " C" + mx + " " + y1 + " " +
                       mx + " " + y2 + " " + x2 + " " + y2,
                    class: "deg-edge",
                }));
            });
        });

        // Nodes
        plan.courses.forEach(function (c) {
            var p = pos[c.title];
            var g = el("g", { class: "deg-node deg-" + (c.status || "locked"),
                              transform: "translate(" + p.x + "," + p.y + ")",
                              tabindex: "0", role: "img",
                              "aria-label": c.title + " — " + (c.status || "locked") });
            g.appendChild(el("rect", { width: NODE_W, height: NODE_H,
                                       rx: 10, class: "deg-node-box" }));
            var label = el("text", { x: 12, y: 26, class: "deg-node-title" });
            // Wrap long titles onto two lines by hand — SVG has no text wrap.
            var words = c.title.split(" ");
            var line1 = "", line2 = "";
            words.forEach(function (w) {
                if ((line1 + " " + w).trim().length <= 26 && !line2) {
                    line1 = (line1 + " " + w).trim();
                } else { line2 = (line2 + " " + w).trim(); }
            });
            var t1 = el("tspan", { x: 12, dy: 0 }); t1.textContent = line1;
            label.appendChild(t1);
            if (line2) {
                var t2 = el("tspan", { x: 12, dy: 18 });
                t2.textContent = line2.length > 26 ? line2.slice(0, 25) + "…" : line2;
                label.appendChild(t2);
            }
            g.appendChild(label);
            var slot = el("text", { x: 12, y: NODE_H - 10, class: "deg-node-slot" });
            slot.textContent = (c.slot || "") +
                (c.status === "building" ? " · building now" : "");
            g.appendChild(slot);
            if (c.status === "building") {
                g.appendChild(el("circle", { cx: NODE_W - 16, cy: 16, r: 5,
                                             class: "deg-node-pulse" }));
            }
            // A LOCKED COURSE KEEPS ITS NAME AND EXPLAINS ITSELF. Grey is the
            // state, not a secret: clicking says what has to finish first —
            // the specific prerequisite when one exists, otherwise the current
            // course — rather than a mute dead node.
            if ((c.status || "locked") === "locked") {
                g.addEventListener("click", function () {
                    var blockers = (c.requires || []).filter(function (r) {
                        var rc = plan.courses.find(function (x) { return x.title === r; });
                        return rc && rc.status !== "complete";
                    });
                    var why = blockers.length
                        ? "Locked until you finish " + blockers.join(" and ") + "."
                        : "Locked — finish your current course to unlock it. " +
                          "It will be built before you arrive.";
                    toast(c.title + ": " + why);
                });
            }
            svg.appendChild(g);
        });

        renderChoice(plan);
        fit();
    }

    /* The registration moment. */
    function renderChoice(plan) {
        var choices = plan.courses.filter(function (c) { return c.status === "choice"; });
        var box = document.getElementById("deg-choice");
        var cards = document.getElementById("deg-choice-cards");
        if (!choices.length) { box.classList.add("hidden"); return; }
        box.classList.remove("hidden");
        cards.textContent = "";
        choices.forEach(function (c) {
            var card = document.createElement("button");
            card.className = "deg-choice-card";
            var h = document.createElement("h3"); h.textContent = c.title;
            var p = document.createElement("p");
            p.textContent = "Term " + c.term + " elective. Choosing locks it " +
                "and starts its build.";
            card.appendChild(h); card.appendChild(p);
            card.addEventListener("click", function () {
                if (plan.demo) {
                    p.textContent = "Preview only — in a real programme this " +
                        "locks the choice and the build begins.";
                    return;
                }
                // currentUid, not the query string: the page reaches a
                // programme either by ?uid= or by resolving the most recent
                // one, and reading the URL meant the second path POSTed to
                // /api/program/null/choose.
                if (!currentUid) {
                    p.textContent = "Could not tell which programme this is — " +
                        "reload the page and try again.";
                    return;
                }
                card.disabled = true;
                fetch("/api/program/" + encodeURIComponent(currentUid) + "/choose",
                      { method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ title: c.title }) })
                    .then(function (r) {
                        if (!r.ok) throw new Error("HTTP " + r.status);
                        return r.json();
                    })
                    .then(function (d) {
                        // A 200 carrying {status:"missing"} is still a failure;
                        // reloading on it would silently discard the choice.
                        if (d && d.status && d.status !== "ok") {
                            throw new Error(d.status);
                        }
                        location.reload();
                    })
                    .catch(function (err) {
                        card.disabled = false;
                        p.textContent = "That choice did not save (" +
                            err.message + "). Nothing has changed — try again.";
                    });
            });
            cards.appendChild(card);
        });
    }

    /* ---------------------------------------------------------- pan & zoom */
    var view = { x: 0, y: 0, k: 1 };
    function apply() {
        svg.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.k + ")";
    }
    function fit() { view = { x: 0, y: 0, k: 1 }; apply(); }

    document.getElementById("deg-zoom-in").addEventListener("click", function () {
        view.k = Math.min(3, view.k * 1.25); apply();
    });
    document.getElementById("deg-zoom-out").addEventListener("click", function () {
        view.k = Math.max(.4, view.k / 1.25); apply();
    });
    document.getElementById("deg-zoom-fit").addEventListener("click", fit);

    viewport.addEventListener("wheel", function (e) {
        if (!e.ctrlKey && Math.abs(e.deltaY) < 40) return;  // let page scroll win
        e.preventDefault();
        view.k = Math.max(.4, Math.min(3, view.k * (e.deltaY < 0 ? 1.1 : 0.9)));
        apply();
    }, { passive: false });

    var drag = null;
    viewport.addEventListener("pointerdown", function (e) {
        drag = { x: e.clientX - view.x, y: e.clientY - view.y };
        viewport.setPointerCapture(e.pointerId);
        viewport.classList.add("dragging");
    });
    viewport.addEventListener("pointermove", function (e) {
        if (!drag) return;
        view.x = e.clientX - drag.x; view.y = e.clientY - drag.y; apply();
    });
    viewport.addEventListener("pointerup", function () {
        drag = null; viewport.classList.remove("dragging");
    });

    /* One transient toast; textContent only. */
    var toastEl = null, toastTimer = null;
    function toast(text) {
        if (!toastEl) {
            toastEl = document.createElement("div");
            toastEl.className = "deg-toast";
            toastEl.setAttribute("role", "status");
            document.querySelector(".degree-shell").appendChild(toastEl);
        }
        toastEl.textContent = text;
        toastEl.classList.add("show");
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toastEl.classList.remove("show"); }, 4200);
    }

    load();
})();
