# Search priorities — what is consulted, in what order, and why

_Condition 7. Established from measurement, not preference: every ordering below
was set by something that went wrong when it was ordered differently._

---

## Phase 1 — Structure (what the course *is*)

Strictly ordered. Each step runs only if the one above it fails.

| # | source | gate it must clear | why it ranks here |
|---|---|---|---|
| 1 | **Sequenced exact-match textbook** — copied as the module spine | relevance ≥ 6.0 (exact title), **not alphabetical**, enough chapters | a real book's chapter order removes the topic-selection step where coverage was being lost |
| 2 | **Curated spine** (`tools/references/spines/`) | subject alias match | for subjects research cannot sequence: linear algebra has no OpenStax title and the Wikibooks entry is an alphabetical index |
| 3 | **Guided synthesis** from the research brief | brief has ≥1 chapter | the model authors structure, with real syllabi as evidence |
| 4 | **Unguided generation** | — | last resort, logged loudly |

**Before any of it:** parent-subject resolution — LLM, then Wikipedia categories,
then a degraded flag. A narrow topic ("the Pythagorean theorem") matches no book;
its discipline ("Geometry") has 31 chapters. Losing this step sends a build
unguided, which is what happened for weeks.

### Inside the brief, the ranking that matters

**Wikibooks → Wikiversity → OpenStax**, each **subject-fit gated** — fit is a
gate, not a score component. Without that, *College Algebra* beat *Linear
Algebra* 4.75 to 4.67 for a linear algebra course, because one shared token plus
a level marker outweighed being the subject.

* **Wikipedia sections** — vocabulary only. Explicitly rejected as a skeleton:
  `Cell biology` yields History, Techniques, Pathology, See also.
* **Internet Archive** — book *titles* as colour. Titles show how a field
  partitions itself; they cannot tell you what a course should contain.

**An alphabetical listing is never a spine.** It has complete coverage and no
teaching order, and copying one produced a course whose modules ran
*Addition…, Cofactors…, Diagonal Matrix, Identity Matrix* while scoring 100% on
coverage.

---

## Phase 2 — Content (what each concept *says*)

Weighted by **kind**, not by count (`compute_confidence`):

| source | weight | cap |
|---|---|---|
| open textbook (Wikibooks/Wikiversity/OpenStax) | 0.30 each | 0.60 |
| primary literature (Crossref, arXiv) | 0.25 each | 0.50 |
| Wikipedia | 0.40 | once |
| **web (SearXNG)** | **0.20 each** | **0.40** |
| domain archives (Met, Art Institute, LoC, Wikidata) | 0.30 | routed, never global |

Full confidence must be *earned* with a textbook or a primary source — a pile of
web pages tops out at 0.80 with Wikipedia. This has been the site of the same
bug twice, where a caller filtered for source kinds and silently dropped one, so
the kind a course most wants counted for nothing.

**Why SearXNG ranks last.** Measured: 8 realistic queries returned 10 results
each for the first four and **zero** for the last four; re-running the failures
first returned zero for all six. Engine-pool exhaustion, not topic difficulty. A
build issues 24–80 queries, so it is unavailable for most of one. Of the results
it did return, **32 of 39 were unvetted tier-3**.

It is kept because it is the only route to practical and contemporary subjects —
MDN, `docs.python.org`, RealPython are reachable no other way — but it is
demoted, and its failures are now **loud**: a CAPTCHA is no longer read as "no
results", and an empty result caused by dead engines is no longer cached for 24
hours.

---

## Phase 3 — Sourceless subjects (the iterative loop)

When no published syllabus exists:

1. the **model proposes** a checklist — short searchable topic names, 2–6 words
2. each uncovered item is searched through the research service
3. **coverage is measured deterministically**, never self-assessed
4. the loop stops on: covered · 2 dry rounds · budget

*The model proposes, deterministic code disposes.* Asking a model "is there
enough material?" invites the same optimism that produces padded courses.

A course built against an invented checklist is **labelled**: coverage against it
does not demonstrate parity with any published course.

---

## Efficiency: what makes repeated search affordable

| lever | effect |
|---|---|
| `_get_json` chokepoint cache — every MediaWiki, Internet Archive and OpenStax lookup | **73.93 s → 0.43 s** (172×) |
| brief cache (7 d) | 34.9 s → 0.00 s |
| research service internal cache | 24 h search, 7 d extraction |
| rate limiting with `Retry-After` | arXiv's documented 1-per-3s was being ignored entirely |

**Successes only, never misses.** A cached failure makes a transient outage
permanent for the whole TTL — the same absent-vs-zero error, at the storage
layer. Every cache has an opt-out so a test whose sources are deliberately dead
cannot be satisfied from disk.

---

## The rule underneath all of it

**Absence of evidence is not evidence of absence.** A throttled reply, a dead
endpoint, a 404 and a genuinely empty subject all return "nothing" — and treating
them alike is what sent a build unguided while reporting success. Every layer
here now distinguishes *we looked and found nothing* from *we could not look*:
`degraded` on the brief, `search_degraded` on concept research, `failed`/`throttled`
tallies at the fetch layer, and NOT RUN rather than 0 in every gate.
