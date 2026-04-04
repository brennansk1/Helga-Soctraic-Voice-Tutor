const socket = io();

// Course creation progress tracking
let courseCreationInProgress = false;
let currentCreationStep = null;

// Join the status room to receive health updates
socket.on('connect', () => {
    console.log('Connected to server via Socket.IO');
    socket.emit('join_status_room');
});

socket.on('health_update', (data) => {
    console.log('Received health update:', data);
    updateStatusGrid(data);
});

// Listen for course creation progress updates
socket.on('status_update', (data) => {
    if (data.message) {
        handleCourseCreationProgress(data.message);
    }
});

function handleCourseCreationProgress(message) {
    // Track course creation progress steps
    const progressMessages = [
        'Preparing database',
        'Scraping ZIM files',
        'Vectorizing content',
        'Building graph',
        'Finalizing course',
        'Restarting services',
        'Course ready'
    ];
    
    if (message.includes('Creating course on')) {
        courseCreationInProgress = true;
        currentCreationStep = 'starting';
    } else if (progressMessages.some(msg => message.includes(msg))) {
        currentCreationStep = message;
    } else if (message.includes('Course ready') || message.includes('Ingestion failed')) {
        courseCreationInProgress = false;
        currentCreationStep = null;
    }
    
    console.log('Course creation progress:', { inProgress: courseCreationInProgress, step: currentCreationStep });
}

function updateStatusGrid(services) {
    const grid = document.querySelector('.status-grid');
    grid.innerHTML = ''; // Clear current grid

    for (const [name, info] of Object.entries(services)) {
        const card = document.createElement('div');
        card.className = `status-card status-${info.status}`;

        let iconSvg = '';
        if (info.status === 'online') {
            iconSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
        } else if (info.status === 'offline') {
            iconSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
        } else {
            iconSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
        }

        let detailsHtml = '';
        if (info.data) {
            if (info.data.cpu_percent !== undefined) {
                detailsHtml += `<p><strong>CPU:</strong> ${info.data.cpu_percent}%</p>`;
            }
            if (info.data.memory_percent !== undefined) {
                detailsHtml += `<p><strong>RAM:</strong> ${info.data.memory_percent}%</p>`;
            }
            if (info.data.db_status) {
                detailsHtml += `<p><strong>DB:</strong> ${info.data.db_status}</p>`;
            }
            if (info.data.model_status) {
                detailsHtml += `<p><strong>Model:</strong> ${info.data.model_status}</p>`;
            }
        }
        if (info.error) {
            detailsHtml += `<p class="error-detail"><strong>Error:</strong> ${info.error}</p>`;
        }
        if (info.latency !== null && info.latency !== undefined) {
            detailsHtml += `<p><strong>Latency:</strong> ${info.latency}ms</p>`;
        }

        card.innerHTML = `
            <div class="card-header">
                <h3>${name.charAt(0).toUpperCase() + name.slice(1)}</h3>
                <div class="status-indicator-icon status-${info.status}">${iconSvg}</div>
            </div>
            <div class="card-body">
                <p><strong>Status:</strong> <span class="status-text-${info.status}">${info.status.toUpperCase()}</span></p>
                ${detailsHtml}
            </div>
        `;
        grid.appendChild(card);
    }

    // Update timestamp
    document.getElementById('last-updated').textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
}
