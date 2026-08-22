# Mode B (K-12) — research findings

**Received:** 2026-08-21. Answers `MODE_B_RESEARCH_BRIEF.md`.
**Status:** the evidence base for Mode B design. Read with the brief.

> These are the findings as delivered, condensed. Where a claim carries a
> citation, the citation is the research's, not ours. The Caveats section is
> load-bearing — several of the strongest-sounding conclusions are flagged by
> the research itself as extrapolation.

---

## The verdict on our central hypothesis

We hypothesised: *"For K-2, Socratic questioning is the wrong primary mode
entirely."*

**Partly right — right conclusion, wrong reason, and the boundary is later than
we thought.** The hypothesis bundled two separable claims:

- **"Socratic questioning is developmentally wrong for under-8s" — FALSE.**
  Guided dialogue demonstrably helps children well under 8. Robin Alexander's
  Dialogic Teaching EEF RCT (2014-17, 2,493 Year 5 pupils, 76 schools) produced
  **two additional months' progress in English and science, one in maths**, at
  a three-padlock EEF security rating. Dialogic book-sharing improves language
  in preschoolers.
- **"Direct instruction + heavy visuals + voice + structured practice should
  dominate K-2" — TRUE**, for three reasons we did not fully name:
  1. **Novices need guidance.** Kirschner/Sweller/Clark (2006) and Stockard et
     al. (2018, *Review of Educational Research* 88(4), 328 studies, ~4,000
     effects) — explicit instruction beats minimal-guidance discovery for
     novices; reading effect ~0.66. The advantage "begins to recede only when
     learners have sufficiently high prior knowledge". A child is a novice in
     nearly every standard they meet.
  2. **Our Socratic engine specifically scores ~2.1/5** and three interventions
     failed to move it. Betting a six-year-old's instruction on our weakest
     subsystem is the highest-risk choice available.
  3. **The mechanics defeat dialogue** — see below.

**Therefore: fix the tutor AND route K-1/2-3 around it. Not either/or.**

## The binding constraints for the youngest are mechanical, not cognitive-limits-on-reasoning

- **Working memory** ~4 items at age 5, ~5 at 7, 6-7 only by adolescence
  (Gathercole et al. 2004; Cowan). A six-year-old cannot hold a multi-clause
  question + their half-formed answer + a hint simultaneously.
- **ASR on child speech is 2-5× worse than adult.** Word error rate by age:
  **26.9% (5), 14.6% (6), 10.5% (7), 5.1% (8+)** (Yeung & Alwan 2018).
  Open-source models on 4-9 year-olds: **WER >40%** for the youngest. Adult-level
  accuracy only around **age 13-14**. Decisive: below ~grade 2, free-form spoken
  answers cannot be trusted, and a pre-reader cannot read a correction.
- **Typing** is not reliable before ~grade 2, and not a primary input until
  ~grade 4-5.

## Re-banding: K-1 | 2-3 | 4-5 | 6-8 | 9-12

Our four bands were **wrong at the bottom**. The discontinuity that matters is
**reading fluency** — "learning to read" → "reading to learn", ~end of grade
2-3 — not a grade line. Nielsen Norman Group's children's UX research
independently splits early childhood into pre-readers (3-5), beginning readers
(6-8), older children, confirming "K-2" spans two different users.

**Detect it, don't assume it.** Use oral reading fluency in words-correct-per-
minute on the non-gating diagnostic (Hasbrouck & Tindal 2017 norms):

| WCPM | Mode |
|---|---|
| < ~60 | pre-reader/emergent — voice primary, no required reading, tap/manipulative input, TTS + word highlighting |
| ~60-100 | transitional — short readable instructions, mixed voice/text, short typed words |
| > ~100 (+ working-memory & expressive-language check) | dialogue can carry more weight |

