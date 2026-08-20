# Mode A (Personal / Scholar) — completion scoreboard

**The single place to check how close personal mode is to done.** Every row is
either backed by a command you can re-run, or marked as unverified. Nothing here
is a status someone typed in by hand and forgot to update — if a row claims
VERIFIED, the command beside it produced that result.

Last measured: 2026-08-19 (§0 re-measured; sections below still carry their 2026-08-04 measurements unless marked).

> ## What changed on 2026-08-04 — the grounding chain
>
> **The biggest finding: the research service had never started.** It was
> diagnosed for weeks as "SearXNG is down". SearXNG was fine. The service's
> Dockerfile copied ONE file, and when `ranking.py` was split out for
> testability nothing added it to the image, so the container crash-looped on
> `ModuleNotFoundError` — quietly, because `restart: unless-stopped` just
> looped it. `docker ps` said "Restarting" and nobody read it.
>
> | | before | after |
> |---|---|---|
> | grounding confidence | **0.40** — below the 0.5 floor, always | **0.85** |
> | "Limited sources" marker | on **every concept of every course** | cleared |
>
> **Research now runs in BOTH phases.** It used to run only during hydration,
> so the most consequential decision in the pipeline — *what this course is
> made of* — was taken with no evidence at all, from one LLM call. That is why
> a Pythagoras course covered 42% of its own subject while passing every
> structural detector: it was structurally clean and substantively hollow.
>
> ```
> Phase 1  what should this course contain?   curriculum_research.py  (NEW)
> Phase 2  what does this concept say?        research_server.py
> ```
>
> **Sources are now the right SHAPE for a course.** Papers report the frontier;
> a course teaches the canon, explained. Open textbooks (Wikibooks,
> Wikiversity) are wired in and weighted highest. Domain-routed archives were
> added for the subjects that genuinely need them — Met and Art Institute for
> art, Library of Congress for history, Wikidata everywhere — and routed rather
> than global, because an irrelevant hit costs latency *and* inflates
> confidence while teaching nothing.
>
> **Three of the candidate public APIs do not work as published** and are
> recorded in `domain_sources.py` so they are not re-added from a stale list:
> Chronicling America's legacy API was retired in 2025; Open Library's
> `search.json` hangs (the site itself answers in 0.18 s); Data USA 404s.
>
> **`compute_confidence` has now been the site of the same bug twice** — first
> primary literature, then textbooks — where the caller filtered for source
> kinds and silently dropped one, so the source kind a course most wants
> counted for nothing. It is now weighted by kind, and full confidence must be
> earned with a textbook or primary source rather than a pile of web pages.
>
> ### Frontend
> - `/build` — live visualisation of a course build: stages, evidence found,
>   the tree growing, the coverage verdict. Logs are translated out of our
>   internals (`STRUCT:MODULE:x`) into what actually happened
>   (`Found syllabus — Wikibooks: "Geometry" (31 chapters)`).
> - `/library` — search public archives or upload EPUB/PDF/MD/TXT. Availability
>   is three answers (full text / lending-only / metadata) and the UI refuses to
>   collapse them.
> - **Build state survives navigation and the UI locks while a build runs.**
>   Progress used to live only in the page that started it, and the UI kept
>   offering "Create course" during a build — rejecting it only *after* the
>   learner filled in the form.
>
> ---
>
> **What changed on 2026-08-03.** Four criteria were marked BUILT-but-never-run.
> Running three of them found a defect in each — all three at seams between
> components, none visible to either side's unit tests:
>
> | Exercised | What it found |
> |---|---|
> | Spaced repetition | `get_due_reviews()` could never return anything. Verified against a real DB: 2 scheduled reviews present, 0 returned, even at year 2099. Spoken review mode always said "nothing due". |
> | Memory Palace | Re-anchoring a locus silently did nothing — both lookups returned the *oldest* anchor. Learners were stuck with their first image forever. |
> | Gate criterion 6 | Never actually ran. Wired into generation; the one course that had "passed the gate" scores 42% coverage, INADEQUATE. |
>
> Plus the HelgaBench judge, self-tested for the first time, was found to be
> manufacturing scores. See §4.
>
> Closing out criterion 4 then surfaced two more: concept scheduling ignored
> review history entirely (a fixed grade→interval table, while the FSRS engine
> sat unused), and `update_progress` used `INSERT OR REPLACE`, so **every
> column the caller did not pass was silently reset to its default**.

