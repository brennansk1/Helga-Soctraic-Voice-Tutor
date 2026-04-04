# AI Usage Guide: Development with Antigravity

This folder serves as the **External Brain** for Antigravity. By maintaining these files, you ensure that any AI agent joining the project can immediately understand the architecture, current status, and future roadmap without extensive re-analysis.

## 🛠️ The Context Stack

| File | Role | When to Update |
| :--- | :--- | :--- |
| `PROJECT_BRIEF.md` | **The North Star.** Defines mission, tech stack, and core architecture. | When fundamental technologies or goals change. |
| `ACTIVE_CONTEXT.md` | **The Short-Term Memory.** Current task, immediate status, and environment details. | At the beginning and end of every development session. |
| `TASK_QUEUE.md` | **The Roadmap.** Categorized backlog of P0-P3 tasks. | When new features are planned or bugs are discovered. |
| `DEV_LOG.md` | **The Audit Trail.** Chronological log of major fixes and changes. | After any significant fix or implementation is verified. |
| `LESSONS_LEARNED.md` | **The Wisdom Base.** Critical gotchas, optimizations, and technical insights. | When a non-obvious bug is solved or a performance pattern is discovered. |

## 🚀 Best Practices for Antigravity

### 1. The "Hand-off" Pattern
Before starting a new feature, ask Antigravity to:
> "Analyze the current context and update `ACTIVE_CONTEXT.md` for the next task."

### 2. Guarding Against Redundancy
Context files prevent the AI from repeating research. If you find yourself explaining the same architecture twice, **add it to the Project Brief or Lessons Learned.**

### 3. State Management
Antigravity is optimized to read these files FIRST. If the files are out of sync with the code, the AI may make incorrect assumptions. Use the `task.md` artifact during long sessions to track atomic progress.

### 4. Sequential Hydration (Project Specific)
In this project, always remind the AI of the **Sequential Hydration** lesson from `LESSONS_LEARNED.md` when working on ingestion, as it prevents OOM crashes on the Jetson hardware.

## 🤖 Rules for AI Agents
1. **Always Read First:** Read all files in `.ai_context` before suggesting major changes.
2. **Update on Completion:** Synchronize `ACTIVE_CONTEXT` and `DEV_LOG` before ending a task.
3. **Respect the Queue:** Pull next steps from `TASK_QUEUE.md` unless the user provides a direct override.
4. **Learn from Failure:** Every major bug fix MUST result in an entry in `LESSONS_LEARNED.md`.
