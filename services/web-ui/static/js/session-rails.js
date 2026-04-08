// session-rails.js — Side rail UI: context navigation, flashcards, memory palace
// Extracted from session.js for maintainability

function toggleRails(mode) {
    const contextRail = document.getElementById('context-rail');
    const flashcardRail = document.getElementById('flashcard-rail');
    const palaceRail = document.getElementById('palace-rail');

    if (contextRail) contextRail.classList.toggle('hidden', mode !== 1);
    if (flashcardRail) flashcardRail.classList.toggle('hidden', mode !== 2);
    if (palaceRail) palaceRail.classList.toggle('hidden', mode !== 3);
}

function updateContextRail(courseData, nodeId) {
    if (!courseData) {
        // Fetch it if not provided in the state. /api/course_structure
        // requires a ?uid= param or it 502s — grab it from the URL or the
        // global FSM state first.
        const uid = (new URLSearchParams(window.location.search)).get('course_uid')
                    || (window.currentState && window.currentState.active_course_uid);
        if (!uid) {
            const rail = document.getElementById('context-rail');
            if (rail) rail.innerHTML = '';
            return;
        }
        fetch('/api/course_structure?uid=' + encodeURIComponent(uid))
            .then(response => response.json())
            .then(data => renderContextRail(data, nodeId))
            .catch(error => {
                console.error('Error fetching course structure:', error);
                const rail = document.getElementById('context-rail');
                if (rail) rail.innerHTML = '<p class="error">Could not load course data.</p>';
            });
    } else {
        renderContextRail(courseData, nodeId);
    }
}

function renderContextRail(courseData, currentNodeId) {
    const titleEl = document.getElementById('course-title');
    const breadcrumbsEl = document.getElementById('breadcrumb-path');
    const facetsListEl = document.getElementById('facets-list');
    const progressPercentEl = document.getElementById('progress-percent');
    const progressBarEl = document.getElementById('progress-bar');

    if (!titleEl || !breadcrumbsEl || !facetsListEl || !progressPercentEl || !progressBarEl) return;

    titleEl.textContent = courseData.title || 'Course';
    breadcrumbsEl.textContent = (courseData.breadcrumbs || []).join(' > ');

    facetsListEl.innerHTML = '';
    if (courseData.facets && courseData.facets.length) {
        courseData.facets.forEach(facet => {
            const li = document.createElement('li');
            li.textContent = facet.name;
            li.dataset.id = facet.id;
            li.className = facet.completed ? 'completed' : '';
            if (facet.id === currentNodeId || facet.active) {
                li.classList.add('active');
            }
            li.addEventListener('click', () => sendEvent('NAVIGATE_TO_TOPIC', { topic_id: facet.id }));
            facetsListEl.appendChild(li);
        });
    } else {
        facetsListEl.innerHTML = '<li>No topics found.</li>';
    }

    const progress = courseData.progress || 0;
    progressPercentEl.textContent = `${progress}%`;
    const circumference = 2 * Math.PI * 45;
    progressBarEl.style.strokeDashoffset = circumference - (progress / 100) * circumference;
}

// Track last rendered pedagogy to avoid flickering on repeated polls
let _lastPedagogyKey = '';

function renderPedagogy(nodeData) {
    const pedagogySection = document.getElementById('pedagogy-hints');
    const misconceptionsList = document.getElementById('misconceptions-list');
    const analogiesList = document.getElementById('analogies-list');

    if (!pedagogySection || !misconceptionsList || !analogiesList) return;

    const misconceptions = nodeData.misconceptions || [];
    const analogies = nodeData.analogies || [];

    // Skip re-render if data hasn't changed (prevents DOM flickering every 2s)
    const newKey = JSON.stringify(misconceptions) + JSON.stringify(analogies);
    if (newKey === _lastPedagogyKey) return;
    _lastPedagogyKey = newKey;

    if (misconceptions.length === 0 && analogies.length === 0) {
        pedagogySection.classList.add('hidden');
        return;
    }

    pedagogySection.classList.remove('hidden');
    misconceptionsList.innerHTML = misconceptions.length ? '<h5>Common Pitfalls</h5>' : '';
    analogiesList.innerHTML = analogies.length ? '<h5>Analogies</h5>' : '';

    misconceptions.forEach(m => {
        const div = document.createElement('div');
        div.className = 'hint-item misconception';
        div.textContent = m;
        misconceptionsList.appendChild(div);
    });

    analogies.forEach(a => {
        const div = document.createElement('div');
        div.className = 'hint-item analogy';
        div.textContent = a;
        analogiesList.appendChild(div);
    });
}

function updateFlashcard(state) {
    const promptEl = document.getElementById('prompt-text');
    const answerEl = document.getElementById('answer-text');
    const answerDiv = document.getElementById('flashcard-answer');
    const revealBtn = document.getElementById('reveal-answer-btn');

    if (promptEl && state.last_question) {
        promptEl.textContent = state.last_question;
    }
    if (answerEl && state.current_card_text) {
        answerEl.textContent = state.current_card_text;
    }
    // Initially hide answer
    if (answerDiv) answerDiv.classList.add('hidden');
    if (revealBtn) {
        revealBtn.classList.remove('hidden');
        revealBtn.onclick = () => {
            answerDiv.classList.remove('hidden');
            revealBtn.classList.add('hidden');
        };
    }
}

function updatePalace(state) {
    const descEl = document.getElementById('locus-description');
    if (descEl) {
        descEl.textContent = state.locus || 'No location';
    }
    // Simple map visualization
    const canvas = document.getElementById('palace-canvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, 400, 400);
        ctx.fillStyle = '#f0f0f0';
        ctx.fillRect(0, 0, 400, 400);
        ctx.fillStyle = '#000';
        ctx.font = '20px Arial';
        ctx.fillText('Memory Palace', 120, 200);
        // Draw current locus as a red dot
        ctx.fillStyle = '#ff0000';
        ctx.beginPath();
        ctx.arc(200, 200, 10, 0, 2 * Math.PI);
        ctx.fill();
    }
}