Reference points: end grade 1 ≈ 60 WCPM; end grade 2 ≈ 100; end grade 3 ≈ 107;
end grade 5 ≈ 139-146. Our own ASR can estimate WCPM from a read-aloud passage,
but child-ASR error means treat it as approximate and confirm near thresholds.

## Per-band parameters (replacing our judgement-set table)

| | K-1 | 2-3 | 4-5 | 6-8 | 9-12 |
|---|---|---|---|---|---|
| Max words/turn | **15** | 30 | 50 | 70 | 110 |
| Sentences | 1 | 1-2 | 2-3 | 2-4 | 3-5 |
| New ideas | 1 | 1 | 1 | 1-2 | 2 |
| Expected answer | a tap or one word | word/short phrase | sentence with reason | 1-2 sentences | multi-clause |
| Diagram budget/concept | 4-5 | 4 | 3 | 3 | 3 |
| TTS | ON + read-aloud + highlight | ON (read-aloud optional) | off (opt-in) | off | off |
| Mastery gate streak/Qs/types | 2/2/**1-2** | 2/3/2 | 2/3/3 | 2/3/3 | 3/4/3 |
| Primary mode | direct instruction + practice ~70%, dialogue ~10% | DI + guided practice ~60%, dialogue ~20% | guided practice + DI ~50%, dialogue ~30% | dialogue + practice ~50/50 | dialogue primary ~60% |
| Session length | 10-15 min, rotate every 5-8 | 15-20 min, rotate every 8-10 | 20-25 min | 25-35 min | 35-45 min |
| Autonomy | adult-launched, single-action screen | adult-launched, 1-2 choices | largely independent | independent | fully independent |

## Which question types survive contact with a young child

- **K-1:** only **Scenario, Application, simple Contrast** (concrete,
  here-and-now).
- **Mechanism, Synthesis:** require holding multiple elements in working memory
  — **grade 4+**.
- **Edge Case:** requires knowing a rule well enough to see its boundary —
  **grade 5+**.
- **Hint ladder:** the adult 4-step ladder is too long for K-1 working memory.
  For K-1 and 2-3, short-circuit to the worked example fast: one probe or one
  hint, then show them, then a parallel check. The "I don't know" →
  stop-questioning → micro-lecture → verify path matters *more* for young
  children and should trigger **sooner**.

## Cognitive load: narration for pre-readers is access, not redundancy

- **Modality effect** (Ginns 2005, 43 effects, d = 0.72; strongly moderated —
  d = 0.93 system-paced vs **−0.14 self-paced**; a 2011 replication reduced the
  estimate to d = 0.38, 0.20 after bias correction). Helga is **self-paced**, so
  do not over-rely on this.
- **Redundancy effect** (Mayer, median d ≈ 0.86) says simultaneous spoken +
  written text hurts — **but that literature is built on fluent-reading college
  students**, and its founding assumption (the learner can decode the text) does
  not hold for pre-readers. Adesope & Nesbit (2012, 57 studies) found
  spoken+written **beats** spoken-only for low-prior-knowledge learners
  (g = 0.34); Knoop-van Campen et al. (2018) found a **reversed** redundancy
  effect in 11-year-olds with dyslexia.
- **Conclusion:** for K-1, narrating on-screen words is the only access channel,
  and synchronised word highlighting additionally builds print awareness.
- **Caveat — transient information effect** (Leahy & Sweller 2011, tested on
  primary-school students): keep narration **short and segmented**.

## Praise: our "very high affirmation density" is a real risk

Brummelman et al. (2014, *Psychological Science*): **inflated praise** ("that's
incredibly beautiful!") reduces challenge-seeking in children with low
self-esteem; **person-praise** ("you're so smart") makes children give up sooner
after failure than **process-praise** ("you worked hard on that").
Longitudinally (2017, *Child Development*), parents' inflated praise predicted
*lower* child self-esteem.

**Re-specify as: high process-praise density, near-zero inflated/person praise.**

## Gamification genuinely inverts by age

The overjustification literature was built on **preschoolers** (Lepper, Greene &
Nisbett 1973, ages 3-5): expected extrinsic rewards for an already-enjoyed
activity roughly **halved** subsequent free-choice engagement. Deci, Koestner &
Ryan (1999, 128 experiments): engagement-, completion- and
performance-contingent rewards undermined intrinsic motivation (d = −0.40,
−0.36, −0.28) and — decisively — **"Tangible rewards tended to be more
detrimental for children than college students."**

Sailer & Homner (2020) found positive gamification effects (cognitive g = 0.49,
motivational 0.36, behavioural 0.25) but **did not disaggregate by age** and
skews adult. So: **age-gate extrinsic mechanics OFF for K-1**, phase in
cautiously for older children and only on genuinely dull practice, never on what
a child already enjoys. Keep the mastery map primary at every age.

## Session length

The "attention span = age in minutes" rule is **practitioner heuristic, not
robust experiment**, and applies to adult-directed, non-preferred tasks. Use it
as a **segment** length, not a session cap. See the band table above.

## Manipulatives: physical matters for the youngest

Carbonneau, Marley & Selig (2013, 55 studies, N = 7,237): small-to-moderate
benefit for concrete manipulatives over abstract symbols, moderated by design
(plainer manipulatives transfer better). Virtual manipulatives are competitive
and sometimes superior **at secondary level**, but concrete objects retain an
edge for young children on foundational number/fraction concepts. **CRA
(Concrete-Representational-Abstract) is well-supported.**

**Implication:** for K-2 maths a child should have physical manipulatives (a
kit), with our on-screen widgets as the *Representational* bridge — not a
substitute for the Concrete stage.

## Teaching what a screen cannot teach

**Recommendation: bring PE and studio arts INTO scope as parent-guided
off-screen modules — but market Helga as "academic-core-complete,
enrichment-supported".** Utah requires them to graduate (1.5 Fine Arts credits,
1.5 PE + 0.5 Health under R277-700-6), so an accredited-transcript ambition
cannot exclude them; but the evidence that parent-delivered guides get used is
weak, so **never let a physical requirement block academic progression.**

### Classification: two axes, not three points on one

1. **Epistemic** — derivable vs must-be-told (we already have this).
2. **Enactment channel** — pure-dialogue / dialogue+diagram / screen-practice /
   on-screen-manipulative / physical-manipulative / real-experiment /
   sustained-bodily-practice / supervised-instruction.

"Needs physical interaction" is **orthogonal**, not a third point on the
tell/derive line. Partly auto-classifiable: a standard's verb is a strong signal
("identify", "explain" → screen; "measure", "perform", "demonstrate",
"construct" → physical). **Auto-classify at build time, human-review the
physical subset** — mis-classifying a lab as screen-teachable produces a hollow
science education.

### The three mechanisms

- **Kits** — evidence supports hands-on kit learning. Emulate KiwiCo/MEL
  Science/Home Science Tools and homeschool kit curricula (Sonlight, Oak Meadow,
  Torchlight). **Sequence the kit during/around the concept** — manipulative
  in-hand when taught (CRA Concrete), experiment after the explanatory setup so
  the child predicts-then-tests.
- **Video** — adequate for observing phenomena and visualising the invisible;
  **inferior for procedural/measurement skill and for explanatory connection**
  (physical labs beat virtual on explanation quality). Actively worse than doing
  when the objective IS the manipulation. Integrate by questioning **before**
  (predict), **during** (notice), **after** (explain).
- **Parent-guided activity — the weakest mechanism, correctly identified.**
  Fidelity is the documented failure point: roughly **10% of evidence-based
  programs in real family settings are delivered as intended** (Biglan 2015).
  What makes a guide actually get used: (1) minimal prep and time; (2) explicit
  step-by-step scripts; (3) **materials pre-supplied** — a kit in a box beats
  "gather these items"; (4) a concrete completion checkpoint. **Assume low
  fidelity; make the minimum viable version still valuable.**

### Homework

Cooper, Robinson & Patall (2006, *RER* 76(1)): the homework-achievement
correlation is **near-zero for elementary** (r ≈ 0), ~+.07 middle, **~+.25 high
school**. The "10-minute rule" is the practitioner standard.

**Do NOT build mandatory achievement-homework into K-5.** Off-screen work in
early grades is justified **only** where the channel is irreplaceable
(experiment, PE, art, music practice).

### Verifying off-screen work — ranked by reliability × burden

1. **Child describes what they did back to the tutor + a follow-up question only
   a doer could answer.** Best ratio, works inside our existing dialogue engine,
   needs no vision model, **and can be graded normally by our existing grading
   call** — the cleanest bridge from off-screen work into the graded model.
2. **Parent attestation** — low burden, low reliability. Should produce a
   **capped, flagged, low-confidence** completion signal that unlocks
   progression but carries reduced FSRS weight.
3. **Photo** — we have **no resident vision model**; store for the parent/
   portfolio, do not machine-check.

Reminders go to the **parent only**, as a short "this week" list, not a stream.

### The offline constraint

- Purchase/video links surface on the **parent's dashboard** and an exported/
  printed materials sheet at course start — never in the child's reach, never
  requiring the offline machine to fetch.
- **Resolve materials once at course-build time** and treat as fixed content.
- Links rot: when a reference dies, show **the description of what's needed**
  ("a 250ml graduated cylinder") plus a generic search term — degrade
  gracefully, never show a broken link.

### Equity, safety, accreditation

- **Kits cost money** — provide a household-objects substitute path (beans for
  counters, measuring cups for volume, paper folding for fractions). Be honest
  that chemistry/microscopy have no safe household substitute; mark those
  optional-enrichment.
- **Safety:** age-graded hazard labels, explicit required-supervision flags,
  adult-only steps marked, disclaimers at materials-sheet time, hazardous
  experiments behind parent acknowledgment.
- **Accreditation is a LOW bar:** accredited homeschool/online programs accept
  parent attestation, logged hours and portfolios for PE and arts. Utah allows
  credit by course completion OR competency assessment (R277-705); homeschool
  families may set their own graduation criteria. **Design verification for
  pedagogical honesty, not for an accreditation bar that is already lenient.**

## UI/UX specifics

- **Tap targets ≥ 2cm × 2cm** for young children (NN/g) — roughly **60-75+ pt**,
  well above WCAG 2.2 AA's 24×24 CSS px floor and above Apple's 44pt / Material's
  48dp. K-1: **1-3 simultaneous on-screen choices**, no scrolling, no text input.
  2-3: up to ~4 choices, minimal scrolling. Icons must be **literal, not
  abstract**. Children treat anything button-like as tappable and abandon
  quickly if taps don't respond.
- **Body text** ≥16px, **18px+** for the youngest; line height ≥1.5; adjustable
  spacing (WCAG 1.4.12).
- **Dyslexia fonts:** evidence for OpenDyslexic/Dyslexie is **thin**. Use a
  well-designed sans-serif with generous x-height and distinct b/d/p/q (e.g.
  Atkinson Hyperlegible) + always-available TTS.
- **ASR error handling for a non-reader:** never surface a text correction.
  Treat ASR answers as low-confidence; fall back to tap/multiple-choice re-ask;
  re-play the question by voice; accept parent relay. **A wrong transcription
  must never be scored as a wrong answer** — prefer closed-response widgets for
  anything gated.
- **What changes with age** across Khan Academy Kids, Duolingo ABC, Teach Your
  Monster to Read, Osmo, Endless Alphabet: **reading dependence, navigation
  depth, and number of simultaneous choices** — not the feature list.
- **Four tabs is ~three too many for a kindergartener.** K-1: collapse to a
  **single "Today" screen with one action** ("Tap to start"); everything else
  adult-gated or hidden. Second tab at 2-3; full four-tab IA by 4-5. **The
  student shell should itself be age-adaptive in tab count.**

## Open questions we had not asked

- **Accessibility/neurodivergence: build into the one mode, don't fork.**
  Dyslexia → legible sans-serif + spacing + TTS (already have). ADHD → shorter
  segments, movement breaks, fewer simultaneous choices. Autism → predictability,
  consistent layout, previewable session structure, no surprising changes. These
  are "the same mode built better".
- **ELL:** the redundancy-reversal evidence means TTS + highlighting already
  serves ELLs; consider L1 support for instructions.
- **Parent trust by age:** parents of *younger* children want **more** transcript
  visibility, not less. Make young-child transcripts parent-visible by default
  while keeping crisis-specific sensitive content protected.
- **Attachment/anthropomorphism:** children anthropomorphise more than adults,
  **voice amplifies it**, and they form genuine parasocial attachments to voice
  assistants (Hoffman et al. 2021, 3-10 year-olds). Mitigate: **age-graded AI
  disclosure** ("Helga is a helpful computer program, not a person"), avoid
  over-humanising the persona for the youngest, avoid simulated emotional
  neediness, build in "go do something with a real person" nudges. **Yes, a
  child should know they're talking to an AI, at every age.** Thin-evidence
  territory — flagged as extrapolation.

---

## Caveats the research flagged about itself

**Read these before treating any number above as settled.**

- **Almost none of this is measured on AI tutors with young children
  specifically.** The developmental, cognitive-load, praise, manipulatives and
  homework literatures are robust but built on human teachers, classroom
  software, and (for cognitive load) mostly fluent-reading adults. The
  redundancy-reversal-for-pre-readers conclusion is a **reasoned inference from
  adjacent populations**, not a controlled experiment on true pre-readers.
- **"Attention span = age in minutes" is practitioner heuristic**, not
  experiment. Treat session lengths as starting values to tune from transcript
  review — exactly as the original table should have been.
- **Bloom's 2-sigma is overstated and has never been replicated at scale.**
  VanLehn (2011): human tutoring d = 0.79, step-based ITS d = 0.76, answer-based
  CAI d = 0.31. Modern tutoring-at-scale meta-analyses cluster around 0.3-0.4σ
  (Nickow, Oreopoulos & Quan 2020). **Helga is a strong intervention if it lands
  near ITS effect sizes — do not benchmark against a mythical 2σ.**
- **The gamification age-inversion rests on the overjustification literature,
  not on the gamification meta-analysis** (which is not age-disaggregated).
  Treat age-gating as evidence-informed prudence.
- **The WCPM switching thresholds are a synthesis** of Hasbrouck-Tindal norms
  applied to our use case, **not a validated switching rule.** Validate against
  our own data.
- **Parent-fidelity pessimism comes from clinical parent-training and
  home-visiting programs**, which may not transfer to motivated homeschool
  families who self-selected into a tutoring product. **Measure it before
  trusting it.**
- **The modality effect magnitude is contested** (d = 0.72 vs 0.20-0.38 after
  bias correction) and **reverses under learner-paced conditions (d = −0.14)**.
  Helga is self-paced, so the pre-reader case for narration rests on **access**
  — they cannot read the text at all — not on the modality effect.

## Thresholds that would change these recommendations

- K-1 single-action screen under-challenges children who handle two choices →
  add the second tab earlier.
- ASR on child speech improves to **<10% WER for six-year-olds** (currently
  20-40%) → reopen voice as a gated-answer channel for K-1.
- Parent-completion telemetry on off-screen modules exceeds **~60-70%** (vs the
  ~10% fidelity floor) → weight parent-attested work more heavily in FSRS.
  Below that, keep it capped and low-confidence.
- A resident vision model becomes affordable within the 24GB budget → photo
  verification becomes machine-checkable.
