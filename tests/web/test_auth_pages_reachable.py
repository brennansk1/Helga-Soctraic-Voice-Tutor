"""You must be able to reach the page that signs you in.

`csrf_protect` checked the token on every method, and /login and /signup answer
both GET and POST. The GET is what RENDERS THE FORM CARRYING THE TOKEN, so it
demanded a token from a browser that had no way to have one yet: a plain visit
to /login returned

    403  {"error": "CSRF token invalid or missing"}

Nobody could sign in or create an account, and /students and /parent redirect
to /login, so those dead-ended too. Found by walking every page route in a
browser rather than by reading the code -- it is invisible from the route
definition, which looks correct.

These tests deliberately do NOT set TESTING, because `csrf_protect` short
-circuits in test mode and would pass no matter what the decorator did.
"""
import importlib
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services/web-ui"))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ.setdefault("DATA_ROOT", str(tmp_path_factory.mktemp("authpages")))
    os.environ.setdefault("FLASK_SECRET_KEY", "test-only")
    app_mod = importlib.import_module("app")
    app_mod.app.config["TESTING"] = False        # the point of the test
    app_mod.app.config["PROPAGATE_EXCEPTIONS"] = True
    with app_mod.app.test_client() as c:
        yield c


@pytest.mark.parametrize("path", ["/login", "/signup"])
def test_the_sign_in_pages_render_without_a_token(client, path):
    """The page that hands out the token cannot require the token."""
    r = client.get(path)
    assert r.status_code == 200, (
        f"GET {path} returned {r.status_code}; nobody can sign in")
    assert b"csrf" in r.data.lower() or b"<form" in r.data.lower()


@pytest.mark.parametrize("path", ["/students", "/parent"])
def test_the_login_redirect_does_not_dead_end(client, path):
    """A redirect to a page that 403s is a dead end, not a redirect."""
    r = client.get(path)
    if r.status_code in (301, 302, 303, 307, 308):
        dest = r.headers["Location"]
        assert "/login" in dest
        assert client.get(dest).status_code == 200, (
            f"{path} redirects to {dest}, which does not load")


@pytest.mark.parametrize("path", ["/login", "/logout"])
def test_state_changing_posts_are_still_refused(client, path):
    """Exempting GET must not exempt anything that changes state."""
    assert client.post(path, json={}).status_code == 403


def test_safe_methods_are_the_only_exemption():
    from app import SAFE_METHODS

    assert SAFE_METHODS == frozenset({"GET", "HEAD", "OPTIONS"})
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert verb not in SAFE_METHODS
