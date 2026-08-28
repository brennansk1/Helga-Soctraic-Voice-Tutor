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

    // 'audit' and 'items' are the last two. They were missing here, so the
    // never-walk-backwards guard had no index for them and the list ended a
    // stage before the build did.
    var ORDER = ['preflight', 'research', 'skeleton', 'coverage', 'hydrate',
                 'assets', 'audit', 'items'];
    var reached = 0;   // furthest stage index activated so far

    function setStage(name, state) {
        var el = stageEls[name];
        if (!el) return;
        // Never walk backwards. Two producers drive these stages now (the
        // fine-grained CHECK:/RESEARCH: stream and the coarse PIPELINE_STAGE
        // events); whichever arrives second must not un-finish a stage.
        var idx = ORDER.indexOf(name);
        if (state === 'active') {
            if (idx < reached) return;
            reached = idx;
        }
        el.classList.remove('is-active', 'is-done', 'is-warn');
        if (state) el.classList.add('is-' + state);
        // Everything before the active stage is finished by definition.
        if (state === 'active') {
            var i = idx;
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
        /* SIX CHECKS, NOT ONE. The builder emits CHECK:PREFLIGHT:PASS:<msg>
           for the topic, the context window, the depth, storage writability,
           the model being online and the model being able to generate — and
           this collapsed every one of them to "Model is reachable", so the
           learner saw the same sentence repeated and learned nothing from any
           of it. The message is right there after the prefix. */
        [/^CHECK:PREFLIGHT:WARN:?(.*)/,  function (m) {
            return (m[1] || 'A pre-flight check passed with a caveat').trim(); }],
        [/^CHECK:PREFLIGHT:PASS:?(.*)/,  function (m) {
            var raw = (m[1] || '').trim();
            if (!raw) return 'Pre-flight check passed';
            /* REPLACE THE WHOLE LINE, not the matched prefix. Using
               raw.replace() left the untouched tail on the end, so
               "Context window 8192 tokens" rendered as "Model context is
               large enough (8192 tokens) tokens". */
            var known = [
                [/^topic valid/i,            function ()  { return 'Topic looks buildable'; }],
                [/^context window (\d+)/i,   function (m) { return 'Model context is large enough — ' + m[1] + ' tokens'; }],
                [/^depth valid/i,            function ()  { return 'Requested depth is valid'; }],
                [/^storage writable/i,       function ()  { return 'Storage is writable'; }],
                [/^llm online.*?(\d+) *ms/i, function (m) { return 'Model is reachable — ' + m[1] + 'ms'; }],
                [/^llm online/i,             function ()  { return 'Model is reachable'; }],
                [/^llm content generation/i, function ()  { return 'Model can generate content'; }]
            ];
            for (var i = 0; i < known.length; i++) {
                var hit = raw.match(known[i][0]);
                if (hit) return known[i][1](hit);
            }
            return raw.charAt(0).toUpperCase() + raw.slice(1);
        }],

        /* AUDITED 2026-08-24: seven status prefixes were emitted by the
           backend and rendered by nothing, so real build work happened in
           silence. These are the build-time ones. (CPROG/PEDAGOGY/QTYPE are
           tutoring-time and belong to the session view, not this page.) */
        [/^DOMAIN:KINDS:(\d+):(\d+)/,    function (m) {
            var typed = +m[1], unknown = +m[2];
            return 'Teaching layer applied — ' + typed + ' concepts typed' +
                   (unknown ? ', ' + unknown + ' left generic' : ''); }],
        [/^AUDIT:PASS1:DEDUP:STARTING/,   function () {
            return 'Checking for repeated material'; }],
        [/^AUDIT:DEDUP:(\d+)/,            function (m) {
            return 'Removed ' + m[1] + ' duplicate ' +
                   (+m[1] === 1 ? 'topic' : 'topics'); }],
        [/^AUDIT:PASS2:LLM_REVIEW:STARTING/, function () {
            return 'Reviewing the syllabus for quality'; }],
        [/^AUDIT:COMPLETE:(\d+):(\d+)/,  function (m) {
            return 'Syllabus settled — ' + m[1] + ' modules, ' + m[2] +
                   ' concepts'; }],
        [/^SYLLABUS:AUDIT:STARTING/,      function () {
            return 'Comparing against a real syllabus'; }],
        [/^SYLLABUS:PHASE:1_SKELETON/,    function () {
            return 'Drafting the course outline'; }],
        [/^DOCS:CURRICULUM:(\d+)/,        function (m) {
            return 'Read ' + m[1] + ' documentation sections'; }],
        [/^DOCS:SYNTHESIS_FAILED:(.+)/,   function (m) {
            return 'Documentation pass failed — ' + m[1]; }],
        [/^CHECK:PREFLIGHT:FAIL/,        function () { return 'Could not reach the model'; }],
        [/^CHECK:SYLLABUS_EVIDENCE:NONE/, function () {
            return 'No open textbook found for this subject — building without that evidence'; }],
        [/^CHECK:SYLLABUS_EVIDENCE:(.+)/, function (m) {
            return 'Found ' + m[1] + ' of real syllabus material'; }],
        // The scope warning is the one a learner must actually act on, so it is
        // passed through verbatim rather than summarised: it names the shortfall
        // and offers the right-sized alternative, and a paraphrase would lose
        // the number that makes it actionable.
        [/^CHECK:SCOPE:(stretched|unsupported):(.+)/, function (m) {
            return (m[1] === 'unsupported' ? '\u26A0 ' : '') + m[2]; }],

        /* THE SEARCH IS STILL WORKING — SAY WHAT IT IS DOING.
           A thin first sweep now escalates instead of immediately shrinking
           the course, and that escalation takes real time. Without these two
           lines the learner watches a progress bar sit still and concludes it
           has hung; with them they can see it is widening the search, and WHY
           each widening is a sensible thing to try. */
        [/^SCOPE:DEEPEN:([a-z]+):(.+)/, function (m) {
            return 'Not much material yet \u2014 searching ' + m[2]; }],

        /* And the outcome, in the learner's terms. "sufficient" and
           "saturated" are opposite answers and must never read the same:
           one means we found it, the other means it genuinely is not there. */
        [/^SCOPE:DEEPENED:(sufficient|saturated|budget|exhausted|degraded):(.+)/,
         function (m) {
            var mark = (m[1] === 'sufficient') ? '\u2713 '
                     : (m[1] === 'degraded') ? '\u2139 '
                     : '\u26A0 ';
            return mark + m[2]; }],
        [/^CHECK:COVERAGE:(\d+)/,        function (m) {
            return 'Covers ' + m[1] + '% of the published syllabus for this subject'; }],
        [/^CHECK:SEQUENCING:INDEX_ORDER/, function () {
            return 'Modules came out in alphabetical order — rebuilding in a ' +
                   'teaching sequence'; }],
        [/^STRUCT:SPINE:(.+)/,           function (m) {
            return 'Following a real textbook\u2019s chapter order — ' + m[1]; }],
        [/^STRUCT:BACKFILL:(\d+)(.*)/,   function (m) {
            return 'Adding ' + m[1] + ' topic(s) the published syllabus has ' +
                   'and this outline missed'; }],
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
        [/^AUDIT:PHASE:START/,           function () {
            return 'Auditing the finished course — every concept, and the '
                 + 'course as a whole'; }],
        [/^REPAIR:CONCEPT:(.+)/,         function (m) {
            return '   repairing ' + m[1]; }],
        [/^REPAIR:DONE:(\d+):(\d+):(\d+)/, function (m) {
            // Said in the terms that matter: what was fixed, and what is being
            // held back rather than taught.
            var fixed = +m[1] + +m[2], withheld = +m[3];
            var parts = [];
            if (fixed) parts.push(fixed + ' concept(s) corrected');
            if (withheld) parts.push(withheld + ' held back — a check found '
                                   + 'something wrong that could not be fixed');
            return parts.length ? 'Repair: ' + parts.join('; ')
                                : 'Repair: nothing needed fixing'; }],
        // EVERY MESSAGE THE BUILDER EMITS NEEDS A SENTENCE HERE.
        //
        // Unmatched messages fall through to `return msg`, so a learner
        // watching a build sees the raw developer string — "CHECK:GROUNDING:
        // NONE", "AUDIT:SHORT_TITLE:3 node(s) renamed". Found by testing all
        // 79 strings course_builder.py emits against the patterns this table
        // matches; ten had no translation.
        [/^AUDIT:PASS2:FIXING:(\d+)_ISSUES/, function (m) {
            return 'Correcting ' + m[1] + ' problem(s) in the outline'; }],
        [/^AUDIT:RENAME:(\d+)/,          function (m) {
            return 'Renamed ' + m[1] + ' item(s) that repeated their parent'; }],
        [/^AUDIT:SHORT_TITLE:(\d+)/,     function (m) {
            return 'Gave ' + m[1] + ' item(s) a fuller title'; }],
        [/^CHECK:COHERENCE:INCOHERENT/,  function () {
            return 'The outline did not hang together — rebuilding it'; }],
        [/^CHECK:EVIDENCE_SUPPLEMENTARY:(\d+)/, function (m) {
            return m[1] + '% of the evidence is supplementary rather than '
                 + 'course-grade — the build caps how far it leans on it'; }],
        [/^CHECK:GROUNDING:NONE/,        function () {
            return 'No usable source found for this subject — the course will '
                 + 'say so rather than pretend otherwise'; }],
        [/^CHECK:RESEARCH_LOOP:(\d+)/,   function (m) {
            return 'Searching again with better terms (round ' + m[1] + ')'; }],
        [/^CHECK:SCOPE:(\d+):(\d+)/,     function (m) {
            return 'Scope check: material supports ' + m[1] + ' of the '
                 + m[2] + ' asked for'; }],
        [/^CHECK:TITLES:GENERIC:(\d+)/,  function (m) {
            return m[1] + ' title(s) were too generic to teach from — '
                 + 'rewriting them'; }],
        [/^SCOPE:DEEPENED:(\d+)/,        function (m) {
            return 'Thin subject — going deeper instead of wider ('
                 + m[1] + ' added)'; }],
        [/^AUDIT:GATE:(\w+):(.*)/,       function (m) {
            // THE VERDICT THAT DECIDES WHETHER THE COURSE OPENS.
            //
            // Emitted by the gate and matched by nothing, so the most
            // consequential line of the whole build would have reached the
            // learner as "AUDIT:GATE:needs_review:4 of 4 concepts are missing
            // sections the tutor reads". Found by testing the strings the
            // builder emits against the patterns this table matches, rather
            // than by watching a build.
            var gated = m[1], why = (m[2] || '').trim();
            if (gated === 'ready') {
                return 'Cleared to teach' + (why ? ' — ' + why : '');
            }
            return 'Held for review' + (why ? ': ' + why : '') +
                   '. You can open it from Courses and finish it.'; }],
        [/^AUDIT:DONE:(\w+):(\d+):(\d+)/, function (m) {
            // Said plainly. "blocking_findings" means something is FALSE, and
            // a learner reading this deserves the word rather than the enum.
            var verdict = m[1], blocking = +m[2], serious = +m[3];
            if (verdict === 'clean') {
                return 'Audit passed — nothing found';
            }
            if (verdict === 'blocking_findings') {
                return 'Audit found ' + blocking + ' claim(s) contradicted by a '
                     + 'real database' + (serious ? ', and ' + serious
                     + ' other problem(s)' : '');
            }
            if (verdict === 'needs_review') {
                return 'Audit found ' + serious + ' thing(s) worth reviewing';
            }
            if (verdict === 'incomplete') {
                return 'Audit could not check everything — see the course page';
            }
            return 'Audit finished: ' + verdict; }],
        /* Must precede the generic STRUCT:\w+ rule below, which would
           otherwise render this as a bare "1:6". */
        [/^STRUCT:MODULE_PROGRESS:(\d+):(\d+)/, function (m) {
            return 'Module ' + m[1] + ' of ' + m[2];
        }],
        [/^STRUCT:MODULE:(.+)/,          function (m) { return 'Module: ' + m[1]; }],
        [/^STRUCT:(?:UNIT|LESSON):(.+)/, function (m) { return '   ' + m[1]; }],
        // The concept/hydration lines carry the uid in field 2. The old
        // catch-all printed it, so the log read "con_4f2a91bc:Photosynthesis".
        [/^STRUCT:CONCEPT:[^:]*:(.+)/,   function (m) { return '   ' + m[1]; }],
        [/^STRUCT:HYDRATING:[^:]*:[^:]*:(.+)/, function (m) {
            return '   Writing “' + m[1] + '”…'; }],
        [/^STRUCT:HYDRATED:[^:]*:[^:]*:(.+)/, function (m) {
            return '   Wrote “' + m[1] + '”'; }],
        [/^STRUCT:WARN:(\w+):?(.*)/,     function (m) { return warnText(m[1], m[2]); }],
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
        [/^STRUCT:DAG_BUILD/,             function () { return 'Constructing topological prerequisite DAG'; }],
        [/^HYDRATE:REFUTATION_TEXT:([^|]+)/, function (m) { return 'Formulating misconception refutation for “' + m[1] + '”'; }],
        [/^HYDRATE:FADING:([^|]+)/,          function (m) { return 'Generating faded worked-example steps for “' + m[1] + '”'; }],
        [/^ASSET:COORDINATE_VALIDATION/,  function () { return 'Verifying diagram geometry coordinates'; }],
        [/^ERROR:\s*(.*)/,               function (m) { return m[1] || 'Something went wrong'; }],
        [/^LOG:\s*(.*)/,                 function (m) { return m[1]; }],
    ];

    // STRUCT:WARN:<KIND>[:<detail>] — the six quality caveats. They are the
    // only signal a learner gets that this course sits below the level it
    // claims or still carries a claim the fact-checker could not resolve, and
    // this page had no handler at all: they rendered as plain log lines,
    // indistinguishable from ordinary progress.
    var WARN_TEXT = {
        CONCEPT_STUB:    function (d) { return 'Could not write “' + d + '” — left as a stub'; },
        DEPTH_MISS:      function (d) { return '“' + d + '” is thinner than the level requested'; },
        DEPTH_SUMMARY:   function (d) { return d || 'Some concepts are below the requested level'; },
        FACT_UNRESOLVED: function (d) { return '“' + d + '” has a claim that could not be verified'; },
        FACT_SUMMARY:    function (d) { return d + ' concepts still contain confirmed-false claims'; },
        LEVEL_GAP:       function (d) { return 'This course reads ' + d + ' levels from the one it claims'; }
    };

    var warnings = [];

    function warnText(kind, detail) {
        return WARN_TEXT[kind] ? WARN_TEXT[kind](detail)
                               : (kind + (detail ? ': ' + detail : ''));
    }

    function humanise(msg) {
        for (var i = 0; i < HUMAN.length; i++) {
            var m = msg.match(HUMAN[i][0]);
            if (m) return HUMAN[i][1](m);
        }
        return msg;
    }

    // One sentence for the live stream, or '' when a message is purely
    // internal. Walks the same HUMAN table the log renderer uses, so the
    // stream and the log can never tell two different stories.
    //
    // This declaration used to sit INSIDE the RESEARCH:BOOK entry of the HUMAN
    // table, so it was scoped to that callback and never existed out here.
    // handle() calls it on the very first status message, which therefore threw
    // ReferenceError and took the whole build view down with it — no stages, no
    // stream, no completion. Everything below depends on it being at this
    // scope, so it stays here.
    function translate(msg) {
        for (var i = 0; i < HUMAN.length; i++) {
            var m = msg.match(HUMAN[i][0]);
            if (m) { try { return HUMAN[i][1](m) || ''; } catch (e) { return ''; } }
        }
        return '';
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

    function streamKind(msg) {
        if (/^RESEARCH:/.test(msg)) return 'evidence';
        if (/^STRUCT:WARN|^CHECK:.*(FAIL|INADEQUATE)|^STRUCT:REDUNDANT/.test(msg)) return 'warn';
        if (/^CHECK:/.test(msg)) return 'gate';
        if (/^STRUCT:/.test(msg)) return 'structure';
        return '';
    }

    // --- message interpretation --------------------------------------------
    //
    // The builder emits a structured prefix vocabulary (CHECK:, STRUCT:, LOG:).
    // Parsing the STRUCTURE rather than matching free text is deliberate: the
    // previous progress UI matched substrings like "hydrat" and broke whenever
    // a message was reworded.

    // --- book mode -----------------------------------------------------
    //
    // The first BOOK:* status proves this is an upload build, so the
    // researched rail is swapped for the book rail. Without this, an upload
    // showed "Research: finding real syllabi" — narrating a pipeline that was
    // not running.
    var bookMode = false;
    function enterBookMode() {
        if (bookMode) return;
        bookMode = true;
        var r = document.getElementById('build-stages');
        var b = document.getElementById('book-stages');
        if (r) r.classList.add('hidden');
        if (b) b.classList.remove('hidden');
        var sub = document.getElementById('build-sub');
        if (sub) sub.textContent = 'Reading your book — structure first, then every chapter.';
    }

    var nowText = document.getElementById('build-now-text');
    var streamEl = document.getElementById('build-stream');
    function now(text) { if (nowText && text) nowText.textContent = text; }
    function stream(text, kind) {
        if (!streamEl || !text) return;
        var li = document.createElement('li');
        li.className = 'stream-item' + (kind ? ' stream-' + kind : '');
        var t = document.createElement('time');
        var d = new Date();
        t.textContent = ('0' + d.getHours()).slice(-2) + ':' +
                        ('0' + d.getMinutes()).slice(-2) + ':' +
                        ('0' + d.getSeconds()).slice(-2);
        var span = document.createElement('span');
        span.textContent = text;                    // textContent, always
        li.appendChild(t); li.appendChild(span);
        streamEl.appendChild(li);
        while (streamEl.children.length > 80) streamEl.removeChild(streamEl.firstChild);
        streamEl.scrollTop = streamEl.scrollHeight;
    }

    function handleBook(msg) {
        var m;
        if ((m = msg.match(/^BOOK:PARSED:(\w+):(\d+):(\d+):(\d+)/))) {
            enterBookMode(); setStage('book-read', 'done'); setStage('book-shape', 'active');
            var sent = 'Read the book: ' + m[2] + ' chapters' +
                (+m[3] ? ', ' + m[3] + ' parts' : '') + ', ' +
                Number(m[4]).toLocaleString() + ' words (' + m[1].toUpperCase() + ')';
            now(sent); stream(sent, 'evidence');
            return true;
        }
        if ((m = msg.match(/^BOOK:SHAPE:([\w_]+):(.*)/))) {
            enterBookMode(); setStage('book-shape', 'done'); setStage('book-name', 'active');
            var human = { textbook: 'Textbook: chapters become modules, sections become lessons',
                          parts_as_units: 'Parts become units; one lesson per chapter',
                          chapters_as_lessons: 'One lesson per chapter, in the book\u2019s own order' }[m[1]] || m[1];
            now(human); stream(human + ' \u2014 ' + m[2], 'structure');
            return true;
        }
        if ((m = msg.match(/^BOOK:READING:(\d+):(\d+):(.*)/))) {
            enterBookMode(); setStage('book-name', 'active');
            var barWrap = document.getElementById('book-progress');
            var fill = document.getElementById('book-progress-fill');
            var label = document.getElementById('book-progress-label');
            if (barWrap) barWrap.classList.remove('hidden');
            if (fill) fill.style.width = Math.round(100 * m[1] / Math.max(1, m[2])) + '%';
            if (label) label.textContent = 'Chapter ' + m[1] + ' of ' + m[2];
            var line = 'Reading chapter ' + m[1] + ' of ' + m[2] +
                       (m[3] ? ' \u2014 ' + m[3] : '');
            now(line); stream(line);
            if (+m[1] === +m[2]) { setStage('book-name', 'done'); setStage('hydrate', 'active'); }
            return true;
        }
        if ((m = msg.match(/^BOOK:WARN:CHAPTER_SKIPPED:(.*)/))) {
            stream('Could not name concepts for ' + m[1] + ' \u2014 it will retry in hydration', 'warn');
            return true;
        }
        if ((m = msg.match(/^BOOK:UNREADABLE:(.*)/))) {
            now('Could not read ' + m[1]);
            stream('Could not read ' + m[1] + ' \u2014 the build stopped rather than invent a course', 'warn');
            return true;
        }
        if ((m = msg.match(/^CHECK:BOOK_QA:(\w+)/))) {
            setStage('book-gate', m[1] === 'BOOK_FAITHFUL' ? 'done' : 'warn');
            var v = m[1] === 'BOOK_FAITHFUL'
                ? 'Quality gate passed: every lesson linked to its chapter, in the book\u2019s order'
                : 'Quality gate flagged issues \u2014 the course is usable and the flags are recorded';
            now(v); stream(v, m[1] === 'BOOK_FAITHFUL' ? 'gate' : 'warn');
            return true;
        }
        return false;
    }

    // Exposed as a test hook: a build takes tens of minutes, and the only way
    // to exercise this view without one is to replay a recorded status stream.
    // Used by E2E tests and the design preview; harmless in production.
    window.__helgaBuildHandle = function (raw) { handle(raw); };

    function handle(raw) {
        var msg = String(raw == null ? '' : raw).trim();
        if (!msg) return;
        log(msg);
        if (handleBook(msg)) return;
        // Everything below narrates the RESEARCHED pipeline; a translated
        // sentence also feeds the stream so the live feed is never empty.
        var t = translate(msg);
        if (t) { now(t); stream(t, streamKind(msg)); }

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

        /* Stage 5 — the item bank, which is what makes the course reviewable.

           THIS BLOCK THREW ON EVERY SINGLE BUILD MESSAGE. handle()'s parameter
           is `raw` and its trimmed copy is `msg`; this used `text`, which is
           declared nowhere in the function. So the ReferenceError fired on the
           first message of every build and again on all that followed, killing
           this handler before it reached the ITEMS: check and everything below
           it. Stage 5 could therefore never appear on the build page — the
           "Review items" tile stayed grey through builds that had extracted
           thousands of items.

           Caught by reading the console on a live build, not by grepping:
           every message produced "Uncaught ReferenceError: text is not
           defined" at build-view.js:565. */
        if (msg.indexOf('ITEMS:') === 0) {
            var n = parseInt(msg.slice(6), 10);
            setStage('items', 'active');
            if (!isNaN(n)) {
                stream(n
                    ? n.toLocaleString() + ' review items ready'
                    : 'No review items could be made from this content', n ? 'ok' : 'warn');
                setStage('items', n ? 'done' : 'warn');
            }
            return;
        }

        // Stage 4 — the audit. The last thing between a finished build and a
        // course a learner can open, and the only pass that reads the whole
        // course at once. Its verdict is the honest headline for the build, so
        // it must be visible rather than folded into "done".
        if (msg.indexOf('AUDIT:PHASE:START') === 0) {
            setStage('assets', 'done');
            setStage('audit', 'active');
        }
        if (msg.indexOf('REPAIR:CONCEPT:') === 0) {
            setStage('audit', 'active');
        }
        if (msg.indexOf('AUDIT:DONE:') === 0) {
            var av = msg.split(':');           // AUDIT:DONE:verdict:blocking:serious
            setStage('audit', av[2] === 'clean' ? 'done' : 'warn');
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

        if (msg.indexOf('STRUCT:WARN:') === 0) {
            var wf = msg.slice('STRUCT:WARN:'.length).split(':');
            var wt = warnText(wf[0], wf.slice(1).join(':'));
            warnings.push(wt);
            addEvidence('<p class="build-warn">' + esc(wt) + '</p>');
            return;
        }

        if (msg.indexOf('STRUCT:MODULE:') === 0) {
            modules.push({ title: msg.slice('STRUCT:MODULE:'.length), children: [] });
            renderTree();
            $('build-sub').textContent = 'Writing the course structure…';
            return;
        }
        // Only genuine structure nodes become tree children. The old catch-all
        // also swallowed STRUCT:HYDRATING / STRUCT:STRUCTURING / STRUCT:HYDRATED
        // — progress events about concepts that were ALREADY counted — which
        // inflated the concept total roughly fourfold, saturated the growth bar
        // within the first module, and printed raw internals like
        // "con_4f2a91bc:START:Photosynthesis" into the learner's tree.
        if (msg.indexOf('STRUCT:') === 0 && modules.length) {
            var sParts = msg.split(':');
            var sType = sParts[1];
            if (sType === 'UNIT' || sType === 'LESSON') {
                var container = modules[modules.length - 1];
                var label = sParts.slice(2).join(':');
                if (label) { container.children.push(label); renderTree(); }
            } else if (sType === 'CONCEPT') {
                // STRUCT:CONCEPT:<uid>:<title> — the title only; the uid is ours.
                var cTitle = sParts.slice(3).join(':');
                if (cTitle) {
                    modules[modules.length - 1].children.push(cTitle);
                    conceptCount++;
                    renderTree();
                }
            } else if (sType === 'HYDRATING') {
                // A concept being written, not a new one. Report it, count nothing.
                setStage('hydrate', 'active');
                var hTitle = sParts.slice(4).join(':');
                if (hTitle) $('build-sub').textContent = 'Writing “' + hTitle + '”…';
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

        /* THE END OF THE BUILD, READ FROM THE STREAM THAT ACTUALLY EXISTS.
           This view waited for a 'course_ready' Socket.IO event; nothing on the
           server has ever emitted one. So a finished build showed the last
           hydration line forever, never revealed the "Open it" panel, and never
           released the single-build lock — which then walled off /create for the
           guard's full four-hour expiry. The pipeline's own last words are these
           two lines, and they are what we listen for instead. */
        if (/^Course built successfully/i.test(msg)) {
            settle('complete');
            return;
        }
        if (/^Error creating course|^Skeleton generation failed/i.test(msg)) {
            settle('error', msg);
            return;
        }
    }

    function fail(text) {
        var e = $('build-error');
        e.textContent = text;
        e.hidden = false;
    }

    // A build ends once. Both the status stream and the poll below can spot the
    // same ending, and neither may draw the panel twice or fight the other.
    var settled = false;

    function settle(phase, detail) {
        if (settled) return;
        settled = true;
        stopPolling();
        // Release the lock either way: a build that has ended is not a build in
        // progress, and a failed one that keeps the lock is the worse outcome —
        // it blocks the retry.
        if (window.HelgaBuildGuard) window.HelgaBuildGuard.clear();
        if (phase === 'error') {
            fail(detail
                ? 'The build stopped: ' + detail
                : 'The build stopped before it finished. Nothing was saved.');
            return;
        }
        /* A CANCELLED BUILD IS NOT A FINISHED ONE. Without this branch a
           cancellation fell through to the success path below, which asks for
           a course_uid, gets null, and still shows the completion panel — the
           build the learner just stopped, reported as done. */
        if (phase === 'cancelled') {
            fail('You stopped this build. Nothing was saved, and any concepts '
                 + 'already written are kept so a rebuild can reuse them.');
            return;
        }
        // The uid is not in the status text, so ask the service that holds it.
        // Without one the panel still appears — the course exists — but it
        // points at Courses rather than pretending to know which one.
        fetch('/api/creation_status')
            .then(function (r) { return r.ok ? r.json() : {}; })
            .catch(function () { return {}; })
            .then(function (s) { finish(s && s.course_uid); });
    }

    function idle() {
        var cancel = $('build-cancel');
        if (cancel) cancel.hidden = true;
        var title = $('build-topic');
        if (title) title.textContent = 'No course is building';
        var sub = $('build-sub');
        if (sub) sub.textContent =
            'Start one from Courses, or pick a book in the Library.';
        /* The replay fills these from the LAST build's messages, so without
           clearing them an idle page showed that build's evidence and modules
           as though they were live. */
        var ev = $('build-evidence');
        if (ev) ev.textContent = 'Nothing building — no evidence to show.';
        var mods = $('build-modules');
        if (mods) mods.textContent = '';
        var el = $('build-elapsed');
        if (el && el.parentElement) el.parentElement.hidden = true;
        ORDER.forEach(function (s) {
            var n = stageEls[s];
            if (n) n.classList.remove('is-active', 'is-done', 'is-warn');
        });
        var stream = $('build-stream');
        if (stream) {
            stream.textContent = '';
            var li = document.createElement('li');
            li.className = 'stream-item';
            li.textContent = 'Nothing is building right now.';
            stream.appendChild(li);
        }
    }

    function finish(courseUid) {
        settled = true;
        stopPolling();
        ORDER.forEach(function (s) {
            var el = stageEls[s];
            if (el && !el.classList.contains('is-warn')) {
                el.classList.remove('is-active');
                el.classList.add('is-done');
            }
        });
        $('build-sub').textContent = 'Done.';
        var done = $('build-done');
        var nMods = totals ? totals.modules : modules.length;
        var nCons = totals && typeof totals.concepts === 'number' ? totals.concepts : conceptCount;
        $('build-done-sub').textContent =
            nMods + ' modules' +
            (nCons ? ', ' + nCons + ' concepts' : '') +
            ' — built in ' + $('build-elapsed').textContent + '.' +
            (warnings.length
                ? ' ' + warnings.length + ' quality note' +
                  (warnings.length === 1 ? '' : 's') + ' — see the evidence panel.'
                : '');
        if (courseUid) {
            $('build-open').href = '/learn?course_uid=' + encodeURIComponent(courseUid);
        } else {
            $('build-open').href = '/courses';
            $('build-open').textContent = 'Open your courses';
        }
        done.hidden = false;
    }

    /* --- the poll ------------------------------------------------------------
       The Socket.IO stream only reaches a browser that is on this page at the
       moment a message is sent. A learner who opened /build after the pipeline
       had already finished, or whose socket dropped during a build that runs for
       tens of minutes, would see a page that never resolves.

       The verdict comes from the build guard so this page and the nav pill read
       the server the same way — including its rule that only positive evidence
       of an ending counts, because a proxy that cannot reach core answers 200
       with an error field and that must not be mistaken for "finished". */
    var pollTimer = null;

    function stopPolling() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }

    function poll() {
        if (settled) return;

        /* THE RAIL USED TO MOVE ONLY ON STATUS MESSAGES.
           Those are pushed over Socket.IO, so any stretch that emits nothing —
           a curriculum sweep sitting in 45-second Wikimedia rate-limit
           backoffs, for instance — left the rail on "Pre-flight" for many
           minutes while the build was healthy and busy. Worse, if the socket
           never connects (a build page opened on an origin the server's CORS
           list does not name), NOTHING arrives and the page reads "Warming
           up..." for the entire build.

           The server already knows the coarse phase and reports it on
           /api/creation_status, which this poller was fetching and using only
           to detect completion. Use it for the rail too: a second, independent
           source of truth that does not depend on the socket at all.
           setStage() never walks backwards, so whichever source is ahead wins
           and the fine-grained stream still adds the detail. */
        fetch('/api/creation_status')
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; })
            .then(function (st) {
                if (!st || settled) return;
                /* THE TWO VOCABULARIES DO NOT MATCH, and silently.
                   The server reports skeleton -> audit -> hydration ->
                   complete; this rail is named preflight, research, skeleton,
                   coverage, hydrate, assets. "audit" and "hydration" are in
                   neither list, so ORDER.indexOf() returned -1 for both and
                   the rail stuck on Structure for the whole build while the
                   server happily reported progress nobody could see. */
                var PHASE_TO_STAGE = {
                    // The build's very first status, set before preflight
                    // even runs. Found by the vocabulary test, not by hand.
                    initializing: 'preflight',
                    preflight: 'preflight', research: 'research',
                    skeleton: 'skeleton',
                    audit: 'coverage', coverage: 'coverage',
                    hydration: 'hydrate', hydrate: 'hydrate',
                    assets: 'assets'
                };
                var stage = PHASE_TO_STAGE[st.phase];
                if (stage) setStage(stage, 'active');
                if (st.phase === 'complete') {
                    settled = true; stopPolling(); finish(st.course_uid);
                    return;
                }
                if (st.phase === 'error' || st.phase === 'aborted') {
                    settled = true; stopPolling(); settle('error', null);
                    return;
                }
                if (st.phase === 'cancelled') {
                    settled = true; stopPolling(); settle('cancelled', null);
                    return;
                }
                if (st.active === false && st.course_uid) {
                    settled = true; stopPolling(); finish(st.course_uid);
                    return;
                }
                /* NO BUILD AT ALL IS ITS OWN STATE.
                   Opening /build with nothing running showed the last build's
                   rail — every stage green, the subtitle "Done." — and still
                   offered "Cancel build" for a build that did not exist. The
                   replay is what fills the rail, so the page looked busy while
                   the server reported nothing at all. Say so instead. */
                if (st.active === false && !st.course_uid && !st.started_at) {
                    /* ...UNLESS A BUILD IS RUNNING SOMEWHERE THIS ENDPOINT
                       CANNOT SEE. creation_status reads the FSM in core-logic.
                       A RESUME runs ContentHydrator inside the rag-engine, so
                       every field checked above is empty for its whole
                       duration and this page — whose entire job is showing the
                       running build — said "No course is building" while one
                       was writing its 37th concept. The Create modal one click
                       earlier correctly refused to start a second build,
                       so two screens disagreed about the same fact.

                       /api/build/status is the record a resume DOES claim. */
                    fetch('/api/build/status')
                        .then(function (r) { return r.ok ? r.json() : null; })
                        .catch(function () { return null; })
                        .then(function (b) {
                            if (settled) return;
                            if (b && b.active && !b.stale) {
                                var t = $('build-topic');
                                if (t && b.topic) t.textContent = b.topic;
                                var sub = $('build-sub');
                                if (sub) {
                                    sub.textContent = b.source === 'resume'
                                        ? 'Continuing a build that stopped '
                                          + 'early — concepts already written '
                                          + 'are kept.'
                                        : 'Building.';
                                }
                                var stage = PHASE_TO_STAGE[b.stage];
                                if (stage) setStage(stage, 'active');
                                return;   // keep polling; it is alive
                            }
                            settled = true; stopPolling(); idle();
                        });
                    return;
                }
            });

        if (!window.HelgaBuildGuard || !window.HelgaBuildGuard.probe) return;
        window.HelgaBuildGuard.probe(function (verdict, courseUid, phase) {
            if (settled || verdict !== 'ended') return;
            if (phase === 'error') settle('error', null);
            else { settled = true; stopPolling(); finish(courseUid); }
        });
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

        /* REPLAY WHAT ALREADY HAPPENED.
           Socket.IO only delivers messages sent AFTER this page connected, so
           reloading during a build — or opening it from the pill after
           navigating away — showed an empty log and "Warming up..." for a
           build that had been talking for an hour. The durable record already
           keeps the last 120 messages and /api/build/status already returns
           them; nothing replayed them. Replayed through the same handler as
           live traffic, so the rail, the evidence panel and the module list
           all rebuild exactly as they would have live. */
        fetch('/api/build/status')
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; })
            .then(function (st) {
                if (!st || !st.messages || !st.messages.length) return;
                st.messages.forEach(function (m) {
                    // The record's field is `m`, not `message`. Checked
                    // against the real payload rather than assumed: guessing
                    // here fails silently, replaying nothing at all.
                    try { handle(typeof m === 'string' ? m : (m && (m.m || m.message || m.msg))); }
                    catch (e) { /* one bad record must not stop the replay */ }
                });
            });

        /* ELAPSED IS SINCE THE BUILD STARTED, NOT SINCE THIS TAB OPENED.
           `started` is set when the script loads, so reloading a page during a
           three-hour build showed "0:02" — and this number is the learner's
           only evidence of how long the thing has been going. The server sends
           started_at on /api/creation_status; trust it when it is there. */
        fetch('/api/creation_status')
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; })
            .then(function (st) {
                if (!st || !st.started_at) return;
                var t = Date.parse(st.started_at);
                // Guard a clock skew between container and browser: a start in
                // the future would render a negative timer.
                if (!isNaN(t) && t <= Date.now()) started = t;
            });

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
                var text = d && (d.message || d.status || d.text);
                // Log the human line either way, then let the structured event
                // — when there is one — drive the stages and the finish card.
                if (text) log(text);
                if (d && d.event && handleStage(d.event, text)) return;
                handle(text, true);
            });
            // There was a socket.on('course_ready', …) here. No server code has
            // ever emitted that event — app.py emits state_update, health_update,
            // stream_token and status_update, and nothing else — so it was the
            // only path to the completion panel and it could never fire.
            // Completion now comes from the status stream and the poll below.
        }

        poll();                              // settles a build already finished
        pollTimer = setInterval(poll, 10000);

        /* --- cancel ---------------------------------------------------
           The endpoint existed from the start with nothing calling it. A
           build holds the single-build lock for minutes, so "I picked the
           wrong book" had no answer short of restarting the service. */
        var cancelBtn = $('build-cancel');
        var confirmBox = $('build-cancel-confirm');
        var cancelErr = $('build-cancel-error');

        function showConfirm(on) {
            if (!confirmBox) return;
            confirmBox.classList.toggle('hidden', !on);
            if (on) {
                if (cancelErr) cancelErr.hidden = true;
                var no = $('build-cancel-no');
                if (no) no.focus();
            } else if (cancelBtn) { cancelBtn.focus(); }
        }

        if (cancelBtn) cancelBtn.addEventListener('click', function () { showConfirm(true); });
        var noBtn = $('build-cancel-no');
        if (noBtn) noBtn.addEventListener('click', function () { showConfirm(false); });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && confirmBox && !confirmBox.classList.contains('hidden')) {
                showConfirm(false);
            }
        });

        var yesBtn = $('build-cancel-yes');
        if (yesBtn) yesBtn.addEventListener('click', function () {
            yesBtn.disabled = true;
            yesBtn.textContent = 'Stopping…';
            fetch('/api/cancel_creation', { method: 'POST' })
                .then(function (r) {
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.json().catch(function () { return {}; });
                })
                .then(function () {
                    // Release the single-build lock through the guard's own
                    // clear(), not by deleting the key: clear() also repaints
                    // the nav pill, so reaching past it would leave a
                    // "Building…" pill pointing at a build that is over.
                    if (window.HelgaBuildGuard) window.HelgaBuildGuard.clear();
                    stream('Build cancelled. Nothing was saved.', 'warn');
                    window.location.href = '/create';
                })
                .catch(function (err) {
                    // Failing to cancel must not look like a cancel.
                    yesBtn.disabled = false;
                    yesBtn.textContent = 'Stop the build';
                    if (cancelErr) {
                        cancelErr.hidden = false;
                        cancelErr.textContent = 'Could not stop the build (' +
                            err.message + '). It is still running.';
                    }
                });
        });

        window.HelgaBuildView = { handle: handle, finish: finish };
    });
})();
