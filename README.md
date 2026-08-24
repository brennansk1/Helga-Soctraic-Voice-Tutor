# Helga — a Socratic tutor that runs on your own machine

**Helga teaches by asking, not by telling.** Give it a topic and it builds a
course from researched sources, then works through that course with you in
dialogue — probing what you actually understand, drawing a diagram when a
picture carries what words cannot, and scheduling the review before you forget
it.

It runs **entirely offline**. No API keys, no cloud account, no telemetry,
nothing leaves the machine. The model, the search index, the speech synthesis
and your learning history all live on your hardware. Profiles are local: a
parent account and a student profile per learner, stored in your own database.

<!-- SCREENSHOT:hero -->

---

## Why it is different

|  | Helga |
|---|---|
| **Asks before it tells** | A tutor turn is capped and must end in a question. Claims about what *you* said are checked against the transcript before the turn ships. |
| **Knows you across sessions** | FSRS tracks stability, difficulty and lapses per concept. The tutor reads your own record — "you have missed this twice" — rather than modelling you as a percentage. |
| **Shows its sources** | Courses are built from Wikipedia, open textbooks and primary literature, with a visible grounding confidence rather than whatever the model remembered. |
| **Draws when it helps** | Fifteen figure kinds, including code listings with blanked lines. A diagram is staged so it cannot hand over the answer you are being asked for. |
| **Measured, not asserted** | Teaching quality is scored by [HelgaBench](docs/HELGABENCH.md) across seven domains, against a published noise floor. |

---

## Architecture

Three things run **natively on the host** because they need the GPU or the
Neural Engine, which a Linux container on macOS cannot reach. Everything else
is a container.

```
HOST (native)
  Ollama   :11434   inference — nail-35b-a3b-ctx (13.7 GB, 16k context)
  TTS      :5005    Kokoro-82M on MLX
  STT      :5001    Nemotron-3.5-ASR on MLX / ANE

DOCKER COMPOSE (5 services)
  web-ui     :5050  Flask + Socket.IO dashboard
  core-logic :5003  FSM, course creation, tutoring
  rag-engine :5002  SQLite, FTS5 search, flashcards
  research   :5006  build-time content augmentation
  searxng          self-hosted web search (internal)
```

> **The model is a local GGUF import, not a registry pull**, and the stack asks
> for the **`-ctx` variant** specifically — the base model ships with a 4096
> token context, which is too small for course building and fails *silently*
> with `400 — request exceeds available context size`. See
> [docs/MODEL.md](docs/MODEL.md). `./deploy.sh` creates the variant for you if
> the base model is present.

**Stack:** Python 3.11, Flask, SQLite (WAL + FTS5), sentence-transformers
(all-MiniLM-L6-v2), Kokoro TTS, Nemotron ASR, SearXNG, FSRS-5.

**Hardware:** developed on a Mac Mini M4 Pro (24 GB). The 24 GB figure is not
incidental — the model alone holds ~13.7 GB resident, and the containers share
what is left.

---

## Quick Start

