"""End-to-end UI journeys against a real, running Helga stack.

WHY THIS FILE WAS REWRITTEN
---------------------------
The previous version had eight tests and effectively asserted nothing:

  - every one hardcoded http://localhost:5050 and failed on
    ERR_CONNECTION_REFUSED, because nothing ever started the app. Permanently
    red, and therefore ignored.
  - the selectors had rotted past the UI. It clicked a "Start Journey" button
    that no longer exists, opened a `#settings-modal` replaced long ago by a
    Settings *page*, and switched to "Cyberpunk" and "Reader" themes that were
    removed (there is light and dark). It visited /test and /wizard, neither of
    which is a page any more.
  - nearly every assertion sat behind `if element.is_visible():`, so when a
    selector stopped matching, the test quietly passed instead of failing.

A suite that cannot run, and that passes when its target is missing, is worse
than none: it reports coverage that does not exist. These are rewritten against
the six-tab IA, they run on a stack the fixture starts (see conftest.py), and
they assert unconditionally.

Skips appear only where a precondition is genuinely absent (no built course on
disk), and each one says so.
"""

import re

import httpx
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from playwright.sync_api import Page, expect


# --- shell ------------------------------------------------------------------

class TestAppShell:

    def test_home_renders_with_the_six_tab_nav(self, page: Page, base_url):
        page.goto(base_url + "/")
        expect(page.locator("#app-nav .nav-link")).to_have_count(6)
        for label in ("Home", "Courses", "Learn", "Progress", "Practice", "Settings"):
            expect(page.locator(f"#app-nav .nav-link:text-is('{label}')")).to_be_visible()

    def test_every_nav_destination_actually_loads(self, page: Page, base_url):
        for path in ("/", "/courses", "/progress", "/practice", "/settings"):
            resp = page.goto(base_url + path)
            assert resp.status < 400, f"{path} returned {resp.status}"
            expect(page.locator("#app-nav")).to_be_visible()

    def test_retired_tabs_redirect_into_practice(self, page: Page, base_url):
        for old, tab in (("/quiz", "quiz"), ("/review", "due"),
                         ("/schedule", "upcoming")):
            page.goto(base_url + old)
            expect(page).to_have_url(re.compile(r".*/practice\?tab=" + tab))

    def test_the_page_never_scrolls_sideways(self, page: Page, base_url):
        """Horizontal overflow is the commonest way a layout looks broken, and
        it is invisible in a screenshot taken at the width you designed for."""
        for width in (1440, 1100, 820, 390):
            page.set_viewport_size({"width": width, "height": 900})
            for path in ("/", "/courses", "/progress", "/practice"):
                page.goto(base_url + path)
                page.wait_for_timeout(250)
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth >"
                    " document.documentElement.clientWidth")
                assert not overflow, f"{path} overflows at {width}px"

    def test_nav_collapses_to_a_hamburger_on_a_phone(self, page: Page, base_url):
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(base_url + "/progress")
        expect(page.locator("#hamburger-btn")).to_be_visible()
        expect(page.locator("#app-nav .nav-link").first).to_be_hidden()
        page.locator("#hamburger-btn").click()
        expect(page.locator("#app-nav .nav-link").first).to_be_visible()


# --- theme ------------------------------------------------------------------

class TestTheming:

    def test_theme_toggle_flips_and_persists(self, page: Page, base_url):
        page.goto(base_url + "/")
        start = page.evaluate("document.documentElement.getAttribute('data-theme')")
        page.locator("#header-theme-toggle").click()
        page.wait_for_timeout(300)
        flipped = page.evaluate("document.documentElement.getAttribute('data-theme')")
        assert flipped != start, "toggling did not change the theme"
        page.reload()
        page.wait_for_timeout(200)
        after = page.evaluate("document.documentElement.getAttribute('data-theme')")
        assert after == flipped, "theme did not survive a reload"

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_both_themes_render_every_surface(self, page: Page, base_url, theme):
        page.goto(base_url + "/")
        page.evaluate(f"localStorage.setItem('helga-theme','{theme}')")
        for path in ("/", "/courses", "/progress", "/practice", "/settings"):
            page.goto(base_url + path)
            page.wait_for_timeout(250)
            same = page.evaluate("""() => {
                const b = getComputedStyle(document.body);
                return b.color === b.backgroundColor;
            }""")
            assert not same, f"{path} paints text in the background colour ({theme})"


# --- assets -----------------------------------------------------------------

