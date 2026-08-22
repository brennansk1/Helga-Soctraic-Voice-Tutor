# Teaching computer science — the integration plan

**Written 2026-08-21.** Scope: computer science only. Every judgement here is a
CS judgement and lives in `services/domains/computer_science/`. A history or
chemistry extension will answer the same questions differently, which is the
whole reason the package exists.

---

## 1. What the evidence says, and what it changes

Three findings from the literature changed the design after it was first built.

### 1.1 Faded examples beat complete ones

> Students working through **faded** worked examples significantly outperformed
> students working through traditional worked examples on algorithmic
> performance, and were better on *instructional efficiency* — performance
> combined with cognitive effort.
> — [Springer, Technology, Knowledge and Learning (2025)](https://link.springer.com/article/10.1007/s10758-025-09901-2), [ERIC EJ1086007](https://files.eric.ed.gov/fulltext/EJ1086007.pdf)

Fading is a *progression*, not a state: complete example → partially completed →
independent. The first implementation of `code_examples.py` emitted ONE blank,
always — which is the middle rung of a three-rung ladder, given to everyone
regardless of what they had shown.

**Change:** blanks are a function of demonstrated competence, not a constant.

### 1.2 Expertise reversal

> Worked examples are superior to unguided problem solving **particularly for
> novices**, because they reduce cognitive load.
> — Cognitive Load Theory; [Booth et al.](https://files.eric.ed.gov/fulltext/ED566953.pdf)

The corollary is the trap: the same complete example given to a competent
student is *worse* than asking them to solve it. So the ladder must climb.

**Change:** the fade level reads `TurnState` and the correct-streak the FSM
already tracks. Those exist; nothing new needs measuring.

### 1.3 Erroneous examples are their own technique

Studied head-to-head against worked examples and tutored problem solving
([ResearchGate 282524740](https://www.researchgate.net/publication/282524740)).
Showing broken code and asking what is wrong is not a lesser version of showing
correct code — it teaches a different skill.

**Change:** `DEBUGGING` keeps its own path (broken case + `highlight`), rather
than being treated as a variant of `PROCEDURE`.

### 1.4 Prerequisite structure is discoverable, not guessable

Automatic prerequisite discovery from textbooks and LLM-assisted prerequisite
identification are both established
([arXiv 2011.10337](https://arxiv.org/pdf/2011.10337),
[arXiv 2402.01672](https://arxiv.org/pdf/2402.01672)).

Kind-based ranking (orientation → tooling → syntax → procedure → mechanism →
debugging → reference) is a **coarse prior**. It correctly put installation
before building in the dbt course, and it cannot tell that `defer` is advanced.

**Change:** kind ranking stays as the cheap deterministic layer; an LLM pass
refines within-rank order and classifies the concepts patterns cannot.

---

## 2. The three-rung ladder

| rung | when | what the tutor shows |
|---|---|---|
| **0 — worked** | first exposure; no demonstrated competence | the complete, correct example. No blanks. Ask what one line *does*. |
| **1 — faded** | at least one concept established on this topic | the same shape with 1–2 tokens removed. The tutor holds the answers. |
| **2 — independent** | streak of correct answers, or high Bloom | the *task* and a skeleton, most of the body removed. |

Two properties this must keep:

- **The answer is always known server-side.** The builder removes a token it
  read from the source, so the tutor can mark the reply. Without that, "practice"
  is the confident-bluffer failure in SQL: plausible, wrong, affirmed.
- **The example is from the source.** dbt's docs carry 264 code blocks in 45
  pages; a lifted example is correct by construction, an invented one is a guess
  about a version the model may not have seen.

---

## 3. Where each piece hooks in

| stage | hook | what happens |
|---|---|---|
| **crawl / parse** | `doc_reader`, `book_reader` | code blocks survive extraction with line structure intact — the precondition for everything below |
| **sequence** | `doc_reader.sequence` + `concept_kind.rank` | coarse teaching order; junk dropped |
| **skeleton** | `build_from_docs` | shape recorded with a *why* |
| **name concepts** | `book_source.attach_concepts` | named by READING the page, via `llm_generate_json` |
| **classify** | *(new)* `classify_course` | LLM fills the kinds patterns cannot — 25 of 40 dbt lessons |
| **assets** | `code_examples.attach_to_course` | one vetted example per code-shaped concept, deduplicated course-wide |
| **tutor** | `prompts.get_socratic_tutor_prompt` | `concept_kind` guidance, routed through the domain registry |

---

## 4. What is deliberately NOT built

**No code execution.** A sandbox is the honest way to verify a student's code,
and it is a substantial build with real security surface. Until it exists, the
product teaches *reasoning about* code and *completing* code, and the release
notes must say so rather than implying practice it cannot check.

**No generated examples when the source has none.** A concept whose chapter
carries no code gets no code aid. Inventing one puts an unverified snippet in
front of a learner, which is the failure mode this whole design avoids.

**No cross-domain defaults.** `registry.for_subject` returns None for history,
and None means "use the generic path". A domain without a specialist is the
normal case.
