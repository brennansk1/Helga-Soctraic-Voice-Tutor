Here is the third and final reference document: the **Helga Implementation Guide & Logic Atlas**.

While the **Design** tells the agent *what* to build, and the **Architecture** tells it *where* to build it, this document tells the agent exactly *how* the complex logic works inside the functions. It contains the prompt templates, math formulas, and event payloads.

---

# Helga: Implementation Guide & Logic Atlas (v1.0)

**Role:** Low-Level Logic & Algorithm Specification
**Target Audience:** Coding Agent / Developer
**Purpose:** Defines the internal logic, math, prompts, and data structures required to implement the services defined in the Architecture.

---

## 1. The Prompt Registry (Llama-3.2 System Prompts)

The coding agent must implement these exact prompt templates in `services/core/prompts.py`. The system uses **Polymorphic Prompting**, meaning the `{{security_token}}` is generated randomly at runtime (e.g., UUID4) and injected to prevent jailbreaks.

### **1.1. The Socratic Tutor (Mode 1)**

*Used for general instruction.*

```text
<|begin_of_text|><|start_header_id|>system<|end_header_id|>
[SECURITY_TOKEN: {{security_token}}]
You are Helga, an offline, headless AI tutor. Your goal is to guide the user to understanding via Socratic questioning.

RULES:
1. Do NOT lecture. Do NOT give long summaries.
2. Speak in short, conversational sentences (optimized for TTS).
3. If the user is wrong, gently correct them using an analogy.
4. If the user is right, confirm briefly and move to the next concept.
5. STRICTLY base your questions on the provided CONTEXT.

CONTEXT:
{{retrieved_zim_text}}
<|eot_id|><|start_header_id|>user<|end_header_id|>
{{user_input}}
<|eot_id|><|start_header_id|>assistant<|end_header_id|>

```

### **1.2. The Cartographer (Ingestion Logic)**

*Used when mapping unstructured text to KuzuDB nodes.*

```text
<|begin_of_text|><|start_header_id|>system<|end_header_id|>
You are a Knowledge Graph Architect. Extract concepts and relationships from the text.
Output JSON ONLY.

SCHEMA:
- Entities: Key concepts (Nouns).
- Relationships: "Foundational" (Prerequisite), "Functional" (How it works), "Historical" (Origin).

TEXT:
{{raw_text_chunk}}

JSON OUTPUT:
{
  "concepts": [{"name": "...", "type": "..."}],
  "edges": [{"source": "...", "target": "...", "relation": "..."}]
}
<|eot_id|><|start_header_id|>assistant<|end_header_id|>

```

---

## 2. The Pedagogy Engine (FSRS v5 Logic)

The agent must implement the **Free Spaced Repetition Scheduler (FSRS)** logic in `services/core/fsrs_engine.py`. Do not use a library; implement the simplified math for the Jetson.

### **2.1. The Math**

* **Variables:**
* : Retrievability (Probability of recall, 0.0-1.0).
* : Stability (Time in days for  to fall to 0.9).
* : Difficulty (1.0-10.0).
* : Grade (1=Fail, 3=Pass, 4=Easy).


* **Formulas:**
1. **New Stability ():**



*(Simplify the full FSRS formula for edge compute)*:
* If  (Fail):  days.
* If  (Pass): .
* If  (Easy): .


2. **Current Retrievability ():**



Where  is days since last review.



### **2.2. The Queue Logic**

* **Query:** When user says "Review Session":
```python
# Calculate R for all learned items
elapsed_days = (now - last_review).days
current_R = 0.9 ** (elapsed_days / stability)

# Filter
due_items = [item for item in all_items if current_R < 0.9]
sort(due_items, key=lambda x: x.current_R) # Ascending (Most urgent first)

```



---

## 3. The State Machine Atlas (FSM Transition Table)

The agent must implement `services/core/fsm_logic.py` using this exact logic table.

