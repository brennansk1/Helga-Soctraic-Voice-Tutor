
/* "3 days ago" reads better than a date on a card, and a learner deciding
   which course to pick up is asking how long it has been, not when it was. */
function relativeDay(epochSeconds) {
    if (!epochSeconds) return '';
    var days = Math.floor((Date.now() / 1000 - epochSeconds) / 86400);
    if (days <= 0) return 'today';
    if (days === 1) return 'yesterday';
    if (days < 7) return days + ' days ago';
    if (days < 14) return 'last week';
    return Math.floor(days / 7) + ' weeks ago';
}

/* Watch a resumed build to completion.

   Written because the "Resume build" handler originally handed off to a
   `pollCourseStatus` that DID NOT EXIST — the typeof guard made it fall
   through to a blind page reload four seconds later, while the build it was
   reporting on ran for another hour. The guard hid the missing function
   instead of surfacing it, which is the same shape as every other
   component-works-but-the-path-never-fires defect in this project.

   Polls the status endpoint the wizard already uses. Gives up after a bounded
   number of attempts rather than polling a dead service forever. */
function pollCourseStatus(uid, button) {
    var attempts = 0;
    var MAX = 720;            // 5s apart, so an hour of building
    (function tick() {
        if (++attempts > MAX) { return; }
        fetch('/api/course_status/' + uid)
            .then(function (r) { return r.ok ? r.json() : {}; })
            .then(function (d) {
                var st = (d && d.status || '').toLowerCase();
                if (st === 'ready') { location.reload(); return; }
                if (st === 'failed' || st === 'hydration_failed') {
                    if (button) {
                        button.disabled = false;
                        button.textContent = 'Resume build';
                    }
                    return;
                }
                setTimeout(tick, 5000);
            })
            .catch(function () { setTimeout(tick, 5000); });
    })();
}

/* Fetched once per list render and merged onto the cards. Failure is silent
   and the cards fall back to a plain "Continue" — a decoration must not be
   able to stop the list rendering. */
function loadResumePoints() {
    return fetch('/api/resume_points')
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (d) { window.RESUME_POINTS = d || {}; })
        .catch(function () { window.RESUME_POINTS = {}; });
}
/**
 * courses.js — Course listing and Quick Create flow
 * Handles: course card rendering, quick create modal, progress updates
 */

// Alpine palette colors for course card headers
const ALPINE_COLORS = [
    ['#2e6b8a', '#1d4a6a'],  // Alpine blue
    ['#4a8c6f', '#356b53'],  // Meadow green
    ['#c17f4a', '#a8693a'],  // Warm timber
    ['#8b5e3c', '#6b4830'],  // Dark wood
    ['#5a9bb5', '#447a91'],  // Sky blue
    ['#d4a843', '#b08a30'],  // Edelweiss gold
    ['#7fb3d0', '#5e94b0'],  // Light blue
    ['#c45c4a', '#a04838'],  // Brick red
];

