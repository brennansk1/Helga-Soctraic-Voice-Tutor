"""Fetching documentation politely, concurrently, and only once.

WHAT THIS REPLACES
------------------
The first crawler used `urllib`, serially, with a fixed 0.25s delay and NO
CACHE. Three measured consequences:

  * 491 dbt pages took ~6 minutes of pure waiting, most of it idle
  * every re-crawl refetched all 491 pages — a rebuild of the same course
    hammered somebody else's docs host for content that had not changed
  * robots.txt was read only to find sitemaps, never to check whether a path
    was allowed. That is not a technicality; it is the one norm every
    documentation host is entitled to expect.

WHY CONCURRENT AND POLITE ARE NOT OPPOSITES
-------------------------------------------
The guidance is to tie crawl rate to the server's own response time: hold a
small number of connections open, and back off when latency rises. That is
faster than a fixed delay AND gentler under load, because a fixed delay is
simultaneously too slow when the host is idle and too aggressive when it is
struggling.

So: a bounded connection pool, a per-host minimum interval, and an adaptive
component that widens the interval when responses slow or a 429 arrives.

CACHING IS THE BIGGEST WIN
--------------------------
Documentation changes on the order of weeks. The existing research service
already caches extracted pages for 7 days (`CACHE_TTL_EXTRACT`) using
diskcache, and this reuses that same store rather than starting a second one —
two caches with different TTLs on the same content is how a "fresh" crawl ends
up serving week-old pages from the other cache.
"""
import asyncio
import logging
import os
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

USER_AGENT = os.environ.get("HELGA_USER_AGENT",
                            "Helga-Research/1.0 (course builder; +offline tutor)")

#: Simultaneous connections per host. Five is the number the scraping guidance
#: names as a safe starting point before watching for 429s.
DEFAULT_CONCURRENCY = 5

#: Floor on the gap between requests to one host, seconds. Raised adaptively.
MIN_INTERVAL = 0.15

#: Response time above which the crawler slows itself down. A host taking this
#: long is either large or struggling, and either way is not asking for more
#: parallel load.
SLOW_RESPONSE = 1.5

#: How long a fetched page stays good. Documentation moves in weeks, not hours,
#: and this matches the research service's existing extract TTL so the two
#: stores cannot disagree about freshness.
CACHE_TTL = 604800          # 7 days


def _cache():
    """The shared research cache, or None if unavailable."""
    try:
        try:  # imported as a package (tests, other services)
            from services.research.research_server import cache
        except ImportError:  # container: this image mounts the modules flat at /app
            from research_server import cache
        return cache
    except Exception:
        try:
            from diskcache import Cache
            return Cache(os.environ.get("HELGA_CACHE_DIR", "/tmp/helga-doc-cache"))
        except Exception:                    # pragma: no cover - defensive
            return None


class RobotsGate:
    """robots.txt, parsed once per host and remembered.

    Fails OPEN on an unreachable or malformed robots.txt: a host that does not
    publish one has not forbidden anything, and refusing to crawl because a
    file 404'd would be the absent-versus-zero error in the place that blocks
    every course.
    """

    def __init__(self):
        self._parsers = {}
        self._delays = {}

    def _parser(self, origin, robots_text):
        rp = RobotFileParser()
        try:
            rp.parse((robots_text or "").splitlines())
        except Exception:                    # pragma: no cover - defensive
            return None
        return rp

    def load(self, origin, robots_text):
        rp = self._parser(origin, robots_text)
        self._parsers[origin] = rp
        if rp is not None:
            try:
                d = rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*")
                if d:
                    # A host that ASKS for a delay gets it, even if that makes
                    # the crawl slow. Ignoring an explicit Crawl-delay is the
                    # rudest thing a crawler can do while still claiming to
                    # respect robots.txt.
                    self._delays[origin] = float(d)
            except Exception:
                pass

    def allowed(self, url):
        try:
            p = urlparse(url)
            rp = self._parsers.get(f"{p.scheme}://{p.netloc}")
            if rp is None:
                return True                  # fail open — see class docstring
            return rp.can_fetch(USER_AGENT, url)
        except Exception:                    # pragma: no cover - defensive
            return True

    def delay_for(self, url):
        try:
            p = urlparse(url)
            return self._delays.get(f"{p.scheme}://{p.netloc}", 0.0)
        except Exception:                    # pragma: no cover - defensive
            return 0.0


