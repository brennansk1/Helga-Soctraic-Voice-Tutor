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

        if (name === "scope") runScopeCheck();
        if (name === "review") renderReview();
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

    function renderScope(j) {
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
        } else if (j.verdict === "ok") {
            verdict.textContent = "The subject can carry it";
            verdict.classList.add("ok");
            detail.textContent = j.reason || "";
        } else {
            verdict.textContent = "This subject looks thin for that size";
            verdict.classList.add("warn");
            detail.textContent = (j.reason || "") +
                " You can continue — the course will be honest about " +
                "stretching rather than padded with filler.";
        }
        scopeResult.appendChild(verdict);
        scopeResult.appendChild(detail);

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
        associate: "Associate degree — 20 courses over 4 terms",
        bachelors: "Bachelor's degree — 40 courses over 8 terms",
        course: "College course — one semester",
        sequence: "Two-semester sequence",
        seminar: "Seminar",
        overview: "Quick overview",
    };

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
            createSub.textContent = isDegree
                ? "plans the programme now; each course builds as you approach it"
                : "";
            reviewNote.textContent = isDegree
                ? "A degree is planned up front and built lazily: your first course " +
                  "generates now, and each next course is built one ahead of you — " +
                  "including electives you choose at registration."
                : "You can watch the build live, or leave — it continues either way.";
            createBtn.disabled = !(state.topic.trim().length >= 3 && state.template);
        }
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

    function startCreate() {
        createBtn.disabled = true;
        createBtn.querySelector(".create-btn-label").textContent = "Starting…";
        if (state.source === "book") {
            // The upload already started the build server-side; go watch it.
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
                    source: "create_carousel",
                },
            }),
        }).then(function () {
            window.location.href = "/build";
        }).catch(function (e) {
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

    /* ------------------------------------------------ init */
    go(0);
})();
