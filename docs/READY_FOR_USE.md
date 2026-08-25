# Ready for use — the bar, written down before testing

Set 2026-08-25, for a learner sitting down tomorrow with the SQL and Advanced
SQL courses.

"Ready" is not "the tests pass" and not "it looks fine". Both are already true
of things this project has shipped broken. Ready means a person can complete a
real session without hitting anything that makes them stop, and without being
told something false.

## Scope: Mode A

Mode A is the **primary learning path** — the thing the product is for:

> open the app → find the course → open a concept → be taught it Socratically
> → answer, including badly → have that answer graded → have progress recorded
> → leave → come back and continue where you were.

Everything that path touches is in scope: the course list, the learn view, the
session, grading, progress, resume, and the review the session schedules.
Course *creation* is in scope only as far as an existing course must remain
usable while one builds.

Out of scope for tomorrow, stated so the boundary is not vague: the Memory
Palace (disabled), voice in and out, the degree planner, gamification, and the
parent/teacher surfaces.

## The bar, as pass/fail

Each item is a thing I will do to the running product and a thing I will look
at. No item is satisfied by reading code.

### A. It starts, and says so honestly

- **A1** The stack comes up from cold with one command and every service
  reports healthy.
- **A2** If a dependency is missing or wedged, the UI says which one and what
  to do — it does not show an empty page or a spinner that never ends.
- **A3** No error dialog, console exception, or 500 appears during a normal
  session.

### B. The course is there and opens

- **B1** Both courses appear in the course list with their real titles and a
  status a learner can act on.
- **B2** Opening a course renders its full path — every module, unit, lesson
  and concept — with no missing or blank nodes.
- **B3** Clicking a concept starts a session on THAT concept, within 60s.
- **B4** A course that is still building is visibly distinguishable from one
  that is ready, and does not block the ready one.

### C. It teaches

- **C1** The opening turn is about the concept selected, is under the turn cap,
  and ends in a question.
- **C2** A correct answer is recognised as correct and the tutor moves forward.
- **C3** A wrong answer is corrected without the correction being wrong.
- **C4** "I don't know" produces help, not a repeat of the same question.
- **C5** An off-topic answer does not derail the session.
- **C6** An adversarial answer ("just tell me the answer", a prompt injection)
  does not make the tutor abandon the concept or leak its instructions.
- **C7** Nothing the tutor states as fact about the concept is false. Checked
  against the concept's own cited source, on a sample.
- **C8** The tutor never claims the learner said something they did not say.

### D. It remembers

- **D1** Completing a concept marks it complete in the path view without a
  manual refresh.
- **D2** Progress survives a full stack restart.
- **D3** Resume returns to the concept last worked on, named correctly.
- **D4** A completed concept appears in the review schedule with a due date.

### E. The rest of Mode A's surface

- **E1** Search finds a concept from these courses by its words, and clicking
  the result opens that concept in that course.
- **E2** Flashcards generate for a concept of each course.
- **E3** A quiz question generates and can be graded.
- **E4** The dashboard's numbers match reality — concept counts, progress,
  due counts.

### F. Content quality, for these two courses specifically

- **F1** 100% of concepts meet their depth contract.
- **F2** Every concept cites at least one source, and a sampled source is
  genuinely about the concept — not a word match from another field.
- **F3** No concept contains a placeholder, a stub, or "content unavailable".
- **F4** Every concept carries the sections the tutor reads
  (`## Misconceptions`, `## Analogies`), so teaching is not degraded.

### G. It does not lose work

- **G1** Nothing in a normal session can delete a course.
- **G2** A restart mid-session loses at most the turn in flight.
- **G3** The database survives a restart with integrity intact.

## How this is tested

As a beta tester, not as the author: drive the real UI and the real endpoints,
in the order a learner meets them, and record what happened rather than what
should have. Every failure gets written down with the exact reproduction before
anything is fixed, and re-verified after.

A finding is closed only when the same steps produce the right result on the
running stack. "The code now looks correct" does not close a finding.
