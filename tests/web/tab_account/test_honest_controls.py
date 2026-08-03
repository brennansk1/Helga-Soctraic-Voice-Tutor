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
    NAV_PAGES = ['/courses', '/learn', '/progress', '/practice', '/settings']

    # feature -> the page that must link to it
    NESTED_ENTRY_POINTS = {
        '/palace': '/learn',    # a mode of the open course, not a destination
        '/status': '/settings',  # an operator tool, not a learner tool
    }

    ADVERTISED_PAGES = NAV_PAGES + list(NESTED_ENTRY_POINTS)

    def test_every_nav_page_is_linked_from_the_nav(self):
        html = app.test_client().get('/').data.decode()
        nav = html.split('app-nav', 1)[-1].split('</nav>', 1)[0]
        for path in self.NAV_PAGES:
            self.assertIn(f'href="{path}"', nav,
                          f"{path} is advertised but not reachable from the nav")

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

    def test_the_nav_stays_at_six_destinations(self):
        """The A5.1 target shape. Nine links wrapped onto a second row at
        1280px and orphaned the settings icon; this is the regression guard."""
        html = app.test_client().get('/').data.decode()
        nav = html.split('app-nav', 1)[-1].split('</nav>', 1)[0]
        links = re.findall(r'class="nav-link[^"]*"', nav)
        self.assertEqual(
            len(links), 6,
            f"nav has {len(links)} destinations; A5.1 fixed it at six "
            f"(Home, Courses, Learn, Progress, Practice, Settings)"
        )

if __name__ == '__main__':
    unittest.main()
