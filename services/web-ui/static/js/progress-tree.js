/**
 * progress-tree.js — Shared progress tree component for course creation
 * Used by both Quick Create (courses.js) and Custom Wizard (wizard.js).
 *
 * Provides: ProgressTree namespace with functions for rendering the live
 * structure tree during course building, including research/writing phase icons.
 */

var ProgressTree = (function() {
    'use strict';

    /**
     * Add a tree node (module, unit, lesson, or concept) to the progress tree.
     * @param {HTMLElement} treeEl - The tree container element
     * @param {string} type - Node type: 'module', 'unit', 'lesson', or 'concept'
     * @param {string} title - Display title
     * @param {string} [uid] - Optional UID for concept nodes (used for hydration tracking)
     */
    function addTreeNode(treeEl, type, title, uid) {
        if (!treeEl) return;

        // Remove placeholder if present
        var placeholder = treeEl.querySelector('.build-tree-placeholder');
        if (placeholder) placeholder.remove();

        var node = document.createElement('div');
        node.className = 'tree-node ' + type;
        node.textContent = title;
        if (uid) node.dataset.uid = uid;
        treeEl.appendChild(node);
        treeEl.scrollTop = treeEl.scrollHeight;
    }

    /**
     * Update the hydration status badge on a concept node.
     * Supports research, writing, and completion phases with distinct icons.
     *
     * Status flow:
     *   START       -> magnifying glass icon ("researching...")
     *   RESEARCHING -> magnifying glass icon ("researching...")
     *   STRUCTURING -> pen icon ("writing...")
     *   WRITING     -> pen icon ("writing...")
     *   DONE        -> checkmark icon ("done")
     *   ERROR       -> X icon ("error")
     *
     * @param {HTMLElement} treeEl - The tree container element
     * @param {string} uid - The concept UID
     * @param {string} status - Status string: START, RESEARCHING, STRUCTURING, WRITING, DONE, ERROR, etc.
     * @param {string} title - Concept title (for logging/display)
     * @returns {boolean} Whether this was a completion event (DONE)
     */
    function updateHydrationStatus(treeEl, uid, status, title) {
        if (!treeEl) return false;

        var conceptNode = treeEl.querySelector('[data-uid="' + uid + '"]');
        if (!conceptNode) return false;

        // Remove old badge
        var oldBadge = conceptNode.querySelector('.hydration-badge');
        if (oldBadge) oldBadge.remove();

        var badge = document.createElement('span');
        badge.className = 'hydration-badge';
        var isDone = false;

        if (status === 'START' || status === 'RESEARCHING') {
            // Research phase: magnifying glass icon
            badge.className += ' researching';
            badge.innerHTML = '<span class="badge-icon">&#128269;</span> researching\u2026';
        } else if (status === 'STRUCTURING' || status === 'WRITING') {
            // Writing phase: pen icon
            badge.className += ' writing';
            badge.innerHTML = '<span class="badge-icon">&#9998;</span> writing\u2026';
        } else if (status === 'DONE' || status.startsWith('ai') || status.startsWith('llm')) {
            // Completion: checkmark
            badge.className += ' done';
            badge.innerHTML = '<span class="badge-icon">&#10003;</span> done';
            isDone = true;
        } else if (status === 'ERROR') {
            badge.className += ' error';
            badge.innerHTML = '<span class="badge-icon">&#10007;</span> error';
        } else {
            // Unknown sub-status: show as research phase (conservative default)
            badge.className += ' researching';
            badge.innerHTML = '<span class="badge-icon">&#128269;</span> ' + status.toLowerCase() + '\u2026';
        }

        conceptNode.appendChild(badge);
        treeEl.scrollTop = treeEl.scrollHeight;

        return isDone;
    }

    /**
     * Human wording for STRUCT:WARN:<KIND>[:<detail>].
     *
     * These six are the only signal a learner ever gets that the course they
     * are about to study is below the level it claims, or still contains a
     * claim the fact-checker could not resolve. They used to be dropped on the
     * floor by every consumer of the status stream.
     */
    var WARN_TEXT = {
        CONCEPT_STUB:    function (d) { return 'Could not write "' + d + '" — left as a stub'; },
        DEPTH_MISS:      function (d) { return '"' + d + '" is thinner than the level requested'; },
        DEPTH_SUMMARY:   function (d) { return d || 'Some concepts are below the requested level'; },
        FACT_UNRESOLVED: function (d) { return '"' + d + '" has a claim that could not be verified'; },
        FACT_SUMMARY:    function (d) { return d + ' concepts still contain confirmed-false claims'; },
        LEVEL_GAP:       function (d) { return 'Course reads ' + d + ' levels from the one it claims'; }
    };

    function escHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    /**
     * Render a build warning as a tree entry. Marked with the i-warning mask
     * icon from icons.css (never a glyph — emoji are drawn by the OS, ignore
     * the theme and are banned across this UI), and coloured inline because
     * the tree stylesheet has no warning variant. A warning that is
     * indistinguishable from ordinary progress is not a warning.
     */
    function addWarningNode(treeEl, text) {
        if (!treeEl) return;
        var placeholder = treeEl.querySelector('.build-tree-placeholder');
        if (placeholder) placeholder.remove();
        var node = document.createElement('div');
        node.className = 'tree-node warn';
        node.style.color = 'var(--status-warning)';
        node.innerHTML = '<span class="i i-warning" aria-hidden="true"></span> ' +
                         escHtml(text);
        treeEl.appendChild(node);
        treeEl.scrollTop = treeEl.scrollHeight;
    }

    /**
     * Process a STRUCT: status message and update the tree accordingly.
     * Returns an object with metadata about what was processed.
     *
     * @param {string} msg - The full status message (e.g. "STRUCT:MODULE:Introduction")
     * @param {HTMLElement} treeEl - The tree container element
     * @param {HTMLElement} [statusEl] - Optional status text element to update
     * @returns {Object} { type, uid, title, isDone, isHydrating, isWarning }
     */
    function handleStructMessage(msg, treeEl, statusEl) {
        var parts = msg.split(':');
        var sType = parts[1];
        var result = { type: sType, uid: null, title: '', isDone: false,
                       isHydrating: false, isWarning: false };

        if (sType === 'MODULE') {
            var modTitle = parts.slice(2).join(':');
            result.title = modTitle;
            addTreeNode(treeEl, 'module', modTitle);
            if (statusEl) statusEl.textContent = 'Building module: ' + modTitle;
        } else if (sType === 'UNIT') {
            var unitTitle = parts.slice(2).join(':');
            result.title = unitTitle;
            addTreeNode(treeEl, 'unit', unitTitle);
        } else if (sType === 'LESSON') {
            var lessonTitle = parts.slice(2).join(':');
            result.title = lessonTitle;
            addTreeNode(treeEl, 'lesson', lessonTitle);
        } else if (sType === 'CONCEPT') {
            var cUid = parts[2];
            var cTitle = parts.slice(3).join(':');
            result.uid = cUid;
            result.title = cTitle;
            addTreeNode(treeEl, 'concept', cTitle, cUid);
        } else if (sType === 'HYDRATING') {
            result.isHydrating = true;
            var hUid = parts[2];
            var hStatus = parts[3]; // START, RESEARCHING, STRUCTURING, WRITING, etc.
            var hTitle = parts.slice(4).join(':');
            result.uid = hUid;
            result.title = hTitle;
            if (statusEl) statusEl.textContent = 'Generating content: ' + hTitle;
            updateHydrationStatus(treeEl, hUid, hStatus, hTitle);
        } else if (sType === 'HYDRATED') {
            var dUid = parts[2];
            var dSource = parts[3];
            var dTitle = parts.slice(4).join(':');
            result.uid = dUid;
            result.title = dTitle;
            result.isDone = true;
            updateHydrationStatus(treeEl, dUid, 'DONE', dTitle);
            if (statusEl) statusEl.textContent = 'Hydrated: ' + dTitle;
        } else if (sType === 'WARN') {
            var kind = parts[2] || '';
            var detail = parts.slice(3).join(':');
            result.isWarning = true;
            result.title = WARN_TEXT[kind] ? WARN_TEXT[kind](detail)
                                           : (kind + (detail ? ': ' + detail : ''));
            addWarningNode(treeEl, result.title);
        }

        return result;
    }

    /**
     * Update a progress bar based on hydration counts.
     * Skeleton phase is ~30% of total, hydration is ~70%.
     *
     * @param {HTMLElement} barEl - The progress bar fill element
     * @param {number} hydratedCount - Number of concepts hydrated so far
     * @param {number} totalConcepts - Total number of concepts
     */
    function updateProgressBar(barEl, hydratedCount, totalConcepts) {
        if (!barEl || totalConcepts <= 0) return;
        var skeletonPct = 30;
        var hydrationPct = (hydratedCount / totalConcepts) * 70;
        var totalPct = Math.min(95, skeletonPct + hydrationPct);
        barEl.style.width = totalPct + '%';
    }

    // Public API
    return {
        addTreeNode: addTreeNode,
        addWarningNode: addWarningNode,
        updateHydrationStatus: updateHydrationStatus,
        handleStructMessage: handleStructMessage,
        updateProgressBar: updateProgressBar
    };
})();
