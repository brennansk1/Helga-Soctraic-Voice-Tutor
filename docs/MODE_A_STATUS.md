# Mode A (Personal / Scholar) — completion scoreboard

**The single place to check how close personal mode is to done.** Every row is
either backed by a command you can re-run, or marked as unverified. Nothing here
is a status someone typed in by hand and forgot to update — if a row claims
VERIFIED, the command beside it produced that result.

Last measured: 2026-08-03.

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
| 3 | **See where content came from** | **VERIFIED** | 100% citation coverage on the passing course; primary literature via Crossref at mastery ≥4 |
| 4 | **Reviewed on schedule** (FSRS) | **VERIFIED** | loop verified on a real DB (37 tests). FSRS now drives **both** flashcards and concepts — schema v10 persists stability/difficulty/lapses on `user_progress`; measured interval growth on repeated recall: **3 → 11 → 35 → 101 days** |
| 5 | **All three learning modes** reachable | **VERIFIED** | Socratic ✅, Spaced Repetition ✅, Memory Palace walked end-to-end against real storage (17 tests) |
| 6 | **Bring your own material** | PARTIAL | extraction verified (13 tests, synthetic EPUB, spine order, bad-zip, PDF honestly rejected); **no real book taken through to a built course** — needs a hydration run |
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

2. **Voice never exercised** — the last done-criterion with no end-to-end run.
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
- **SearXNG is down.** Grounding is Wikipedia + Crossref/arXiv only, so
  `source_confidence` tops out at 0.4 — below the 0.5 floor, so every concept
  carries a visible "Limited sources" marker. Correct behaviour, but it means
  no course currently clears the floor.
- **The ternary 27B is not viable** for generation: it degenerates into
  repetition on the real builder prompt (3/3), while producing clean output on a
  simplified version (4/4). `qwen3.5:27b-mlx` is the next candidate and must be
  gated on the real prompt.
