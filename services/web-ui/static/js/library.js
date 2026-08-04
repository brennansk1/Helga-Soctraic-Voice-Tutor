/* Library — find a book, or bring your own.
 *
 * The honest bit is the availability badge. "Available" is THREE different
 * answers — full public-domain text, lending-only, metadata only — and
 * collapsing them is how a learner ends up with a course generated from a
 * 200-word catalogue blurb. Every structural check we run would pass it.
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

    var OK_EXT = ['.epub', '.pdf', '.md', '.markdown', '.txt'];
    var MAX_MB = 50;

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

    function renderResults(list) {
        var host = $('book-results');
        if (!list.length) {
            host.innerHTML = '<p class="library-empty">No books found. Try a ' +
                             'broader title.</p>';
            return;
        }
        host.innerHTML = list.map(function (b) {
            return '<article class="book-card" data-id="' + esc(b.identifier) + '">' +
                     '<div class="book-main">' +
                       '<h3>' + esc(b.title) + '</h3>' +
                       '<p class="book-meta">' + esc(b.author || 'Unknown author') +
                         (b.year ? ' · ' + esc(b.year) : '') + '</p>' +
                     '</div>' +
                     '<div class="book-actions">' +
                       '<span class="book-availability is-checking">Checking…</span>' +
                     '</div>' +
                   '</article>';
        }).join('');
        list.forEach(function (b) { checkAvailability(b.identifier); });
    }

    function checkAvailability(id) {
        var card = document.querySelector('.book-card[data-id="' + id + '"]');
        if (!card) return;
        var slot = card.querySelector('.book-actions');
        fetch('/api/books/availability?identifier=' + encodeURIComponent(id))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.can_build) {
                    slot.innerHTML =
                        '<span class="book-availability is-open">Full text</span>' +
                        '<button class="btn btn-primary btn-small book-build">Build course</button>';
                    slot.querySelector('.book-build')
                        .addEventListener('click', function () { build(id); });
                } else {
                    // Say WHY, and do not offer a button that cannot work.
                    slot.innerHTML =
                        '<span class="book-availability is-restricted">Lending only</span>' +
                        '<span class="book-reason">' + esc(d.reason || '') + '</span>';
                }
            })
            .catch(function () {
                slot.innerHTML = '<span class="book-availability is-unknown">' +
                                 'Could not check</span>';
            });
    }

    function build(identifier) {
        fetch('/api/books/build', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ identifier: identifier })
        }).then(function (r) { return r.json().then(function (d) {
            return { ok: r.ok, d: d }; }); })
          .then(function (res) {
              if (!res.ok) throw new Error(res.d && res.d.error);
              window.location.href = '/build?topic=' +
                  encodeURIComponent(res.d.title || 'your book');
          })
          .catch(function (e) {
              var err = $('library-error');
              err.textContent = (e && e.message) ||
                  'Could not start the build. Try again in a moment.';
              err.hidden = false;
          });
    }

    function wireSearch() {
        var form = $('book-search-form');
        if (!form) return;
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var q = ($('book-q').value || '').trim();
            if (!q) return;
            var host = $('book-results');
            host.innerHTML = '<div class="u-skeleton library-skeleton"></div>' +
                             '<div class="u-skeleton library-skeleton"></div>';
            fetch('/api/books/search?q=' + encodeURIComponent(q))
                .then(function (r) { return r.json(); })
                .then(function (d) { renderResults(d.results || []); })
                .catch(function () {
                    host.innerHTML = '<p class="library-empty">Book search is ' +
                                     'unavailable right now.</p>';
                });
        });
    }

    function wireUpload() {
        var choose = $('library-choose');
        var input = $('library-file');
        var drop = $('library-drop');
        if (!choose || !input) return;

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
            var err = $('library-error');
            err.hidden = true;
            var f = input.files && input.files[0];
            if (!f) return;
            var name = f.name.toLowerCase();
            var ok = OK_EXT.some(function (x) { return name.endsWith(x); });
            if (!ok) {
                err.textContent = 'Helga can read EPUB, PDF, Markdown and text.';
                err.hidden = false;
                return;
            }
            var mb = f.size / (1024 * 1024);
            if (mb > MAX_MB) {
                err.textContent = 'That file is ' + mb.toFixed(1) +
                                  ' MB — the limit is ' + MAX_MB + ' MB.';
                err.hidden = false;
                return;
            }
            var info = $('library-file-info');
            info.textContent = f.name + ' — ' + mb.toFixed(1) + ' MB';
            info.hidden = false;
            $('library-plan').hidden = false;
            $('library-plan-body').innerHTML =
                '<p>Helga will read the document, follow its own chapter ' +
                'structure, and build a course from it.</p>';
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
        xhr.onload = function () {
            if (xhr.status >= 200 && xhr.status < 300) {
                window.location.href = '/build?topic=' +
                    encodeURIComponent(file.name);
            } else {
                var err = $('library-error');
                err.textContent = 'Upload failed (' + xhr.status + ').';
                err.hidden = false;
                btn.disabled = false;
                btn.textContent = 'Build the course';
            }
        };
        xhr.onerror = function () {
            $('library-error').textContent = 'Network error during upload.';
            $('library-error').hidden = false;
            btn.disabled = false;
            btn.textContent = 'Build the course';
        };
        xhr.send(fd);
    }

    document.addEventListener('DOMContentLoaded', function () {
        wireTabs(); wireSearch(); wireUpload();
    });
})();