class TestNoBrokenAssets:
    """A missing image does not raise; it just looks broken. helga-avatar.svg
    and user-avatar.svg were referenced from eight places and did not exist."""

    def test_no_static_asset_404s(self, page: Page, base_url):
        missing = []
        page.on("response", lambda r: missing.append(r.url)
                if r.status == 404 and '/static/' in r.url else None)
        for path in ("/", "/courses", "/progress", "/practice", "/settings", "/palace"):
            page.goto(base_url + path)
            page.wait_for_timeout(400)
        assert missing == [], f"404s on static assets: {sorted(set(missing))}"

    def test_no_uncaught_javascript_errors(self, page: Page, base_url):
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for path in ("/", "/courses", "/progress", "/practice", "/settings"):
            page.goto(base_url + path)
            page.wait_for_timeout(400)
        assert errors == [], f"uncaught JS errors: {errors}"


# --- practice ---------------------------------------------------------------

class TestPractice:

    def test_tabs_switch_and_stay_linkable(self, page: Page, base_url):
        page.goto(base_url + "/practice")
        expect(page.locator("#panel-due")).to_be_visible()
        page.locator("#tab-upcoming").click()
        expect(page).to_have_url(re.compile(r".*tab=upcoming"))
        expect(page.locator("#panel-upcoming")).to_be_visible()
        page.go_back()
        expect(page.locator("#panel-due")).to_be_visible()

    def test_a_deep_link_opens_the_right_tab(self, page: Page, base_url):
        page.goto(base_url + "/practice?tab=quiz")
        expect(page.locator("#panel-quiz")).to_be_visible()

    def test_tabs_are_keyboard_operable(self, page: Page, base_url):
        page.goto(base_url + "/practice")
        page.locator("#tab-due").focus()
        page.keyboard.press("ArrowRight")
        expect(page.locator("#panel-quiz")).to_be_visible()


# --- progress ---------------------------------------------------------------

class TestProgress:

    def test_progress_reports_a_real_state(self, page: Page, base_url):
        """Either totals, or an explicit "nothing yet". Never an empty shell."""
        page.goto(base_url + "/progress")
        page.wait_for_timeout(1200)
        totals = page.locator("#progress-totals .progress-stat")
        empty = page.locator("#progress-empty")
        assert totals.count() > 0 or empty.is_visible(), \
            "Progress rendered neither data nor an empty state"

    def test_no_raw_uids_are_shown_to_the_learner(self, page: Page, base_url):
        """Failed builds were listed as 'course_c6620699 — 0 / 0 known'."""
        page.goto(base_url + "/progress")
        page.wait_for_timeout(1200)
        body = page.locator("body").inner_text()
        assert not re.search(r"\bcourse_[0-9a-f]{8}\b", body), \
            "a raw course uid is visible on Progress"


# --- courses ----------------------------------------------------------------

class TestCourses:

    def test_courses_page_lists_or_explains_itself(self, page: Page, base_url):
        page.goto(base_url + "/courses")
        page.wait_for_timeout(1200)
        assert page.locator(".course-card").count() > 0 \
            or page.locator(".empty-icon").count() > 0, \
            "Courses rendered neither cards nor an empty state"

    def test_an_empty_course_offers_no_button_that_must_fail(self, page: Page, base_url):
        """A course with no concepts used to show 'Start Learning' next to
        '0 Concepts' — an action that could only error."""
        page.goto(base_url + "/courses")
        page.wait_for_timeout(1200)
        expect(page.locator(".course-card:has(.course-card-empty) "
                            "button:text-is('Start Learning')")).to_have_count(0)


# --- learn path -------------------------------------------------------------

