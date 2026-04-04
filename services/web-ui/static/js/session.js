// Session.js - Handles the unified session interface using WebSockets (text-only)
// Audio/WebRTC/microphone code has been removed. TTS is on-demand via addTTSButton().
// Course creation and rail UI code are in separate module files:
//   session-course-creation.js, session-rails.js

let currentMode = 1; // Default to Mode 1
let currentState = {};
let thinkingBubble, thinkingStatusText;
let toggleLogsBtn, thinkingLogsContainer, thinkingLogsCode;
let displayedMessagesCount = 0;
window.navigatingToNode = false; // Guard flag: suppress stale transcript during navigation

window.resetChatSession = function () {
    console.log("[resetChatSession] Clearing chat state");
    const chatStream = document.getElementById('chat-stream');
    if (chatStream) {
        // Remove only standard messages, keep thinking bubbles if they are fresh?
        // Actually, for a clean restart, we probably want to keep the "Initializing..." bubble
        // but for safety, let's keep the .thinking-message check.
        const childrenToRemove = Array.from(chatStream.children).filter(child => !child.classList.contains('thinking-message'));
        childrenToRemove.forEach(child => chatStream.removeChild(child));
    }
    displayedMessagesCount = 0;
};

// Global socket variable (initialized in DOMContentLoaded)
let socket = null;

function getModeFromState(state) {
    if (!state) return 0;
    switch (state) {
        case 'SOCRATIC_LEARNING': return 1;
        case 'SPACED_REPETITION': return 2;
        case 'MEMORY_PALACE': return 3;
        default: return 0; // Lobby or other
    }
}

function updateUI(state) {
    if (!state) {
        console.error("Received null or undefined state. Aborting UI update.");
        return;
    }
    currentState = state;

    // Update pause overlay
    const pauseOverlay = document.getElementById('pause-overlay');
    if (pauseOverlay) {
        pauseOverlay.classList.toggle('hidden', state.state !== 'PAUSED');
    }

    // Update pause/resume button
    const pauseResumeBtn = document.getElementById('pause-resume');
    if (pauseResumeBtn) {
        pauseResumeBtn.textContent = state.state === 'PAUSED' ? 'Resume' : 'Pause';
    }

    // Update mode and rail visibility
    const newMode = getModeFromState(state.state);
    if (newMode !== currentMode) {
        currentMode = newMode;
        toggleRails(currentMode);
    }

    // Update chat stream
    updateChatStream(state.transcript);

    // Update specific mode UIs
    if (currentMode === 1) {
        updateContextRail(state.course_structure, state.graph_node ? state.graph_node.uid : null);
        if (state.graph_node) {
            renderPedagogy(state.graph_node);
        }
    } else if (currentMode === 2) {
        updateFlashcard(state);
    } else if (currentMode === 3) {
        updatePalace(state);
    }
}

