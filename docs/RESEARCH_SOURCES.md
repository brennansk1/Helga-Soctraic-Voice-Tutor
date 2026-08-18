# Where the research service gets its evidence

_Reviewed and measured 2026-08-18 on the Mac Mini. Every number here came from
running the live service, not from reading its code._

## The question that prompted this

"Should SearXNG be removed and replaced with more public APIs?"

Short answer: **the service was already mostly public APIs — SearXNG is 1 of 10
sources and the lowest-weighted one. Don't delete it, but stop trusting its
silence.** The urgent problem was never which sources exist; it was that a dead
source and an empty subject looked identical.

## Current sources

| source | kind | used for | confidence weight |
|---|---|---|---|
| **OpenStax** | open textbook | chapter structure (real syllabi) | 0.30 ea, cap 0.60 |
| Wikibooks | open textbook | chapter structure | 0.30 ea, cap 0.60 |
| Wikiversity | course shapes | how a course is sequenced | 0.30 ea, cap 0.60 |
| Crossref | primary literature | DOI-backed citations | 0.25 ea, cap 0.50 |
| arXiv | preprints | STEM citations | 0.25 ea, cap 0.50 |
| Wikipedia | tertiary | grounding text, terminology | 0.40 once |
| Internet Archive | book metadata | how a field partitions itself | — |
| Wikidata, LoC, Met, Art Institute | domain-routed archives | art/history primary material | 0.30 |
| **SearXNG** | meta-search | open web, practical/vocational topics | **0.20 ea, cap 0.40** |

## What SearXNG measured at

8 realistic concept queries against the live container:

* First 4 queries returned 10 results each. **Last 4 returned zero.**
* Re-running the failing queries *first* returned zero for **all six**,
  including ones that had worked minutes earlier. So it is engine-pool
  exhaustion, not topic difficulty.
* `unresponsive_engines` told the story: startpage suspended (CAPTCHA), brave
  suspended (too many requests), wikipedia timeout, then duckduckgo — which
  supplied **all 39** results single-handed — CAPTCHA'd.
* Of those 39, only 7 were tier-1. **32 were tier-3, unvetted open web.**

A course build issues 24–80 queries. SearXNG dies around query 14, so for most
of a build it contributes nothing. The same six topics against the public APIs:
**23 of 24 calls succeeded.**

## Why it is still not deleted

Every other source is encyclopedic or academic. SearXNG is the only route to
practical, vocational and contemporary topics — and the tier table admits it,
listing MDN, `docs.python.org`, RealPython and Investopedia as tier-2. Those are
reachable only by web search. Delete it and Helga stays strong on the
Pythagorean theorem while going blind on "React hooks" or "Excel pivot tables".

It is now demoted rather than removed: best-effort, and **loud when it fails**.

## What actually got fixed

1. **A CAPTCHA is no longer read as "nothing exists."** SearXNG answers total
   engine failure with HTTP 200 and an empty list, so "every engine is blocked"
   and "nobody has written about this" were the same value. `unresponsive_engines`
   distinguishes them; `research_concept` now returns `search_degraded` and
   `search_stats`. Confidence could not carry this signal: a concept grounded by
   textbooks and Wikipedia scores 1.0 with zero web sources — correct, and
   therefore silent about whether the web leg ran.
2. **That empty list is no longer cached.** It had a 24-hour TTL, so a transient
   block became a day-long "there is nothing on the web about this concept".
3. **Rate limits are now respected** — see `services/research/ratelimit.py`.
   arXiv publishes one request per three seconds and we were ignoring it
   outright. Crossref's `X-Rate-Limit-*` headers now override our guesses, and
   the table marks which limits are documented and which are conservatism.
4. **No more fabricated contact address.** Crossref was being sent
   `mailto:noreply@localhost`. A fake address is worse than none — the polite
   pool is checking for a reachable contact. `HELGA_CONTACT` supplies a real
   one; unset, we make no claim.
5. **OpenStax added** — 129 peer-reviewed books with full chapter trees, the
   highest-weighted source kind. See the commit for the two ranking bugs this
   surfaced.

## Evaluated and rejected

* **LibreTexts** — 403 Forbidden on every subject host (chem/math/bio). Not
  usable without credentials.
* **OpenAlex** — works fine, but a search for the Pythagorean theorem returns
  "Pythagorean fuzzy subsets" and similar. That is the research frontier, and
  this codebase deliberately de-emphasises it: a course teaches the settled
  canon. It would add volume to a source kind we already have two of.

## A note on "fully offline"

`CLAUDE.md` describes Helga as fully offline. The research service is not, and
SearXNG least of all: self-hosting it makes it *look* local while it proxies to
Google/Brave/DDG, which is exactly why it gets CAPTCHA'd. Worth reconciling that
claim with reality, or scoping it to "offline at tutoring time, online at build
time" — which is what the system actually does.
