
/* Watch a custom-course build to completion.
   The wizard previously had no way to observe a build it had started: the
   server answered immediately with a placeholder uid and the UI declared
   victory. This asks the same endpoint the automatic-creation flow uses. */
async function pollCustomCourseBuild(statusEl, progressEl) {
    const started = Date.now();
    const MAX_MS = 30 * 60 * 1000;
    while (Date.now() - started < MAX_MS) {
        await new Promise(r => setTimeout(r, 5000));
        let st;
        try {
            const r = await fetch('/api/creation_status');
            if (!r.ok) continue;
            st = await r.json();
        } catch (_) { continue; }

        if (st.message && statusEl) statusEl.textContent = st.message;
        if (typeof st.percent === 'number' && progressEl) {
            progressEl.style.width = Math.max(5, Math.min(99, st.percent)) + '%';
        }
        const phase = (st.phase || '').toLowerCase();
        if (st.course_uid && (phase === 'complete' || phase === 'done')) {
            if (progressEl) {
                progressEl.style.width = '100%';
                progressEl.style.background = 'var(--status-success)';
            }
            if (statusEl) statusEl.textContent = 'Course created successfully!';
            window.location.href = '/learn?course_uid=' + st.course_uid;
            return;
        }
        if (phase === 'error' || phase === 'aborted' || phase === 'failed') {
            if (statusEl) {
                statusEl.textContent = 'Build failed: ' + (st.message || phase);
                statusEl.style.color = 'var(--status-error)';
            }
            showCustomGenerationRetry(statusEl);
            return;
        }
    }
    if (statusEl) {
        statusEl.textContent =
            'Still building after 30 minutes — check the Courses page.';
    }
}
/**
 * wizard.js — Custom Course Builder (5-step wizard)
 * Path B of the dual-path course creation system.
 */

const wizardState = {
    currentStep: 1,
    title: '',
    description: '',
    prior_knowledge: 'new',
    teaching_style: '',
    content_source: 'llm',  // UI-6: Store content_source at step 1
    modules: [],
    clarification_answers: [],
    course_uid: null
};

// --- Step Navigation ---

function wizardNext() {
    const step = wizardState.currentStep;

    // Validate current step
    if (step === 1) {
        const title = document.getElementById('wiz-title').value.trim();
        if (title.length < 3) {
            document.getElementById('wiz-title').style.borderColor = 'var(--status-error)';
            return;
        }
        wizardState.title = title;
        wizardState.description = document.getElementById('wiz-description').value.trim();
        wizardState.prior_knowledge = document.querySelector('input[name="wiz-background"]:checked')?.value || 'new';
        wizardState.teaching_style = document.getElementById('wiz-style').value;
        // UI-6: Capture content_source at step 1 so it persists across navigation
        var csEl = document.querySelector('input[name="wizard_content_source"]:checked');
        wizardState.content_source = csEl ? csEl.value : 'llm';
    }

    if (step === 2) {
        collectModulesFromDOM();
        if (wizardState.modules.length === 0) {
            wizardToast('Add at least one module', 'warning');
            return;
        }
    }

    if (step === 3) {
        collectConceptsFromDOM();
    }

    if (step === 4) {
        collectAnswersFromDOM();
        // Step 5 triggers generation
        showStep(5);
        startCustomGeneration();
        return;
    }

    const nextStep = step + 1;
    showStep(nextStep);

    // Step 4 triggers Q&A generation
    if (nextStep === 4) {
        generateClarifyingQuestions();
    }
}

function wizardBack() {
    if (wizardState.currentStep > 1) {
        showStep(wizardState.currentStep - 1);
    }
}

function showStep(stepNum) {
    wizardState.currentStep = stepNum;

    // Hide all steps
    document.querySelectorAll('.wiz-step').forEach(el => el.style.display = 'none');

    // Show target step
    const target = document.getElementById(`wiz-step-${stepNum}`);
    if (target) target.style.display = 'block';

    // Update progress bar
    document.querySelectorAll('.wizard-step').forEach(el => {
        const s = parseInt(el.dataset.step);
        el.classList.remove('active', 'completed');
        if (s === stepNum) el.classList.add('active');
        else if (s < stepNum) el.classList.add('completed');
    });

    // Render step-specific content
    if (stepNum === 2) renderModuleCards();
    if (stepNum === 3) renderDetailCards();
}