// Simple markdown to HTML renderer for chat messages
function renderMarkdown(text) {
    if (!text) return '';
    let html = text
        // Escape HTML entities first
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        // Headers (## before #)
        .replace(/^### (.+)$/gm, '<strong>$1</strong>')
        .replace(/^## (.+)$/gm, '<strong>$1</strong>')
        .replace(/^# (.+)$/gm, '<strong>$1</strong>')
        // Bold **text**
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        // Italic *text*
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        // Line breaks (double newline = paragraph, single = br)
        .replace(/\n\n/g, '</p><p>')
        .replace(/\n/g, '<br>');
    return '<p>' + html + '</p>';
}

function updateChatStream(transcript) {
    if (!transcript) {
        console.warn("Transcript is null or undefined. Skipping chat update.");
        return;
    }

    const chatStream = document.getElementById('chat-stream');
    if (!chatStream) {
        console.error("[updateChatStream] chat-stream element NOT FOUND");
        return;
    }
    console.log("[updateChatStream] Current transcript length:", transcript.length, "displayedCount:", displayedMessagesCount);

    // Navigation guard: when navigating to a new node, the stale boot transcript
    // ("Mnemosyne online...") may arrive before the FSM processes NAVIGATE_TO_TOPIC.
    // We filter it out but DO NOT block — fresh content renders immediately.
    if (window.navigatingToNode) {
        // If transcript is only the boot message, skip this update
        const isBootOnly = transcript.length === 1 &&
            (transcript[0].text || '').toLowerCase().includes('mnemosyne online');
        if (isBootOnly || transcript.length === 0) {
            console.log("[updateChatStream] Navigate guard: skipping boot/empty transcript");
            return;
        }
        // Fresh content from FSM — clear guard and reset for clean render
        console.log("[updateChatStream] Navigate guard: fresh content arrived, clearing guard");
        window.navigatingToNode = false;
        if (window._navGuardTimeout) clearTimeout(window._navGuardTimeout);
        while (chatStream.firstChild) {
            chatStream.removeChild(chatStream.firstChild);
        }
        displayedMessagesCount = 0;
    }

    // Reset chat on transcript length mismatch
    if (transcript.length < displayedMessagesCount) {
        console.log("Transcript reset detected. Clearing chat stream.");
        while (chatStream.firstChild) {
            chatStream.removeChild(chatStream.firstChild);
        }
        displayedMessagesCount = 0;
    }

    // Add new messages
    const newMessages = transcript.slice(displayedMessagesCount);
    if (newMessages.length > 0) {
        newMessages.forEach((message, idx) => {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${message.sender} new-message`;
            messageDiv.dataset.index = displayedMessagesCount + idx;

            let avatar = '';
            if (message.sender === 'ai' || message.sender === 'helga') {
                avatar = `<div class="msg-avatar"><img src="https://ui-avatars.com/api/?name=H&background=3b82f6&color=fff" alt="Helga"></div>`;
            } else if (message.sender === 'user') {
                avatar = `<div class="msg-avatar"><img src="https://ui-avatars.com/api/?name=You&background=10b981&color=fff" alt="You"></div>`;
            }

            const playBtn = (message.sender === 'ai' || message.sender === 'helga') ? '<button class="play-btn">▶</button>' : '';

            // Grade badge: show on the AI message right after a user answer
            let gradeBadge = '';
            if ((message.sender === 'ai' || message.sender === 'helga') && message.grade) {
                const gradeMap = {1: ['Needs Work', 'grade-1'], 2: ['Getting There', 'grade-2'], 3: ['Good', 'grade-3'], 4: ['Excellent', 'grade-4']};
                const [label, cls] = gradeMap[message.grade] || ['', ''];
                if (label) gradeBadge = `<span class="grade-badge ${cls}">${label}</span>`;
            }

            messageDiv.innerHTML = `
                ${avatar}
                <div class="msg-content" data-rawtext="${message.text.replace(/"/g, '&quot;')}">
                    ${gradeBadge}
                    ${renderMarkdown(message.text)}
                    ${playBtn}
                </div>
            `;
            chatStream.appendChild(messageDiv);
        });
        displayedMessagesCount = transcript.length;
    }

    // Auto-scroll to the bottom
    chatStream.scrollTop = chatStream.scrollHeight;
}


function handleThinkingUpdate(data) {
    const message = data.message;
    const logEntry = data.log;

    console.log('[handleThinkingUpdate] Received data:', JSON.stringify(data));

    // Normalize message (handle LOG: prefix)
    let displayMessage = message || '';
    if (displayMessage.startsWith('LOG: ')) {
        displayMessage = displayMessage.substring(5);
    } else if (displayMessage.startsWith('LOG:')) {
        displayMessage = displayMessage.substring(4);
    }

    // Handle Thinking UI for Inline Chat
    if (data.progress !== undefined) {
        const chatStream = document.getElementById('chat-stream');

        // Ensure we are in session view
        const sessionView = document.getElementById('session-view');
        if (sessionView && sessionView.classList.contains('hidden')) {
            console.log('[handleThinkingUpdate] Force switching to session view');
            sessionView.classList.remove('hidden');
            document.getElementById('path-view').classList.add('hidden');
            // Toggle headers (if any left, or just ensure correct one is shown)
            const sessionHeader = document.getElementById('session-header');
            if (sessionHeader) sessionHeader.classList.remove('hidden');
            const headerLeft = document.querySelector('.header-left');
            if (headerLeft) headerLeft.classList.add('hidden');

            currentMode = 1;
            toggleRails(1);
        }

        if (chatStream) {
            let activeBubble = chatStream.lastElementChild;
            const isThinkingBubble = activeBubble && activeBubble.classList.contains('thinking-message');
            const isCompleted = activeBubble && activeBubble.classList.contains('completed');

            // Create new bubble if needed
            if ((!isThinkingBubble || isCompleted) && data.progress < 100) {
                const template = document.getElementById('thinking-bubble-template');
                if (template) {
                    const node = template.content.cloneNode(true);
                    chatStream.appendChild(node);
                    activeBubble = chatStream.lastElementChild;

                    // Add Click Listener for Toggle
                    const header = activeBubble.querySelector('.thinking-header');
                    const logs = activeBubble.querySelector('.thinking-logs-inline');
                    if (header && logs) {
                        header.addEventListener('click', () => {
                            logs.classList.toggle('hidden');
                            header.classList.toggle('expanded');
                        });
                    }
                    chatStream.scrollTop = chatStream.scrollHeight;
                }
            }

            // Update Active Bubble
            if (activeBubble && activeBubble.classList.contains('thinking-message') && !activeBubble.classList.contains('completed')) {
                // Update Label
                const textSpan = activeBubble.querySelector('.thinking-text');
                if (textSpan) textSpan.textContent = displayMessage || "Thinking...";

                // Append Log
                const logsContent = activeBubble.querySelector('.logs-content');
                if (logsContent) {
                    const logItem = document.createElement('div');
                    logItem.className = 'log-item';
                    const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: "numeric", minute: "numeric", second: "numeric" });
                    logItem.innerText = `[${time}] ${displayMessage || 'Update'}`;
                    logsContent.appendChild(logItem);
                    // activeBubble.querySelector('.thinking-logs-inline').scrollTop = logsContent.scrollHeight;
                }

                // Completion
                if (data.progress >= 100) {
                    activeBubble.classList.add('completed');
                    if (textSpan) textSpan.textContent = "Thought Process";
                    const spinner = activeBubble.querySelector('.spinner-tiny');
                    if (spinner) spinner.style.display = 'none'; // Hide spinner

                    // Optional: Auto-collapse if open? It starts hidden.
                }
            }
        }
    }

    // Check for educational status prefixes (STRUCT, CHECK, SYLLABUS, ERROR)
    if (message && (
        message.startsWith('STRUCT:') ||
        message.startsWith('CHECK:') ||
        message.startsWith('SYLLABUS:') ||
        message.startsWith('ERROR:') ||
        message.startsWith('LOG:')
    )) {
        addProgressLog(displayMessage);
    }

    // Check for course creation messages
    if (message && message.startsWith('Creating course on ')) {
        const topic = message.replace('Creating course on ', '');
        showCreationProgressModal(topic);
        return;
    }

    // Parse JSON log entries for step extraction
    let jsonLog = null;
    if (logEntry) {
        try {
            jsonLog = JSON.parse(logEntry);
        } catch (e) {
            // Not JSON, treat as plain text
            jsonLog = null;
        }
    }

    // Extract step from JSON log if available
    if (jsonLog && jsonLog.step) {
        const stepMap = {
            'prepare': { percent: 0, detail: 'Stopping services and preparing database...' },
            'scrape': { percent: 20, detail: 'Downloading educational content...' },
            'chunk': { percent: 40, detail: 'Chunking content into segments...' },
            'graph': { percent: 60, detail: 'Organizing concepts and relationships...' },
            'finalize': { percent: 80, detail: 'Preparing for learning...' },
            'restart': { percent: 100, detail: 'Restarting services and verifying...' }
        };

        if (stepMap[jsonLog.step]) {
            const stepInfo = stepMap[jsonLog.step];
            updateProgressStep(jsonLog.step, stepInfo.percent, stepInfo.detail);
        }
    }

    // Update progress bar during creation with new 6-step flow
    if (message === 'Preparing database...') {
        updateProgressStep('prepare', 0, 'Stopping services and preparing database...');
        addProgressLog('Preparing database...');
    } else if (message === 'Scraping ZIM files...') {
        updateProgressStep('scrape', 20, 'Downloading educational content...');
        addProgressLog('Scraping ZIM files...');
    } else if (message === 'Vectorizing content...') {
        updateProgressStep('chunk', 40, 'Chunking content into segments...');
        addProgressLog('Vectorizing content...');
    } else if (message === 'Building graph...') {
        updateProgressStep('graph', 60, 'Organizing concepts and relationships...');
        addProgressLog('Building graph...');
    } else if (message === 'Finalizing course...') {
        updateProgressStep('finalize', 80, 'Preparing for learning...');
        addProgressLog('Finalizing course...');
    } else if (displayMessage === 'Restarting Systems...' || displayMessage === 'Restarting services...') {
        updateProgressStep('restart', 100, 'Restarting services and verifying...');
        addProgressLog('Restarting services...');
    } else if (message === 'Course built successfully!' || message === 'Course ready to start!' || message === 'Course ready!') {
        updateProgressStep('complete', 100, 'Course is ready!');
        addProgressLog('✅ Course built successfully!');
        setTimeout(() => {
            hideCourseCreationModal();
            isCreatingCourse = false;
            enableCourseCreationButton();
        }, 2000); // Hide after 2 seconds
    } else if (message && (message.includes('Ingestion failed') || message.includes('Ingestion error') || message.includes('Service restart failed'))) {
        creationStatus.textContent = '❌ ' + message;
        progressFill.style.width = '0%';
        addProgressLog('❌ ' + message);
        setTimeout(() => {
            hideCourseCreationModal();
            isCreatingCourse = false;
            enableCourseCreationButton();
        }, 3000);
    }

    // Always add logEntry to progress log if present
    if (logEntry) {
        console.log('[handleThinkingUpdate] Adding log entry:', logEntry);

        // Color-code based on log level
        let logClass = 'log-entry';
        if (jsonLog) {
            if (jsonLog.level === 'ERROR') logClass = 'log-entry log-error';
            else if (jsonLog.level === 'WARN') logClass = 'log-entry log-warn';
            else if (jsonLog.level === 'INFO') logClass = 'log-entry log-info';
            else if (jsonLog.level === 'DEBUG') logClass = 'log-entry log-debug';
        }

        // Add to course creation logs if that's active
        addProgressLog(logEntry, logClass);

        // Add to global logs (if we keep them, otherwise just inline)
        // thinkingLogsCode.textContent += logEntry + '\n'; // LEGACY
    }

    // Scroll chat stream
    const chatStream = document.getElementById('chat-stream');
    if (chatStream) chatStream.scrollTop = chatStream.scrollHeight;
}




function sendEvent(eventType, payload) {
    console.log("[sendEvent] Sending event:", { type: eventType, payload });
    const requestBody = { type: eventType, payload: payload };
    console.log('[sendEvent] Request body:', JSON.stringify(requestBody));

    fetch('/api/event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
    })
        .then(response => {
            console.log('[sendEvent] Response status:', response.status, response.statusText);
            if (!response.ok) {
                throw new Error(`Server returned ${response.status}: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            console.log('[sendEvent] Server response to event:', data);
        })
        .catch(error => {
            console.error('[sendEvent] Error sending event:', error);
            // Visual feedback for the user
            const chatStream = document.getElementById('chat-stream');
            if (chatStream) {
                const errorDiv = document.createElement('div');
                errorDiv.className = 'message ai error-message';
                errorDiv.style.border = '1px solid red';
                errorDiv.style.color = 'red';
                errorDiv.textContent = `Error sending message: ${error.message}. Is the server online?`;
                chatStream.appendChild(errorDiv);
                chatStream.scrollTop = chatStream.scrollHeight;
            } else {
                alert(`Error communicating with server: ${error.message}`);
            }
        });
}

function sendTextMessage() {
    const textInput = document.getElementById('text-input');
    const sendBtn = document.getElementById('send-btn');
    const text = textInput.value.trim();

    // Input Guard: prevent empty sends or rapid double-submissions
    if (!text || (sendBtn && sendBtn.disabled)) {
        console.log('[SEND_MESSAGE] Guard block: Text is empty or button disabled.');
        textInput.focus();
        return;
    }

    // Disable briefly to prevent rapid fire
    if (sendBtn) sendBtn.disabled = true;

    // Clear previous thinking logs and hide container
    if (thinkingLogsCode && thinkingLogsContainer) {
        thinkingLogsCode.textContent = '';
        thinkingLogsContainer.classList.add('hidden');
    }
    if (toggleLogsBtn) toggleLogsBtn.classList.remove('open');

    console.log('[SEND_MESSAGE] Calling sendEvent with TEXT_INPUT');

    // Optimistic render: show user message immediately (don't wait for 2s state poll)
    const chatStream = document.getElementById('chat-stream');
    if (chatStream) {
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message user new-message';
        msgDiv.innerHTML = `
            <div class="msg-avatar"><img src="https://ui-avatars.com/api/?name=You&background=10b981&color=fff" alt="You"></div>
            <div class="msg-content">${text.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
        `;
        chatStream.appendChild(msgDiv);
        chatStream.scrollTop = chatStream.scrollHeight;
        displayedMessagesCount++;
    }

    sendEvent('TEXT_INPUT', { text: text });
    textInput.value = '';

    // Re-enable and focus
    if (sendBtn) {
        setTimeout(() => { sendBtn.disabled = false; }, 500);
    }
    textInput.focus();
}


// --- Socket Listener Setup ---
function setupSocketListeners() {
    if (!socket) return;

    socket.on('disconnect', () => {
        console.warn('Socket.IO disconnected.');
    });

    socket.on('connect', () => {
        console.log("WebSocket connected to Web-UI server");
        // Join status_room to receive service health updates
        socket.emit('join_status_room');
    });

    socket.on('health_update', (data) => {
        // Find the "input" service status
        const inputStatus = data.input;
        const statusEmoji = document.getElementById('input-status-indicator');
        if (statusEmoji && inputStatus) {
            if (inputStatus.status === 'online') {
                statusEmoji.textContent = '🟢';
                statusEmoji.title = 'Input Service: Online (Lat: ' + inputStatus.latency + 'ms)';
            } else if (inputStatus.status === 'degraded') {
                statusEmoji.textContent = '🟡';
                statusEmoji.title = 'Input Service: Degraded (Model loading?)';
            } else {
                statusEmoji.textContent = '🔴';
                statusEmoji.title = 'Input Service: Offline';
            }
        }
    });

    socket.on('state_update', (data) => {
        console.log("State update received via WebSocket:", data);
        updateUI(data);
    });

    socket.on('status_update', (data) => {
        console.log("Status update received via WebSocket:", data);
        handleThinkingUpdate(data);
    });

    // --- Streaming LLM token handler ---
    // Tokens arrive one-by-one from the LLM via core-logic -> web-ui -> here.
    // We accumulate them in a temporary "streaming" bubble. When 'done' is true
    // (or the next full state_update arrives with the final transcript), we
    // finalize the bubble.
    socket.on('stream_token', (data) => {
        handleStreamToken(data);
    });

}

// Chat mode state
let chatModeEnabled = false;

// Voice selector state
let voiceSelector = null;

// Event listeners
document.addEventListener('DOMContentLoaded', function () {
    // Initialize UI elements
    thinkingBubble = document.getElementById('thinking-bubble');
    thinkingStatusText = document.getElementById('thinking-status-text');
    toggleLogsBtn = document.getElementById('toggle-logs');
    thinkingLogsContainer = document.getElementById('thinking-logs');
    if (thinkingLogsContainer) thinkingLogsCode = thinkingLogsContainer.querySelector('code');
    voiceSelector = document.getElementById('voice-selector');

    // Course creation modal elements
    courseModal = document.getElementById('course-creation-modal');
    courseForm = document.getElementById('course-creation-form');
    courseTopicInput = document.getElementById('course-topic');
    creationProgress = document.getElementById('creation-progress');
    creationStatus = document.getElementById('creation-status');
    progressFill = document.getElementById('progress-fill');
    closeModalBtn = document.getElementById('close-modal');

    // Restore chat mode preference from localStorage
    const savedChatMode = localStorage.getItem('chatModeEnabled');
    if (savedChatMode === 'true') {
        chatModeEnabled = true;
        applyChatMode();
    }

    // Fetch and populate voices
    fetchAndPopulateVoices();

    // Establish WebSocket connection (single io() call for the entire file)
    socket = io();
    window.socket = socket;

    // Setup socket listeners immediately after connection
    setupSocketListeners();

    // --- Static Event Handlers ---

    // Log toggle handler
    if (toggleLogsBtn) {
        toggleLogsBtn.addEventListener('click', () => {
            const isHidden = thinkingLogsContainer.classList.toggle('hidden');
            toggleLogsBtn.classList.toggle('open', !isHidden);
        });
    }

    // Chat mode toggle
    const chatModeToggle = document.getElementById('chat-mode-toggle');
    if (chatModeToggle) {
        chatModeToggle.addEventListener('click', () => {
            toggleChatMode();
        });
    }

    // Voice selector
    if (voiceSelector) {
        voiceSelector.addEventListener('change', handleVoiceChange);
    }

    // Pause/Resume
    const pauseResumeBtn = document.getElementById('pause-resume');
    if (pauseResumeBtn) {
        pauseResumeBtn.addEventListener('click', function () {
            const isPaused = this.textContent === 'Pause';
            sendEvent(isPaused ? 'PAUSE' : 'RESUME', {});
            this.textContent = isPaused ? 'Resume' : 'Pause';
        });
    }

    // Text input
    const textInput = document.getElementById('text-input');
    if (textInput) {
        textInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendTextMessage();
            }
        });
    }
    const sendBtn = document.getElementById('send-btn'); // Corrected ID from 'send-text' to 'send-btn' based on learn.html
    if (sendBtn) {
        sendBtn.addEventListener('click', sendTextMessage);
    }

    // Course creation modal
    if (courseForm) {
        courseForm.addEventListener('submit', (e) => {
            e.preventDefault();
            submitCourseCreation();
        });
    }
    if (closeModalBtn) {
        closeModalBtn.addEventListener('click', hideCourseCreationModal);
    }

    // Sudo password form
    const sudoPasswordForm = document.getElementById('sudo-password-form');
    if (sudoPasswordForm) {
        sudoPasswordForm.addEventListener('submit', submitSudoPassword);
    }

    const chatStream = document.getElementById('chat-stream');
    if (chatStream) {
        // Event delegation for transcript interactions
        chatStream.addEventListener('blur', (e) => {
            if (e.target.contentEditable === 'true') {
                const messageDiv = e.target.closest('.message');
                const index = parseInt(messageDiv.dataset.index);
                const newText = e.target.textContent.trim();
                if (newText) {
                    sendEvent('EDIT_MESSAGE', { index: index, text: newText });
                }
            }
        }, true);

        chatStream.addEventListener('click', (e) => {
            if (e.target.classList.contains('tts-play-btn')) {
                // Handled by addTTSButton onclick
            }
        });
    }

    console.log('Socket.IO connected!');

    // Check for create_course query param
    const urlParams = new URLSearchParams(window.location.search);
    const createTopic = urlParams.get('create_course');
    const courseDepth = urlParams.get('depth') || '3'; // Default to 3 if not specified
    if (createTopic && courseModal) { // Guard courseModal
        showCourseCreationModal(decodeURIComponent(createTopic), courseDepth);
        // Clear the URL
        window.history.replaceState(null, null, window.location.pathname);
    }

    // Check for resume query param
    const resume = urlParams.get('resume');
    if (resume === 'true') {
        // Fetch state to get UID, then resume
        fetch('/api/fsm_state').then(res => res.json()).then(state => {
            if (state.active_course_uid) {
                console.log("Auto-resuming course:", state.active_course_uid);
                sendEvent('RESUME_COURSE', { uid: state.active_course_uid });
                // Clear the URL
                window.history.replaceState(null, null, window.location.pathname);
            }
        }).catch(err => console.error("Failed to auto-resume:", err));
    }

    // Initial fetch for context rail data, in case it's not in the first state push
    // updateContextRail internally checks for element existence, so it is safe-ish,
    // but the rail container might not exist.
    if (document.getElementById('context-rail')) {
        updateContextRail(null, null);
    }
});

