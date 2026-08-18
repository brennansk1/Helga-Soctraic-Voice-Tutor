# Caching in course creation — a standing trend, not a one-off fix

**Observed repeatedly during the 2026-08-18 pass: caching is under-used across
course creation, and the cost shows up as latency AND as wrong answers.**

Treat this page as a checklist when touching the build path.

## Why it is not only a speed concern

Wikimedia throttles bursts, and a throttled reply is indistinguishable from
"this book does not exist" at the lookup layer. That ambiguity is what sent a
real build UNGUIDED: the third candidate query in a burst returned empty, was
read as *absence of evidence*, and the course was generated with no syllabus to
follow — missing the formula it was about. Caching removes most of the burst, so
the ambiguity mostly stops arising. **A cache here buys correctness, not just
time.**

## The rule that keeps a cache honest

**Cache successes only.** A miss is ambiguous — "nothing exists" vs "we were
throttled / the service was down" — and caching the second kind makes a
transient failure permanent for the whole TTL. This is the same absent-vs-zero
confusion that made a 6-title brief look like evidence, and that scored a
missing HelgaBench key as 1.

Corollary: any cache must be bypassable, or it will silently satisfy a test
whose sources were deliberately made to fail. Every cache below has an opt-out.

## Current coverage

| layer | what | TTL | opt-out |
|---|---|---|---|
| `syllabus_sources._get_json` | **all** MediaWiki + Internet Archive + OpenStax lookups — `_search_book`, `_chapters_of`, `_wikipedia_sections`, `_wikiversity_course_shapes`, `_internet_archive_books`, `_openstax_release/_catalogue/_chapters` | 7 d | `HELGA_RESEARCH_CACHE=0` |
| `curriculum_research.curriculum_brief` | assembled brief per (topic, level, broader) | 7 d | `use_cache=False`, `HELGA_BRIEF_CACHE=0` |
| `research_server` | web search / page extraction | 24 h / 7 d | — |

Measured: `subject_outline('The Pythagorean Theorem')` 73.93 s → **0.43 s**
(172×). `curriculum_brief` 34.9 s → **0.00 s**.

## Still uncached — candidates, with the caveat that matters

0. **Done since this was written:** the SearXNG search cache no longer stores an
   empty result caused by a CAPTCHA — it had a 24 h TTL, so a transient block
   became a day-long "nothing exists on the web about this concept". That is the
   successes-only rule below, violated where it cost most.
1. **The parent-subject lookup.** `"What academic subject is X part of?"` is a
   tiny, effectively deterministic LLM call made once per build and identical
   across rebuilds of the same topic. Safe to cache; pure win.
2. **Domain classification / constraints.** Same shape as above.
3. **Hydration research per concept.** The builder POSTs the research service
   per concept. The service caches internally (24 h/7 d), so the round trip is
   cheap — but a builder-side cache would skip it entirely on rebuild.
4. **General LLM response caching — do NOT do this blindly.** Caching on
   (prompt, model, temperature) would make retries and rebuilds free, but it
   also makes every course on a topic *identical*. That is right for a
   reproducible catalog build and wrong for a learner asking for their own
   course. If it is added, scope it to the deterministic classification calls
   above, or make it explicit per build (`--reproducible`), never a global
   default.

## When adding a new network or LLM call to the build path

Ask, in order: is it repeated across concepts/modules? across rebuilds? is the
answer stable for days? can a failure be mistaken for a legitimate empty result?
If the last answer is yes, cache the success and never the miss.
