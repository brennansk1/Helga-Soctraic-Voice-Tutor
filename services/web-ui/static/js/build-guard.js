/* Global build guard.
 *
 * A build takes tens of minutes and only one can run at a time (_build_lock in
 * course_builder rejects the second). Two problems followed:
 *
 *   1. Progress lived only in the page that started it. Navigating away and
 *      back lost everything, even though the same build was still running.
 *   2. The UI kept offering "Create course" during a build, so the learner
 *      filled in a form and THEN got an error. An interface should not offer an
 *      action the system will refuse.
 *
 * This runs on every page: it shows a persistent banner linking to the live
 * view, and disables creation controls while a build is in flight.
 */
(function () {
    'use strict';

    var POLL_MS = 5000;
    var banner = null;
    var wasActive = false;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function ensureBanner() {
        if (banner) return banner;
        banner = document.createElement('div');
        banner.className = 'build-banner';
        banner.setAttribute('role', 'status');
        document.body.appendChild(banner);
        return banner;
    }

    // The record's stage names, in learner words. A banner that says
    // "hydrate" is our vocabulary leaking; one that says nothing for twenty
    // minutes reads as a hang.
    var STAGE_LABEL = {
        preflight: 'checking the model',
        skeleton:  'planning the structure',
        audit:     'checking coverage',
        hydrate:   'writing the concepts',
        assets:    'drawing the diagrams',
        finalize:  'finishing up'
    };

    function progressText(state) {
        // Concepts written out of concepts planned is the one count that means
        // something during the long phase; modules only grow during the short one.
        if (state.stage === 'hydrate' && state.concepts) {
            return (state.hydrated || 0) + ' of ' + state.concepts + ' concepts written';
        }
        if (state.modules) return state.modules + ' modules so far';
        return '';
    }

    function show(state) {
        var el = ensureBanner();
        var mins = state.started_at
            ? Math.max(0, Math.round((Date.now() / 1000 - state.started_at) / 60))
            : 0;
        var bits = [
            STAGE_LABEL[state.stage] || '',
            progressText(state),
            mins ? mins + ' min' : ''
        ].filter(Boolean);
        el.innerHTML =
            '<span class="build-banner-pulse" aria-hidden="true"></span>' +
            '<span class="build-banner-text">Building <strong>' +
                esc(state.topic || 'your course') + '</strong>' +
                (bits.length ? ' — ' + esc(bits.join(' · ')) : '') +
            '</span>' +
            '<a class="build-banner-link" href="/build">Watch progress</a>';
        el.classList.add('is-visible');
        document.body.classList.add('is-building');
    }

    function hide() {
        if (banner) banner.classList.remove('is-visible');
        document.body.classList.remove('is-building');
    }

    // Creation controls are disabled rather than hidden: a control that
    // vanishes reads as a bug, one that is visibly unavailable reads as a rule.
    var CREATE_SELECTORS = [
        '[onclick*="openQuickCreate"]',
        '[onclick*="openImportModal"]',
        '[href="/courses/new"]',
        '#library-build',
        '.book-build',
    ];

    function setLocked(locked) {
        CREATE_SELECTORS.forEach(function (sel) {
            document.querySelectorAll(sel).forEach(function (el) {
                if (locked) {
                    el.setAttribute('aria-disabled', 'true');
                    el.classList.add('is-build-locked');
                    el.title = 'Helga is already building a course';
                    if ('disabled' in el) el.disabled = true;
                } else {
                    el.removeAttribute('aria-disabled');
                    el.classList.remove('is-build-locked');
                    if (el.title === 'Helga is already building a course') el.title = '';
                    if ('disabled' in el) el.disabled = false;
                }
            });
        });
    }

    // What to say when a build stops running. The record now distinguishes a
    // finished course from a failed one (`ok`, `error`, `stale`), and this used
    // to toast "Your course is ready." on EVERY transition out of active —
    // including a build that raised in hydration, or one the stale reaper
    // declared dead. Congratulating someone on a course that does not exist is
    // worse than saying nothing: they go looking for it.
    function announce(s) {
        if (!window.showToast) return;
        if (s && s.ok === false) {
            var why = s.stale
                ? 'the build stopped reporting'
                : (s.error || 'the build did not finish');
            window.showToast('Course build failed — ' + why, 'error');
            return;
        }
        if (s && s.ok === true) {
            var name = s.topic ? '"' + s.topic + '" is ready.' : 'Your course is ready.';
            if (s.stubs) {
                // A course that finished with stubs IS delivered, but saying so
                // is the difference between a tutor and a chatbot.
                window.showToast(name + ' ' + s.stubs + ' concept' +
                                 (s.stubs === 1 ? '' : 's') +
                                 ' could not be written.', 'warning');
            } else {
                window.showToast(name, 'success');
            }
            return;
        }
        // ok is absent: an older record, or a writer that never resolved it.
        // Report the fact, claim nothing about the result.
        window.showToast('The course build has finished.', 'info');
    }

    function poll() {
        fetch('/api/build/status')
            .then(function (r) { return r.json(); })
            .then(function (s) {
                if (s && s.active) {
                    wasActive = true;
                    show(s);
                    setLocked(true);
                } else {
                    if (wasActive) announce(s);
                    wasActive = false;
                    hide();
                    setLocked(false);
                }
            })
            .catch(function () { /* a status check must never break a page */ });
    }

    document.addEventListener('DOMContentLoaded', function () {
        poll();
        setInterval(poll, POLL_MS);
    });
})();