// Voice selection functions
function fetchAndPopulateVoices() {
    if (!voiceSelector) return;

    fetch('/api/voices')
        .then(response => response.json())
        .then(data => {
            const voices = data.voices || [];
            if (voices.length === 0) {
                console.warn('No voices returned from API, using defaults');
                return;
            }

            // Populate dropdown with fetched voices
            voiceSelector.innerHTML = '';
            voices.forEach(voice => {
                const option = document.createElement('option');
                option.value = voice;
                option.textContent = voice;
                voiceSelector.appendChild(option);
            });

            // Restore previous selection from localStorage
            const savedVoice = localStorage.getItem('helga_voice_id');
            if (savedVoice && voices.includes(savedVoice)) {
                voiceSelector.value = savedVoice;
            } else {
                voiceSelector.value = 'Vivian'; // Default
            }

            console.log('[fetchAndPopulateVoices] Voices populated:', voices);
        })
        .catch(error => {
            console.error('[fetchAndPopulateVoices] Failed to fetch voices:', error);
            // Keep default voices in dropdown
        });
}

function handleVoiceChange() {
    if (!voiceSelector) return;

    const selectedVoice = voiceSelector.value;
    localStorage.setItem('helga_voice_id', selectedVoice);
    console.log('[handleVoiceChange] Voice changed to:', selectedVoice);

    // Emit Socket.IO event to update FSM
    if (socket) {
        socket.emit('update_settings', { voice_id: selectedVoice });
        console.log('[handleVoiceChange] Emitted update_settings event');
    }
}