// --- Step 2: Module Cards ---

function renderModuleCards() {
    const container = document.getElementById('wiz-modules-list');
    container.innerHTML = '';
    wizardState.modules.forEach((mod, idx) => {
        const card = document.createElement('div');
        card.className = 'module-card';
        card.innerHTML = `
            <button class="module-remove" onclick="removeModule(${idx})">&times;</button>
            <div style="margin-bottom: 0.75rem;">
                <label>Module ${idx + 1} Title</label>
                <input type="text" class="mod-title" data-idx="${idx}" value="${escapeHtml(mod.title)}" placeholder="e.g. Kinematics">
            </div>
            <div>
                <label>Note to Helga (optional)</label>
                <textarea class="mod-note" data-idx="${idx}" rows="2" placeholder="e.g. Focus on projectile motion, skip rotational...">${escapeHtml(mod.note || '')}</textarea>
            </div>
        `;
        container.appendChild(card);
    });
}

function addModuleCard() {
    wizardState.modules.push({ title: '', note: '', concepts: [] });
    renderModuleCards();
    // Focus the new module's title input
    const inputs = document.querySelectorAll('.mod-title');
    if (inputs.length) inputs[inputs.length - 1].focus();
}

function removeModule(idx) {
    wizardState.modules.splice(idx, 1);
    renderModuleCards();
}

function collectModulesFromDOM() {
    document.querySelectorAll('.mod-title').forEach(el => {
        const idx = parseInt(el.dataset.idx);
        if (wizardState.modules[idx]) {
            wizardState.modules[idx].title = el.value.trim();
        }
    });
    document.querySelectorAll('.mod-note').forEach(el => {
        const idx = parseInt(el.dataset.idx);
        if (wizardState.modules[idx]) {
            wizardState.modules[idx].note = el.value.trim();
        }
    });
    // Remove empty modules
    wizardState.modules = wizardState.modules.filter(m => m.title.length > 0);
}

async function suggestModules() {
    const btn = document.getElementById('suggest-modules-btn');
    btn.disabled = true;
    btn.textContent = 'Generating...';

    try {
        const resp = await fetch('/api/suggest_modules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: wizardState.title,
                description: wizardState.description,
                prior_knowledge: wizardState.prior_knowledge
            })
        });
        const data = await resp.json();
        const suggestions = data.modules || [];

        /* THE SUGGESTED TITLE IS DATA, AND IT USED TO BE CODE.
           This card carried onclick="acceptSuggestion(this, 'TITLE')" with the
           title run through escapeHtml(). Wrong escape for the position: the
           HTML parser decodes &#39; back to a bare apostrophe BEFORE the
           attribute is parsed as JavaScript, so a module the model called
           "Newton's Laws" produced a SyntaxError and an Accept button that did
           nothing. The title now travels as a data-* attribute and is read back
           as a string; nothing derived from a response is ever parsed as code.
           Titles and descriptions go in with textContent for the same reason. */
        const container = document.getElementById('wiz-modules-list');
        suggestions.forEach(s => {
            const card = document.createElement('div');
            card.className = 'suggestion-card';

            const text = document.createElement('div');
            const strong = document.createElement('strong');
            strong.textContent = s.title || '';
            const sub = document.createElement('div');
            sub.style.cssText = 'font-size: 0.8rem; color: var(--text-secondary);';
            sub.textContent = s.description || '';
            text.appendChild(strong);
            text.appendChild(sub);

            const acts = document.createElement('div');
            acts.className = 'suggestion-actions';
            const accept = document.createElement('button');
            accept.className = 'accept-btn';
            accept.textContent = 'Accept';
            accept.dataset.title = s.title || '';
            accept.addEventListener('click', function () {
                acceptSuggestion(accept, accept.dataset.title);
            });
            const dismiss = document.createElement('button');
            dismiss.className = 'dismiss-btn';
            dismiss.textContent = 'Dismiss';
            dismiss.addEventListener('click', function () { card.remove(); });
            acts.appendChild(accept);
            acts.appendChild(dismiss);

            card.appendChild(text);
            card.appendChild(acts);
            container.appendChild(card);
        });
    } catch (e) {
        wizardToast('Failed to generate suggestions', 'error');
    }

    btn.disabled = false;
    btn.textContent = 'Suggest Modules';
}

