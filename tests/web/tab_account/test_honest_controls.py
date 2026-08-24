"""A3 tests: every user-facing control must have a verified effect.

These guard the class of defect where the UI presents a control that silently
does nothing — either a route the frontend calls that was never proxied, or an
FSM event that returns success for a state change that never happened.
"""

import os
import re
import sys
import unittest
from unittest.mock import MagicMock, patch

_here = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_here, '../../../'))
_webui = os.path.join(_root, 'services/web-ui')
for p in (_root, _webui):
    if p not in sys.path:
        sys.path.insert(0, p)

for _mod in ("gevent", "gevent.monkey", "socketio", "flask_socketio"):
    if isinstance(sys.modules.get(_mod), MagicMock):
        del sys.modules[_mod]
        sys.modules.pop("app", None)
        sys.modules.pop("auth", None)

import app as webui_app  # noqa: E402

app = webui_app.app
app.config['TESTING'] = True


class TestProfileResetProxy(unittest.TestCase):
    """The Settings 'Reset Progress' button POSTs /api/profile/reset.

    librarian implemented it; web-ui never proxied it, so the button 404'd.
    """

    def test_reset_route_is_registered(self):
        rules = {r.rule for r in app.url_map.iter_rules()}
        self.assertIn(
            '/api/profile/reset', rules,
            "Settings 'Reset Progress' POSTs here; without a web-ui proxy the "
            "control silently 404s."
        )

    def test_reset_route_accepts_post(self):
        methods = set()
        for r in app.url_map.iter_rules():
            if r.rule == '/api/profile/reset':
                methods |= r.methods
        self.assertIn('POST', methods)

    def test_reset_forwards_to_rag_and_returns_its_response(self):
        client = app.test_client()
        fake = MagicMock()
        fake.json.return_value = {"status": "reset"}
        fake.status_code = 200
        with patch.object(webui_app.requests, 'post', return_value=fake) as post:
            rv = client.post('/api/profile/reset')
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_json(), {"status": "reset"})
        self.assertTrue(post.called, "reset must actually reach the RAG service")
        self.assertIn('/api/profile/reset', post.call_args[0][0])

    def test_reset_reports_upstream_failure_rather_than_faking_success(self):
        client = app.test_client()
        with patch.object(webui_app.requests, 'post',
                          side_effect=Exception("rag down")):
            rv = client.post('/api/profile/reset')
        self.assertEqual(rv.status_code, 502)
        self.assertIn('error', rv.get_json())


class TestNoVestigialToggleEvents(unittest.TestCase):
    """TOGGLE_MIC / TOGGLE_TTS / TOGGLE_TEXT_ONLY were removed, not implemented.

    The FSM does not own audio: TTS is per-message and client-side, mic capture
    is push-to-talk in session.js. The old handler swallowed these events and
    returned True — reporting success for a no-op. Nothing may reintroduce that
    without also implementing a real effect.
    """

    def test_fsm_does_not_silently_swallow_toggle_events(self):
        fsm_path = os.path.join(_root, 'services/core/fsm_logic.py')
        with open(fsm_path) as f:
            src = f.read()
        offending = [
            ln.strip() for ln in src.splitlines()
            if 'TOGGLE_TTS' in ln and not ln.lstrip().startswith('#')
        ]
        self.assertEqual(
            offending, [],
            "TOGGLE_* reappeared in fsm_logic as live code. If a global mute is "
            "wanted it belongs in the client beside the playback it controls; "
            "an FSM handler returning True is a control that lies."
        )




