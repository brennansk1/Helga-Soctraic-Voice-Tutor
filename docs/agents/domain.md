# Domain

Single-context repository: **Helga**, an offline Socratic voice tutor running on
a Mac Mini M4 Pro (24 GB unified memory).

## Shape

Microservices behind a Flask + Socket.IO web UI on **port 5050** (not 5000 —
that is macOS AirPlay). Inference is **Ollama native on the host**, not a
container, reached at `host.docker.internal:11434`.

| Service | Port | Role |
|---|---|---|
| web-ui | 5050→5000 | Flask dashboard, proxies to the rest |
| core-logic | 5003 | FSM (all tutoring interaction) + course creation |
| rag-engine | 5002 | course CRUD, FTS5 search, flashcards |
| research | 5006 | build-time grounding (Wikipedia, Wikibooks, SearXNG) |
| searxng | 8080 | self-hosted web search |
| tts / stt | 5005 / 5001 | Kokoro TTS, Nemotron ASR — host-native, containers behind `--profile portable` |

## The pipeline that matters

    Phase 1  what should this course contain?   curriculum_research + skeleton
    Phase 2  what does each concept say?        ContentHydrator + research
    Phase 3  what does the learner LOOK at?     asset_collector

## Vocabulary

- **preset** — one of 8 learner-facing configurations (scope / mastery /
  starting_from). The mastery number selects the depth contract, so the
  marketing copy and the enforcement are the same number.
- **depth contract** — per-mastery word band + required elements
  (`formal_definition`, `worked_example`, `named_result`,
  `derivation_or_proof`, `primary_source`, `exercise`). MONOTONIC: level N+1
  requires everything N does.
- **the gate** — six conjunctive criteria. A course is not good because it
  averages well; the failure being defended against is *structurally
  impeccable and substantively hollow*.
- **node-based** — Helga is the Duolingo of a college course. Delivery format
  SHOULD differ from a university syllabus; penalising that measures the wrong
  thing. See `MODE_A_STATUS.md` §4d.
- **Mode A** — Personal/Scholar, a self-directed adult. Mode B (Student/Guided)
  is a placeholder.

## Standing rule, learned expensively

Every instrument in this repo has been wrong at least once, usually
manufacturing a verdict out of no information. **Run the thing and read the
output; do not infer behaviour from the code.** Thirteen defects on 2026-08-07
were found that way, and four confident readings of `fsm_logic.py` were wrong
before one was right.
