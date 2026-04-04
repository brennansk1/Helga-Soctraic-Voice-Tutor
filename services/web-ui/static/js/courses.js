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

async function loadCourses() {
    const grid = document.getElementById('courses-grid');
    try {
        const resp = await fetch('/api/courses');
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
                actionButton = `<button class="btn-alpine btn-alpine-primary" style="flex: 1;" onclick="startCourse('${course.uid}', '${escapeHtml(course.title)}')">${progress > 0 ? 'Continue' : 'Start Learning'}</button>`;
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
                        <button class="btn-alpine btn-alpine-secondary" style="padding: 0.5rem 0.75rem;" onclick="window.location.href='/course/view?uid=${course.uid}'" title="View Structure">📋</button>
                        <button class="btn-alpine btn-alpine-danger" style="padding: 0.5rem 0.75rem;" onclick="deleteCourse('${course.uid}', '${escapeHtml(course.title)}')" title="Delete">🗑</button>
                    </div>
                </div>
            `;
            grid.appendChild(card);
        });
    } catch (e) {
        grid.innerHTML = `
            <div class="empty-state" style="grid-column: 1/-1;">
                <div class="empty-icon">⚠️</div>
                <h3>Failed to load courses</h3>
                <p>${escapeHtml(e.message)}</p>
            </div>
        `;
    }
}

async function startCourse(uid, title) {
    try {
        await fetch('/api/set_active_course', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ uid, title })
        });
        window.location.href = '/learn?course_uid=' + encodeURIComponent(uid);
    } catch (e) {
        if (window.showToast) window.showToast('Failed to start course', 'error');
    }
}

async function deleteCourse(uid, title) {
    if (!confirm('Delete "' + title + '"? This cannot be undone.')) return;
    try {
        await fetch('/api/delete_course?uid=' + encodeURIComponent(uid), { method: 'DELETE' });
        loadCourses();
    } catch (e) {
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
}

function closeQuickCreate() {
    document.getElementById('quick-create-backdrop').classList.remove('active');
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
    setPhase('complete');
    var bar = document.getElementById('qc-progress-bar');
    if (bar) { bar.style.width = '100%'; bar.style.background = 'var(--status-success)'; }

    document.getElementById('qc-progress-status').textContent = 'Course created successfully!';
    document.getElementById('qc-progress-actions').style.display = 'block';

    var titleEl = document.getElementById('qc-complete-title');
    if (titleEl) titleEl.textContent = topic + ' — Ready!';

    // Fetch actual stats from API (buildState may be stale after audit)
    var summary = document.getElementById('qc-complete-summary');
    if (summary) {
        var modCount = buildState.moduleOrder.length;
        var conCount = buildState.totalConcepts;
        if (modCount > 0 && conCount > 0) {
            summary.textContent = modCount + ' modules, ' + conCount + ' concepts generated';
        } else {
            // Fallback: fetch from API
            fetch('/api/courses').then(function(r) { return r.json(); }).then(function(data) {
                var courses = data.courses || [];
                var match = courses.find(function(c) { return c.title && c.title.toLowerCase().includes(topic.toLowerCase()); }) || courses[0];
                if (match) {
                    fetch('/api/course_structure?uid=' + match.uid).then(function(r) { return r.json(); }).then(function(cs) {
                        var mods = (cs.structure || {}).modules || [];
                        var cons = 0;
                        mods.forEach(function(m) {
                            (m.units || []).forEach(function(u) {
                                (u.lessons || []).forEach(function(l) {
                                    cons += (l.concepts || []).length;
                                });
                            });
                        });
                        summary.textContent = mods.length + ' modules, ' + cons + ' concepts generated';
                    }).catch(function() {});
                }
            }).catch(function() {
                summary.textContent = 'Course created';
            });
        }
    }

    // Trigger confetti
    if (window.showConfetti) window.showConfetti();
}

async function submitQuickCreate() {
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

    // Connect socket for progress
    var socket = io();
    socket.on('status_update', function(data) {
        var msg = data.message || '';
        var statusEl = document.getElementById('qc-progress-status');

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
                // Switch to hydration phase on first hydrating event
                if (buildState.phase === 'skeleton') setPhase('hydrate');
                var hUid = parts[2];
                var hStatus = parts[3]; // START, STRUCTURING, etc.
                var hTitle = parts.slice(4).join(':');
                statusEl.textContent = 'Generating content: ' + hTitle;
                updateHydrationStatus(hUid, hStatus, hTitle);
            } else if (sType === 'HYDRATED') {
                var dUid = parts[2];
                var dSource = parts[3];
                var dTitle = parts.slice(4).join(':');
                updateHydrationStatus(dUid, 'DONE', dTitle);
                statusEl.textContent = 'Hydrated: ' + dTitle;
            }
        }
        // --- Phase markers ---
        else if (msg.startsWith('SYLLABUS:PHASE:')) {
            var phase = msg.split(':')[2];
            if (phase === '1_SKELETON') {
                setPhase('skeleton');
                statusEl.textContent = 'Building course skeleton...';
            }
        }
        // --- Audit events ---
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
        // --- Progressive availability: show "Start Learning" early ---
        else if (msg === 'COURSE_AVAILABLE') {
            var actionsEl = document.getElementById('qc-progress-actions');
            if (actionsEl) actionsEl.style.display = 'block';
            statusEl.textContent = 'First concepts ready! You can start learning while the rest builds.';
        }
        // --- Completion ---
        else if (msg === 'COURSE_COMPLETE' || msg.includes('successfully') || msg.includes('System Idle')) {
            showCompletion(topic);
        }
        // --- General status ---
        else if (!msg.startsWith('CHECK:') && !msg.startsWith('CPROG:') && !msg.startsWith('PEDAGOGY:') && !msg.startsWith('QTYPE:')) {
            statusEl.textContent = msg;
        }

        // --- Progress from backend ---
        if (data.progress) {
            var bar = document.getElementById('qc-progress-bar');
            if (bar) bar.style.width = data.progress + '%';
        }

        // --- Log entries ---
        if (msg.startsWith('LOG:')) {
            addLogEntry(msg.substring(4).trim(), 'info');
        } else if (msg.startsWith('Error') || msg.startsWith('ERROR')) {
            addLogEntry(msg, 'error');
        } else if (msg.startsWith('CHECK:')) {
            var checkParts = msg.split(':');
            var checkResult = checkParts[2]; // PASS, FAIL, WARN
            addLogEntry(checkParts.slice(3).join(':'), checkResult === 'PASS' ? 'success' : checkResult === 'FAIL' ? 'error' : 'warn');
        }
    });

    // Submit via REST — three-slider system
    try {
        await fetch('/api/create_course', {
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
    } catch (e) {
        document.getElementById('qc-progress-status').textContent = 'Error: ' + e.message;
    }

    // Start Learning button
    document.getElementById('qc-start-learning-btn').onclick = async function() {
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
});
