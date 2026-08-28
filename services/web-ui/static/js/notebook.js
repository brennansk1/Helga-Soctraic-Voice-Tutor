/* The Session Notebook — reads the notes back.
 *
 * One course per view. The data arrives pre-grouped from the RAG side
 * (services/rag/notes_api.py): concepts in course order, notes chronological
 * within each, and a trailing "Other notes" group for rows whose concept no
 * longer exists in the structure.
 *
 * Three states, all visibly distinct on purpose:
 *   - empty  : "notes appear as you study" — a fresh install is NOT broken;
 *   - error  : the service did not answer, named, never drawn as emptiness;
 *   - loaded : concept groups, each exchange with its grade.
 *
 * Everything is createElement/textContent. Notes contain the learner's own
 * words and the model's — the two least trustable strings in the system.
 */
(function () {
    "use strict";

    var courseUid = window.NOTEBOOK_COURSE_UID || "";

    function $(id) { return document.getElementById(id); }

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    /* "2026-08-19T10:11:12.345" -> "2026-08-19 10:11"; odd input passes
       through — a strange timestamp is still better shown than dropped. */
    function niceTs(iso) {
        if (!iso) return "undated";
        return iso.replace("T", " ").slice(0, 16);
    }

    function show(id, on) {
        var n = $(id);
        if (n) n.hidden = !on;
    }

    function fail(msg) {
        show("notebook-loading", false);
        var box = $("notebook-error");
        if (box) {
            // textContent: msg can carry a server-supplied status string.
            box.textContent = "Helga could not open this notebook (" + msg +
                "). This is not a statement that you have no notes.";
            box.hidden = false;
        }
    }

    /* --------------------------------------------------- course picker */
    function loadPicker() {
        show("notebook-picker", true);
        fetch("/api/courses")
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function (data) {
                renderPicker((data && data.courses) || []);
            })
            .catch(function (e) { fail(e.message); });
    }

    /* The picker was printing the raw column value -- learners saw "partial"
       and "needs_review" next to their courses, which are names for states in
       the build pipeline, not descriptions of a notebook. The courses page
       already speaks in outcomes ("Fix and finish"); this says what the
       notebook itself will contain. */
    var STATUS_LABELS = {
        partial: "partly written",
        needs_review: "needs a fix",
        building: "still building",
        skeleton: "not written yet",
        failed: "did not finish"
    };

    function statusLabel(status) {
        return STATUS_LABELS[status] || status;
    }

    function renderPicker(courses) {
        var list = $("notebook-picker-list");
        if (!list) return;
        if (!courses.length) {
            list.appendChild(el("p", "notebook-picker-none",
                "No courses yet — build one first, then come back " +
                "after a session."));
            return;
        }
        courses.forEach(function (c) {
            var a = el("a", "notebook-picker-item");
            a.href = "/notebook?course_uid=" + encodeURIComponent(c.uid);
            a.appendChild(el("span", "notebook-picker-item-title",
                c.title || c.uid));
            if (c.status && c.status !== "ready") {
                a.appendChild(el("span", "notebook-picker-item-status",
                    statusLabel(c.status)));
            }
            list.appendChild(a);
        });
    }

    /* ------------------------------------------------------- notebook */
    function loadNotebook() {
        show("notebook-loading", true);
        fetch("/api/notebook/" + encodeURIComponent(courseUid))
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(render)
            .catch(function (e) { fail(e.message); });
    }

    function wireActions(data) {
        var actions = $("notebook-actions");
        if (!actions) return;
        var exp = $("notebook-export-link");
        if (exp) exp.href = "/api/notebook/" +
            encodeURIComponent(courseUid) + "/export";
        var syl = $("notebook-syllabus-link");
        if (syl) syl.href = "/print/syllabus/" + encodeURIComponent(courseUid);
        var cert = $("notebook-cert-link");
        if (cert) cert.href = "/print/certificate/" +
            encodeURIComponent(courseUid);
        // Export of an empty notebook is a file of nothing; keep the print
        // links, hide the download until there is something to download.
        if (exp) exp.hidden = !(data && data.total_notes > 0);
        actions.hidden = false;
    }

    function render(data) {
        show("notebook-loading", false);

        var line = $("notebook-course-line");
        if (line && data && data.course_title) {
            line.textContent = data.course_title +
                " — what you worked through, question by question.";
        }
        wireActions(data);

        var groups = (data && data.groups) || [];
        if (!groups.length) {
            var resume = $("notebook-empty-resume");
            if (resume) resume.href =
                "/learn?course_uid=" + encodeURIComponent(courseUid);
            show("notebook-empty", true);
            return;
        }

        var host = $("notebook-groups");
        if (!host) return;
        groups.forEach(function (g) { host.appendChild(renderGroup(g)); });
        host.hidden = false;
    }

    function renderGroup(g) {
        var sec = el("section", "notebook-group");
        var head = el("header", "notebook-group-head");
        head.appendChild(el("h2", "notebook-group-title",
            g.concept_title || g.concept_uid || "Untitled concept"));
        var crumbBits = [];
        if (g.module_title) crumbBits.push(g.module_title);
        if (g.lesson_title) crumbBits.push(g.lesson_title);
        if (crumbBits.length) {
            head.appendChild(el("p", "notebook-group-crumb",
                crumbBits.join(" › ")));
        }
        head.appendChild(el("span", "notebook-group-count",
            g.notes.length + (g.notes.length === 1 ? " note" : " notes")));
        sec.appendChild(head);

        var list = el("div", "notebook-notes");
        g.notes.forEach(function (n) { list.appendChild(renderNote(n)); });
        sec.appendChild(list);
        return sec;
    }

    function renderNote(n) {
        var card = el("article", "notebook-note notebook-note-" +
            (n.kind || "note"));
        var meta = el("div", "notebook-note-meta");
        meta.appendChild(el("time", "notebook-note-ts", niceTs(n.created_at)));
        if (n.grade != null) {
            var gradeCls = n.grade >= 3 ? "notebook-grade-pass"
                                        : "notebook-grade-partial";
            meta.appendChild(el("span", "notebook-grade " + gradeCls,
                "Grade " + n.grade + "/4"));
        }
        card.appendChild(meta);

        if (n.kind === "exchange") {
            var q = el("p", "notebook-q");
            q.appendChild(el("span", "notebook-label", "Q "));
            q.appendChild(document.createTextNode(n.question || ""));
            card.appendChild(q);
            var a = el("p", "notebook-a");
            a.appendChild(el("span", "notebook-label", "You "));
            a.appendChild(document.createTextNode(n.answer || ""));
            card.appendChild(a);
            if (n.reasoning && n.reasoning !== "N/A") {
                var r = el("p", "notebook-r");
                r.appendChild(el("span", "notebook-label", "Helga "));
                r.appendChild(document.createTextNode(n.reasoning));
                card.appendChild(r);
            }
        } else if (n.kind === "compacted") {
            // A compacted note is still evidence a turn happened; drawing it
            // as a normal note with no text would look like data loss.
            card.appendChild(el("p", "notebook-compacted",
                "Older note compacted — the grade and date were kept, the " +
                "full text was retired."));
        } else {
            var body = el("p", "notebook-freetext");
            if (n.role) body.appendChild(el("span", "notebook-label",
                n.role + " "));
            body.appendChild(document.createTextNode(n.text || ""));
            card.appendChild(body);
        }
        return card;
    }

    document.addEventListener("DOMContentLoaded", function () {
        if (courseUid) loadNotebook();
        else loadPicker();
    });
})();
