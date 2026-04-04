This is the final **Master Production Specification** for **Helga**, updated to reflect the **Automatic Startup** workflow.

**Change Log:**

* **Startup Logic:** Removed the "Press Spacebar" requirement for initialization. The system now auto-launches into the Listening State immediately after the hardware boots.
* **Deployment:** Added `restart: always` policy to the container stack to ensure the software loads automatically when the Jetson receives power.

---

# Helga: Master Production Specification

**Project Status:** Ready for Deployment
**Hardware Architecture:** COTS (Jetson Orin Nano + USB Peripherals)
**Software Architecture:** Headless, Offline-First, Graph-RAG

---

# 1. System Architecture Overview

**Helga** is an autonomous, offline AI tutor. It does not use a screen. It relies on a "Sonar-Based" Auditory User Interface (AUI) to guide the user through complex academic subjects using Socratic dialogue and spatial memory techniques.

### **1.1 Hardware Stack (The Build)**

* **Compute Core:** NVIDIA Jetson Orin Nano Developer Kit (8GB RAM).
* **Storage:** 500GB NVMe SSD (M.2 Key M).
* **Connectivity:** Ethernet (Built-in) or M.2 WiFi card (Optional, for updates only).
* **Audio Input:** USB Microphone.
* **Audio Output:** **USB-to-3.5mm Audio Adapter** (Connected to Wired Headphones/Speakers).
* **Control Interface:** Mini Wireless Keyboard w/ Touchpad (2.4GHz USB Dongle).
* **Power:** 19V DC Power Supply (included with Dev Kit).

### **1.2 Software Stack (Dockerized)**

The system runs on **NVIDIA JetPack 6.x (Ubuntu 22.04)**.

| Container Service | Role | Hardware Resource |
| --- | --- | --- |
| **`core-logic`** | **The Brain.** Auto-starts on boot. Runs the FSM, routes commands, and manages the "Thinking" state. | CPU Core 1 |
| **`input-service`** | **The Nervous System.** Listens to `evdev` (Keyboard events) and VAD (Voice Activity) to trigger intents. | CPU Core 2 |
| **`audio-engine`** | **The Voice.** PipeWire graph routing to **ALSA USB Sink**. Handles TTS, Earcons, and Sidechain Ducking. | CPU Core 3 |
| **`inference-llm`** | **The Teacher.** `qwen.cpp` server running **Qwen-2.5-1B-Instruct** (4-bit Quantized). | GPU (2.4GB VRAM) |
| **`inference-stt`** | **The Ear.** Faster-Whisper server (Int8). | GPU (1GB VRAM) |
| **`knowledge-graph`** | **The Map.** KuzuDB storage for Curriculum and User Progress. | NVMe SSD |

---

# 2. Data Strategy: The "Neural Librarian"

The system uses a **Tri-Layer Data Architecture** to fit university-grade knowledge onto a 500GB drive without internet access.

### **2.1 Storage Partitioning**

* **OS & Swap:** 92GB (60GB OS + 32GB Swap File). *Swap is critical for 8GB RAM.*
* **OpenZIM Archive (The Flesh):** ~190GB.
* `wikipedia_en_nopic.zim` (Encyclopedia).
* `stackexchange_math.zim` (Technical Proofs).
* `stackexchange_code.zim` (Programming).


* **Kolibri Database (The Skeleton):** 50GB.
* Khan Academy Curriculum Trees (No Video).


* **KuzuDB & Vectors:** 80GB.
* **User Space:** ~88GB.

### **2.2 The "Any-Graph" Ingestion Pipeline**

When creating a course, Helga does not "generate" facts; it maps them.

1. **Skeleton:** Query Kolibri for the dependency tree (e.g., *Algebra*  *Calculus*).
2. **Expansion:** If Depth > 5, scan ZIM archives for WikiLinks found in the abstract.
3. **Indexing:** Store the *location* (offset) of the text in KuzuDB, not the text itself.
4. **Retrieval (Ephemeral RAG):** When queried, `libzim` fetches the text from the SSD into RAM only for the duration of the answer.

