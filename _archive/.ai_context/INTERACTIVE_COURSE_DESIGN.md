# Interactive Course Designer: Future Feature Roadmap

## 1. Source Material Injection ("Bring Your Own Data")
Allows users to upload specific documents to serve as the primary truth source for a module.

### User Experience
- **User:** "Create a module called 'Company Safety Protocols'."
- **Helga:** "Context?"
- **User:** "Use the 'safety_manual.pdf' I uploaded as the source."

### Implementation Strategy
- **Ingestion:** Add a `LocalFileProvider` to `services/core/content_provider.py`.
- **Preprocessing:** When a file is uploaded via Web UI, chunk it and store it in a vector store (e.g., ChromaDB or simple cosine similarity in memory) separate from the main Knowledge Graph.
- **Hydration Logic:**
    - The `ContentHydrator` checks if a module has a `source_file` attribute.
    - If yes, it bypasses ZIM search and instead performs RAG (Retrieval Augmented Generation) against the uploaded document to fill `Concept.resource_text`.

---

## 2. AI Structural Audit ("Gap Analysis")
An intermediate step where the AI acts as a consultant to verify the user's manual structure before committing it to the database.

### User Experience
- **User:** "I have modules for Propulsion and Orbitals. Am I missing anything?"
- **Helga:** "Analyzing... For a course on Rocket Science, you are missing 'Guidance Systems' and 'Re-entry Dynamics'. Shall I add them?"

### Implementation Strategy
- **Trigger:** Explicit user question ("Check my work") or auto-trigger before "Finish".
- **Prompt:** Send the current `draft_course_structure` JSON to the LLM with the prompt: "Identify critical missing topics for a comprehensive course on {Title} based on this syllabus."
- **Action:** The LLM returns a list of `suggested_modules`. Helga offers them to the user, who can say "Add Guidance" to insert it into the draft.

---

## 3. Dynamic Persona Configuration
Allows the user to define how the course is taught, not just what is taught. This persists a style modifier into the course metadata.

### User Experience
- **Helga:** "Course structure confirmed. What teaching style should I use?"
- **User:** "Explain it like I'm five," or "Strict academic drill," or "Use lots of analogies."

### Implementation Strategy
- **Schema Change:** Add `teaching_style` property to the Course node in `schema.cypher`.
- **Runtime Logic:** In `fsm_logic.py`, when entering `SOCRATIC_LEARNING` mode, read this property.
- **Prompt Injection:** Modify `get_socratic_tutor_prompt` to accept a `style_modifier`.
- **Default:** "You are a Socratic Tutor."
- **Modified:** "You are a Socratic Tutor. Constraint: Use simple language and metaphors suitable for a child."

---

## 4. Interactive "Draft Board" UI
Enhance the Web UI from a passive visualization to an active editor, allowing complex reordering that is tedious via voice.

### User Experience
- User sees the list of modules on the screen.
- User drags "Module 3" to position 1 via mouse/touch.
- Helga acknowledges: "Okay, moved {Module} to the beginning."

### Implementation Strategy
- **API Endpoint:** Add `POST /api/draft/reorder` in `fsm_logic.py`.
- **Frontend:** Use a drag-and-drop library (e.g., SortableJS) in `session.js`.
- **Synchronization:** When the UI triggers a reorder, it sends the new index list to the backend. The backend updates `draft_course_structure` and emits a confirmation event.

---

## 5. Smart Pre-Assessment
Instead of a static Depth setting, Helga quizzes the user to determine which modules need depth and which can be skimmed.

### User Experience
- **User:** "Create a course on Biology."
- **Helga:** "Let's see where to start. What is the powerhouse of the cell?"
- **User:** "Mitochondria."
- **Helga:** "Correct. I'll set the 'Cell Structure' module to Depth 1 (Review) and focus deeper on 'Genetics'."

### Implementation Strategy
- **Pre-Flight Quiz:** Before the "Drafting Phase", Helga generates 3-5 broad questions about the Course Title.
- **Scoring:** Based on answers, the Depth parameter becomes a dictionary `{ "Module A": 1, "Module B": 4 }` instead of a global integer.
- **Builder Logic:** The `SkeletonBuilder` uses these specific depths when generating the sub-graph for each module.
