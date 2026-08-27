/* Progress — "what do I actually know?" (A5.2)
 *
 * Rendering rule for this whole file: never invent a number. If a value is
 * null it is shown as "—" with a reason, not as 0. A learner reading 0%
 * accuracy on a concept they have never been asked about would conclude they
 * failed it.
 */
(function () {
    'use strict';

    function $(id) { return document.getElementById(id); }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function pct(v) {
        return v == null ? '—' : Math.round(v * 100) + '%';
    }

    var BLOOM = ['', 'Remember', 'Understand', 'Apply', 'Analyse', 'Evaluate', 'Create'];

    function bloomName(n) {
        return (n && BLOOM[n]) ? BLOOM[n] : '—';
    }

    // Recall strength is the one genuinely predictive number on this page:
    // FSRS retrievability, i.e. the chance you'd get it right if asked now.
    function strengthClass(r) {
        if (r == null) return 'is-unknown';
        if (r >= 0.9) return 'is-strong';
        if (r >= 0.7) return 'is-fading';
        return 'is-weak';
    }

    function renderTotals(t) {
        var host = $('progress-totals');
        if (!host) return;
        var cards = [
            { value: t.known == null ? '—' : t.known,
              label: 'concepts known',
              sub: (t.concepts ? 'of ' + t.concepts : '') },
            { value: pct(t.accuracy),
              label: 'accuracy',
              sub: t.accuracy == null ? 'no answers yet' : 'across all answers' },
            // The number here counted CONCEPTS past their review date while
            // Practice — the page you actually review on — showed its queue,
            // which also holds flashcards. Three surfaces said 40, 6 and 3 at
            // the same moment. The queue is what a learner acts on, so the
            // label now names what it counts and `queueDue` fills it from the
            // same endpoint Practice uses.
            { value: window.__queueDue == null
                        ? (t.due_today == null ? '—' : t.due_today)
                        : window.__queueDue,
              label: 'due today',
              sub: (window.__queueDue || t.due_today) ? 'in your review queue'
                                                      : 'nothing waiting' },
            { value: t.started == null ? '—' : t.started,
              label: 'in progress',
              sub: 'started, not yet mastered' }
        ];
        host.innerHTML = cards.map(function (c) {
            return '<article class="progress-stat">' +
                     '<span class="progress-stat-value">' + esc(c.value) + '</span>' +
                     '<span class="progress-stat-label">' + esc(c.label) + '</span>' +
                     '<span class="progress-stat-sub">' + esc(c.sub) + '</span>' +
                   '</article>';
        }).join('');
    }

    function renderGaps(gaps) {
        var section = $('progress-gaps-section');
        var host = $('progress-gaps');
        if (!host || !section) return;
        if (!gaps.length) { section.hidden = true; return; }
        section.hidden = false;

        host.innerHTML = gaps.map(function (g) {
            var why = [];
            if (g.accuracy != null && g.accuracy < 0.5) why.push(pct(g.accuracy) + ' correct');
            if (g.lapses) why.push(g.lapses + (g.lapses === 1 ? ' lapse' : ' lapses'));
            if (g.retention != null && g.retention < 0.7) why.push('recall ' + pct(g.retention));
            return '<article class="progress-gap">' +
                     '<div class="progress-gap-main">' +
                       '<h3 class="progress-gap-title">' + esc(g.title || g.concept_uid) + '</h3>' +
                       '<p class="progress-gap-why">' + esc(why.join(' · ')) + '</p>' +
                     '</div>' +
                     '<a class="btn btn-secondary progress-gap-action" href="/learn?course_uid=' +
                        encodeURIComponent(g.course_uid || '') +
                        '&concept_uid=' + encodeURIComponent(g.concept_uid || '') +
                        '">Revisit</a>' +
                   '</article>';
        }).join('');
    }

    function renderCourses(courses, concepts) {
        var host = $('progress-courses');
        if (!host) return;

        var byCourse = {};
        concepts.forEach(function (c) {
            (byCourse[c.course_uid] = byCourse[c.course_uid] || []).push(c);
        });

        host.innerHTML = courses.map(function (co) {
            var list = byCourse[co.course_uid] || [];
            // A mastery bar, not a completion bar: three segments so "started
            // but shaky" is visibly different from "known".
            var known = list.filter(function (c) { return c.state === 'known'; }).length;
            var learning = list.filter(function (c) { return c.state === 'learning'; }).length;
            var unseen = list.length - known - learning;
            var w = function (n) { return list.length ? (100 * n / list.length) : 0; };

            return '<article class="progress-course">' +
                     '<header class="progress-course-head">' +
                       '<h3 class="progress-course-title">' + esc(co.title) + '</h3>' +
                       '<span class="progress-course-meta">' +
                          esc(known) + ' / ' + esc(list.length) + ' known' +
                          (co.accuracy != null ? ' · ' + pct(co.accuracy) + ' accuracy' : '') +
                          (co.avg_bloom != null ? ' · avg ' + bloomName(Math.round(co.avg_bloom)) : '') +
                       '</span>' +
                     '</header>' +
                     '<div class="progress-bar" role="img" aria-label="' +
                         esc(known + ' known, ' + learning + ' in progress, ' +
                             unseen + ' not started') + '">' +
                       '<span class="progress-bar-known" style="width:' + w(known) + '%"></span>' +
                       '<span class="progress-bar-learning" style="width:' + w(learning) + '%"></span>' +
                       '<span class="progress-bar-unseen" style="width:' + w(unseen) + '%"></span>' +
                     '</div>' +
                     '<details class="progress-course-detail">' +
                       '<summary>Concept breakdown</summary>' +
                       '<ul class="progress-concepts">' +
                         list.map(renderConcept).join('') +
                       '</ul>' +
                     '</details>' +
                   '</article>';
        }).join('');
    }

    function renderConcept(c) {
        var strength = c.state === 'unseen'
            ? '<span class="progress-concept-unseen">not started</span>'
            : '<span class="progress-strength ' + strengthClass(c.retention) + '">' +
                  (c.retention == null ? 'no recall data' : pct(c.retention) + ' recall') +
              '</span>';
        return '<li class="progress-concept">' +
                 '<span class="progress-concept-title">' + esc(c.title || c.concept_uid) + '</span>' +
                 '<span class="progress-concept-meta">' +
                    (c.bloom_level ? esc(bloomName(c.bloom_level)) + ' · ' : '') +
                    (c.times_reviewed ? esc(c.times_correct + '/' + c.times_reviewed) + ' correct' : '') +
                 '</span>' +
                 strength +
               '</li>';
    }

    function render(data) {
        var totals = data.totals || {};
        var concepts = data.concepts || [];

        if (!concepts.length) {
            var empty = $('progress-empty');
            if (empty) empty.hidden = false;
            var host = $('progress-totals');
            if (host) host.innerHTML = '';
            return;
        }
        renderTotals(totals);
        renderGaps(data.gaps || []);
        renderCourses(data.courses || [], concepts);
    }

    function load() {
        var err = $('progress-error');
        if (err) err.hidden = true;
        // BOTH, THEN RENDER. Firing these side by side left renderTotals
        // reading window.__queueDue before it existed, so the card kept the
        // old number under the new label — worse than either alone, because it
        // now claimed to be the queue and was not.
        Promise.all([
            fetch('/api/due_concepts')
                .then(function (r) { return r.ok ? r.json() : []; })
                .then(function (d) {
                    var items = Array.isArray(d) ? d : (d.concepts || d.due || []);
                    return items.length;
                })
                .catch(function () { return null; }),
            fetch('/api/progress/overview')
                .then(function (r) { return r.json().then(function (b) {
                    return { ok: r.ok, body: b }; }); })
        ])
            .then(function (both) {
                window.__queueDue = both[0];
                var res = both[1];
                if (!res.ok) throw new Error((res.body && res.body.error) ||
                                             'HTTP ' + res.status);
                render(res.body);
            })
            .catch(function (e) {
                var host = $('progress-totals');
                if (host) host.innerHTML = '';
                if (err) err.hidden = false;
                var d = $('progress-error-detail');
                // textContent: the reason can carry a server-supplied string.
                if (d && e && e.message) {
                    d.textContent = e.message +
                        ' — this page shows nothing rather than guessing.';
                }
            });
    }

    document.addEventListener('DOMContentLoaded', function () {
        var retry = $('progress-retry');
        if (retry) retry.addEventListener('click', load);
        load();
    });
})();

