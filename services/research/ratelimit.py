"""Per-host rate limiting for the research service's outbound API calls.

WHY THIS EXISTS
---------------
Every source the research service uses is somebody else's free public API, and
several of them publish a rate limit we were simply not honouring. A course
build issues hundreds of lookups in a burst, which is the exact traffic shape
these limits exist to stop.

This is not only politeness. The measured failure mode on this project is that a
throttled reply is indistinguishable from "no such thing exists" -- that is what
sent a build unguided while reporting success, and it is what made SearXNG cache
a CAPTCHA as "there is nothing on the web about this concept" for 24 hours.
Staying under the limit is the cheapest way to stop manufacturing that ambiguity.

WHAT IS DOCUMENTED VS WHAT IS A GUESS
-------------------------------------
Marked per entry below, because the distinction matters when someone later wants
to tune these. Treating a guess as a published spec is how you end up confidently
violating a real limit.

  * arXiv -- DOCUMENTED and hard: the API Terms of Use ask for no more than one
    request every three seconds, from a single connection. This is the strictest
    limit we are subject to and we were previously ignoring it entirely.
  * Crossref -- DOCUMENTED as a two-pool system rather than a fixed number. A
    request with a real mailto goes to the "polite pool" and gets better
    service; without one you are in the shared public pool. Crossref returns
    X-Rate-Limit-Limit / X-Rate-Limit-Interval headers, which we honour.
  * Wikimedia (Wikipedia / Wikibooks / Wikiversity / Wikidata) -- the Action API
    publishes no hard anonymous cap, but the UA policy asks for a descriptive
    User-Agent with contact info, and the guidance for automated clients is to
    make requests SERIALLY rather than in parallel. Interval below is a
    conservative choice, not a published number.
  * Internet Archive -- NOT documented. They throttle, and observation is all we
    have. Conservative.
  * Met Museum -- DOCUMENTED at 80 requests/second, which we will never approach.
  * Library of Congress, Art Institute of Chicago -- not clearly documented;
    conservative.
  * OpenAlex -- DOCUMENTED at 10/second and 100,000/day, polite pool via mailto.
    Listed now so it is already governed if/when it gets wired in.

CONTACT ADDRESS
---------------
Crossref and OpenAlex give better service to requests carrying a real mailto,
and Wikimedia asks for contact info in the User-Agent. We previously sent
`mailto:noreply@localhost` -- a fake address is worse than none: it claims to be
a contact point that cannot be contacted, which is precisely what the polite
pool is checking for. Set HELGA_CONTACT to a real address to opt in; with it
unset we omit the claim rather than fabricate one.
"""

import logging
import os
import threading
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Minimum seconds between requests to each host. See the module docstring for
# which of these are published limits and which are deliberate conservatism.
_MIN_INTERVAL = {
    "export.arxiv.org": 3.0,        # DOCUMENTED, hard
    "arxiv.org": 3.0,
    "api.crossref.org": 0.1,        # header-governed; adapts at runtime
    "api.openalex.org": 0.1,        # DOCUMENTED 10/s
    "archive.org": 1.0,             # undocumented; observed throttling
    "web.archive.org": 1.0,
    "en.wikipedia.org": 0.2,        # conservative; policy asks for serial use
    "en.wikibooks.org": 0.2,
    "en.wikiversity.org": 0.2,
    "www.wikidata.org": 0.2,
    "openstax.org": 0.3,            # not documented; conservative
    "www.loc.gov": 0.5,
    "api.artic.edu": 0.5,
    "collectionapi.metmuseum.org": 0.05,   # DOCUMENTED 80/s
}

# Hosts with a published per-day allowance. Counted so we notice before they do.
_DAILY_CAP = {
    "api.openalex.org": 100_000,    # DOCUMENTED
}

_DEFAULT_INTERVAL = 0.0   # unknown hosts are not throttled by default

_lock = threading.Lock()
_last_call = {}      # host -> monotonic timestamp of the last request
_blocked_until = {}  # host -> monotonic timestamp from a Retry-After
_daily = {}          # host -> [utc_day, count]