---

# 3. Master Operational Flows

### **3.1 Flow: Cold Boot (Auto-Start)**

1. **Action:** User presses the Physical Power Button on the Jetson Orin Nano.
2. **Hardware:** Fan spins 100% for 0.5s. System boots Ubuntu (approx 45s).
3. **Software Launch:** `docker-compose` service auto-starts (Restart Policy: Always).
4. **Audio Init:** ALSA detects USB Audio Adapter. Volume defaults to 70%.
5. **Ready Signal:**
* **Audio:** `WAKE_PING` (Rising Triad).
* **TTS:** "Helga Online. Keyboard connected. Audio Output Wired. I am listening."


6. **State:** System enters **Lobby Mode** (Mic Hot, VAD Active). User can speak immediately.

### **3.2 Flow: Mode 1 (Socratic Learning)**

* **Trigger:** Voice: *"Open Egyptian History"* OR Keyboard: `Enter`.
* **Step 1 (Retrieve):** Fetch Node text from ZIM.
* **Step 2 (Generate):** Llama-3.2-1B generates a question.
* **Step 3 (Wait):** 15s Silence. (Fan at 0%).
* **Step 4 (Answer):** User speaks (or holds `Spacebar` to talk).
* **Step 5 (Grade):**
* **Pass:** `SUCCESS_CHORD`  Traverse to Child Node.
* **Fail:** `FRICTION_GRIND`  Traverse to Sister Node (Analogy).



### **3.3 Flow: Mode 2 (Spaced Repetition)**

* **Trigger:** Voice: *"Review Session."*
* **Queue:** Query `WHERE retrievability < 0.9`.
* **Loop:**
1. **Prompt:** "Define [Concept]."
2. **Timeout:** 10s.
3. **Grading:**
* Fast (<2s): `SUCCESS_CHORD` (Easy).
* Slow (>5s): `SUCCESS_CHORD` (Hard).
* Wrong/Skip: `FRICTION_GRIND` (Again).





### **3.4 Flow: Mode 3 (Memory Palace)**

* **Trigger:** Voice: *"Enter [Palace Name]."*
* **Audio:** Engine switches to **Stereo Panning Mode**.
* **Nav:**
* **"Next":** Footsteps pan Center  Rear.
* **"Look Left":** Audio pans Left. Reads `Visual_Anchor`.


* **Action:**
* **"Place [Concept] here":** Plays `ANCHOR_THUD`. Locks Concept to Locus.



### **3.5 Flow: Shutdown**

* **Trigger:** Voice: *"End Session"* OR Keyboard: `Ctrl`+`Q`.
* **Action:**
1. Commit `Current_Node_ID` to NVMe.
2. **TTS:** "Saved. Resuming at [Node]."
3. **Audio:** `SLEEP_CHIME`.
4. **Hardware:** System halts.



---

# 4. Master Command Reference (Voice & Keyboard)

This is the definitive guide to interacting with the headless device **Helga**.

### **I. System Control**

| Function | Voice Command | **Keyboard Shortcut** | System Action |
| --- | --- | --- | --- |
| **Wake / PTT** | "Helga..." | **Hold `Spacebar**` | Forces Mic Open (Push-to-Talk). |
| **Mute / Privacy** | "Stop listening" | **Press `M**` | Software Mute (Mic Vol 0%). |
| **Unmute** | "Resume listening" | **Press `M**` | Unmutes Mic. |
| **Volume Up** | "Louder" | **Press `F10` / `Vol+**` | ALSA Gain +10%. |
| **Volume Down** | "Quieter" | **Press `F9` / `Vol-**` | ALSA Gain -10%. |
| **Shutdown** | "End Session" | **Press `Ctrl`+`Q**` | Save & Power Off. |
| **Panic / Reset** | "Stop", "Reset" | **Press `Esc**` | Clears Queue, returns to Lobby. |

