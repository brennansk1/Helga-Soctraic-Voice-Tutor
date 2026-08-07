# Quality specification — what "good enough" means, per preset and per check

**Status:** internal bars are VERIFIED against the code (file:line given for each).
External anchors are pending a research pass and are marked `[EXTERNAL — PENDING]`.
Written 2026-08-07.

> **Why this document exists.** The stated goal is "better than the current LLM
> and course-creation tutor products". Every instrument in this repo is
> *internally* referential — it asks whether a course meets a contract we wrote,
> covers topics a judge we wrote derived, cites sources we fetched. None of them
> compares Helga's output to what a frontier chatbot would produce for the same
> request. So the gate can pass while the product loses. That gap is called out
> explicitly in §6 rather than papered over.

---

## 1. The eight presets and what each one promises

`services/core/course_builder.py:300` (`COURSE_PRESETS`)

| Preset | scope | mastery | from | The promise it makes to a learner |
|---|---|---|---|---|
| Quick Overview | 2 | 1 | 1 | The shape of the subject in an evening. No prerequisites. |
| High School | 3 | 2 | 1 | Solid grounding with worked examples. Assumes no background. |
| College Course | 3 | 3 | 2 | Formal definitions, worked problems, real sources. |
| Advanced Undergraduate | 3 | 4 | 3 | Named results, derivations, primary literature. |
| Graduate Seminar | 2 | 5 | 4 | Proofs, exercises, research sources. Expert register. |
| Full Discipline Survey | 5 | 3 | 1 | Breadth over depth — the whole field. Long build. |
| Refresher | 3 | 3 | 4 | Skips introductions, restarts at application level. |
| Deep Dive | 1 | 5 | 3 | One narrow topic, taken as far as it goes. |

**The preset's `mastery` selects the depth contract.** That is the mechanism by
which a blurb becomes a testable bar — the marketing copy and the enforcement
are the same number.

---

## 2. The depth contract — the per-concept bar

`services/core/depth_contract.py` (`CONTRACTS`)

| Mastery | Label | Words | Required elements |
|---|---|---|---|
| 1 | Awareness | 120–1000 | `any_source` |
| 2 | Understanding | 200–1300 | `worked_example`, `any_source` |
| 3 | Application | 320–1500 | `formal_definition`, `worked_example`, `any_source` |
| 4 | Proficiency | 500–1800 | + `named_result`, `derivation_or_proof`, `primary_source` |
| 5 | Expertise | 700–2200 | + `formal_notation`, `exercise` |

**Two properties worth preserving:**

- **The word range is a BAND, not a target.** Too short means thin; too long
  means the level is not being distinguished. The observed failure was every
  level converging on ~770 words.
- **Requirements are MONOTONIC.** Level N+1 requires everything N does, plus
  more. An earlier version dropped `worked_example` at level 5, which made
  "Expertise" less rigorous than "Proficiency" in one dimension. A level name
  stops meaning anything the moment advancing the slider can relax a
  requirement.

**How the elements are detected** (`depth_contract.py`):

| Element | Detection |
|---|---|
| `any_source` | `https?://\S+` in the body |
| `primary_source` | arxiv / doi.org / pubmed / jstor / sciencedirect / springer / wiley / nature / ieee |
| `formal_notation` | inline or unicode maths |
| others | regex over the body |

> **Known weakness.** `any_source` is satisfied by *any* URL. It measures that a
> citation is PRESENT, not that it is RELEVANT — which is exactly how a
> constructed-language grammar page was cited as a quantum textbook on 8 of 22
> concepts while the course reported confidence 1.00. Relevance is enforced
> upstream in the research service, not here.

---

## 3. The conjunctive quality gate — all six must pass

A course is not "good" because it scores well on average. The gate is
**conjunctive** because the failure mode being defended against is a course that
is *structurally impeccable and substantively hollow* — and structural checks
cannot see that. `path_audit`'s 16 detectors reported a 42%-coverage course as
clean.

| # | Criterion | Bar | Where |
|---|---|---|---|
| 1 | Depth contract | every concept passes, first try or after retry | `depth_contract.validate_concept` |
| 2 | Level calibration | reads at the level claimed (blind judge, hints stripped) | `tools/level_audit.py` |
| 3 | Substance & factual correctness | no verified-false claims | `services/common/fact_check.py` |
| 4 | Structure | no single-concept lessons | folded pre-persist |
| 5 | Grounding | confidence ≥ **0.5** | `HELGA_CONFIDENCE_FLOOR`, `course_builder.py:2555` |
| 6 | Syllabus realism | coverage ≥ **70%** | `HELGA_MIN_COVERAGE_PCT`, `syllabus_check.py:299` |

### Criterion 5 — how grounding confidence is earned

`services/research/ranking.py` (`SOURCE_KIND_WEIGHTS`)

| Source kind | Weight | Class |
|---|---|---|
| wikipedia | 0.40 | tertiary |
| textbook (Wikibooks/Wikiversity) | 0.30 | textbook |
| journal (Crossref DOI) | 0.25 | primary |
| preprint (arXiv) | 0.25 | primary |
| web | 0.20 | web |
| artefact (Met / Art Institute) | 0.20 | archive |
| primary_document (LoC) | 0.20 | archive |
| structured_fact (Wikidata) | 0.10 | structured |