class PoliteFetcher:
    """Cached, concurrent, robots-aware fetching of one documentation host.

    Exposes a SYNCHRONOUS `fetch(url)` so it drops straight into the existing
    `doc_reader.crawl(entry, fetch)` contract, and `fetch_many(urls)` for the
    bulk path where concurrency actually pays.
    """

    def __init__(self, concurrency=DEFAULT_CONCURRENCY, use_cache=True,
                 respect_robots=True):
        self.concurrency = max(1, int(concurrency))
        self.use_cache = use_cache
        self.respect_robots = respect_robots
        self.robots = RobotsGate()
        self._interval = MIN_INTERVAL
        self._last = 0.0
        self._robots_loaded = set()
        self.stats = {"fetched": 0, "cached": 0, "blocked": 0, "failed": 0}

    # ----------------------------------------------------------------- cache
    def _key(self, url):
        return f"docpage:{url}"

    def _cached(self, url):
        if not self.use_cache:
            return None
        c = _cache()
        if c is None:
            return None
        try:
            return c.get(self._key(url))
        except Exception:                    # pragma: no cover - defensive
            return None

    def _store(self, url, html):
        if not self.use_cache or html is None:
            return
        c = _cache()
        if c is None:
            return
        try:
            c.set(self._key(url), html, expire=CACHE_TTL)
        except Exception:                    # pragma: no cover - defensive
            pass

    # ---------------------------------------------------------------- robots
    def _ensure_robots(self, url):
        if not self.respect_robots:
            return
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin in self._robots_loaded:
            return
        self._robots_loaded.add(origin)
        text = self._raw_get(f"{origin}/robots.txt", check=False) or ""
        self.robots.load(origin, text)

    # --------------------------------------------------------------- fetching
    def _raw_get(self, url, check=True):
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status != 200:
                    return None
                body = r.read().decode("utf-8", "replace")
            self._observe(time.time() - t0)
            return body
        except Exception as e:
            if check:
                logger.debug(f"[FETCH] {url}: {type(e).__name__}")
            return None

    def _observe(self, elapsed):
        """Adaptive pacing: a slow host earns a wider gap, a fast one a narrower."""
        if elapsed > SLOW_RESPONSE:
            self._interval = min(2.0, self._interval * 1.5)
        else:
            self._interval = max(MIN_INTERVAL, self._interval * 0.9)

    def fetch(self, url):
        """One page: cache, then robots, then network. Synchronous."""
        hit = self._cached(url)
        if hit is not None:
            self.stats["cached"] += 1
            return hit or None
        self._ensure_robots(url)
        if self.respect_robots and not self.robots.allowed(url):
            self.stats["blocked"] += 1
            logger.info(f"[FETCH] robots.txt disallows {url}")
            return None
        gap = max(self._interval, self.robots.delay_for(url))
        wait = gap - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        html = self._raw_get(url)
        self._last = time.time()
        if html is None:
            self.stats["failed"] += 1
        else:
            self.stats["fetched"] += 1
            self._store(url, html)
        return html

    # -------------------------------------------------------------- bulk path
    async def _fetch_many_async(self, urls):
        import aiohttp
        out, to_get = {}, []
        for u in urls:
            hit = self._cached(u)
            if hit is not None:
                out[u] = hit or None
                self.stats["cached"] += 1
            elif self.respect_robots and not self.robots.allowed(u):
                out[u] = None
                self.stats["blocked"] += 1
            else:
                to_get.append(u)
        if not to_get:
            return out

        sem = asyncio.Semaphore(self.concurrency)
        timeout = aiohttp.ClientTimeout(total=25)

        async def one(session, url):
            async with sem:
                # Per-host pacing INSIDE the semaphore: concurrency bounds how
                # many are in flight, the interval bounds how fast new ones
                # start. Both are needed — five connections opened
                # simultaneously every 0.15s is not polite, it is bursty.
                await asyncio.sleep(self._interval)
                try:
                    t0 = time.time()
                    async with session.get(url) as r:
                        if r.status == 429:
                            self._interval = min(3.0, self._interval * 2)
                            logger.info(f"[FETCH] 429 from {url}; backing off "
                                        f"to {self._interval:.2f}s")
                            return url, None
                        if r.status != 200:
                            return url, None
                        body = await r.text()
                    self._observe(time.time() - t0)
                    return url, body
                except Exception:
                    return url, None

        async with aiohttp.ClientSession(
                timeout=timeout, headers={"User-Agent": USER_AGENT}) as session:
            results = await asyncio.gather(*(one(session, u) for u in to_get))
        for url, body in results:
            out[url] = body
            if body is None:
                self.stats["failed"] += 1
            else:
                self.stats["fetched"] += 1
                self._store(url, body)
        return out

    def fetch_many(self, urls):
        """Many pages concurrently. Falls back to serial if aiohttp is absent."""
        urls = list(dict.fromkeys(urls))
        if not urls:
            return {}
        if urls:
            self._ensure_robots(urls[0])
        try:
            import aiohttp  # noqa: F401
        except ImportError:                  # pragma: no cover - aiohttp is a dep
            return {u: self.fetch(u) for u in urls}
        try:
            return asyncio.run(self._fetch_many_async(urls))
        except RuntimeError:
            # Already inside a loop (e.g. called from async code) — serial is
            # correct here rather than nesting event loops.
            return {u: self.fetch(u) for u in urls}