/* ---------------------------------------------------------------- activity map
   Days you did something, a year at a time. The point is the pattern rather
   than any single number: consistency is what decides whether spaced repetition
   works over a year, and it is the one part of progress you can read at a
   glance without parsing a figure.

   Weeks are columns and weekdays are rows, which is the arrangement people
   already know how to read from version-control profiles. Colour comes from the
   accent ramp so it inherits the theme; the written summary underneath carries
   the same information for anyone who cannot use the colour at all.
*/
(function () {
    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    function el(id) { return document.getElementById(id); }

    function level(count, peak) {
        if (!count) { return 0; }
        if (peak <= 1) { return 4; }
        /* Four steps over the observed range rather than absolute thresholds:
           a learner doing five reviews a day and one doing fifty should both
           see their own light and heavy days, not a uniform block. */
        var r = count / peak;
        if (r <= 0.25) { return 1; }
        if (r <= 0.5) { return 2; }
        if (r <= 0.75) { return 3; }
        return 4;
    }

    function plural(n, one, many) {
        return n + ' ' + (n === 1 ? one : (many || one + 's'));
    }

    function longest(days) {
        var best = 0, run = 0;
        days.forEach(function (d) {
            run = d.count ? run + 1 : 0;
            if (run > best) { best = run; }
        });
        return best;
    }

    function currentStreak(days) {
        var run = 0;
        for (var i = days.length - 1; i >= 0; i--) {
            /* Today not yet done does not break a streak — the day is not over.
               Any earlier gap does. */
            if (!days[i].count) {
                if (i === days.length - 1) { continue; }
                break;
            }
            run += 1;
        }
        return run;
    }

    function renderHeatmap(data) {
        var section = el('progress-activity-section');
        var grid = el('heatmap');
        var days = (data && data.days) || [];
        if (!section || !grid || !days.length) { return; }

        var peak = days.reduce(function (m, d) { return Math.max(m, d.count || 0); }, 0);

        /* Start the grid on the Monday on or before the first day, so every
           column is a whole week and the weekday rows line up. */
        var first = new Date(days[0].date + 'T00:00:00');
        var lead = (first.getDay() + 6) % 7;          // 0 = Monday
        var cells = [];
        for (var p = 0; p < lead; p++) { cells.push(null); }
        days.forEach(function (d) { cells.push(d); });

        grid.innerHTML = cells.map(function (d) {
            if (!d) { return '<span class="heat is-pad" aria-hidden="true"></span>'; }
            var n = d.count || 0;
            var when = new Date(d.date + 'T00:00:00')
                .toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
            return '<span class="heat is-l' + level(n, peak) + '" title="' +
                   (n ? plural(n, 'review') : 'Nothing') + ' on ' + when + '"></span>';
        }).join('');

        renderMonths(cells);

        var active = data.active_days || 0;
        var streak = currentStreak(days);
        var best = longest(days);
        var summary = active
            ? plural(active, 'active day') + ' in the last year · ' +
              plural(data.total || 0, 'review') + ' · ' +
              (streak ? 'currently ' + plural(streak, 'day') + ' in a row'
                      : 'no run going right now') +
              (best > streak ? ', best run ' + plural(best, 'day') : '')
            : 'No activity recorded yet — review something and this fills in.';

        if (data.recorded_from) {
            var from = new Date(data.recorded_from + 'T00:00:00')
                .toLocaleDateString(undefined, { day: 'numeric', month: 'long' });
            /* Say where the record starts. Squares before Helga began logging
               reviews are empty because nothing was written down, not because
               the learner was idle, and an unqualified year of grey is a
               reproach it has not earned. */
            summary += '. Records begin ' + from + '.';
        }
        el('heatmap-summary').textContent = summary;
        var hint = el('activity-hint');
        if (hint) { hint.textContent = 'the last year'; }
        section.hidden = false;
    }

    /* Each label SPANS its month's columns rather than sitting in the first
       one. A 13px column is narrower than the word in it, so one-label-per-cell
       ran the names into each other — the axis opened with "AugSep". Spanning
       also lets a stub month at either end be dropped, since a label with two
       columns under it has nowhere to sit. */
    var MIN_LABEL_COLUMNS = 3;

    function renderMonths(cells) {
        var host = el('heatmap-months');
        if (!host) { return; }

        var runs = [], columns = Math.ceil(cells.length / 7);
        for (var col = 0; col < columns; col++) {
            var cell = cells[col * 7] || cells[col * 7 + 6];
            if (!cell) { continue; }
            var m = new Date(cell.date + 'T00:00:00').getMonth();
            var last = runs[runs.length - 1];
            if (last && last.month === m) { last.span += 1; }
            else { runs.push({ month: m, start: col + 1, span: 1 }); }
        }

        host.style.gridTemplateColumns = 'repeat(' + columns + ', 13px)';
        host.innerHTML = runs.map(function (r) {
            var text = r.span >= MIN_LABEL_COLUMNS ? MONTHS[r.month] : '';
            return '<span style="grid-column:' + r.start + ' / span ' + r.span +
                   '">' + text + '</span>';
        }).join('');
    }

    function loadHeatmap() {
        fetch('/api/review/activity?days=365')
            .then(function (r) {
                if (!r.ok) { throw new Error('HTTP ' + r.status); }
                return r.json();
            })
            .then(renderHeatmap)
            .catch(function () {
                /* The map is context, not a claim the rest of the page depends
                   on: a failure hides it rather than pushing an error card
                   above the gaps the learner came here to read. */
                var s = el('progress-activity-section');
                if (s) { s.hidden = true; }
            });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', loadHeatmap);
    } else {
        loadHeatmap();
    }
})();
