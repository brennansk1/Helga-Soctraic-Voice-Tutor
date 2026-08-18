"""Rate limiting for the research service's outbound APIs.

These are somebody else's free public APIs and several publish a limit. The
project-specific reason to care: a throttled reply is indistinguishable from
"no such thing exists", and that ambiguity has already sent a build unguided
and cached a CAPTCHA as "nothing exists on the web about this concept".
"""

import os
import sys
import time
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.research import ratelimit as rl  # noqa: E402


class TestDocumentedLimits(unittest.TestCase):
    def setUp(self):
        rl._last_call.clear()
        rl._blocked_until.clear()
        rl._daily.clear()

    def test_arxiv_three_second_rule_is_enforced(self):
        """arXiv's Terms of Use ask for no more than one request every three
        seconds. We were previously sending them back to back."""
        u = "https://export.arxiv.org/api/query"
        assert rl.wait(u) == 0.0             # first call is free
        slept = rl.wait(u)
        assert slept >= 2.9, f"second arXiv call waited only {slept:.2f}s"

    def test_unknown_hosts_are_not_throttled(self):
        assert rl.wait("https://example.invalid/x") == 0.0
        assert rl.wait("https://example.invalid/x") == 0.0

    def test_hosts_are_limited_independently(self):
        rl.wait("https://export.arxiv.org/api/query")
        # a slow host must not stall an unrelated one
        assert rl.wait("https://api.crossref.org/works") < 0.5


class TestServerSignals(unittest.TestCase):
    def setUp(self):
        rl._last_call.clear()
        rl._blocked_until.clear()

    def test_retry_after_is_honoured(self):
        u = "https://archive.org/advancedsearch.php"
        rl.note_response(u, 429, {"Retry-After": "2"})
        assert rl.wait(u) >= 1.8

    def test_published_headers_override_our_guess(self):
        """Crossref reports its own limit; the published number should win over
        the conservative guess in the table."""
        u = "https://api.crossref.org/works"
        rl.note_response(u, 200, {"X-Rate-Limit-Limit": "2",
                                  "X-Rate-Limit-Interval": "1s"})
        assert abs(rl._MIN_INTERVAL["api.crossref.org"] - 0.5) < 1e-6

    def test_malformed_headers_do_not_raise(self):
        u = "https://api.crossref.org/works"
        rl.note_response(u, 200, {"X-Rate-Limit-Limit": "banana",
                                  "X-Rate-Limit-Interval": "?"})
        rl.note_response(u, 429, {"Retry-After": "soon"})   # falls back to 5s


class TestContactIsNeverFabricated(unittest.TestCase):
    """A fake mailto is worse than none: the polite pool is checking for a
    reachable contact, and noreply@localhost is not one."""

    def tearDown(self):
        os.environ.pop("HELGA_CONTACT", None)

    def test_no_contact_configured_means_no_claim(self):
        os.environ.pop("HELGA_CONTACT", None)
        assert "mailto" not in rl.user_agent()
        assert rl.contact_param() == {}

    def test_real_contact_is_advertised(self):
        os.environ["HELGA_CONTACT"] = "someone@example.org"
        assert "mailto:someone@example.org" in rl.user_agent()
        assert rl.contact_param() == {"mailto": "someone@example.org"}


class TestDailyCap(unittest.TestCase):
    def setUp(self):
        rl._daily.clear()

    def test_uncapped_host_always_allowed(self):
        assert rl.count_call("https://api.crossref.org/works") is True

    def test_documented_cap_is_counted_and_enforced(self):
        u = "https://api.openalex.org/works"
        rl._DAILY_CAP[u.split("/")[2]] = 3
        try:
            assert [rl.count_call(u) for _ in range(3)] == [True, True, True]
            assert rl.count_call(u) is False
            assert rl.usage()["api.openalex.org"]["used"] == 4
        finally:
            rl._DAILY_CAP["api.openalex.org"] = 100_000


if __name__ == "__main__":
    unittest.main()