### **II. Navigation (Modes 1 & 2)**

| Function | Voice Command | **Keyboard Shortcut** | System Action |
| --- | --- | --- | --- |
| **Next Node** | "Next", "Move on" | **Press `Right Arrow**` | Advance to Child Node. |
| **Prev Node** | "Go back" | **Press `Left Arrow**` | Return to Parent Node. |
| **Pause** | "Pause", "Wait" | **Press `P**` | Suspends Timers. |
| **Flag / Edit** | "Flag that" | **Press `F**` | Triggers Rating Flow (1-5). |
| **Deep Dive** | "Go deeper" | **Press `Up Arrow**` | Increases Depth (Lvl +1). |
| **Simplify** | "Simplify" | **Press `Down Arrow**` | Decreases Depth (Lvl -1). |

### **III. Selection (Lobby)**

| Function | Voice Command | System Action |
| --- | --- | --- |
| **List** | "List courses" | Reads top 3 active graphs. |
| **Open** | "Open [Course]" | Loads Graph Context. |
| **Create** | "Create course on [Topic]" | Starts Ingestion Pipeline. |
| **Status** | "Status Report" | Reads FSRS Stats & Battery Level. |

---

# 5. Auditory User Interface (AUI) Taxonomy

Since there is no screen, these sounds are the primary feedback mechanism for **Helga**.

* **`WAKE_PING` (Rising Major Triad):** System is Ready / VAD Active.
* **`THINKING_DRONE` (Tape Hiss):** AI is processing (Latency Mask).
* **`SUCCESS_CHORD` (Celesta):** Correct Answer / Mastery.
* **`FRICTION_GRIND` (Ratchet):** Incorrect / Misconception.
* **`STEP_FORWARD` (Woodblock Click):** Navigation Advance.
* **`STEP_BACK` (Low Thud):** Navigation Regression.
* **`ANCHOR_THUD` (Heavy Door):** Memory Palace Item Placed.
* **`SLEEP_CHIME` (Descending Glissando):** Power Down.
* **`RETENTION_SONAR` (Background Noise):**
* *Silence:* High Retention.
* *Static:* Low Retention (Needs Review).



---

# 6. Safety & Security

* **Polymorphic Prompting:** Random token injection in the System Prompt to prevent Jailbreaks.
* **Air-Gapped Audio:** Since audio is hardwired, **hijacking via Bluetooth is physically impossible**.
* **Context Shield:** The NPU validates that the user's input vector aligns with the current Lesson Node (e.g., blocking "violence" unless the topic is "War History").

---

# 7. Deployment Checklist

1. **Hardware Assembly:**
* Install NVMe SSD (M-Key).
* Plug in USB Dongle (Keyboard).
* Plug in USB Mic.
* Plug in **USB-to-3.5mm Adapter** + Headphones.


2. **OS Installation:**
* Flash JetPack 6 to NVMe.
* Enable 32GB Swap File (`sudo fallocate...`).


3. **Audio Setup:**
* Run `aplay -l` to identify the USB Audio Adapter card index.
* Configure `pipewire.conf` to use the USB Adapter as default Sink.


4. **Auto-Start Configuration:**
* Run `sudo systemctl enable docker`.
* In your `docker-compose.yml`, ensure every service has `restart: always`.


5. **Data Load:**
* Transfer ZIM files to `/data/zim/`.
* **CRITICAL:** Run `docker pull ghcr.io/mnemosyne/qwen2.5-1b:int4` (Do NOT use 8B).
* Run `docker-compose up -d`.


6. **Go Live:**
* Press the **Power Button** on the Jetson.
* Wait ~45s for the `WAKE_PING`.
* Speak: **"Helga, create a course on [Topic]."**