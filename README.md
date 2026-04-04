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
# 1. Pull the LLM model
ollama pull qwen3:14b

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
OLLAMA_MODEL=qwen3:14b           # LLM model
OLLAMA_URL=http://host.docker.internal:11434
FLASK_ENV=production
DEFAULT_VOICE=af_heart            # Kokoro TTS voice
```

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
