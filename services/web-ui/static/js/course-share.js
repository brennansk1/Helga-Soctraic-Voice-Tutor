/**
 * course-share.js — Export a course as a portable bundle, import one back.
 *
 * Injected by DOM manipulation ONLY: courses.html and courses.js are owned by
 * another change and this file must be droppable into the page with a single
 * <script> tag. The grid re-renders its cards from scratch on every
 * loadCourses() (including the 5s building poll), so a one-shot injection
 * would silently vanish on the first refresh — a MutationObserver re-injects
 * export buttons whenever cards appear.
 *
 * Everything is built with createElement/textContent. No course title or
 * server message ever meets innerHTML.
 */
(function () {
    'use strict';

    var MAX_UPLOAD_BYTES = 256 * 1024 * 1024; // mirrors the server cap

    // Named server reasons, translated once for humans. An unknown reason
    // falls through with its name visible rather than a generic shrug —
    // "unsupported_format_version" in a toast is actionable, "error" is not.
    var REASONS = {
        empty_upload: 'No file was attached.',
        bundle_too_large: 'That bundle is over the 256 MB limit.',
        not_a_zip: 'That file is not a course bundle (not a zip).',
        too_many_files: 'Rejected: the bundle contains too many files.',
        path_traversal: 'Rejected: the bundle contains unsafe file paths.',
        zip_bomb: 'Rejected: the bundle looks like a zip bomb.',
        unpacked_too_large: 'Rejected: the bundle unpacks too large.',
        manifest_missing: 'Not a Helga course bundle (no manifest).',
        manifest_invalid: 'Not a Helga course bundle (bad manifest).',
        manifest_mismatch: 'Rejected: the bundle contradicts its own manifest.',
        unsupported_format_version: 'This bundle needs a newer version of Helga.',
        structure_missing: 'Rejected: the bundle has no course structure.',
        structure_invalid: 'Rejected: the course structure is unreadable.',
        db_rows_invalid: 'Rejected: the bundle’s course data is unreadable.',
        unexpected_entry: 'Rejected: the bundle contains unexpected files.',
        course_not_found: 'That course no longer exists.',
        share_service_unreachable: 'Helga’s course service is not answering.',
        import_write_failed: 'Import failed while writing; nothing was kept.',
        import_failed: 'Import failed; nothing was kept.',
        export_failed: 'Export failed.'
    };

    function toast(msg, type) {
        if (window.showToast) { window.showToast(msg, type || 'error'); }
        else if (type === 'error') { console.error('[share] ' + msg); }
    }

    function reasonText(body) {
        var name = body && body.error;
        return REASONS[name] || ('Failed: ' + (name || 'unknown reason'));
    }

    function el(tag, className) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        return node;
    }

    function icon(name) {
        var span = el('span', 'i ' + name);
        span.setAttribute('aria-hidden', 'true');
        return span;
    }

    /* ------------------------------------------------ export, per card */

    function exportCourse(uid, title, btn) {
        btn.disabled = true;
        btn.classList.add('share-busy');
        // fetch → blob rather than a plain navigation: a failed export
        // answers JSON, and a browser navigated to JSON shows the user our
        // internals instead of a toast with the named reason.
        fetch('/api/share/course/' + encodeURIComponent(uid) + '/export')
            .then(function (res) {
                if (!res.ok) {
                    return res.json().catch(function () { return {}; })
                        .then(function (body) { throw new Error(reasonText(body)); });
                }
                return res.blob();
            })
            .then(function (blob) {
                var a = document.createElement('a');
                var url = URL.createObjectURL(blob);
                a.href = url;
                a.download = (title || 'course')
                    .replace(/[^A-Za-z0-9]+/g, '-')
                    .replace(/^-+|-+$/g, '')
                    .toLowerCase() + '-' + uid + '.helga-course.zip';
                document.body.appendChild(a);
                a.click();
                a.remove();
                setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
                toast('Course exported — share the file with anyone running Helga.', 'success');
            })
            .catch(function (e) {
                toast(e && e.message ? e.message : 'Export failed.', 'error');
            })
            .finally(function () {
                btn.disabled = false;
                btn.classList.remove('share-busy');
            });
    }

    function injectExportButtons(grid) {
        var cards = grid.querySelectorAll('.course-card');
        for (var i = 0; i < cards.length; i++) {
            var card = cards[i];
            if (card.querySelector('[data-share-export]')) continue;
            var actions = card.querySelector('.course-card-actions');
            // The uid is not on the card element itself; the view/delete
            // buttons carry it as data-uid, the same way courses.js reads it.
            var carrier = card.querySelector('[data-action][data-uid]');
            if (!actions || !carrier || !carrier.dataset.uid) continue;

            var btn = el('button', 'btn-alpine btn-alpine-ghost course-card-icon-btn');
            btn.type = 'button';
            btn.dataset.shareExport = carrier.dataset.uid;
            btn.dataset.shareTitle = carrier.dataset.title ||
                (card.querySelector('.course-card-title') || {}).textContent || '';
            btn.title = 'Export course';
            btn.setAttribute('aria-label',
                'Export ' + (btn.dataset.shareTitle || 'course') + ' as a shareable file');
            btn.appendChild(icon('i-download'));
            actions.appendChild(btn);
        }
    }

    /* ------------------------------------------------ import, page-level */

    function importBundle(file, btn) {
        if (!/\.zip$/i.test(file.name)) {
            toast('A course bundle is a .zip file (exported from Helga).', 'error');
            return;
        }
        if (file.size > MAX_UPLOAD_BYTES) {
            toast(REASONS.bundle_too_large, 'error');
            return;
        }
        var label = btn.textContent;
        btn.disabled = true;
        btn.classList.add('share-busy');
        btn.textContent = 'Importing…';

        var form = new FormData();
        form.append('bundle', file, file.name);
        fetch('/api/share/course/import', { method: 'POST', body: form })
            .then(function (res) {
                return res.json().catch(function () { return {}; })
                    .then(function (body) { return { ok: res.ok, body: body }; });
            })
            .then(function (r) {
                if (!r.ok || !r.body.ok) { throw new Error(reasonText(r.body)); }
                var msg = 'Imported “' + (r.body.title || 'course') + '”';
                if (r.body.renamed) msg += ' as a new copy';
                if (r.body.warnings && r.body.warnings.length) {
                    msg += ' (' + r.body.warnings.length + ' warning' +
                        (r.body.warnings.length === 1 ? '' : 's') + ' — see console)';
                    console.warn('[share] import warnings:', r.body.warnings);
                }
                toast(msg + '.', 'success');
                if (typeof window.loadCourses === 'function') window.loadCourses();
                else window.location.reload();
            })
            .catch(function (e) {
                toast(e && e.message ? e.message : 'Import failed.', 'error');
            })
            .finally(function () {
                btn.disabled = false;
                btn.classList.remove('share-busy');
                btn.textContent = label;
            });
    }

    function injectImportControl() {
        var bar = document.querySelector('.action-bar .action-buttons');
        if (!bar || bar.querySelector('[data-share-import]')) return;

        var input = document.createElement('input');
        input.type = 'file';
        input.accept = '.zip';
        input.hidden = true;

        var btn = el('button', 'btn-alpine btn-alpine-secondary');
        btn.type = 'button';
        btn.dataset.shareImport = '1';
        btn.appendChild(icon('i-upload'));
        btn.appendChild(document.createTextNode(' Import a course'));
        btn.addEventListener('click', function () { input.click(); });
        input.addEventListener('change', function () {
            if (input.files && input.files[0]) importBundle(input.files[0], btn);
            input.value = ''; // same file twice must fire change twice
        });

        bar.appendChild(btn);
        bar.appendChild(input);
    }

    /* ------------------------------------------------ wiring */

    function init() {
        // The stylesheet rides in with the script, so wiring the feature into
        // a page stays a single <script> tag.
        if (!document.querySelector('link[data-share-css]')) {
            var link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = '/static/css/share.css';
            link.dataset.shareCss = '1';
            document.head.appendChild(link);
        }
        injectImportControl();
        var grid = document.getElementById('courses-grid');
        if (!grid) return;

        // One delegated listener, so re-injected buttons need no rebinding —
        // the same pattern courses.js uses for its own card actions.
        grid.addEventListener('click', function (ev) {
            var btn = ev.target.closest('[data-share-export]');
            if (!btn || !grid.contains(btn)) return;
            exportCourse(btn.dataset.shareExport, btn.dataset.shareTitle, btn);
        });

        injectExportButtons(grid);
        new MutationObserver(function () {
            injectExportButtons(grid);
        }).observe(grid, { childList: true, subtree: true });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