Full confidence must be **earned with a textbook or primary source**, not with a
pile of web pages. This registry is the single source of truth precisely because
the alternative — each caller keeping its own list — produced the same bug three
times running.

### Criterion 6 — read the caveat before quoting the number

The 70% floor is measured by a **9B judge that undercounts**: it scores a
*complete* outline at ~71%, declaring plainly-present topics missing. So:

- Treat the **verdict** as meaningful and the **percentage as a lower bound**.
- Investigate a LOW score; do not trust a high one.
- `HELGA_SYLLABUS_GATE=1` (hard-fail) should stay OFF until a larger judge makes
  the number tight. Failing builds on a lower bound rejects good courses.

---

## 4. Tutoring quality — the chat side

`tools/helgabench.py`. Five dimensions, scored 1–5 by an LLM judge:

`socratic` · `adaptation` · `accuracy` · `misconception_handling` · `progression`

**Measured baseline, judge self-tested, `--repeat 3`, n=15 dialogues:**

| Model | OVERALL | accuracy |
|---|---|---|
| qwen3:14b | **4.34** | **5.00** (sd 0.00) |
| qwen2.5-coder:14b-instruct | 4.33 | — |
| qwen3.5:9b | 3.41 | 3.93 |
| gemma-3-12b-it | 3.13 | — |
| qwen3.5:4b | 2.99 | 2.93 (sd 1.61) |

**Rules for reading these numbers:**

1. **Always run `--self-test` first.** A HelgaBench score was once mostly
   instrument defect: a missing key read as `int(data.get(d, 0))` and clamped to
   1, inventing the worst possible score out of silence.
2. **The minimum detectable effect at n=15 is ~0.26.** Ignore differences below
   it. A single judge call has been measured swinging ±2 on an identical
   transcript.
3. **`accuracy` is the dimension that disqualifies.** A tutor that states wrong
   facts fails at the one thing it exists for, regardless of how Socratic it is.

---

## 5. The seven Mode A done-criteria

A self-directed adult can, without hitting a dead end:

| # | Criterion | State |
|---|---|---|
| 1 | Course at the genuine depth requested | VERIFIED |
| 2 | Learn Socratically, voice or text | text VERIFIED; **voice unrun** |
| 3 | See where content came from | VERIFIED (0.85 confidence) |
| 4 | Reviewed on schedule (FSRS) | VERIFIED — 3→11→35→101 days |
| 5 | All three learning modes reachable | VERIFIED |
| 6 | Bring your own material | PARTIAL — no real book taken to a built course |
| 7 | Every control does what it says | VERIFIED |

---

## 6. The bar this repo does NOT measure

**"Better than the current LLM and course-creation tutor products" has no
instrument here.** Every check above is internally referential.

A frontier chatbot will happily produce 22 fluent concepts on quantum computing.
What it will not do is cite real syllabi, name its sources, or tell you it
covered 55% of the field. **Grounding and coverage are the differentiator** —
and they are precisely the two that have been broken.

**Proposed competitive test** (not yet implemented):

1. Same topic, same level, generated by Helga and by a frontier model.
2. Score both with `syllabus_check` and `substance_check`.
3. If Helga does not win on **coverage** and **grounding**, the differentiator is
   not real yet.

Prose fluency is table stakes and Helga already has it. It is not where the
product is won.

---

## 7. Current measured position

Course `course_56ddfe61` (Quantum Computing, mastery 3, pre-fix):

| Check | Result |
|---|---|
| Depth contract | **20/22 (91%)** |
| Structure | clean |
| Words | 26,078 total, median 1,233/concept |
| Grounding | real sources present, **contaminated** by an irrelevant textbook |
| Coverage | **55% — INADEQUATE** (floor 70) |
| Phase 3 assets | 14 diagrams, first manifest ever produced |

**Verdict: prose strong, structure strong, curriculum incomplete, grounding
contaminated.** The limiting factor is not the model — it is topic selection and
the source filter, both of which are fixable without a model swap.

---

## 8. Instrument health — check before trusting any number

Five instrument defects were found in a single day, every one reporting a
property of the harness as a property of the model, always in the direction that
eliminates candidates:

| Instrument | Defect |
|---|---|
| HelgaBench judge | missing key → clamped to 1, inventing the worst score from silence |
| `model_gate` schema probe | 400-token budget truncated valid JSON; thinking not disabled |
| `model_gate` contract | validated a document missing the `## Sources` block production appends — two required elements were unsatisfiable |
| `model_gate` schema probe | 120s timeout charged the cold model load to the capability |
| `syllabus_check` | a silent judge scored **0%** instead of "not measured" |

**Rule: a number from an instrument that has not self-tested recently is not
evidence.** Four of the five defects above shared one shape — manufacturing the
worst possible verdict out of no information.

---

## 9. External anchors

`[EXTERNAL — PENDING]` — a research pass is gathering citable standards for:

- curriculum coverage thresholds (ABET / ACM CS2023 core vs elective)
- pedagogical soundness criteria (Cognitive Load Theory, Mayer, Rosenshine, Bloom)
- readability bands per level (Flesch-Kincaid, Dale-Chall, Lexile)
- published AI-tutor rubrics, especially how they score a tutor that ACCEPTS a
  misconception
- measured hallucination rates for LLM-generated educational content and what
  error rate is considered acceptable

This section will be filled in with source + URL + how each becomes an automated
check. Anything that cannot be sourced credibly will be marked as such rather
than invented.
