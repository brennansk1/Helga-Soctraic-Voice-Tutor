# "Build module by module" does not build module by module

Measured 2026-08-28 by walking the wizard end to end, not by reading it.

## What I entered

* Title: **HTTP Caching Headers**
* Goal: "I already know HTTP basics. I want to understand Cache-Control and
  ETag well enough to debug a stale-response bug."
* **One** module: "Cache-Control and ETag"
* Note on that module: "Keep this small: three concepts only."
* **Three** named concepts: max-age and response freshness · no-cache versus
  no-store · ETag revalidation and the 304 response
* Three clarifying questions answered.

## What it built

| | entered | built |
|---|---|---|
| modules | 1 (mine) | **6** (none of them mine) |
| concepts | 3 (named) | **145** |
| learner context | a sentence | *empty* |

The six modules — Cache-Control Directives, Expires and Last-Modified, ETag and
Validation Logic, S-Cache and Vary Header, Stale Content and Revalidation,
Cache Invalidation Strategies — are a reasonable generic syllabus for the
title. That is the point: it is a **topic** course. `build_state` recorded
`source="topic"`, which is exactly what it was.

## Why

`wizard.js` posts everything it collected to **`/api/create_course_custom`**
(fsm_logic). That route reads one field:

```python
data = request.json or {}
title = data.get("title", "").strip()
...
fsm.start_creation(f"create course {title} with depth 3", epub_filepath=None)
```

Modules, per-module notes, named concepts, description, teaching style and the
clarification answers are all present in `data` and none are read.

A route that DOES honour them exists: **`/api/custom_course/create`** in
librarian, which requires `modules` plus a `structure` and refuses without
them. Its name differs from the one the wizard calls by word order alone —
`create_course_custom` versus `custom_course/create` — which is the likeliest
way this happened.

## What was fixed, and what was not

Fixed: the description now reaches the build as `learner_context`, and the
teaching style is applied, through the same `_pending_course_params` mechanism
the degree planner already uses. So the course is at least built *for* the
learner who asked.

Not fixed: **the module outline and the named concepts.** That is not a missing
line in the route — the wizard has no `structure` to send, because it never
calls `/api/custom_course/preview`. The real fix is to run the wizard through
preview → create:

1. Step 3 or 4 POSTs the collected modules to `/api/custom_course/preview` and
   shows the returned structure for confirmation (the step already exists in
   the UI vocabulary — "Generate" is step 5).
2. Step 5 POSTs `{title, description, teaching_style, modules, structure}` to
   `/api/custom_course/create`.
3. `/api/create_course_custom` in fsm_logic is then either deleted or kept
   explicitly as the title-only path, and named so that the two cannot be
   confused again.

That is a change to the flow and to two services, and it wants its own
verification pass rather than being folded into an unrelated one.

## Why this matters more than its size suggests

Three creation entry points are advertised on /courses: Create a course, Build
module by module, and Import a course. Two of them build from a title. The one
that promises the learner control over the structure currently accepts that
control and discards it — and tells them nothing.
