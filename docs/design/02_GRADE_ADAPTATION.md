# Design Spec 02 — Grade-Level (K-12) Adaptation

> The product's pedagogical heart: a kindergartner must not be taught like a 12th-grader. This spec
> gives the **exact per-band parameters and prompt fragments** and where they plug into the existing
> Socratic engine. Branches B17.1–B17.3 (this doc), B17.4–B17.7 (kid tooling). Insertion points
> verified in `services/common/prompts.py` and `services/core/fsm_logic.py`.

## 1. The four bands (+ optional numeric grade)
`grade_band ∈ {K-2, 3-5, 6-8, 9-12}` on `students.grade_band` and on each catalog course (spec 01 §2, §4.1).
`grade_numeric` (0=K … 12) allows finer tuning *within* a band (e.g., a 2nd-grader gets the top of the K-2
register). The **course's** band must match the **student's** band for catalog courses (a 3-5 student is offered
3-5 catalog courses); electives may differ and use the student's band for delivery.

Resolution order at session start (in `MnemosyneFSM`): `student.grade_band` → course `grade_band` → default `6-8`.
Store the resolved band in the FSM session blob (spec 01 §3) and pass to every prompt builder.

## 2. Per-band adaptation parameters (the canonical table)

| Parameter | K-2 | 3-5 | 6-8 | 9-12 |
|---|---|---|---|---|
| **Persona** | Warm playful guide | Friendly encouraging coach | Curious thinking-partner | Rigorous academic mentor |
| **Max words / tutor turn** | 25 | 45 | 70 | 110 |
| **Sentences / turn** | 1–2 | 2–3 | 2–4 | 3–5 |
| **New ideas / turn** | 1 | 1 | 1–2 | 2 |
| **Vocabulary ceiling** | top-2000 common words; define any other | common + 1 keyterm/turn | grade-appropriate academic | full academic register |
| **Question framing** | concrete, playful, here-and-now | concrete with light abstraction | concrete→abstract bridges | abstract, multi-step reasoning |
| **Default Bloom floor / ceiling** | 1 / 3 | 1 / 4 | 2 / 5 | 2 / 6 |
| **Mastery-gate streak / Qs** | 2 / 2 | 2 / 3 | 2 / 3 | 3 / 4 |
| **Distinct Socratic types to pass** | min(2, available) | min(3, available) | 3 | 3 |
| **Affirmation density** | very high | high | moderate | calibrated |
| **Hint ladder depth** | shallow, fast worked example | medium | medium | full 4-step before example |
| **Answer length expected** | a word/short phrase | a sentence | 1–2 sentences w/ reason | multi-clause w/ justification |
| **TTS default** | ON, slower rate | ON | OFF (opt-in) | OFF |
| **Read-aloud of own text** | yes | optional | no | no |
| **Emoji/encouragement marks** | allowed (sparing) | rare | none | none |
| **Markdown/LaTeX** | none | minimal | yes | yes |

These numbers are starting values; tune from transcript review (open item in plan). They live in one module
constant so they're adjustable in one place.

## 3. Implementation: `GRADE_BAND_PROFILES` constant
New constant in `services/common/prompts.py` (single source of truth), consumed by the prompt builders and by
`course_builder.py` (catalog hydration writes band-appropriate content) and `fsm_logic.py` (mastery bounds):

```python
GRADE_BAND_PROFILES = {
  "K-2":  dict(persona="a warm, playful learning guide for a very young child",
              max_words=25, max_sentences=2, new_ideas=1,
              bloom_floor=1, bloom_ceiling=3, gate_streak=2, gate_questions=2, gate_types=2,
              register="Use only simple everyday words. If you must use a new word, say what it means in kid terms. "
                       "Talk about things the child can see or touch. Be cheerful and encouraging.",
              answer_expectation="A single word or a short phrase is a great answer.",
              tts_default=True, allow_emoji=True, allow_markdown=False),
  "3-5":  dict(persona="a friendly, encouraging coach", max_words=45, max_sentences=3, new_ideas=1,
              bloom_floor=1, bloom_ceiling=4, gate_streak=2, gate_questions=3, gate_types=3,
              register="Use clear everyday language. Introduce at most one new term per turn and explain it. "
                       "Use concrete examples; you may begin gentle 'what if' thinking.",
              answer_expectation="One sentence is enough.",
              tts_default=True, allow_emoji=False, allow_markdown=False),
  "6-8":  dict(persona="a curious thinking-partner", max_words=70, max_sentences=4, new_ideas=2,
              bloom_floor=2, bloom_ceiling=5, gate_streak=2, gate_questions=3, gate_types=3,
              register="Use grade-appropriate academic vocabulary, briefly defining technical terms. "
                       "Bridge concrete examples to the underlying principle.",
              answer_expectation="A sentence or two, with a reason, is ideal.",
              tts_default=False, allow_emoji=False, allow_markdown=True),
  "9-12": dict(persona="a rigorous academic mentor", max_words=110, max_sentences=5, new_ideas=2,
              bloom_floor=2, bloom_ceiling=6, gate_streak=3, gate_questions=4, gate_types=3,
              register="Use precise academic language. Expect multi-step reasoning and ask the student to "
                       "justify, compare, or critique. Do not over-affirm.",
              answer_expectation="Expect a multi-clause answer with justification.",
              tts_default=False, allow_emoji=False, allow_markdown=True),
}
```

