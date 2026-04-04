Here is the **Helga System Reference Architecture (SRA)**. This document contains the structural blueprints, schemas, interface definitions, and container specifications required for a coding agent to implement the software stack without ambiguity.

---

# Helga: System Reference Architecture (v1.0)

**Target System:** NVIDIA Jetson Orin Nano (8GB)
**OS:** Ubuntu 22.04 (JetPack 6)
**Architecture Pattern:** Containerized Microservices (Docker Compose)
**Communication:** HTTP REST (Internal) + Shared Volumes + Evdev (Input)

---

## 1. Directory Structure Definition

The coding agent must generate the following file tree. Root directory is `/opt/helga`.

```text
/opt/helga/
├── docker-compose.yml           # Orchestration manifest
├── .env                         # Environment variables (API keys, HW paths)
├── Makefile                     # Build/Deploy shortcuts
├── configs/
│   ├── helga_config.yaml        # Master logic configuration
│   ├── pipewire.conf            # Audio mixing graph definition
│   └── alsa_monitor.conf        # USB Audio hotplug rules
├── data/                        # Persistent Data (Mounted to NVMe)
│   ├── zim/                     # Read-Only: .zim archives (Wiki, StackExchange)
│   ├── kuzu_db/                 # Read-Write: Knowledge Graph
│   ├── models/                  # Read-Only: GGUF models (Llama-3.2-1B, Whisper)
│   └── logs/                    # Read-Write: User session logs
├── services/
│   ├── core/                    # [Service] State Machine & Logic
│   ├── input/                   # [Service] Keyboard & Mic VAD
│   ├── audio/                   # [Service] TTS & Mixer
│   └── rag/                     # [Service] Knowledge Retrieval
└── tools/                       # Utility scripts (Ingestion, Setup)

```

---

## 2. Container Orchestration Specification (`docker-compose.yml`)

The agent must define 6 services sharing a bridge network named `helga-net`.

### **Global Constraints**

* **Restart Policy:** `always` (Auto-start on boot).
* **Logging:** JSON-file driver, max-size 10m.
* **Runtime:** `nvidia` (for GPU-enabled containers).

### **Service 1: `inference-llm**`

* **Image:** `ghcr.io/ggerganov/qwen.cpp:server-cuda`
* **Role:** Socratic Text Generation.
* **Volumes:** `./data/models:/models:ro`
* **Command Arguments:**
* Model: `/models/qwen-2.5-1b-instruct-q4_k_m.gguf`
* Context: `4096`
* GPU Layers: `99` (Offload all)
* Host: `0.0.0.0`, Port: `8080`


* **Healthcheck:** `curl -f http://localhost:8080/health`

### **Service 2: `inference-stt**`

* **Context:** `./services/input` (Multi-stage build)
* **Role:** Speech-to-Text (STT).
* **Base Image:** `nvidia/cuda:12.2.0-runtime-ubuntu22.04`
* **Dependencies:** `faster-whisper`, `flask`, `cuda-toolkit`.
* **Volumes:** `./data/models:/models:ro`
* **Environment:** `MODEL=small.en`, `COMPUTE_TYPE=int8`.
* **Expose:** Port `5000`.

### **Service 3: `audio-engine**`

* **Context:** `./services/audio`
* **Role:** Text-to-Speech (TTS), Earcons, Mixing.
* **Base Image:** `python:3.10-slim` (with `pipewire`, `alsa-utils`).
* **Devices:**
* `/dev/snd:/dev/snd` (Direct ALSA access for USB Adapter).


* **Volumes:** `./configs/pipewire.conf:/etc/pipewire/pipewire.conf`
* **Privileged:** `true` (Required for realtime audio scheduling).
* **Expose:** Port `5001`.

### **Service 4: `rag-engine**`

* **Context:** `./services/rag`
* **Role:** Knowledge Graph & Archive Reader.
* **Dependencies:** `kuzu`, `libzim`, `flask`.
* **Volumes:**
* `./data/kuzu_db:/db`
* `./data/zim:/zim:ro`


* **Expose:** Port `5002`.

### **Service 5: `input-service**`

* **Context:** `./services/input`
* **Role:** Physical Hardware Listener (Keyboard/Mic).
* **Devices:**
* `/dev/input:/dev/input` (Keyboard access).
* `/dev/snd:/dev/snd` (Microphone access).


* **Volumes:** `./configs:/configs:ro`
* **Network:** `helga-net`.

### **Service 6: `core-logic**`

* **Context:** `./services/core`
* **Role:** Finite State Machine (The "Brain").
* **Dependencies:** `requests`, `pyyaml`.
* **Volumes:**
* `./configs:/configs:ro`
* `./data/logs:/logs`


* **Depends_on:** All other services.

---

## 3. Data Schema Specifications

The coding agent must implement these schemas for persistence.

### **3.1 Knowledge Graph (KuzuDB)**

* **Node: Concept**
* `uid` (STRING, Primary Key)
* `title` (STRING)
* `depth_level` (INT64)
* `zim_source` (STRING) - *Enum: 'wiki', 'math', 'code'*
* `zim_offset` (INT64) - *Byte location in ZIM file*
* `summary_vector` (FLOAT[384]) - *For semantic search*


