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
        /* Hide whatever the last successful load rendered. Numbers from an
           earlier request, left on screen beside an error, are read as current
           — and on this page they are counts of what you owe today. An error
           the learner can see beats a figure they cannot date. */
        var stale = $(tab === 'upcoming' ? 'horizon' : 'due-list');
        if (stale) { stale.hidden = true; }
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

    /* Which courses today's session draws from. Empty means all of them, which
       is the default AND the better one: interleaving across courses is a
       desirable difficulty, not a compromise. Remembered per browser so the
       choice does not have to be made daily. */
    var scope = (function () {
        try { return localStorage.getItem('helga-review-scope') || ''; }
        catch (e) { return ''; }
    })();

    function setScope(uid) {
        scope = uid || '';
        try { localStorage.setItem('helga-review-scope', scope); }
        catch (e) { /* private mode: the choice just will not persist */ }
        loadDue();
    }

    function loadDue() {
        var err = $('due-error');
        if (err) err.hidden = true;
        var url = '/api/review/queue' +
                  (scope ? '?course_uid=' + encodeURIComponent(scope) : '');
        fetch(url)
            .then(okJson)
            .then(renderDue)
            .catch(function (e) { showLoadError('due', e.message); });
    }

    /* Ninety-seven of these is a wall, not a list, and a wall gets scrolled
       past. Show the worst few — the ones forgotten most — and say how many
       more there are rather than rendering all of them. */
    var LEECHES_SHOWN = 5;

    function renderLeeches(leeches) {
        if (!leeches.length) { return ''; }
        var worst = leeches.slice()
            .sort(function (a, b) { return (b.lapses || 0) - (a.lapses || 0); });
        var shown = worst.slice(0, LEECHES_SHOWN);
        var rest = worst.length - shown.length;

        return '<h3 class="due-subhead">Worth going through again</h3>' +
               '<p class="due-note is-tight">These have been forgotten enough ' +
                 'times that another card is unlikely to be what fixes them.</p>' +
               shown.map(function (l) {
                   return '<article class="practice-card">' +
                            '<div class="practice-card-body">' +
                              '<h3 class="practice-card-title">' + fmtInline(l.front) + '</h3>' +
                              '<p class="practice-card-meta">' +
                                 esc(l.course_title || '') +
                                 (l.course_title ? ' · ' : '') +
                                 'forgotten ' + esc(String(l.lapses)) + ' times</p>' +
                            '</div>' +
                            '<a class="btn btn-primary practice-card-action" href="/learn?course_uid=' +
                               encodeURIComponent(l.course_uid || '') +
                               '&concept_uid=' + encodeURIComponent(l.concept_uid || '') +
                               '">Revisit</a>' +
                          '</article>';
               }).join('') +
               (rest ? '<p class="due-note is-tight">' + rest.toLocaleString() +
                       ' more like these are waiting. They stay out of the daily ' +
                       'queue until you go through the concept again.</p>' : '');
    }

    /* Inline formatting only — for a card title, where a <ul> would be wrong. */
    function fmtInline(text) {
        return esc(text || '')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    }

    /* An empty day next to thousands of unstarted items reads as a broken
       queue unless it says when work comes back and why it is not now. */
    function explainWhenWorkReturns(data) {
        var el = $('due-empty-next');
        if (!el) { return; }
        var counts = (data && data.counts) || {};
        var bits = [];

        if (data && data.new_today) {
            bits.push('You have already started ' + data.new_today +
                      ' new ' + (data.new_today === 1 ? 'item' : 'items') +
                      " today — that is the day's allowance, and it resets tomorrow");
        } else if (counts.new_available) {
            bits.push(counts.new_available.toLocaleString() +
                      ' items are waiting to be started; Helga introduces them a ' +
                      'few a day rather than all at once');
        }
        if (counts.upcoming) {
            bits.push(counts.upcoming.toLocaleString() +
                      ' reviews are already scheduled ahead');
        }
        if (counts.leeches) {
            bits.push(counts.leeches.toLocaleString() +
                      ' need the concept again rather than another card');
        }
        el.textContent = bits.join('. ') + (bits.length ? '.' : '');
        el.hidden = !bits.length;
    }

    function queueLength(data) {
        return ((data && data.queue) || []).length;
    }

    /* Populated once from the item bank, not from today's queue: a course with
       nothing due today is still a course you can choose to drill. */
    /* "This week" is the same question on whichever tab you are looking at, so
       the Due tab fills it too rather than leaving a dash until you happen to
       open the horizon. */
    var weekLoaded = false;
    function loadWeekAhead() {
        if (weekLoaded) { return; }
        weekLoaded = true;
        fetch('/api/review/forecast?days=7').then(okJson).then(function (d) {
            var week = ((d && d.forecast) || [])
                .reduce(function (a, p) { return a + (p.count || 0); }, 0);
            setCount('stat-week', week);
        }).catch(function () { /* the headline stat is not worth an error card */ });
    }

    var scopeLoaded = false;
    var knownCourses = [];      // cached so the note can re-render on every change
    function loadScopeOptions() {
        if (scopeLoaded) { renderScopeNote(); return; }
        scopeLoaded = true;
        fetch('/api/review/stats').then(okJson).then(function (stats) {
            var courses = (stats && stats.courses) || [];
            knownCourses = courses;
            var wrap = $('review-scope-wrap');
            var sel = $('review-scope');
            if (!sel || courses.length < 2) { return; }   // one course: no choice to make
            courses.forEach(function (c) {
                var o = document.createElement('option');
                o.value = c.course_uid;
                o.textContent = c.title + ' (' + c.total.toLocaleString() + ')';
                sel.appendChild(o);
            });
            sel.value = scope;
            if (wrap) { wrap.hidden = false; }
            sel.addEventListener('change', function () { setScope(this.value); });
            renderScopeNote();
        }).catch(function () { /* the selector is a convenience, not the feature */ });
    }

    /* Re-rendered on every load, not once: the note said "Drilling SQL only"
       for the rest of the session after switching back to all courses. */
    function renderScopeNote() {
        var note = $('review-scope-note');
        if (!note) { return; }
        if (!scope) { note.hidden = true; note.textContent = ''; return; }
        var found = knownCourses.filter(function (c) { return c.course_uid === scope; })[0];
        note.textContent = 'Drilling ' + (found ? found.title : 'one course') +
            ' only. Mixing courses is better for retention — Helga interleaves ' +
            'them deliberately — so switch back when you are done here.';
        note.hidden = false;
    }

    var KIND_WORD = {
        recall: 'recall', discriminate: 'true/false',
        apply: 'applied', socratic: 'explain'
    };

    /* The Due tab is a briefing on today's session, not a list of cards. What
       is being held back matters as much as what is being shown: a queue that
       silently truncates reads as "you are finished". */
    function renderDue(data) {
        var loading = $('due-loading');
        var list = $('due-list');
        var empty = $('due-empty');
        if (loading) { loading.hidden = true; }

        var queue = (data && data.queue) || [];
        var counts = (data && data.counts) || {};
        loadScopeOptions();
        loadWeekAhead();
        /* "Due now" is what there is to DO today, not how big the bank is.
           due_total counts every unseen item too, so it read 2,495 on a day
           whose session was fourteen items — a number that looks like a debt
           nobody could ever clear, which is exactly how a review habit dies. */
        var today = queueLength(data);
        setCount('stat-due', today);
        var badge = $('tab-due-count');
        if (badge) {
            badge.textContent = today;
            badge.hidden = !today;
        }

        if (!queue.length) {
            if (list) { list.hidden = true; }
            if (empty) { empty.hidden = false; }
            setShown('review-start-wrap', false);
            renderScopeNote();          // a scoped empty day must say it is scoped
            explainWhenWorkReturns(data);
            return;
        }
        if (empty) { empty.hidden = true; }
        setShown('review-start-wrap', true);

        var mix = {};
        queue.forEach(function (i) { mix[i.kind] = (mix[i.kind] || 0) + 1; });
        var parts = Object.keys(mix).map(function (k) {
            return mix[k] + ' ' + (KIND_WORD[k] || k);
        });
        var hint = $('review-start-hint');
        if (hint) {
            hint.textContent = queue.length + ' in today\'s session — ' +
                               parts.join(', ') + '.';
        }

        // Everything the session is NOT showing, said plainly.
        var notes = [];
        if (counts.held_back) {
            notes.push(counts.held_back + ' more ' +
                       (counts.held_back === 1 ? 'is' : 'are') +
                       ' due but held back to keep today finishable');
        }
        if (data.new_paused_for_backlog) {
            notes.push('new material is paused until the backlog clears');
        }
        if (counts.leeches) {
            notes.push(counts.leeches + ' ' +
                       (counts.leeches === 1 ? 'item keeps' : 'items keep') +
                       ' being forgotten and ' +
                       (counts.leeches === 1 ? 'needs' : 'need') + ' the concept again');
        }
        if (counts.upcoming) {
            notes.push(counts.upcoming + ' scheduled for later');
        }
        if (counts.new_available) {
            notes.push(counts.new_available.toLocaleString() +
                       ' not started yet, introduced a few each day');
        }

        if (list) {
            list.innerHTML =
                (notes.length
                    ? '<p class="due-note">' + esc(notes.join(' · ')) + '.</p>'
                    : '') +
                renderLeeches(data.leeches || []);
            list.hidden = false;
        }
    }


    /* ---------------------------------------------------------- review session
       One item at a time from /api/review/queue, which is already ordered,
       interleaved across courses and capped for the day — the browser's job is
       to present it honestly, not to re-decide any of that.

       The queue is deliberately MIXED: recall, true/false discrimination,
       applied prediction and open Socratic questions, because practising facts
       alone transfers no better than not practising at all. Each kind is asked
       for differently, so the learner cannot answer an analysis prompt with a
       flashcard reflex.
    */
    var GRADE_NAME = { 1: 'again', 2: 'hard', 3: 'good', 4: 'easy' };

    var KIND = {
        recall:       { label: 'Recall',        ask: 'Question',
                        reveal: 'Answer',
                        hint: 'Bring it to mind before you look.' },
        discriminate: { label: 'True or false', ask: 'Is this accurate?',
                        reveal: 'Verdict',
                        hint: 'Commit to an answer first.' },
        apply:        { label: 'Apply',         ask: 'Work it through',
                        reveal: 'What actually happens',
                        hint: 'Predict the outcome before revealing it.' },
        socratic:     { label: 'Explain',       ask: 'Explain in your own words',
                        reveal: 'What a good answer covers',
                        hint: 'Answer aloud or in your head, then mark yourself against the criteria.' }
    };

    var session = {
        queue: [], i: 0, revealed: false, answered: null,
        graded: { again: 0, hard: 0, good: 0, easy: 0 },
        counts: null, cap: 0, capped: false
    };

    function kindInfo(kind) { return KIND[kind] || KIND.recall; }

    function startReview() {
        setShown('review-done', false);
        var hint = $('review-start-hint');
        if (hint) { hint.textContent = 'Loading your queue…'; }

        fetch('/api/review/queue')
            .then(okJson)
            .then(function (d) {
                session.queue = d.queue || [];
                session.i = 0;
                session.graded = { again: 0, hard: 0, good: 0, easy: 0 };
                session.counts = d.counts || null;
                session.multiCourse = (function () {
                    var seen = {};
                    (d.queue || []).forEach(function (i) { seen[i.course_uid] = 1; });
                    return Object.keys(seen).length > 1;
                })();
                session.cap = d.daily_cap || 0;
                session.capped = !!d.capped;
                if (!session.queue.length) {
                    if (hint) { hint.textContent = 'Nothing is due right now.'; }
                    return;
                }
                setShown('due-list', false);
                setShown('review-start-wrap', false);
                setShown('review-session', true);
                document.addEventListener('keydown', onReviewKey);
                renderCard();
            })
            .catch(function (e) {
                if (hint) {
                    hint.textContent = 'Could not load your queue (' + e.message + ').';
                }
            });
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
        loadDue();
    }

    /* Only the grades that actually happened, plus what today's cap held back.
       A session that ends without mentioning the backlog reads as "you are
       finished", which is the one thing it must never imply. */
    function summarise(g) {
        var total = g.again + g.hard + g.good + g.easy;
        var parts = [];
        if (g.easy)  { parts.push(g.easy + ' easy'); }
        if (g.good)  { parts.push(g.good + ' good'); }
        if (g.hard)  { parts.push(g.hard + ' hard'); }
        if (g.again) { parts.push(g.again + ' to see again soon'); }
        var line = total + (total === 1 ? ' item' : ' items') + ' reviewed' +
                   (parts.length ? ' — ' + parts.join(', ') + '.' : '.');
        var held = session.counts && session.counts.held_back;
        if (held) {
            line += ' ' + held + ' more ' + (held === 1 ? 'was' : 'were') +
                    " held back so today's session stays finishable.";
        }
        return line;
    }

    /* Named currentItem, not current: the quiz section below declares
   `var current` in this same scope, and a var assignment overwrites a
   hoisted function of the same name at runtime — the review session
   died with "current is not a function" the moment it loaded. */
    function currentItem() { return session.queue[session.i]; }

    function renderCard() {
        var item = currentItem();
        if (!item) { endReview(true); return; }

        session.revealed = false;
        session.answered = null;
        session.graded_uid = null;
        var info = kindInfo(item.kind);

        $('review-progress').textContent = (session.i + 1) + ' / ' + session.queue.length;
        $('review-bar-fill').style.width =
            Math.round((session.i / session.queue.length) * 100) + '%';

        var kindEl = $('review-kind');
        kindEl.textContent = info.label;
        kindEl.className = 'review-kind is-' + item.kind;
        /* Only worth saying when more than one course is in play — on a
           single-course session it is the same word on every card. */
        var courseEl = $('review-course');
        courseEl.textContent = session.multiCourse ? (item.course_title || '') : '';
        courseEl.hidden = !courseEl.textContent;
        setShown('review-new', !!item.is_new);

        $('review-front-label').textContent = info.ask;
        $('review-front').innerHTML = fmt(item.front);
        $('review-back-label').textContent = info.reveal;
        $('review-back-body').innerHTML = fmt(item.back);

        setShown('review-back', false);
        setShown('review-verdict', false);
        setShown('review-grades', false);
        setShown('review-leech', false);
        $('review-feedback').textContent = '';

        /* Retrigger the entry animation. Removing the class and forcing a
           reflow before re-adding it is the only reliable way to replay a CSS
           animation on an element that was never detached — without the
           reflow the browser coalesces both changes and nothing moves. */
        var card = $('review-card');
        if (card) {
            card.classList.remove('is-fresh');
            void card.offsetWidth;
            card.classList.add('is-fresh');
        }

        // True/false is answered before the reveal; everything else reveals first.
        /* Open questions get an answer box. Marking against the author's
           criteria beats self-marking most on exactly this tier — a learner who
           has just read the criteria is the worst judge of whether their own
           answer met them. */
        var isOpen = item.kind === 'socratic';
        setShown('review-mark', isOpen);
        setShown('review-marked', false);
        var box = $('review-answer');
        if (box) { box.value = ''; }
        var markBtn = $('review-mark-btn');
        if (markBtn) { markBtn.disabled = false; markBtn.textContent = 'Mark my answer'; }
        var markNote = $('review-mark-note');
        if (markNote) { markNote.textContent = ''; }

        var isChoice = item.kind === 'discriminate';
        setShown('review-choices', isChoice);
        setShown('review-reveal-wrap', !isChoice);
        var hintEl = document.querySelector('.review-key-hint');
        if (hintEl) { hintEl.textContent = info.hint; }
    }

    /* Item text is author-written markdown-ish: inline code and bold carry
       meaning in every one of these prompts, so they are rendered — and
       everything else is escaped, because this is still untrusted text. */
    function fmt(text) {
        var html = esc(text || '')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

        /* Criteria arrive as a bullet list and are the one place the learner
           reads point by point to mark themselves. Rendered as a paragraph
           they run together into a wall the eye slides off. */
        var lines = html.split('\n');
        var out = [], list = null;
        lines.forEach(function (line) {
            var m = line.match(/^\s*[-*]\s+(.*)$/);
            if (m) {
                if (!list) { list = []; }
                list.push('<li>' + m[1] + '</li>');
            } else {
                if (list) { out.push('<ul class="review-criteria">' + list.join('') + '</ul>'); list = null; }
                if (line.trim()) { out.push('<p>' + line + '</p>'); }
            }
        });
        if (list) { out.push('<ul class="review-criteria">' + list.join('') + '</ul>'); }
        return out.join('') || html;
    }

    function reveal() {
        if (session.revealed) { return; }
        var item = currentItem();
        if (!item) { return; }
        session.revealed = true;
        setShown('review-back', true);
        setShown('review-reveal-wrap', false);
        setShown('review-choices', false);
        /* The criteria are on screen now. An answer typed after reading them is
           not evidence of recall, so the box closes rather than inviting one. */
        setShown('review-mark', false);
        /* A true/false item has already been graded by the time it reveals —
           the learner committed and the content says which answer was right.
           Offering the four self-rating buttons underneath invites a second
           grade on the same item, and self-rating is the thing this tier exists
           to avoid. */
        setShown('review-grades', item.kind !== 'discriminate');
    }

    /* A discrimination item is graded objectively: the learner committed to an
       answer and the content says which one was right, so nothing here depends
       on self-assessment — which is known to over-report. */
    function answerChoice(said) {
        var item = currentItem();
        if (!item || session.answered !== null) { return; }
        var truth = !!(item.payload && item.payload.truth);
        session.answered = said;
        var right = (said === truth);

        var verdict = $('review-verdict');
        verdict.textContent = right ? 'Correct.' : 'Not quite.';
        verdict.className = 'review-verdict ' + (right ? 'is-right' : 'is-wrong');
        setShown('review-verdict', true);
        reveal();

        /* Objective result, mapped onto the same 1-4 FSRS takes from every
           other tier. Right is "Good", not "Easy": there were two options, so a
           correct answer is weaker evidence than recalling something cold. */
        grade(right ? 3 : 1, true);
    }

    /* THE ONLY MODEL CALL IN THE REVIEW LOOP. Measured on this machine: about
       11 seconds warm, well over two minutes cold. The wait is narrated rather
       than hidden, and a failure leaves the learner exactly where they were —
       able to reveal and mark themselves. */
    function markAnswer() {
        var item = currentItem();
        var box = $('review-answer');
        if (!item || !box) { return; }
        var answer = (box.value || '').trim();
        if (!answer) {
            $('review-mark-note').textContent = 'Write an answer first.';
            return;
        }
        var btn = $('review-mark-btn');
        btn.disabled = true;
        btn.textContent = 'Marking…';
        var note = $('review-mark-note');
        note.textContent = 'This one asks the model, so it can take a while — '
                         + 'longer still if it has gone cold.';

        fetch('/api/review/check_answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ uid: item.uid, answer: answer })
        })
        .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
        .then(function (res) {
            if (!res.ok || !res.body || res.body.gradable === false) {
                throw new Error((res.body && res.body.error) || 'could not mark it');
            }
            showMarked(res.body);
            reveal();
            grade(res.body.grade, true);
        })
        .catch(function (e) {
            btn.disabled = false;
            btn.textContent = 'Mark my answer';
            /* Say what happened and leave self-marking available, rather than
               inventing a grade nobody produced. */
            note.textContent = 'Could not mark it (' + e.message +
                               '). Reveal the criteria and mark yourself.';
        });
    }

    var GRADE_WORD = { 1: 'Not yet', 2: 'Partly there', 3: 'Met the criteria',
                       4: 'Met them comfortably' };

    function showMarked(v) {
        $('review-marked-grade').textContent =
            (GRADE_WORD[v.grade] || 'Marked') + '.';
        $('review-marked-note').textContent = v.note || '';
        var missed = (v.missed || []).filter(Boolean);
        $('review-marked-missed').innerHTML = missed.length
            ? missed.map(function (m) { return '<li>' + esc(m) + '</li>'; }).join('')
            : '';
        setShown('review-marked', true);
    }

    function grade(n, auto) {
        if (!session.revealed) { return; }
        var item = currentItem();
        if (!item) { return; }
        // One grade per item. A keyboard 1-4 after an objective true/false
        // would otherwise send a second, contradictory rating.
        if (session.graded_uid === item.uid) { return; }
        session.graded_uid = item.uid;
        session.graded[GRADE_NAME[n]] += 1;

        var fb = $('review-feedback');
        fb.textContent = 'Saving…';

        fetch('/api/review/grade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            /* concept_uid and course_uid travel with the grade because the
               server needs them to answer "is what this rests on also weak?".
               Sending only {uid, rating} left that lookup with nothing to look
               up, so it returned nothing every time — the feature existed on
               both sides and never once fired. */
            body: JSON.stringify({
                uid: item.uid,
                rating: n,
                concept_uid: item.concept_uid || '',
                course_uid: item.course_uid || '',
                kind: item.kind || ''
            })
        })
        .then(function (r) {
            if (!r.ok) { throw new Error('scheduler returned ' + r.status); }
            return r.json();
        })
        .then(function (d) {
            /* The interval comes from the scheduler that wrote it down. The
               grade buttons promise no number precisely so this one cannot be
               contradicted. */
            var days = d && (d.interval_days != null ? d.interval_days : d.interval);
            fb.textContent = days == null ? ''
                : (days < 1 ? 'Again shortly.'
                   : 'Next in ' + Math.round(days) + ' day' +
                     (Math.round(days) === 1 ? '' : 's') + '.');
            if (d && d.leech) { offerRepair(item, d.weak_prerequisite); return; }
            advance(auto ? 900 : 350);
        })
        .catch(function (e) {
            fb.textContent = 'Not recorded — this item stays due. (' + e.message + ')';
            advance(1200);
        });
    }

    /* Repeated forgetting is a teaching problem, not a scheduling one — and
       often not a problem with THIS concept. The server checks what this one
       rests on; if something underneath is also failing, going back to the
       dependent re-teaches a symptom. */
    function offerRepair(item, weakRoot) {
        var text = $('review-leech-text');
        var link = $('review-leech-link');

        if (weakRoot && weakRoot.concept_uid) {
            text.textContent = 'You have lost this one several times, and ' +
                (weakRoot.title ? '\u201c' + weakRoot.title + '\u201d' : 'a concept it rests on') +
                ' — which this one is built on — is slipping too. That is the ' +
                'more likely place to fix it.';
            link.textContent = weakRoot.title
                ? 'Go back to \u201c' + weakRoot.title + '\u201d'
                : 'Go back to what this rests on';
            link.href = '/learn?course_uid=' +
                        encodeURIComponent(weakRoot.course_uid || item.course_uid || '') +
                        '&concept_uid=' + encodeURIComponent(weakRoot.concept_uid);
            setShown('review-leech', true);
            setShown('review-grades', false);
            advance(6000);      // a longer read; there is a decision in it
            return;
        }

        text.textContent = 'You have lost this one several times. Reviewing it ' +
                           'again next week is unlikely to be what fixes it.';
        link.textContent = 'Go through this concept again';
        link.href = '/learn?course_uid=' + encodeURIComponent(item.course_uid || '') +
                    '&concept_uid=' + encodeURIComponent(item.concept_uid || '');
        setShown('review-leech', true);
        setShown('review-grades', false);
        // The learner chooses: repair now, or carry on and decide later.
        advance(4000);
    }

    function advance(delay) {
        session.i += 1;
        setTimeout(renderCard, delay || 350);
    }

    function onReviewKey(e) {
        if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) { return; }
        if (e.metaKey || e.ctrlKey || e.altKey) { return; }
        var item = currentItem();
        if (item && item.kind === 'discriminate' && session.answered === null) {
            if (e.key === 't' || e.key === 'T') { e.preventDefault(); answerChoice(true); return; }
            if (e.key === 'f' || e.key === 'F') { e.preventDefault(); answerChoice(false); return; }
        }
        if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); reveal(); return; }
        if (/^[1-4]$/.test(e.key)) { e.preventDefault(); grade(parseInt(e.key, 10)); return; }
        if (e.key === 'Escape') { endReview(false); }
    }

    function setShown(id, on) {
        var el = $(id);
        if (el) { el.hidden = !on; }
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

    // --- upcoming: the long horizon ------------------------------------------

    var BANDS = [
        ['new',      'Not started', 'Never reviewed.'],
        ['learning', 'Learning',    'Coming back within a week.'],
        ['young',    'Settling',    'Holding for a week or more.'],
        ['mature',   'Known',       'Holding for three weeks or more.'],
        ['retired',  'Long-term',   'Holding for a year; barely shown now.']
    ];

    function loadUpcoming() {
        var err = $('upcoming-error');
        if (err) err.hidden = true;
        Promise.all([
            fetch('/api/review/stats').then(okJson),
            fetch('/api/review/forecast?days=30').then(okJson)
        ])
            .then(function (r) { renderHorizon(r[0], r[1]); })
            .catch(function (e) { showLoadError('upcoming', e.message); });
    }

    function renderHorizon(stats, fc) {
        var host = $('horizon');
        var empty = $('upcoming-empty');
        var total = (stats && stats.total) || 0;

        if (!total) {
            if (host) { host.hidden = true; }
            if (empty) { empty.hidden = false; }
            return;
        }
        if (empty) { empty.hidden = true; }
        if (host) { host.hidden = false; }

        renderMaturity(stats);
        renderForecast(fc || {});
        renderCourses(stats);
    }

    /* Over a long horizon the honest headline is not a due count but a maturity
       distribution: how much of what you have met is actually holding. */
    function renderMaturity(stats) {
        var bands = stats.bands || {};
        var total = stats.total || 1;
        var seen = total - (bands['new'] || 0);

        $('known-sub').textContent = seen
            ? seen.toLocaleString() + ' of ' + total.toLocaleString() +
              ' items started · ' + (stats.known_pct || 0) +
              '% of everything is holding for three weeks or more.'
            : total.toLocaleString() + ' items ready, none started yet.';

        var bar = $('maturity-bar');
        bar.innerHTML = BANDS.map(function (b) {
            var n = bands[b[0]] || 0;
            if (!n) { return ''; }
            return '<span class="maturity-seg is-' + b[0] + '" style="flex:' + n +
                   '" title="' + esc(b[1] + ': ' + n) + '"></span>';
        }).join('');

        $('maturity-key').innerHTML = BANDS.map(function (b) {
            var n = bands[b[0]] || 0;
            return '<li class="maturity-key-item' + (n ? '' : ' is-zero') + '">' +
                     '<span class="maturity-dot is-' + b[0] + '" aria-hidden="true"></span>' +
                     '<span class="maturity-name">' + esc(b[1]) + '</span>' +
                     '<span class="maturity-count">' + n.toLocaleString() + '</span>' +
                     '<span class="maturity-why">' + esc(b[2]) + '</span>' +
                   '</li>';
        }).join('');
    }

    /* The curve exists to show that it is FLAT. Load balancing is invisible
       when it works, and a learner with no view of the shape has no reason to
       believe next Tuesday is survivable. */
    function renderForecast(fc) {
        var points = (fc && fc.forecast) || [];
        var host = $('forecast');
        var axis = $('forecast-axis');
        if (!host) { return; }
        if (!points.length) { host.innerHTML = ''; return; }

        var counts = points.map(function (p) { return p.count || 0; });
        var peak = Math.max.apply(null, counts.concat([1]));
        var week = counts.slice(0, 8).reduce(function (a, b) { return a + b; }, 0);
        setCount('stat-week', week);

        var busiest = points[counts.indexOf(peak)];
        /* The curve is scheduled reviews. Unstarted material is a separate,
           deliberately paced stream — folding it in would put the whole bank on
           the first bar and misdescribe both numbers. */
        var pipeline = fc.not_started
            ? ' ' + fc.not_started.toLocaleString() + ' more have not started; ' +
              'Helga introduces about ' + (fc.new_per_day || 12) +
              ' a day and pauses when you fall behind.'
            : '';
        var anchor = points[0].date;
        $('forecast-sub').textContent = (peak
            ? week.toLocaleString() + ' scheduled in the next week · busiest day is ' +
              forecastDay(anchor, busiest.date) + ' with ' + peak + '.'
            : 'Nothing scheduled yet in the next 30 days.') + pipeline;

        host.innerHTML = points.map(function (p, i) {
            var n = p.count || 0;
            var pct = Math.round((n / peak) * 100);
            return '<span class="forecast-col' + (i === 0 ? ' is-today' : '') + '"' +
                     ' title="' + esc(forecastDay(points[0].date, p.date) + ': ' + n) + '">' +
                     '<span class="forecast-fill" style="height:' +
                        Math.max(n ? 4 : 0, pct) + '%"></span>' +
                   '</span>';
        }).join('');

        if (axis) {
            axis.innerHTML = '<span>Today</span><span>+15 days</span><span>+30</span>';
        }
    }

    /* Multiple courses is the normal case, not an edge case: the queue mixes
       them on purpose, so this is where a learner sees each one's own shape. */
    function renderCourses(stats) {
        var host = $('course-rows');
        if (!host) { return; }
        var courses = stats.courses || [];
        if (!courses.length) { host.innerHTML = ''; return; }

        host.innerHTML = courses.map(function (c) {
            var started = c.total - (c['new'] || 0);
            return '<article class="course-row">' +
                     '<div class="course-row-head">' +
                       '<h3 class="course-row-title">' + esc(c.title) + '</h3>' +
                       '<span class="course-row-count">' +
                          c.total.toLocaleString() + ' items</span>' +
                     '</div>' +
                     '<div class="maturity-bar is-slim">' +
                        BANDS.map(function (b) {
                            var n = c[b[0]] || 0;
                            return n ? '<span class="maturity-seg is-' + b[0] +
                                       '" style="flex:' + n + '"></span>' : '';
                        }).join('') +
                     '</div>' +
                     '<p class="course-row-meta">' +
                        (started
                          ? started.toLocaleString() + ' started · ' +
                            c.known_pct + '% holding'
                          : 'not started yet') +
                     '</p>' +
                     '<button type="button" class="btn btn-secondary course-row-action"' +
                       ' data-review-course="' + esc(c.course_uid) + '">' +
                       'Review just this' +
                     '</button>' +
                   '</article>';
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

    /* The forecast is a run of consecutive days starting at the SERVER's today,
       and the server counts days in UTC while the browser counts them locally.
       For several hours each evening those are different dates, and labelling
       each bar against the browser's clock printed "Tomorrow" over the bar the
       axis called "Today". Anchoring to the first point removes the question:
       whatever day zero is for the server, it is today for this chart. */
    function forecastDay(anchorIso, iso) {
        var a = new Date(anchorIso + 'T00:00:00');
        var d = new Date(iso + 'T00:00:00');
        if (isNaN(a) || isNaN(d)) { return formatDay(iso); }
        var diff = Math.round((d - a) / 86400000);
        if (diff === 0) { return 'Today'; }
        if (diff === 1) { return 'Tomorrow'; }
        if (diff < 7) {
            return d.toLocaleDateString(undefined, { weekday: 'long' });
        }
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
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

    /* Delegated, so the handlers survive every re-render of the queue and there
       is exactly one listener per control rather than one per card. */
    function wireReview() {
        document.addEventListener('click', function (e) {
            if (!e.target.closest) { return; }
            if (e.target.closest('#review-start')) { startReview(); return; }
            if (e.target.closest('#review-quit')) { endReview(false); return; }
            if (e.target.closest('#review-mark-btn')) { markAnswer(); return; }
            var g = e.target.closest('.grade-btn');
            if (g) { grade(parseInt(g.dataset.grade, 10)); return; }
            var c = e.target.closest('.choice-btn');
            if (c) { answerChoice(c.dataset.choice === 'true'); return; }
            var only = e.target.closest('[data-review-course]');
            if (only) {
                setScope(only.getAttribute('data-review-course'));
                var sel = $('review-scope');
                if (sel) { sel.value = scope; }
                show('due', true);
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        wireTabs();
        wireQuiz();
        wireReview();
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
