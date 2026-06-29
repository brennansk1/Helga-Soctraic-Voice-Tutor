# Helga: Socratic AI Tutor

**Helga** is a self-hosted AI tutor that uses Socratic dialogue to teach any subject. It generates courses from topics, asks probing questions, adapts to your understanding via Bloom's taxonomy tracking, and schedules reviews with FSRS spaced repetition.

Built for **Mac Mini M4 Pro 24GB** with Ollama + Qwen 3 14B. Runs entirely locally — no API keys, no cloud dependencies.

---

## Architecture

```
Mac Mini M4 Pro 24GB
  Ollama (native)         Qwen 3 14B Q4_K_M (~9.5GB)
  :11434                  ~20-25 tok/s on M4 Pro

  Docker Compose (6 services)
    web-ui    :5050   Flask + Socket.IO dashboard
    core-logic :5003  FSM, course creation, tutoring
    rag-engine :5002  SQLite, embeddings, search
    tts        :5005  Kokoro TTS (on-demand audio)
    searxng     :8080  Self-hosted web search
    research   :5006  Content augmentation service
```

**Stack:** Python 3.11, Flask, SQLite (WAL mode), sentence-transformers (all-MiniLM-L6-v2), Kokoro TTS, SearXNG, py-fsrs v6.

---

## Quick Start

### Prerequisites
- Docker Desktop
- [Ollama](https://ollama.com) installed natively
- ~14GB disk for model + ~2GB for Docker images

### Setup

```bash
# 1. Pull the LLM model (multimodal — text + vision)
ollama pull qwen3.5:9b      # or qwen3.5:9b-mlx on Apple Silicon for faster decode

# 2. Clone and start
git clone <repo-url> && cd helga
cp .env.example .env
docker compose build
docker compose up -d

# 3. Open browser
open http://localhost:5050
```

### Verify

```bash
make health    # Check all 6 services
make test      # Run test suite
make backup    # Backup SQLite database
```

---

## Features

### Socratic Tutoring
- Adaptive questioning with 6 question types (clarification, probing, evidence, viewpoints, implications, application)
- Bloom's taxonomy tracking (Remember through Create)
- Mastery requires multiple correct answers, not just one lucky guess
- Micro-lecture fallback after 3 consecutive failures
- Full conversation history in LLM prompts

### Course Creation
- **Quick Create**: Enter a topic + depth level, get a full course in minutes
- **Custom Wizard**: Build courses step-by-step with module/concept suggestions
- Web search augmentation via SearXNG for source-backed content
- Self-consistency verification (3-pass factual claim checking)
- Per-concept metadata: misconceptions, analogies, key terms, examples

### Spaced Repetition
- FSRS v6 scheduling (99.6% superiority over SM-2)
- Reviews use Socratic dialogue, not Anki-style flashcards
- Grades inferred from dialogue quality

### Gamification
- XP system with level progression
- Daily streaks and goals
- 13 achievement badges
- Mastery badges per concept (Seedling through Edelweiss)
- Optional — can be toggled off in Settings

### Text-to-Speech
- Kokoro TTS (82M params, 14 voices)
- On-demand play buttons on tutor messages
- Audio caching for instant replay

---

## Tabs

| Tab | Purpose |
|-----|---------|
| Home | Dashboard, stats, resume learning |
| Courses | Browse, create (Quick/Custom), delete |
| Learn | Socratic dialogue sessions |
| Quiz | Adaptive testing across courses |
| Review | FSRS-scheduled spaced repetition |
| Schedule | Review calendar view |
| Status | Service health monitoring |
| Settings | Profile, theme, voice, gamification |

---

## Configuration

Copy `.env.example` to `.env` and adjust:

```bash
OLLAMA_MODEL=qwen3.5:9b          # LLM model (multimodal: text + vision)
OLLAMA_URL=http://host.docker.internal:11434
FLASK_ENV=production
DEFAULT_VOICE=af_heart            # Kokoro TTS voice
```

---

## Model evaluation & swapping

Helga is model-agnostic: it talks to Ollama's OpenAI-compatible API, so swapping
the grading/tutoring model is a config change, not a code change.

### Swapping the model

Set `OLLAMA_MODEL` in your `.env` (or the environment). `docker-compose.yml`
already reads it with a default of `qwen3.5:9b` (multimodal — text + vision):

```yaml
OLLAMA_MODEL: ${OLLAMA_MODEL:-qwen3.5:9b}
```

```bash
# .env — alternatives
OLLAMA_MODEL=qwen3.5:9b-mlx      # Apple-Silicon MLX build, faster decode
# OLLAMA_MODEL=qwen3:14b         # heavier, text-only (previous default)
# OLLAMA_MODEL=qwen3.5:35b-a3b   # MoE reach model (~20GB), stronger reasoning
```

Pull the model first (`ollama pull qwen3.5:9b`), then restart:
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

MIT