function acceptSuggestion(btn, title) {
    wizardState.modules.push({ title: title, note: '', concepts: [] });
    btn.closest('.suggestion-card').remove();
    renderModuleCards();
}

// --- Step 3: Detail Cards (Concepts per Module) ---

function renderDetailCards() {
    const container = document.getElementById('wiz-details-list');
    container.innerHTML = '';
    wizardState.modules.forEach((mod, idx) => {
        const conceptsHtml = (mod.concepts || []).map((c, ci) =>
            `<span class="concept-chip">${escapeHtml(c.title)} <span class="remove-concept" onclick="removeConcept(${idx}, ${ci})">&times;</span></span>`
        ).join(' ');

        const card = document.createElement('div');
        card.className = 'module-card';
        card.innerHTML = `
            <h3 style="margin: 0 0 0.5rem 0; font-size: 1rem;"><span class="i i-package" aria-hidden="true"></span> ${escapeHtml(mod.title)}</h3>
            ${mod.note ? `<p style="font-size: 0.8rem; color: var(--text-secondary); margin: 0 0 0.75rem 0;">Note: ${escapeHtml(mod.note)}</p>` : ''}
            <div class="concept-list" id="concepts-${idx}" style="margin-bottom: 0.75rem; display: flex; flex-wrap: wrap; gap: 0.4rem;">
                ${conceptsHtml || '<span style="color: var(--text-secondary); font-size: 0.85rem;">Helga will generate all concepts for this module.</span>'}
            </div>
            <div style="display: flex; gap: 0.5rem; align-items: center;">
                <input type="text" id="new-concept-${idx}" placeholder="Add a concept..." style="flex: 1; padding: 0.4rem 0.6rem; border: 1px solid var(--border-color); border-radius: 6px; background: var(--bg-primary); color: var(--text-primary); font-size: 0.85rem;">
                <button class="btn-alpine btn-alpine-secondary" style="padding: 0.4rem 0.7rem; font-size: 0.8rem;" onclick="addConcept(${idx})">Add</button>
                <button class="btn-alpine btn-alpine-accent" style="padding: 0.4rem 0.7rem; font-size: 0.8rem;" onclick="suggestConcepts(${idx})">Suggest</button>
            </div>
        `;
        container.appendChild(card);

        // Enter key to add concept
        const input = card.querySelector(`#new-concept-${idx}`);
        input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addConcept(idx); } });
    });
}

function addConcept(modIdx) {
    const input = document.getElementById(`new-concept-${modIdx}`);
    const title = input.value.trim();
    if (!title) return;
    if (!wizardState.modules[modIdx].concepts) wizardState.modules[modIdx].concepts = [];
    wizardState.modules[modIdx].concepts.push({ title: title, note: '', user_defined: true });
    input.value = '';
    renderDetailCards();
}

function removeConcept(modIdx, conceptIdx) {
    wizardState.modules[modIdx].concepts.splice(conceptIdx, 1);
    renderDetailCards();
}

