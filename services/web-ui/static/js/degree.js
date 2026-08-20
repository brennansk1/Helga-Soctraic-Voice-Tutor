/* The degree viewer.
 *
 * WHAT A PROGRAMME ACTUALLY IS
 * ----------------------------
 * A fixed list of courses that together make the degree. The learner completes
 * all of them, chooses the ORDER, and studies exactly ONE at a time. The only
 * real constraint is the prerequisite graph: a course is available the moment
 * everything it requires is complete.
 *
 * That model is why this file no longer draws a term grid. `term` is still in
 * the plan JSON and is still useful as the planner's rough difficulty ordering,
 * but a "4 terms x 5 courses" layout says five courses run in parallel on a
 * calendar, and nothing in this product works that way — there is one learner,
 * one course, and a single-build lock underneath.
 *
 * WHY THIS IS NOT A NODE GRAPH EITHER
 * -----------------------------------
 * Measured on the two real planned programmes rather than assumed:
 *
 *   associate:  20 courses,  2 prerequisite edges — 16 isolated, 2 pairs
 *   bachelor's: 40 courses, 17 prerequisite edges — 19 isolated, 3 chains of
 *               6, 1 chain of 3; no chain crosses a requirement area
 *
 * A pannable DAG canvas spends its entire area drawing almost no structure, and
 * laying courses out by dependency depth collapses too: 18 of the associate's
 * 20 courses sit at depth 0, so "depth" is one tall column and a rounding
 * error. The honest shape of this data is a small number of short SEQUENCES
 * inside a mostly unordered set — which is exactly how a real catalogue prints
 * a degree. So: requirement areas, sequences drawn as sequences, and every gate
 * named in words instead of implied by a curve the eye has to trace.
 *
 * Data: GET /api/program/<uid>, the JSON plan_degree() emits
 * ({subject, template, terms, courses:[{title, term, slot, requires[], built,
 * course_uid, completed}]}).
 */
