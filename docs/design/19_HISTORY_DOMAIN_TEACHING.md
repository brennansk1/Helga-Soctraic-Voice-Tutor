# History domain — where the failure is ASKING, not telling

**Status:** built 2026-08-22. Third domain, after computer science and
mathematics.

---

## 1. The constraint runs the other way

Both existing domains share a constraint: never make the learner produce
something nobody can check. Computer science must not ask for typed code;
mathematics must not ask for a solved answer.

History's is the mirror image, and it is sharper:

> **Never ask a learner to guess a contingent fact.**

You can elicit *why* the July Crisis escalated. You cannot elicit that Hastings
was 14 October 1066 — a date is true because it happened, and no amount of
reasoning reaches it. Asking is a quiz with the answer withheld, which
Koedinger & Aleven's assistance dilemma names directly: withholding help stops
helping at some point.

`honest_telling` — the benchmark dimension that scores exactly this — is
**2.20** for history, the second lowest of any domain.

So `FACT` ranks **0**, above every other kind. A concept that is both "a date"
and "about causation" is taught as the date, because every other kind's
guidance invites reasoning and reasoning toward a contingent fact is
impossible.

---

## 2. The two-sided rubric, and why hedging is not the answer

`contested_interpretation` penalises **both** directions:

| failure | scores |
|---|---|
| flattening a live historiographical debate into one settled story | low |
| inventing a controversy where historians broadly agree | low |

A module that presents everything as contested therefore scores no better than
one that presents everything as settled. That symmetry is the whole difficulty
of the domain, and it cannot be recovered at teaching time from a title.

**The guard is evidential.** A `HISTORIOGRAPHY` move requires **two NAMED
historians** taking different positions in the source. "Some historians argue"
is refused: that construction appears just as readily in front of a settled
question, so it is evidence of nothing. Two named people disagreeing in the
text is evidence; a hedge is not.

---

## 3. The moves, from Wineburg

The Stanford History Education Group's finding is that the skills unique to the
historian's work are **sourcing, contextualization and corroboration** — and
that working historians read a document's attribution *first* while students
read it last, if at all.

All three are answerable from material in front of the learner, need no recall,
and have a checkable answer in the source itself — which is exactly what this
domain needs, given it cannot ask for recall.

| move | the question it asks |
|---|---|
| `SOURCE_CHECK` | given who wrote this, when, and for whom — what would they stress, and leave out? |
| `CORROBORATE` | where do two accounts agree, and where do they diverge? (before any question of who is right) |
| `HISTORIOGRAPHY` | what would have to be true for one reading to be the better one? |
| `COUNTERFACTUAL` | which cause, if absent, most likely changes the outcome? |

**An extract without provenance is refused.** Sourcing is a question *about* the
attribution; without one there is nothing to ask and the extract is merely a
quotation.

---

## 4. Nine kinds

`FACT` · `MISCONCEPTION` · `CONTESTED` · `SOURCE` · `CHRONOLOGY` · `CAUSATION`
· `SIGNIFICANCE` · `CONTEXT` · `CONTINUITY`

None shared with the other domains. The distinctions that decide teaching here
are between a thing that simply **is the case**, a thing that **follows** from
what came before, and a thing **historians genuinely dispute** — and the
benchmark's own topics are one of each, marked `derivable` False, True, True.
The kinds agree with that flag, which they must, or the domain teaches against
its own instrument.

**No `SHAPE` override**, for the same reason as mathematics: history is taught
on a calendar and `SCHOOL_SHAPE` was calibrated for exactly that.

---

## 5. Measured

Eight turns through the production prompt path, `fsm_logic._domain_teaching`:

| | |
|---|---|
| turns | 8 |
| ended with one question | 7/8 |
| **asked the learner to guess a fact** | **0** |
| empty | 0 |
| FACT concepts — stated the fact outright | **2/2** |
| CONTESTED — named two positions | **2/2** |
| CONTESTED — resolved the debate | **0** (must be 0) |

