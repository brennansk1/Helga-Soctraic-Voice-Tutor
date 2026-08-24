# Authoring from a stronger model

Helga builds courses with the local model. This is the surface that lets a
larger one — Claude, or anything that speaks HTTP — take over any part of that
build, at any point, and hand the rest back.

The design constraint is one sentence: **a course authored here is held to the
same bar as one Helga wrote itself.** Not a bypass, not an import path. The
depth contract judges the content, the degree validator and shape gate judge
the plan, and work that falls short is refused with its reasons rather than
stored.

Helga still calls nothing out. This is an inbound API on your own machine; the
external model is driven by you, not by Helga.

---

## Start here

```bash
curl localhost:5002/api/pipeline
```

The surface describes itself — every route, the quality rule, the handback
mechanism, and what is free about the structure. It is written to be read by
the model that is about to use it.

| Route | What it is for |
|---|---|
| `GET /api/pipeline` | this description |
| `GET /api/pipeline/presets` | preset → scope, mastery, starting_from |
| `GET /api/pipeline/contract?mastery=&domain=&topic=` | the bar a body must clear, **before** writing it |
| `GET /api/pipeline/course/<uid>` | per-concept state: content, words, kind, author |
| `GET /api/pipeline/course/<uid>/concept/<cuid>` | one concept, its context, and its bar |
| `POST /api/pipeline/course` | a whole course — structure and bodies — in one request |
| `PUT /api/pipeline/course/<uid>/concept/<cuid>` | take over one concept |
| `PUT /api/pipeline/course/<uid>/concepts` | take over many at once |
| `POST /api/pipeline/course/<uid>/concept/<cuid>/asset` | attach a diagram or image (licence required) |
| `POST /api/pipeline/course/<uid>/finalize` | judge every body, set the status from the verdict |
| `POST /api/pipeline/course/<uid>/resume` | hand the rest back to the local model |
| `POST /api/pipeline/program` | hand in a whole degree plan |
| `GET /api/pipeline/program/<uid>` | a degree and how much of it exists |

---

## A whole course in one request

`POST /api/pipeline/course` takes the entire curriculum — modules, units,
lessons, concepts and the markdown for each — in a single payload.

That is deliberate. A large model can hold a whole curriculum in one context:
it knows what module six will say while it writes module one, so it can order
concepts, avoid repeating itself, and pitch each body at what the learner has
already been told. Splitting that across ninety calls throws away the one
advantage it has, and takes longer than the local model does.

```json
{
  "title": "Reading a Query Plan",
  "model": "claude-opus-5",
  "mastery": 3,
  "teaching_domain": "computer_science",
  "context": "I already write joins. I want to read EXPLAIN output for analytics work.",
  "modules": [
    {"title": "What the Planner Produces",
     "concepts": [
       {"title": "What a Query Plan Is", "content": "# What a Query Plan Is\n\n**Definition.** ..."}
     ]}
  ]
}
```

### The structure is free

Any number of modules. Any number of concepts per module. Uneven sizes. Units
and lessons omitted entirely if you think in modules-and-concepts — they are
synthesised rather than demanded.

The preset counts exist to size a **local** build, which has to guess how much
a subject can carry before it has written any of it. A caller holding the whole
curriculum already knows, and being made to pad a thin module to a target or
split a genuinely large one makes the course worse. Nothing is refused,
truncated or padded for its shape.

### The bar the content is held to

Read `GET /api/pipeline/contract` first. It returns the word range and the
required elements for the mastery level you are writing at — at mastery 3, for
example, 320–1500 words with a formal definition, a worked example and at least
one source URL.

Content that misses it is **refused, not stored**, and the response says what
was missing per concept:

```json
{"uid": "con_fcf8a275", "words": 374, "ok": false,
 "problems": ["missing required element: formal_definition"],
 "hint": "Revise so that it: state a precise formal definition of the key term."}
```

The local pipeline retries and records a miss, because something has to exist
for the learner. A caller that can rewrite is told what is wrong instead. Pass
`allow_below_contract=true` to store anyway — the course then cannot claim
`ready` on that concept.

---

## The handback

Content is optional per concept. Write the twenty you care about, leave the
seventy you do not, and `POST .../resume`.

There is no coordination to do and no work list to maintain. The local
hydrator already skips any concept that has a body, so what is missing **is**
the queue, derived from the course itself. That is why stepping in at any point
works at all.

Three things reach the local model when it finishes your course:

- **The brief** — the `context` you posted is stored on the course, not used
  once and discarded. A handback is the case with the least context of all: the
  concepts left behind are titles in a structure someone else designed.
- **Research** — Wikipedia and SearXNG run per concept, with a broaden step
  when confidence is below the floor.
- **The depth contract** — the same one your content faced, with retries.

Measured, brief = *"I write Python and I parse log files … not the theory of
finite automata"*: the locally-written concepts came back full of Python and
log examples and no automata theory.

`POST .../finalize` then judges every body and sets the course status from the
verdict — `ready` only if every concept clears its contract.

---

## Degrees

`POST /api/pipeline/program` takes a finished plan: `{subject, template,
courses: [{title, term, slot, requires}]}`.

A degree is the artefact where holding the whole thing in one context matters
most, because the constraint that makes it a degree rather than a course list
is the prerequisite graph — and a graph is exactly what a model reasons about
badly one course at a time.

Two gates run, the same two a locally planned degree faces:

1. **`program.validate`** — what makes a programme *unteachable*: a
   prerequisite cycle, a prerequisite that is not in the programme, one
   scheduled no earlier than the course needing it.
2. **`tools.degree_quality.assess`** — whether it is *shaped* like a degree:
   terms of comparable size, a capstone at the end, prerequisite sets that
   distinguish siblings rather than "everything that came before", real titles
   rather than numbered placeholders, and at least one real edge.

Both refuse with reasons and save nothing. The gate is arithmetic on the plan —
no model, no latency.

The `context` on a plan is kept **on** the plan, because each course inside a
programme is built later and alone; without it, "Statistics" inside a
psychology degree is built as a generic maths course.

---

## Provenance

Every concept records who wrote it. `GET /api/pipeline/course/<uid>` reports
`written_by` per concept, so a course built by both models is legible
afterwards:

```
Tables and Rows              325w  by claude-opus-5
Filtering with WHERE        1060w  by nail-35b-a3b-ctx
```

---

## What this surface does not do

- **There is no authentication.** Any client that can reach the port can write
  course content. That is acceptable on a laptop and is not acceptable exposed
  to a network — put it behind something that authenticates before you tunnel
  it.
- Fact-check, grounding and coverage verdicts belong to the local build and do
  **not** run on content posted here. The course reports `verdicts_pending`
  rather than inheriting a pass it never earned.