// Chat mode functions
function toggleChatMode() {
    chatModeEnabled = !chatModeEnabled;
    localStorage.setItem('chatModeEnabled', chatModeEnabled);

    if (chatModeEnabled) {
        applyChatMode();
    } else {
        applyFocusMode();
    }
}

function applyChatMode() {
    const sessionContainer = document.getElementById('session-container');
    const textInput = document.getElementById('text-input');
    const contextRail = document.getElementById('context-rail');
    const flashcardRail = document.getElementById('flashcard-rail');
    const palaceRail = document.getElementById('palace-rail');
    const chatModeToggle = document.getElementById('chat-mode-toggle');

    if (sessionContainer) {
        sessionContainer.classList.add('chat-mode');
    }

    // Hide context rails
    if (contextRail) contextRail.classList.add('hidden');
    if (flashcardRail) flashcardRail.classList.add('hidden');
    if (palaceRail) palaceRail.classList.add('hidden');

    // Focus text input
    if (textInput) {
        textInput.focus();
    }

    // Update toggle button state
    if (chatModeToggle) {
        chatModeToggle.textContent = 'Chat Mode: ON';
        chatModeToggle.classList.add('active');
    }

}

function applyFocusMode() {
    const sessionContainer = document.getElementById('session-container');
    const chatModeToggle = document.getElementById('chat-mode-toggle');

    if (sessionContainer) {
        sessionContainer.classList.remove('chat-mode');
    }

    // Show context rails based on current mode
    toggleRails(currentMode);

    // Update toggle button state
    if (chatModeToggle) {
        chatModeToggle.textContent = 'Chat Mode: OFF';
        chatModeToggle.classList.remove('active');
    }
}

