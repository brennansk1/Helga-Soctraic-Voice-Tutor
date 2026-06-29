/**
 * search.js — Global header search for Helga.
 *
 * Wires to GET /api/search?q=<query> (web-ui proxy → RAG /search).
 * Returns {"results":[{"uid","title","text","type"}]}.
 * Course results navigate to /learn?course_uid=<uid>.
 * Concept results navigate to /learn?course_uid=<uid>.
 *
 * Keyboard: '/' focuses the input (unless already typing in an input).
 * Escape closes the dropdown.
 * Click-outside closes the dropdown.
 * Results are HTML-escaped (XSS safe).
 */
(function () {
    'use strict';

    // ── Helpers ──────────────────────────────────────────────────────────────

    function escHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#x27;');
    }

    function snippet(text, maxLen) {
        if (!text) return '';
        var t = text.replace(/\s+/g, ' ').trim();
        return t.length > maxLen ? t.slice(0, maxLen) + '…' : t;
    }

    // ── State ─────────────────────────────────────────────────────────────────

    var _debounceTimer = null;
    var _lastQuery = '';
    var _open = false;

    // ── DOM refs (populated in init) ──────────────────────────────────────────

    var searchWrapper = null;   // .helga-search-wrapper (relative container)
    var searchInput   = null;   // the <input>
    var searchBtn     = null;   // the toggle button (icon)
    var searchPanel   = null;   // the results dropdown

    // ── Core logic ────────────────────────────────────────────────────────────

    function openInput() {
        if (!searchWrapper) return;
        searchWrapper.classList.add('helga-search-expanded');
        searchInput.removeAttribute('aria-hidden');
        searchInput.focus();
    }

    function closeAll() {
        if (!searchWrapper) return;
        searchWrapper.classList.remove('helga-search-expanded');
        searchInput.setAttribute('aria-hidden', 'true');
        closeDropdown();
        searchInput.value = '';
        _lastQuery = '';
    }

    function openDropdown() {
        if (!searchPanel) return;
        searchPanel.hidden = false;
        _open = true;
        searchWrapper.setAttribute('aria-expanded', 'true');
    }

    function closeDropdown() {
        if (!searchPanel) return;
        searchPanel.hidden = true;
        _open = false;
        if (searchWrapper) searchWrapper.setAttribute('aria-expanded', 'false');
    }

    function setStatus(msg) {
        if (!searchPanel) return;
        searchPanel.innerHTML = '<p class="hsr-status">' + escHtml(msg) + '</p>';
    }

    function buildResultItem(r) {
        var li = document.createElement('li');
        li.className = 'hsr-item';
        li.setAttribute('role', 'option');

        // Navigation target: both Course and Concept go to /learn
        var href = '/learn?course_uid=' + encodeURIComponent(r.uid || '');

        var typeLabel = escHtml(r.type || 'Result');
        var title     = escHtml(r.title || '(untitled)');
        var text      = escHtml(snippet(r.text, 100));

        li.innerHTML =
            '<a class="hsr-link" href="' + href + '" tabindex="0">' +
                '<span class="hsr-type">' + typeLabel + '</span>' +
                '<span class="hsr-title">' + title + '</span>' +
                (text ? '<span class="hsr-snippet">' + text + '</span>' : '') +
            '</a>';

        // Close dropdown when a result is clicked
        li.querySelector('a').addEventListener('click', function () {
            closeAll();
        });

        return li;
    }

    function renderResults(results) {
        if (!searchPanel) return;

        if (!results || results.length === 0) {
            setStatus('No results found.');
            openDropdown();
            return;
        }

        var ul = document.createElement('ul');
        ul.className = 'hsr-list';
        ul.setAttribute('role', 'listbox');
        ul.setAttribute('aria-label', 'Search results');

        var max = Math.min(results.length, 8);
        for (var i = 0; i < max; i++) {
            ul.appendChild(buildResultItem(results[i]));
        }

        searchPanel.innerHTML = '';
        searchPanel.appendChild(ul);
        openDropdown();
    }

    function doSearch(q) {
        q = q.trim();
        if (q === _lastQuery) return;
        _lastQuery = q;

        if (!q) {
            closeDropdown();
            return;
        }

        setStatus('Searching…');
        openDropdown();

        fetch('/api/search?q=' + encodeURIComponent(q), { cache: 'no-store' })
            .then(function (res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(function (data) {
                renderResults(data.results || []);
            })
            .catch(function (err) {
                setStatus('Search unavailable.');
                console.warn('[helga-search] fetch error:', err);
            });
    }

    function onInput() {
        var q = searchInput.value;
        clearTimeout(_debounceTimer);
        _debounceTimer = setTimeout(function () {
            doSearch(q);
        }, 250);
    }

    // ── Keyboard shortcuts ────────────────────────────────────────────────────

    function onDocKeydown(e) {
        var tag = document.activeElement && document.activeElement.tagName;
        var isEditable =
            tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' ||
            (document.activeElement && document.activeElement.isContentEditable);

        // '/' focuses search (unless already in an editable field)
        if (e.key === '/' && !isEditable) {
            e.preventDefault();
            openInput();
            return;
        }

        // Escape closes dropdown / input
        if (e.key === 'Escape') {
            if (_open) {
                closeDropdown();
            } else {
                closeAll();
            }
        }
    }

    // ── Click-outside ─────────────────────────────────────────────────────────

    function onDocClick(e) {
        if (!searchWrapper) return;
        if (!searchWrapper.contains(e.target)) {
            closeAll();
        }
    }

    // ── Build DOM ─────────────────────────────────────────────────────────────

    function buildSearchDOM(header) {
        // Wrapper — sits between gamification-bar and the theme-toggle in flex row
        searchWrapper = document.createElement('div');
        searchWrapper.className = 'helga-search-wrapper';
        searchWrapper.setAttribute('role', 'search');
        searchWrapper.setAttribute('aria-label', 'Site search');
        searchWrapper.setAttribute('aria-expanded', 'false');

        // Search icon button (visible when input is collapsed)
        searchBtn = document.createElement('button');
        searchBtn.className = 'helga-search-btn';
        searchBtn.setAttribute('aria-label', 'Open search');
        searchBtn.setAttribute('title', 'Search (/)');
        searchBtn.setAttribute('type', 'button');
        searchBtn.innerHTML =
            '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" ' +
            'viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
            'aria-hidden="true"><circle cx="11" cy="11" r="8"/>' +
            '<line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';

        // Text input
        searchInput = document.createElement('input');
        searchInput.type = 'search';
        searchInput.className = 'helga-search-input';
        searchInput.placeholder = 'Search courses & concepts…';
        searchInput.setAttribute('aria-label', 'Search courses and concepts');
        searchInput.setAttribute('aria-autocomplete', 'list');
        searchInput.setAttribute('aria-controls', 'helga-search-panel');
        searchInput.setAttribute('aria-hidden', 'true');
        searchInput.setAttribute('autocomplete', 'off');
        searchInput.setAttribute('autocorrect', 'off');
        searchInput.setAttribute('spellcheck', 'false');

        // Results panel (dropdown)
        searchPanel = document.createElement('div');
        searchPanel.className = 'helga-search-panel';
        searchPanel.id = 'helga-search-panel';
        searchPanel.setAttribute('role', 'region');
        searchPanel.setAttribute('aria-label', 'Search results');
        searchPanel.hidden = true;

        searchWrapper.appendChild(searchBtn);
        searchWrapper.appendChild(searchInput);
        searchWrapper.appendChild(searchPanel);

        // Insert before the theme-toggle button in the header
        var themeToggle = header.querySelector('#header-theme-toggle');
        if (themeToggle) {
            header.insertBefore(searchWrapper, themeToggle);
        } else {
            // Fallback: append before hamburger or at end of header
            var hamburger = header.querySelector('#hamburger-btn');
            if (hamburger) {
                header.insertBefore(searchWrapper, hamburger);
            } else {
                header.appendChild(searchWrapper);
            }
        }

        // Events
        searchBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            if (searchWrapper.classList.contains('helga-search-expanded')) {
                closeAll();
            } else {
                openInput();
            }
        });

        searchInput.addEventListener('input', onInput);

        searchInput.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                e.stopPropagation();
                if (_open) {
                    closeDropdown();
                } else {
                    closeAll();
                }
            }
            // Enter: navigate to first result
            if (e.key === 'Enter' && _open) {
                var first = searchPanel.querySelector('.hsr-link');
                if (first) {
                    closeAll();
                    window.location.href = first.getAttribute('href');
                }
            }
        });

        document.addEventListener('keydown', onDocKeydown);
        document.addEventListener('click', onDocClick);

        injectStyles();
    }

    // ── CSS ───────────────────────────────────────────────────────────────────

    function injectStyles() {
        if (document.getElementById('helga-search-styles')) return;
        var style = document.createElement('style');
        style.id = 'helga-search-styles';
        style.textContent = [
            /* Wrapper sits inline in the flex header */
            '.helga-search-wrapper {',
            '  position: relative;',
            '  display: flex;',
            '  align-items: center;',
            '  gap: 0;',
            '  flex-shrink: 0;',
            '  margin-right: 0.35rem;',
            '}',

            /* Icon button — matches .header-theme-toggle */
            '.helga-search-btn {',
            '  display: flex;',
            '  align-items: center;',
            '  justify-content: center;',
            '  width: 36px;',
            '  height: 36px;',
            '  padding: 0;',
            '  background: none;',
            '  border: 1px solid var(--border-color, rgba(128,128,128,0.3));',
            '  border-radius: 8px;',
            '  color: var(--text-secondary, #6b7c6e);',
            '  cursor: pointer;',
            '  flex-shrink: 0;',
            '  transition: background 0.15s, color 0.15s, border-color 0.15s;',
            '}',
            '.helga-search-btn:hover {',
            '  background: var(--bg-secondary, rgba(128,128,128,0.1));',
            '  color: var(--text-primary, #2d3a2e);',
            '  border-color: var(--accent-primary, #2e6b8a);',
            '}',

            /* Collapsed state: hide input */
            '.helga-search-input {',
            '  width: 0;',
            '  opacity: 0;',
            '  pointer-events: none;',
            '  padding: 0;',
            '  border: none;',
            '  outline: none;',
            '  transition: width 0.2s ease, opacity 0.2s ease, padding 0.2s ease;',
            '  background: var(--bg-secondary, #fff);',
            '  color: var(--text-primary, #2d3a2e);',
            '  font-family: inherit;',
            '  font-size: 0.9rem;',
            '  border-radius: 0 8px 8px 0;',
            '  height: 36px;',
            '  box-sizing: border-box;',
            '}',

            /* Expanded state */
            '.helga-search-wrapper.helga-search-expanded .helga-search-btn {',
            '  border-radius: 8px 0 0 8px;',
            '  border-right: none;',
            '  background: var(--bg-secondary, #fff);',
            '  color: var(--accent-primary, #2e6b8a);',
            '  border-color: var(--accent-primary, #2e6b8a);',
            '}',
            '.helga-search-wrapper.helga-search-expanded .helga-search-input {',
            '  width: 220px;',
            '  opacity: 1;',
            '  pointer-events: auto;',
            '  padding: 0 0.6rem;',
            '  border: 1px solid var(--accent-primary, #2e6b8a);',
            '  border-left: none;',
            '}',
            '.helga-search-input:focus {',
            '  outline: none;',
            '}',

            /* Results panel */
            '.helga-search-panel {',
            '  position: absolute;',
            '  top: calc(100% + 6px);',
            '  right: 0;',
            '  width: 340px;',
            '  max-height: 420px;',
            '  overflow-y: auto;',
            '  background: var(--bg-secondary, #fff);',
            '  border: 1px solid var(--border-color, #d4cdc4);',
            '  border-radius: var(--border-radius-sm, 8px);',
            '  box-shadow: var(--shadow-card, 0 4px 16px rgba(45,58,46,0.08));',
            '  z-index: 500;',
            '}',

            /* Status message */
            '.hsr-status {',
            '  padding: 0.75rem 1rem;',
            '  margin: 0;',
            '  color: var(--text-secondary, #6b7c6e);',
            '  font-size: 0.875rem;',
            '}',

            /* Result list */
            '.hsr-list {',
            '  list-style: none;',
            '  margin: 0;',
            '  padding: 0.25rem 0;',
            '}',
            '.hsr-item {',
            '  display: block;',
            '}',
            '.hsr-link {',
            '  display: flex;',
            '  flex-direction: column;',
            '  gap: 0.1rem;',
            '  padding: 0.55rem 1rem;',
            '  text-decoration: none;',
            '  color: inherit;',
            '  transition: background 0.1s;',
            '}',
            '.hsr-link:hover, .hsr-link:focus {',
            '  background: var(--bg-tertiary, #e8f4f0);',
            '  outline: none;',
            '}',
            '.hsr-type {',
            '  font-size: 0.68rem;',
            '  font-weight: 700;',
            '  text-transform: uppercase;',
            '  letter-spacing: 0.05em;',
            '  color: var(--accent-primary, #2e6b8a);',
            '}',
            '.hsr-title {',
            '  font-size: 0.9rem;',
            '  font-weight: 600;',
            '  color: var(--text-primary, #2d3a2e);',
            '  white-space: nowrap;',
            '  overflow: hidden;',
            '  text-overflow: ellipsis;',
            '}',
            '.hsr-snippet {',
            '  font-size: 0.8rem;',
            '  color: var(--text-secondary, #6b7c6e);',
            '  white-space: nowrap;',
            '  overflow: hidden;',
            '  text-overflow: ellipsis;',
            '}',

            /* Mobile */
            '@media (max-width: 768px) {',
            '  .helga-search-wrapper.helga-search-expanded .helga-search-input {',
            '    width: 140px;',
            '  }',
            '  .helga-search-panel {',
            '    width: 280px;',
            '    right: 0;',
            '  }',
            '}',
        ].join('\n');
        document.head.appendChild(style);
    }

    // ── Init ──────────────────────────────────────────────────────────────────

    function init() {
        var header = document.querySelector('.app-header');
        if (!header) return;  // not a page with the standard header
        buildSearchDOM(header);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
