# Project Brief: Helga Socratic Tutor

## 🚀 Mission
Helga is an autonomous, offline AI tutor designed for headless operation on NVIDIA Jetson devices. It provides an auditory user interface (AUI) for Socratic learning, spaced repetition (FSRS), and memory palace techniques, leveraging graph-RAG for knowledge retrieval from offline archives (ZIM files).

## 🏗️ Architecture
- **Development Environment:** Mac Mini (24GB RAM) for rapid feature development.
- **Production Target:** NVIDIA Jetson Orin Nano (8GB RAM), 500GB NVMe SSD, USB-to-3.5mm Audio Adapter.
- **Strategy:** Develop 100% of features on higher-resource hardware (Mac Mini), then migrate back to Jetson for final optimization and memory troubleshooting.
- **Orchestration:** Docker Compose.
- **Core Engine:** Finite State Machine (FSM) for interaction management.
- **Data Layer (Tri-Layer Architecture):** 
  - **OpenZIM Archive (The Flesh):** ~190GB of raw text (Wikipedia, StackExchange).
  - **Kolibri Database (The Skeleton):** Curriculum trees (e.g., Khan Academy).
  - **KuzuDB (The Index):** Knowledge Graph mapping concepts to ZIM offsets.
- **Inference Suite:**
  - **LLM:** Qwen-2.5-1B-Instruct (local GGUF).
  - **STT:** Faster-Whisper (CUDA optimized).
  - **TTS:** Piper (Latency optimized).

## 🛠️ Tech Stack
- Python 3.10
- Flask & Flask-SocketIO (API & Web UI)
- KuzuDB (Graph RAG)
- Docker & Docker Compose
- PipeWire (Audio Routing)

## 🎯 Primary Goals & Roadmap
1. 24/7 Autonomous Development and Operation.
2. 100% Offline functionality.
3. Pedagogy-first Socratic questioning loops.
4. **Interactive Course Designer:** (In-Progress Roadmap)
    - Source Material Injection (RAG for local docs).
    - AI Structural Audit (Gap Analysis for syllabi).
    - Dynamic Persona Configuration (Teaching style customization).
    - Interactive "Draft Board" UI (Drag-and-drop reordering).
    - Smart Pre-Assessment (Quiz-based depth setting).