async function suggestConcepts(modIdx) {
    const mod = wizardState.modules[modIdx];

    // Show loading state on the Suggest button
    const suggestBtn = document.querySelector(`#new-concept-${modIdx}`)?.parentElement?.querySelector('.btn-alpine-accent');
    if (suggestBtn) {
        suggestBtn.disabled = true;
        suggestBtn.dataset.origText = suggestBtn.textContent;
        suggestBtn.textContent = 'Loading...';
    }

    try {
        const resp = await fetch('/api/suggest_concepts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: wizardState.title,
                description: wizardState.description,
                prior_knowledge: wizardState.prior_knowledge,
                module_title: mod.title,
                module_note: mod.note,
                existing_concepts: (mod.concepts || []).map(c => c.title)
            })
        });
        const data = await resp.json();
        const suggestions = data.concepts || [];

        // Same fix as the module suggestions above: a concept title the model
        // wrote is data, so it goes in as a data-* attribute and textContent,
        // never as a JavaScript string literal inside an onclick.
        const container = document.getElementById(`concepts-${modIdx}`).parentElement;
        suggestions.forEach(s => {
            const card = document.createElement('div');
            card.className = 'suggestion-card';
            card.style.marginTop = '0.5rem';

            const label = document.createElement('span');
            label.textContent = s.title || '';

            const acts = document.createElement('div');
            acts.className = 'suggestion-actions';
            const add = document.createElement('button');
            add.className = 'accept-btn';
            add.textContent = 'Add';
            add.dataset.title = s.title || '';
            add.addEventListener('click', function () {
                acceptConceptSuggestion(add, modIdx, add.dataset.title);
            });
            const skip = document.createElement('button');
            skip.className = 'dismiss-btn';
            skip.textContent = 'Skip';
            skip.addEventListener('click', function () { card.remove(); });
            acts.appendChild(add);
            acts.appendChild(skip);

            card.appendChild(label);
            card.appendChild(acts);
            container.appendChild(card);
        });
    } catch (e) {
        wizardToast('Failed to suggest concepts', 'error');
    } finally {
        // Reset Suggest button state
        if (suggestBtn) {
            suggestBtn.disabled = false;
            suggestBtn.textContent = suggestBtn.dataset.origText || 'Suggest';
        }
    }
}

function acceptConceptSuggestion(btn, modIdx, title) {
    if (!wizardState.modules[modIdx].concepts) wizardState.modules[modIdx].concepts = [];
    wizardState.modules[modIdx].concepts.push({ title: title, note: '', user_defined: false });
    btn.closest('.suggestion-card').remove();
    renderDetailCards();
}

function collectConceptsFromDOM() {
    // Concepts are already in wizardState.modules[].concepts from add/suggest actions
}

// --- Step 4: Clarifying Questions ---

