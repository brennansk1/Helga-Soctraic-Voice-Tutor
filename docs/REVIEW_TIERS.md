# The four review tiers, and what each can be graded against

Written 2026-08-28 from the live item bank, not from the design.

Review items are **extracted** from concept markdown, never generated at review
time — there is no model call in a review session, which is what makes a
session usable on this hardware. Everything below follows from that.

| Tier | What it is | How it is graded |
|---|---|---|
| `recall` | cloze / definition | learner self-rates 1–4, FSRS |
| `discriminate` | true–false with a verdict | objective: the stored verdict |
| `apply` | a consequence or edge case | model, against the concept text |
| `socratic` | explain-why | model, against the concept's own **Mastery Criteria** |

## Objective grading by execution: measured, and not built

The plan called for apply items to be "graded objectively by execution where
content allows". The content does not allow it, and this is the measurement
rather than an impression. Over the live bank (SQL and Advanced SQL, 1,241
apply + discriminate items):

* **15** items contain a runnable `SELECT … FROM` anywhere in the question.
* **2** ask the learner to write a query.
* **0** quote a query and ask what it returns.

So an execution grader would serve two items in twelve hundred. The machinery is
not the obstacle — `services/core/sql_ground_truth.py` already executes SQL
against the `helga-sqlcheck` Postgres, and the builder and auditor already use
it to verify claims in generated content. The obstacle is upstream: the
extractor builds apply items out of prose edge-cases ("what happens with
ambiguous column references?"), because that is what the concept documents
contain. There are no worked exercises with expected outputs to extract.

**What would actually unlock it** is a change to what gets written, not to how
answers are marked: concept documents would need a section of exercises with
executable answers, and `review_items.extract()` a rule that turns each into an
item carrying the query and its expected result. Then execution grading has
something to grade, and it becomes the obviously correct way to mark those
items. Until then, building the grader first would be building a road to
nowhere.

`discriminate` items are already graded objectively — the verdict is stored with
the item — so the tier that could be objective without new content already is.

## The three that were built

* **Socratic answers are graded against the concept's own Mastery Criteria**
  (`/api/review/check_answer`, `_RUBRIC_SCHEMA`), not against a generic rubric.
* **Repeated failure routes to the weak prerequisite.** A leech offers a repair
  path to the weakest ancestor in the prerequisite DAG (`weakest_root`,
  `offerRepair`) rather than showing the same failed item again.
* **`desired_retention` is exposed in Settings** and is the real FSRS lever:
  0.85 / 0.90 / 0.95 measured at 13 / 9 / 4 days of interval on the same card.

## A tier a course can fail to build at all

Extraction means a course whose concepts lack the sections the tutor reads
yields only the prose fallback, which is `recall`. Measured: "Reading a Query
Plan" holds 26 items, every one `recall`. The review queue reports
`tiers_present` and `recall_only` so the Practice tab can say so — a
factual-only session is the case the evidence says does not transfer, and it
must not look like a full one.
