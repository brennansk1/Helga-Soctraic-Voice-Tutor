# Mode A (Personal / Scholar) — completion scoreboard

**The single place to check how close personal mode is to done.** Every row is
either backed by a command you can re-run, or marked as unverified. Nothing here
is a status someone typed in by hand and forgot to update — if a row claims
VERIFIED, the command beside it produced that result.

Last measured: 2026-08-03.

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

# tutoring quality vs the recorded baseline      ~15 min
python3 tools/helgabench.py --repeat 3 --compare docs/baselines/helgabench_a0.json

# does the tutor accept wrong answers?           ~5 min
python3 tools/sycophancy_probe.py

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
| 4 | **Reviewed on schedule** (FSRS) | BUILT, unverified | FSRS-5 engine present; not exercised end-to-end |
| 5 | **All three learning modes** reachable | PARTIAL | Socratic ✅, Spaced Repetition ✅, Memory Palace UI restored but unexercised |
| 6 | **Bring your own material** | BUILT, unverified | EPUB/MD/TXT extraction + UI; no real book ingested end-to-end |
| 7 | **Every control does what it says** | **VERIFIED** | dead toggles removed, `/api/profile/reset` proxied, tests assert both |

**3 of 7 verified. 4 built but never exercised end-to-end.** That gap is the honest
headline: most remaining risk is *unrun*, not *unwritten*.

---

## 2. The quality gate (§4.10 of SPRINT_PLAN.md)

| # | Criterion | State | Note |
|---|---|---|---|
| 1 | Apparatus (depth contract) | **ENFORCED** | regenerates against the named missing element |
| 2 | Level calibration | **ENFORCED** | blind judge, hints stripped, recorded per course |
| 3 | Substance & factual correctness | **ENFORCED** | `fact_check` with independent confirmation |
| 4 | Structure | **ENFORCED** | degenerate lessons folded pre-persist |
| 5 | Grounding | **ENFORCED** | Wikipedia + Crossref/arXiv; confidence floor visible |
| 6 | Syllabus realism | **BUILT, not wired** | `syllabus_check.py` runs manually only |

**A full course passed the conjunctive gate** (`course_2b9df59e`, mastery 2,
12 concepts). One course, one tier — a demonstration, not a guarantee.

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

1. **A4 — pedagogy.** HelgaBench baseline is 3.12/5 with
   `misconception_handling` at **1.6**, the systematic weakness across all five
   student profiles. An anti-sycophancy prompt change showed **no measurable
   effect** (n=15, within noise). The sharp probe shows the tutor already
   corrects *blatant* errors 97% of the time, so the real gap is
   **fluent-but-hollow** answers, and the probe needs harder cases before this
   can be fixed honestly.
2. **Criterion 6 not wired into generation** — the only gate criterion with
   external ground truth still runs by hand.
3. **Four done-criteria never exercised** — voice, FSRS review, Memory Palace,
   document import. All built; none run end to end.
4. **A6 — optimization.** Ollama idle-eviction unbuilt (≈6 GB pinned when
   idle); `tts` container allocated 2048M for a 319 MB model; two duplicate
   Kokoro copies on disk.
5. **A7 — hardening.** No Ollama circuit-breaker fallback, no soak test, no
   backup/restore drill. `main.py:81` still reports a false green (substring
   model match: `qwen3:14b` "matches" `qwen3:14b-q4_K_M`).
6. **n=1 everywhere.** One passing course, one probe per tier. Given a measured
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