(function () {
    "use strict";

    // The programme currently rendered. Read from the URL when one is named,
    // otherwise resolved from /api/programs — either way the ONE place that
    // knows which programme an action applies to.
    var currentUid = null;

    /* Requirement areas, in the order a catalogue prints them. The plan's
       `slot` is the planner's vocabulary; these are the learner's. An unknown
       slot is humanised rather than dropped, because a slot this file has
       never heard of is still a course the learner has to pass. */
    var AREA_LABELS = {
        gen_ed: "General education",
        core: "Core requirements",
        elective: "Electives",
        capstone: "Capstone",
    };
    var AREA_ORDER = ["gen_ed", "core", "elective", "capstone"];

    /* ---------------------------------------------------------- data */
    function demoPlan() {
        // The real Economics associate plan from tools testing — real titles,
        // real prerequisite edges — so the design is exercised by real shape.
        return {
            subject: "Economics", template: "associate", terms: 4,
            demo: true,
            courses: [
                { title: "English Composition I", slot: "gen_ed", term: 1, completed: true, built: true },
                { title: "Introduction to Statistics", slot: "gen_ed", term: 1, completed: true, built: true },
                { title: "College Algebra", slot: "gen_ed", term: 1, completed: true, built: true },
                { title: "English Composition II", slot: "gen_ed", term: 2, requires: ["English Composition I"], built: true },
                { title: "American Government", slot: "gen_ed", term: 1 },
                { title: "Introduction to Psychology", slot: "gen_ed", term: 2 },
                { title: "Natural Science with Laboratory", slot: "gen_ed", term: 2 },
                { title: "Principles of Microeconomics", slot: "core", term: 1, current: true, built: true },
                { title: "Principles of Macroeconomics", slot: "core", term: 2, built: true },
                { title: "Calculus for Business and Economics I", slot: "core", term: 2, building: true },
                { title: "Calculus for Business and Economics II", slot: "core", term: 3,
                  requires: ["Calculus for Business and Economics I"] },
                { title: "Financial Accounting", slot: "core", term: 2 },
                { title: "Managerial Accounting", slot: "core", term: 3 },
                { title: "Business Law", slot: "core", term: 3 },
                { title: "Economic Statistics", slot: "core", term: 3,
                  requires: ["Introduction to Statistics"], built: true },
                { title: "Money and Banking", slot: "elective", term: 4 },
                { title: "International Economics", slot: "elective", term: 4 },
                { title: "Environmental Economics", slot: "elective", term: 4 },
                { title: "Economics Research Seminar", slot: "capstone", term: 4,
                  requires: ["Principles of Microeconomics", "Principles of Macroeconomics",
                             "Economic Statistics"] },
            ],
        };
    }

    function isComplete(c) {
        return c.completed === true || c.status === "complete";
    }

    /* The plan stores what the planner decided; the page speaks in states.
       Deriving one from the other HERE keeps the derivation in one place and
       stops the UI inventing a status the data cannot support — notably
       "complete", which needs progress the plan may not carry, and "building",
       which would claim work is under way when nothing said so.

       Older stored plans carry `chosen: false` on courses that used to be
       exclusive electives. Under the current model there is no such thing as an
       unchosen course, so the flag is simply ignored: those courses are part of
       the programme like every other, gated only by their prerequisites. */
    function derive(plan) {
        var courses = (plan.courses || []).slice();
        var byTitle = {};
        courses.forEach(function (c) { byTitle[c.title] = c; });

        var currentTaken = false;
        courses.forEach(function (c) {
            if (isComplete(c)) { c._state = "complete"; c._gates = []; return; }

            // A prerequisite the plan does not contain cannot be evidence of
            // anything, so it does not gate. Only courses in this programme do.
            c._gates = (c.requires || []).filter(function (r) {
                var p = byTitle[r];
                return p && !isComplete(p);
            });

            // Exactly one course can be in progress: one learner, one course at
            // a time. A second claimant falls back to its ordinary state rather
            // than splitting the page's attention between two "now"s.
            if (!currentTaken &&
                (c.current === true || c.in_progress === true ||
                 c.status === "current" || plan.current_title === c.title)) {
                currentTaken = true;
                c._state = "current";
                return;
            }

            // Building is NOT folded into the focus slot. The scheduler builds
            // one course ahead of the learner, so a course can legitimately be
            // generating while a different one is being studied — and an
            // earlier version of this file, which let the focus slot claim the
            // only building state, labelled that course "Available now" while
            // Helga was still writing it.
            if (c.building === true || c.status === "building") {
                c._state = "building";
                return;
            }
            c._state = c._gates.length ? "locked" : "available";
        });

        plan._byTitle = byTitle;
        plan._courses = courses;
        return plan;
    }

    /* Connected components over the prerequisite edges. A component with more
       than one member is a SEQUENCE — the only genuine structure in the plan —
       and is drawn as one. Everything else is a track of one. */
    function tracks(plan) {
        var parent = {};
        function find(x) {
            while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
            return x;
        }
        plan._courses.forEach(function (c) { parent[c.title] = c.title; });
        plan._courses.forEach(function (c) {
            (c.requires || []).forEach(function (r) {
                if (!(r in parent)) return;
                var a = find(r), b = find(c.title);
                if (a !== b) parent[a] = b;
            });
        });

        // How many courses must precede this one — the order inside a sequence.
        var depths = {};
        function depth(title, seen) {
            if (title in depths) return depths[title];
            var c = plan._byTitle[title];
            if (!c || seen.indexOf(title) !== -1) return 0;
            var reqs = (c.requires || []).filter(function (r) { return r in plan._byTitle; });
            var d = 0;
            reqs.forEach(function (r) { d = Math.max(d, 1 + depth(r, seen.concat([title]))); });
            depths[title] = d;
            return d;
        }

        var groups = {};
        plan._courses.forEach(function (c) {
            var k = find(c.title);
            (groups[k] = groups[k] || []).push(c);
        });
        return Object.keys(groups).map(function (k) {
            var members = groups[k].slice().sort(function (a, b) {
                return depth(a.title, []) - depth(b.title, []) ||
                       (a.term || 0) - (b.term || 0) ||
                       a.title.localeCompare(b.title);
            });
            return members;
        });
    }

    function areaRank(slot) {
        var i = AREA_ORDER.indexOf(slot || "");
        return i < 0 ? 99 : i;
    }

    function areaLabel(slot) {
        if (!slot) return "Programme courses";
        if (AREA_LABELS[slot]) return AREA_LABELS[slot];
        return slot.replace(/_/g, " ").replace(/^./, function (m) { return m.toUpperCase(); });
    }

    function showEmpty(msg) {
        var e = document.getElementById("deg-empty");
        if (msg) e.textContent = msg;
        e.classList.remove("hidden");
        ["deg-progress", "deg-focus", "deg-next", "deg-plan"].forEach(function (id) {
            document.getElementById(id).hidden = true;
        });
    }

    function loadPlan(uid) {
        currentUid = uid;
        fetch("/api/program/" + encodeURIComponent(uid))
            .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
            .then(function (p) { render(p); })
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
        //
        // Which means "no programmes" and "could not ask" must be told apart,
        // and the list alone does not tell them apart. When web-ui cannot reach
        // core it answers HTTP 200 with {programs: [], error: "unavailable"} —
        // an empty list that means nothing of the sort. Falling through drew a
        // FABRICATED bachelor's map over a learner's real degree, labelled only
        // "Preview with example data" in the subtitle. So: the error field, a
        // non-ok status, a body that is not a list, and a network failure are
        // all "could not load", and only a clean empty list earns the example.
        fetch("/api/programs")
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function (d) {
                if (!d || d.error) throw new Error((d && d.error) || "no reply");
                if (!Array.isArray(d.programs)) throw new Error("unreadable reply");
                if (d.programs.length) { loadPlan(d.programs[0].uid); return; }
                render(demoPlan());
            })
            .catch(function (err) {
                showEmpty("Your programmes could not be loaded (" + err.message +
                          "). This is not a statement that you have none — " +
                          "reload once the tutor service is back.");
            });
    }

    /* ---------------------------------------------------------- helpers */
    function elem(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        // textContent, always: course titles come from a model and a title
        // containing "<" must render as a title, not as markup.
        if (text !== undefined && text !== null) e.textContent = text;
        return e;
    }

    function icon(name) {
        var i = elem("span", "i i-" + name);
        i.setAttribute("aria-hidden", "true");
        return i;
    }

    /* One line per state, in the learner's terms.
       `built` is orthogonal to availability: a course can be open to start and
       not yet generated. On the DECISION surface that difference is the offer
       — "ready now" against "Helga builds it before you arrive" — so it is
       spelled out. In the plan below, where the same sentence would repeat
       fifteen times down a column and stop being read, availability alone is
       the fact worth printing. */
    function stateNote(c, prominent) {
        if (c._state === "complete") return "Complete";
        if (c._state === "current") return "In progress";
        if (c._state === "building") {
            // A course being built ahead of the learner may still be gated.
            // Saying only "building" would imply it is theirs to start.
            return c._gates && c._gates.length
                ? "Building now — unlocks after " + c._gates.join(" and ")
                : "Building now";
        }
        if (c._state === "available") {
            if (!prominent) return "Available now";
            return c.built ? "Ready to start now" : "Built when you choose it";
        }
        if (c._gates && c._gates.length) {
            return "Unlocks after " + c._gates.join(" and ");
        }
        return "Locked";
    }

    /* On the decision surface an available course gets an arrow, because there
       the card is a control and the arrow is its "go". In the plan the same
       course is a record, not a button, so it gets the neutral mark the legend
       uses — an arrow that leads nowhere is a promise the page does not keep. */
    var STATE_ICON = {
        complete: "check", current: "book", building: "build",
        available: "dot", locked: "lock",
    };
    var ACTION_ICON = "arrow-right";

    /* ---------------------------------------------------------- render */
    function render(plan) {
        derive(plan);

        var level = plan.template === "bachelors" ? "Bachelor's" : "Associate";
        document.getElementById("deg-title").textContent =
            (plan.subject || "Programme") + " — " + level;

        var preview = document.getElementById("deg-preview");
        preview.textContent = "";
        preview.hidden = !plan.demo;
        if (plan.demo) {
            preview.appendChild(icon("spark"));
            preview.appendChild(document.createTextNode(
                "An example degree, so you can see how a programme reads. " +
                "Create one of your own from the Create page."));
        }

        renderProvenance(plan);
        renderProgress(plan);
        renderFocus(plan);
        renderNext(plan);
        renderAreas(plan);
    }

    function renderProvenance(plan) {
        var host = document.getElementById("deg-provenance");
        if (!host) return;
        host.textContent = "";
        if (plan.demo || !plan.curriculum_source) { host.hidden = true; return; }
        host.hidden = false;
        host.classList.toggle("is-proposed", plan.authoritative === false);

        var tag = elem("span", "deg-prov-tag", plan.authoritative
            ? "Published curriculum" : "Model-proposed");
        host.appendChild(tag);

        // The planner's own note when it has one -- it says the useful thing
        // (that each course is still evidence-gated) better than a generic
        // disclaimer would.
        host.appendChild(elem("span", "deg-prov-text",
            plan.note ? plan.note : (plan.reference || plan.curriculum_source)));
    }

    function counts(plan) {
        var n = { complete: 0, current: 0, building: 0, available: 0, locked: 0 };
        plan._courses.forEach(function (c) { n[c._state] += 1; });
        n.total = plan._courses.length;
        return n;
    }

    function renderProgress(plan) {
        var n = counts(plan);
        var host = document.getElementById("deg-progress");
        host.hidden = false;

        // The COUNT is the progress statement. A percentage bar alone tells a
        // learner how they feel; "3 of 20" tells them where they are.
        var count = document.getElementById("deg-count");
        count.textContent = "";
        count.appendChild(elem("strong", null, String(n.complete)));
        count.appendChild(document.createTextNode(
            " of " + n.total + " courses complete"));

        var pct = n.total ? Math.round(n.complete / n.total * 100) : 0;
        document.getElementById("deg-meter-fill").style.width = pct + "%";
        document.getElementById("deg-meter").setAttribute(
            "aria-label", n.complete + " of " + n.total + " courses complete");

        // Everything else is a tally, not a bar segment: four coloured slivers
        // in one track cannot be read, and three of these four numbers are not
        // progress at all.
        var tally = document.getElementById("deg-tally");
        tally.textContent = "";
        [["current", n.current, "in progress"],
         ["building", n.building, "building"],
         ["available", n.available, "available now"],
         ["locked", n.locked, "locked"]].forEach(function (row) {
            if (!row[1]) return;
            var li = elem("li", "degree-tally-item is-" + row[0]);
            li.appendChild(elem("span", "degree-key is-" + row[0]));
            li.appendChild(elem("span", null, row[1] + " " + row[2]));
            tally.appendChild(li);
        });
    }

    /* 1. ONE COURSE AT A TIME. */
    function renderFocus(plan) {
        var section = document.getElementById("deg-focus");
        var body = document.getElementById("deg-focus-body");
        var n = counts(plan);
        body.textContent = "";
        section.hidden = false;

        // The course being studied owns the slot. Only when nothing is in
        // progress does a build get to stand in it — then "what is happening
        // right now" genuinely is Helga writing a course.
        var focus = plan._courses.filter(function (c) { return c._state === "current"; })[0] ||
                    plan._courses.filter(function (c) { return c._state === "building"; })[0];

        if (!focus) {
            // Not an error state and not a shrug: the learner has a programme
            // and a decision waiting, so this says what to do next.
            document.getElementById("deg-focus-h").textContent =
                n.complete ? "Nothing in progress" : "Ready to begin";
            var card = elem("div", "degree-focus-card is-empty");
            card.appendChild(elem("p", "degree-focus-lead",
                n.complete
                    ? "You have finished " + n.complete + " " +
                      (n.complete === 1 ? "course" : "courses") +
                      ". Pick the next one when you are ready."
                    : "You take one course at a time, and the order is yours. " +
                      "Anything with its prerequisites behind it is open to you."));
            var go = elem("button", "degree-focus-action", n.available
                ? "See the " + n.available + " courses open to you"
                : "See your programme");
            go.type = "button";
            go.addEventListener("click", function () {
                var target = document.getElementById(n.available ? "deg-next" : "deg-plan");
                target.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth",
                                        block: "start" });
            });
            card.appendChild(go);
            body.appendChild(card);
            return;
        }

        document.getElementById("deg-focus-h").textContent = "Now studying";
        var box = elem("div", "degree-focus-card is-" + focus._state);
        box.appendChild(elem("p", "degree-focus-area", areaLabel(focus.slot)));
        box.appendChild(elem("h3", "degree-focus-name", focus.title));

        if (focus._state === "building") {
            box.appendChild(elem("p", "degree-focus-lead",
                "Helga is writing this course now. It will be waiting when it " +
                "is done — you do not have to sit here."));
            var bar = elem("div", "degree-build-bar");
            bar.setAttribute("role", "progressbar");
            bar.setAttribute("aria-label", "Building " + focus.title);
            box.appendChild(bar);
        } else {
            box.appendChild(elem("p", "degree-focus-lead",
                "One course at a time. Finish this one and the courses it " +
                "unlocks open up."));
            if (focus.course_uid) {
                var open = elem("a", "degree-focus-action", "Continue this course");
                open.href = "/learn?course_uid=" + encodeURIComponent(focus.course_uid);
                box.appendChild(open);
            }
        }
        body.appendChild(box);
    }

    /* 2. THE DECISION. */
    function renderNext(plan) {
        var section = document.getElementById("deg-next");
        var grid = document.getElementById("deg-next-grid");
        grid.textContent = "";

        var open = plan._courses.filter(function (c) { return c._state === "available"; });
        if (!open.length) {
            section.hidden = true;
            return;
        }
        section.hidden = false;

        // The promise belongs HERE, said once, rather than repeated on all
        // twenty-three cards until it stops being read.
        document.getElementById("deg-next-sub").textContent =
            open.length + " " + (open.length === 1 ? "course is" : "courses are") +
            " open to you — every prerequisite they have is already behind you. " +
            "Pick one and Helga builds it before you arrive.";

        // A course Helga has already built can start this second, so those come
        // first; after that the list runs in requirement-area order, because at
        // forty courses an unsorted wall of twenty-three is not a decision, it
        // is a search. Term is the planner's rough difficulty hint and breaks
        // the remaining ties.
        open.sort(function (a, b) {
            return (b.built ? 1 : 0) - (a.built ? 1 : 0) ||
                   areaRank(a.slot) - areaRank(b.slot) ||
                   (a.term || 0) - (b.term || 0) ||
                   a.title.localeCompare(b.title);
        }).forEach(function (c) {
            grid.appendChild(courseCard(plan, c, true));
        });
    }

    /* 3 & 4. THE WHOLE DEGREE, grouped the way a catalogue groups one. */
    function renderAreas(plan) {
        var host = document.getElementById("deg-areas");
        document.getElementById("deg-plan").hidden = false;
        host.textContent = "";

        var allTracks = tracks(plan);
        var areas = {};
        allTracks.forEach(function (t) {
            // A sequence never crossed a requirement area in either real plan;
            // if one ever does, its first course decides where it is filed
            // rather than the sequence being torn in half.
            var slot = t[0].slot || "";
            (areas[slot] = areas[slot] || []).push(t);
        });

        Object.keys(areas).sort(function (a, b) {
            return areaRank(a) - areaRank(b) || a.localeCompare(b);
        }).forEach(function (slot) {
            var group = areas[slot];
            var flat = [];
            group.forEach(function (t) { flat = flat.concat(t); });
            var done = flat.filter(function (c) { return c._state === "complete"; }).length;

            var area = elem("section", "degree-area");
            var head = elem("div", "degree-area-head");
            head.appendChild(elem("h3", "degree-area-name", areaLabel(slot)));
            head.appendChild(elem("p", "degree-area-count",
                done + " of " + flat.length + " complete"));
            var meter = elem("div", "degree-area-meter");
            var fill = elem("span", "degree-area-fill");
            fill.style.width = (flat.length ? Math.round(done / flat.length * 100) : 0) + "%";
            meter.appendChild(fill);
            head.appendChild(meter);
            area.appendChild(head);

            var wrap = elem("div", "degree-tracks");
            // Longest sequences first: they carry the area's structure, and a
            // track of one reads fine wherever it lands.
            group.sort(function (a, b) {
                return b.length - a.length ||
                       (a[0].term || 0) - (b[0].term || 0) ||
                       a[0].title.localeCompare(b[0].title);
            }).forEach(function (t) {
                if (t.length === 1) {
                    var single = elem("div", "degree-track");
                    single.appendChild(courseCard(plan, t[0], false));
                    wrap.appendChild(single);
                    return;
                }
                // A sequence gets a band of its own across the full width, and
                // runs left to right. Stacked in one grid cell it left three
                // columns of dead space beside it on the 40-course plan and
                // buried the free courses six rows down.
                var band = elem("div", "degree-track is-sequence");
                band.appendChild(elem("p", "degree-track-label", "Take in order"));
                var flow = elem("div", "degree-track-flow");
                t.forEach(function (c) { flow.appendChild(courseCard(plan, c, false)); });
                band.appendChild(flow);
                wrap.appendChild(band);
            });
            area.appendChild(wrap);
            host.appendChild(area);
        });

        markScrollableSequences();
        // A chain that fits at 1280 will not at 800, so the hint is measured
        // again on resize rather than decided once at render. Removed first:
        // render() can run more than once per page.
        window.removeEventListener("resize", markScrollableSequences);
        window.addEventListener("resize", markScrollableSequences);
    }

    /* Mark only the sequences whose chain is genuinely wider than its band, so
       a two-course sequence is never given a scroll hint it does not need. */
    function markScrollableSequences() {
        var bands = document.querySelectorAll(".degree-track.is-sequence");
        [].forEach.call(bands, function (band) {
            var flow = band.querySelector(".degree-track-flow");
            if (!flow) return;
            band.classList.toggle("has-more", flow.scrollWidth - flow.clientWidth > 4);
        });
    }

    /* One card, one course. `prominent` is the decision surface, where a card
       is a control; in the plan below it the same course is a record. */
    function courseCard(plan, c, prominent) {
        var actionable = prominent && c._state === "available";
        // Whether a course EXISTS YET is a different fact from its state, and
        // the learner needs both: "available" says you may start it, "built"
        // says it is already written and opens immediately rather than after
        // a build. Prose alone carried this, which meant it read only if you
        // read — a class makes it visible at a glance.
        var card = elem(actionable ? "button" : "div",
                        "degree-course is-" + c._state
                        + (c.built ? " is-built" : " is-unbuilt")
                        + (prominent ? " is-prominent" : ""));
        if (actionable) card.type = "button";

        if (prominent) {
            // The decision surface has room to say which requirement this
            // satisfies, and puts the arrow on the right where a control's
            // "go" lives rather than in front of the label.
            var head = elem("div", "degree-course-head");
            head.appendChild(elem("span", "degree-course-area", areaLabel(c.slot)));
            // A built course opens instantly; an unbuilt one costs a build
            // first. That is the difference between "start now" and "start in
            // forty minutes", so it earns a mark of its own.
            if (c.built && c._state === "available") {
                head.appendChild(elem("span", "degree-built-chip", "Ready"));
            }
            head.appendChild(icon(actionable ? ACTION_ICON : (STATE_ICON[c._state] || "dot")));
            card.appendChild(head);
            // h3 here, h4 in the plan: the decision cards sit directly under
            // the section's h2, while a plan card sits under its area's h3.
            // The heading level follows the nesting rather than the styling.
            card.appendChild(elem("h3", "degree-course-title", c.title));
        } else {
            // In the plan the state icon rides on the title line: a card whose
            // first line is an icon and nothing else wastes a row of height
            // twenty times over.
            var line = elem("div", "degree-course-line");
            line.appendChild(icon(STATE_ICON[c._state] || "dot"));
            line.appendChild(elem("h4", "degree-course-title", c.title));
            card.appendChild(line);
        }

        var note = elem("p", "degree-course-note", stateNote(c, prominent));
        // "Ready now" is a stronger offer than "will be built", and only the
        // stronger one gets the accent.
        if (prominent && c._state === "available" && !c.built) note.className += " is-quiet";
        card.appendChild(note);

        if (c._state === "building") card.appendChild(elem("div", "degree-build-bar"));

        if (actionable) {
            card.addEventListener("click", function () { start(plan, c, card); });
        }
        return card;
    }

    /* Starting a course. A course that already exists opens; one that does not
       is requested, and the plan reloads showing it as the thing being built.
       The endpoint is still /choose — under the old model it locked an elective,
       under this one it records which course the learner is taking next, which
       is the same write. */
    function start(plan, c, card) {
        if (plan.demo) {
            card.querySelector(".degree-course-note").textContent =
                "Example programme — in your own, this starts the course.";
            return;
        }
        if (c.built && c.course_uid) {
            location.href = "/learn?course_uid=" + encodeURIComponent(c.course_uid);
            return;
        }
        // currentUid, not the query string: the page reaches a programme either
        // by ?uid= or by resolving the most recent one, and reading the URL
        // meant the second path POSTed to /api/program/null/choose.
        var note = card.querySelector(".degree-course-note");
        if (!currentUid) {
            note.textContent = "Could not tell which programme this is — " +
                "reload the page and try again.";
            return;
        }
        card.disabled = true;
        note.textContent = "Asking Helga to build it…";
        fetch("/api/program/" + encodeURIComponent(currentUid) + "/choose",
              { method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: c.title }) })
            .then(function (r) {
                return r.json().then(function (b) {
                    return { ok: r.ok, status: r.status, body: b };
                });
            })
            .then(function (res) {
                var d = res.body || {};
                if (!res.ok) {
                    // These two are answers, not faults, and deserve their own
                    // words: one course at a time is the model, and a locked
                    // course is locked for a reason the learner can act on.
                    if (d.reason === "build_in_progress") {
                        throw new Error("a course is already being built — " +
                                        "one at a time");
                    }
                    if (d.reason === "prerequisites_unmet") {
                        throw new Error("finish " +
                                        (d.requires || []).join(" and ") + " first");
                    }
                    throw new Error(d.error || "HTTP " + res.status);
                }
                if (d.status && d.status !== "ok") throw new Error(d.status);

                // Choosing a course STARTS it. Go watch it being built — the
                // page that exists for exactly that — rather than reloading
                // the plan and leaving the learner to guess what happened.
                if (d.building) {
                    if (window.HelgaBuildGuard) window.HelgaBuildGuard.set(c.title);
                    location.href = "/build";
                    return;
                }
                // Already built: open it.
                if (d.course_uid) {
                    location.href = "/learn?course_uid=" +
                        encodeURIComponent(d.course_uid);
                    return;
                }
                location.reload();
            })
            .catch(function (err) {
                card.disabled = false;
                note.textContent = "Could not start it — " + err.message + ".";
            });
    }

    function prefersReducedMotion() {
        return window.matchMedia &&
               window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    }

    load();
})();
