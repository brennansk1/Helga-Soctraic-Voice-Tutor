# Lessons Learned: Project Helga

## 🔒 Schema & System Stability
- **Cloning Hazard:** When using `shutil.copytree` to create a temp DB, Kuzu copies the *old* schema file. `init_schema` checks `if not exists` and thus skips updates. **Solution:** Run migrations on *every* connection using `ALTER TABLE` inside a try/catch block.
- **Docker Daemon Hangs:** On macOS, the Docker *Daemon* itself can hang (blocking `docker ps`, `df`, etc.). This requires a full restart of Docker Desktop.
- **Retry Logic:** File operations (like `shutil.rmtree`) on mapped volumes are prone to transient locking. Always wrap file removals in a retry loop with linear backoff.

## 🧠 Memory Management
- **The Jetson Freeze:** Spawning parallel threads (3x) for embedding generation/hydration during ingestion crashes the Jetson Orin Nano (8GB) due to OOM.
- **Solution:** Always use **Sequential Hydration** (one concept at a time) and trigger manual garbage collection (`gc.collect()`) after large DB writes.
- **Buffer Safety:** Native ZRAM is not enough for heavy LLM+Ingestion tasks. An **8GB NVMe swapfile** is required for system stability.

## 🤖 LLM Robustness
- **Parsing Hell:** Local small models (Qwen-0.5B/1.5B) frequently fail to produce 100% compliant JSON strings.
- **Grading Fix:** LLM (Qwen) often prefixes JSON with noise. Regex-based extraction (`re.search(r'\{.*?\}')`) is mandatory for robust grading.
- **Literals Alternative:** Prompting the model to return **Python List/Dictionary literals** and parse with `ast.literal_eval()` is a valid fallback.

## 🏗️ Architecture & Database
- **Lock Contention:** KuzuDB uses **file-level locking**. Services like `rag-engine` and `night_audit` MUST be stopped during ingestion to release write locks.
- **Atomic Swap:** Ingestion should happen in a temporary database followed by an atomic swap to minimize downtime and prevent corruption of the main DB.
- **Service Management:** A dedicated `ServiceManager` is required to orchestrate the 6-step course creation flow (Stop services -> Ingest -> Restart -> Health Check).

## 📦 Docker & Permissions
- **Root Bloat:** Files created inside a root-running container (like `core-logic`) will be unreadable by non-root services (like `rag-engine`).
- **Solution:** Always `chown -R 1000:1000 data/` (UID 1000 is used for non-root containers).
- **Sudo Cache:** In environments where docker requires sudo, caching the password to a secure file is necessary for headless background operations.

## 🎯 Development Strategy
- **Decoupling Resources:** When building feature-rich applications for edge hardware (Jetson), it is often more efficient to develop 100% of the features on a high-resource environment (like a Mac Mini with 24GB RAM) first. This allows for rapid iteration without fighting resource constraints.
- **Post-Dev Optimization:** Once the feature set is stable and complete, a dedicated optimization phase is used to port the application back to the target edge hardware and solve the resulting memory/latency problems.
- **Service Responsibility:** Database integrity checks should run in the service that *owns* the connection (e.g., `rag-engine`). Running them in a consumer service (like `core-logic`) leads to lock contention and startup race conditions.

## 🎨 Interactive Course Designer Patterns
- **FSM State Explosion:** Adding interactive drafting states (DRAFTING_COURSE, GAP_ANALYSIS, PRE_ASSESSMENT, TEACHING_STYLE_SELECT) requires careful state management. Each state needs its own handler method and transition logic in `transition()`.
- **Prompt Engineering for Personas:** Dynamic persona injection works best with explicit "Constraint:" lines in the system prompt. The LLM responds well to role-play instructions like "You are a FRIENDLY SOCRATIC TUTOR for young learners."
- **LocalFileProvider Caching:** When hydrating multiple concepts from the same uploaded document, cache the `LocalFileProvider` instance to avoid re-reading and re-vectorizing the file for each concept.
- **Pre-Assessment Grading:** Simple keyword matching is sufficient for diagnostic quizzes. Full LLM grading is overkill for pre-assessment where we just need correct/partial/unknown classification.
- **SortableJS Integration:** CDN-loaded SortableJS works well for drag-and-drop. The `ghostClass` and `chosenClass` options provide good visual feedback. Always update ordinal numbers in the DOM after reorder.
- **Modal UX:** Always add `onclick="if(event.target===this) closeModal()"` to modal backdrop divs for close-on-outside-click behavior.

## 🍏 Mac vs Linux Development
- **Docker Desktop:** Does not support `runtime: nvidia` or direct device mapping (e.g., `/dev/snd`) in the same way as Linux. Must use CPU-only configuration.
- **Port Conflicts:** macOS Control Center (AirPlay) listens on port 5000. Web UI must use an alternative port (e.g., 5006).
- **Indentation:** When editing Python files via agents, ensure indentation is preserved strictly to avoid `IndentationError` in critical services.
- **Field Consistency:** Inconsistency between producer (core-logic) and consumer (rag-engine/tests) field names (e.g., `resource_text` vs `text`) is a primary source of silent failures. Standardize on `resource_text` for raw content.
- **Chunk-Level RAG:** Relying solely on concept-level summaries is insufficient for rich educational tutoring. Chunking large ZIM articles at ingestion time is mandatory for high-quality Socratic hints later.

## 🔍 Codebase Audit Patterns
- **LLM-Generated Injection:** When an LLM returns node types (e.g., `type: "Module"`) that get interpolated into Cypher queries, always validate against a whitelist. LLM hallucinations can produce arbitrary strings that become f-string-injected into DB queries.
- **Markdown Structure Validation:** LLM-generated structured Markdown frequently omits required sections. Always validate output against expected headers and inject stubs for missing ones rather than failing silently.
- **Pedagogy JSON Retry:** Small LLMs fail to produce compliant JSON ~30% of the time. A retry with a more explicit schema-only prompt (no context) significantly improves parse success rate.
- **Microlecture-First Flow:** Always force `initial_mode="LECTURE"` when entering a new topic to ensure students get context before being questioned. Silent Socratic-first entry is confusing for learners.
- **Test Mock Isolation:** When multiple test classes share module-level mocks (e.g., `sys.modules['kuzu']`), the mock's call count accumulates across tests. Always assign a fresh mock to instance attributes in `setUp()` to isolate test assertions.
- **Flask Module-Level Decorators:** `fsm_logic.py` uses `@app.route()` at module level. Mocking Flask for tests requires a mock class with a `route()` method that returns a passthrough decorator, not just a `MagicMock()`.
