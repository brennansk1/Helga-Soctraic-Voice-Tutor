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

    /* A failed request is not an empty schedule.
     *
     * Both of these used to funnel a network error into render*([]), which
     * drew the "Nothing due right now — that is the system working" card. For
     * a spaced-repetition tool that is the worst available lie: the learner is
     * told they are caught up, skips the session, and the reviews that were
     * actually due slip past their interval. Failing loudly costs a moment of
     * friction; failing quietly costs retention.
     */
    function showLoadError(tab, detail) {
        var loading = $(tab + '-loading');
        if (loading) loading.hidden = true;
        var empty = $(tab + '-empty');
        if (empty) empty.hidden = true;
        var err = $(tab + '-error');
        if (err) err.hidden = false;
        var d = $(tab + '-error-detail');
        if (d && detail) {
            // textContent: this string carries a server-supplied status.
            d.textContent = 'Helga could not reach the review service (' +
                detail + '), so this is not a statement that you have ' +
                'nothing due.';
        }
    }

    function okJson(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
    }

    function loadDue() {
        var err = $('due-error');
        if (err) err.hidden = true;
        fetch('/api/due_concepts')
            .then(okJson)
            .then(function (data) { renderDue((data && data.concepts) || []); })
            .catch(function (e) { showLoadError('due', e.message); });
    }


    /* ---------------------------------------------------------- review session
       One card at a time, the way spaced repetition is meant to work: read the
       question, try to recall it, reveal, then say honestly how it went. The
       due list is a manifest; this is the work.

       Only cards with a real question AND answer can be reviewed this way. A
       due CONCEPT with no card behind it is not a flashcard and keeps its
       "open the lesson" action in the list below — conflating the two would
       ask the learner to grade their recall of something they were never
       shown a question for. */
    var lastDueItems = [];
    var session = { queue: [], i: 0, revealed: false, graded: {again:0,hard:0,good:0,easy:0} };

    function reviewable(items) {
        return (items || []).filter(function (c) {
            return c && String(c.front || '').trim() && String(c.back || '').trim();
        });
    }

    function startReview(items) {
        session.queue = reviewable(items);
        session.i = 0;
        session.revealed = false;
        session.graded = { again: 0, hard: 0, good: 0, easy: 0 };
        if (!session.queue.length) { return; }
        setShown('review-session', true);
        setShown('due-list', false);
        setShown('review-start-wrap', false);
        setShown('review-done', false);
        renderCard();
        document.addEventListener('keydown', onReviewKey);
    }

    function endReview(finished) {
        document.removeEventListener('keydown', onReviewKey);
        setShown('review-session', false);
        setShown('due-list', true);
        setShown('review-start-wrap', true);
        if (finished) {
            $('review-done-detail').textContent = summarise(session.graded);
            setShown('review-done', true);
        }
        loadDue();   // grades moved cards out of the queue; re-read it
    }

    /* Only the grades that actually happened. "0 easy, 0 hard" is noise
       dressed as information. */
    function summarise(g) {
        var total = g.again + g.hard + g.good + g.easy;
        var parts = [];
        if (g.easy)  { parts.push(g.easy + ' easy'); }
        if (g.good)  { parts.push(g.good + ' good'); }
        if (g.hard)  { parts.push(g.hard + ' hard'); }
        if (g.again) { parts.push(g.again + ' to see again soon'); }
        return total + (total === 1 ? ' card' : ' cards') + ' reviewed' +
               (parts.length ? ' — ' + parts.join(', ') + '.' : '.');
    }

    function renderCard() {
        var c = session.queue[session.i];
        if (!c) { endReview(true); return; }
        session.revealed = false;
        $('review-progress').textContent = (session.i + 1) + ' / ' + session.queue.length;
        $('review-bar-fill').style.width =
            Math.round((session.i / session.queue.length) * 100) + '%';
        $('review-front').textContent = c.front || '';
        $('review-back-body').textContent = c.back || '';
        setShown('review-back', false);
        setShown('review-grades', false);
        setShown('review-reveal-wrap', true);
        $('review-feedback').textContent = '';
    }

    function reveal() {
        if (session.revealed) { return; }
        session.revealed = true;
        setShown('review-back', true);
        setShown('review-reveal-wrap', false);
        setShown('review-grades', true);
    }

    var GRADE_NAME = { 1: 'again', 2: 'hard', 3: 'good', 4: 'easy' };

    function grade(n) {
        if (!session.revealed) { return; }   // grading before looking is not a review
        var c = session.queue[session.i];
        session.graded[GRADE_NAME[n]] += 1;
        var fb = $('review-feedback');
        fb.textContent = 'Saving…';

        fetch('/api/grade_card_fsrs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            /* The scheduler's contract is {uid, rating} — see
               librarian.grade_card_fsrs_endpoint. Sending anything else earns a
               silent 400, which is how this path stayed dead for 40 cards. */
            body: JSON.stringify({ uid: c.uid, rating: n })
        })
        .then(function (r) {
            if (!r.ok) { throw new Error('scheduler returned ' + r.status); }
            return r.json();
        })
        .then(function (d) {
            /* THE REAL INTERVAL, from the scheduler that owns it. The buttons
               deliberately promise no number; this is where one appears, and
               it is the one actually written down. */
            var days = d && (d.interval_days != null ? d.interval_days : d.interval);
            if (days != null) {
                fb.textContent = days < 1 ? 'Again shortly.'
                    : 'Next in ' + Math.round(days) + ' day' + (Math.round(days) === 1 ? '' : 's') + '.';
            } else { fb.textContent = ''; }
        })
        .catch(function (e) {
            fb.textContent = 'Not recorded — this card stays due. (' + e.message + ')';
        })
        .then(function () {
            session.i += 1;
            setTimeout(renderCard, 350);
        });
    }

    function onReviewKey(e) {
        if (e.target && /INPUT|TEXTAREA/.test(e.target.tagName)) { return; }
        if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); reveal(); return; }
        if (/^[1-4]$/.test(e.key)) { e.preventDefault(); grade(parseInt(e.key, 10)); return; }
        if (e.key === 'Escape') { endReview(false); }
    }

    function setShown(id, on) {
        var el = $(id);
        if (el) { el.hidden = !on; }
    }

    function renderDue(items) {
        var loading = $('due-loading');
        var list = $('due-list');
        var empty = $('due-empty');
        if (loading) loading.hidden = true;

        setCount('stat-due', items.length);

        /* HOW MANY OF THESE CAN ACTUALLY BE REVIEWED.
           The queue mixes flashcards (a question and an answer) with concepts
           that are merely due. Only the first kind can be graded on recall, so
           the button says how many there are rather than implying the whole
           queue works that way. */
        var cards = reviewable(items);
        var startWrap = $('review-start-wrap');
        var startHint = $('review-start-hint');
        if (startWrap) { startWrap.hidden = cards.length === 0; }
        if (startHint) {
            var rest = items.length - cards.length;
            startHint.textContent = cards.length + ' card' +
                (cards.length === 1 ? '' : 's') + ' ready' +
                (rest ? ' · ' + rest + ' concept' + (rest === 1 ? '' : 's') +
                        ' to revisit below' : '');
        }
        var startBtn = $('review-start');
        if (startBtn && startBtn.dataset.bound !== '1') {
            startBtn.dataset.bound = '1';
            startBtn.addEventListener('click', function () { startReview(lastDueItems); });
        }
        var quit = $('review-quit');
        if (quit && quit.dataset.bound !== '1') {
            quit.dataset.bound = '1';
            quit.addEventListener('click', function () { endReview(false); });
        }
        var rev = $('review-reveal');
        if (rev && rev.dataset.bound !== '1') {
            rev.dataset.bound = '1';
            rev.addEventListener('click', reveal);
        }
        var grades = $('review-grades');
        if (grades && grades.dataset.bound !== '1') {
            grades.dataset.bound = '1';
            grades.addEventListener('click', function (e) {
                var b = e.target.closest('[data-grade]');
                if (b) { grade(parseInt(b.dataset.grade, 10)); }
            });
        }
        lastDueItems = items;

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
            // A BARE SKELETON FOR FOUR MINUTES READS AS BROKEN.
            //
            // The question is written by the model on local hardware. Measured
            // on this machine: 13 seconds when it is warm, 253 when it is not.
            // The old loading state was one featureless grey bar for the whole
            // of that, with nothing to say whether it was working or hung —
            // and looking at it is the only way that shows up, because the
            // endpoint returns 200 either way.
            //
            // The house rule everywhere else in this app is a counter, never a
            // bare spinner. Same rule here.
            var _elapsed = 0, _tick = null;
            if (area) {
                area.hidden = false;
                area.innerHTML =
                    '<div class="u-skeleton practice-skeleton-row"></div>' +
                    '<p class="practice-loading-note">' +
                    'Writing a question<span id="quiz-elapsed"></span></p>';
                _tick = setInterval(function () {
                    _elapsed += 1;
                    var el = $('quiz-elapsed');
                    if (!el) return;
                    // Past twenty seconds, say WHY it is slow rather than
                    // leaving the learner to guess.
                    el.textContent = ' — ' + _elapsed + 's' +
                        (_elapsed > 20 ? ', the model is warming up' : '');
                }, 1000);
            }
            var _done = function () { if (_tick) { clearInterval(_tick); _tick = null; } };
            fetch('/api/quiz?course_uid=' + encodeURIComponent(uid))
                .then(function (r) { return r.json(); })
                .then(function (q) { _done(); renderQuiz(q); })
                .catch(function () {
                    _done();
                    if (area) {
                        area.innerHTML = '<p class="practice-error">Could not start a ' +
                            'quiz. Is the course still building?</p>';
                    }
                });
        });
    }

    /* The question being asked right now. Grading needs the reference material
       the question was generated from, and /api/quiz hands it over only here. */
    var current = null;

    function renderQuiz(q) {
        var area = $('quiz-area');
        if (!area) return;
        if (!q || !q.question) {
            area.innerHTML = '<p class="practice-error"></p>';
            area.firstChild.textContent = (q && q.error) || 'No question came back.';
            return;
        }
        current = q;

        /* THE TAB USED TO END HERE.
           It drew the question and a textarea and stopped: no submit control,
           no listener, and no call to /api/quiz/grade anywhere in this file. A
           learner could type an answer and had no way to hand it in — retrieval
           practice with the retrieval taken out. The /quiz page already speaks
           to the grader; this is the same conversation. */
        area.textContent = '';
        var art = document.createElement('article');
        art.className = 'practice-question u-animate-pop';

        var ctx = document.createElement('p');
        ctx.className = 'practice-question-context';
        ctx.textContent = q.concept_title || '';

        var h = document.createElement('h3');
        h.className = 'practice-question-text';
        h.textContent = q.question;

        var ta = document.createElement('textarea');
        ta.className = 'form-input practice-answer';
        ta.rows = 4;
        ta.id = 'quiz-answer';
        ta.placeholder = 'Answer from memory first.';

        var row = document.createElement('div');
        row.className = 'practice-course-picker';
        var submit = document.createElement('button');
        submit.className = 'btn btn-primary';
        submit.id = 'quiz-submit';
        submit.type = 'button';
        submit.textContent = 'Check my answer';
        var skip = document.createElement('button');
        skip.className = 'btn btn-secondary';
        skip.type = 'button';
        skip.textContent = 'Another question';
        row.appendChild(submit);
        row.appendChild(skip);

        var result = document.createElement('div');
        result.id = 'quiz-result';
        result.setAttribute('aria-live', 'polite');

        art.appendChild(ctx);
        art.appendChild(h);
        art.appendChild(ta);
        art.appendChild(row);
        art.appendChild(result);
        area.appendChild(art);

        submit.addEventListener('click', function () { gradeAnswer(ta, submit, result); });
        // Ctrl/Cmd+Enter submits — Enter alone has to stay a newline in a
        // multi-line answer box.
        ta.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                gradeAnswer(ta, submit, result);
            }
        });
        skip.addEventListener('click', nextQuestion);
        ta.focus();
    }

    function nextQuestion() {
        var btn = $('quiz-start');
        if (btn) btn.click();
    }

    function gradeError(result, detail) {
        /* A GRADER THAT COULD NOT RUN IS NOT A VERDICT. librarian answers a
           failed grading with a non-2xx and a named error precisely so that no
           client turns an Ollama hiccup into a red cross against the learner —
           and, downstream of that, into FSRS downgrades of cards the model never
           looked at. Say what failed; do not render a grade. */
        result.textContent = '';
        var p = document.createElement('p');
        p.className = 'practice-error';
        p.textContent = 'Your answer was not graded (' + detail +
            '). It has not been marked wrong — try again in a moment.';
        result.appendChild(p);
    }

    function gradeAnswer(ta, submit, result) {
        var answer = (ta.value || '').trim();
        if (!answer || !current) return;
        submit.disabled = true;
        submit.textContent = 'Checking…';
        result.textContent = '';

        fetch('/api/quiz/grade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question: current.question,
                answer: answer,
                context: current.context_text || '',
                concept_uid: current.concept_uid || '',
                course_uid: current.course_uid || ''
            })
        })
            .then(function (r) {
                return r.json().catch(function () { return {}; })
                    .then(function (b) { return { ok: r.ok, status: r.status, body: b || {} }; });
            })
            .then(function (res) {
                submit.disabled = false;
                submit.textContent = 'Check my answer';
                if (!res.ok || !res.body.grade) {
                    gradeError(result, res.body.error || 'HTTP ' + res.status);
                    return;
                }
                renderGrade(result, res.body);
            })
            .catch(function (e) {
                submit.disabled = false;
                submit.textContent = 'Check my answer';
                gradeError(result, e.message);
            });
    }

    function renderGrade(result, data) {
        result.textContent = '';

        var verdict = document.createElement('p');
        verdict.className = 'practice-question-context';
        var pct = parseInt(data.score, 10);
        verdict.textContent = String(data.grade) +
            (isNaN(pct) ? '' : ' — ' + pct + '%');
        result.appendChild(verdict);

        if (data.feedback) {
            var fb = document.createElement('p');
            fb.textContent = data.feedback;          // server text: textContent
            result.appendChild(fb);
        }
        if (data.missing_concepts && data.missing_concepts.length) {
            var miss = document.createElement('p');
            miss.className = 'practice-question-context';
            miss.textContent = 'Missing: ' + data.missing_concepts.join(', ');
            result.appendChild(miss);
        }
        if (data.cards_created) {
            var cards = document.createElement('p');
            cards.className = 'practice-question-context';
            cards.textContent = data.cards_created + ' flashcard(s) added for review.';
            result.appendChild(cards);
        }

        var again = document.createElement('button');
        again.className = 'btn btn-primary';
        again.type = 'button';
        again.textContent = 'Next question';
        again.addEventListener('click', nextQuestion);
        result.appendChild(again);
    }

    // --- upcoming -----------------------------------------------------------

    function loadUpcoming() {
        var err = $('upcoming-error');
        if (err) err.hidden = true;
        fetch('/api/schedule')
            .then(okJson)
            .then(function (data) { renderUpcoming((data && data.reviews) || []); })
            .catch(function (e) { showLoadError('upcoming', e.message); });
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
    // Retry buttons on the two error cards.
    document.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('[data-practice-retry]');
        if (!b) return;
        var tab = b.getAttribute('data-practice-retry');
        if (tab === 'due') loadDue();
        else if (tab === 'upcoming') loadUpcoming();
    });
})();
