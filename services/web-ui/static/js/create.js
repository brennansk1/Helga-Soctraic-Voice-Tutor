/* Course-creation carousel.
 *
 * One decision per page; the pages adapt to the first decision. A book upload
 * makes structure the book's job, so the template and scope pages are skipped
 * — showing a mastery slider for a novel would promise a control the pipeline
 * deliberately ignores.
 *
 * Rules carried from the codebase:
 *   - never innerHTML anything a user or server produced; textContent only
 *   - every long operation shows a counter or a named state, never a bare
 *     spinner
 *   - failure states are named; there is no silent fallback
 */
(function () {
    "use strict";

    var track = document.getElementById("car-track");
    var dots = document.getElementById("car-dots");
    var prevBtn = document.getElementById("car-prev");
    var nextBtn = document.getElementById("car-next");
    var pages = Array.prototype.slice.call(track.querySelectorAll(".car-page"));

    var state = {
        source: null,        // "book" | "research"
        file: null,
        parse: null,         // BOOK:PARSED payload from the upload probe
        template: null,
        topic: "",
        mastery: 3,
        style: "",
        context: "",
        scope: null,         // scope-check result
        index: 0,
    };

    // The visible page sequence depends on the source: a book decides its own
    // structure and size, so template/subject/scope pages don't apply.
    function visiblePages() {
        if (state.source === "book") return ["source", "review"];
        return ["source", "template", "subject", "scope", "review"];
    }

    /* Depth-contract words per level — the REAL enforcement text, so the
       slider describes what the build will actually demand. */
    var MASTERY = {
        1: "Awareness — a grounded overview. Every concept cites a source.",
        2: "Understanding — adds a worked example to every concept.",
        3: "Application — formal definitions and worked examples, enforced.",
        4: "Proficiency — named results, derivations and primary sources required.",
        5: "Expertise — full formal notation and exercises; graduate register.",
    };

    /* ------------------------------------------------ carousel mechanics */
    function rebuildDots() {
        var seq = visiblePages();
        dots.textContent = "";
        seq.forEach(function (name, i) {
            var d = document.createElement("button");
            d.className = "car-dot";
            d.setAttribute("role", "tab");
            d.setAttribute("aria-label", "Step " + (i + 1) + " of " + seq.length);
            d.setAttribute("aria-selected", i === state.index ? "true" : "false");
            d.addEventListener("click", function () { go(i); });
            dots.appendChild(d);
        });
    }

    function go(i) {
        var seq = visiblePages();
        state.index = Math.max(0, Math.min(i, seq.length - 1));
        var name = seq[state.index];

        pages.forEach(function (p) {
            var active = p.dataset.page === name;
            p.classList.toggle("active", active);
            // Off-screen pages are inert so keyboard order never tours hidden
            // content.
            if (active) { p.removeAttribute("inert"); }
            else { p.setAttribute("inert", ""); }
        });

        // Slide to the DOM index of the named page.
        var domIndex = pages.findIndex(function (p) { return p.dataset.page === name; });
        track.style.transform = "translateX(-" + (domIndex * 100) + "%)";

        prevBtn.disabled = state.index === 0;
        nextBtn.disabled = !canLeave(name) || state.index === seq.length - 1;
        rebuildDots();

        if (name === "subject") tuneContextPrompt();
        if (name === "scope") runScopeCheck();
        if (name === "review") renderReview();
    }

    /* The context box is asking one thing — "which version of this subject do
       you mean?" — but a degree and a single course give very different
       examples of it, and a course-shaped example on the degree route reads
       as though the box does not apply. */
    function tuneContextPrompt() {
        var box = document.getElementById("context");
        if (!box) return;
        var isDegree = state.template === "associate" ||
                       state.template === "bachelors";
        box.placeholder = isDegree
            ? "e.g. I want the research and statistics side, aiming at graduate study — not clinical practice or counselling."
            : "e.g. I write joins and GROUP BY already. I want window functions, CTEs and reading a query plan, for analytics work — not database administration.";
    }

    /* A page must be satisfied before the arrow moves on — but dots can jump
       BACK freely; only forward motion is gated. */
    function canLeave(name) {
        switch (name) {
            case "source":
                return state.source === "research" ||
                       (state.source === "book" && !!state.parse);
            case "template": return !!state.template;
            case "subject": return state.topic.trim().length >= 3;
            case "scope": return true;   // the check informs; it never blocks
            default: return true;
        }
    }

    prevBtn.addEventListener("click", function () { go(state.index - 1); });
    nextBtn.addEventListener("click", function () { go(state.index + 1); });
    document.getElementById("create-shell").addEventListener("keydown", function (e) {
        if (e.target.tagName === "INPUT" && e.key !== "Escape") return;
        if (e.key === "ArrowLeft") go(state.index - 1);
        if (e.key === "ArrowRight" && !nextBtn.disabled) go(state.index + 1);
    });

    /* ------------------------------------------------ page 1: source */
    var srcBook = document.getElementById("src-book");
    var srcResearch = document.getElementById("src-research");
    var uploadZone = document.getElementById("upload-zone");
    var fileInput = document.getElementById("book-file");
    var uploadLabel = document.getElementById("upload-label");
    var uploadParse = document.getElementById("upload-parse");

    function pickSource(which) {
        state.source = which;
        srcBook.setAttribute("aria-pressed", which === "book" ? "true" : "false");
        srcResearch.setAttribute("aria-pressed", which === "research" ? "true" : "false");
        uploadZone.classList.toggle("hidden", which !== "book");
        rebuildDots();
        nextBtn.disabled = !canLeave("source");
    }
    srcBook.addEventListener("click", function () { pickSource("book"); });
    srcResearch.addEventListener("click", function () { pickSource("research"); });

    ["dragover", "dragenter"].forEach(function (ev) {
        uploadLabel.addEventListener(ev, function (e) {
            e.preventDefault(); uploadLabel.classList.add("dragover");
        });
    });
    ["dragleave", "drop"].forEach(function (ev) {
        uploadLabel.addEventListener(ev, function (e) {
            e.preventDefault(); uploadLabel.classList.remove("dragover");
        });
    });
    uploadLabel.addEventListener("drop", function (e) {
        if (e.dataTransfer.files.length) acceptFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener("change", function () {
        if (fileInput.files.length) acceptFile(fileInput.files[0]);
    });

    function acceptFile(f) {
        var ok = /\.(epub|pdf|md|markdown|txt)$/i.test(f.name);
        uploadParse.classList.remove("hidden");
        uploadParse.textContent = "";
        if (!ok) {
            uploadParse.textContent = f.name + " is not a supported format. " +
                "Accepted: .epub, .pdf, .md, .txt — convert .docx/.mobi first.";
            state.file = null; state.parse = null;
            return;
        }
        state.file = f;
        // Honest state while we wait: the server parses structure on upload.
        uploadParse.textContent = "Reading " + f.name + " (" +
            Math.round(f.size / 1024 / 102.4) / 10 + " MB) — structure first…";
        probeUpload(f);
    }

    function probeUpload(f) {
        var fd = new FormData();
        fd.append("file", f);
        fetch("/api/upload_epub", { method: "POST", body: fd })
            .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                uploadParse.textContent = "";
                if (!res.ok) {
                    uploadParse.textContent =
                        "Could not read the book: " + (res.j.error || "unknown error");
                    state.parse = null;
                    return;
                }
                state.parse = res.j;
                var line = document.createElement("div");
                line.textContent = f.name + " uploaded. ";
                var shape = document.createElement("span");
                shape.className = "parse-shape";
                shape.textContent = "The course will follow the book's own " +
                    "structure — progress appears chapter by chapter.";
                line.appendChild(shape);
                uploadParse.appendChild(line);
                nextBtn.disabled = !canLeave("source");
                rebuildDots();
            })
            .catch(function (e) {
                uploadParse.textContent = "Upload failed: " + e.message;
                state.parse = null;
            });
    }

    /* ------------------------------------------------ page 2: template */
    Array.prototype.forEach.call(
        document.querySelectorAll(".tpl-card"),
        function (card) {
            card.addEventListener("click", function () {
                Array.prototype.forEach.call(
                    document.querySelectorAll(".tpl-card"),
                    function (c) { c.classList.remove("selected"); });
                card.classList.add("selected");
                state.template = card.dataset.template;
                nextBtn.disabled = false;
            });
        });

    /* ------------------------------------------------ page 3: subject */
    var topicInput = document.getElementById("topic");
    var masteryInput = document.getElementById("mastery");
    var masteryDesc = document.getElementById("mastery-desc");
    var contextInput = document.getElementById("context");
    if (contextInput) {
        contextInput.addEventListener("input", function () {
            state.context = contextInput.value;
        });
    }
    var styleInput = document.getElementById("style");

    function syncMastery() { masteryDesc.textContent = MASTERY[masteryInput.value]; }
    masteryInput.addEventListener("input", syncMastery);
    syncMastery();
    topicInput.addEventListener("input", function () {
        state.topic = topicInput.value;
        nextBtn.disabled = !canLeave("subject");
    });
    styleInput.addEventListener("input", function () { state.style = styleInput.value; });
    masteryInput.addEventListener("change", function () { state.mastery = +masteryInput.value; });

    /* ------------------------------------------------ page 4: scope check */
    var scopeIdle = document.getElementById("scope-idle");
    var scopeTimer = null;
    var scopeRunning = document.getElementById("scope-running");
    var scopeResult = document.getElementById("scope-result");
    var scopeTopicEl = document.getElementById("scope-topic");
    var scopeRan = "";

    function runScopeCheck() {
        var topic = state.topic.trim();
        if (!topic || scopeRan === topic) return;
        scopeRan = topic;
        scopeIdle.classList.add("hidden");
        scopeResult.classList.add("hidden");
        scopeRunning.classList.remove("hidden");
        scopeTopicEl.textContent = topic;
        // Elapsed, because the wait is long enough to doubt.
        var t0 = Date.now();
        var elEl = document.getElementById("scope-elapsed");
        if (scopeTimer) clearInterval(scopeTimer);
        scopeTimer = setInterval(function () {
            if (!elEl) return;
            elEl.textContent = Math.round((Date.now() - t0) / 1000) + "s";
        }, 1000);

        fetch("/api/scope_check", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ topic: topic, template: state.template }),
        })
            .then(function (r) { return r.json(); })
            .then(function (j) { renderScope(j); })
            .catch(function () {
                // The check is advisory; its absence is stated, never hidden.
                renderScope({ available: false });
            });
    }

    
