/* Library — find a book Helga can actually teach from, or bring your own.
 *
 * The honest bit is the availability badge. "Available" is THREE different
 * answers — full public-domain text, lending-only, metadata only — and
 * collapsing them is how a learner ends up with a course generated from a
 * 200-word catalogue blurb. Every structural check we run would pass it. So
 * that distinction is not merely displayed here: it is the primary filter, the
 * dedup tie-breaker, and the thing that decides whether a Build button exists.
 *
 * NOTHING SCRAPED IS EVER innerHTML. Every title, author, snippet and excerpt
 * on this page is a third-party string from an archive we do not control, and
 * several of them (wiki search snippets in particular) arrive containing
 * markup. They go in through textContent, without exception.
 */
(function () {
    'use strict';

    function $(id) { return document.getElementById(id); }

    /* Small DOM builder. It exists so that "put this scraped string on screen"
     * has exactly one spelling in this file, and that spelling is textContent. */
    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) { n.className = cls; }
        if (text !== undefined && text !== null && text !== '') {
            n.textContent = String(text);
        }
        return n;
    }

    var OK_EXT = ['.epub', '.pdf', '.md', '.markdown', '.txt'];
    var MAX_MB = 50;

    var AVAILABILITY = {
        full_text:  { label: 'Full text',      cls: 'is-open' },
        restricted: { label: 'Lending only',   cls: 'is-restricted' },
        metadata:   { label: 'Catalogue only', cls: 'is-metadata' },
        unknown:    { label: 'Checking…',      cls: 'is-checking' }
    };

    /* Search state lives here rather than being re-read from the DOM, so that
     * "load more" appends the NEXT page of the same query instead of
     * re-deriving what the previous page happened to ask for. */
    var state = {
        query: '',
        page: 1,
        availability: 'full_text',
        sources: null,          // null = every source the server offers
        openOnly: false,
        yearMin: '',
        yearMax: '',
        sort: 'relevance',
        seen: {},               // source:id already on screen, for load-more
        busy: false
    };

    // ---------------------------------------------------------------- tabs

    function wireTabs() {
        document.querySelectorAll('.library-tab').forEach(function (tab) {
            tab.addEventListener('click', function () {
                document.querySelectorAll('.library-tab').forEach(function (t) {
                    t.classList.toggle('is-active', t === tab);
                    t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
                });
                document.querySelectorAll('.library-panel').forEach(function (p) {
                    p.hidden = (p.id !== tab.dataset.panel);
                });
            });
        });
    }

    // ------------------------------------------------------------- filters

    function wireFilters() {
        // Sources are fetched rather than hardcoded: the server decides which
        // adapters exist, and a chip for an adapter that is not wired up is a
        // filter that silently returns nothing.
        fetch('/api/library/sources')
            .then(function (r) { return r.json(); })
            .then(function (d) { buildSourceChips(d.sources || []); })
            .catch(function () { /* chips stay empty; search still works */ });

        $('lib-avail-chips').addEventListener('click', function (e) {
            var chip = e.target.closest('.lib-chip');
            if (!chip) { return; }
            state.availability = chip.dataset.avail;
            this.querySelectorAll('.lib-chip').forEach(function (c) {
                c.classList.toggle('is-active', c === chip);
            });
            rerun();
        });

        $('lib-open-only').addEventListener('change', function () {
            state.openOnly = this.checked; rerun();
        });
        $('lib-sort').addEventListener('change', function () {
            state.sort = this.value; rerun();
        });
        ['lib-year-min', 'lib-year-max'].forEach(function (id) {
            $(id).addEventListener('change', function () {
                state.yearMin = $('lib-year-min').value.trim();
                state.yearMax = $('lib-year-max').value.trim();
                rerun();
            });
        });
        $('lib-more-btn').addEventListener('click', function () {
            state.page += 1;
            runSearch(true);
        });
    }

    function buildSourceChips(sources) {
        var host = $('lib-source-chips');
        host.textContent = '';
        sources.forEach(function (s) {
            var chip = el('button', 'lib-chip is-active', s.label);
            chip.type = 'button';
            chip.dataset.source = s.id;
            chip.setAttribute('aria-pressed', 'true');
            chip.addEventListener('click', function () {
                var on = chip.classList.toggle('is-active');
                chip.setAttribute('aria-pressed', on ? 'true' : 'false');
                var picked = Array.prototype.map.call(
                    host.querySelectorAll('.lib-chip.is-active'),
                    function (c) { return c.dataset.source; });
                // All-on is the same query as none-specified, and pinning the
                // full list would stop the server from gaining a source later
                // without the client also learning about it.
                state.sources = (picked.length === sources.length) ? null : picked;
                rerun();
            });
            host.appendChild(chip);
        });
    }

    function rerun() {
        if (!state.query) { return; }
        state.page = 1;
        runSearch(false);
    }

    // -------------------------------------------------------------- search

    function buildUrl() {
        var p = new URLSearchParams();
        p.set('q', state.query);
        p.set('page', String(state.page));
        p.set('availability', state.availability);
        p.set('sort', state.sort);
        if (state.openOnly) { p.set('open_only', '1'); }
        if (state.yearMin) { p.set('year_min', state.yearMin); }
        if (state.yearMax) { p.set('year_max', state.yearMax); }
        if (state.sources && state.sources.length) {
            p.set('sources', state.sources.join(','));
        }
        return '/api/library/search?' + p.toString();
    }

    function runSearch(append) {
        if (state.busy) { return; }
        state.busy = true;
        var host = $('book-results');
        var more = $('lib-more-btn');

        if (append) {
            more.disabled = true;
            more.textContent = 'Loading…';
        } else {
            state.seen = {};
            host.textContent = '';
            for (var i = 0; i < 6; i++) {
                host.appendChild(el('div', 'lib-card lib-card--skeleton'));
            }
            $('lib-more').hidden = true;
        }

        fetch(buildUrl())
            .then(function (r) {
                return r.json().then(function (d) { return { ok: r.ok, d: d }; });
            })
            .then(function (res) {
                var d = res.d || {};
                renderSourceBar(d.sources || [], d);
                if (!res.ok || d.all_sources_failed) {
                    /* NOT the same as "no results". Every source being down is
                     * a machine problem the user can wait out; an empty archive
                     * is a query problem they can rephrase. Different words,
                     * because they call for different next actions. */
                    if (!append) {
                        showState(host, 'Nothing could be reached',
                            (d.error || 'None of the book sources answered.') +
                            ' Helga does not need the network once a course is ' +
                            'built, but it does need it to find a book.');
                    }
                    return;
                }
                renderResults(d.results || [], append);
                $('lib-more').hidden = !d.has_more;
                $('lib-filters').hidden = false;
            })
            .catch(function (e) {
                if (!append) {
                    showState(host, 'Search failed',
                        'The library service did not respond' +
                        (e && e.message ? ' (' + e.message + ').' : '.'));
                }
            })
            .then(function () {
                state.busy = false;
                more.disabled = false;
                more.textContent = 'Load more books';
            });
    }

    function showState(host, title, body) {
        host.textContent = '';
        var box = el('div', 'lib-empty');
        box.appendChild(el('h3', 'lib-empty-title', title));
        box.appendChild(el('p', 'lib-empty-body', body));
        host.appendChild(box);
    }

    /* Per-source accounting, visible for every search.
     *
     * If Gutenberg is down the user should be told that, not silently handed
     * fewer books and left to conclude Gutenberg has nothing on the subject.
     * That exact confusion — a throttled or broken source reading as "there is
     * nothing here" — is the failure ratelimit.py was written to stop. */
    function renderSourceBar(sources, d) {
        var bar = $('lib-sourcebar');
        bar.textContent = '';
        if (!sources.length) { bar.hidden = true; return; }
        bar.hidden = false;

        sources.forEach(function (s) {
            var chip = el('span', 'lib-src ' + (s.ok ? 'is-ok' : 'is-down'));
            chip.appendChild(el('span', 'lib-src-name', s.label));
            if (s.ok) {
                chip.appendChild(el('span', 'lib-src-count',
                    s.total ? s.count + ' of ' + s.total.toLocaleString()
                            : 'nothing found'));
            } else {
                chip.appendChild(el('span', 'lib-src-count',
                    s.error || 'unavailable'));
                chip.title = s.error || 'unavailable';
            }
            bar.appendChild(chip);
        });

        if (d && d.merged_from) {
            var shown = (d.results || []).length;
            if (d.merged_from > shown) {
                bar.appendChild(el('span', 'lib-src-merged',
                    d.merged_from + ' records → ' + shown + ' distinct works'));
            }
        }
    }

    function renderResults(list, append) {
        var host = $('book-results');
        if (!append) { host.textContent = ''; }

        var fresh = list.filter(function (b) {
            var k = b.source + ':' + b.id;
            if (state.seen[k]) { return false; }
            state.seen[k] = true;
            return true;
        });

        if (!fresh.length && !append) {
            showState(host, 'No books matched',
                'Every source answered, and none of them has a match for ' +
                '“' + state.query + '”. Try a broader title, or widen the ' +
                'filters — “Full text only” hides books Helga cannot read.');
            return;
        }
        fresh.forEach(function (b) { host.appendChild(card(b)); });
    }

    // ---------------------------------------------------------------- card

    function card(b) {
        var art = el('article', 'lib-card');
        art.dataset.source = b.source;
        art.dataset.id = b.id;

        var img = document.createElement('img');
        img.className = 'lib-cover';
        img.loading = 'lazy';
        /* Served through our own endpoint, never hotlinked. The search is
         * already server-side; letting the browser fetch cover art directly
         * would newly hand archive.org the learner's IP address and a list of
         * what they are reading. The endpoint also substitutes a blank rather
         * than ever resolving to a broken image, so there is no onerror path
         * to write here. */
        img.src = '/api/library/cover?source=' + encodeURIComponent(b.source) +
                  '&id=' + encodeURIComponent(b.id);
        img.alt = '';                    // decorative: the title is right there
        art.appendChild(img);

        var body = el('div', 'lib-card-body');
        body.appendChild(el('h3', 'lib-card-title', b.title));
        body.appendChild(el('p', 'lib-card-meta',
            (b.author || 'Unknown author') + (b.year ? ' · ' + b.year : '')));

        var tags = el('div', 'lib-card-tags');
        tags.appendChild(el('span', 'lib-source-tag', b.source_label));
        if (b.open_license) {
            var lic = el('span', 'lib-lic-tag', 'Open licence');
            if (b.license) { lic.title = b.license; }
            tags.appendChild(lic);
        }
        body.appendChild(tags);

        if (b.description) {
            body.appendChild(el('p', 'lib-card-desc', b.description));
        }
        var also = alsoText(b);
        if (also) {
            // Saying where else the work lives is the honest form of
            // deduplication: we collapsed the rows, we did not hide them.
            body.appendChild(el('p', 'lib-card-also', also));
        }

        var actions = el('div', 'lib-card-actions');
        actions.appendChild(badge(b.availability, b.availability_reason));

        var details = el('button', 'lib-detail-btn', 'Details');
        details.type = 'button';
        details.addEventListener('click', function () { openDetail(b); });
        actions.appendChild(details);

        if (b.availability === 'full_text') {
            actions.appendChild(buildButton(b));
        }
        body.appendChild(actions);
        art.appendChild(body);

        /* Only the Archive costs a live call to answer this, and only the
         * Archive comes back `unknown`. Every other source decided at search
         * time from data it already had in hand. */
        if (b.availability === 'unknown') { resolveAvailability(art, b); }
        return art;
    }

    /* Two different kinds of duplicate got collapsed into one card, and they
     * need different words. Another SOURCE holding the same work is useful
     * news — it is where to read it if this copy disappoints. Another scan on
     * the SAME source is not news at all, it is the Archive holding twelve
     * photographs of one book, and phrasing that as "also on Internet
     * Archive" on an Internet Archive card reads as a mistake. */
    function alsoText(b) {
        var list = b.also_in || [];
        var elsewhere = [], here = 0, seen = {};
        list.forEach(function (a) {
            if (a.source === b.source) { here += 1; return; }
            if (!seen[a.source_label]) {
                seen[a.source_label] = true;
                elsewhere.push(a.source_label);
            }
        });
        var parts = [];
        if (elsewhere.length) { parts.push('Also on ' + elsewhere.join(', ')); }
        if (here) {
            parts.push(here + ' other cop' + (here === 1 ? 'y' : 'ies') +
                       ' on ' + b.source_label);
        }
        return parts.join(' · ');
    }

    function badge(availability, reason) {
        var meta = AVAILABILITY[availability] || AVAILABILITY.unknown;
        var s = el('span', 'book-availability ' + meta.cls, meta.label);
        if (reason) { s.title = reason; }
        return s;
    }

    function buildButton(b) {
        var btn = el('button', 'lib-build-btn', 'Build course');
        btn.type = 'button';
        /* The builder's contract takes an Internet Archive identifier. Offering
         * the button on a source it cannot accept would be the same broken
         * promise as offering it on a lending-only book — so it is disabled and
         * says where the book can be read instead. */
        if (b.source !== 'internet_archive') {
            /* A DISABLED BUTTON EXPLAINS ITSELF ONLY ON HOVER, and never on a
               touch screen. Every non-Internet-Archive result on a search
               therefore showed a row of dead "Build course" buttons whose
               reason was invisible — measured on "SQL", where the best match
               by far is a Wikibooks text and every one of its buttons was
               dead. Say what this source CAN do instead: the label carries the
               limitation, and the action still goes somewhere useful. */
            var read = el('a', 'lib-build-btn is-alt', 'Read at ' + b.source_label);
            read.href = b.url || '#';
            read.target = '_blank';
            read.rel = 'noopener noreferrer';
            read.title = 'Helga builds courses from Internet Archive scans ' +
                         'today, so it cannot build from this one. It is ' +
                         'readable at ' + b.source_label + '.';
            return read;
        }
        btn.addEventListener('click', function () { build(b.id); });
        return btn;
    }

    function resolveAvailability(cardEl, b) {
        fetch('/api/library/availability?source=' +
              encodeURIComponent(b.source) + '&id=' + encodeURIComponent(b.id))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                b.availability = d.availability || 'unknown';
                b.availability_reason = d.reason || '';
                var actions = cardEl.querySelector('.lib-card-actions');
                var old = actions.querySelector('.book-availability');
                actions.replaceChild(
                    badge(b.availability, b.availability_reason), old);
                if (b.availability === 'full_text') {
                    actions.appendChild(buildButton(b));
                } else if (b.availability_reason) {
                    // Say WHY, and do not offer a button that cannot work.
                    cardEl.querySelector('.lib-card-body').appendChild(
                        el('p', 'lib-card-reason', b.availability_reason));
                }
                if (state.availability === 'full_text' &&
                        b.availability !== 'full_text') {
                    /* The filter said "only what Helga can read", and this one
                     * has just turned out not to be. Honour the filter rather
                     * than leaving behind a card the user excluded. */
                    cardEl.classList.add('is-filtered-out');
                }
            })
            .catch(function () {
                var actions = cardEl.querySelector('.lib-card-actions');
                var old = actions.querySelector('.book-availability');
                if (!old) { return; }
                var b2 = badge('unknown', 'Could not reach the archive to check.');
                b2.textContent = 'Could not check';
                actions.replaceChild(b2, old);
            });
    }

    // -------------------------------------------------------------- detail

    function openDetail(b) {
        var panel = $('lib-detail');
        var body = $('lib-detail-body');
        body.textContent = '';
        body.appendChild(el('p', 'lib-detail-loading', 'Loading details…'));
        panel.hidden = false;
        panel.focus();
        document.body.classList.add('lib-detail-open');

        fetch('/api/library/detail?source=' + encodeURIComponent(b.source) +
              '&id=' + encodeURIComponent(b.id))
            .then(function (r) {
                return r.json().then(function (d) { return { ok: r.ok, d: d }; });
            })
            .then(function (res) {
                if (!res.ok) { throw new Error(res.d && res.d.error); }
                renderDetail(res.d);
            })
            .catch(function (e) {
                body.textContent = '';
                body.appendChild(el('h2', 'lib-detail-title', b.title));
                body.appendChild(el('p', 'lib-detail-error',
                    (e && e.message) ||
                    'Those details could not be fetched just now.'));
            });
    }

    function renderDetail(d) {
        var body = $('lib-detail-body');
        body.textContent = '';

        var head = el('div', 'lib-detail-head');
        var img = document.createElement('img');
        img.className = 'lib-detail-cover';
        img.src = '/api/library/cover?source=' + encodeURIComponent(d.source) +
                  '&id=' + encodeURIComponent(d.id);
        img.alt = '';
        head.appendChild(img);

        var hmeta = el('div', 'lib-detail-meta');
        hmeta.appendChild(el('h2', 'lib-detail-title', d.title));
        hmeta.appendChild(el('p', 'lib-card-meta',
            (d.author || 'Unknown author') + (d.year ? ' · ' + d.year : '')));
        hmeta.appendChild(badge(d.availability, ''));
        if (d.availability_reason) {
            hmeta.appendChild(el('p', 'lib-detail-reason', d.availability_reason));
        }
        head.appendChild(hmeta);
        body.appendChild(head);

        if (d.description) {
            body.appendChild(el('h3', 'lib-detail-h', 'About'));
            body.appendChild(el('p', 'lib-detail-desc', d.description));
        }

        var facts = [
            ['Licence', d.license],
            ['Formats', (d.formats || []).join(', ')],
            ['Pages', d.pages],
            ['Downloads', d.downloads],
            ['Subjects', (d.subjects || []).join(', ')]
        ].filter(function (f) { return f[1]; });
        if (facts.length) {
            var dl = el('dl', 'lib-facts');
            facts.forEach(function (f) {
                dl.appendChild(el('dt', 'lib-fact-k', f[0]));
                dl.appendChild(el('dd', 'lib-fact-v', String(f[1])));
            });
            body.appendChild(dl);
        }

        /* The excerpt is the reason this panel exists. A scan can be catalogued
         * immaculately and still be OCR mush, and no metadata field says so —
         * reading the opening is the only cheap test, and this is the very text
         * the builder would be handed. */
        body.appendChild(el('h3', 'lib-detail-h', 'The text Helga would read'));
        if (d.excerpt) {
            body.appendChild(el('pre', 'lib-excerpt', d.excerpt));
        }
        if (d.excerpt_note) {
            body.appendChild(el('p', 'lib-detail-note', d.excerpt_note));
        }

        var foot = el('div', 'lib-detail-actions');
        if (d.url) {
            var a = el('a', 'lib-detail-link',
                'Open at ' + (d.source_label || 'the source'));
            a.href = d.url;
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            foot.appendChild(a);
        }
        if (d.can_build && d.source === 'internet_archive') {
            var btn = el('button', 'lib-build-btn', 'Build course from this');
            btn.type = 'button';
            btn.addEventListener('click', function () { build(d.id); });
            foot.appendChild(btn);
        }
        body.appendChild(foot);
    }

    function closeDetail() {
        $('lib-detail').hidden = true;
        document.body.classList.remove('lib-detail-open');
    }

    // --------------------------------------------------------------- build

    function build(identifier) {
        // Unchanged contract: /api/books/build takes an Archive identifier.
        fetch('/api/books/build', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': window.CSRF_TOKEN || ''
            },
            body: JSON.stringify({ identifier: identifier })
        }).then(function (r) {
            return r.json().then(function (d) { return { ok: r.ok, d: d }; });
        }).then(function (res) {
            if (!res.ok) { throw new Error(res.d && res.d.error); }
            window.location.href = '/build?topic=' +
                encodeURIComponent(res.d.title || 'your book');
        }).catch(function (e) {
            fail((e && e.message) ||
                 'Could not start the build. Try again in a moment.');
        });
    }

    function fail(msg) {
        var err = $('library-error');
        err.textContent = msg;
        err.hidden = false;
        err.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    // -------------------------------------------------------- search wiring

    function wireSearch() {
        var form = $('book-search-form');
        if (!form) { return; }
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var q = ($('book-q').value || '').trim();
            if (!q) { return; }
            $('library-error').hidden = true;
            state.query = q;
            state.page = 1;
            runSearch(false);
        });
    }

    // -------------------------------------------------------------- upload

    function wireUpload() {
        var choose = $('library-choose');
        var input = $('library-file');
        var drop = $('library-drop');
        if (!choose || !input) { return; }

        choose.addEventListener('click', function () { input.click(); });

        ['dragover', 'dragenter'].forEach(function (ev) {
            drop.addEventListener(ev, function (e) {
                e.preventDefault(); drop.classList.add('is-over');
            });
        });
        ['dragleave', 'drop'].forEach(function (ev) {
            drop.addEventListener(ev, function (e) {
                e.preventDefault(); drop.classList.remove('is-over');
            });
        });
        drop.addEventListener('drop', function (e) {
            if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                input.files = e.dataTransfer.files;
                onFile();
            }
        });
        input.addEventListener('change', onFile);

        function onFile() {
            $('library-error').hidden = true;
            var f = input.files && input.files[0];
            if (!f) { return; }
            var name = f.name.toLowerCase();
            var ok = OK_EXT.some(function (x) { return name.endsWith(x); });
            if (!ok) {
                fail('Helga can read EPUB, PDF, Markdown and text.');
                return;
            }
            var mb = f.size / (1024 * 1024);
            if (mb > MAX_MB) {
                fail('That file is ' + mb.toFixed(1) + ' MB — the limit is ' +
                     MAX_MB + ' MB.');
                return;
            }
            var info = $('library-file-info');
            info.textContent = f.name + ' — ' + mb.toFixed(1) + ' MB';
            info.hidden = false;

            var pbody = $('library-plan-body');
            pbody.textContent = '';
            pbody.appendChild(el('p', null,
                'Helga will read the document, follow its own chapter ' +
                'structure, and build a course from it. The file never leaves ' +
                'this machine.'));
            $('library-plan').hidden = false;
            $('library-build').disabled = false;
            $('library-build').onclick = function () { upload(f); };
        }
    }

    function upload(file) {
        var btn = $('library-build');
        btn.disabled = true;
        btn.textContent = 'Uploading…';
        var fd = new FormData();
        fd.append('file', file);
        var xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload_epub');
        xhr.setRequestHeader('X-CSRF-Token', window.CSRF_TOKEN || '');
        xhr.upload.addEventListener('progress', function (e) {
            if (e.lengthComputable) {
                btn.textContent = 'Uploading… ' +
                    Math.round((e.loaded / e.total) * 100) + '%';
            }
        });
        xhr.onload = function () {
            if (xhr.status >= 200 && xhr.status < 300) {
                window.location.href = '/build?topic=' +
                    encodeURIComponent(file.name);
            } else {
                fail('Upload failed (' + xhr.status + ').');
                btn.disabled = false;
                btn.textContent = 'Build the course';
            }
        };
        xhr.onerror = function () {
            fail('Network error during upload.');
            btn.disabled = false;
            btn.textContent = 'Build the course';
        };
        xhr.send(fd);
    }

    document.addEventListener('DOMContentLoaded', function () {
        wireTabs(); wireSearch(); wireFilters(); wireUpload();
        $('lib-detail-close').addEventListener('click', closeDetail);
        $('lib-detail').addEventListener('click', function (e) {
            if (e.target === this) { closeDetail(); }
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !$('lib-detail').hidden) { closeDetail(); }
        });
    });
})();