function getCardColors(title) {
    let hash = 0;
    for (let i = 0; i < title.length; i++) hash = title.charCodeAt(i) + ((hash << 5) - hash);
    const idx = Math.abs(hash) % ALPINE_COLORS.length;
    return ALPINE_COLORS[idx];
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// Build course cards out of nodes, not out of strings. Course titles and
// descriptions are stored data and go in with textContent, so no escaping
// question ever arises for them.
function mkEl(tag, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
}

function mkIcon(name) {
    const span = mkEl('span', 'i ' + name);
    span.setAttribute('aria-hidden', 'true');
    return span;
}

// --- Course Loading ---

/* The status line's opening text, and the exact string the 45s stall-detector
   compares against. It used to say "Initializing skeleton builder..." and the
   detector matched on the substring "Initializing" — so both the American
   spelling and the internal class name were on screen, and any rewording of one
   silently broke the other. */
var QC_STARTING = 'Starting the course builder…';

let coursesBuildingPollTimer = null;

/**
 * Build-time quality verdicts, keyed by course uid.
 *
 * Served by /api/course_quality, which reads structure.json directly — rag's
 * course list carries none of this (see the endpoint's comment in app.py).
 * Fetched alongside the course list rather than before it: the verdicts
 * decorate a card, they must never be the reason the grid fails to appear.
 */
async function loadCourseQuality() {
    try {
        const resp = await fetch('/api/course_quality');
        if (!resp.ok) return {};
        const data = await resp.json();
        return (data && data.courses) || {};
    } catch (e) {
        console.warn('[courses] quality verdicts unavailable:', e);
        return {};
    }
}

/* The build's verdicts are written for the person who built the checker.
   "do not match the depth contract for this level (75% met)" names an internal
   artefact, restates its own numbers as a percentage, and — because the count
   is interpolated into a fixed plural — says "1 ... do not" whenever exactly one
   concept failed.

   Rewritten here because this is the presentation layer; the strings themselves
   are generated server-side (the course-quality endpoint) and that is where the
   vocabulary should eventually be fixed. Anything not matched is passed through
   untouched rather than guessed at. */
function humaniseHeadline(text) {
    if (!text) return '';
    var depth = text.match(
        /^(\d+) of (\d+) checked concepts? do(?:es)? not match the depth contract for this level/);
    if (depth) {
        var missed = Number(depth[1]), checked = Number(depth[2]);
        return missed + ' of the ' + checked + ' concepts we checked ' +
               (missed === 1 ? 'is' : 'are') +
               ' not pitched at the level you asked for';
    }
    var partial = text.match(/^Depth was only verified for (\d+) of (\d+) concepts$/);
    if (partial) {
        return 'Only ' + partial[1] + ' of ' + partial[2] +
               ' concepts were checked against the level you asked for';
    }
    return text;
}

/**
 * One line per card, describing what the build's own checks concluded.
 *
 * The asymmetry is the point. A course that took a check and FAILED it gets a
 * tinted row that reads as a different kind of element. A course with minor
 * caveats, and a course too old to have been checked at all, get a single line
 * of muted text — because almost every course has something, and a grid where
 * every card is flagged is a grid with no flags in it.
 */
function qualityRow(quality) {
    if (!quality || !quality.verdict) return '';
    const headline = escapeHtml(humaniseHeadline(quality.headline || ''));
    switch (quality.verdict) {
        case 'failed':
            return `<div class="course-card-quality is-failed">`
                 + `<span class="i i-warning" aria-hidden="true"></span>`
                 + `<span>${headline}</span></div>`;
        case 'caution':
            return `<div class="course-card-quality is-caution" title="${headline}">`
                 + `<span>Checked, with caveats — ${headline}</span></div>`;
        case 'verified':
            return `<div class="course-card-quality is-verified" title="${headline}">`
                 + `<span class="i i-check" aria-hidden="true"></span>`
                 + `<span>Passed its build checks</span></div>`;
        default:
            // "unassessed" is NOT a pass. It is quiet, but it is still said, so
            // a learner can tell an unchecked course from a checked one.
            return `<div class="course-card-quality is-unassessed" title="${headline}">`
                 + `<span>Quality checks were not run on this course</span></div>`;
    }
}

async function loadCourses() {
    const grid = document.getElementById('courses-grid');
    // Abort if the API hangs — prevents infinite "Loading courses..." state
    // when rag-engine is slow or unreachable.
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    try {
        // Fetched CONCURRENTLY with the list, not before it: the resume hints
        // decorate the cards and must not add a serial round trip to the time
        // the learner waits for them. `loadResumePoints` never rejects.
        const resumeReady = loadResumePoints();
        // Both decorations are wanted, and both are fetched
        // concurrently with the list rather than before it.
        const qualityReady = loadCourseQuality();
        const resp = await fetch('/api/courses', { signal: controller.signal });
        const qualityByUid = await qualityReady;
        clearTimeout(timeoutId);
        await resumeReady;
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        const courses = data.courses || [];

        if (courses.length === 0) {
            grid.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="empty-icon"><span class="i i-books" aria-hidden="true"></span></div>
                    <h3>Start your first course</h3>
                    <p>Pick any topic — Helga will build a structured course and teach it to you through questions.</p>
                    <button class="btn-alpine btn-alpine-primary" onclick="openQuickCreate()" style="margin-top: 1rem;">Create a course</button>
                </div>
            `;
            return;
        }

        grid.innerHTML = '';
        // Phase 3 output, surfaced where a learner chooses a course. Silent
        // when a course has none — an older course predating asset collection
        // should look normal, not deficient.
        // `assets` never arrived on the course-list payload either (rag's
        // /api/courses copies six fields and this is not one of them), so this
        // chip has been dead since it was written. It comes off the quality
        // endpoint now, which reads structure.json first-hand.
        const assetChip = (course) => {
            const a = (qualityByUid[course.uid] || {}).assets || course.assets;
            if (!a || !a.collected) return null;
            const parts = [];
            if (a.diagrams) parts.push(a.diagrams + ' diagram' + (a.diagrams === 1 ? '' : 's'));
            if (a.images) parts.push(a.images + ' image' + (a.images === 1 ? '' : 's'));
            if (!parts.length) return null;
            const chip = mkEl('span', 'course-card-assets');
            chip.title = 'Visuals prepared when this course was built';
            chip.appendChild(mkIcon('i-spark'));
            chip.appendChild(document.createTextNode(' ' + parts.join(' · ')));
            return chip;
        };

        courses.forEach(course => {
            const [bg1, bg2] = getCardColors(course.title);
            const stats = course.stats || {};
            const progress = course.progress || 0;

            const card = document.createElement('div');
            card.className = 'course-card';
            const status = (course.status || 'unknown').toLowerCase();
            const isReady = status === 'ready';   // 'available' (part-built) is NOT enterable
            const isBuilding = status === 'skeleton' || status === 'building';

            /* THE TITLE IS DATA, AND IT USED TO BE CODE.
               These buttons carried onclick="startCourse('uid', 'TITLE', this)"
               with the title run through escapeHtml(). That is the wrong escape
               for this position: the HTML parser turns &#39; back into a literal
               apostrophe BEFORE the attribute's contents are parsed as
               JavaScript, so a course called Newton's Laws produced
               startCourse('...', 'Newton's Laws', this) — a SyntaxError that
               made Delete and Start Learning do nothing at all, and a title
               chosen on purpose could close the string and run whatever
               followed. Adding a second layer of escaping would only move the
               boundary; the fix is to stop generating code from data. The uid
               and title travel as data-* attributes and the handler reads them
               back as strings. */
            let actionButton;
            if (isBuilding) {
                actionButton = mkEl('button', 'btn-alpine btn-alpine-primary');
                actionButton.style.cssText = 'flex: 1; opacity: 0.6;';
                actionButton.disabled = true;
                actionButton.textContent = 'Building…';
            } else if (isReady) {
                actionButton = mkEl('button', 'btn-alpine btn-alpine-primary');
                // NOT flex:1. That split the row evenly with three 40px icon buttons,
        // leaving a 104px box, so every two-word label — "Resume build",
        // "Start Learning" — wrapped to two lines. Let the label size the
        // button and let the icons keep their intrinsic width.
                actionButton.dataset.action = 'start';
                actionButton.dataset.uid = course.uid;
                actionButton.dataset.title = course.title || '';
                /* "Continue" alone made the learner re-derive their own
                   place from the path view. The position is persisted per
                   course already; it just was not surfaced. */
                var rp = (window.RESUME_POINTS || {})[course.uid];
                if (progress > 0 && rp && rp.concept_title) {
                    actionButton.textContent = 'Continue: ' + rp.concept_title;
                    actionButton.title = 'Resume ' + rp.concept_title +
                        (rp.saved_at ? ' — last studied ' + relativeDay(rp.saved_at) : '');
                } else {
                    actionButton.textContent = progress > 0 ? 'Continue' : 'Start learning';
                }
            } else if (status === 'partial' || status === 'hydration_failed' ||
                       status === 'failed') {
                /* A DEAD CARD USED TO BE THE ONLY OUTCOME HERE.
                   hydrate() marks a course "partial" when even ONE concept
                   comes back a stub, and everything that is not "ready"
                   rendered disabled. So one bad concept in a hundred left the
                   course permanently unopenable, and the only way forward was
                   Delete and rebuild — discarding every concept that HAD
                   hydrated, which on this hardware is hours of model time.
                   Hydration already skips concepts that have content, so
                   resuming costs only what actually failed. */
                actionButton = mkEl('button', 'btn-alpine btn-alpine-primary');
                actionButton.style.cssText = 'flex: 1;';
                actionButton.textContent = 'Resume build';
                actionButton.title = status === 'partial'
                    ? 'Some concepts did not finish. This retries only those.'
                    : 'This build stopped early. This continues it.';
                actionButton.addEventListener('click', function () {
                    actionButton.disabled = true;
                    actionButton.textContent = 'Resuming…';
                    fetch('/api/course/' + course.uid + '/resume_build',
                          { method: 'POST' })
                        .then(function (r) { return r.json().catch(function () { return {}; })
                            .then(function (d) { return { ok: r.ok, d: d }; }); })
                        .then(function (res) {
                            if (!res.ok) throw new Error(res.d.error || 'failed');
                            /* The build outlives this request, so hand over to
                               the same poller a fresh build uses rather than
                               inventing a second progress path. */
                            actionButton.textContent = 'Building…';
                            pollCourseStatus(course.uid, actionButton);
                        })
                        .catch(function (e) {
                            actionButton.disabled = false;
                            actionButton.textContent = 'Resume failed — retry';
                            actionButton.title = String(e && e.message || e);
                        });
                });
            } else {
                // unknown — nothing safe to offer
                actionButton = mkEl('button', 'btn-alpine btn-alpine-primary');
                actionButton.style.cssText = 'flex: 1; opacity: 0.6;';
                actionButton.disabled = true;
                actionButton.textContent = 'Not ready';
            }

            // A5.3 — the header was a full-bleed gradient slab in a colour
            // hashed from the TITLE, so a course about Roman history could be
            // brick red for no reason and the grid read as a set of warning
            // banners. The accent is now a 3px spine: still per-course, still
            // recognisable at a glance, no longer shouting.
            //
            // An empty course also used to offer "Start Learning" next to
            // "0 Modules · 0 Lessons · 0 Concepts" — a button that could only
            // fail. Those are surfaced as incomplete builds instead.
            const isEmpty = !(stats.concepts > 0);
            card.style.setProperty('--course-accent', bg1);

            const body = mkEl('div', 'course-card-body');

            const h3 = mkEl('h3', 'course-card-title');
            h3.textContent = course.title || '';
            /* WHICH TEACHING LAYER THIS COURSE GOT.
               A course that routed to no domain is taught generically — no
               per-kind guidance, none of the prohibitions that define each
               domain — and nothing surfaced that, so the lesser path looked
               identical to the better one. Appended to the title node, which
               is created HERE: an earlier version built the badge further up
               and guarded on a `titleEl` that does not exist in this scope, so
               it would have attached to nothing. */
            if (course.teaching_domain) {
                const badge = mkEl('span', 'course-card-domain');
                badge.textContent = ({
                    mathematics: 'Mathematics',
                    science: 'Science',
                    history: 'History',
                    computer_science: 'Computer Science'
                })[course.teaching_domain] || course.teaching_domain;
                badge.title = 'Taught with the ' + badge.textContent +
                    ' teaching layer';
                badge.style.cssText =
                    'font-size:11px;opacity:.65;margin-left:8px;' +
                    'padding:1px 7px;border:1px solid currentColor;' +
                    'border-radius:9px;white-space:nowrap;vertical-align:middle;';
                h3.appendChild(badge);
            }
            body.appendChild(h3);

            /* NO PLACEHOLDER DESCRIPTION.
               This fell back to "A comprehensive interactive course." — a
               sentence true of nothing in particular, printed identically on
               every card, so a grid of four different subjects read as four
               copies of one course. A card with no description now simply has
               none: the title, the counts and the build verdict are all real,
               and a gap is more honest than filler that calls the course
               comprehensive.

               Descriptions ARE written at build time — structure.json carries
               them for the courses that have one — but /api/courses returns ''
               for every course. That belongs to the course-list endpoint, not
               to this file. */
            if (course.description) {
                const desc = mkEl('p', 'course-card-desc');
                desc.textContent = course.description;
                body.appendChild(desc);
            }

            const statsRow = mkEl('div', 'course-card-stats');
            if (isEmpty) {
                const warn = mkEl('span', 'course-card-empty');
                warn.appendChild(mkIcon('i-warning'));
                warn.appendChild(document.createTextNode(' No content — build did not finish'));
                statsRow.appendChild(warn);
            } else {
                [(stats.modules || 0) + ' modules',
                 (stats.lessons || 0) + ' lessons',
                 (stats.concepts || 0) + ' concepts'].forEach(t => {
                    const s = document.createElement('span');
                    s.textContent = t;
                    statsRow.appendChild(s);
                });
                const chip = assetChip(course);
                if (chip) statsRow.appendChild(chip);
            }
            body.appendChild(statsRow);

            /* Theirs: the build verdict, grafted onto the DOM-built
               card. qualityRow escapes its headline and emits no
               event handlers, so injecting it as HTML is safe. */
            if (!isEmpty && !isBuilding) {
                const q = qualityRow(qualityByUid[course.uid]);
                if (q) body.insertAdjacentHTML('beforeend', q);
            }

            if (!isEmpty) {
                const prog = mkEl('div', 'course-card-progress');
                const bar = mkEl('div', 'alpine-progress');
                const fill = mkEl('div', 'alpine-progress-fill');
                fill.style.width = progress + '%';
                bar.appendChild(fill);
                const pct = mkEl('span', 'course-card-pct');
                pct.textContent = Math.round(progress) + '%';
                prog.appendChild(bar);
                prog.appendChild(pct);
                body.appendChild(prog);
            }

            const actions = mkEl('div', 'course-card-actions');
            if (isEmpty) {
                const inc = mkEl('button', 'btn-alpine btn-alpine-secondary');
                inc.disabled = true;
                inc.textContent = 'Incomplete';
                actions.appendChild(inc);
            } else {
                actions.appendChild(actionButton);
            }

            const viewBtn = mkEl('button', 'btn-alpine btn-alpine-ghost course-card-icon-btn');
            viewBtn.dataset.action = 'view';
            viewBtn.dataset.uid = course.uid;
            viewBtn.title = 'View structure';
            viewBtn.setAttribute('aria-label', 'View structure for ' + (course.title || ''));
            viewBtn.appendChild(mkIcon('i-clipboard'));
            actions.appendChild(viewBtn);

            const delBtn = mkEl('button', 'btn-alpine btn-alpine-ghost course-card-icon-btn is-danger');
            delBtn.dataset.action = 'delete';
            delBtn.dataset.uid = course.uid;
            delBtn.dataset.title = course.title || '';
            delBtn.title = 'Delete course';
            delBtn.setAttribute('aria-label', 'Delete ' + (course.title || ''));
            delBtn.appendChild(mkIcon('i-trash'));
            actions.appendChild(delBtn);

            body.appendChild(actions);
            card.appendChild(body);
            grid.appendChild(card);
        });
        wireCardActions(grid);

        // Auto-refresh if any course is still building — without this the user
        // sees "Building..." forever until they manually reload the page.
        const hasBuilding = courses.some(c => {
            const s = (c.status || '').toLowerCase();
            return s === 'skeleton' || s === 'building';
        });
        if (coursesBuildingPollTimer) {
            clearTimeout(coursesBuildingPollTimer);
            coursesBuildingPollTimer = null;
        }
        if (hasBuilding) {
            coursesBuildingPollTimer = setTimeout(loadCourses, 5000);
        }
    } catch (e) {
        clearTimeout(timeoutId);
        const isAbort = e.name === 'AbortError';
        grid.innerHTML = `
            <div class="empty-state" style="grid-column: 1/-1;">
                <div class="empty-icon"><span class="i i-warning" aria-hidden="true"></span></div>
                <h3>${isAbort ? 'Loading is taking too long' : 'Could not load your courses'}</h3>
                <p>${escapeHtml(isAbort ? 'The server is slow to respond. Check that rag-engine is running.' : (e.message || 'Unknown error'))}</p>
                <button class="btn-alpine btn-alpine-primary" onclick="loadCourses()" style="margin-top: 1rem;">Retry</button>
            </div>
        `;
    }
}

/* One listener on the grid, rebound after every render. Delegation keeps the
   card markup free of behaviour, and — the point of the exercise — means no
   course title is ever concatenated into something that will later be parsed
   as JavaScript. */
function wireCardActions(grid) {
    if (grid.dataset.actionsWired) return;
    grid.dataset.actionsWired = '1';
    grid.addEventListener('click', function (ev) {
        const btn = ev.target.closest('[data-action]');
        if (!btn || !grid.contains(btn)) return;
        const uid = btn.dataset.uid;
        if (!uid) return;
        if (btn.dataset.action === 'start') startCourse(uid, btn.dataset.title || '', btn);
        else if (btn.dataset.action === 'view') window.location.href = '/course/view?uid=' + encodeURIComponent(uid);
        else if (btn.dataset.action === 'delete') deleteCourse(uid, btn.dataset.title || '');
    });
}

function startCourse(uid, title, triggerEl) {
    // Visible loading feedback on the clicked button so the user sees the
    // click was received. Without this, the structure fetch (up to several
    // hundred ms on large courses) feels like a dead UI.
    var btn = triggerEl || (window.event && window.event.target && window.event.target.closest('button'));
    if (btn) {
        btn.classList.add('is-loading');
        btn.disabled = true;
    }

    // Safety net: if navigation hasn't happened within 8s, something is
    // wrong and we should restore the button so the user can retry.
    var loadingTimeout = setTimeout(function() {
        if (btn) { btn.classList.remove('is-loading'); btn.disabled = false; }
        if (window.showToast) window.showToast('Course is slow to load. Please try again.', 'error');
    }, 8000);

    // Do NOT await /api/set_active_course — it triggers the FSM's resume
    // flow which can call the LLM for a bridge sentence and take >10s.
    // Instead we rely on the learn page sending SET_CONTEXT with this
    // course_uid immediately on load, and on enterNode embedding the
    // course_uid in its NAVIGATE_TO_TOPIC payload. Both paths wipe any
    // stale transcript on the FSM side. We still fire the call so the
    // last_active_course gets persisted for next session.
    fetch('/api/set_active_course', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uid: uid, title: title })
    }).catch(function() {});

    // Fetch structure to find first incomplete concept, then route to chatbox
    fetch('/api/course_structure?uid=' + encodeURIComponent(uid))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            clearTimeout(loadingTimeout);
            var firstIncomplete = null;
            var mods = (data.structure || {}).modules || [];
            for (var mi = 0; mi < mods.length && !firstIncomplete; mi++) {
                var units = mods[mi].units || [];
                for (var ui = 0; ui < units.length && !firstIncomplete; ui++) {
                    var lessons = units[ui].lessons || [];
                    for (var li = 0; li < lessons.length && !firstIncomplete; li++) {
                        var concepts = lessons[li].concepts || [];
                        for (var ci = 0; ci < concepts.length; ci++) {
                            if (!concepts[ci].completed) {
                                firstIncomplete = concepts[ci].uid;
                                break;
                            }
                        }
                    }
                }
            }
            // Fall back to first concept if everything is completed
            if (!firstIncomplete && mods.length > 0) {
                var u = (mods[0].units || [])[0];
                var l = u ? (u.lessons || [])[0] : null;
                var c = l ? (l.concepts || [])[0] : null;
                if (c) firstIncomplete = c.uid;
            }
            var url = '/learn?course_uid=' + encodeURIComponent(uid);
            if (firstIncomplete) url += '&concept_uid=' + encodeURIComponent(firstIncomplete);
            window.location.href = url;
        })
        .catch(function() {
            clearTimeout(loadingTimeout);
            // If structure fetch fails, still navigate to learn — it can handle missing concept_uid
            window.location.href = '/learn?course_uid=' + encodeURIComponent(uid);
        });
}

// Styled delete confirmation modal — replaces the native confirm() popup so
// the flow matches the rest of the Alpine theme and cannot be dismissed by
// accident.
let pendingDeleteUid = null;

function deleteCourse(uid, title) {
    pendingDeleteUid = uid;
    const titleEl = document.getElementById('delete-confirm-title');
    if (titleEl) titleEl.textContent = '"' + title + '"';
    const backdrop = document.getElementById('delete-confirm-backdrop');
    if (backdrop) backdrop.classList.add('active');
}

function closeDeleteConfirm() {
    pendingDeleteUid = null;
    const backdrop = document.getElementById('delete-confirm-backdrop');
    if (backdrop) backdrop.classList.remove('active');
    const btn = document.getElementById('delete-confirm-btn');
    if (btn) { btn.disabled = false; btn.textContent = 'Delete course'; }
}

async function confirmDeleteCourse() {
    if (!pendingDeleteUid) return;
    const uid = pendingDeleteUid;
    const btn = document.getElementById('delete-confirm-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Deleting…'; }
    try {
        const resp = await fetch('/api/delete_course?uid=' + encodeURIComponent(uid), { method: 'DELETE' });
        closeDeleteConfirm();
        if (!resp.ok && window.showToast) {
            window.showToast('Could not delete the course', 'error');
        }
        loadCourses();
    } catch (e) {
        closeDeleteConfirm();
        if (window.showToast) window.showToast('Could not delete the course', 'error');
    }
}

// --- Quick Create Modal ---

function openQuickCreate() {
    document.getElementById('quick-create-backdrop').classList.add('active');
    document.getElementById('qc-form-phase').style.display = 'block';
    document.getElementById('qc-progress-phase').style.display = 'none';
    document.getElementById('qc-topic').value = '';
    document.getElementById('qc-topic').focus();
    // Ensure the submit button is in its resting state — a previous aborted
    // build may have left it in the loading class.
    var submitBtn = document.getElementById('qc-submit-btn');
    if (submitBtn) {
        submitBtn.classList.remove('is-loading');
        submitBtn.disabled = false;
    }

    // If a build is already in progress on the core service, auto-reattach
    // instead of showing the form. This prevents the user from accidentally
    // hitting 409 "creation already in progress" by clicking Quick Create
    // again mid-build.
    fetch('/api/creation_status', { cache: 'no-store' })
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(d) {
            if (d && d.active) {
                reattachToActiveBuild();
            }
        })
        .catch(function() { /* non-fatal — let the user submit */ });
}

function closeQuickCreate() {
    document.getElementById('quick-create-backdrop').classList.remove('active');
    // UI-8: Reset the form so previous values don't persist on re-open
    var form = document.getElementById('qc-form');
    if (form) form.reset();
}

// Recovery path when the initial create POST fails — flip the modal back
// to the form phase so the user can try again without reopening.
function retryQuickCreate() {
    var formPhase = document.getElementById('qc-form-phase');
    var progressPhase = document.getElementById('qc-progress-phase');
    if (formPhase && progressPhase) {
        progressPhase.style.display = 'none';
        formPhase.style.display = 'block';
    }
    var statusEl = document.getElementById('qc-progress-status');
    if (statusEl) statusEl.style.color = '';
    var submitBtn = document.getElementById('qc-submit-btn');
    if (submitBtn) {
        submitBtn.classList.remove('is-loading');
        submitBtn.disabled = false;
    }
    var topicInput = document.getElementById('qc-topic');
    if (topicInput) topicInput.focus();
}

// Reattach to an in-progress build triggered by another tab/page. We can't
// replay the STRUCT events we missed, but we can open the modal in progress
// mode, populate the topic/phase/pct from /api/creation_status, and let the
// socket stream any new events from the current point onward.
function reattachToActiveBuild() {
    fetch('/api/creation_status', { cache: 'no-store' })
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(d) {
            if (!d || !d.active) return;  // nothing to reattach to

            // Show the modal in progress mode (skip form phase entirely)
            document.getElementById('quick-create-backdrop').classList.add('active');
            document.getElementById('qc-form-phase').style.display = 'none';
            document.getElementById('qc-progress-phase').style.display = 'block';

            // Seed the UI from polling state
            var topic = d.topic || 'course';
            document.getElementById('qc-progress-title').textContent = 'Building: ' + topic;
            // Seed the progress bar from the polled percentage so the user
            // doesn't sit at 0% until the next socket event arrives.
            var bar = document.getElementById('qc-progress-bar');
            if (bar && d.progress_pct != null) {
                bar.style.width = Math.max(5, d.progress_pct) + '%';
            }
            var statusEl = document.getElementById('qc-progress-status');
            var phaseText = {
                skeleton:  'Building the course outline…',
                audit:     'Running quality checks…',
                hydration: 'Writing the concepts…',
                complete:  'Build complete.',
                error:     'Build encountered an error.'
            }[d.phase] || 'Working…';
            if (statusEl) statusEl.textContent = phaseText;

            // Tree is empty because we missed prior STRUCT events — show a
            // banner inside so the user knows why.
            var treeEl = document.getElementById('qc-progress-tree');
            if (treeEl) {
                treeEl.innerHTML =
                    '<div class="build-tree-placeholder">' +
                    '<div class="skeleton-pulse"></div>' +
                    '<span>Reattached mid-build. New structure will stream in as it\'s generated.</span>' +
                    '</div>';
            }

            // Reset buildState and set the current phase so incoming events
            // route to the right handlers.
            if (typeof buildState !== 'undefined') {
                buildState = {
                    phase: d.phase || 'skeleton',
                    modules: {},
                    moduleOrder: [],
                    conceptCount: 0,
                    hydratedCount: 0,
                    totalConcepts: 0,
                    warnings: [],
                    finished: false,
                    courseUid: d.course_uid || null
                };
            }
            if (typeof setPhase === 'function') setPhase(d.phase || 'skeleton');

            // Connect the socket so new status updates flow in. The existing
            // creation flow uses the same channel; handlers for STRUCT/AUDIT/
            // COURSE_COMPLETE messages will fire from here on.
            try { setupCreationSocket(topic); } catch (e) { console.warn('setupCreationSocket failed', e); }
        })
        .catch(function(e) { console.warn('Reattach failed:', e); });
}

// Shared socket wiring for the creation progress modal. Called both on fresh
// builds (from the Quick Create submit handler) and on reattach (from
// reattachToActiveBuild or the ?reattach=1 query param). Guarded so multiple
// invocations in the same page don't stack handlers.
// Current topic for the in-flight build. Updated on every setupCreationSocket
// call so that subsequent builds in the same page session use the fresh
// topic instead of the one baked into the socket's first closure. The
// listener below reads this via window._currentCreationTopic.
var _creationSocket = null;
window._currentCreationTopic = null;
function setupCreationSocket(topic) {
    // Always refresh the active topic so repeat builds in the same tab
    // use the current course name for completion card + status text.
    window._currentCreationTopic = topic;
    if (_creationSocket) return _creationSocket;  // already wired
    var socket = io();
    _creationSocket = socket;
    socket.on('status_update', function(data) {
        var msg = data.message || '';
        var statusEl = document.getElementById('qc-progress-status');
        if (!statusEl) return;  // modal not open — nothing to do

        // --- B6.4: structured pipeline-stage events (preferred over free-text) ---
        // The FSM emits these at every real phase boundary (send_pipeline_stage),
        // so stage/pct/uid are read straight off the wire instead of being
        // guessed from prose. Free-text parsing stays below only for the
        // fine-grained STRUCT/AUDIT/CHECK stream, which has no structured
        // equivalent yet.
        if (data.event && data.event.type === 'PIPELINE_STAGE') {
            handlePipelineStage(data.event, data.message);
            return;  // handled structurally — skip legacy string parsing
        }

        // Once the build has ended, free text must not repaint the status line.
        // The pipeline's `finally` block emits "Restarting Systems..." AFTER
        // the DONE stage; without this the completion card sits under a status
        // line implying the build is still running.
        if (buildState.finished) return;

        // --- STRUCT events: build the tree ---
        if (msg.startsWith('STRUCT:')) {
            var parts = msg.split(':');
            var sType = parts[1];

            if (sType === 'MODULE') {
                addTreeNode('module', parts.slice(2).join(':'));
                statusEl.textContent = 'Building module: ' + parts.slice(2).join(':');
            } else if (sType === 'UNIT') {
                addTreeNode('unit', parts.slice(2).join(':'));
            } else if (sType === 'LESSON') {
                addTreeNode('lesson', parts.slice(2).join(':'));
            } else if (sType === 'CONCEPT') {
                var cUid = parts[2];
                var cTitle = parts.slice(3).join(':');
                addTreeNode('concept', cTitle, cUid);
            } else if (sType === 'HYDRATING') {
                if (buildState.phase === 'skeleton') setPhase('hydrate');
                var hUid = parts[2];
                var hStatus = parts[3];
                var hTitle = parts.slice(4).join(':');
                statusEl.textContent = 'Generating content: ' + hTitle;
                updateHydrationStatus(hUid, hStatus, hTitle);
            } else if (sType === 'HYDRATED') {
                var dUid = parts[2];
                var dTitle = parts.slice(4).join(':');
                updateHydrationStatus(dUid, 'DONE', dTitle);
                statusEl.textContent = 'Hydrated: ' + dTitle;
            } else if (sType === 'WARN') {
                // These six are the ONLY signal that the course is below the
                // level it claims or still carries confirmed-false claims.
                // They used to enter this branch, match nothing, and vanish.
                recordBuildWarning(parts.slice(2).join(':'));
            }
        }
        else if (msg.startsWith('SYLLABUS:PHASE:')) {
            var phase = msg.split(':')[2];
            if (phase === '1_SKELETON') {
                setPhase('skeleton');
                statusEl.textContent = 'Building the course outline…';
            }
        }
        else if (msg.startsWith('AUDIT:')) {
            var auditParts = msg.split(':');
            var auditType = auditParts[1];
            if (auditType === 'DEDUP') {
                statusEl.textContent = 'Quality check: removed ' + auditParts[2] + ' duplicate concepts';
                addLogEntry('Audit removed ' + auditParts[2] + ' duplicates', 'warn');
            } else if (auditType === 'RENAME') {
                statusEl.textContent = 'Quality check: renamed ' + auditParts[2] + ' items';
                addLogEntry('Audit renamed ' + auditParts[2] + ' items', 'info');
            } else if (auditType === 'COMPLETE') {
                var aModules = auditParts[2] || '?';
                var aConcepts = auditParts[3] || '?';
                statusEl.textContent = 'Audit complete: ' + aModules + ' modules, ' + aConcepts + ' concepts';
                buildState.totalConcepts = parseInt(aConcepts) || buildState.totalConcepts;
                addLogEntry('Audit complete: ' + aModules + ' modules, ' + aConcepts + ' concepts', 'success');
            } else {
                statusEl.textContent = 'Quality audit: ' + auditParts.slice(1).join(' ');
            }
        }
        else if (msg === 'COURSE_AVAILABLE') {
            // Retained only to swallow the signal from an in-flight older build.
            // A course is no longer enterable part-built: the depth, fact,
            // level, grounding and coverage checks all run after hydration, so
            // "one concept exists" was never the same claim as "ready".
            statusEl.textContent = 'Concepts written — running quality checks…';
        }
        else if (msg === 'COURSE_COMPLETE') {
            // NOT a build signal. Its only emitter is the TEACHING loop: it
            // means a LEARNER just finished studying a course. Treating it as
            // build completion cost two bugs — this modal never closed on a
            // real build (the pipeline never sends it), and finishing a course
            // in another tab painted a false "course built" card here.
            // Build completion is the PIPELINE_STAGE DONE event, handled above.
        }
        /* The deepening search is the one long pause a learner will actually
           wonder about: the bar stops moving while the builder widens its
           search. Rendered in plain language here rather than filtered out,
           because "still working, and here is why" is the whole point.
           Without this branch the raw "SCOPE:DEEPEN:adjacent:..." string would
           be printed to the user — this file shows any message it does not
           recognise verbatim. */
        else if (msg.startsWith('SCOPE:DEEPEN:')) {
            var dparts = msg.split(':');
            statusEl.textContent =
                'Not much material yet — searching ' +
                dparts.slice(3).join(':');
        }
        else if (msg.startsWith('SCOPE:DEEPENED:')) {
            var oparts = msg.split(':');
            statusEl.textContent = oparts.slice(2).join(':');
        }
        else if (!msg.startsWith('CHECK:') && !msg.startsWith('CPROG:') && !msg.startsWith('PEDAGOGY:') && !msg.startsWith('QTYPE:')) {
            statusEl.textContent = msg;
        }

        if (data.progress) {
            var bar = document.getElementById('qc-progress-bar');
            if (bar) bar.style.width = data.progress + '%';
        }

        if (msg.startsWith('LOG:')) {
            addLogEntry(msg.substring(4).trim(), 'info');
        } else if (msg.startsWith('Error') || msg.startsWith('ERROR')) {
            addLogEntry(msg, 'error');
        } else if (msg.startsWith('CHECK:')) {
            var checkParts = msg.split(':');
            var checkResult = checkParts[2];
            addLogEntry(checkParts.slice(3).join(':'), checkResult === 'PASS' ? 'success' : checkResult === 'FAIL' ? 'error' : 'warn');
        }
    });
    return socket;
}

// B6.4 consumer. `stage` is authoritative: the FSM emits it at the actual
// phase boundary, so nothing here has to infer progress from wording.
var STAGE_PHASE = {
    PREFLIGHT: 'skeleton',
    SKELETON:  'skeleton',
    AUDIT:     'skeleton',
    HYDRATE:   'hydrate',
    FINALIZE:  'hydrate',
    DONE:      'complete'
};

function handlePipelineStage(ev, message) {
    var statusEl = document.getElementById('qc-progress-status');
    var bar = document.getElementById('qc-progress-bar');
    if (!statusEl) return;  // modal not open

    if (ev.stage === 'ERROR') {
        buildState.finished = true;
        statusEl.classList.add('qc-status-error');
        statusEl.style.color = 'var(--status-error)';
        statusEl.textContent = message || 'Build failed.';
        addLogEntry(ev.detail || message || 'Build failed', 'error');
        if (window.showToast) window.showToast('The course build failed', 'error');
        return;
    }

    statusEl.textContent = message || ev.stage;
    var phase = STAGE_PHASE[ev.stage];
    if (phase) setPhase(phase);

    var pct = ev.stage === 'DONE' ? 100 : ev.pct;
    if (bar && typeof pct === 'number') {
        bar.style.width = Math.max(0, Math.min(100, pct)) + '%';
    }
    if (ev.course_uid) buildState.courseUid = ev.course_uid;

    if (ev.stage === 'DONE') {
        buildState.finished = true;
        showCompletion(ev.topic || window._currentCreationTopic, ev);
    }
}

// STRUCT:WARN:<KIND>[:<detail>] — the build's quality caveats.
var WARN_TEXT = {
    CONCEPT_STUB:    function (d) { return 'Content could not be written for "' + d + '" — it was left as a stub'; },
    DEPTH_MISS:      function (d) { return '"' + d + '" is thinner than the level you asked for'; },
    DEPTH_SUMMARY:   function (d) { return d ? d.charAt(0).toUpperCase() + d.slice(1) : 'Some concepts are below the requested level'; },
    FACT_UNRESOLVED: function (d) { return '"' + d + '" still contains a claim Helga could not verify'; },
    FACT_SUMMARY:    function (d) { return d + ' concepts still contain confirmed-false claims'; },
    LEVEL_GAP:       function (d) { return 'The course reads ' + d + ' levels away from the one it claims'; },
    // Detail arrives pre-formatted as "<n>/<verified> concepts incomplete",
    // matching DEPTH_SUMMARY's convention.
    SECTIONS_SUMMARY: function (d) {
        return d ? d.charAt(0).toUpperCase() + d.slice(1) + ' — required sections the model never wrote'
                 : 'Some concepts are missing required sections';
    }
};

function recordBuildWarning(payload) {
    var idx = payload.indexOf(':');
    var kind = idx === -1 ? payload : payload.slice(0, idx);
    var detail = idx === -1 ? '' : payload.slice(idx + 1);
    var text = WARN_TEXT[kind] ? WARN_TEXT[kind](detail)
                               : (kind + (detail ? ': ' + detail : ''));
    buildState.warnings.push({ kind: kind, detail: detail, text: text });
    addLogEntry(text, 'warn');
    var treeEl = document.getElementById('qc-progress-tree');
    if (treeEl && window.ProgressTree && ProgressTree.addWarningNode) {
        ProgressTree.addWarningNode(treeEl, text);
    }
}

// Rendered on the completion card. A course that is below its stated level or
// carries unverified claims is still delivered — but saying so is the whole
// difference between a tutor and a chatbot.
function renderBuildWarnings() {
    var host = document.getElementById('qc-complete-summary');
    if (!host || !buildState.warnings.length) return;
    var counts = {};
    buildState.warnings.forEach(function(w) { counts[w.kind] = (counts[w.kind] || 0) + 1; });
    var note = document.createElement('div');
    note.className = 'build-warn';
    note.style.cssText = 'margin-top:0.5rem;color:var(--status-warning);font-size:0.85rem;text-align:left;';
    var lines = [];
    // Summary warnings state the whole-course verdict; per-concept ones are
    // collapsed to a count so the card stays readable on a 200-concept build.
    buildState.warnings.forEach(function(w) {
        if (w.kind === 'DEPTH_SUMMARY' || w.kind === 'FACT_SUMMARY' || w.kind === 'LEVEL_GAP') {
            lines.push(w.text);
        }
    });
    ['CONCEPT_STUB', 'DEPTH_MISS', 'FACT_UNRESOLVED'].forEach(function(k) {
        if (!counts[k]) return;
        var label = { CONCEPT_STUB: 'concept(s) could not be written',
                      DEPTH_MISS: 'concept(s) below the requested level',
                      FACT_UNRESOLVED: 'concept(s) with unverified claims' }[k];
        lines.push(counts[k] + ' ' + label);
    });
    // i-warning mask icon, never a glyph: emoji are drawn by the OS, ignore
    // the theme and are banned across this UI (see icons.css).
    note.innerHTML =
        '<strong><span class="i i-warning" aria-hidden="true"></span> ' +
        'Quality notes on this course</strong><ul style="margin:0.25rem 0 0 1rem;">' +
        lines.map(function (l) { return '<li>' + escapeHtml(l) + '</li>'; }).join('') +
        '</ul>';
    host.parentNode.insertBefore(note, host.nextSibling);
}

function updateDepthEstimate() {
    var scope = parseInt(document.getElementById('qc-scope').value);
    var mastery = parseInt(document.getElementById('qc-mastery').value);
    var start = parseInt(document.getElementById('qc-start').value);
    var moduleBase = [0, 3, 4, 6, 8, 11];
    var skipFactor = [0, 0, 0, 1, 2, 3];
    var conceptBase = [0, 3, 4, 5, 7, 10];
    var modules = Math.max(2, moduleBase[scope] - skipFactor[start]);
    var total = modules * conceptBase[mastery];
    var hours = Math.round(total * 0.1 * 10) / 10;
    /* STUDY time, and only study time. This line used to repeat the module and
       concept counts that the line directly beneath it already gives, and to
       print "~N hours" with no noun — directly above a second, much larger hour
       figure for the BUILD. Two unlabelled durations stacked on each other is a
       reader's problem, not a reader's information. */
    document.getElementById('qc-depth-estimate').textContent =
        'About ' + hours + ' hours of study once it is built';
}

// --- Build Progress State ---
var buildState = {
    phase: 'skeleton',  // skeleton | hydrate | complete
    modules: {},        // { module_title: { concepts: {uid: {title, status}} } }
    moduleOrder: [],
    conceptCount: 0,
    hydratedCount: 0,
    totalConcepts: 0,
    warnings: [],       // STRUCT:WARN:* — quality caveats on the built course
    finished: false,    // set by the DONE / ERROR pipeline stage
    courseUid: null     // carried by the DONE stage; no title-guessing needed
};

// The phase names the BACKEND uses. `hydration` (creation_status) and
// `hydrate` (the UI's own vocabulary) both mean the middle phase; the old
// ternary tested only for 'hydrate', so a reattach mid-hydration — and every
// 'audit' or 'initializing' poll — fell through to index 2 and drew the build
// as COMPLETE while it was still writing concepts.
var PHASE_INDEX = {
    initializing: 0,
    skeleton: 0,
    audit: 0,
    hydrate: 1,
    hydration: 1,
    finalize: 1,
    complete: 2,
    done: 2
};

function setPhase(phase) {
    var phaseIdx = PHASE_INDEX[String(phase || '').toLowerCase()];
    if (phaseIdx === undefined) {
        // Fail loudly and stand still. Guessing "complete" for an unknown
        // phase is how a half-built course got advertised as finished.
        console.warn('[setPhase] Unknown build phase "' + phase +
                     '" — leaving the phase indicator where it is.');
        return;
    }
    buildState.phase = phase;
    var dots = ['phase-skeleton', 'phase-hydrate', 'phase-complete'];
    var labels = ['phase-label-skeleton', 'phase-label-hydrate', 'phase-label-complete'];
    var lines = document.querySelectorAll('.phase-line');

    dots.forEach(function(id, i) {
        var el = document.getElementById(id);
        if (!el) return;
        el.classList.remove('active', 'done');
        if (i < phaseIdx) el.classList.add('done');
        else if (i === phaseIdx) el.classList.add('active');
    });
    labels.forEach(function(id, i) {
        var el = document.getElementById(id);
        if (!el) return;
        el.classList.remove('active', 'done');
        if (i < phaseIdx) el.classList.add('done');
        else if (i === phaseIdx) el.classList.add('active');
    });
    lines.forEach(function(line, i) {
        line.classList.toggle('done', i < phaseIdx);
    });
}

function addTreeNode(type, title, uid) {
    var treeEl = document.getElementById('qc-progress-tree');
    if (!treeEl) return;

    // Delegate rendering to shared component
    ProgressTree.addTreeNode(treeEl, type, title, uid);

    // Track in state
    if (type === 'module') {
        buildState.modules[title] = { concepts: {} };
        buildState.moduleOrder.push(title);
    } else if (type === 'concept' && uid) {
        var lastMod = buildState.moduleOrder[buildState.moduleOrder.length - 1];
        if (lastMod && buildState.modules[lastMod]) {
            buildState.modules[lastMod].concepts[uid] = { title: title, status: 'pending' };
        }
        buildState.conceptCount++;
        buildState.totalConcepts++;
    }
}

function updateHydrationStatus(uid, status, title) {
    var treeEl = document.getElementById('qc-progress-tree');
    if (!treeEl) return;

    // Delegate to shared component (handles research/writing/done icons)
    var isDone = ProgressTree.updateHydrationStatus(treeEl, uid, status, title);
    if (isDone) {
        buildState.hydratedCount++;
    }

    // Update progress bar
    var bar = document.getElementById('qc-progress-bar');
    ProgressTree.updateProgressBar(bar, buildState.hydratedCount, buildState.totalConcepts);
}

function addLogEntry(msg, type) {
    var logsEl = document.getElementById('qc-progress-logs');
    if (!logsEl) return;
    var entry = document.createElement('div');
    var ts = new Date().toLocaleTimeString();
    entry.className = 'log-' + (type || 'info');
    entry.textContent = '[' + ts + '] ' + msg;
    logsEl.appendChild(entry);
    logsEl.scrollTop = logsEl.scrollHeight;
}

// `info` is the DONE pipeline event (course_uid, topic, modules, concepts).
// It is the authority on what was built: a tab that reattached mid-build never
// saw the STRUCT events and so has no local counts to report.
function showCompletion(topic, info) {
    info = info || {};
    // Sanity guard: if the caller fired completion with no evidence at all —
    // no modules seen locally AND no counts on the event — bail rather than
    // paint a fake success card with stale data.
    var hasCounts = typeof info.modules === 'number' || typeof info.concepts === 'number';
    if (!hasCounts && (!buildState.moduleOrder || buildState.moduleOrder.length === 0)) {
        console.warn('[showCompletion] Ignoring completion trigger — no build evidence (no modules seen, no counts on the event).');
        return;
    }

    setPhase('complete');
    var bar = document.getElementById('qc-progress-bar');
    if (bar) { bar.style.width = '100%'; bar.style.background = 'var(--status-success)'; }

    document.getElementById('qc-progress-status').textContent = 'Course built.';
    document.getElementById('qc-progress-actions').style.display = 'block';

    var titleEl = document.getElementById('qc-complete-title');
    if (titleEl) titleEl.textContent = (topic || 'Your course') + ' is ready';

    // Prefer the counts the builder itself reported; fall back to what this
    // tab observed. The old fallback that fetched /api/courses and picked
    // courses[0] would show stats from an unrelated course.
    var summary = document.getElementById('qc-complete-summary');
    if (summary) {
        var modCount = typeof info.modules === 'number' ? info.modules : buildState.moduleOrder.length;
        var conCount = typeof info.concepts === 'number' ? info.concepts : buildState.totalConcepts;
        if (modCount > 0 && conCount > 0) {
            summary.textContent = modCount + ' modules, ' + conCount + ' concepts';
        } else {
            summary.textContent = 'Course created';
        }
    }
    renderBuildWarnings();

    // Trigger confetti
    if (window.showConfetti) window.showConfetti();
}

async function submitQuickCreate(submitBtn) {
    var topic = document.getElementById('qc-topic').value.trim();
    var scope = parseInt(document.getElementById('qc-scope').value);
    var mastery = parseInt(document.getElementById('qc-mastery').value);
    var startFrom = parseInt(document.getElementById('qc-start').value);
    var depth = Math.max(scope, mastery); // Legacy compat
    var style = document.getElementById('qc-style').value;

    if (topic.length < 3) {
        var err = document.getElementById('qc-topic-error');
        err.textContent = 'Topic must be at least 3 characters';
        err.style.display = 'block';
        return;
    }
    document.getElementById('qc-topic-error').style.display = 'none';

    // Immediate feedback on click — the form → progress transition is
    // animated (300ms) so without this the button feels unresponsive.
    if (submitBtn) {
        submitBtn.classList.add('is-loading');
        submitBtn.disabled = true;
    }

    // Reset state
    buildState = { phase: 'skeleton', modules: {}, moduleOrder: [], conceptCount: 0,
                   hydratedCount: 0, totalConcepts: 0, warnings: [], finished: false,
                   courseUid: null };

    // Smooth transition from form to progress phase
    var formPhase = document.getElementById('qc-form-phase');
    var progressPhase = document.getElementById('qc-progress-phase');
    formPhase.classList.add('fade-out');
    setTimeout(function() {
        formPhase.style.display = 'none';
        formPhase.classList.remove('fade-out');
        progressPhase.style.display = 'block';
        progressPhase.classList.add('fade-in');
        // Clean up animation class after it completes
        setTimeout(function() { progressPhase.classList.remove('fade-in'); }, 400);
    }, 300);
    document.getElementById('qc-progress-title').textContent = 'Building: ' + topic;
    document.getElementById('qc-progress-status').textContent = QC_STARTING;
    document.getElementById('qc-progress-tree').innerHTML = '<div class="build-tree-placeholder"><div class="skeleton-pulse"></div><span>Waiting for the first modules…</span></div>';
    document.getElementById('qc-progress-logs').innerHTML = '';
    document.getElementById('qc-progress-actions').style.display = 'none';
    setPhase('skeleton');

    // Connect socket for progress (extracted so reattachToActiveBuild can reuse it)
    setupCreationSocket(topic);

    // Submit via REST — three-slider system. A 45s safety timeout rescues
    // the UI from a stuck "Starting…" state when the core-logic
    // service is unreachable or the socket never reports its first event.
    var submitTimeout = setTimeout(function() {
        var statusEl = document.getElementById('qc-progress-status');
        var tree = document.getElementById('qc-progress-tree');
        if (statusEl && statusEl.textContent === QC_STARTING) {
            statusEl.textContent = 'Still waiting for the build to start… the server may be slow.';
            statusEl.style.color = 'var(--status-warning)';
            if (tree) {
                tree.innerHTML =
                    '<div class="build-tree-placeholder" style="color: var(--status-warning);">' +
                    '<span>No progress updates received yet.</span>' +
                    '</div>';
            }
        }
    }, 45000);

    try {
        var resp = await fetch('/api/create_course', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                topic: topic,
                depth: depth,
                scope: scope,
                mastery: mastery,
                starting_from: startFrom,
                teaching_style: style
            })
        });

        // 409 = "creation already in progress". This fires when a previous
        // build is still running (user clicked Create during a prior build,
        // or refreshed mid-build). Instead of showing a misleading "service
        // offline" error, reattach to the live build so they can watch it
        // finish.
        if (resp.status === 409) {
            clearTimeout(submitTimeout);
            var statusEl = document.getElementById('qc-progress-status');
            if (statusEl) {
                statusEl.textContent = 'A course is already being built — reattaching to it…';
                statusEl.style.color = '';
            }
            if (window.showToast) window.showToast('Reattached to the in-progress build', 'info');
            // Reset the submit button so a retry is possible if the user closes the modal
            var submitBtn = document.getElementById('qc-submit-btn');
            if (submitBtn) {
                submitBtn.classList.remove('is-loading');
                submitBtn.disabled = false;
            }
            // Pull the live build state and wire up progress streaming
            reattachToActiveBuild();
            return;
        }
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
    } catch (e) {
        clearTimeout(submitTimeout);
        var statusEl = document.getElementById('qc-progress-status');
        if (statusEl) {
            statusEl.textContent = 'Could not start the build: ' + e.message;
            statusEl.style.color = 'var(--status-error)';
        }
        // Replace placeholder tree with retry affordance
        var tree = document.getElementById('qc-progress-tree');
        if (tree) {
            tree.innerHTML =
                '<div class="build-tree-placeholder" style="flex-direction: column; gap: 0.75rem; color: var(--status-error);">' +
                '<span>Could not reach the course builder. The core service may be offline.</span>' +
                '<button type="button" class="btn-alpine btn-alpine-secondary" onclick="retryQuickCreate()">Try again</button>' +
                '</div>';
        }
        if (window.showToast) window.showToast('Could not start the course build', 'error');
    }

    // Start Learning button
    document.getElementById('qc-start-learning-btn').onclick = async function() {
        var self = this;
        self.classList.add('is-loading');
        self.disabled = true;
        // The DONE stage names the course it built. Use it — the title match
        // below is a guess that lands on the wrong course whenever two courses
        // share a word.
        if (buildState.courseUid) {
            fetch('/api/set_active_course', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: buildState.courseUid, title: topic })
            }).catch(function() {});
            window.location.href = '/learn?course_uid=' + encodeURIComponent(buildState.courseUid);
            return;
        }
        try {
            var resp = await fetch('/api/courses');
            var data = await resp.json();
            var courses = data.courses || [];
            // Find by title match (case-insensitive, partial match) or fall back to most recent
            var topicLower = topic.toLowerCase();
            var latest = courses.find(function(c) {
                return c.title && c.title.toLowerCase().includes(topicLower);
            }) || courses.find(function(c) {
                return c.status === 'ready';
            }) || courses[0];
            if (latest) {
                // Fire and forget — don't wait for the response (it triggers LLM which is slow)
                fetch('/api/set_active_course', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ uid: latest.uid, title: latest.title })
                }).catch(function() {});
                // Redirect immediately
                window.location.href = '/learn?course_uid=' + latest.uid;
            } else {
                window.location.href = '/courses';
            }
        } catch (e) {
            self.classList.remove('is-loading');
            self.disabled = false;
            window.location.href = '/learn';
        }
    };
}

// --- Init ---

document.addEventListener('DOMContentLoaded', function() {
    loadCourses();

    ['qc-scope', 'qc-mastery', 'qc-start'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('input', updateDepthEstimate);
    });

    // Close delete confirmation on Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            var dc = document.getElementById('delete-confirm-backdrop');
            if (dc && dc.classList.contains('active')) closeDeleteConfirm();
        }
    });

    // Auto-reattach to an in-progress build when arriving here from the
    // global creation banner (href="/courses?reattach=1").
    var qp = new URLSearchParams(window.location.search);
    if (qp.get('reattach') === '1') {
        // Strip the query param so reloads don't keep reopening the modal
        history.replaceState({}, '', '/courses');
        reattachToActiveBuild();
    }
});