/* A SERVER REASON IS A FRAGMENT, AND THIS PAGE GLUES SENTENCES ONTO IT.
   Measured on the live scope check:

     "research was degraded (failed or throttled lookups) — thin evidence here
      means we could not look, not that the subject is thin You can continue —
      the course will be honest about stretching…"

   A lowercase opening and two sentences run together, because the reason is
   concatenated with a following sentence and nothing checks how it ends. The
   text is assembled here, so it is punctuated here. */
function asSentence(text) {
    var t = String(text == null ? "" : text).trim();
    if (!t) { return ""; }
    t = t.charAt(0).toUpperCase() + t.slice(1);
    if (!/[.!?…]$/.test(t)) { t += "."; }
    return t;
}

function renderScope(j) {
        if (scopeTimer) { clearInterval(scopeTimer); scopeTimer = null; }
        scopeRunning.classList.add("hidden");
        scopeResult.classList.remove("hidden");
        scopeResult.textContent = "";

        var verdict = document.createElement("p");
        verdict.className = "scope-verdict";
        var detail = document.createElement("p");
        detail.className = "scope-detail";

        if (!j || j.available === false) {
            verdict.textContent = "Scope check unavailable";
            verdict.classList.add("warn");
            detail.textContent = "The research service could not be reached. " +
                "The build itself re-checks scope before generating anything.";
        } else if (j.verdict === "ok" && j.grounded === false) {
            /* THE COUNT SAID YES AND THE BUILD WILL SAY NO.
               This screen graded on how MUCH material exists; the builder
               grades on how CLOSE it is, and rejects anything under its
               grounding bar. Measured on "SQL": 60 chapters found, best match
               scored 3.0 against a bar of 6.0, and the build logged
               "treating as SOURCELESS" — after this screen had promised the
               subject was covered. Saying it here is the honest version. */
            verdict.textContent = "Enough material, but none of it is close enough";
            verdict.classList.add("warn");
            detail.textContent = asSentence(j.grounding_note) +
                (j.best_relevance != null && j.grounding_bar != null
                    ? " (closest match scored " + j.best_relevance +
                      "; the build needs " + j.grounding_bar + ".)"
                    : "");
        } else if (j.verdict === "ok") {
            verdict.textContent = "The subject can carry it";
            verdict.classList.add("ok");
            detail.textContent = asSentence(j.reason);
        } else {
            verdict.textContent = "This subject looks thin for that size";
            verdict.classList.add("warn");
            detail.textContent = asSentence(j.reason) +
                " You can continue — the course will be honest about " +
                "stretching rather than padded with filler.";
        }
        scopeResult.appendChild(verdict);
        scopeResult.appendChild(detail);

        /* SHOW THE EVIDENCE, NOT JUST THE VERDICT.
           "The subject can carry it" is an assertion; the titles behind it are
           what let a learner judge whether to believe it. The endpoint has
           always had them — they were computed, counted, and thrown away. */
        var found = document.createElement("div");
        found.className = "scope-evidence";

        function group(label, items, asLinks) {
            if (!items || !items.length) return;
            var h = document.createElement("p");
            h.className = "scope-evidence-label";
            h.textContent = label;
            found.appendChild(h);
            var ul = document.createElement("ul");
            ul.className = "scope-evidence-list";
            items.forEach(function (it) {
                var li = document.createElement("li");
                var title = (typeof it === "string") ? it : it.title;
                if (asLinks && it && it.url) {
                    var a = document.createElement("a");
                    a.href = it.url; a.target = "_blank"; a.rel = "noopener noreferrer";
                    a.textContent = title;
                    li.appendChild(a);
                } else {
                    li.textContent = title;
                }
                if (it && it.source) {
                    var sm = document.createElement("span");
                    sm.className = "scope-evidence-src";
                    sm.textContent = " — " + it.source;
                    li.appendChild(sm);
                }
                // A title alone reads as "this will be used". The score is
                // what decides whether it actually is.
                if (it && it.relevance != null && j.grounding_bar != null) {
                    var sc = document.createElement("span");
                    var ok = Number(it.relevance) >= Number(j.grounding_bar);
                    sc.className = "scope-evidence-score" + (ok ? " is-ok" : " is-weak");
                    sc.textContent = ok
                        ? " · match " + it.relevance
                        : " · match " + it.relevance + ", below the " +
                          j.grounding_bar + " needed";
                    li.appendChild(sc);
                }
                ul.appendChild(li);
            });
            found.appendChild(ul);
        }

        if (j && j.available !== false) {
            group("Syllabi found", j.syllabi, true);
            group("Courses found", j.courses, true);
            group("Texts found", j.texts, true);
            if (j.vocabulary && j.vocabulary.length) {
                var v = document.createElement("p");
                v.className = "scope-evidence-vocab";
                v.textContent = "Topics it expects to cover: " +
                    j.vocabulary.slice(0, 10).join(", ");
                found.appendChild(v);
            }
            if (j.broadened_to && j.broadened_to.length) {
                var b = document.createElement("p");
                b.className = "scope-evidence-vocab";
                b.textContent = "No book on this exact topic — searched wider: "
                    + j.broadened_to.join(", ");
                found.appendChild(b);
            }
            if (found.childNodes.length) scopeResult.appendChild(found);
        }

        if (j && j.practice_tier) {
            var tier = document.createElement("div");
            tier.className = "scope-tier";
            tier.textContent = j.practice_tier;
            scopeResult.appendChild(tier);
        }
        state.scope = j;
    }

    /* ------------------------------------------------ page 5: review + create */
    var reviewPanel = document.getElementById("review-panel");
    var createBtn = document.getElementById("create-btn");
    var createSub = document.getElementById("create-btn-sub");
    var reviewNote = document.getElementById("review-note");

    var TEMPLATE_LABELS = {
        associate: "Associate degree — 20 courses, about 2 years",
        bachelors: "Bachelor's degree — 40 courses, about 4 years",
        course: "College course — one semester",
        sequence: "Two-semester sequence",
        seminar: "Seminar",
        overview: "Quick overview",
    };

    /* Kept next to TEMPLATE_LABELS and matching program.py's TEMPLATES. If a
       template's shape changes there, these two numbers change with it. */
    var GENED_COUNT = { associate: 7, bachelors: 12 };
    var TEMPLATE_COURSES = { associate: 20, bachelors: 40 };

    function row(k, v) {
        var r = document.createElement("div"); r.className = "review-row";
        var kk = document.createElement("span"); kk.className = "k"; kk.textContent = k;
        var vv = document.createElement("span"); vv.className = "v"; vv.textContent = v;
        r.appendChild(kk); r.appendChild(vv);
        return r;
    }

    function renderReview() {
        reviewPanel.textContent = "";
        if (state.source === "book") {
            reviewPanel.appendChild(row("Source", state.file ? state.file.name : "—"));
            reviewPanel.appendChild(row("Structure", "The book's own — one lesson per chapter"));
            reviewPanel.appendChild(row("Content", "Read from each chapter, not recalled"));
            createSub.textContent = "from your book";
            reviewNote.textContent = "Progress appears chapter by chapter. " +
                "You can leave this page; the build continues.";
            createBtn.disabled = !state.parse;
        } else {
            reviewPanel.appendChild(row("Subject", state.topic || "—"));
            reviewPanel.appendChild(row("Scale", TEMPLATE_LABELS[state.template] || "—"));
            reviewPanel.appendChild(row("Level", (MASTERY[state.mastery] || "").split(" — ")[0]));
            if (state.style) reviewPanel.appendChild(row("Style", state.style));
            if (state.scope && state.scope.verdict && state.scope.verdict !== "ok") {
                reviewPanel.appendChild(row("Scope", "thin — will be honest, not padded"));
            }
            var isDegree = state.template === "associate" || state.template === "bachelors";

            /* The general-education question only exists for degrees -- a
               single course has no general education to decline. Naming the
               actual numbers ("7 of the 20") beats an abstract "some courses":
               the whole point is letting someone see what they are agreeing
               to before they agree to it. */
            var gened = document.getElementById("gened-choice");
            if (gened) {
                gened.classList.toggle("hidden", !isDegree);
                if (isDegree) {
                    var n = GENED_COUNT[state.template] || 0;
                    var total = TEMPLATE_COURSES[state.template] || 0;
                    var lead = document.getElementById("gened-lead");
                    if (lead) {
                        lead.textContent = n + " of the " + total + " courses are "
                            + "outside the major — writing, maths, a science, a "
                            + "humanity. A real programme requires them.";
                    }
                    var skipNote = document.getElementById("gened-note-skip");
                    if (skipNote) {
                        skipNote.textContent = (total - n) + " courses instead of "
                            + total + ", and " + ((total - n) * 3) + " credit hours "
                            + "instead of " + (total * 3) + " — no longer a full "
                            + "degree's worth. The page will say so.";
                    }
                    var doneNote = document.getElementById("gened-note-done");
                    if (doneNote) {
                        doneNote.textContent = "All " + n + " stay in the plan and "
                            + "count as complete, so the credit total still "
                            + "compares with a real degree.";
                    }
                }
            }

            createSub.textContent = isDegree
                ? "plans the programme now; each course builds as you approach it"
                : "";
            reviewNote.textContent = isDegree
                ? "A degree is planned up front and built lazily: your first course " +
                  "generates now, and each next course is built one ahead of you, " +
                  "in the order you choose."
                : "You can watch the build live, or leave — it continues either way.";
            createBtn.disabled = !(state.topic.trim().length >= 3 && state.template);
        }
    }

    function genEdChoice() {
        var picked = document.querySelector('input[name="gened"]:checked');
        return (picked && picked.value) || "include";
    }

    /* THE SMART-STRETCH NOTIFIER. A bachelor's in Dungeon Mastering is a
       legitimate wish and an impossible promise: the scope check knows how
       much real material exists, and asking for 40 courses of it deserves a
       plain conversation before compute is spent — not a padded programme and
       not a silent downgrade. */
    function stretchWarning() {
        var isDegree = state.template === "associate" || state.template === "bachelors";
        var thin = state.scope && state.scope.available !== false &&
                   state.scope.verdict && state.scope.verdict !== "ok";
        if (!isDegree || !thin) return Promise.resolve(true);
        return new Promise(function (resolve) {
            var veil = document.createElement("div");
            veil.className = "stretch-veil";
            var card = document.createElement("div");
            card.className = "stretch-card";
            card.setAttribute("role", "alertdialog");
            card.setAttribute("aria-modal", "true");
            var h = document.createElement("h2");
            h.textContent = "This subject looks thin for a " +
                (state.template === "bachelors" ? "bachelor's" : "degree");
            var p1 = document.createElement("p");
            p1.textContent = (state.scope.reason || "The evidence sweep found " +
                "less material than a full programme needs.") +
                " Helga will not pad the gap with filler — the honest options " +
                "are a smaller programme, or continuing with courses that are " +
                "labelled where they stretch.";
            var acts = document.createElement("div");
            acts.className = "stretch-actions";
            var down = document.createElement("button");
            down.className = "btn-secondary";
            down.textContent = "Scale down to a course";
            var goOn = document.createElement("button");
            goOn.className = "btn-primary";
            goOn.textContent = "Continue anyway";
            down.addEventListener("click", function () {
                state.template = "course";
                veil.remove(); renderReview(); resolve(false);
            });
            goOn.addEventListener("click", function () {
                veil.remove(); resolve(true);
            });
            acts.appendChild(down); acts.appendChild(goOn);
            card.appendChild(h); card.appendChild(p1); card.appendChild(acts);
            veil.appendChild(card);
            document.body.appendChild(veil);
            goOn.focus();
        });
    }

    createBtn.addEventListener("click", function () {
        stretchWarning().then(function (proceed) {
            if (!proceed) { createBtn.disabled = false; return; }
            startCreate();
        });
    });

    /* A degree is not a big course, and picking one used to behave as though
       it were. The carousel sent template ("associate", "bachelors") inside a
       TEXT_INPUT payload that the FSM's text handler never read, so choosing a
       Bachelor's silently built ONE course — the flagship promise quietly not
       happening. Degree tiers go to the planner instead. */
    var DEGREE_TEMPLATES = { associate: 1, bachelors: 1 };

    function startDegree() {
        var label = createBtn.querySelector(".create-btn-label");
        var sub = document.getElementById("create-btn-sub");
        createBtn.disabled = true;

        // A counter, not a spinner: planning consults curriculum sources and
        // the model, and a spinner with no counter is indistinguishable from a
        // hang. (House rule, and it is the right one here.)
        var t0 = Date.now();
        label.textContent = "Planning your degree…";
        var tick = setInterval(function () {
            var s = Math.round((Date.now() - t0) / 1000);
            if (sub) sub.textContent = s + "s — laying out terms and prerequisites";
        }, 1000);

        fetch("/api/program", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ subject: state.topic.trim(),
                                   template: state.template,
                                   context: state.context.trim(),
                                   general_education: genEdChoice() }),
        })
            .then(function (r) {
                return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
            })
            .then(function (res) {
                clearInterval(tick);
                if (!res.ok) {
                    // 422 is the planner saying this subject cannot carry a
                    // programme this size — an answer, not a fault, and it
                    // deserves different words from a server error.
                    throw new Error(res.status === 422
                        ? (res.body.error || "this subject cannot carry a programme that size")
                        : (res.body.error || "HTTP " + res.status));
                }
                window.location.href = "/degree?uid=" +
                    encodeURIComponent(res.body.uid);
            })
            .catch(function (e) {
                clearInterval(tick);
                createBtn.disabled = false;
                label.textContent = "Create";
                if (sub) sub.textContent = "";
                reviewNote.textContent = "Could not plan the degree: " +
                    e.message + " — nothing was created.";
            });
    }

    function startCreate() {
        if (DEGREE_TEMPLATES[state.template] && state.source !== "book") {
            startDegree();
            return;
        }
        createBtn.disabled = true;
        createBtn.querySelector(".create-btn-label").textContent = "Starting…";
        if (state.source === "book") {
            // The upload already started the build server-side; go watch it.
            if (window.HelgaBuildGuard) window.HelgaBuildGuard.set();
            window.location.href = "/build";
            return;
        }
        var text = "create course on " + state.topic.trim();
        if (state.style) text += " style " + state.style.trim();
        fetch("/api/event", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                type: "TEXT_INPUT",
                payload: {
                    text: text,
                    template: state.template,
                    mastery: state.mastery,
                    context: state.context.trim(),
                    source: "create_carousel",
                },
            }),
        })
            .then(function (r) {
                /* THE REPLY HAS TO BE READ. This used to arm the four-hour
                   single-build lock and navigate to /build on any settled
                   fetch — including the 502 web-ui returns when core is down
                   and the 401 it returns without a student session. The
                   learner was then sent to watch a build that had never
                   started, and could not create anything for the rest of the
                   lock's life. A refusal is an answer, and it is reported
                   with the name the server gave it. */
                return r.json()
                    .catch(function () { return {}; })   // 502 pages are not JSON
                    .then(function (b) { return { ok: r.ok, status: r.status, body: b || {} }; });
            })
            .then(function (res) {
                if (!res.ok) {
                    throw new Error(res.status === 401
                        ? "you are not signed in as a student"
                        : (res.body.error || "HTTP " + res.status));
                }
                // Only now: the build exists, so the lock may be armed.
                if (window.HelgaBuildGuard) window.HelgaBuildGuard.set();
                window.location.href = "/build";
            })
            .catch(function (e) {
                createBtn.disabled = false;
                createBtn.querySelector(".create-btn-label").textContent = "Create";
                reviewNote.textContent = "Could not start the build: " + e.message +
                    " — nothing was created.";
            });
    }

    /* Touch swipe: phones and iPads navigate by gesture as well as arrows.
       Threshold 48px so a scroll wobble does not change pages. */
    var touchX = null;
    track.addEventListener("touchstart", function (e) {
        touchX = e.touches[0].clientX;
    }, { passive: true });
    track.addEventListener("touchend", function (e) {
        if (touchX === null) return;
        var dx = e.changedTouches[0].clientX - touchX;
        touchX = null;
        if (Math.abs(dx) < 48) return;
        if (dx < 0 && !nextBtn.disabled) go(state.index + 1);
        if (dx > 0) go(state.index - 1);
    }, { passive: true });

    /* THE SINGLE-BUILD LOCK. The hardware runs one model; a second build
       would queue behind the first and look like a hang. If one is running,
       this page says so and routes to it instead of pretending to offer a
       choice it cannot honour. */
    if (window.HelgaBuildGuard && window.HelgaBuildGuard.active()) {
        var shell = document.getElementById("create-shell");
        shell.textContent = "";
        var box = document.createElement("div");
        box.className = "stretch-card";
        box.style.margin = "10vh auto";
        var h = document.createElement("h2");
        h.textContent = "A course is already being built";
        var p1 = document.createElement("p");
        p1.textContent = "Helga builds one course at a time — a second build " +
            "would queue behind the first. Watch the current build, or come " +
            "back when it finishes.";
        var a = document.createElement("a");
        a.className = "create-btn"; a.href = "/build";
        a.style.textDecoration = "none";
        var al = document.createElement("span");
        al.className = "create-btn-label"; al.textContent = "Go to the build";
        a.appendChild(al);
        box.appendChild(h); box.appendChild(p1); box.appendChild(a);

        /* THIS CARD USED TO BE A DEAD END.
           It rendered once from the lock's state at page load and never looked
           again, so when the build ended — or was killed, which leaves the
           durable record reporting stale/failed — the guard cleared, the nav
           pill disappeared, and this card sat there blocking creation until
           the learner thought to reload. Measured today after a core restart.

           The guard reconciles against the server every 30s; watch it and
           bring the flow back the moment it lets go. The button is for anyone
           who does not want to wait for the next tick. */
        var again = document.createElement("button");
        again.className = "create-btn-secondary";
        again.type = "button";
        again.style.marginTop = "var(--space-3)";
        again.textContent = "Check again";
        again.addEventListener("click", function () { location.reload(); });
        box.appendChild(again);

        var freed = setInterval(function () {
            if (!window.HelgaBuildGuard || !window.HelgaBuildGuard.active()) {
                clearInterval(freed);
                location.reload();
            }
        }, 5000);

        shell.appendChild(box);
        return;
    }

    /* ------------------------------------------------ init */
    go(0);
})();
