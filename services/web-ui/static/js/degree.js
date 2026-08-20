/* The degree viewer.
 *
 * WHAT A PROGRAMME ACTUALLY IS
 * ----------------------------
 * A fixed list of courses that together make the degree. The learner completes
 * all of them, chooses the ORDER, and studies exactly ONE at a time. The only
 * real constraint is the prerequisite graph: a course is available the moment
 * everything it requires is complete.
 *
 * That model is why this file does not draw a term grid. `term` is still in the
 * plan JSON and is still useful as the planner's rough difficulty ordering, but
 * a "4 terms x 5 courses" layout says five courses run in parallel on a
 * calendar, and nothing in this product works that way — there is one learner,
 * one course, and a single-build lock underneath.
 *
 * WHY THIS IS NOT A NODE GRAPH EITHER
 * -----------------------------------
 * Measured on the real planned programmes rather than assumed:
 *
 *   associate:  19 courses,  2 prerequisite edges
 *   bachelor's: 40 courses, 17 prerequisite edges — 4 sequences, the rest
 *               isolated; no sequence crosses a requirement area
 *
 * A pannable DAG canvas spends its entire area drawing almost no structure. The
 * honest shape of this data is a small number of short SEQUENCES inside a mostly
 * unordered set — which is how a real catalogue prints a degree.
 *
 * WHY IT IS A DEGREE AUDIT
 * ------------------------
 * The reference product is the audit worksheet every student has actually used.
 * Three things make one an audit rather than a checklist, and this page had
 * none of them:
 *
 *   1. Every requirement block carries an explicit VERDICT. "0 of 7 complete"
 *      is a count; "Not started" is an answer. A learner should not have to do
 *      the subtraction to find out whether an area is done.
 *   2. Every unfinished block says what is STILL NEEDED, in a sentence. That
 *      one line is what makes an audit actionable instead of descriptive.
 *   3. A KEY explains the marks, including the built/not-built axis, which this
 *      page draws on every row and card and had never explained anywhere.
 *
 * And one thing from the modern successors worth taking: name what BLOCKS
 * progress, derived from the prerequisite graph rather than asserted.
 *
 * Data: GET /api/program/<uid>, the JSON plan_degree() emits
 * ({subject, template, terms, courses:[{title, term, slot, requires[], built,
 * course_uid, completed, size}], size}).
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
        /* NOT "Electives". The stored slot key is still `elective` because
           existing plans carry it, but under the current model nothing here is
           optional: the learner completes every course in the programme and
           chooses only the ORDER. Calling these electives told them they could
           skip some, which is the one thing the model does not allow. */
        elective: "Advanced study",
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
            return groups[k].slice().sort(function (a, b) {
                return depth(a.title, []) - depth(b.title, []) ||
                       (a.term || 0) - (b.term || 0) ||
                       a.title.localeCompare(b.title);
            });
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

    /* Requirement blocks, in catalogue order, each carrying both its flat course
       list (for the verdict) and its tracks (for the drawing). Built once per
       render and shared by the summary band and the blocks themselves, so the
       two can never disagree about what is in an area. */
    function areaGroups(plan) {
        var byslot = {};
        tracks(plan).forEach(function (t) {
            // A sequence never crossed a requirement area in either real plan;
            // if one ever does, its first course decides where it is filed
            // rather than the sequence being torn in half.
            var slot = t[0].slot || "";
            (byslot[slot] = byslot[slot] || []).push(t);
        });
        return Object.keys(byslot).sort(function (a, b) {
            return areaRank(a) - areaRank(b) || a.localeCompare(b);
        }).map(function (slot) {
            var group = byslot[slot];
            // Longest sequences first: they carry the area's structure, and a
            // track of one reads fine wherever it lands.
            group.sort(function (a, b) {
                return b.length - a.length ||
                       (a[0].term || 0) - (b[0].term || 0) ||
                       a[0].title.localeCompare(b[0].title);
            });
            var flat = [];
            group.forEach(function (t) { flat = flat.concat(t); });
            return { slot: slot, label: areaLabel(slot), tracks: group, courses: flat,
                     id: "deg-block-" + (slot || "other").replace(/[^\w-]/g, "") };
        });
    }

    /* ------------------------------------------------- the audit verdicts */

    /* THE BLOCK VERDICT — the thing that makes this an audit.
     *
     * DegreeWorks prints three: Complete, Not complete, and "Complete except
     * for in-progress classes". Two of those are taken as they stand; the third
     * is split, because "Not complete" says the same word about a block where
     * eleven of twelve are done and one where nothing has been touched, and
     * this product has the data to tell them apart. So four, and each one is a
     * state a learner can act on differently.
     */
    function verdict(list) {
        var n = { complete: 0, current: 0, building: 0, available: 0, locked: 0 };
        list.forEach(function (c) { n[c._state] += 1; });
        var total = list.length;
        if (!total) return { key: "none", label: "Nothing required" };
        if (n.complete === total) return { key: "complete", label: "Complete" };
        // The audit's own wording, kept verbatim: the block is finished apart
        // from work already under way, which is a materially different position
        // from having courses still to choose.
        if (n.complete + n.current === total && n.current) {
            return { key: "in-progress", label: "Complete except for in-progress" };
        }
        if (n.complete || n.current || n.building) {
            return { key: "partial", label: "In progress" };
        }
        return { key: "not-started", label: "Not started" };
    }

    /* Credits are printed only where they can also say whether they were
       measured. `estimated` rides with the number rather than being assumed. */
    function creditsOf(list) {
        var cr = 0, est = false, any = false;
        list.forEach(function (c) {
            if (!c.size || !c.size.credits) return;
            any = true;
            cr += c.size.credits;
            if (c.size.estimated) est = true;
        });
        return any ? { credits: cr, estimated: est } : null;
    }

    function fmtCr(n) {
        return String(Math.round(n * 10) / 10).replace(/\.0$/, "");
    }

    function plural(n, one, many) {
        return n + " " + (n === 1 ? one : many);
    }

    /* THE "STILL NEEDED" LINE.
     *
     * The single most useful sentence a degree audit prints, and this page had
     * no equivalent of it. Every number in it is counted off the derived state
     * of the courses in the block — nothing here is a target, a projection or a
     * guess. If it cannot be counted it is not said.
     */
    function stillNeeded(block) {
        var rem = block.courses.filter(function (c) { return c._state !== "complete"; });
        if (!rem.length) return null;
        var n = { current: 0, building: 0, available: 0, locked: 0 };
        rem.forEach(function (c) { n[c._state] += 1; });
        return { count: rem.length, n: n, credits: creditsOf(rem) };
    }

    /* The openness breakdown, as clauses. Ordered by how close each group is to
       being startable, so the actionable part of the sentence comes first. */
    function stillNeededClauses(s) {
        var parts = [];
        if (s.n.current) {
            parts.push(s.n.current === 1 ? "1 is in progress"
                                         : s.n.current + " are in progress");
        }
        if (s.n.available) parts.push(s.n.available + " open to you now");
        if (s.n.building) {
            parts.push(s.n.building === 1 ? "1 being built now"
                                          : s.n.building + " being built now");
        }
        if (s.n.locked) {
            parts.push(s.n.locked === 1 ? "1 locked behind an earlier course"
                                        : s.n.locked + " locked behind earlier courses");
        }
        return parts;
    }

    function joinClauses(parts) {
        if (parts.length <= 1) return parts.join("");
        return parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
    }

    /* WHAT BLOCKS PROGRESS. Every locked course names the courses gating it, so
       the reverse index — which unfinished course would open the most — is a
       count, not a model. Nothing is predicted; it is arithmetic over the same
       edges the rows already show. */
    function blockers(plan) {
        var tally = {};
        plan._courses.forEach(function (c) {
            if (c._state !== "locked") return;
            (c._gates || []).forEach(function (g) { tally[g] = (tally[g] || 0) + 1; });
        });
        return Object.keys(tally).map(function (t) {
            return { title: t, unlocks: tally[t] };
        }).sort(function (a, b) {
            return b.unlocks - a.unlocks || a.title.localeCompare(b.title);
        });
    }

    function showEmpty(msg) {
        var e = document.getElementById("deg-empty");
        if (msg) e.textContent = msg;
        e.classList.remove("hidden");
        ["deg-progress", "deg-focus", "deg-views", "deg-audit", "deg-next"]
            .forEach(function (id) {
                var el = document.getElementById(id);
                if (el) el.hidden = true;
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
       spelled out. In the audit below, where the same sentence would repeat
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
       the card is a control and the arrow is its "go". In the audit the same
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

        var blocks = areaGroups(plan);

        renderProvenance(plan);
        renderProgress(plan);
        renderFocus(plan);
        renderNext(plan);
        renderSummary(plan, blocks);
        renderBlockers(plan);
        renderAreas(plan, blocks);
        setupViews(plan);
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

        // The credit-hour equivalent, because "60 credits" is a size a person
        // already knows and "20 courses" is not. Taken from the plan's own
        // `size` block rather than recomputed here, and marked as an estimate
        // while it still is one — a figure that cannot say whether it was
        // measured is not worth printing.
        var old = host.querySelector(".degree-credits");
        if (old) old.parentNode.removeChild(old);
        var size = plan.size;
        if (size && size.credits_total) {
            var sub = elem("p", "degree-credits");
            sub.appendChild(document.createTextNode(
                size.credits_complete + " of " + size.credits_total +
                " credit hours · about " +
                size.hours_total.toLocaleString() + " hours of study"));
            if (size.estimated_share >= 0.5) {
                sub.appendChild(elem("span", "degree-credits-est",
                                     "estimated until built"));
            }
            count.parentNode.insertBefore(sub, count.nextSibling);

            /* SAY WHY THE NUMBER IS SMALL.
               A learner who declined general education sees 84 credits where a
               university would say 120. Printing the smaller figure on its own
               would either look like a bug or quietly overstate what this
               programme is; naming the choice does neither, and it is the
               reason the credit count is worth printing at all. */
            if (size.general_education === "skip" && size.full_credits) {
                sub.appendChild(elem("span", "degree-credits-note",
                    "general education not included — a full "
                    + (plan.template === "bachelors" ? "bachelor's" : "associate")
                    + " is " + size.full_credits + " credit hours over "
                    + size.full_courses + " courses"));
            } else if (size.general_education === "transferred"
                       && size.transferred_courses) {
                sub.appendChild(elem("span", "degree-credits-note",
                    size.transferred_courses
                    + " general-education courses counted as already complete"));
            }
        }

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

    /* ONE COURSE AT A TIME — and it stays above the view switcher, because
       "what am I in the middle of" should not be behind a tab. */
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
            if (n.available) {
                var go = elem("button", "degree-focus-action",
                              "See the " + n.available + " courses open to you");
                go.type = "button";
                // Switching view rather than scrolling: the decision now has a
                // surface of its own, and sending the learner scrolling past
                // the audit to reach it would undo the point of the split.
                go.addEventListener("click", function () { showView("next", true); });
                card.appendChild(go);
            }
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

    /* ------------------------------------------------ the audit: summary */

    /* The whole degree in one band, above everything. Four blocks, four
       verdicts, four "still needed" lines — so the structure of the degree is
       legible at scroll position zero instead of 1.8 screens down. Each card is
       the anchor into its own block. */
    function renderSummary(plan, blocks) {
        var host = document.getElementById("deg-summary");
        host.textContent = "";

        blocks.forEach(function (b) {
            var v = verdict(b.courses);
            var done = b.courses.filter(function (c) { return c._state === "complete"; }).length;

            var card = elem("button", "degree-sum is-" + v.key);
            card.type = "button";

            var top = elem("div", "degree-sum-top");
            top.appendChild(elem("span", "degree-sum-name", b.label));
            top.appendChild(elem("span", "degree-sum-verdict", v.label));
            card.appendChild(top);

            card.appendChild(elem("p", "degree-sum-count",
                done + " of " + b.courses.length + " complete"));

            var meter = elem("div", "degree-sum-meter");
            var fill = elem("span", "degree-sum-fill");
            fill.style.width = (b.courses.length
                ? Math.round(done / b.courses.length * 100) : 0) + "%";
            meter.appendChild(fill);
            card.appendChild(meter);

            var s = stillNeeded(b);
            var line = elem("p", "degree-sum-need");
            if (!s) {
                line.className += " is-done";
                line.appendChild(icon("check"));
                line.appendChild(document.createTextNode("Requirement satisfied"));
            } else {
                // The short form here, the full sentence in the block itself:
                // a card this narrow that carries the whole clause list stops
                // being scannable, which is the only thing it is for.
                var lead = plural(s.count, "course", "courses") + " still needed";
                var open = s.n.available;
                line.textContent = open ? lead + " · " + open + " open now" : lead;
            }
            card.appendChild(line);

            card.addEventListener("click", function () { revealBlock(b.id); });
            host.appendChild(card);
        });
    }

    /* Stellic's useful idea, kept honest: say what is actually in the way. The
       number is a count of locked courses naming that course as a gate, so it
       can be checked against the rows below it. */
    function renderBlockers(plan) {
        var host = document.getElementById("deg-blockers");
        host.textContent = "";
        var n = counts(plan);
        var remaining = n.total - n.complete;
        if (!remaining) {
            host.hidden = false;
            host.className = "degree-blockers is-clear";
            host.appendChild(icon("check"));
            host.appendChild(document.createTextNode(
                "Every requirement is complete."));
            return;
        }

        var b = blockers(plan);
        host.hidden = false;
        if (!b.length) {
            host.className = "degree-blockers is-clear";
            host.appendChild(icon("check"));
            host.appendChild(document.createTextNode(
                "Nothing blocks you — all " + remaining + " remaining " +
                (remaining === 1 ? "course is" : "courses are") +
                " open to take in whatever order you like."));
            return;
        }

        host.className = "degree-blockers";
        host.appendChild(icon("lock"));
        var top = b[0];
        var text = "Finishing " + top.title + " would open " +
                   plural(top.unlocks, "more course", "more courses") + ".";
        if (b.length > 1) {
            text += " " + plural(b.length - 1, "other course gates", "other courses gate") +
                    " the rest.";
        }
        host.appendChild(document.createTextNode(text));
    }

    /* ------------------------------------------------- the audit: blocks */

    /* THE WORKSHEET. Requirement blocks of ROWS, not a grid of cards.
     *
     * At forty courses the card grid was a wall: four columns of boxes with the
     * sequence bands cutting across them, and the whole degree took 1.5 screens
     * of its own. A row is what an audit actually prints — mark, name, what
     * satisfied it or what would, credits — and forty of them are scannable
     * where forty cards are not. The card survives where a card belongs: on the
     * decision surface, where it is a control rather than a record.
     *
     * Blocks are <details> so a satisfied one can be folded away, which is what
     * a real audit does with a block it has nothing left to say about. Native
     * disclosure rather than a hand-rolled one: it is keyboard-operable and
     * findable by in-page search without any of this file's help.
     */
    function renderAreas(plan, blocks) {
        var host = document.getElementById("deg-areas");
        document.getElementById("deg-audit").hidden = false;
        host.textContent = "";

        blocks.forEach(function (b) {
            var v = verdict(b.courses);
            var done = b.courses.filter(function (c) { return c._state === "complete"; }).length;

            var area = elem("details", "degree-area is-" + v.key);
            area.id = b.id;
            // A finished block folds; anything with work left in it opens. The
            // learner's attention belongs on what is outstanding, and a
            // satisfied block that still costs a screen of scrolling is the
            // audit burying its own conclusion.
            area.open = v.key !== "complete";

            var head = elem("summary", "degree-area-head");
            var nameWrap = elem("div", "degree-area-idbox");
            nameWrap.appendChild(elem("h3", "degree-area-name", b.label));
            nameWrap.appendChild(elem("span", "degree-area-verdict is-" + v.key, v.label));
            head.appendChild(nameWrap);

            var right = elem("div", "degree-area-stat");
            right.appendChild(elem("span", "degree-area-count",
                done + " of " + b.courses.length + " complete"));
            var meter = elem("span", "degree-area-meter");
            var fill = elem("span", "degree-area-fill");
            fill.style.width = (b.courses.length
                ? Math.round(done / b.courses.length * 100) : 0) + "%";
            meter.appendChild(fill);
            right.appendChild(meter);
            head.appendChild(right);
            area.appendChild(head);

            // THE STILL-NEEDED LINE. Whole sentence, inside the block, exactly
            // where the audit puts it.
            var s = stillNeeded(b);
            if (s) {
                var need = elem("p", "degree-need");
                need.appendChild(elem("span", "degree-need-label", "Still needed:"));
                var txt = " " + plural(s.count, "course", "courses") + " from " +
                          b.label.toLowerCase();
                if (s.credits) {
                    txt += " (" + fmtCr(s.credits.credits) + " credits" +
                           (s.credits.estimated ? ", estimated until built" : "") + ")";
                }
                var clauses = joinClauses(stillNeededClauses(s));
                need.appendChild(document.createTextNode(
                    txt + (clauses ? " — " + clauses + "." : ".")));
                area.appendChild(need);
            } else {
                var okd = elem("p", "degree-need is-done");
                okd.appendChild(icon("check"));
                okd.appendChild(document.createTextNode(
                    "Nothing still needed — every course in this block is complete."));
                area.appendChild(okd);
            }

            var list = elem("ul", "degree-rows");
            b.tracks.forEach(function (t) {
                if (t.length === 1) {
                    list.appendChild(courseRow(plan, t[0], null));
                    return;
                }
                // A SEQUENCE. Kept as its own grouping with its own label,
                // because "these are not free to reorder" is the one structural
                // fact the plan has. Drawn as ordered rows rather than the old
                // horizontally-scrolling band: the band pushed a chain of six
                // off the side of its own container at 1440 and had to be given
                // a scroll shadow, while the phone layout already turned it
                // vertical. Vertical everywhere is the same drawing at every
                // width, and it costs no horizontal scroll anywhere.
                var seq = elem("li", "degree-seq");
                seq.appendChild(elem("p", "degree-seq-label", "Take in order"));
                var ol = elem("ol", "degree-seq-list");
                t.forEach(function (c, i) { ol.appendChild(courseRow(plan, c, i + 1)); });
                seq.appendChild(ol);
                list.appendChild(seq);
            });
            area.appendChild(list);
            host.appendChild(area);
        });
    }

    /* One row, one course. The audit's unit of record.
       `ordinal` is set only inside a sequence, where position is meaningful. */
    function courseRow(plan, c, ordinal) {
        // Whether a course EXISTS YET is a different fact from its state, and
        // the learner needs both: "available" says you may start it, "built"
        // says it is already written and opens immediately rather than after a
        // build. Solid leading edge against dashed, the same vocabulary the
        // create carousel uses for "this does not exist yet".
        var li = elem("li", "degree-row is-" + c._state +
                            (c.built ? " is-built" : " is-unbuilt"));

        var mark = elem("span", "degree-row-mark");
        if (ordinal) mark.appendChild(elem("span", "degree-row-ord", String(ordinal)));
        mark.appendChild(icon(STATE_ICON[c._state] || "dot"));
        li.appendChild(mark);

        var body = elem("div", "degree-row-body");
        var titleLine = elem("div", "degree-row-titleline");
        titleLine.appendChild(elem("span", "degree-row-title", c.title));
        if (c.built && c._state === "available") {
            titleLine.appendChild(elem("span", "degree-built-chip", "Ready"));
        }
        body.appendChild(titleLine);
        // What satisfied the row, or what would: complete rows say so, unmet
        // rows name the gate. Both are the audit's job.
        body.appendChild(elem("span", "degree-row-note", stateNote(c, false)));
        li.appendChild(body);

        if (c.size && c.size.credits) {
            var cr = elem("span", "degree-row-cr" + (c.size.estimated ? " is-est" : ""),
                          fmtCr(c.size.credits) + " cr");
            // The estimate marker travels with the number wherever it goes.
            // The key explains the styling; the title says it in words for
            // anyone who does not read a key.
            if (c.size.estimated) cr.title = "Estimated until the course is built";
            li.appendChild(cr);
        }

        if (c._state === "building") {
            var bar = elem("div", "degree-build-bar degree-row-bar");
            li.appendChild(bar);
        }
        return li;
    }

    /* --------------------------------------------------- the decision view */
    function renderNext(plan) {
        var section = document.getElementById("deg-next");
        var grid = document.getElementById("deg-next-grid");
        grid.textContent = "";

        var open = plan._courses.filter(function (c) { return c._state === "available"; });
        section.dataset.count = String(open.length);
        if (!open.length) {
            grid.appendChild(elem("p", "degree-section-sub",
                "No course is open to start right now. Everything left is " +
                "either in progress, being built, or waiting on a course " +
                "above it in the audit."));
            document.getElementById("deg-next-sub").textContent = "";
            return;
        }

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
            grid.appendChild(courseCard(plan, c));
        });
    }

    /* One card, one course — the decision surface only, where a card is a
       control rather than a record. */
    function courseCard(plan, c) {
        var actionable = c._state === "available";
        var card = elem(actionable ? "button" : "div",
                        "degree-course is-" + c._state
                        + (c.built ? " is-built" : " is-unbuilt")
                        + " is-prominent");
        if (actionable) card.type = "button";

        var head = elem("div", "degree-course-head");
        head.appendChild(elem("span", "degree-course-area", areaLabel(c.slot)));
        // A built course opens instantly; an unbuilt one costs a build first.
        // That is the difference between "start now" and "start in forty
        // minutes", so it earns a mark of its own.
        if (c.built && c._state === "available") {
            head.appendChild(elem("span", "degree-built-chip", "Ready"));
        }
        head.appendChild(icon(actionable ? ACTION_ICON : (STATE_ICON[c._state] || "dot")));
        card.appendChild(head);
        card.appendChild(elem("h3", "degree-course-title", c.title));

        // What this course COSTS, next to the decision to take it. Three
        // credits means something to anyone who has been to college; "144
        // concepts" does not.
        if (c.size && c.size.credits) {
            var size = elem("p", "degree-course-size" + (c.size.estimated ? " is-est" : ""),
                            fmtCr(c.size.credits) + " credits · ~" + c.size.hours + " h");
            if (c.size.estimated) size.title = "Estimated until the course is built";
            card.appendChild(size);
        }
        var note = elem("p", "degree-course-note", stateNote(c, true));
        // "Ready now" is a stronger offer than "will be built", and only the
        // stronger one gets the accent.
        if (c._state === "available" && !c.built) note.className += " is-quiet";
        card.appendChild(note);

        if (c._state === "building") card.appendChild(elem("div", "degree-build-bar"));

        if (actionable) {
            card.addEventListener("click", function () { start(plan, c, card); });
        }
        return card;
    }

    /* ------------------------------------------------------- the two views */

    var VIEWS = [
        { key: "audit", tab: "deg-tab-audit", panel: "deg-audit", hash: "#audit" },
        { key: "next", tab: "deg-tab-next", panel: "deg-next", hash: "#next" },
    ];

    function showView(key, focusPanel) {
        VIEWS.forEach(function (v) {
            var on = v.key === key;
            var tab = document.getElementById(v.tab);
            var panel = document.getElementById(v.panel);
            if (!tab || !panel) return;
            tab.classList.toggle("is-on", on);
            tab.setAttribute("aria-selected", on ? "true" : "false");
            tab.tabIndex = on ? 0 : -1;
            panel.hidden = !on;
        });
        // A hash, so a view is linkable and survives a reload. replaceState
        // rather than assigning location.hash: the latter scrolls the panel
        // under the header, and the switch should leave the page where it is.
        if (history.replaceState) {
            history.replaceState(null, "", location.pathname + location.search +
                                 (key === "audit" ? "" : "#" + key));
        }
        if (focusPanel) {
            var p = document.getElementById(key === "audit" ? "deg-audit" : "deg-next");
            if (p) p.focus({ preventScroll: true });
        }
    }

    function setupViews(plan) {
        var nav = document.getElementById("deg-views");
        if (!nav) return;
        var openCount = Number(document.getElementById("deg-next").dataset.count || 0);
        var n = counts(plan);

        document.getElementById("deg-tab-audit-sub").textContent =
            n.complete + " of " + n.total + " done";
        document.getElementById("deg-tab-next-sub").textContent =
            openCount ? openCount + " open" : "none open";

        nav.hidden = false;

        var tabs = VIEWS.map(function (v) { return document.getElementById(v.tab); });
        VIEWS.forEach(function (v, i) {
            var tab = tabs[i];
            if (!tab || tab.dataset.wired) return;
            tab.dataset.wired = "1";
            tab.addEventListener("click", function () { showView(v.key, false); });
            // Arrow keys move between tabs, which is what a tablist owes a
            // keyboard user; without it the roles are a costume.
            tab.addEventListener("keydown", function (e) {
                var d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                if (!d) return;
                e.preventDefault();
                var next = tabs[(i + d + tabs.length) % tabs.length];
                showView(VIEWS[(i + d + VIEWS.length) % VIEWS.length].key, false);
                next.focus();
            });
        });

        // The audit is the default: this page is named after the degree, and
        // the decision has its own tab carrying its own count. A hash wins over
        // the default so a link can point at either.
        showView(location.hash === "#next" ? "next" : "audit", false);
    }

    /* Open a requirement block and bring it into view — the summary band's
       cards are anchors, and an anchor that lands on a folded block has not
       actually shown the learner anything. */
    function revealBlock(id) {
        showView("audit", false);
        var el = document.getElementById(id);
        if (!el) return;
        el.open = true;
        el.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth",
                            block: "start" });
        el.classList.add("is-flash");
        setTimeout(function () { el.classList.remove("is-flash"); }, 900);
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
                        // A rule about the whole application does not belong
                        // in the subtitle of the button that happened to hit
                        // it. The card says what is running and offers the
                        // one useful action: go and watch it.
                        card.disabled = false;
                        // stateNote's second argument was `prominent`, a name
                        // that does not exist in this scope — this line threw a
                        // ReferenceError and swallowed the notice with it.
                        // Every caller of start() is a decision card, so the
                        // prominent phrasing is the right one, stated.
                        note.textContent = stateNote(c, true);
                        if (window.HelgaBuildNotice) {
                            window.HelgaBuildNotice.buildInProgress(c.title);
                            return;
                        }
                        throw new Error("a course is already being built");
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