async function generateClarifyingQuestions() {
    const loadingEl = document.getElementById('wiz-questions-loading');
    const listEl = document.getElementById('wiz-questions-list');
    // Reset the loader to its animated default — a previous failure may
    // have replaced the inner HTML with an error paragraph.
    loadingEl.style.display = 'block';
    loadingEl.innerHTML =
        '<div class="chat-msg tutor typing" style="display:inline-flex; margin-bottom:1rem;">' +
        '<span class="dot"></span><span class="dot"></span><span class="dot"></span>' +
        '</div>' +
        '<p>Generating clarifying questions…</p>';
    listEl.style.display = 'none';

    try {
        const resp = await fetch('/api/clarify_course', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(wizardState)
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        const questions = data.questions || [];

        listEl.innerHTML = '';
        questions.forEach((q, idx) => {
            const card = document.createElement('div');
            card.className = 'question-card';
            card.innerHTML = `
                <div class="q-text">${escapeHtml(q.question)}</div>
                ${q.context ? `<div class="q-context">${escapeHtml(q.context)}</div>` : ''}
                <textarea class="q-answer" data-idx="${idx}" rows="2" placeholder="Your answer (optional)..."></textarea>
            `;
            listEl.appendChild(card);
        });

        loadingEl.style.display = 'none';
        listEl.style.display = 'flex';
    } catch (e) {
        // Replace the loader with an error state + retry button so the user
        // has a path forward rather than being stranded on a spinner.
        loadingEl.innerHTML =
            '<p style="color: var(--status-error); margin-bottom: 0.75rem;">Failed to generate questions. You can retry, or proceed without them.</p>' +
            '<button type="button" class="btn-alpine btn-alpine-secondary" id="wiz-questions-retry-btn">Retry</button>';
        const retryBtn = document.getElementById('wiz-questions-retry-btn');
        if (retryBtn) retryBtn.addEventListener('click', generateClarifyingQuestions);
    }
}

function collectAnswersFromDOM() {
    wizardState.clarification_answers = [];
    document.querySelectorAll('.q-answer').forEach(el => {
        const text = el.value.trim();
        if (text) {
            const qText = el.closest('.question-card').querySelector('.q-text').textContent;
            wizardState.clarification_answers.push({ question: qText, answer: text });
        }
    });
}

// --- Step 5: Generation ---

async function startCustomGeneration() {
    const statusEl = document.getElementById('wiz-gen-status');
    const progressEl = document.getElementById('wiz-gen-progress');
    const treeEl = document.getElementById('wiz-gen-tree');

    statusEl.style.color = '';  // reset from a previous failed attempt
    statusEl.textContent = 'Creating course structure...';
    progressEl.style.width = '5%';
    progressEl.style.background = '';  // reset success/error tint
    treeEl.innerHTML = '<div class="build-tree-placeholder"><div class="skeleton-pulse"></div><span>Generating structure...</span></div>';

    // Connect Socket.IO for progress updates. The socket lives until the build
    // reports DONE or ERROR (plus 5s of grace for trailing STRUCT events).
    const socket = io();
    let conceptCount = 0;
    let hydratedCount = 0;
    let completed = false;
    const buildWarnings = [];

    // The socket used to be closed 5s after the REST call returned. That call
    // only ACKs the build, so the wizard was disconnecting itself seconds into
    // a twenty-minute build and never saw another event. It now stays open
    // until the build actually ends.
    function closeSocket() {
        setTimeout(() => { try { socket.disconnect(); } catch (_) {} }, 5000);
    }

    // Shown when the build genuinely finishes — either the DONE pipeline stage
    // or a synchronous REST response that already carries a real uid.
    function showWizardComplete(courseUid) {
        if (completed) return;
        completed = true;
        clearTimeout(stallTimer);
        if (courseUid) wizardState.course_uid = courseUid;
        progressEl.style.width = '100%';
        progressEl.style.background = 'var(--status-success)';
        statusEl.style.color = '';
        statusEl.textContent = 'Course created successfully!';
        document.getElementById('wiz-gen-complete').style.display = 'block';

        const summaryEl = document.getElementById('wiz-gen-summary');
        summaryEl.textContent = `${wizardState.title} — ${wizardState.modules.length} modules`;
        if (buildWarnings.length) {
            const warn = document.createElement('div');
            warn.className = 'build-warn';
            warn.style.cssText = 'margin-top:0.5rem;color:var(--status-warning);font-size:0.85rem;text-align:left;';
            const shown = buildWarnings.slice(0, 8).map(w => `<li>${escapeHtml(w)}</li>`);
            if (buildWarnings.length > 8) {
                shown.push(`<li>…and ${buildWarnings.length - 8} more</li>`);
            }
            // i-warning mask icon from icons.css — emoji are banned in this UI.
            warn.innerHTML =
                '<strong><span class="i i-warning" aria-hidden="true"></span> ' +
                'Quality notes on this course</strong>' +
                `<ul style="margin:0.25rem 0 0 1rem;">${shown.join('')}</ul>`;
            summaryEl.parentNode.insertBefore(warn, summaryEl.nextSibling);
        }

        const startBtn = document.getElementById('wiz-start-learning-btn');
        const uid = wizardState.course_uid;
        startBtn.onclick = async () => {
            startBtn.classList.add('is-loading');
            startBtn.disabled = true;
            try {
                await fetch('/api/set_active_course', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ uid: uid, title: wizardState.title })
                });
            } catch (_) { /* best effort */ }
            window.location.href = '/learn?course_uid=' + encodeURIComponent(uid);
        };
        closeSocket();
    }

    // Safety timeout — if no events arrive within 60s, surface the problem.
    const stallTimer = setTimeout(() => {
        if (conceptCount === 0 && hydratedCount === 0) {
            statusEl.textContent = 'Still waiting for the server… the build may be slow to start.';
            statusEl.style.color = 'var(--status-warning)';
        }
    }, 60000);

    socket.on('status_update', data => {
        const msg = data.message || '';

        // B6.4: structured pipeline stages are authoritative. This replaces
        // `msg.includes('successfully')`, which matched the builder's own
        // "Preflight checks completed successfully" and drove this bar to 100%
        // seconds into a build that had not written a single concept.
        if (data.event && data.event.type === 'PIPELINE_STAGE') {
            const ev = data.event;
            if (ev.stage === 'ERROR') {
                clearTimeout(stallTimer);
                statusEl.textContent = data.message || 'Build failed.';
                statusEl.style.color = 'var(--status-error)';
                progressEl.style.background = 'var(--status-error)';
                showCustomGenerationRetry(statusEl);
                closeSocket();
                return;
            }
            if (ev.stage === 'DONE') {
                showWizardComplete(ev.course_uid);
                return;
            }
            clearTimeout(stallTimer);
            statusEl.textContent = data.message || ev.stage;
            if (typeof ev.pct === 'number') {
                progressEl.style.width = Math.max(0, Math.min(100, ev.pct)) + '%';
            }
            return;
        }

        if (msg.startsWith('STRUCT:')) {
            // Remove placeholder on first real event
            const ph = treeEl.querySelector('.build-tree-placeholder');
            if (ph) ph.remove();

            // Delegate to shared ProgressTree component
            const result = ProgressTree.handleStructMessage(msg, treeEl, statusEl);

            // Quality caveats — below-level content, unverified claims. The
            // shared component renders them into the tree and flags them here;
            // without this the only evidence that a course is weak dies in the
            // socket handler.
            if (result.isWarning) buildWarnings.push(result.title);

            // Track concept count for progress bar
            if (result.type === 'CONCEPT') {
                conceptCount++;
            } else if (result.isDone) {
                hydratedCount++;
                if (conceptCount > 0) {
                    progressEl.style.width = Math.min(95, 30 + (hydratedCount / conceptCount) * 65) + '%';
                }
                statusEl.textContent = 'Hydrated ' + hydratedCount + '/' + conceptCount;
            }
            treeEl.scrollTop = treeEl.scrollHeight;
        } else if (msg === 'COURSE_COMPLETE') {
            // Ignored on purpose: COURSE_COMPLETE is the teaching loop's
            // "learner finished studying" signal, not a build signal. Build
            // completion arrives as the PIPELINE_STAGE DONE event above.
        } else if (!msg.startsWith('CPROG:') && !msg.startsWith('QTYPE:') && !msg.startsWith('PEDAGOGY:')) {
            statusEl.textContent = msg;
        }
        if (data.progress) {
            progressEl.style.width = data.progress + '%';
        }
    });

    try {
        /* PREVIEW, THEN CREATE — so the outline the learner wrote is the
           outline that gets built.

           This posted the whole wizardState to /api/create_course_custom,
           which reads `title` and nothing else and builds a generic topic
           course. Measured: one module named "Cache-Control and ETag" with
           three named concepts produced six modules none of which were mine
           and 145 concepts. See docs/WIZARD_GAP.md.

           The endpoints that honour a structure already existed and were
           never called. /api/custom_course/preview turns the module list into
           a real structure; /api/custom_course/create takes that structure
           plus the modules and builds exactly it. */
        statusEl.textContent = 'Laying out your modules…';
        const previewResp = await fetch('/api/custom_course/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: wizardState.title,
                teaching_style: wizardState.teaching_style,
                modules: wizardState.modules
            })
        });
        const preview = await previewResp.json().catch(() => ({}));
        if (!previewResp.ok || !preview.structure) {
            throw new Error(preview.error || ('Could not lay out the modules (HTTP '
                                              + previewResp.status + ')'));
        }

        // The create proxy takes form data, with the two structures as JSON
        // strings, because it also accepts a source file per module.
        statusEl.textContent = 'Building your course…';
        const fd = new FormData();
        fd.append('title', wizardState.title);
        fd.append('description', wizardState.description || '');
        fd.append('teaching_style', wizardState.teaching_style || '');
        fd.append('content_source', wizardState.content_source || 'llm');
        fd.append('modules', JSON.stringify(wizardState.modules));
        fd.append('structure', JSON.stringify(preview.structure));

        const resp = await fetch('/api/custom_course/create', {
            method: 'POST',
            body: fd
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.error || ('HTTP ' + resp.status));
        }
        const result = await resp.json();
        clearTimeout(stallTimer);

        /* THE BUILD HAS STARTED — IT HAS NOT FINISHED.
           This branch fired on any truthy `course_uid`, and the server sent
           the literal string "pending" before it had built anything, so the
           wizard painted "Course created successfully!" over a build that had
           in fact refused to start, and then navigated to
           /learn?course_uid=pending.
           The server no longer sends a uid it does not have; the real one
           arrives from /api/creation_status when the build actually lands. */
        if (result.course_uid && result.course_uid !== 'pending') {
            wizardState.course_uid = result.course_uid;
            progressEl.style.width = '100%';
            progressEl.style.background = 'var(--status-success)';
            statusEl.textContent = 'Course created successfully!';
            document.getElementById('wiz-gen-complete').style.display = 'block';

            const summaryEl = document.getElementById('wiz-gen-summary');
            summaryEl.textContent = `${wizardState.title} — ${wizardState.modules.length} modules`;

            const startBtn = document.getElementById('wiz-start-learning-btn');
            startBtn.onclick = async () => {
                startBtn.classList.add('is-loading');
                startBtn.disabled = true;
                try {
                    await fetch('/api/set_active_course', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ uid: result.course_uid, title: wizardState.title })
                    });
                } catch (_) { /* best effort */ }
                window.location.href = '/learn?course_uid=' + result.course_uid;
            };
        } else if (result.status === 'building') {
            /* Accepted and running. Poll for the finished course rather than
               claiming success or reporting an error — neither is true yet. */
            statusEl.textContent = 'Building your course — this takes a few minutes…';
            pollCustomCourseBuild(statusEl, progressEl);
        } else {
            statusEl.textContent = 'Error: ' + (result.error || 'Unknown error');
            statusEl.style.color = 'var(--status-error)';
            showCustomGenerationRetry(statusEl);
            closeSocket();
        }
    } catch (e) {
        clearTimeout(stallTimer);
        statusEl.textContent = 'Failed to create course: ' + e.message;
        statusEl.style.color = 'var(--status-error)';
        showCustomGenerationRetry(statusEl);
        closeSocket();
    }
}

