/* Practice — one surface, three states (A5.1).
 *
 * Replaces the Quiz / Review / Schedule tabs. Tab state lives in the URL so a
 * given view is linkable and the browser Back button behaves, which the three
 * separate pages got for free and a naive tab widget throws away.
 */
(function () {
    'use strict';

    var TABS = ['due', 'quiz', 'upcoming'];
    var loaded = {};

    function $(id) { return document.getElementById(id); }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // --- tabs ---------------------------------------------------------------

    function show(tab, pushState) {
        if (TABS.indexOf(tab) === -1) tab = 'due';

        TABS.forEach(function (t) {
            var btn = $('tab-' + t);
            var panel = $('panel-' + t);
            var on = (t === tab);
            if (btn) {
                btn.classList.toggle('is-active', on);
                btn.setAttribute('aria-selected', on ? 'true' : 'false');
                // Roving tabindex: only the selected tab is in the tab order,
                // arrow keys move between them. Required for a real tablist.
                btn.tabIndex = on ? 0 : -1;
            }
            if (panel) panel.hidden = !on;
        });

        if (pushState) {
            var url = '/practice' + (tab === 'due' ? '' : '?tab=' + tab);
            history.pushState({ tab: tab }, '', url);
        }
        load(tab);
    }

    function load(tab) {
        if (loaded[tab]) return;
        loaded[tab] = true;
        if (tab === 'due') loadDue();
        if (tab === 'quiz') loadCourses();
        if (tab === 'upcoming') loadUpcoming();
    }

    function wireTabs() {
        var btns = TABS.map(function (t) { return $('tab-' + t); }).filter(Boolean);
        btns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                show(btn.dataset.tab, true);
            });
            btn.addEventListener('keydown', function (e) {
                var i = btns.indexOf(btn);
                var next = null;
                if (e.key === 'ArrowRight') next = btns[(i + 1) % btns.length];
                if (e.key === 'ArrowLeft') next = btns[(i - 1 + btns.length) % btns.length];
                if (e.key === 'Home') next = btns[0];
                if (e.key === 'End') next = btns[btns.length - 1];
                if (next) {
                    e.preventDefault();
                    next.focus();
                    show(next.dataset.tab, true);
                }
            });
        });
        window.addEventListener('popstate', function (e) {
            show((e.state && e.state.tab) || 'due', false);
        });
    }

    // --- due ----------------------------------------------------------------

    function loadDue() {
        fetch('/api/due_concepts')
            .then(function (r) { return r.json(); })
            .then(function (data) { renderDue((data && data.concepts) || []); })
            .catch(function () { renderDue([]); });
    }

    function renderDue(items) {
        var loading = $('due-loading');
        var list = $('due-list');
        var empty = $('due-empty');
        if (loading) loading.hidden = true;

        setCount('stat-due', items.length);
        var badge = $('tab-due-count');
        if (badge) {
            badge.textContent = items.length;
            badge.hidden = items.length === 0;
        }

        if (!items.length) {
            if (empty) empty.hidden = false;
            return;
        }
        if (!list) return;

        list.innerHTML = items.map(function (c) {
            var title = c.front || c.title || c.concept_uid || 'Concept';
            var when = c.next_review_date || '';
            return '<article class="practice-card">' +
                     '<div class="practice-card-body">' +
                       '<h3 class="practice-card-title">' + esc(title) + '</h3>' +
                       (when ? '<p class="practice-card-meta">due ' + esc(when) + '</p>' : '') +
                     '</div>' +
                     '<a class="btn btn-primary practice-card-action" href="/learn?course_uid=' +
                        encodeURIComponent(c.course_uid || '') +
                        '&concept_uid=' + encodeURIComponent(c.concept_uid || '') +
                        '">Review</a>' +
                   '</article>';
        }).join('');
        list.hidden = false;
    }

    // --- quiz ---------------------------------------------------------------

    function loadCourses() {
        var sel = $('quiz-course');
        if (!sel) return;
        fetch('/api/courses')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var courses = (data && (data.courses || data)) || [];
                if (!courses.length) {
                    sel.innerHTML = '<option value="">No courses yet</option>';
                    return;
                }
                sel.innerHTML = courses.map(function (c) {
                    return '<option value="' + esc(c.uid) + '">' +
                           esc(c.title || c.uid) + '</option>';
                }).join('');
            })
            .catch(function () {
                sel.innerHTML = '<option value="">Could not load courses</option>';
            });
    }

    function wireQuiz() {
        var btn = $('quiz-start');
        if (!btn) return;
        btn.addEventListener('click', function () {
            var sel = $('quiz-course');
            var uid = sel && sel.value;
            if (!uid) return;
            var area = $('quiz-area');
            if (area) {
                area.hidden = false;
                area.innerHTML = '<div class="u-skeleton practice-skeleton-row"></div>';
            }
            fetch('/api/quiz?course_uid=' + encodeURIComponent(uid))
                .then(function (r) { return r.json(); })
                .then(function (q) { renderQuiz(q); })
                .catch(function () {
                    if (area) {
                        area.innerHTML = '<p class="practice-error">Could not start a ' +
                            'quiz. Is the course still building?</p>';
                    }
                });
        });
    }

    function renderQuiz(q) {
        var area = $('quiz-area');
        if (!area) return;
        if (!q || !q.question) {
            area.innerHTML = '<p class="practice-error">No question came back.</p>';
            return;
        }
        area.innerHTML =
            '<article class="practice-question u-animate-pop">' +
              '<p class="practice-question-context">' + esc(q.concept_title || '') + '</p>' +
              '<h3 class="practice-question-text">' + esc(q.question) + '</h3>' +
              '<textarea class="form-input practice-answer" rows="4" ' +
                 'placeholder="Answer from memory first."></textarea>' +
            '</article>';
    }

    // --- upcoming -----------------------------------------------------------

    function loadUpcoming() {
        fetch('/api/schedule')
            .then(function (r) { return r.json(); })
            .then(function (data) { renderUpcoming((data && data.reviews) || []); })
            .catch(function () { renderUpcoming([]); });
    }

    function renderUpcoming(reviews) {
        var host = $('upcoming-list');
        var empty = $('upcoming-empty');
        var pending = reviews.filter(function (r) {
            return (r.status || 'pending') !== 'completed';
        });

        setCount('stat-week', withinDays(pending, 7).length);

        if (!pending.length) {
            if (empty) empty.hidden = false;
            if (host) host.innerHTML = '';
            return;
        }
        if (!host) return;

        // Group by date — a flat list of 40 rows tells the learner nothing
        // about shape; "Thursday: 6" does.
        var byDate = {};
        pending.forEach(function (r) {
            var d = r.scheduled_date || 'unscheduled';
            (byDate[d] = byDate[d] || []).push(r);
        });

        host.innerHTML = Object.keys(byDate).sort().map(function (d) {
            var group = byDate[d];
            return '<section class="practice-day">' +
                     '<h3 class="practice-day-title">' + esc(formatDay(d)) +
                       '<span class="practice-day-count">' + group.length + '</span>' +
                     '</h3>' +
                     '<ul class="practice-day-items">' +
                       group.map(function (r) {
                           return '<li>' + esc(r.unit_title || r.unit_uid || 'Concept') + '</li>';
                       }).join('') +
                     '</ul>' +
                   '</section>';
        }).join('');
    }

    function withinDays(reviews, days) {
        var limit = new Date();
        limit.setDate(limit.getDate() + days);
        return reviews.filter(function (r) {
            if (!r.scheduled_date) return false;
            return new Date(r.scheduled_date) <= limit;
        });
    }

    function formatDay(iso) {
        if (iso === 'unscheduled') return 'Unscheduled';
        var d = new Date(iso + 'T00:00:00');
        if (isNaN(d)) return iso;
        var today = new Date(); today.setHours(0, 0, 0, 0);
        var diff = Math.round((d - today) / 86400000);
        if (diff < 0) return 'Overdue';
        if (diff === 0) return 'Today';
        if (diff === 1) return 'Tomorrow';
        if (diff < 7) {
            return d.toLocaleDateString(undefined, { weekday: 'long' });
        }
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }

    function setCount(id, n) {
        var el = $(id);
        if (el) el.textContent = n;
    }

    document.addEventListener('DOMContentLoaded', function () {
        wireTabs();
        wireQuiz();
        show(window.PRACTICE_ACTIVE_TAB || 'due', false);
    });
})();