---

## 0. Re-measured 2026-08-19 — the state of the actual data directory

Everything below §0 was measured on 2026-08-04. Two weeks of work landed since
(content hydration, the claims ledger, the book pipeline, the teaching loop,
assets, degrees, the frontend). This section is what is true of the REAL data
directory today, not of the code.

> **RESOLVED 2026-08-19, later the same day.** The migration has now been run
> against the real database: it is at **v17**, both `user_progress` rows
> survived, and all ten new tables exist. A backup of the pre-migration file is
> in the session scratchpad. The three features that depend on those tables
> were then exercised against the REAL data and degrade correctly rather than
> raising: the trust surface reports `available: false` for a course built
> before v12 — which the UI renders as "Sources not recorded", and which is
> the honest answer for that course — and the programme store returns an empty
> list and `None` for a missing programme.
>
> **The live database was at schema_version 10.** Migrations v11-v17 have never
run against `data/helga.db`. That means `sources`, `claim_sources`,
`taught_concepts`, `session_notes`, `teaching_objects`, `concepts` (+FTS5),
`concept_math`, `concept_assets`, `programs` and `program_courses` **do not
exist in the real database**. Every feature built on them is inert against real
data — the trust surface reports "sources not recorded" for every concept
because, for these courses, that is literally true.

Verified safe on a copy of the real file: v10 → v17 applies cleanly, the
existing `user_progress` rows survive, and the course list still reads. It runs
automatically on the next `StorageManager` init, i.e. the next time a service
starts.