class TestLearnPath:

    def _built_course_uid(self, page, base_url):
        page.goto(base_url + "/courses")
        page.wait_for_timeout(1200)
        ready = page.locator(".course-card:not(:has(.course-card-empty))")
        if ready.count() == 0:
            pytest.skip("no fully-built course on disk")
        href = ready.first.locator("button[aria-label^='View structure']").get_attribute("onclick") or ""
        m = re.search(r"uid=(course_[0-9a-f]+)", href)
        if not m:
            pytest.skip("could not determine a course uid from the card")
        return m.group(1)

    def _open_the_path(self, page, base_url):
        """Land on the course MAP.

        Clicking "Start Learning" on a card navigates straight into a concept
        session (`&concept_uid=...`) — it resumes where you left off — which
        hides the path view and the learn header. Tests about the path have to
        ask for the path.
        """
        uid = self._built_course_uid(page, base_url)
        page.goto(base_url + "/learn?course_uid=" + uid)
        page.wait_for_timeout(2800)
        expect(page.locator("#path-view")).to_be_visible()
        return uid

    def test_the_path_renders_labelled_nodes(self, page: Page, base_url):
        self._open_the_path(page, base_url)
        nodes = page.locator(".path-node")
        assert nodes.count() > 0, "the learn path rendered no nodes"
        labels = page.locator(".node-label")
        assert labels.count() == nodes.count(), \
            "every node needs a permanent label; titles used to be hover-only"
        assert labels.first.inner_text().strip(), "node label is empty"

    def test_back_returns_to_the_path_in_place(self, page: Page, base_url):
        """A5.4 — "Back to course map" used to `window.location` away to
        /course/view, so it left the Learn tab entirely: different page,
        different nav highlight, and the path the learner was walking was
        gone. The course map IS the path view."""
        self._open_the_path(page, base_url)
        page.locator(".path-node:not(.locked)").first.click()
        page.wait_for_timeout(2000)
        expect(page.locator("#session-view")).to_be_visible()

        page.locator("#back-to-path-btn-session").click()
        page.wait_for_timeout(2000)

        expect(page.locator("#path-view")).to_be_visible()
        expect(page).to_have_url(re.compile(r".*/learn\?course_uid=.*"))
        assert "concept_uid=" not in page.url, \
            "leaving concept_uid in the URL re-enters the session on reload"
        assert page.locator(".path-node").count() > 0, \
            "the path did not re-render after the session"

    def test_memory_palace_is_reachable_from_learn(self, page: Page, base_url):
        """It has shipped reachable-by-URL-only twice."""
        self._open_the_path(page, base_url)
        link = page.locator("#palace-mode-link")
        expect(link).to_be_visible()
        assert "course_uid=" in (link.get_attribute("href") or ""), \
            "Palace must open the course you are in, not whatever the FSM saw last"


# --- ask --------------------------------------------------------------------

class TestAsk:
    """A5.2 — Socratic dialogue existed ONLY inside a concept node; there was no
    free-chat endpoint anywhere. A learner could not ask a question spanning
    their courses, which is the largest gap versus just using a chatbot."""

    def test_ask_is_reachable_from_every_page(self, page: Page, base_url):
        for path in ("/", "/courses", "/progress", "/practice", "/settings"):
            page.goto(base_url + path)
            expect(page.locator("#ask-open-btn")).to_be_visible()

    def test_the_panel_opens_and_traps_focus(self, page: Page, base_url):
        page.goto(base_url + "/progress")
        page.locator("#ask-open-btn").click()
        expect(page.locator("#ask-panel")).to_be_visible()
        assert page.evaluate(
            "document.activeElement && document.activeElement.id") == "ask-input", \
            "opening a dialog without moving focus into it strands keyboard users"

    def test_escape_closes_and_restores_focus(self, page: Page, base_url):
        page.goto(base_url + "/progress")
        page.locator("#ask-open-btn").click()
        expect(page.locator("#ask-panel")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#ask-panel")).to_be_hidden()
        assert page.evaluate(
            "document.activeElement && document.activeElement.id") == "ask-open-btn", \
            "focus must return to whatever opened the dialog"

    def test_keyboard_shortcut_opens_it(self, page: Page, base_url):
        page.goto(base_url + "/progress")
        page.keyboard.press("ControlOrMeta+k")
        expect(page.locator("#ask-panel")).to_be_visible()

    def test_an_empty_question_does_nothing(self, page: Page, base_url):
        page.goto(base_url + "/progress")
        page.locator("#ask-open-btn").click()
        page.locator("#ask-form button[type=submit]").click()
        page.wait_for_timeout(400)
        expect(page.locator(".ask-turn")).to_have_count(0)


# --- settings ---------------------------------------------------------------

class TestSettings:

    def test_settings_exposes_system_status(self, page: Page, base_url):
        """Status stopped being a tab in A5.1. If Settings does not link to it,
        it is reachable only by URL."""
        page.goto(base_url + "/settings")
        expect(page.locator("a[href='/status']")).to_be_visible()

    def test_status_page_loads(self, page: Page, base_url):
        assert page.goto(base_url + "/status").status < 400


# --- backend fuzzing --------------------------------------------------------

@settings(max_examples=20, deadline=None,
          suppress_health_check=[HealthCheck.too_slow,
                                 HealthCheck.function_scoped_fixture])
@given(search_query=st.text(max_size=100))
def test_backend_search_resilience_with_hypothesis(base_url, search_query):
    """Hypothesis generates edge-case queries; the endpoint must not 500."""
    with httpx.Client(timeout=10) as client:
        resp = client.get(base_url + "/api/due_concepts",
                          params={"topic": search_query})
    assert resp.status_code in (200, 400, 404, 502), \
        f"{resp.status_code} for query {search_query!r}"