class TestNoFeatureReachableOnlyByUrl(unittest.TestCase):
    """An advertised feature with no way in is a broken promise.

    Memory Palace shipped this way twice: first the route redirected to home
    while the FSM kept the full MEMORY_PALACE state, then the page was restored
    but never linked, so it was reachable only by typing the URL. A5's gate
    forbids a recurrence, so it is asserted rather than remembered.
    """

    # A5.1 cut the nav from nine destinations to six. The rule is unchanged —
    # every advertised feature needs a way in that is not typing a URL — but
    # "the nav" is no longer the only legitimate entry point, because some
    # features are now modes of a parent surface rather than destinations.
    NAV_PAGES = ['/courses', '/degree', '/library', '/progress', '/practice',
                 '/settings']

    # Aliases kept for old links and bookmarks. They are NOT destinations and
    # must not occupy a slot in the nav: /test sat there promising graded exams
    # that do not exist — no exam route, no template, no endpoint — and
    # clicking it highlighted PRACTICE, which is the tell that the two were
    # never distinct. Removing a nav entry is exactly what this class exists to
    # catch, so the guarantee is replaced rather than dropped: each alias must
    # still land on a page the nav does link.
    ALIAS_REDIRECTS = {'/test': '/practice', '/quiz': '/practice',
                       '/review': '/practice'}

    # feature -> the page that must link to it
    NESTED_ENTRY_POINTS = {
        '/palace': '/learn',    # a mode of the open course, not a destination
        '/status': '/settings',  # an operator tool, not a learner tool
        # /setup is not a destination either -- you go there when the machine
        # will not run Helga. It had no inbound link from ANYWHERE, including
        # from the blocking gate that tells you the machine is broken, so the
        # page whose whole job is to fix that was reachable only by URL.
        # resources.js builds the link, so it is asserted as a script entry
        # point below rather than here.
    }

    # feature -> the script that must build a link to it.
    #
    # /learn left the nav deliberately: it requires a course_uid and bounced to
    # /courses without one, so as a tab it only worked AFTER visiting another
    # tab. Its real entry points are a course card and the Continue pill, both
    # rendered by script, so they can never appear in server-rendered HTML and
    # the assertion has to look where the link actually is. The guarantee is
    # unchanged — something must link there — only the place it is written.
    SCRIPT_ENTRY_POINTS = {
        '/learn': ['services/web-ui/static/js/courses.js',
                   'services/web-ui/static/js/build-guard.js'],
        '/setup': ['services/web-ui/static/js/resources.js'],
    }

    ADVERTISED_PAGES = (NAV_PAGES + list(NESTED_ENTRY_POINTS)
                        + list(SCRIPT_ENTRY_POINTS))

    def test_every_nav_page_is_linked_from_the_nav(self):
        html = app.test_client().get('/').data.decode()
        nav = html.split('app-nav', 1)[-1].split('</nav>', 1)[0]
        for path in self.NAV_PAGES:
            self.assertIn(f'href="{path}"', nav,
                          f"{path} is advertised but not reachable from the nav")

    def test_every_alias_lands_on_a_page_the_nav_links(self):
        """An alias may leave the nav; it may not become a dead end."""
        client = app.test_client()
        html = client.get('/').data.decode()
        nav = html.split('app-nav', 1)[-1].split('</nav>', 1)[0]
        for alias, target in self.ALIAS_REDIRECTS.items():
            resp = client.get(alias)
            self.assertIn(resp.status_code, (301, 302, 308),
                          f"{alias} no longer redirects; it is orphaned")
            self.assertIn(target, resp.headers.get('Location', ''),
                          f"{alias} redirects somewhere other than {target}")
            self.assertIn(f'href="{target}"', nav,
                          f"{alias} lands on {target}, which the nav does not link")

    def test_nested_features_are_linked_from_their_parent(self):
        """Removing something from the nav is only safe if it gained a home.

        Memory Palace has shipped reachable-by-URL-only TWICE. Taking it out of
        the nav without an entry point inside Learn would be the third time, so
        the parent link is asserted rather than trusted.
        """
        client = app.test_client()
        for feature, parent in self.NESTED_ENTRY_POINTS.items():
            page = client.get(parent)
            body = page.data.decode()
            self.assertIn(
                f'href="{feature}', body,
                f"{feature} was removed from the nav and {parent} does not "
                f"link to it — that is an advertised feature with no way in"
            )

    def test_script_rendered_features_have_an_entry_point(self):
        """A link built in JavaScript is still a link; a missing one is still
        a feature reachable only by typing a URL."""
        import os
        root = os.path.join(os.path.dirname(__file__), '..', '..', '..')
        for feature, scripts in self.SCRIPT_ENTRY_POINTS.items():
            found = []
            for rel in scripts:
                path = os.path.abspath(os.path.join(root, rel))
                with open(path, encoding='utf-8') as fh:
                    if feature in fh.read():
                        found.append(rel)
            self.assertTrue(
                found,
                f"{feature} is advertised, is not in the nav, and no script "
                f"in {scripts} builds a link to it — that is a feature "
                f"reachable only by typing a URL"
            )

    def test_advertised_pages_actually_render(self):
        for path in self.ADVERTISED_PAGES:
            rv = app.test_client().get(path)
            self.assertIn(rv.status_code, (200, 302),
                          f"{path} is linked but returns {rv.status_code}")

    def test_retired_tabs_still_resolve(self):
        """Quiz/Review/Schedule are bookmarked and linked from other pages.
        Folding them into Practice must not produce a 404."""
        client = app.test_client()
        for old, tab in (('/quiz', 'quiz'), ('/review', 'due'),
                         ('/schedule', 'upcoming')):
            rv = client.get(old)
            self.assertEqual(rv.status_code, 302,
                             f"{old} should redirect into Practice")
            self.assertIn('/practice', rv.headers.get('Location', ''))
            self.assertIn(f'tab={tab}', rv.headers.get('Location', ''),
                          f"{old} must land on the matching Practice state")

    def test_no_page_still_advertises_a_retired_tab(self):
        """Folding Quiz/Review/Schedule into Practice is not finished while
        other pages still link to them. Home kept three tiles pointing at the
        old tabs, so the front door advertised an information architecture the
        app no longer had — the links worked (they redirect) but the product
        described itself two different ways on two screens."""
        import glob
        offenders = []
        for path in glob.glob(os.path.join(_root, 'services/web-ui/templates/*.html')):
            if os.path.basename(path) == 'practice.html':
                continue        # owns the redirect targets
            with open(path) as f:
                body = f.read()
            for dead in ('href="/quiz"', 'href="/review"', 'href="/schedule"'):
                if dead in body:
                    offenders.append(f"{os.path.basename(path)} -> {dead}")
        self.assertEqual(offenders, [], f"links to retired tabs: {offenders}")

    # The real constraint is that the bar must not wrap onto a second row at
    # 1280px — nine links did, and orphaned the settings icon. The count is the
    # cheap proxy for it. Seven is the current shape: Learn left (it needed a
    # course_uid and bounced without one), Degree and Test arrived. Seven is
    # verified not to wrap at 1280px by the responsive sweep; raising this
    # number again without re-checking that is how the bar breaks twice.
    # Raised from 7 to 8 when /library was added, and only because the wrap
    # was re-measured rather than assumed. The original number came from NINE
    # links wrapping at 1280px; eight does not wrap. Measured in a browser at
    # 1440, 1280 and 1024 -- one row at every width, and at 1024 (tighter than
    # the width the wrap happened at) the links occupy 509px of a 550px nav
    # and the bar ends 127px clear of the header utilities.
    #
    # Raise this again only with the same measurement. The number is a proxy
    # for "the bar does not wrap"; bumping it to make a change pass is how the
    # guard stops guarding anything.
    MAX_NAV_DESTINATIONS = 8

    def test_the_nav_does_not_grow_until_it_wraps(self):
        """The A5.1 target shape. Nine links wrapped onto a second row at
        1280px and orphaned the settings icon; this is the regression guard."""
        html = app.test_client().get('/').data.decode()
        nav = html.split('app-nav', 1)[-1].split('</nav>', 1)[0]
        links = re.findall(r'class="nav-link[^"]*"', nav)
        self.assertLessEqual(
            len(links), self.MAX_NAV_DESTINATIONS,
            f"nav has {len(links)} destinations; more than "
            f"{self.MAX_NAV_DESTINATIONS} wrapped the bar onto a second row "
            f"at 1280px last time"
        )

if __name__ == '__main__':
    unittest.main()
