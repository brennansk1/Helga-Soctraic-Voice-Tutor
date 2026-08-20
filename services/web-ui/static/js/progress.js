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
            { value: t.due_today == null ? '—' : t.due_today,
              label: 'due today',
              sub: t.due_today ? 'ready to review' : 'nothing waiting' },
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
        fetch('/api/progress/overview')
            .then(function (r) { return r.json().then(function (b) {
                return { ok: r.ok, body: b }; }); })
            .then(function (res) {
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