// --- Typing Indicator ---
function showTypingIndicator() {
    const chatStream = document.getElementById('chat-stream');
    if (!chatStream) return;
    let existing = document.getElementById('typing-indicator');
    if (existing) return;
    const div = document.createElement('div');
    div.id = 'typing-indicator';
    div.className = 'message ai-message typing';
    div.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
    chatStream.appendChild(div);
    chatStream.scrollTop = chatStream.scrollHeight;
}

function hideTypingIndicator() {
    const el = document.getElementById('typing-indicator');
    if (el) el.remove();
}

// --- TTS Play Button Helper ---
function addTTSButton(messageEl, text) {
    const btn = document.createElement('button');
    btn.className = 'tts-play-btn';
    btn.innerHTML = '&#9654;';
    btn.title = 'Play audio';
    btn.onclick = async function() {
        btn.disabled = true;
        btn.textContent = '...';
        try {
            const resp = await fetch('/api/tts', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text: text})});
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.onended = () => { btn.innerHTML = '&#9654;'; btn.disabled = false; };
            audio.play();
        } catch(e) { btn.innerHTML = '&#9654;'; btn.disabled = false; }
    };
    messageEl.appendChild(btn);
}

// --- Confetti Animation Trigger ---
// Call showConfetti() to display a CSS-only confetti burst.
// The container auto-removes after 2.5s.
// Respects gamification setting — no confetti when gamification is disabled.
function showConfetti() {
    // Skip celebration animations when gamification is toggled off
    if (localStorage.getItem('helga-gamification') === 'false') return;

    var existing = document.querySelector('.confetti-container');
    if (existing) existing.remove();

    var container = document.createElement('div');
    container.className = 'confetti-container';

    for (var i = 0; i < 20; i++) {
        var piece = document.createElement('div');
        piece.className = 'confetti-piece';
        container.appendChild(piece);
    }

    document.body.appendChild(container);

    setTimeout(function() {
        if (container.parentNode) {
            container.parentNode.removeChild(container);
        }
    }, 2500);
}
window.showConfetti = showConfetti;