The detectors were validated first, 9/9, on hand-built cases — no false
positives on legitimate Socratic questions ("what would have to be true for
Fischer to be right?") and every violation caught. The computer-science
equivalent of this detector once flagged its own prohibition as a violation, so
the check is now standard practice before any measurement is reported.

**One anomaly, recorded rather than diagnosed.** In the first run the
`CHRONOLOGY` turn answered about compound interest — entirely unrelated to the
July Crisis. It did not reproduce: on retry, with title-only context *and* with
full context, both turns were on topic. Observed once at temperature 0.7, not
reproducible, cause unknown.

---

## 6. Wiring

Verified through `fsm_logic._domain_teaching` before any claim was made — the
"component works, path never fires" defect has now occurred nine times in this
repository, once in the mathematics domain during the sprint that documented
it. History attaches its material under `teaching_pair`, the field the FSM
actually reads, and `tests/domains/test_domain_reaches_the_tutor.py` fails any
domain that writes a `teach*` field the FSM does not read.

Behaviour routing works through the same optional `choose_move` hook
mathematics introduced: a bluffer and a stuck learner get a `SOURCE_CHECK`
(something concrete to commit to), an advanced learner gets the
`HISTORIOGRAPHY`.

A `FACT` concept gets **no** attached material at all. A date needs stating,
and giving it a source exercise would invite the reasoning-toward-a-fact this
domain exists to prevent.

---

## 7. Measured on the benchmark — and a problem with the dimension

Two runs, n=15 each, fingerprint `4faf5407715a9e4d`:

| dimension | A | B |
|---|---|---|
| **contested_interpretation** | **1.47** | **1.33** |
| adaptation | 1.80 | 2.13 |
| socratic | 2.27 | 2.47 |
| progression | 2.27 | 2.73 |
| visual_integration | 1.93 | 2.87 |
| misconception_handling | 4.29 | 4.67 |
| honest_telling | 3.40 | 3.40 |
| accuracy | 4.40 | 4.07 |

**The domain's own dimension did not move**, while several others did.

### The dimension does not reward what the domain produces

`contested_interpretation` by topic in run B:

    The causes of the First World War    1.80
    The sequence of the July Crisis      1.20
    The date of the Battle of Hastings   1.00

Hastings is a **FACT** topic. There is no historiographical debate to present —
the date is not disputed, and the benchmark itself marks it `derivable: False`.

The guidance added for exactly this case IS being followed. Measured on run B's
Hastings dialogues: the one student who stated a wrong year was corrected
(1/1), and 3 of 5 turns said explicitly that the date is settled. And the topic
scored **1.00**, the floor.

So the tutor does the right thing and scores the minimum. The rubric defines a
penalty for *inventing* a controversy but **no positive behaviour for a
genuinely settled question**, which leaves the dimension unmeasurable on a
third of this domain's topics.

**That is feedback for the instrument, not for the tutor.** Either the FACT
topic should not be scored on `contested_interpretation`, or the rubric needs
to say what a good answer looks like when nothing is contested — "says plainly
that this is settled and does not manufacture a debate" would do.

### What IS verified, independently of that dimension

* build from a real textbook: 9/9 concepts classified, 0 unknown
* the LLM classifier fires in the real pipeline
* material pairing correct by kind after the fix in §8
* every FACT concept given no material at all
* 8 turns: 0 guessed facts, 2/2 facts stated outright, 2/2 contested questions
  with two named positions, 0 debates resolved

## 8. Two defects only a real build could show

**Provenance vocabulary missed memoranda.** A source reading "Memorandum from
the German Chancellor to the Foreign Office, Berlin, 1912" was refused for
having no provenance — the exact attribution a `SOURCE_CHECK` interrogates.
Widened to the document types diplomatic history actually uses.

**Material matched by vocabulary instead of kind.** Two concepts came out
swapped: "Timeline of July Crisis" (`CHRONOLOGY`) took the `HISTORIOGRAPHY`
move because Albertini's position mentions "the last week of **July**", leaving
the real `CONTESTED` concept with a source extract.

A concept's KIND states what it NEEDS; its vocabulary only says what it
MENTIONS. Kind affinity now decides, word overlap is the fallback. This refines
the equivalent mathematics fix rather than repeating it: mathematics needed
matching at all, history needed it keyed on the right signal.

## 9. Not yet done

* the LLM concept classifier for thin titles (both other domains have one)
* a course built end to end from a real history textbook
* `contested_interpretation` measured on the benchmark rather than by the
  detectors above
