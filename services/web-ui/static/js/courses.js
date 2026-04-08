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

// --- Course Loading ---

let coursesBuildingPollTimer = null;

async function loadCourses() {
    const grid = document.getElementById('courses-grid');
    // Abort if the API hangs — prevents infinite "Loading courses..." state
    // when rag-engine is slow or unreachable.
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    try {
        const resp = await fetch('/api/courses', { signal: controller.signal });
        clearTimeout(timeoutId);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        const courses = data.courses || [];

        if (courses.length === 0) {
            grid.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="empty-icon">📚</div>
                    <h3>No courses yet</h3>
                    <p>Create your first course to start learning!</p>
                    <button class="btn-alpine btn-alpine-primary" onclick="openQuickCreate()" style="margin-top: 1rem;">Quick Create</button>
                </div>
            `;
            return;
        }

        grid.innerHTML = '';
        courses.forEach(course => {
            const [bg1, bg2] = getCardColors(course.title);
            const stats = course.stats || {};
            const progress = course.progress || 0;

            const card = document.createElement('div');
            card.className = 'course-card';
            const status = (course.status || 'unknown').toLowerCase();
            const isReady = status === 'ready' || status === 'available';
            const isBuilding = status === 'skeleton' || status === 'building';

            let actionButton;
            if (isBuilding) {
                actionButton = `<button class="btn-alpine btn-alpine-primary" style="flex: 1; opacity: 0.6;" disabled>Building...</button>`;
            } else if (isReady) {
                actionButton = `<button class="btn-alpine btn-alpine-primary" style="flex: 1;" onclick="startCourse('${course.uid}', '${escapeHtml(course.title)}', this)">${progress > 0 ? 'Continue' : 'Start Learning'}</button>`;
            } else {
                // failed, hydration_failed, partial, unknown — show disabled with status
                actionButton = `<button class="btn-alpine btn-alpine-primary" style="flex: 1; opacity: 0.6;" disabled>${status === 'failed' || status === 'hydration_failed' ? 'Build Failed' : 'Not Ready'}</button>`;
            }

            card.innerHTML = `
                <div class="course-card-header" style="background: linear-gradient(135deg, ${bg1}, ${bg2});">
                    <h3>${escapeHtml(course.title)}</h3>
                </div>
                <div class="course-card-body">
                    <p style="color: var(--text-secondary); font-size: 0.85rem; margin: 0;">
                        ${escapeHtml(course.description || 'A comprehensive interactive course.')}
                    </p>
                    <div class="course-card-stats">
                        <span>📦 ${stats.modules || 0} Modules</span>
                        <span>📖 ${stats.lessons || 0} Lessons</span>
                        <span>🧠 ${stats.concepts || 0} Concepts</span>
                    </div>
                    <div class="alpine-progress" style="height: 6px;">
                        <div class="alpine-progress-fill" style="width: ${progress}%; background: ${bg1};"></div>
                    </div>
                    <div class="course-card-actions">
                        ${actionButton}
                        <button class="btn-alpine btn-alpine-secondary" style="padding: 0.5rem 0.75rem;" onclick="window.location.href='/course/view?uid=${course.uid}'" title="View Structure" aria-label="View structure for ${escapeHtml(course.title)}">📋</button>
                        <button class="btn-alpine btn-alpine-danger" style="padding: 0.5rem 0.75rem;" onclick="deleteCourse('${course.uid}', '${escapeHtml(course.title)}')" title="Delete" aria-label="Delete ${escapeHtml(course.title)}">🗑</button>
                    </div>
                </div>
            `;
            grid.appendChild(card);
        });

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
                <div class="empty-icon">⚠️</div>
                <h3>${isAbort ? 'Loading is taking too long' : 'Failed to load courses'}</h3>
                <p>${escapeHtml(isAbort ? 'The server is slow to respond. Check that rag-engine is running.' : (e.message || 'Unknown error'))}</p>
                <button class="btn-alpine btn-alpine-primary" onclick="loadCourses()" style="margin-top: 1rem;">Retry</button>
            </div>
        `;
    }
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
    if (btn) { btn.disabled = false; btn.textContent = 'Delete Course'; }
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
            window.showToast('Failed to delete course', 'error');
        }
        loadCourses();
    } catch (e) {
        closeDeleteConfirm();
        if (window.showToast) window.showToast('Failed to delete course', 'error');
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
                skeleton:  'Building course skeleton...',
                audit:     'Running quality audit...',
                hydration: 'Generating concept content...',
                complete:  'Build complete.',
                error:     'Build encountered an error.'
            }[d.phase] || 'Working...';
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
                    totalConcepts: 0
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
            }
        }
        else if (msg.startsWith('SYLLABUS:PHASE:')) {
            var phase = msg.split(':')[2];
            if (phase === '1_SKELETON') {
                setPhase('skeleton');
                statusEl.textContent = 'Building course skeleton...';
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
            var actionsEl = document.getElementById('qc-progress-actions');
            if (actionsEl) actionsEl.style.display = 'block';
            statusEl.textContent = 'First concepts ready! You can start learning while the rest builds.';
        }
        else if (msg === 'COURSE_COMPLETE') {
            // Only the exact COURSE_COMPLETE signal closes the modal.
            // Loose matches on "successfully" or "System Idle" previously
            // fired during service restarts / preflight, triggering the
            // completion card mid-build with stale data from an old course.
            // Read the active topic from the shared var, not the closure,
            // so repeat builds in the same tab use the current course name.
            showCompletion(window._currentCreationTopic || topic);
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
    document.getElementById('qc-depth-estimate').textContent =
        '~' + total + ' concepts · ~' + hours + ' hours · ' + modules + ' modules';
}

// --- Build Progress State ---
var buildState = {
    phase: 'skeleton',  // skeleton | hydrate | complete
    modules: {},        // { module_title: { concepts: {uid: {title, status}} } }
    moduleOrder: [],
    conceptCount: 0,
    hydratedCount: 0,
    totalConcepts: 0
};

function setPhase(phase) {
    buildState.phase = phase;
    var dots = ['phase-skeleton', 'phase-hydrate', 'phase-complete'];
    var labels = ['phase-label-skeleton', 'phase-label-hydrate', 'phase-label-complete'];
    var lines = document.querySelectorAll('.phase-line');
    var phaseIdx = phase === 'skeleton' ? 0 : phase === 'hydrate' ? 1 : 2;

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

function showCompletion(topic) {
    // Sanity guard: if the caller fired completion with no build progress
    // (e.g., a stray status_update arriving before any STRUCT events),
    // bail so we don't paint a fake success card with stale data.
    if (!buildState.moduleOrder || buildState.moduleOrder.length === 0) {
        console.warn('[showCompletion] Ignoring completion trigger — buildState is empty (no modules seen yet).');
        return;
    }

    setPhase('complete');
    var bar = document.getElementById('qc-progress-bar');
    if (bar) { bar.style.width = '100%'; bar.style.background = 'var(--status-success)'; }

    document.getElementById('qc-progress-status').textContent = 'Course created successfully!';
    document.getElementById('qc-progress-actions').style.display = 'block';

    var titleEl = document.getElementById('qc-complete-title');
    if (titleEl) titleEl.textContent = topic + ' — Ready!';

    // Use the live buildState counts — they're accurate because the STRUCT
    // events populated them during the build. The old fallback that fetched
    // /api/courses and picked courses[0] as a last resort would show stats
    // from an unrelated course when the buildState was stale.
    var summary = document.getElementById('qc-complete-summary');
    if (summary) {
        var modCount = buildState.moduleOrder.length;
        var conCount = buildState.totalConcepts;
        if (modCount > 0 && conCount > 0) {
            summary.textContent = modCount + ' modules, ' + conCount + ' concepts generated';
        } else {
            summary.textContent = 'Course created';
        }
    }

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
    buildState = { phase: 'skeleton', modules: {}, moduleOrder: [], conceptCount: 0, hydratedCount: 0, totalConcepts: 0 };

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
    document.getElementById('qc-progress-status').textContent = 'Initializing skeleton builder...';
    document.getElementById('qc-progress-tree').innerHTML = '<div class="build-tree-placeholder"><div class="skeleton-pulse"></div><span>Waiting for structure...</span></div>';
    document.getElementById('qc-progress-logs').innerHTML = '';
    document.getElementById('qc-progress-actions').style.display = 'none';
    setPhase('skeleton');

    // Connect socket for progress (extracted so reattachToActiveBuild can reuse it)
    setupCreationSocket(topic);

    // Submit via REST — three-slider system. A 45s safety timeout rescues
    // the UI from a stuck "Initializing..." state when the core-logic
    // service is unreachable or the socket never reports its first event.
    var submitTimeout = setTimeout(function() {
        var statusEl = document.getElementById('qc-progress-status');
        var tree = document.getElementById('qc-progress-tree');
        if (statusEl && statusEl.textContent.indexOf('Initializing') !== -1) {
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
            statusEl.textContent = 'Failed to start build: ' + e.message;
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
        if (window.showToast) window.showToast('Failed to start course build', 'error');
    }

    // Start Learning button
    document.getElementById('qc-start-learning-btn').onclick = async function() {
        var self = this;
        self.classList.add('is-loading');
        self.disabled = true;
        try {
            var resp = await fetch('/api/courses');
            var data = await resp.json();
            var courses = data.courses || [];
            // Find by title match (case-insensitive, partial match) or fall back to most recent
            var topicLower = topic.toLowerCase();
            var latest = courses.find(function(c) {
                return c.title && c.title.toLowerCase().includes(topicLower);
            }) || courses.find(function(c) {
                return c.status === 'available' || c.status === 'ready';
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
