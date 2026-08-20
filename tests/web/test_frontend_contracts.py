"""Contracts the front end broke once and must not break again.

Every case here is a defect that was live in this tree, not a hypothetical. The
front end has no JS test runner, so these read the shipped sources and assert
the shape of the fix — the same approach test_design_system.py takes for the
theme tokens. Each test names the failure it is standing guard over.
"""

import os
import re
import shutil
import subprocess
import unittest

_here = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_here, '../../'))
_js = os.path.join(_root, 'services/web-ui/static/js')
_templates = os.path.join(_root, 'services/web-ui/templates')

OWNED_JS = [
    'courses.js', 'wizard.js', 'degree.js', 'build-view.js',
    'build-guard.js', 'create.js', 'practice.js', 'session.js',
]


def _read(*parts):
    with open(os.path.join(*parts), encoding='utf-8') as f:
        return f.read()


def _code_only(src):
    """Drop comments so a comment that QUOTES the old bug is not read as it.

    Several of the fixes below explain themselves by reproducing the line they
    replaced, which is exactly the text these tests search for. Quote handling
    is deliberately simple — enough to keep a `//` inside a string literal from
    starting a comment, which is all these files contain.
    """
    out, i, n = [], 0, len(src)
    quote = None
    while i < n:
        c = src[i]
        if quote:
            out.append(c)
            if c == '\\' and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in '\'"`':
            quote = c
            out.append(c)
            i += 1
            continue
        if src.startswith('//', i):
            i = src.find('\n', i)
            if i == -1:
                break
            continue
        if src.startswith('/*', i):
            end = src.find('*/', i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


class TestNoDataInsideInlineHandlers(unittest.TestCase):
    """A course title is data. It must never become JavaScript.

    courses.js and wizard.js built onclick attributes by interpolating a title
    through escapeHtml(). That is the wrong escape for the position: the HTML
    parser turns &#39; back into a bare apostrophe BEFORE the attribute's
    contents are parsed as JS, so a course called "Newton's Laws" produced a
    SyntaxError — Delete and Start Learning did nothing — and a title chosen on
    purpose could close the string and run whatever followed.
    """

    # Every inline handler an owned script writes, with its attribute value.
    # The lookbehind keeps `el.onclick = fn` — a property assignment, which is
    # fine — out of it.
    HANDLER = re.compile(
        r"""(?<![\w.])on(?:click|dblclick|change|input|submit|keydown|keyup|"""
        r"""keypress|mouse[a-z]+|focus|blur|load|error|drag[a-z]*|touch[a-z]+)"""
        r"""\s*=\s*(["'])((?:(?!\1).)*)""",
        re.S,
    )

    def _bad_handlers(self, src):
        """Handlers that put a runtime value inside a JS STRING literal.

        That is the whole bug: an index like removeConcept(${i}) is a number the
        page computed, but 'TITLE' inside the same attribute is data landing in
        a position where a quote character ends the string and the rest is
        parsed as code.
        """
        bad = []
        for _, value in self.HANDLER.findall(src):
            if "'" not in value:
                continue
            if '${' in value or re.search(r"""'\s*\+|\+\s*'""", value):
                bad.append(value.strip())
        return bad

    def test_no_owned_script_interpolates_values_into_an_inline_handler(self):
        for name in OWNED_JS:
            bad = self._bad_handlers(_code_only(_read(_js, name)))
            self.assertEqual(
                [], bad,
                f"{name} builds a JavaScript string literal inside an inline "
                "event handler out of runtime values. Attach the listener with "
                "addEventListener and carry the value in a data-* attribute.")

    def test_the_regression_would_be_caught(self):
        # Guards the guard: the exact line courses.js used to ship must fail.
        old = ("""<button onclick="startCourse('${course.uid}', """
               """'${escapeHtml(course.title)}', this)">Go</button>""")
        self.assertTrue(self._bad_handlers(old))

    def test_course_cards_carry_their_uid_as_data(self):
        src = _read(_js, 'courses.js')
        self.assertIn("dataset.action = 'start'", src)
        self.assertIn("dataset.action = 'delete'", src)
        self.assertIn('addEventListener', src)

    def test_course_titles_are_written_with_textContent(self):
        src = _read(_js, 'courses.js')
        # The card title and description are stored data; they go in as text.
        self.assertRegex(src, r'h3\.textContent\s*=\s*course\.title')
        self.assertRegex(src, r'desc\.textContent\s*=\s*course\.description')


class TestBuildCompletionIsRecognisable(unittest.TestCase):
    """A finished build has to be visible to the client that started it.

    Both the build view and the single-build lock waited for a 'course_ready'
    Socket.IO event. No server code has ever emitted one — app.py emits
    state_update, health_update, stream_token and status_update, and nothing
    else. So a completed build never revealed its "Open it" panel and never
    released the lock, which then blocked /create for the guard's full four-hour
    expiry.
    """

    def test_completion_does_not_depend_on_an_event_alone(self):
        """Neither file may rely on course_ready as its only completion path.

        Subscribing to it is not itself wrong — it is wrong as the ONLY way to
        learn a build finished, which is what it was.
        """
        for name in ('build-view.js', 'build-guard.js'):
            src = _code_only(_read(_js, name))
            listeners = re.findall(r"""\.on\(\s*['"]course_ready['"]""", src)
            self.assertEqual(
                [], listeners,
                f"{name} still subscribes to course_ready. If that event has "
                "become real, keep the status-stream and poll paths as well.")

    def test_completion_is_read_from_the_status_stream(self):
        # The pipeline's own last words on success, in fsm_logic.py.
        pipeline = _read(_root, 'services/core/fsm_logic.py')
        self.assertIn('Course built successfully!', pipeline)
        for name in ('build-view.js', 'build-guard.js'):
            self.assertIn('Course built successfully', _read(_js, name),
                          f"{name} does not recognise the pipeline's success line.")

    def test_the_lock_is_reconciled_against_the_server(self):
        src = _read(_js, 'build-guard.js')
        self.assertIn('/api/creation_status', src)
        self.assertIn('/api/build/status', src)
        # Both endpoints exist on the web-ui side.
        app = _read(_root, 'services/web-ui/app.py')
        self.assertIn("'/api/creation_status'", app)
        self.assertIn("'/api/build/status'", app)

    def test_an_unreachable_service_never_releases_the_lock(self):
        # web-ui answers 200 with an `error` field when it cannot reach core.
        # Reading that as "no build running" would unlock the create page mid
        # build and let a second build queue behind the first.
        src = _read(_js, 'build-guard.js')
        self.assertRegex(src, r'\.error\b')
        self.assertIn('GRACE_MS', src)

    def test_translate_is_declared_at_module_scope(self):
        """It was declared INSIDE the HUMAN lookup table.

        Scoped to one table entry, it did not exist where handle() calls it, so
        the first status message threw ReferenceError and took the whole build
        view down: no stages, no stream, no completion.
        """
        src = _read(_js, 'build-view.js')
        table = src[src.index('var HUMAN = ['):]
        table = table[:table.index('\n    ];')]
        self.assertNotIn('function translate', table,
                         'translate() is nested inside the HUMAN table again.')
        self.assertRegex(src, r'(?m)^    function translate\(msg\) \{')


class TestCreateChecksTheReply(unittest.TestCase):
    """Arming a four-hour lock on an unread response.

    create.js called HelgaBuildGuard.set() and navigated to /build from a bare
    .then(), so a 502 (core down) or 401 (no student session) still armed the
    lock and sent the learner to watch a build that never started.
    """

    def test_the_lock_is_armed_only_after_an_ok_response(self):
        src = _read(_js, 'create.js')
        start = src.index('function startCreate()')
        body = src[start:]
        guard = body.index('HelgaBuildGuard.set()', body.index('/api/event'))
        preamble = body[body.index('/api/event'):guard]
        self.assertIn('res.ok', preamble,
                      'startCreate arms the build lock without reading r.ok.')

    def test_the_failure_is_named(self):
        src = _read(_js, 'create.js')
        self.assertIn('res.body.error', src)
        self.assertIn('nothing was created', src)


class TestDegreeNeverFabricatesAPlan(unittest.TestCase):
    """An example degree drawn over a learner's real programmes.

    /api/programs answers HTTP 200 with {programs: [], error: 'unavailable'}
    when web-ui cannot reach core. degree.js read that as "no programmes yet"
    and rendered demoPlan() — a fabricated bachelor's map, labelled only
    "Preview with example data" in the subtitle.
    """

    def test_a_failed_load_does_not_render_the_example(self):
        src = _read(_js, 'degree.js')
        load = src[src.index('function load()'):]
        load = load[:load.index('/* ------')]
        catch = load[load.index('.catch('):]
        self.assertNotIn('demoPlan', catch,
                         'degree.js still falls back to the example plan when '
                         'the programmes could not be loaded.')

    def test_an_error_field_is_treated_as_a_failure(self):
        src = _read(_js, 'degree.js')
        self.assertRegex(src, r'd\.error')
        self.assertIn('Array.isArray(d.programs)', src)

    def test_the_example_still_shows_when_there_are_genuinely_none(self):
        src = _read(_js, 'degree.js')
        self.assertIn('if (d.programs.length) { loadPlan(d.programs[0].uid); return; }', src)
        self.assertIn('render(demoPlan());', src)


class TestScheduleDayDetail(unittest.TestCase):
    """Clicking a future day listed today's cards under that day's heading.

    schedule.html sent ?target_date=<clicked day>. web-ui's /api/due_cards
    proxy forwards only topic and course_uid, and librarian's endpoint never
    reads it either, so the parameter was dropped twice over.
    """

    def test_the_parameter_nobody_reads_is_not_sent(self):
        src = _read(_templates, 'schedule.html')
        self.assertNotRegex(
            src, r'due_cards\?[^`\'"]*target_date',
            'schedule.html still sends target_date to /api/due_cards, which '
            'neither the proxy nor librarian reads.')

    def test_cards_are_filtered_to_the_day_that_was_clicked(self):
        src = _read(_templates, 'schedule.html')
        self.assertIn("c.next_review_date || ''", src)
        self.assertIn('=== dateStr', src)

    def test_a_future_day_says_it_cannot_list_the_cards(self):
        src = _read(_templates, 'schedule.html')
        self.assertIn('dateStr > todayStr', src)
        self.assertIn('once they are due', src)


class TestPracticeQuizCanBeAnswered(unittest.TestCase):
    """The Quiz tab drew a question and a textarea and stopped there.

    No submit control, no listener, no call to /api/quiz/grade anywhere in
    practice.js — retrieval practice with the retrieval taken out.
    """

    def test_the_answer_reaches_the_grader(self):
        src = _read(_js, 'practice.js')
        self.assertIn("'/api/quiz/grade'", src)
        self.assertIn("method: 'POST'", src)

    def test_the_grader_endpoint_exists(self):
        app = _read(_root, 'services/web-ui/app.py')
        self.assertIn("@app.route('/api/quiz/grade', methods=['POST'])", app)

    def test_the_payload_matches_what_librarian_reads(self):
        src = _read(_js, 'practice.js')
        for field in ('question', 'answer', 'context', 'concept_uid', 'course_uid'):
            self.assertRegex(src, rf'\b{field}:',
                             f'the grade request omits {field}')
        lib = _read(_root, 'services/rag/librarian.py')
        grade = lib[lib.index('def quiz_grade_endpoint'):]
        for field in ('question', 'answer', 'context', 'concept_uid', 'course_uid'):
            self.assertIn(f'data.get("{field}"', grade)

    def test_there_is_a_control_to_submit_with(self):
        src = _read(_js, 'practice.js')
        self.assertIn("submit.addEventListener('click'", src)
        self.assertIn('Check my answer', src)

    def test_a_grading_failure_is_not_shown_as_a_wrong_answer(self):
        # librarian deliberately returns a non-2xx when the grader could not
        # run, so that no client turns an Ollama hiccup into a red cross.
        src = _read(_js, 'practice.js')
        self.assertIn('not been marked wrong', src)
        self.assertIn('!res.ok || !res.body.grade', src)


class TestOwnedScriptsParse(unittest.TestCase):
    """Cheap insurance: every file touched here is still valid JavaScript."""

    def test_every_owned_script_parses(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed on this machine')
        for name in OWNED_JS:
            r = subprocess.run([node, '--check', os.path.join(_js, name)],
                               capture_output=True, text=True)
            self.assertEqual(0, r.returncode, f'{name}: {r.stderr}')


if __name__ == '__main__':
    unittest.main()
