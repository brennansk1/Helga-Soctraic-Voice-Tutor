/* Ask — free-form questions across everything the learner has studied (A5.2).
 *
 * Deliberately NOT a tab. A question is something you have while you are in the
 * middle of something else; making it a destination means leaving the thing
 * that prompted it. It is a panel available from every page, on a button and
 * on Cmd/Ctrl-K.
 *
 * The product claim is that Helga answers from YOUR material, so the sources
 * are shown with every answer and an ungrounded answer is labelled as such.
 * Hiding that distinction would make this indistinguishable from a chatbot.
 */
(function () {
    'use strict';

    var panel, input, body, form, opener, lastFocus = null;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // The model emits light markdown. Rendering it literally puts **asterisks**
    // in front of the learner; running it through a markdown library would pull
    // in a dependency and an XSS surface for two constructs. Escape first, then
    // promote only bold, italic and line breaks — nothing that can carry an
    // attribute or a URL.
    function fmt(text) {
        return esc(text)
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
            .replace(/`([^`\n]+)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>');
    }

    function courseUidFromUrl() {
        try {
            return new URLSearchParams(window.location.search).get('course_uid');
        } catch (e) { return null; }
    }

    function build() {
        panel = document.createElement('div');
        panel.className = 'ask-panel';
        panel.id = 'ask-panel';
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-modal', 'true');
        panel.setAttribute('aria-label', 'Ask Helga');
        panel.hidden = true;
        panel.innerHTML =
            '<div class="ask-backdrop" data-ask-close></div>' +
            '<section class="ask-sheet">' +
              '<header class="ask-head">' +
                '<h2 class="ask-title">Ask Helga</h2>' +
                '<button type="button" class="ask-close" data-ask-close ' +
                        'aria-label="Close">' +
                  '<span class="i i-x" aria-hidden="true"></span></button>' +
              '</header>' +
              '<div class="ask-body" id="ask-body">' +
                '<p class="ask-hint">Ask anything about what you have been ' +
                'studying. Helga answers from your own courses and shows which ' +
                'concepts it used.</p>' +
              '</div>' +
              '<form class="ask-form" id="ask-form">' +
                '<label class="sr-only" for="ask-input">Your question</label>' +
                '<input id="ask-input" class="form-input ask-input" ' +
                       'autocomplete="off" placeholder="Why does controlling ' +
                       'for a mediator bias the estimate?">' +
                '<button class="btn btn-primary" type="submit">Ask</button>' +
              '</form>' +
            '</section>';
        document.body.appendChild(panel);

        body = panel.querySelector('#ask-body');
        input = panel.querySelector('#ask-input');
        form = panel.querySelector('#ask-form');

        panel.addEventListener('click', function (e) {
            if (e.target.closest('[data-ask-close]')) close();
        });
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            submit();
        });
        // Focus trap: a dialog you can Tab out of is a dialog that loses the
        // keyboard user behind it.
        panel.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { close(); return; }
            if (e.key !== 'Tab') return;
            var f = panel.querySelectorAll('button, input, a[href]');
            if (!f.length) return;
            var first = f[0], last = f[f.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault(); last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault(); first.focus();
            }
        });
    }

    function open() {
        if (!panel) build();
        lastFocus = document.activeElement;
        panel.hidden = false;
        document.body.classList.add('ask-open');
        input.focus();
    }

    function close() {
        if (!panel) return;
        panel.hidden = true;
        document.body.classList.remove('ask-open');
        if (lastFocus && lastFocus.focus) lastFocus.focus();
    }

    function append(html) {
        var el = document.createElement('div');
        el.innerHTML = html;
        body.appendChild(el.firstChild);
        body.scrollTop = body.scrollHeight;
        return body.lastChild;
    }

    function submit() {
        var q = (input.value || '').trim();
        if (!q) return;
        var hint = body.querySelector('.ask-hint');
        if (hint) hint.remove();

        append('<article class="ask-turn ask-turn-you"><p>' + esc(q) + '</p></article>');
        input.value = '';

        var pending = append(
            '<article class="ask-turn ask-turn-helga is-pending">' +
              '<div class="u-skeleton ask-skeleton"></div>' +
            '</article>');

        fetch('/api/ask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: q, course_uid: courseUidFromUrl() })
        })
        .then(function (r) { return r.json().then(function (d) {
            return { ok: r.ok, data: d }; }); })
        .then(function (res) {
            if (!res.ok) throw new Error((res.data && res.data.error) || 'failed');
            var d = res.data;
            var sources = (d.sources || []).map(function (s) {
                return '<a class="ask-source" href="/learn?course_uid=' +
                       encodeURIComponent(s.course_uid || '') + '&concept_uid=' +
                       encodeURIComponent(s.concept_uid || '') + '">' +
                       esc(s.title) + '</a>';
            }).join('');

            pending.classList.remove('is-pending');
            pending.innerHTML =
                '<p class="ask-answer">' + fmt(d.answer) + '</p>' +
                (sources
                  ? '<div class="ask-sources"><span class="ask-sources-label">' +
                    'From your courses</span>' + sources + '</div>'
                  : '<p class="ask-ungrounded">Not found in your courses — ' +
                    'answered from general knowledge.</p>');
        })
        .catch(function (err) {
            pending.classList.remove('is-pending');
            pending.classList.add('is-error');
            pending.innerHTML = '<p>' + esc(
                String(err.message) === 'the tutor took too long to answer'
                    ? 'That took too long to answer. Try a shorter question.'
                    : "Helga could not answer just now. Your question wasn't lost — try again."
            ) + '</p>';
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        opener = document.getElementById('ask-open-btn');
        if (opener) opener.addEventListener('click', open);
        document.addEventListener('keydown', function (e) {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
                e.preventDefault();
                (panel && !panel.hidden) ? close() : open();
            }
        });
    });

    window.HelgaAsk = { open: open, close: close };
})();