def user_agent(product="Helga/1.0"):
    """UA string carrying real contact info when we have it, and none when we
    don't. Wikimedia's policy asks for contact details; Crossref uses them to
    route to the polite pool."""
    contact = (os.getenv("HELGA_CONTACT") or "").strip()
    if contact:
        return f"{product} (Socratic Tutor; mailto:{contact})"
    return f"{product} (Socratic Tutor)"


def headers(product="Helga/1.0"):
    return {"User-Agent": user_agent(product)}


def contact_param():
    """`{'mailto': ...}` for APIs that read it as a query param (Crossref,
    OpenAlex), or `{}` when no real address is configured."""
    contact = (os.getenv("HELGA_CONTACT") or "").strip()
    return {"mailto": contact} if contact else {}


def _host(url):
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def wait(url):
    """Block until this host may be called again. Returns seconds slept.

    Deliberately blocking rather than raising: the caller wants the data, and a
    lookup that returns None because we declined to wait 200 ms is
    indistinguishable downstream from a lookup that found nothing -- the exact
    ambiguity this module exists to reduce.
    """
    host = _host(url)
    if not host:
        return 0.0
    interval = _MIN_INTERVAL.get(host, _DEFAULT_INTERVAL)

    with _lock:
        now = time.monotonic()
        # A host we have never called must not wait. `.get(host, 0.0)` looked
        # right and was not: time.monotonic() is measured from process start,
        # not from boot, so early in the process "0.0" is a moment in the recent
        # PAST and the first call to every limited host slept the whole
        # interval for nothing -- 3 s for arXiv on every build.
        prev = _last_call.get(host)
        earliest = max((prev + interval) if prev is not None else 0.0,
                       _blocked_until.get(host, 0.0))
        delay = max(0.0, earliest - now)
        # Reserve this slot before releasing the lock, so concurrent callers
        # queue behind each other instead of all computing the same start time.
        _last_call[host] = now + delay

    if delay > 0:
        if delay > 1.0:
            logger.debug(f"rate limit: waiting {delay:.1f}s for {host}")
        time.sleep(delay)
    return delay


def note_response(url, status=None, resp_headers=None):
    """Feed a response's rate-limit signals back in.

    Honours Retry-After on 429/503, and adapts to Crossref-style
    X-Rate-Limit-Limit / X-Rate-Limit-Interval so the published limit wins over
    the guess in the table above.
    """
    host = _host(url)
    if not host:
        return
    h = {k.lower(): v for k, v in (resp_headers or {}).items()}

    if status in (429, 503):
        try:
            retry = float(h.get("retry-after", 0) or 0)
        except (TypeError, ValueError):
            retry = 0.0
        retry = retry or 5.0
        with _lock:
            _blocked_until[host] = time.monotonic() + min(retry, 300.0)
        logger.warning(f"{host}: {status}, backing off {min(retry, 300.0):.0f}s")
        return

    limit, interval = h.get("x-rate-limit-limit"), h.get("x-rate-limit-interval")
    if limit and interval:
        try:
            n = float(limit)
            secs = float(str(interval).rstrip("s") or 1)
            if n > 0 and secs > 0:
                _MIN_INTERVAL[host] = max(secs / n, 0.01)
        except (TypeError, ValueError):
            pass


def count_call(url):
    """Track usage against a published daily cap. Returns True if still under.

    Warns at 90% rather than only at the wall: a build that dies at the cap
    mid-course is far more expensive than one that was told to slow down.
    """
    host = _host(url)
    cap = _DAILY_CAP.get(host)
    if not cap:
        return True
    day = int(time.time() // 86400)
    with _lock:
        d = _daily.get(host)
        if not d or d[0] != day:
            d = _daily[host] = [day, 0]
        d[1] += 1
        used = d[1]
    if used == int(cap * 0.9):
        logger.warning(f"{host}: {used}/{cap} of today's documented quota used")
    if used > cap:
        logger.error(f"{host}: daily cap {cap} exceeded ({used}) — refusing")
        return False
    return True


def usage():
    """Current per-host call counts against documented daily caps."""
    with _lock:
        return {h: {"used": c, "cap": _DAILY_CAP.get(h)}
                for h, (_, c) in _daily.items()}
