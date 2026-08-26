/* Home — state first, claim second.
 *
 * The page renders a neutral shell, then chooses its hero once data arrives:
 * a returning learner sees Continue, a first-time visitor sees the product's
 * claim and one button. Choosing after the fetch avoids a flash of the wrong
 * hero, which is the usual cost of doing this client-side.
 *
 * Rules carried from the codebase: textContent only, never innerHTML on
 * anything a server produced; failure states are named, never silent.
 */
(function () {
    "use strict";

    var $ = function (id) { return document.getElementById(id); };

    function setHero(title, sub, actions) {
        $("hero-title").textContent = title;
        $("hero-sub").textContent = sub;
        var box = $("hero-actions");
        box.textContent = "";
        actions.forEach(function (a) {
            var el = document.createElement("a");
            el.href = a.href;
            el.className = "home-btn" + (a.primary ? " home-btn-primary" : "");
            el.textContent = a.label;
            box.appendChild(el);
        });
    }

    function lastCourse() {
        try { return JSON.parse(localStorage.getItem("helga_last_course") || "null"); }
        catch (e) { return null; }
    }

    /* ---------------------------------------------------------- stats */
    function loadStats() {
        return fetch("/api/stats")
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(function (d) {
                $("stat-courses").textContent = d.total_courses != null ? d.total_courses : "0";
                $("stat-concepts").textContent = d.concepts_mastered != null ? d.concepts_mastered : "0";
                $("stat-streak").textContent = d.streak != null ? d.streak : "0";
                $("home-error").classList.add("hidden");
                return d;
            });
    }

    function loadDue() {
        // COUNT THE SAME THING THE PRACTICE PAGE COUNTS.
        //
        // This read /api/review_stats, which counts FLASHCARDS and treats a
        // card with no review date as due — so a night of generating cards put
        // "40 Due today" on the front page while Practice, one click away, said
        // 6. Two surfaces, two units, one label. `due_today` was also exactly
        // equal to `total_cards`, which is what that OR NULL does.
        //
        // Practice is the page a learner acts on, so the home figure now comes
        // from the same endpoint and the two agree by construction rather than
        // by coincidence.
        return fetch("/api/due_concepts")
            .then(function (r) { return r.ok ? r.json() : []; })
            .then(function (d) {
                var items = Array.isArray(d) ? d
                    : (d.concepts || d.due || d.items || []);
                var due = items.length;
                $("stat-due").textContent = due;
                return due;
            })
            .catch(function () { $("stat-due").textContent = "0"; return 0; });
    }

    /* ---------------------------------------------------------- courses */
    function loadCourses() {
        return fetch("/api/courses")
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(function (d) {
                var list = Array.isArray(d) ? d : (d.courses || []);
                if (!list.length) return [];
                var grid = $("home-course-grid");
                grid.textContent = "";
                list.slice(0, 6).forEach(function (c) {
                    var a = document.createElement("a");
                    a.className = "home-course";
                    a.href = "/learn?course_uid=" + encodeURIComponent(c.uid);
                    var h = document.createElement("h3");
                    h.textContent = c.title || "Untitled course";
                    var meta = document.createElement("p");
                    // Status is stated plainly. A course still building is not
                    // a broken course, and saying so beats a dead link.
                    // EVERY STATUS IS A SENTENCE, NOT AN ENUM.
                    //
                    // The fall-through printed the raw value, so the shelf read
                    // "Ready", "Still being prepared", "partial" and "failed"
                    // side by side — two sentences and two database words. A
                    // learner cannot act on "partial", and "failed" on a course
                    // that is mid-repair is worse than uninformative.
                    var status = (c.status || "").toLowerCase();
                    var LABEL = {
                        ready: null, complete: null,          // handled below
                        skeleton: "Still being prepared",
                        building: "Still being prepared",
                        resuming: "Still being prepared",
                        partial: "Partly built — open it to finish",
                        hydration_failed: "Stopped part-way — can be resumed",
                        failed: "Stopped part-way — can be resumed"
                    };
                    meta.textContent = (status === "ready" || status === "complete")
                        ? "Ready" + (c.concept_count ? " · " + c.concept_count + " concepts" : "")
                        : (LABEL[status] || "Still being prepared");
                    if (status !== "ready" && status !== "complete") {
                        a.classList.add("home-course-pending");
                    }
                    a.appendChild(h); a.appendChild(meta);
                    grid.appendChild(a);
                });
                $("home-recent").classList.remove("hidden");
                return list;
            });
    }

    /* ---------------------------------------------------------- degree */
    function loadDegree() {
        return fetch("/api/programs")
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var progs = d && (Array.isArray(d) ? d : d.programs);
                if (!progs || !progs.length) return null;
                var p = progs[0];
                $("degree-name").textContent = p.subject
                    ? p.subject + " — " + (p.template === "bachelors" ? "Bachelor's" : "Associate")
                    : "Your programme";
                var courses = p.courses || [];
                var done = courses.filter(function (c) { return c.status === "complete"; }).length;
                var building = courses.filter(function (c) { return c.status === "building"; }).length;
                var bar = $("degree-bar");
                bar.textContent = "";
                courses.forEach(function (c) {
                    var seg = document.createElement("span");
                    seg.className = "degree-seg degree-seg-" + (c.status || "locked");
                    seg.title = c.title || "";
                    bar.appendChild(seg);
                });
                $("degree-note").textContent =
                    done + " of " + courses.length + " courses complete" +
                    (building ? " · " + building + " being built now" : "");
                $("home-degree").classList.remove("hidden");
                return p;
            })
            .catch(function () { return null; });
    }

    /* ---------------------------------------------------------- boot */
    function boot() {
        var last = lastCourse();

        Promise.all([loadStats(), loadDue(), loadCourses(), loadDegree()])
            .then(function (res) {
                var stats = res[0] || {}, courses = res[2] || [];
                if (last && last.uid) {
                    setHero("Welcome back",
                            "Pick up " + (last.title || "your course") + " where you left off.",
                            [{ label: "Continue", href: "/learn?course_uid=" +
                               encodeURIComponent(last.uid), primary: true },
                             { label: "Create another", href: "/create" }]);
                } else if (courses.length) {
                    setHero("Welcome back",
                            "You have " + courses.length + " course" +
                            (courses.length === 1 ? "" : "s") + ". Open one, or build something new.",
                            [{ label: "Your courses", href: "/courses", primary: true },
                             { label: "Create a course", href: "/create" }]);
                } else {
                    firstVisit();
                }
            })
            .catch(function () {
                // Named failure, and the page still works: the claim hero needs
                // no data at all.
                $("home-error").classList.remove("hidden");
                if (last && last.uid) {
                    setHero("Welcome back", "Pick up where you left off.",
                            [{ label: "Continue", href: "/learn?course_uid=" +
                               encodeURIComponent(last.uid), primary: true }]);
                } else { firstVisit(); }
            });
    }

    function firstVisit() {
        setHero("A tutor that builds the course, then teaches it",
                "Give Helga a subject or a book and it researches, writes and " +
                "teaches a real course — Socratically, on your own machine.",
                [{ label: "Create your first course", href: "/create", primary: true }]);
        $("home-hero").classList.add("home-hero-first");
    }

    var retry = $("home-retry");
    if (retry) retry.addEventListener("click", boot);
    boot();
})();