| Current State | Event (Trigger) | Condition | Action | Next State |
| --- | --- | --- | --- | --- |
| **LOBBY** | `EV_VAD_START` | - | Buffer Audio | **LISTENING** |
| **LOBBY** | `EV_KEY_SPACE_DOWN` | - | Open Mic Stream | **LISTENING** |
| **LISTENING** | `EV_VAD_STOP` | `duration > 1.0s` | Send to STT | **THINKING** |
| **LISTENING** | `EV_KEY_SPACE_UP` | - | Send to STT | **THINKING** |
| **THINKING** | `Internal` | `STT_Result != Null` | Play `THINKING_DRONE`, Query LLM | **SPEAKING** |
| **THINKING** | `Internal` | `Error` | Play `FRICTION_GRIND` | **LOBBY** |
| **SPEAKING** | `EV_TTS_FINISH` | - | - | **LOBBY** |
| **SPEAKING** | `EV_KEY_M` | - | Stop TTS, Play `POWER_DOWN` | **LOBBY** |
| **(ANY)** | `EV_KEY_ESC` | - | Reset Queues | **LOBBY** |

---

## 4. Event Payload Dictionary

The services communicate via HTTP POST. The agent must use these JSON schemas.

### **4.1. Input Event (`input-service` -> `core-logic`)**

```json
{
  "event_type": "USER_INTENT",
  "source": "KEYBOARD", // or "VOICE"
  "payload": {
    "intent": "NAV_NEXT",
    "raw_text": "" // Empty for keyboard events
  },
  "timestamp": 1715420000
}

```

### **4.2. Audio Command (`core-logic` -> `audio-engine`)**

```json
{
  "command": "PLAY_TTS",
  "data": {
    "text": "The Nile flooded annually.",
    "voice": "en_US-lessac-medium",
    "speed": 1.1,
    "ducking": true // Lowers background sounds
  }
}

```

### **4.3. RAG Query (`core-logic` -> `rag-engine`)**

```json
{
  "query": "vector_search",
  "embedding": [0.12, -0.45, ...], // 384-float vector
  "filter": {
    "min_depth": 3,
    "source": ["wiki", "stackexchange"]
  },
  "limit": 3
}

```

---

## 5. Audio & Hardware Interfaces

### **5.1. PipeWire Filter Chain (`pipewire.conf`)**

The agent must configure the audio graph to handle the "Sidechain Ducking" automatically.

```conf
# Pseudo-config for the agent to convert to PipeWire syntax
filter-chain = [
    {
        name = "helga-mixer"
        nodes = [
            { type = "compressor", name = "ducker", 
              controls = { "threshold": -20dB, "ratio": 4:1 } 
            }
        ]
        links = [
            # When TTS (Sidechain) plays, Duck the SFX/Drone (Main)
            { output = "tts_stream:monitor", input = "ducker:sidechain" }
            { output = "sfx_stream:out", input = "ducker:in" }
        ]
    }
]

```

### **5.2. EVDEV Key Mapping (`input_mapper.py`)**

Since the Mini Keyboard sends standard HID codes, map them strictly:

```python
KEY_MAP = {
    57: "PTT_SPACE",      # Spacebar
    105: "NAV_PREV",      # Left Arrow
    106: "NAV_NEXT",      # Right Arrow
    50: "TOGGLE_PRIVACY", # 'm'
    1: "RESET_STATE"      # Esc
}

```

---

## 6. Testing Strategy (Headless Verification)

Since there is no screen, the agent must generate a `tests/` directory with these scripts:

1. **`test_audio_sweep.py`**:
* Plays a sine wave from 200Hz to 2kHz.
* *Developer Check:* Plug in headphones. If you hear the sweep, ALSA/PipeWire is correct.


2. **`test_llm_speed.py`**:
* Sends "Count to 10" to the LLM.
* *Success Metric:* Returns full response in < 3.0 seconds (approx 15 tok/sec check).


3. **`test_key_press.py`**:
* Prints "KEY RECEIVED" to console when Spacebar is pressed.
* *Purpose:* Verify `evdev` permissions without running the full stack.



This document provides the atomic details required to write the code.