// --- Achievement Unlock Banner ---
// Displays a full-width banner sliding down from the top when an achievement is unlocked.
// Auto-dismisses after 4 seconds.
function showAchievementBanner(name, description, xpReward) {
    // Skip when gamification is disabled
    if (localStorage.getItem('helga-gamification') === 'false') return;

    // Remove any existing banner before showing a new one
    var existing = document.querySelector('.achievement-banner');
    if (existing) existing.remove();

    var banner = document.createElement('div');
    banner.className = 'achievement-banner';
    banner.innerHTML =
        '<span class="achievement-icon">&#127942;</span>' +
        '<div class="achievement-info">' +
            '<span class="achievement-name">' + (name || 'Achievement Unlocked') + '</span>' +
            '<span class="achievement-desc">' + (description || '') + '</span>' +
        '</div>' +
        (xpReward ? '<span class="achievement-xp">+' + xpReward + ' XP</span>' : '');

    document.body.appendChild(banner);

    // Trigger the slide-down animation on the next frame
    requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            banner.classList.add('show');
        });
    });

    // Auto-dismiss after 4 seconds
    setTimeout(function () {
        banner.classList.remove('show');
        // Remove from DOM after the slide-up transition completes
        setTimeout(function () {
            if (banner.parentNode) {
                banner.parentNode.removeChild(banner);
            }
        }, 600);
    }, 4000);
}
window.showAchievementBanner = showAchievementBanner;