**No course has been built since 2026-08-07.** The newest `structure.json` is
dated Aug 7; the Pythagoras course is still the original three-module one that
scores 42% coverage. So §4 item 0 ("nothing has been rebuilt since the
grounding chain changed") is not merely still open — it now also covers
hydration, the ledger, depth contracts, assets and the book pipeline, none of
which has ever produced a course on disk.

**No book-sourced course exists.** The book pipeline is verified on parsing and
structure against four real books (Pride and Prejudice 59/61 chapters, The Art
of War 13/13, an OpenStax biology text at 19 modules / 69 lessons, and a
self-help title at 81). None was hydrated through to a persisted course, so
criterion 6 stays PARTIAL for the same reason it did on 2026-08-04 — one step
further along, but the last step is the one that counts.

**16 of 19 course rows have no directory on disk.** `courses` in SQLite holds
19 rows; `data/courses/` holds 3. The orphans are mostly `skeleton` and
`available` states from earlier runs (`linear algebra`, four separate
`eigenvalues and eigenvectors`, `causal inference`). This is the AUTO-10
failure mode the plan predicted — JSON written and SQLite diverging — and it is
user-visible: a course list built from SQLite offers 16 entries that cannot
open. Needs a reconciliation pass that either restores or removes them.

### Closed since 2026-08-04

- **§4.2 the trust surface is now on screen.** Sources, grounding, domain tier
  and the supplementary share render per concept in the session view, with
  "not recorded" and "unavailable" as distinct honest states. It will stay
  empty for existing courses until the migration runs and a course is built.
- **§4.0b is closed — both `/build` and `/library` have now been driven in a
  browser.** `/build`: the book rail, the live stream, the single-build lock
  and cancellation. `/library`: both tabs render, the upload drop zone works,
  and a search with the backend absent says "Book search is unavailable right
  now" rather than showing an empty result set — the distinction this codebase
  keeps insisting on, holding in the one surface that had never been looked at.
- **The schema migration has been run** — see the note in §0.

---

## How to re-measure everything

```bash
# unit + integration (fast, no LLM)
python3 -m pytest tests/ -q --ignore=tests/e2e

# is each mastery level reachable?      ~10 min
python3 tools/tier_probe.py

# structural + gate verdict on courses on disk   (free)
python3 tools/golden_courses.py evaluate

# node-path pathologies                          (free)
python3 tools/path_audit.py

# ALWAYS validate the judge before trusting a score   ~2 min
python3 tools/helgabench.py --self-test

# tutoring quality vs the recorded baseline      ~20 min
python3 tools/helgabench.py --repeat 3 \
    --compare docs/baselines/helgabench_a1_calibrated.json

# does the tutor accept wrong answers?           ~5 min
python3 tools/sycophancy_probe.py

# does it HOLD a correction, or drift over turns?  ~5 min
python3 tools/persistence_probe.py

# curriculum coverage vs a real syllabus (gate criterion 6)
python3 tools/syllabus_check.py --course <uid> --reference syllabus.txt

# does content read at the level it claims?      ~5 min
python3 tools/level_audit.py --course <uid>

# is the rigor real, or just markers?            ~5 min
python3 tools/substance_check.py --course <uid>
```

---

## 1. The seven done-criteria for Mode A

A self-directed adult can, without hitting a dead end:

| # | Criterion | State | Evidence |
|---|---|---|---|
| 1 | Course at the **genuine depth requested** | **VERIFIED** | every tier observed reachable (`tier_probe`, ~80% first-attempt); a mastery-2 course scores 100% at L2 and **0% at L4/L5** |
| 2 | Learn Socratically, **voice or text** | BUILT, unverified | `/api/stt` → `session.js`; no end-to-end voice run measured |
| 3 | **See where content came from** | **VERIFIED (and only now real)** | grounding confidence 0.40 → **0.85** once the research service was fixed; sources now span wikipedia + open textbooks + primary literature + web + domain archives. The concept VIEW still does not display them — see §4 |
| 4 | **Reviewed on schedule** (FSRS) | **VERIFIED** | loop verified on a real DB (37 tests). FSRS now drives **both** flashcards and concepts — schema v10 persists stability/difficulty/lapses on `user_progress`; measured interval growth on repeated recall: **3 → 11 → 35 → 101 days** |
| 5 | **All three learning modes** reachable | **VERIFIED** | Socratic ✅, Spaced Repetition ✅, Memory Palace walked end-to-end against real storage (17 tests) |
| 6 | **Bring your own material** | PARTIAL | `/library` built: archive search with honest availability, EPUB/PDF/MD/TXT upload. Extraction verified; **PDF now actually reads** (it was advertised in `/library` and raised UnsupportedDocument). Book figures are extracted and reviewed (15 tests). **Still no real book taken through to a built course** — needs a hydration run against the OpenStax/Gutenberg cases in §6 |
| 7 | **Every control does what it says** | **VERIFIED** | dead toggles removed, `/api/profile/reset` proxied, tests assert both |

**5 of 7 verified, 1 partial, 1 unrun.** The earlier headline — "most remaining
risk is *unrun*, not *unwritten*" — was right, and running it proved the point:
every one of the three criteria exercised on 2026-08-03 was broken, and none of
those breaks was visible to the unit tests on either side of the seam. Voice
(criterion 2) is now the only done-criterion never exercised at all.

---

## 2. The quality gate (§4.10 of SPRINT_PLAN.md)

| # | Criterion | State | Note |
|---|---|---|---|
| 1 | Apparatus (depth contract) | **ENFORCED** | regenerates against the named missing element |
| 2 | Level calibration | **ENFORCED** | blind judge, hints stripped, recorded per course |
| 3 | Substance & factual correctness | **ENFORCED** | `fact_check` with independent confirmation |
| 4 | Structure | **ENFORCED** | degenerate lessons folded pre-persist |
| 5 | Grounding | **ENFORCED** | Wikipedia + Crossref/arXiv; confidence floor visible |
| 6 | Syllabus realism | **WIRED** | runs on every skeleton pre-persist; verdict recorded on the course and emitted as `CHECK:SYLLABUS:<verdict>:<pct>` |

**That course no longer passes.** `course_2b9df59e` cleared criteria 1–5, but
criterion 6 had never been run against it. It scores **42% coverage,
INADEQUATE**. I checked the verdict against the outline rather than trusting the
instrument, and it is right: three modules of one lesson each, with no
Pythagorean triples, no distance formula, no converse test, and no word
problems.

`path_audit`'s 16 structural detectors report the same course as clean. That is
the lesson worth keeping — **structural health is not curricular completeness**,
and criterion 6 is the only check that can tell the difference, because it is
the only one with external ground truth. There is currently **no course that
passes the full conjunctive gate.**

> Criterion 6 is non-blocking by default (`HELGA_SYLLABUS_GATE=1` to enforce).
> The instrument is a documented undercount — a 9B judge scores a *complete*
> outline at ~71% — so the verdict discriminates but the percentage is a lower
> bound. Failing builds on a lower bound would reject good courses.

---

## 3. Presets

All 8 implemented, API-served, UI-wired, 42 tests. Each preset's advertised
`requires` **is** the depth contract for that level — verified by test, not
asserted.

| Preset | Tier | First-attempt attainable? |
|---|---|---|
| Quick Overview | 1 | observed passing |
| High School | 2 | observed passing |
| College Course | 3 | observed **both** passing and failing |
| Advanced Undergraduate | 4 | observed passing (needed Crossref + template sections) |
| Graduate Seminar / Deep Dive | 5 | observed passing (needed a scaled token budget) |
| Refresher, Full Survey | 3 | same tier as College |

> **Read this before quoting the table.** `tier_probe` measures a SINGLE
> generation attempt with **no retries**. Across two sweeps every tier was
> observed passing at least once, and mastery 3 was observed both passing and
> failing — so first-attempt success is roughly 80%, not 100%, and a single
> probe is directional only.
>
> Real course builds are more reliable than this number suggests, because the
> hydrator retries against the *named* missing element rather than re-rolling.
> That is why the full course run reached 100% on the depth contract while
> individual probes sit near 80%. The probe answers "is this level reachable at
> all"; it does not measure what a learner receives.

---

## 4. What is genuinely NOT done

Ranked by risk, not effort.

0. **NOTHING HAS BEEN REBUILT SINCE THE GROUNDING CHAIN CHANGED.** This is the
   single most important open item. Phase-1 research, textbook grounding,
   domain sources and confidence reweighting are all verified to *fetch the
   right evidence and be wired in* — but no course has been generated since.
   The 42% coverage figure is from a course built by the OLD pipeline. Whether
   any of this actually improves coverage is **unmeasured**.

   *The run to do:* rebuild the Pythagoras course and re-run criterion 6
   against the 42% baseline. ~40 minutes. Queue it overnight.

0b. **`/build` and `/library` have never been seen in a browser.** Routes
   return 200 and the JS parses, but no real build has driven the
   visualisation and no book has been searched through the UI.

1. **A4 — pedagogy. The target moved.** The old entry here read
   `misconception_handling` **1.6/5**. That number was largely an instrument
   defect, found by self-testing the HelgaBench judge for the first time —
   every other instrument in this repo self-tests; this one never had.

   Three defects stacked: a missing key was read as `int(data.get(d, 0))` and
   clamped to **1**, inventing the worst possible score out of silence; the
   rubric had no way to say *"the student made no error"*, so a clean dialogue
   scored the same as praising a bluff; and one judge call swings **±2 on an
   identical transcript** (measured 5, 3, 3, 5), so no single-sample score was
   a measurement at all.

   Recalibrated (median of 3 samples, two-call sub-judge, N/A excluded):

   | | old | calibrated |
   |---|---|---|
   | `misconception_handling` | 1.6 (n=15) | **3.0 (n=8)** |

   **The n is the finding.** Seven of fifteen dialogues contained no student
   error to score; all seven previously scored 1.

   *Do not read the other deltas in that comparison.* No tutor code changed
   between the runs — the judge did — so `helgabench_a0.json` is retained as a
   record but is **not a valid comparison point**. `helgabench_a1_calibrated.json`
   is the reference from here.

   A real gap remains under the artifact: **adaptation is now the weakest
   dimension at 2.8**, and "Misconception holder" the weakest profile at 2.4.
   The judge's `worst_moment` notes repeatedly describe *lecturing instead of
   questioning* and *ignoring what the student actually asked* — a different
   problem from the one 1.6 pointed at, and the one worth working on next.

2. **The trust surface is still not on screen.** The A5 gate says "every
   concept view shows its sources and confidence". The markdown carries a
   Sources block and a confidence figure; the session view displays neither.
   Now that confidence is real (0.85, not a flat 0.40) this is worth doing.

3. **Voice never exercised** — the last done-criterion with no end-to-end run.
   Document import is verified as far as extraction; taking a real book through
   to a built course needs a hydration run.
3. **A6 — optimization.** Ollama idle-eviction unbuilt (≈6 GB pinned when
   idle); `tts` container allocated 2048M for a 319 MB model; two duplicate
   Kokoro copies on disk.
4. **A7 — hardening.** No Ollama circuit-breaker fallback, no soak test, no
   backup/restore drill. (The `main.py` false green is **fixed** — the preflight
   required only a substring, so `qwen3:14b` "matched" `qwen3:14b-q4_K_M` and
   then every call 404'd. It now requires an exact tag, honours the one alias
   Ollama really resolves, and names the closest installed tag on a miss.)
5. **n=1 everywhere, and now n=0.** No course currently passes the full gate,
   and there is one probe per tier. Given a measured
   ±1.4/5 noise floor on LLM judges, single results are directional only. The
   golden matrix across the slider space is the real evidence base and has not
   been built.

---

## 5. Known environmental constraints

- **~30s per LLM call** on qwen3.5:9b → ~2 min/concept → a 12-concept course
  takes ~40 min. This caps how much verification is affordable per concept and
  is why fact-check samples at 34%.
- ~~**SearXNG is down.**~~ **RESOLVED 2026-08-04 — and the diagnosis was
  wrong.** SearXNG was always fine. The *research service* had never started:
  its Dockerfile copied one file and omitted `ranking.py`, so it crash-looped
  on ModuleNotFoundError under `restart: unless-stopped`. Grounding confidence
  went 0.40 → 0.85 once fixed. A static test now checks every service's local
  imports against what its Dockerfile copies.

  The lesson worth keeping: a container in a restart loop looks like a running
  system from every angle except the one nobody checked.
- **The ternary 27B is not viable** for generation: it degenerates into
  repetition on the real builder prompt (3/3), while producing clean output on a
  simplified version (4/4). `qwen3.5:27b-mlx` is the next candidate and must be
  gated on the real prompt.

---

## 6. Handoff — where to pick this up

**Do this first, and it needs no supervision:**

```bash
# 1. Bring the stack up (SearXNG + research are required for grounding now)
docker compose up -d searxng research
curl -s localhost:5006/health          # must be 200, not "Restarting"

# 2. THE RUN THAT MATTERS — rebuild with the new grounding chain (~40 min)
#    then compare coverage against the 42% baseline.
python3 tools/syllabus_check.py --course <new_uid> --no-reference
```

If coverage moves meaningfully above 42%, Phase-1 research works and the
biggest quality lever in the product is proven. If it does not, the grounding
is being fetched but not *used*, and the next place to look is the module
prompt in `course_builder._build_inner` — the brief is injected there.

**Then, in order:**

1. Watch a real build drive `/build`, and search a book through `/library`.
   Both are built and route correctly; neither has been seen working.
2. Put sources and confidence on the concept view (A5 gate item, now that
   confidence is real).
3. A5.5 — take a genuine book end to end. The test cases are now **public
   books**, so anyone can reproduce the run, and both are chosen for having
   real *captioned figures* — the asset phase reads a book's own figures and
   uses nothing else for a book-sourced course:

   - **PDF — an OpenStax textbook** (e.g. *Astronomy 2e* or *Biology 2e*),
     openstax.org. CC BY 4.0, several hundred pages, figures numbered
     "Figure 3.2" with real captions. The open licence matters twice: it is
     legally clean to extract from, and the figures stay usable downstream.
   - **EPUB — a Project Gutenberg illustrated title**, e.g. *On the Origin of
     Species* or Gray's *Anatomy of the Human Body*. Public domain, genuine
     plates with captions, and a real EPUB rather than a converted one.

   Offline fixtures shaped like both (real figures plus the page furniture that
   must be rejected) are generated by `tests/fixtures/make_book_fixtures.py`,
   so the suite never needs the network.
4. A4.1a/b — the dialogue contract and learner-history personalisation, the
   cheapest remaining tutor-quality wins.

**Two things not to re-litigate** (both recorded with reasons in the code):

- Papers are not the growth area for grounding. A course needs the canon
  explained, not the frontier reported.
- Universities List, Numbers API, PokeAPI and extra web-search engines were
  evaluated and declined — see `docs/SPRINT_PLAN.md` §A5.6.

**The failure mode this whole pipeline is built against:** a course that passes
every structural check and is substantively hollow. `path_audit` reported the
42%-coverage course as clean across all sixteen detectors. Criterion 6 is the
only check that catches it, which is why it now runs on every build.
