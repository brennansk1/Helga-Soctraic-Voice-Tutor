// session-course-creation.js — Course creation modal, progress tracking, sudo handling
// Extracted from session.js for maintainability

// Course creation modal elements
let courseModal, courseForm, courseTopicInput, creationProgress, creationStatus, progressFill, closeModalBtn;

// Course creation state tracking
let isCreatingCourse = false;
let currentProgressStep = 0;
const progressSteps = {
    'Preparing database': { step: 'prepare', percent: 0, icon: '<span class="i i-download" aria-hidden="true"></span>' },
    'Scraping ZIM files': { step: 'scrape', percent: 20, icon: '<span class="i i-download" aria-hidden="true"></span>' },
    'Vectorizing content': { step: 'vectorize', percent: 40, icon: '<span class="i i-hash" aria-hidden="true"></span>' },
    'Building graph': { step: 'graph', percent: 60, icon: '<span class="i i-build" aria-hidden="true"></span>' },
    'Finalizing course': { step: 'finalize', percent: 80, icon: '<span class="i i-spark" aria-hidden="true"></span>' },
    'Restarting services': { step: 'restart', percent: 100, icon: '<span class="i i-rocket" aria-hidden="true"></span>' }
};

function updateProgressStep(stepId, percentage, detail) {
    // Update progress bar
    progressFill.style.width = percentage + '%';
    document.getElementById('progress-percent').textContent = percentage + '%';

    // Update status text
    creationStatus.textContent = detail;

    // Update step styling - now 6 steps
    const steps = ['prepare', 'scrape', 'vectorize', 'graph', 'finalize', 'restart'];
    steps.forEach(step => {
        const stepEl = document.getElementById('step-' + step);
        if (stepEl) {
            stepEl.classList.remove('active', 'completed');
            if (step === stepId) {
                stepEl.classList.add('active');
            } else if (steps.indexOf(step) < steps.indexOf(stepId)) {
                stepEl.classList.add('completed');
            }
        }
    });
}

function addProgressLog(logEntry, logClass = 'log-entry') {
    const logsContent = document.getElementById('logs-content');
    if (logsContent) {
        const logDiv = document.createElement('div');
        logDiv.className = logClass;
        logDiv.textContent = '> ' + logEntry;
        logsContent.appendChild(logDiv);
        logsContent.parentElement.scrollTop = logsContent.parentElement.scrollHeight;
        console.log('[addProgressLog] Added:', logEntry);
    } else {
        console.warn('[addProgressLog] logs-content element not found');
    }
}

function showCourseCreationModal(topic, depth = '3') {
    if (topic) {
        courseTopicInput.value = topic;
        // Store depth for later use
        courseTopicInput.dataset.depth = depth;
    }
    courseForm.style.display = 'block';
    creationProgress.classList.add('hidden');
    courseModal.classList.remove('hidden');
}

function showCreationProgressModal(topic) {
    // Shows the modal but in "progress" mode, hiding the input form
    if (topic) courseTopicInput.value = topic;
    courseForm.style.display = 'none';
    creationProgress.classList.remove('hidden');
    courseModal.classList.remove('hidden');
    // Initialize progress
    progressFill.style.width = '5%';
    creationStatus.textContent = 'Initializing creation...';
}

function hideCourseCreationModal() {
    courseModal.classList.add('hidden');
    progressFill.style.width = '0%';
    creationStatus.textContent = 'Initializing...';
}

async function submitCourseCreation() {
    const topic = courseTopicInput.value.trim();
    if (!topic) {
        return;
    }

    // Prevent concurrent course creation
    if (isCreatingCourse) {
        showToast('Course creation already in progress. Please wait.', 'warning');
        return;
    }

    // Check if sudo password is available
    try {
        const response = await fetch('/api/check_sudo');
        const data = await response.json();

        if (!data.available) {
            // Show sudo password modal
            showSudoPasswordModal(topic);
            return;
        }
    } catch (error) {
        console.error('Failed to check sudo password:', error);
        // Continue anyway, let the backend handle it
    }

    // Proceed with course creation
    startCourseCreation(topic);
}

function startCourseCreation(topic) {
    isCreatingCourse = true;
    disableCourseCreationButton();
    courseForm.style.display = 'none';
    creationProgress.classList.remove('hidden');

    // Get depth from the input's data attribute or default to 3
    const depth = courseTopicInput.dataset.depth || '3';

    // Include depth in the voice command
    sendEvent('TEXT_INPUT', { text: `create course ${topic} with depth ${depth}` });
}

function disableCourseCreationButton() {
    const submitBtn = courseForm.querySelector('button[type="submit"]');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.classList.add('disabled');
    }
}

function enableCourseCreationButton() {
    const submitBtn = courseForm.querySelector('button[type="submit"]');
    if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.classList.remove('disabled');
    }
}

// Sudo password handling
let pendingCourseTopic = null;

function showSudoPasswordModal(topic) {
    pendingCourseTopic = topic;
    const sudoModal = document.getElementById('sudo-password-modal');
    const sudoError = document.getElementById('sudo-error');
    const sudoInput = document.getElementById('sudo-password-input');

    if (sudoModal && sudoInput) {
        sudoInput.value = '';
        if (sudoError) sudoError.classList.add('hidden');
        sudoModal.classList.remove('hidden');
        sudoInput.focus();
    }
}

function closeSudoModal() {
    const sudoModal = document.getElementById('sudo-password-modal');
    if (sudoModal) {
        sudoModal.classList.add('hidden');
    }
    pendingCourseTopic = null;
    isCreatingCourse = false;
    enableCourseCreationButton();
}

async function submitSudoPassword(event) {
    event.preventDefault();

    const sudoInput = document.getElementById('sudo-password-input');
    const sudoError = document.getElementById('sudo-error');
    const password = sudoInput.value;

    if (!password) {
        if (sudoError) {
            sudoError.textContent = 'Please enter a password';
            sudoError.classList.remove('hidden');
        }
        return;
    }

    try {
        const response = await fetch('/api/set_sudo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: password })
        });

        const data = await response.json();

        if (data.success) {
            // Close modal and proceed with course creation
            const sudoModal = document.getElementById('sudo-password-modal');
            if (sudoModal) sudoModal.classList.add('hidden');

            if (pendingCourseTopic) {
                startCourseCreation(pendingCourseTopic);
                pendingCourseTopic = null;
            }
        } else {
            if (sudoError) {
                sudoError.textContent = data.error || 'Invalid password. Please try again.';
                sudoError.classList.remove('hidden');
            }
            sudoInput.value = '';
            sudoInput.focus();
        }
    } catch (error) {
        console.error('Failed to submit sudo password:', error);
        if (sudoError) {
            sudoError.textContent = 'Failed to submit password. Please try again.';
            sudoError.classList.remove('hidden');
        }
    }
}