function animateBadgeUpgrade(element) {
    element.classList.add('badge-upgrading');
    element.addEventListener('animationend', function() {
        element.classList.remove('badge-upgrading');
    }, { once: true });
}
window.animateBadgeUpgrade = animateBadgeUpgrade;

// --- Streaming LLM Response Rendering ---
// These functions manage a temporary "streaming" chat bubble that accumulates
// tokens as they arrive from the LLM.  When the stream finishes (done=true),
// the bubble is marked complete. The next state_update with the full transcript
// replaces the streaming bubble with the canonical version.

let _streamingBubble = null;       // DOM element for the in-progress streaming bubble
let _streamingText = '';           // Accumulated raw text so far
let _streamRafPending = false;     // requestAnimationFrame guard to batch DOM writes

function handleStreamToken(data) {
    const token = data.token || '';
    const done = data.done || false;

    if (done) {
        // Stream finished — mark bubble as complete so updateChatStream()
        // can replace it with the final canonical message.
        if (_streamingBubble) {
            _streamingBubble.classList.add('stream-done');
            _streamingBubble.classList.remove('streaming');
        }
        _streamingBubble = null;
        _streamingText = '';
        _streamRafPending = false;
        return;
    }

    if (!token) return;

    _streamingText += token;

    // Create the streaming bubble if it does not exist yet
    if (!_streamingBubble) {
        const chatStream = document.getElementById('chat-stream');
        if (!chatStream) return;

        _streamingBubble = document.createElement('div');
        _streamingBubble.className = 'message helga streaming new-message';
        _streamingBubble.innerHTML = `
            <div class="msg-avatar"><img src="https://ui-avatars.com/api/?name=H&background=3b82f6&color=fff" alt="Helga"></div>
            <div class="msg-content streaming-content"></div>
        `;
        chatStream.appendChild(_streamingBubble);
        chatStream.scrollTop = chatStream.scrollHeight;
    }

    // Batch DOM updates with requestAnimationFrame to avoid layout thrashing
    if (!_streamRafPending) {
        _streamRafPending = true;
        requestAnimationFrame(function () {
            _streamRafPending = false;
            if (_streamingBubble) {
                var contentEl = _streamingBubble.querySelector('.streaming-content');
                if (contentEl) {
                    contentEl.innerHTML = renderMarkdown(_streamingText);
                }
                var chatStream = document.getElementById('chat-stream');
                if (chatStream) {
                    chatStream.scrollTop = chatStream.scrollHeight;
                }
            }
        });
    }
}

// Patch updateChatStream to clean up finished streaming bubbles when the
// canonical transcript arrives from the state poller.
var _origUpdateChatStream = updateChatStream;
updateChatStream = function (transcript) {
    if (!transcript) {
        _origUpdateChatStream(transcript);
        return;
    }

    // If a streaming bubble exists and is marked done, remove it before
    // the canonical messages render (they include the final text).
    var chatStream = document.getElementById('chat-stream');
    if (chatStream) {
        var doneBubbles = chatStream.querySelectorAll('.message.stream-done');
        doneBubbles.forEach(function (b) { b.remove(); });
    }

    // If a streaming bubble is still actively receiving tokens, the new
    // transcript message count may include the message being streamed.
    // Remove the live bubble so it doesn't duplicate.
    if (_streamingBubble && chatStream) {
        // The transcript's last entry should match what we are streaming.
        // Remove the streaming bubble to let the canonical version take over.
        if (transcript.length > displayedMessagesCount) {
            _streamingBubble.remove();
            _streamingBubble = null;
            _streamingText = '';
            _streamRafPending = false;
        }
    }

    _origUpdateChatStream(transcript);
};