### Prerequisites
- Docker Desktop
- [Ollama](https://ollama.com) installed natively
- ~14GB disk for model + ~2GB for Docker images

### Setup

```bash
# 1. Pull the LLM model (multimodal — text + vision)
# The project model is a LOCAL GGUF import, not a registry pull.
# See docs/MODEL.md for the one-time setup:
#   hf download peculiar-ragdoll/Nail-Qwen3.6-35B-A3B-GGUF ... && ollama create nail-35b-a3b
# Then the 16k-context variant the stack actually asks for (reuses the blob):
#   printf 'FROM nail-35b-a3b\nPARAMETER num_ctx 16384\n' > Modelfile.ctx
#   ollama create nail-35b-a3b-ctx -f Modelfile.ctx
# ./deploy.sh does this second step for you if the base model is present.

# 2. Clone and start
git clone <repo-url> && cd helga
./deploy.sh
```

`./deploy.sh` rather than `docker compose up`, and the difference matters.
Three services -- Ollama, TTS and STT -- run on the HOST, not in a container,
because they need the GPU or the Neural Engine and a Linux container on macOS
has neither. `docker compose up` starts none of them and sets none of the
Ollama environment that decides whether the stack feels fast, so it produces a
healthy-looking container stack with no voice in either direction and no error
explaining why. deploy.sh starts both halves, builds the 16k-context model if
you only have the base, and runs the preflight.

```bash
# 3. Open browser
open http://localhost:5050
```

If anything is missing, **http://localhost:5050/setup** checks each part of the
installation, says what is wrong in plain words, and gives you the exact
command to fix it. It re-checks itself, so you can leave it open while you
work through the list.

### Verify

```bash
make health    # host services + all 5 containers
make test      # run the test suite
make backup    # back up the SQLite database
```

---

## Features

### Socratic tutoring

<!-- SCREENSHOT:learn -->

- Six question types — clarification, probing, evidence, viewpoints,
  implications, application — chosen per turn rather than cycled
- Bloom's taxonomy tracking, Remember through Create
- Mastery needs repeated correct answers, so one lucky guess does not pass
- **A turn contract enforced in code, not requested in a prompt.** Every tutor
  turn must be under 60 words, must end in a question, must engage with what
  you actually said, and may introduce only one new idea. A turn that breaks a
  rule is regenerated against the *named* violation
- **Claims about you are checked against the transcript.** If a turn says "you
  said X" and you did not say X, it does not ship
- Micro-lecture fallback after three consecutive failures
- On content that genuinely cannot be derived — a convention, a name, a date —
  the tutor states it plainly instead of inviting you to guess

### Diagrams that are part of the teaching

<!-- SCREENSHOT:diagram -->

Fifteen figure kinds: number line, geometry, plot, bars, graph, timeline,
table, venn, cycle, steps, fraction, code, and more. Two properties matter:

- **Staged.** Any element can be hidden until you have answered, so the figure
  can pose the question without giving away the answer
- **Policy-driven.** A per-turn policy decides whether a figure helps at all,
  prefers one already built and checked at course-creation time, and withholds
  the diagram grammar entirely when the answer is no

For code, a listing can blank the line you are being asked about and show a
hint in its place — the difference between a listing that asks and one that
tells.

### Course creation, from researched sources

<!-- SCREENSHOT:courses -->

- **Quick create** — a topic, a level, and a sentence about what you want
- **Say what it is for** — an optional brief that outranks the subject word.
  "SQL" covers an analytics engineer, a backend developer and a DBA; given the
  word alone the builder produced a module on the history of the standard.
  Given *"window functions, for analytics work, not administration"* it
  produced six modules on frames, partitions and offset functions.
- **Custom wizard** — build it module by module
- **Your own material** — EPUB, PDF, Markdown and plain text, including
  figures extracted from the book
- Research runs at both skeleton and hydration time, so *what the course is
  made of* is decided from evidence rather than from one model call
- A syllabus-realism check compares the outline against a real syllabus and
  records a coverage verdict on the course

### Spaced repetition that drives the whole system

- FSRS-5 scheduling on both flashcards and concepts, persisting stability,
  difficulty and lapses per concept
- Reviews are Socratic dialogue, not Anki cards; the grade is inferred from
  how you answered
- The tutor reads that history: "you have forgotten this twice" is a claim
  about *you*, from your own record

### Degree planning

<!-- SCREENSHOT:degree -->

Stack courses into a credit-bearing programme on the Carnegie standard
(1 credit = 45 hours; 60 for an associate, 120 for a bachelor's). General
education is include, transfer-in, or skip — nobody should have to take
English to finish a D&D degree.

A plan has to survive two gates before it is saved, whether Helga planned it or
you handed one in: `validate` for what makes a programme *unteachable* — a
prerequisite cycle, a prerequisite that is not in the programme, one scheduled
no earlier than the course needing it — and a shape gate for whether it looks
like a degree at all: comparable terms, a capstone at the end, prerequisite
sets that distinguish siblings rather than "everything that came before".
Both refuse with reasons and save nothing.

### Bringing in a stronger model

Helga builds with the local model. When you want a larger one — Claude, or
anything that speaks HTTP — it can take over **any part** of the build and hand
the rest back:

- A **whole course in one request**, structure and every body, so a model that
  holds the curriculum in one context can order concepts and avoid repeating
  itself instead of being made to write ninety separate calls
- **Any part of it.** Write twenty concepts, leave seventy; the local model
  fills the rest without being told which. The hydrator already skips anything
  that has a body, so what is missing *is* the queue
- The **same bar either way.** The depth contract judges the content and the
  degree gates judge the plan; work that falls short is refused with its
  reasons rather than stored. A course that arrives this way has not faced the
  fact-check and grounding verdicts, and says so rather than inheriting a pass
- **Provenance per concept**, so a course written by both models is legible
  afterwards

```bash
curl localhost:5002/api/pipeline    # the surface describes itself
```

Full reference: **[docs/EXTERNAL_AUTHORING.md](docs/EXTERNAL_AUTHORING.md)**.
Note that this surface has **no authentication** — fine on a laptop, not fine
exposed to a network.

### Voice, in both directions

- **Speech in** — Nemotron-3.5-ASR on the Neural Engine
- **Speech out** — Kokoro TTS, 82M parameters, 14 voices, cached for replay
- Mathematical notation is converted to speakable English, so a voice learner
  hears "lambda" and "x hat" rather than raw LaTeX

### Optional gamification

XP and levels, daily streaks, 13 achievement badges, per-concept mastery
badges. All of it can be switched off in Settings.

---

## The tabs

| Tab | Purpose |
|-----|---------|
| Home | Dashboard, stats, resume where you stopped |
| Courses | Browse, create, delete; quick create and the custom wizard |
| Degree | Plan a credit-bearing programme from your courses |
| Library | Search archives and bring in your own books |
| Practice | Socratic sessions, quizzes and FSRS-scheduled review |
| Progress | Mastery, streaks, the review calendar |
| Test | Adaptive testing across courses |
| Settings | Profile, theme, voice, gamification, system health |

---

## Is it any good? — HelgaBench

Teaching quality is measured rather than asserted.
[HelgaBench](docs/HELGABENCH.md) scores tutoring across seven domains —
mathematics, science, computer science, medicine, law, history, language and
literature — on dimensions traced to the tutoring literature (Bloom, Chi &
Wylie's ICAP, the Koedinger–Aleven assistance dilemma, VanLehn).

Two things distinguish it from asking a model to rate a transcript:

- **Half the score is deterministic.** Whether a figure gave away the answer,
  whether notation is speakable, whether the tutor drew where nothing needed
  drawing — computed from the transcript, no judge involved
- **It publishes its own noise floor.** Identical runs disagree, so every
  comparison is made against a measured floor and deltas inside it are
  reported as *no change*. A two-run floor was found to understate the spread
  by up to 7×, so floors are derived from three or more runs

```bash
python3 tools/bench_domains.py --static-only          # deterministic half, no model
python3 tools/bench_domains.py --domain mathematics   # full run
```

---

## Configuration

Copy `.env.example` to `.env` and adjust:

```bash
OLLAMA_MODEL=nail-35b-a3b-ctx    # project model, 16k context (docs/MODEL.md)
                                 # NOT the bare nail-35b-a3b: its 4096-token
                                 # context silently truncates 5 of 6 modules.
OLLAMA_URL=http://host.docker.internal:11434
FLASK_ENV=production
DEFAULT_VOICE=af_heart            # Kokoro TTS voice

HELGA_RESEARCH_TIMEOUT=90         # seconds a concept's research may take.
                                  # Cold lookups measured 4-37s, ~85s for two
                                  # at once; below that the result is thrown
                                  # away and the concept is written llm-only.
LOG_LEVEL=INFO                    # rag-engine verbosity. At WARNING the whole
                                  # hydration path is silent, and a build that
                                  # wrote nothing looks like one that never ran.
```

---

## Model evaluation & swapping

Helga is model-agnostic: it talks to Ollama's OpenAI-compatible API, so swapping
the grading/tutoring model is a config change, not a code change.

### Swapping the model

Set `OLLAMA_MODEL` in your `.env` (or the environment). `docker-compose.yml`
already reads it with a default of `nail-35b-a3b-ctx` (see `docs/MODEL.md`):

```yaml
OLLAMA_MODEL: ${OLLAMA_MODEL:-nail-35b-a3b-ctx}
```

```bash
# .env — alternatives
OLLAMA_MODEL=qwen3.5:9b-mlx      # Apple-Silicon MLX build, faster decode
# OLLAMA_MODEL=qwen3:14b         # heavier, text-only (previous default)
# OLLAMA_MODEL=qwen3.5:35b-a3b   # MoE reach model (~20GB), stronger reasoning
```

Install the model first (`docs/MODEL.md`), then restart:
`docker compose up -d`.

### Faster decode on Apple Silicon (MLX)

On Mac (the Mac Mini M4 Pro deployment), update Ollama to **0.19+** to enable the
MLX backend, which roughly doubles decode throughput on Apple Silicon vs. the
default backend. After upgrading Ollama, re-run the benchmark below to confirm
the tok/s improvement on your hardware.

### Candidate models to benchmark

Before swapping, benchmark candidates against the current `qwen3:14b`:

- **Qwen3.5-9B** — smaller, multimodal; a possible lower-latency replacement.
- **Qwen3.5-35B-A3B** — MoE model (~3B active params, ~20GB on disk); higher
  ceiling on grading quality if it fits in RAM.

> Verify exact parameter counts, context length, modality, and disk/RAM
> footprint on the official **Qwen / Hugging Face model card** before adopting —
> the figures above are approximate and intended only to scope the benchmark.

### Running the comparison harness

`tools/grading_eval.py` is a standalone CLI that drives each model through
Helga's real Socratic grading prompt over a curated dataset
(`tools/grading_eval_cases.json`, ~20 cases spanning correct, partial, wrong,
"I don't know", and prompt-injection answers). It reports grade accuracy, JSON
reliability, and latency/throughput.

```bash
# Compare two models (comma-separated Ollama tags)
python3 tools/grading_eval.py --models qwen3:14b,qwen3.5:9b

# Repeat each case 3x for stable latency stats, custom Ollama URL, custom output
python3 tools/grading_eval.py \
  --models qwen3:14b,qwen3.5:35b-a3b \
  --runs 3 \
  --ollama-url http://localhost:11434 \
  --out results.json
```

Output is a comparison table plus a JSON results file:

```
model                   exact-acc   within1   json-rate   mean-lat    tok/s
-----------------------------------------------------------------------------
qwen3:14b                    75.0%     95.0%      100.0%      1.83s     62.4
qwen3.5:9b                   70.0%     90.0%       95.0%      1.12s     98.1
```

Columns: **exact-acc** = exact grade match, **within1** = within ±1 grade
(grades are ordinal 1-4), **json-rate** = share of calls returning parseable
JSON with a valid `grade`, **mean-lat** = mean per-call latency, **tok/s** =
approximate decode throughput. Run `--help` for all flags (works with no Ollama
running; the connection is lazy).

---

## Development

```bash
# Run tests
make test-unit
make test-integration

# View logs
make logs

# Rebuild after code changes
docker compose build && docker compose up -d

# Clean slate
make clean
```

### Key Files

| File | Purpose |
|------|---------|
| `services/core/fsm_logic.py` | FSM state machine, tutoring logic |
| `services/core/course_builder.py` | Course generation pipeline |
| `services/core/fsrs_engine.py` | FSRS v6 spaced repetition |
| `services/rag/librarian.py` | RAG service, course CRUD, search |
| `services/common/storage.py` | SQLite storage facade |
| `services/common/llm_utils.py` | LLM call wrappers with JSON repair |
| `services/common/prompts.py` | Centralized prompt templates |
| `services/web-ui/app.py` | Web UI Flask app |
| `services/research/research_server.py` | Web search augmentation |
| `services/tts/tts_server.py` | Kokoro TTS server |

---

## License

[Apache License 2.0](LICENSE) — © 2026 Brennan Kelley.

Permissive: use, modify and redistribute, including commercially. Adds an
explicit patent grant over MIT, and asks that you keep the notice and state
what you changed.

### Third-party components

Helga runs several models and services under their own terms, which this
licence does not cover. Check them before redistributing a bundle:

| Component | Role |
|---|---|
| Ollama + the configured model | inference |
| Kokoro-82M | text to speech |
| Nemotron-3.5-ASR | speech to text |
| SearXNG | self-hosted web search |
| sentence-transformers `all-MiniLM-L6-v2` | embeddings |