* **Node: User**
* `uid` (STRING, Primary Key)
* `current_course` (STRING)
* `battery_pref` (STRING)


* **Edge: DEPENDS_ON**
* `type` (STRING) - *'Foundational', 'Functional'*


* **Edge: LEARNS** (User -> Concept)
* `retrievability` (FLOAT)
* `stability` (FLOAT)
* `last_review` (TIMESTAMP)



### **3.2 Configuration Contract (`helga_config.yaml`)**

```yaml
system:
  wake_word: "helga"
  mic_device_name: "USB PnP Audio Device" # Check via aplay -l
  keyboard_device_name: "Mini Keyboard"   # Check via evtest

pedagogy:
  socratic_depth: 3       # Rounds of questions before explaining
  fsrs_retention: 0.90    # Target memory retention rate

prompts:
  tutor_system: "You are Helga, a headless AI tutor. Use short sentences..."
  critic_system: "Evaluate the previous response for pedagogical errors..."

```

---

## 4. API Interface Definitions (Inter-Container)

Services communicate via internal HTTP REST.

### **A. Audio Engine API (Port 5001)**

* `POST /tts`
* **Payload:** `{"text": "Hello world", "priority": "high"}`
* **Action:** Interrupts current audio, generates speech via Piper, plays to ALSA sink.


* `POST /earcon`
* **Payload:** `{"name": "WAKE_PING"}`
* **Action:** Plays pre-loaded WAV file from `/soundbanks`.


* `POST /duck`
* **Payload:** `{"state": "active", "level": 0.3}`
* **Action:** Lowers background/TTS volume (used when user is speaking).



### **B. RAG Engine API (Port 5002)**

* `GET /search`
* **Query Param:** `q=python list comprehension`
* **Response:** JSON list of Concept nodes sorted by vector distance.


* `GET /node/{uid}`
* **Response:** Raw text content extracted from ZIM archive (ephemeral fetch).



### **C. Core Logic -> LLM Interface (Port 8080)**

* *Standard OpenAI-compatible API provided by qwen.cpp*
* `POST /completion`
* **Payload:** `{"prompt": "...", "n_predict": 128, "stop": ["User:"]}`



---

## 5. Logic Flow Specifications

The agent must implement these specific algorithms in the `core-logic` service.

### **5.1 The Finite State Machine (FSM)**

Must be implemented as a Class-based FSM pattern.

* **State: LOBBY**
* *Input:* Keyboard `Spacebar` OR VAD "Wake Word".
* *Transition:*  **LISTENING**.


* **State: LISTENING**
* *Action:* Stream Mic Audio  `inference-stt`.
* *Input:* Silence (1.5s) OR Keyboard `Release Spacebar`.
* *Transition:*  **THINKING**.


* **State: THINKING**
* *Action:* Trigger `audio-engine` (Drone Sound). Send Query to `rag-engine` + `inference-llm`.
* *Transition:*  **SPEAKING**.


* **State: SPEAKING**
* *Action:* Send tokens to `audio-engine` (TTS).
* *Input:* Keyboard `M` (Interrupt).
* *Transition:*  **LOBBY** (on complete) OR **LISTENING** (if question asked).



### **5.2 The "Any-Graph" Ingestion Logic**

Located in `tools/ingest.py`.

1. **Parse:** Read `kolibri_content.sqlite` (Khan Academy tree).
2. **Map:** Convert Topic Tree  KuzuDB Nodes/Edges.
3. **Enrich:** Iterate Kuzu Nodes. Use regex to find `[[WikiLinks]]` in descriptions.
4. **Verify:** Check if WikiLink exists in `wikipedia.zim` index.
5. **Link:** Create `RELATED_TO` edge if verified.

### **5.3 Input Handling (The "Nervous System")**

Located in `services/input/keyboard_listener.py`.

* Must use `python-evdev` library to grab the specific `/dev/input/eventX` device.
* **Key Mapping:**
* `KEY_SPACE` (Hold)  Emit `PTT_ACTIVE` / `PTT_RELEASED`.
* `KEY_M` (Press)  Emit `TOGGLE_PRIVACY`.
* `KEY_RIGHT`  Emit `NAV_NEXT`.
* `KEY_LEFT`  Emit `NAV_PREV`.
* `KEY_ESC`  Emit `RESET_STATE`.


* **Output:** Sends HTTP POST to `core-logic:5003/event` webhook.

---

## 6. Hardware Implementation Notes

### **Audio Routing (PipeWire Graph)**

The agent must generate a `pipewire.conf` that defines:

1. **Source:** USB Mic (Capture).
2. **Sink:** USB Audio Adapter (Playback).
3. **Filter Chain:**
* `TTS Stream` + `SFX Stream`  `Compressor`  `USB Sink`.
* *Why:* To prevent clipping when talking over sound effects.



### **System Startup (Systemd)**

The agent must provide a `helga.service` file for host-level startup.

```ini
[Unit]
Description=Helga AI Tutor Container Stack
After=docker.service sound.target

[Service]
Restart=always
WorkingDirectory=/opt/helga
ExecStart=/usr/bin/docker compose up
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target

```