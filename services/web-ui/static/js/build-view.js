/* Course-build visualisation.
 *
 * A build takes tens of minutes on this hardware and used to show a spinner and
 * one line of free text. This turns the status stream the builder ALREADY emits
 * into something a learner can read: which stage is running, what evidence was
 * found, what structure is emerging, and how the coverage check scored it.
 *
 * The point is not reassurance. Showing the research and the coverage verdict
 * is the evidence that the course was built from something — which is the
 * product's entire claim over a chatbot.
 */
(function () {
    'use strict';

    var started = Date.now();
    var stageEls = {};
    var modules = [];          // [{title, children:[...]}]
    var conceptCount = 0;

    function $(id) { return document.getElementById(id); }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // --- stages -------------------------------------------------------------

    var ORDER = ['preflight', 'research', 'skeleton', 'coverage', 'hydrate', 'assets'];

    function setStage(name, state) {
        var el = stageEls[name];
        if (!el) return;
        el.classList.remove('is-active', 'is-done', 'is-warn');
        if (state) el.classList.add('is-' + state);
        // Everything before the active stage is finished by definition.
        if (state === 'active') {
            var i = ORDER.indexOf(name);
            ORDER.slice(0, i).forEach(function (prev) {
                var p = stageEls[prev];
                if (p && !p.classList.contains('is-warn')) {
                    p.classList.remove('is-active');
                    p.classList.add('is-done');
                }
            });
        }
    }

    // --- panels -------------------------------------------------------------

    function addEvidence(html) {
        var host = $('build-evidence');
        var empty = host.querySelector('.build-empty');
        if (empty) empty.remove();
        var div = document.createElement('div');
        div.className = 'build-evidence-item u-animate-rise';
        div.innerHTML = html;
        host.appendChild(div);
    }

    function renderTree() {
        var host = $('build-tree');
        if (!modules.length) return;
        var empty = host.querySelector('.build-empty');
        if (empty) empty.remove();
        host.innerHTML = modules.map(function (m, i) {
            return '<div class="build-module u-animate-rise">' +
                     '<span class="build-module-index">' + (i + 1) + '</span>' +
                     '<div><strong>' + esc(m.title) + '</strong>' +
                       (m.children.length
                         ? '<ul class="build-module-children">' +
                             m.children.slice(-6).map(function (c) {
                                 return '<li>' + esc(c) + '</li>'; }).join('') +
                           '</ul>'
                         : '') +
                     '</div>' +
                   '</div>';
        }).join('');
        var n = $('build-count');
        if (n) {
            n.textContent = modules.length + ' modules' +
                (conceptCount ? ' · ' + conceptCount + ' concepts' : '');
        }
        // Structure growth is the one honest progress signal available: the
        // total is not known in advance, so a percentage bar would be a lie.
        var bar = $('build-growth');
        if (bar) bar.style.setProperty('--grown', Math.min(100, conceptCount * 3) + '%');
    }

    // The builder's status vocabulary is developer output: "STRUCT:MODULE:x",
    // "CHECK:SYLLABUS:ADEQUATE:78%", "LOG: Calling LLM for module structure
    // (attempt 1/3)...". Showing that to a learner is the same mistake as the
    // "Server returned 502: BAD GATEWAY" toast — it is our internals, and it
    // reads as something being wrong. Translate to what actually happened.
    var HUMAN = [
        [/^RESEARCH:LEVEL:(.+)/,          function (m) {
            return 'Targeting ' + m[1] + ' level material'; }],
        [/^RESEARCH:SYLLABUS:([^|]+)\|([^|]+)\|(\d+)/, function (m) {
            return 'Found syllabus — ' + m[1] + ': "' + m[2] + '" (' + m[3] + ' chapters)'; }],
        [/^RESEARCH:COURSE:([^|]+)\|([^|]+)\|(\d+)/, function (m) {
            return 'Found course — ' + m[1] + ': "' + m[2] + '" (' + m[3] + ' sections)'; }],
        [/^RESEARCH:BOOK:(.+)/,           function (m) {
            return 'Found book — ' + m[1]; }],
        [/^HYDRATE:SOURCES:([^|]+)\|([^|]+)\|([\d.]+)/, function (m) {
            return 'Wrote "' + m[1] + '" — grounded in ' + m[2] +
                   ' (confidence ' + m[3] + ')'; }],
        [/^STRUCT:RESEARCH_RETRY:(.+)/,   function (m) {
            return 'Thin sourcing for "' + m[1] + '" — searching wider'; }],
        [/^CHECK:PREFLIGHT:PASS/,        function () { return 'Model is reachable'; }],
        [/^CHECK:PREFLIGHT:FAIL/,        function () { return 'Could not reach the model'; }],
        [/^CHECK:SYLLABUS_EVIDENCE:NONE/, function () {
            return 'No open textbook found for this subject — building without that evidence'; }],
        [/^CHECK:SYLLABUS_EVIDENCE:(.+)/, function (m) {
            return 'Found ' + m[1] + ' of real syllabus material'; }],
        [/^CHECK:SYLLABUS:SKIP/,         function () { return 'Coverage could not be measured'; }],
        [/^CHECK:SYLLABUS:(\w+):(.*)/,   function (m) {
            return 'Coverage check: ' + m[1].toLowerCase() + (m[2] ? ' (' + m[2] + ')' : ''); }],
        [/^CHECK:HYDRATION:WARN/,        function () {
            return 'Some concepts could not be fully written'; }],
        [/^ASSET:PHASE:START/,           function () {
            return 'Gathering assets — drawing the diagrams this course will teach with'; }],
        [/^ASSET:START:(\d+)/,           function (m) {
            return 'Planning visuals for ' + m[1] + ' concepts'; }],
        [/^ASSET:PROGRESS:\d+:(.+)/,     function (m) { return '   ' + m[1]; }],
        [/^ASSET:BUDGET:(\d+)/,          function (m) {
            return 'Asset budget reached (' + m[1] + ') — remaining concepts will draw live'; }],
        [/^ASSET:SKIPPED:(.+)/,          function (m) {
            return 'Asset gathering skipped (' + m[1] + ')'; }],
        [/^ASSET:ERROR:(.+)/,            function (m) {
            return 'Asset gathering had trouble: ' + m[1]; }],
        [/^ASSET:DONE:(\d+):(\d+):(\d+):([\d.]+)/, function (m) {
            return 'Assets ready — ' + m[1] + ' diagram(s), ' + m[2] + ' image(s), ' +
                   m[3] + ' concept(s) needed none (' + m[4] + 's)'; }],
        [/^ASSETS:READY:(\d+):(\d+):(\d+)/, function (m) {
            return 'Loaded ' + m[1] + ' concept(s) of pre-built visuals'; }],
        [/^STRUCT:MODULE:(.+)/,          function (m) { return 'Module: ' + m[1]; }],
        [/^STRUCT:\w+:(.+)/,             function (m) { return '   ' + m[1]; }],
        [/^LOG: Generating (\d+) course modules for '(.+)'/, function (m) {
            return 'Planning ' + m[1] + ' modules for ' + m[2]; }],
        [/^LOG: Generating Units for module: (.+)/, function (m) {
            return 'Breaking down ' + m[1]; }],
        [/^LOG: Generating lessons for unit: (.+)/, function (m) {
            return 'Writing lessons for ' + m[1]; }],
        [/^LOG: Generating concepts for lesson: (.+)/, function (m) {
            return 'Writing concepts for ' + m[1]; }],
        [/^LOG: Building sub-structures for (\d+) modules/, function (m) {
            return 'Expanding ' + m[1] + ' modules'; }],
        [/^LOG: Progressive Skeleton generated\. Found (\d+) modules/, function (m) {
            return 'Structure complete — ' + m[1] + ' modules'; }],
        [/^LOG: Retrying module generation \(attempt (\d+)/, function (m) {
            return 'Retrying the structure (attempt ' + m[1] + ')'; }],
        [/^LOG: Calling LLM for module structure/, function () {
            return 'Asking the model for the course structure'; }],
        [/^LOG: Audit (renamed|expanded|deleted)/, function (m) {
            return 'Tidying titles (' + m[1] + ')'; }],
        [/^LOG: Preflight checks completed/, function () { return 'Ready to build'; }],
        [/^ERROR:\s*(.*)/,               function (m) { return m[1] || 'Something went wrong'; }],
        [/^LOG:\s*(.*)/,                 function (m) { return m[1]; }],
    ];

    function humanise(msg) {
        for (var i = 0; i < HUMAN.length; i++) {
            var m = msg.match(HUMAN[i][0]);
            if (m) return HUMAN[i][1](m);
        }
        return msg;
    }

    function log(text, kind) {
        var ul = $('build-log');
        var human = humanise(text);
        if (!human) return;
        var li = document.createElement('li');
        li.className = 'build-log-line' + (kind ? ' is-' + kind : '');
        if (/^ {3}/.test(human)) li.classList.add('is-nested');
        var t = new Date();
        li.innerHTML = '<span class="build-log-time">' +
            String(t.getMinutes()).padStart(2, '0') + ':' +
            String(t.getSeconds()).padStart(2, '0') + '</span>' +
            '<span class="build-log-text">' + esc(human.trim()) + '</span>';
        ul.appendChild(li);
        while (ul.children.length > 200) ul.removeChild(ul.firstChild);
        ul.scrollTop = ul.scrollHeight;
    }

    // --- message interpretation --------------------------------------------
    //
    // The builder emits a structured prefix vocabulary (CHECK:, STRUCT:, LOG:).
    // Parsing the STRUCTURE rather than matching free text is deliberate: the
    // previous progress UI matched substrings like "hydrat" and broke whenever
    // a message was reworded.

    function handle(raw) {
        var msg = String(raw == null ? '' : raw).trim();
        if (!msg) return;
        log(msg);

        // Phase 3 — asset collection. The course is not enterable until this
        // finishes, so the stage has to be visible or the last minutes of a
        // build look like a hang.
        if (msg.indexOf('ASSET:PHASE:START') === 0 || msg.indexOf('ASSET:START:') === 0) {
            setStage('assets', 'active');
        }
        if (msg.indexOf('ASSET:DONE:') === 0) {
            setStage('assets', 'done');
        }
        if (msg.indexOf('ASSET:ERROR:') === 0 || msg.indexOf('ASSET:SKIPPED:') === 0) {
            // Degradable by design: no pictures is not a failed build.
            setStage('assets', 'warn');
        }
        if (msg.indexOf('CHECK:PREFLIGHT:PASS') === 0) {
            setStage('preflight', 'done'); setStage('research', 'active');
            $('build-sub').textContent = 'Looking for how this subject is actually taught…';
            return;
        }
        if (msg.indexOf('CHECK:PREFLIGHT:FAIL') === 0) {
            setStage('preflight', 'warn');
            fail("Helga could not reach the model. The course was not started.");
            return;
        }

        if (msg.indexOf('RESEARCH:SYLLABUS:') === 0 ||
            msg.indexOf('RESEARCH:COURSE:') === 0) {
            var f = msg.split(':').slice(2).join(':').split('|');
            addEvidence('<div class="build-source">' +
                '<span class="i i-book" aria-hidden="true"></span>' +
                '<div><strong>' + esc(f[1] || '') + '</strong>' +
                '<span>' + esc(f[0] || '') + ' · ' + esc(f[2] || '0') +
                ' chapters</span></div></div>');
            return;
        }
        if (msg.indexOf('RESEARCH:BOOK:') === 0) {
            addEvidence('<div class="build-source is-text">' +
                '<span class="i i-books" aria-hidden="true"></span>' +
                '<div><strong>' + esc(msg.slice('RESEARCH:BOOK:'.length)) +
                '</strong><span>book written at this level</span></div></div>');
            return;
        }
        if (msg.indexOf('RESEARCH:LEVEL:') === 0) {
            $('build-sub').textContent =
                'Gathering ' + msg.slice('RESEARCH:LEVEL:'.length) +
                '-level material…';
            return;
        }

        if (msg.indexOf('CHECK:SYLLABUS_EVIDENCE:') === 0) {
            var detail = msg.split(':').slice(2).join(':');
            setStage('research', 'done'); setStage('skeleton', 'active');
            if (detail === 'NONE') {
                setStage('research', 'warn');
                addEvidence('<p class="build-warn">No open-textbook syllabus found ' +
                            'for this subject. The structure is being generated ' +
                            'without that evidence, so coverage may be weaker.</p>');
            } else {
                addEvidence('<p><strong>' + esc(detail) + '</strong> of real ' +
                            'syllabus material found. Helga is reconciling them ' +
                            'rather than copying any one of them.</p>');
            }
            return;
        }

        if (msg.indexOf('STRUCT:MODULE:') === 0) {
            modules.push({ title: msg.slice('STRUCT:MODULE:'.length), children: [] });
            renderTree();
            $('build-sub').textContent = 'Writing the course structure…';
            return;
        }
        if (msg.indexOf('STRUCT:') === 0 && modules.length) {
            var leaf = msg.split(':').slice(2).join(':');
            if (leaf) {
                modules[modules.length - 1].children.push(leaf);
                conceptCount++;
                renderTree();
            }
            return;
        }

        if (msg.indexOf('CHECK:SYLLABUS:') === 0) {
            setStage('skeleton', 'done');
            var parts = msg.split(':');
            var verdict = parts[2] || '';
            var pct = parts[3] || '';
            if (verdict === 'SKIP') {
                setStage('coverage', 'warn');
                addEvidence('<p class="build-warn">Coverage could not be measured ' +
                            'this time.</p>');
            } else {
                setStage('coverage', verdict === 'ADEQUATE' ? 'done' : 'warn');
                addEvidence('<p><strong>Coverage check: ' + esc(verdict) + '</strong> ' +
                            (pct ? '(' + esc(pct) + ' of the subject’s core topics)' : '') +
                            '</p>');
            }
            setStage('hydrate', 'active');
            $('build-sub').textContent = 'Writing each concept and grounding it in sources…';
            return;
        }

        if (msg.indexOf('HYDRATE:SOURCES:') === 0) {
            conceptCount = conceptCount;   // counted from STRUCT already
            setStage('hydrate', 'active');
            var hf = msg.split(':').slice(2).join(':').split('|');
            $('build-sub').textContent = 'Writing “' + (hf[0] || '') + '”…';
            return;
        }
        if (/hydrat/i.test(msg)) {
            setStage('hydrate', 'active');
        }
        if (msg.indexOf('ERROR:') === 0) {
            log(msg, 'error');
        }
    }

    function fail(text) {
        var e = $('build-error');
        e.textContent = text;
        e.hidden = false;
    }

    function finish(courseUid) {
        ORDER.forEach(function (s) {
            var el = stageEls[s];
            if (el && !el.classList.contains('is-warn')) {
                el.classList.remove('is-active');
                el.classList.add('is-done');
            }
        });
        $('build-sub').textContent = 'Done.';
        var done = $('build-done');
        $('build-done-sub').textContent =
            modules.length + ' modules' +
            (conceptCount ? ', ' + conceptCount + ' concepts' : '') +
            ' — built in ' + $('build-elapsed').textContent + '.';
        if (courseUid) {
            $('build-open').href = '/learn?course_uid=' + encodeURIComponent(courseUid);
        }
        done.hidden = false;
    }

    // --- wiring -------------------------------------------------------------

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.build-stage').forEach(function (el) {
            stageEls[el.dataset.stage] = el;
        });
        setStage('preflight', 'active');

        var params = new URLSearchParams(window.location.search);
        var topic = params.get('topic');
        if (topic) $('build-topic').textContent = 'Building: ' + topic;

        setInterval(function () {
            var s = Math.floor((Date.now() - started) / 1000);
            $('build-elapsed').textContent =
                Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
        }, 1000);

        var toggle = $('build-log-toggle');
        toggle.addEventListener('click', function () {
            var ul = $('build-log');
            ul.hidden = !ul.hidden;
            toggle.setAttribute('aria-expanded', ul.hidden ? 'false' : 'true');
            toggle.textContent = ul.hidden ? 'Show detail' : 'Hide detail';
        });

        if (window.io) {
            var socket = window.io();
            socket.on('status_update', function (d) {
                handle(d && (d.message || d.status || d.text));
            });
            socket.on('course_ready', function (d) {
                finish(d && d.course_uid);
            });
        }

        window.HelgaBuildView = { handle: handle, finish: finish };
    });
})();