## 4. Prompt insertion points (exact)
`get_socratic_tutor_prompt()` and `get_socratic_grading_prompt()` gain a `grade_band="6-8"` kwarg.

- **Persona block (`prompts.py:289-308`):** the grade persona *composes with* the existing `style_modifier`.
  Band sets the base persona + register; `style_modifier` (eli5/academic/analogy/drill) is a secondary flavor.
  Precedence: band register is non-negotiable (a "drill" K-2 session is still K-2-simple). Build:
  `persona_str = f"You are {profile['persona']}."` then append `profile['register']`, then the existing
  style_constraint as a softer overlay.
- **Output rules (`prompts.py:358-366`):** inject band caps —
  `f"Write at most {profile['max_words']} words across at most {profile['max_sentences']} sentences. "
   f"Introduce at most {profile['new_ideas']} new idea(s). {profile['answer_expectation']}"` and gate emoji/markdown
  by `allow_emoji`/`allow_markdown` (the existing "NEVER use markdown" rule becomes band-conditional).
- **Bloom directive (`prompts.py:319-329`):** unchanged mechanism, but the level is bounded by the band's
  floor/ceiling (next §).
- **Grading prompt (`get_socratic_grading_prompt`, `prompts.py:402`):** pass band so the rubric calibrates
  ("for a K-2 learner, a correct one-word answer earns grade 3; do not demand explanation"). Prevents the grader
  from failing young kids for terse-but-correct answers.

## 5. Grade-bounded Bloom & mastery (B17.3) — reuse existing engine
No new control flow; feed band-derived bounds into existing helpers:
- `course_bloom_floor/ceiling` (FSM, `fsm_logic.py:287-292`) default from `profile['bloom_floor'/'bloom_ceiling']`
  when the course/concept doesn't override. Catalog courses set these explicitly per spec 04.
- `progressive_bloom()` (`course_builder.py:206`) already ramps floor→ceiling across modules — unchanged.
- `_check_mastery_gate()` (`fsm_logic.py:1061`) reads `gate_streak`, `gate_questions`, `gate_types` from the band
  instead of the hardcoded `2 / 3 / 3`. This also **fixes baseline bug B3.5** (gate impossible at low ceiling)
  because K-2 needs only `min(2, available)` distinct types.

## 6. Grade-aware delivery (B17.4–B17.7) — summary, detailed in tooling specs
- **Hint ladder / micro-lecture** (`get_hint_prompt`, `get_micro_lecture_prompt`): band sets ladder depth and how
  fast a worked example appears (K-2 short-circuits to a simple worked example after one failed hint).
- **TTS**: default from `profile['tts_default']`; K-2 also slows rate and offers read-aloud of the student's own
  typed text (uses existing Kokoro service).
- **Manipulatives / visual answer modes** (B17.5): for K-2/3-5 math, the FSM may emit a structured "answer widget"
  (tap-to-count, number-line drag) instead of demanding typed abstraction; the widget result enters the same
  `handle_socratic_answer` grading path as a normalized answer string.
- **Affect handling** (B17.7): extends `_detect_ignorance` (`fsm_logic.py:1987`); on repeated misses for K-2/3-5,
  switch to encouragement + simpler scaffold rather than escalating difficulty.

## 7. Content generation (catalog) must also be band-aware
`ContentHydrator` (course_builder.py) writes concept Markdown at the course's band: vocabulary, example concreteness,
and the Socratic Hooks section are generated using the same `GRADE_BAND_PROFILES` register so the *content* a
9-12 Biology concept carries differs from a 3-5 Science concept. This is why band lives on the course too, not
just the student.

## 8. Acceptance criteria (tests)
- Snapshot test: same `concept_uid` + same question type yields outputs whose word count and reading level differ
  monotonically across bands (K-2 < 3-5 < 6-8 < 9-12 by word count; readability score inversely).
- Grading test: a correct one-word answer to a K-2 question grades ≥3; the same terseness at 9-12 may grade 2.
- Mastery test: a K-2 course with bloom_ceiling 3 can complete a concept (regression for B3.5).
- Bound test: `current_bloom_level` never exceeds the band ceiling nor drops below floor.
- Manual: review 5 transcripts per band for register appropriateness before R1 ship.

## 9. Open questions
- Exact `max_words` caps — validate against young-reader comprehension; may split K-2 into K-1 / 2 if needed.
- Whether `grade_numeric` should continuously interpolate caps within a band (v2) vs fixed per-band (v1 = fixed).