function showCustomGenerationRetry(statusEl) {
    if (!statusEl) return;
    // Avoid double-injecting the retry button on repeated failures
    if (statusEl.nextElementSibling && statusEl.nextElementSibling.classList.contains('wiz-gen-retry')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-alpine btn-alpine-secondary wiz-gen-retry';
    btn.style.marginTop = '0.75rem';
    btn.textContent = 'Try again';
    btn.addEventListener('click', () => {
        btn.remove();
        startCustomGeneration();
    });
    statusEl.parentNode.insertBefore(btn, statusEl.nextSibling);
}

// --- Utilities ---

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

// Thin wrapper kept for call-site compatibility. The real showToast lives
// in static/js/toast.js (loaded in base.html before every page's scripts).
// Previously this wrapper recursed into itself via window.showToast because
// function declarations alias the function onto window — calling it would
// stack-overflow on every wizard warning/error.
function wizardToast(msg, type) {
    if (typeof window.showToast === 'function' && window.showToast !== wizardToast) {
        window.showToast(msg, type);
    } else {
        console.warn('[wizard] ' + (type || 'info') + ': ' + msg);
    }
}

// --- Init ---

document.addEventListener('DOMContentLoaded', () => {
    // Pill option toggle
    document.querySelectorAll('.pill-option').forEach(pill => {
        pill.addEventListener('click', () => {
            pill.closest('div').querySelectorAll('.pill-option').forEach(p => p.classList.remove('selected'));
            pill.classList.add('selected');
            pill.querySelector('input').checked = true;
        });
    });

    // Add one empty module by default
    if (wizardState.modules.length === 0) {
        wizardState.modules.push({ title: '', note: '', concepts: [] });
    }
});
