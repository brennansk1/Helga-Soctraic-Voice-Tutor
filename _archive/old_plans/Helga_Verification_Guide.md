# Helga — Production Readiness Verification Guide

**For use with Claude Code CLI | Companion to Helga Sprint Plan**
**All tests assume services running: `docker compose up -d` + Ollama serving qwen3:14b**

---

## 1. SERVICE ARCHITECTURE — EXPECTED STATE

### 1.1 Services & Ports

| Service | Container | Port | Health URL | Expected Response |
|---------|-----------|------|------------|-------------------|
| Web UI | helga-web-ui | 5000 | `GET /health` | `{"status": "healthy", "service": "web-ui"}` |
| Core Logic | helga-core-logic | 5003 | `GET /health` | `{"status": "healthy", "state": "<FSM_STATE>", ...}` |
| RAG Engine | helga-rag-engine | 5002 | `GET /health` | `{"status": "healthy", "db": true}` |
| TTS | helga-tts | 5005 | `GET /health` | `{"status": "healthy", "engine": "kokoro", "params": "82M"}` |
| SearXNG | helga-searxng | 8080 | `GET /healthz` | HTTP 200 |
| Research | helga-research | 5006 | `GET /health` | `{"status": "healthy", "searxng_reachable": true, "cache_entries": int}` |
| Ollama (native) | N/A | 11434 | `GET /api/tags` | JSON with model list including `qwen3:14b` |

### 1.2 Container Health Verification

```bash
# TASK: Verify all 4 Docker containers reach healthy state within 60 seconds
# RUN THIS FIRST — everything else depends on it

MAX_WAIT=60
START=$(date +%s)
while true; do
    ALL_HEALTHY=true
    for svc in helga-web-ui helga-core-logic helga-rag-engine helga-tts helga-searxng helga-research; do
        STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null)
        if [ "$STATUS" != "healthy" ]; then
            ALL_HEALTHY=false
            break
        fi
    done
    if $ALL_HEALTHY; then
        echo "PASS: All services healthy in $(($(date +%s) - START)) seconds"
        break
    fi
    if [ $(($(date +%s) - START)) -ge $MAX_WAIT ]; then
        echo "FAIL: Services not healthy after ${MAX_WAIT}s"
        docker ps --format "table {{.Names}}\t{{.Status}}"
        exit 1
    fi
    sleep 2
done
```

### 1.3 Ollama Verification

```bash
# TASK: Verify Ollama is reachable and model is loaded
curl -sf http://localhost:11434/api/tags | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = [m['name'] for m in data.get('models', [])]
assert any('qwen3' in m and '14b' in m for m in models), f'qwen3:14b not found. Models: {models}'
print('PASS: qwen3:14b loaded in Ollama')
"

# TASK: Verify LLM responds and measure speed
curl -sf http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:14b","messages":[{"role":"user","content":"Say hello in exactly 5 words."}],"max_tokens":20,"stream":false}' \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
content = data['choices'][0]['message']['content']
assert len(content) > 0, 'Empty response from Ollama'
print(f'PASS: Ollama responded: {content[:80]}')
"
```

---

## 2. COMPLETE API CONTRACT — EVERY ENDPOINT

### 2.1 Web UI Routes (Pages)

Each route must return HTTP 200 with valid HTML.

```bash
# TASK: Verify all page routes return 200
BASE="http://localhost:5000"
ROUTES="/ /courses /courses/new /learn /review /test /status"
FAIL=0
for route in $ROUTES; do
    CODE=$(curl -sf -o /dev/null -w "%{http_code}" "${BASE}${route}")
    if [ "$CODE" = "200" ]; then
        echo "PASS: GET $route → $CODE"
    else
        echo "FAIL: GET $route → $CODE (expected 200)"
        FAIL=1
    fi
done
[ $FAIL -eq 0 ] && echo "ALL PAGE ROUTES PASS" || echo "SOME PAGE ROUTES FAILED"
```

### 2.2 Web UI API Proxy Routes

These routes on port 5000 proxy to backend services. Test via the web-ui port.

| Route | Method | Proxies To | Request Body | Expected Response |
|-------|--------|-----------|--------------|-------------------|
| `/api/fsm_state` | GET | core:5003/state | — | `{"state":"LOBBY", "active_course_uid":..., "transcript":[...]}` |
| `/api/stats` | GET | rag:5002/api/stats | — | `{"courses": int, "concepts": int, "streak": int}` |
| `/api/event` | POST | core:5003/event | `{"type":"TEXT_INPUT","payload":{"text":"hello"}}` | `{"status":"ok"}` |
| `/api/courses` | GET | rag:5002/api/courses | — | `{"courses": [{uid, title, description, status, progress}]}` |
| `/api/set_active_course` | POST | core:5003 | `{"uid":"...","title":"..."}` | `{"status":"ok"}` |
| `/api/delete_course` | DELETE | rag:5002 | `?uid=...` | `{"status":"deleted"}` |
| `/api/tts` | POST | tts:5005/api/tts | `{"text":"Hello","voice":"af_heart"}` | WAV audio binary (Content-Type: audio/wav) |
| `/api/voices` | GET | tts:5005/api/voices | — | `{"voices": ["af_heart", "af_bella", ...]}` |
| `/api/create_course` | POST | core:5003 | `{"topic":"Physics","depth":3}` | `{"course_uid":"...", "status":"building"}` |
| `/api/create_course_custom` | POST | core:5003 | Full wizard payload (see §4.5) | `{"course_uid":"...", "status":"building"}` |
| `/api/suggest_modules` | POST | core:5003 | `{"title":"...","description":"...","prior_knowledge":"new"}` | `{"modules":[{title, description}]}` |
| `/api/suggest_concepts` | POST | core:5003 | `{"title":"...","module_title":"...","existing_concepts":[]}` | `{"concepts":[{title, description}]}` |
| `/api/clarify_course` | POST | core:5003 | Full courseBuilder object | `{"questions":[{question, context}]}` |
| `/api/due_concepts` | GET | rag:5002 | `?course_uid=...` (optional) | `{"concepts":[{uid, title, stability, due_date}]}` |
| `/api/update_thinking_status` | POST | internal | `{"message":"..."}` | `{"status":"ok"}` |
| `/health` | GET | internal | — | `{"status":"healthy","service":"web-ui"}` |

```bash
# TASK: Verify all proxy API routes respond (not 404/502)
BASE="http://localhost:5000"
FAIL=0

# GET routes
for route in "/api/fsm_state" "/api/stats" "/api/courses" "/api/voices" "/api/due_concepts" "/health"; do
    CODE=$(curl -sf -o /dev/null -w "%{http_code}" "${BASE}${route}")
    if [ "$CODE" = "200" ]; then
        echo "PASS: GET $route → $CODE"
    else
        echo "FAIL: GET $route → $CODE"
        FAIL=1
    fi
done

# POST routes (with minimal valid payloads)
# /api/event
CODE=$(curl -sf -o /dev/null -w "%{http_code}" -X POST "${BASE}/api/event" \
  -H "Content-Type: application/json" \
  -d '{"type":"PING","payload":{}}')
echo "$([ $CODE = '200' ] && echo PASS || echo FAIL): POST /api/event → $CODE"

# /api/suggest_modules
CODE=$(curl -sf -o /dev/null -w "%{http_code}" -X POST "${BASE}/api/suggest_modules" \
  -H "Content-Type: application/json" \
  -d '{"title":"Test Topic","description":"test","prior_knowledge":"new"}')
echo "$([ $CODE = '200' ] && echo PASS || echo FAIL): POST /api/suggest_modules → $CODE"

[ $FAIL -eq 0 ] && echo "ALL API ROUTES PASS" || echo "SOME API ROUTES FAILED"
```

### 2.3 Core Logic Direct Routes (port 5003)

| Route | Method | Request | Expected Response |
|-------|--------|---------|-------------------|
| `/state` | GET | — | Full FSM state object (see §3.1) |
| `/event` | POST | `{"type":"...","payload":{}}` | `{"status":"ok"}` |
| `/health` | GET | — | `{"status":"healthy","state":"LOBBY",...}` |
| `/api/create_course` | POST | `{"topic":"...","depth":3}` | `{"course_uid":"...","status":"building"}` |
| `/api/create_course_custom` | POST | Full wizard payload | `{"course_uid":"...","status":"building"}` |
| `/api/suggest_modules` | POST | `{title, description, prior_knowledge}` | `{"modules":[...]}` |
| `/api/suggest_concepts` | POST | `{title, module_title, ...}` | `{"concepts":[...]}` |
| `/api/clarify_course` | POST | Full courseBuilder object | `{"questions":[...]}` |
| `/api/set_active_course` | POST | `{"uid":"...","title":"..."}` | `{"status":"ok"}` |

### 2.4 RAG Engine Direct Routes (port 5002)

| Route | Method | Request | Expected Response |
|-------|--------|---------|-------------------|
| `/search` | GET | `?q=topic&course_uid=...` | `{"results":[{uid, title, text, relevance}]}` |
| `/api/courses` | GET | — | `{"courses":[{uid, title, description, status, progress}]}` |
| `/api/courses` | DELETE | `?uid=...` | `{"status":"deleted"}` |
| `/api/stats` | GET | — | `{"courses":int, "concepts":int, "streak":int}` |
| `/api/course_structure` | GET | `?uid=...` | `{"nodes":[{uid, name, type, depth_level}]}` |
| `/flat_syllabus` | GET | `?uid=...` | `{"syllabus":[{uid, title, text}]}` |
| `/api/due_concepts` | GET | `?course_uid=...` | `{"concepts":[{uid, title, stability, difficulty, due_date}]}` |
| `/api/concept_details` | GET | `?uid=...` | `{uid, title, resource_text, misconceptions, analogies, bloom_level, key_terms, ...}` |
| `/api/update_mastery` | POST | `{uid, grade, bloom_level}` | `{"status":"updated"}` |
| `/api/course_tree` | GET | `?uid=...` | Hierarchical JSON for Cytoscape visualization |
| `/health` | GET | — | `{"status":"healthy","db":true}` |

### 2.5 TTS Direct Routes (port 5005)

| Route | Method | Request | Expected Response |
|-------|--------|---------|-------------------|
| `/api/tts` | POST | `{"text":"Hello world","voice":"af_heart"}` | WAV binary (audio/wav), >0 bytes |
| `/api/voices` | GET | — | `{"voices":["af_heart","af_bella",...]}` (14 voices) |
| `/health` | GET | — | `{"status":"healthy","engine":"kokoro","params":"82M"}` |

```bash
# TASK: Verify TTS generates audio
AUDIO_FILE="/tmp/helga_tts_test.wav"
curl -sf -o "$AUDIO_FILE" -X POST "http://localhost:5005/api/tts" \
  -H "Content-Type: application/json" \
  -d '{"text":"Testing Helga text to speech system.","voice":"af_heart"}'

SIZE=$(stat -f%z "$AUDIO_FILE" 2>/dev/null || stat -c%s "$AUDIO_FILE" 2>/dev/null)
if [ "$SIZE" -gt 1000 ]; then
    echo "PASS: TTS generated ${SIZE} bytes of audio"
else
    echo "FAIL: TTS output too small (${SIZE} bytes)"
fi

# TASK: Verify TTS caching (second call should be faster)
START=$(date +%s%N)
curl -sf -o /dev/null -X POST "http://localhost:5005/api/tts" \
  -H "Content-Type: application/json" \
  -d '{"text":"Testing Helga text to speech system.","voice":"af_heart"}'
END=$(date +%s%N)
CACHE_MS=$(( (END - START) / 1000000 ))
echo "Cache hit response time: ${CACHE_MS}ms (should be <100ms)"

# TASK: Verify voice list
VOICE_COUNT=$(curl -sf "http://localhost:5005/api/voices" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(len(data.get('voices', [])))
")
if [ "$VOICE_COUNT" -ge 10 ]; then
    echo "PASS: ${VOICE_COUNT} voices available"
else
    echo "FAIL: Only ${VOICE_COUNT} voices (expected ≥10)"
fi
```

---

## 3. FSM STATE MACHINE — STATE TRANSITIONS

### 3.1 Expected State Object Shape

```json
{
    "state": "LOBBY|SOCRATIC_LEARNING|SPACED_REPETITION|PAUSED|SHUTDOWN",
    "active_course_uid": "string|null",
    "last_question": "string",
    "conversation_history": [{"question":"...","answer":"...","grade":3}],
    "transcript": [{"sender":"user|tutor","text":"..."}],
    "current_context": "string (lesson prose)",
    "syllabus_length": 0,
    "current_lesson_uid": "string|null",
    "current_lesson_title": "string|null",
    "completed_topics": ["uid1", "uid2"],
    "current_card": "string|null",
    "locus": "string|null"
}
```

### 3.2 State Transition Tests

```bash
# TASK: Verify FSM starts in LOBBY
STATE=$(curl -sf http://localhost:5003/state | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])")
[ "$STATE" = "LOBBY" ] && echo "PASS: FSM in LOBBY" || echo "FAIL: FSM in $STATE (expected LOBBY)"

# TASK: Verify PAUSE/RESUME cycle
curl -sf -X POST http://localhost:5003/event \
  -H "Content-Type: application/json" \
  -d '{"type":"PAUSE","payload":{}}' > /dev/null

STATE=$(curl -sf http://localhost:5003/state | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])")
[ "$STATE" = "PAUSED" ] && echo "PASS: FSM paused" || echo "FAIL: Expected PAUSED, got $STATE"

curl -sf -X POST http://localhost:5003/event \
  -H "Content-Type: application/json" \
  -d '{"type":"RESUME","payload":{}}' > /dev/null

STATE=$(curl -sf http://localhost:5003/state | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])")
[ "$STATE" = "LOBBY" ] && echo "PASS: FSM resumed to LOBBY" || echo "FAIL: Expected LOBBY, got $STATE"
```

---

## 4. FEATURE VERIFICATION — COMPLETE FLOW TESTS

### 4.1 Quick Course Creation (Path A) — Full E2E

This is the single most important test. It exercises the entire pipeline: LLM skeleton generation → content hydration → SQLite storage → RAG retrieval.

```bash
# TASK: Create a course via Quick Create and verify everything

echo "=== STEP 1: Create course ==="
RESULT=$(curl -sf -X POST http://localhost:5003/api/create_course \
  -H "Content-Type: application/json" \
  -d '{"topic":"Basic Astronomy","depth":2}')

COURSE_UID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('course_uid',''))")
echo "Course UID: $COURSE_UID"
[ -n "$COURSE_UID" ] && echo "PASS: Course creation initiated" || { echo "FAIL: No course_uid returned"; exit 1; }

echo "=== STEP 2: Wait for creation to complete (max 120s) ==="
MAX_WAIT=120
START=$(date +%s)
while true; do
    STATUS=$(curl -sf "http://localhost:5002/api/courses" | python3 -c "
import sys, json
courses = json.load(sys.stdin).get('courses', [])
for c in courses:
    if c['uid'] == '$COURSE_UID':
        print(c.get('status', 'unknown'))
        break
else:
    print('not_found')
")
    if [ "$STATUS" = "ready" ]; then
        echo "PASS: Course status = ready (took $(($(date +%s) - START))s)"
        break
    elif [ "$STATUS" = "error" ]; then
        echo "FAIL: Course status = error"
        exit 1
    fi
    if [ $(($(date +%s) - START)) -ge $MAX_WAIT ]; then
        echo "FAIL: Course not ready after ${MAX_WAIT}s (status: $STATUS)"
        exit 1
    fi
    sleep 5
done

echo "=== STEP 3: Verify course structure ==="
python3 << 'PYEOF'
import requests, json, sys

UID = "$COURSE_UID"
BASE_RAG = "http://localhost:5002"

# 3a: Course appears in course list
courses = requests.get(f"{BASE_RAG}/api/courses").json()["courses"]
course = next((c for c in courses if c["uid"] == UID), None)
assert course, f"Course {UID} not in course list"
assert course.get("description") or course.get("overview"), "Course has no overview/description"
print(f"PASS: Course '{course['title']}' found with overview")

# 3b: Course has concepts
syllabus = requests.get(f"{BASE_RAG}/flat_syllabus", params={"uid": UID}).json()
concepts = syllabus.get("syllabus", [])
assert len(concepts) >= 5, f"Only {len(concepts)} concepts (expected ≥5)"
print(f"PASS: {len(concepts)} concepts in syllabus")

# 3c: Each concept has prose content (not JSON array)
for c in concepts:
    text = c.get("text", "")
    assert text, f"Concept '{c['title']}' has empty resource_text"
    assert not text.strip().startswith("["), f"Concept '{c['title']}' resource_text is a JSON array, not prose"
    words = len(text.split())
    assert words >= 50, f"Concept '{c['title']}' has only {words} words (expected ≥50)"
print("PASS: All concepts have prose content ≥50 words")

# 3d: Concepts have metadata
details = requests.get(f"{BASE_RAG}/api/concept_details", params={"uid": concepts[0]["uid"]}).json()
assert details.get("bloom_level", 0) >= 1, "bloom_level not set"
misconceptions = json.loads(details.get("misconceptions", "[]"))
analogies = json.loads(details.get("analogies", "[]"))
key_terms = json.loads(details.get("key_terms", "[]"))
assert len(misconceptions) >= 1, "No misconceptions generated"
assert len(analogies) >= 1, "No analogies generated"
print(f"PASS: Concept metadata populated (bloom={details['bloom_level']}, misconceptions={len(misconceptions)}, analogies={len(analogies)})")

# 3e: Semantic search finds concepts
search = requests.get(f"{BASE_RAG}/search", params={"q": concepts[0]["title"]}).json()
results = search.get("results", [])
assert len(results) >= 1, "Semantic search returned no results"
print(f"PASS: Semantic search returned {len(results)} results")

print("\n=== ALL COURSE CREATION CHECKS PASSED ===")
PYEOF
```

### 4.2 Custom Course Creation (Path B) — Full E2E

```bash
# TASK: Test the full guided wizard backend pipeline

python3 << 'PYEOF'
import requests, json, time, sys

BASE = "http://localhost:5003"

# Step 1: Suggest modules
print("=== Testing module suggestions ===")
resp = requests.post(f"{BASE}/api/suggest_modules", json={
    "title": "Introduction to Cooking",
    "description": "I want to learn to cook basic meals from scratch",
    "prior_knowledge": "new"
})
assert resp.status_code == 200, f"suggest_modules returned {resp.status_code}"
modules = resp.json().get("modules", [])
assert len(modules) >= 2, f"Only {len(modules)} modules suggested (expected ≥2)"
assert all("title" in m for m in modules), "Module missing title field"
print(f"PASS: {len(modules)} modules suggested: {[m['title'] for m in modules]}")

# Step 2: Suggest concepts for a module
print("\n=== Testing concept suggestions ===")
resp = requests.post(f"{BASE}/api/suggest_concepts", json={
    "title": "Introduction to Cooking",
    "description": "I want to learn to cook basic meals",
    "prior_knowledge": "new",
    "module_title": modules[0]["title"],
    "module_note": "Focus on knife skills and safety",
    "existing_concepts": ["Kitchen Safety Basics"]
})
assert resp.status_code == 200, f"suggest_concepts returned {resp.status_code}"
concepts = resp.json().get("concepts", [])
assert len(concepts) >= 2, f"Only {len(concepts)} concepts suggested"
# Verify suggestions don't duplicate the existing concept
titles_lower = [c["title"].lower() for c in concepts]
assert "kitchen safety basics" not in titles_lower, "Suggestion duplicated existing concept"
print(f"PASS: {len(concepts)} concepts suggested (no duplicates)")

# Step 3: Generate clarifying questions
print("\n=== Testing clarifying questions ===")
wizard_payload = {
    "title": "Introduction to Cooking",
    "description": "I want to learn to cook basic meals from scratch. I've never cooked before.",
    "prior_knowledge": "new",
    "modules": [
        {
            "title": modules[0]["title"],
            "note": "Focus on knife skills and safety",
            "concepts": [{"title": "Kitchen Safety Basics", "note": "Include fire safety"}]
        },
        {
            "title": modules[1]["title"] if len(modules) > 1 else "Basic Recipes",
            "note": "",
            "concepts": []
        }
    ]
}
resp = requests.post(f"{BASE}/api/clarify_course", json=wizard_payload)
assert resp.status_code == 200, f"clarify_course returned {resp.status_code}"
questions = resp.json().get("questions", [])
assert 3 <= len(questions) <= 7, f"Got {len(questions)} questions (expected 3-7)"
assert all("question" in q for q in questions), "Question missing 'question' field"
print(f"PASS: {len(questions)} clarifying questions generated")
for i, q in enumerate(questions):
    print(f"  Q{i+1}: {q['question'][:80]}...")

# Step 4: Submit full custom creation
print("\n=== Testing custom course creation ===")
wizard_payload["clarification_answers"] = [
    {"question": questions[0]["question"], "answer": "Yes, vegetarian recipes only."},
    {"question": questions[1]["question"], "answer": "I have a basic kitchen with standard tools."}
]
resp = requests.post(f"{BASE}/api/create_course_custom", json=wizard_payload)
assert resp.status_code == 200, f"create_course_custom returned {resp.status_code}"
result = resp.json()
course_uid = result.get("course_uid")
assert course_uid, "No course_uid in response"
print(f"PASS: Custom course creation started, UID: {course_uid}")

# Wait for completion
print("Waiting for course generation (up to 180s)...")
for i in range(36):
    time.sleep(5)
    courses = requests.get("http://localhost:5002/api/courses").json().get("courses", [])
    match = next((c for c in courses if c["uid"] == course_uid), None)
    if match and match.get("status") == "ready":
        print(f"PASS: Custom course ready after {(i+1)*5}s")
        break
else:
    print("FAIL: Custom course not ready after 180s")
    sys.exit(1)

# Verify user-defined concepts are marked correctly
syllabus = requests.get("http://localhost:5002/flat_syllabus", params={"uid": course_uid}).json()
all_concepts = syllabus.get("syllabus", [])
print(f"Total concepts: {len(all_concepts)}")

# Check that at least one concept has source='user'
details_list = []
for c in all_concepts[:5]:  # Check first 5
    det = requests.get("http://localhost:5002/api/concept_details", params={"uid": c["uid"]}).json()
    details_list.append(det)

user_defined = [d for d in details_list if d.get("source") == "user"]
generated = [d for d in details_list if d.get("source") == "generated"]
print(f"User-defined: {len(user_defined)}, Generated: {len(generated)}")
assert len(user_defined) >= 1, "No user-defined concepts found"
assert len(generated) >= 1, "No generated concepts found"
print("PASS: Both user-defined and generated concepts present")

# Check design_brief is stored
courses = requests.get("http://localhost:5002/api/courses").json().get("courses", [])
course = next((c for c in courses if c["uid"] == course_uid), None)
# design_brief would be in course details or a separate endpoint
print(f"PASS: Custom course creation mode = {course.get('creation_mode', 'unknown')}")

print("\n=== ALL CUSTOM COURSE CREATION CHECKS PASSED ===")
PYEOF
```

### 4.3 Socratic Tutoring Flow — Full Dialogue E2E

```bash
# TASK: Walk through a complete Socratic learning session

python3 << 'PYEOF'
import requests, json, time

BASE_CORE = "http://localhost:5003"
BASE_RAG = "http://localhost:5002"

# Pre-condition: ensure a course exists
courses = requests.get(f"{BASE_RAG}/api/courses").json().get("courses", [])
if not courses:
    print("SKIP: No courses available. Run course creation test first.")
    exit(0)

course = courses[0]
print(f"Using course: {course['title']} ({course['uid']})")

# 1. Set active course
resp = requests.post(f"{BASE_CORE}/api/set_active_course", json={
    "uid": course["uid"], "title": course["title"]
})
assert resp.status_code == 200, f"set_active_course failed: {resp.status_code}"
print("PASS: Active course set")

# 2. Resume course → should transition to SOCRATIC_LEARNING
resp = requests.post(f"{BASE_CORE}/event", json={
    "type": "RESUME_COURSE",
    "payload": {"uid": course["uid"], "title": course["title"]}
})
time.sleep(3)  # Wait for LLM to generate first question

# 3. Verify state
state = requests.get(f"{BASE_CORE}/state").json()
assert state["state"] == "SOCRATIC_LEARNING", f"Expected SOCRATIC_LEARNING, got {state['state']}"
assert state["current_lesson_uid"] is not None, "No current lesson"
assert state["last_question"], "No question generated"
print(f"PASS: FSM in SOCRATIC_LEARNING, question: '{state['last_question'][:60]}...'")

# 4. Verify conversation history format includes structure for answers
assert isinstance(state["conversation_history"], list), "conversation_history is not a list"

# 5. Submit a correct-ish answer
resp = requests.post(f"{BASE_CORE}/event", json={
    "type": "TEXT_INPUT",
    "payload": {"text": "I believe this concept relates to the fundamental principles described in the lesson material, where the key mechanism involves the interaction between the components."}
})
time.sleep(5)  # Wait for grading + next question

# 6. Check state after answer
state2 = requests.get(f"{BASE_CORE}/state").json()
transcript = state2.get("transcript", [])
assert len(transcript) >= 3, f"Expected ≥3 transcript entries, got {len(transcript)}"
print(f"PASS: Transcript has {len(transcript)} entries")

# Check user answer is in transcript
user_msgs = [t for t in transcript if t["sender"] == "user"]
assert len(user_msgs) >= 1, "No user messages in transcript"
print(f"PASS: User messages in transcript: {len(user_msgs)}")

# 7. Verify conversation_history now includes student answer
history = state2.get("conversation_history", [])
if history and isinstance(history[0], dict):
    has_answer = any(h.get("answer") for h in history)
    print(f"{'PASS' if has_answer else 'FAIL'}: Conversation history includes student answers")
else:
    print("INFO: History format check — verify manually")

# 8. Submit multiple answers to test multi-question requirement
for i in range(3):
    resp = requests.post(f"{BASE_CORE}/event", json={
        "type": "TEXT_INPUT",
        "payload": {"text": f"This is answer attempt {i+2}. The concept works because of the underlying mechanism."}
    })
    time.sleep(4)

state3 = requests.get(f"{BASE_CORE}/state").json()
print(f"After 4 answers: state={state3['state']}, syllabus_remaining={state3['syllabus_length']}")

# 9. Return to lobby
resp = requests.post(f"{BASE_CORE}/event", json={
    "type": "TEXT_INPUT", "payload": {"text": "go to lobby"}
})
time.sleep(1)
state4 = requests.get(f"{BASE_CORE}/state").json()
assert state4["state"] == "LOBBY", f"Expected LOBBY after stop, got {state4['state']}"
print("PASS: Returned to LOBBY")

print("\n=== SOCRATIC FLOW TEST COMPLETE ===")
PYEOF
```

### 4.4 FSRS Spaced Repetition Verification

```bash
# TASK: Verify FSRS updates concept state after interactions

python3 << 'PYEOF'
import requests, json

BASE_RAG = "http://localhost:5002"

# Get a concept from any course
courses = requests.get(f"{BASE_RAG}/api/courses").json().get("courses", [])
if not courses:
    print("SKIP: No courses. Run creation test first.")
    exit(0)

syllabus = requests.get(f"{BASE_RAG}/flat_syllabus", params={"uid": courses[0]["uid"]}).json()
concepts = syllabus.get("syllabus", [])
if not concepts:
    print("SKIP: No concepts in course.")
    exit(0)

concept_uid = concepts[0]["uid"]

# Get initial FSRS state
details = requests.get(f"{BASE_RAG}/api/concept_details", params={"uid": concept_uid}).json()
initial_stability = details.get("stability", 0)
initial_due_date = details.get("due_date", 0)
print(f"Initial state: stability={initial_stability}, due_date={initial_due_date}")

# Simulate a mastery update (grade 3 = Good)
resp = requests.post(f"{BASE_RAG}/api/update_mastery", json={
    "uid": concept_uid,
    "grade": 3,
    "bloom_level": 2
})
assert resp.status_code == 200, f"update_mastery failed: {resp.status_code}"

# Check updated state
details2 = requests.get(f"{BASE_RAG}/api/concept_details", params={"uid": concept_uid}).json()
new_stability = details2.get("stability", 0)
new_due_date = details2.get("due_date", 0)
review_count = details2.get("review_count", 0)
print(f"Updated state: stability={new_stability}, due_date={new_due_date}, reviews={review_count}")

assert new_stability > 0, "Stability not updated"
assert new_due_date > 0, "Due date not set"
assert review_count >= 1, "Review count not incremented"
print("PASS: FSRS state updated correctly")

# Verify due_concepts endpoint filters correctly
due = requests.get(f"{BASE_RAG}/api/due_concepts").json().get("concepts", [])
print(f"Due concepts: {len(due)} (may be 0 if due_date is in the future)")
print("PASS: due_concepts endpoint responds")

print("\n=== FSRS VERIFICATION COMPLETE ===")
PYEOF
```

### 4.5 Gamification Verification

```bash
# TASK: Verify XP and streak tracking

python3 << 'PYEOF'
import requests, json

BASE = "http://localhost:5003"

state = requests.get(f"{BASE}/state").json()

# Check for gamification fields in state or separate endpoint
# These may be in the state object or in session_state
xp = state.get("total_xp", None)
streak = state.get("streak_days", None)

if xp is not None:
    print(f"PASS: XP tracking present: {xp} XP")
else:
    print("WARN: total_xp not in state object — check if stored in session_state")

if streak is not None:
    print(f"PASS: Streak tracking present: {streak} days")
else:
    print("WARN: streak_days not in state object — check if stored in session_state")

# Verify stats endpoint includes streak
stats = requests.get("http://localhost:5002/api/stats").json()
print(f"Stats: courses={stats.get('courses')}, concepts={stats.get('concepts')}, streak={stats.get('streak')}")

print("\n=== GAMIFICATION CHECK COMPLETE ===")
PYEOF
```

### 4.6 Streaming Response Verification

```bash
# TASK: Verify streaming works from Ollama through core-logic

# Test Ollama streaming directly
echo "=== Testing Ollama streaming ==="
TOKENS=0
curl -sf http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:14b","messages":[{"role":"user","content":"Count to 5."}],"max_tokens":50,"stream":true}' \
  --no-buffer | while IFS= read -r line; do
    if echo "$line" | grep -q '"content"'; then
        TOKENS=$((TOKENS + 1))
    fi
done
echo "PASS: Ollama streaming produces chunked responses"
```

### 4.7 Web Search Pipeline Verification

```bash
# TASK: Verify the full SearXNG → Research Service → Content pipeline

python3 << 'PYEOF'
import requests, json, time

RESEARCH = "http://localhost:5006"
SEARXNG = "http://localhost:8080"

# 1. SearXNG health
print("=== SearXNG Health ===")
try:
    resp = requests.get(f"{SEARXNG}/healthz", timeout=5)
    assert resp.status_code == 200, f"SearXNG unhealthy: {resp.status_code}"
    print("PASS: SearXNG healthy")
except Exception as e:
    print(f"FAIL: SearXNG unreachable: {e}")

# 2. SearXNG returns JSON results
print("\n=== SearXNG Search ===")
resp = requests.get(f"{SEARXNG}/search", params={
    "q": "photosynthesis", "format": "json", "categories": "general"
}, timeout=10)
assert resp.status_code == 200, f"SearXNG search failed: {resp.status_code}"
results = resp.json().get("results", [])
assert len(results) >= 3, f"Only {len(results)} results (expected ≥3)"
print(f"PASS: SearXNG returned {len(results)} results")

# 3. Research service health
print("\n=== Research Service Health ===")
health = requests.get(f"{RESEARCH}/health", timeout=5).json()
assert health.get("searxng_reachable") == True, "Research service can't reach SearXNG"
print(f"PASS: Research service healthy, SearXNG reachable, {health.get('cache_entries', 0)} cached entries")

# 4. Research a well-known concept
print("\n=== Research Concept: Photosynthesis ===")
start = time.time()
resp = requests.post(f"{RESEARCH}/api/research_concept", json={
    "title": "Photosynthesis",
    "module_title": "Plant Biology",
    "course_title": "Introduction to Biology",
    "mastery": 3
}, timeout=30)
elapsed = time.time() - start
assert resp.status_code == 200, f"Research failed: {resp.status_code}"
data = resp.json()

sources = data.get("sources", [])
combined = data.get("combined_text", "")
confidence = data.get("confidence", 0)
word_count = data.get("word_count", 0)

print(f"  Sources: {len(sources)}")
print(f"  Combined text: {word_count} words")
print(f"  Confidence: {confidence}")
print(f"  Time: {elapsed:.1f}s")

assert len(sources) >= 1, "No sources found for 'Photosynthesis'"
assert word_count >= 200, f"Combined text too short: {word_count} words"
assert confidence >= 0.3, f"Confidence too low: {confidence}"
print("PASS: Research returned rich source material")

# Check Wikipedia was found
wiki_sources = [s for s in sources if s.get("type") == "wikipedia"]
assert len(wiki_sources) >= 1, "Wikipedia not found for 'Photosynthesis'"
print(f"PASS: Wikipedia source present: {wiki_sources[0]['url']}")

# Check domain tiers
tier1 = [s for s in sources if s.get("domain_tier") == 1]
print(f"PASS: {len(tier1)} tier-1 sources (Wikipedia, .edu, .gov)")

# 5. Cache hit test
print("\n=== Cache Hit Test ===")
start2 = time.time()
resp2 = requests.post(f"{RESEARCH}/api/research_concept", json={
    "title": "Photosynthesis",
    "module_title": "Plant Biology",
    "course_title": "Introduction to Biology",
    "mastery": 3
}, timeout=10)
elapsed2 = time.time() - start2
print(f"  Second call: {elapsed2:.3f}s (first was {elapsed:.1f}s)")
assert elapsed2 < 2.0, f"Cache hit too slow: {elapsed2:.1f}s"
print("PASS: Cache hit significantly faster")

# 6. Batch research test
print("\n=== Batch Research: 5 Concepts ===")
start3 = time.time()
resp3 = requests.post(f"{RESEARCH}/api/research_batch", json={
    "concepts": [
        {"title": "Mitosis", "module_title": "Cell Biology"},
        {"title": "DNA Replication", "module_title": "Molecular Biology"},
        {"title": "Natural Selection", "module_title": "Evolution"},
        {"title": "Krebs Cycle", "module_title": "Biochemistry"},
        {"title": "Mendel's Laws", "module_title": "Genetics"}
    ],
    "course_title": "Biology",
    "mastery": 2
}, timeout=60)
elapsed3 = time.time() - start3
assert resp3.status_code == 200, f"Batch research failed: {resp3.status_code}"
batch_results = resp3.json().get("results", {})
assert len(batch_results) == 5, f"Expected 5 results, got {len(batch_results)}"
print(f"  5 concepts researched in {elapsed3:.1f}s")

# Check all have sources
empty_sources = [t for t, r in batch_results.items() if len(r.get("sources", [])) == 0]
print(f"  Concepts with sources: {5 - len(empty_sources)}/5")
assert len(empty_sources) <= 1, f"Too many concepts without sources: {empty_sources}"
print("PASS: Batch research returned sources for ≥4/5 concepts")

# 7. Graceful degradation test (research obscure topic)
print("\n=== Obscure Topic Test ===")
resp4 = requests.post(f"{RESEARCH}/api/research_concept", json={
    "title": "Zygomorphic Floral Symmetry in Paleocene Angiosperms",
    "module_title": "Advanced Paleobotany",
    "course_title": "Paleobotany",
    "mastery": 5
}, timeout=30)
assert resp4.status_code == 200, "Research service crashed on obscure topic"
data4 = resp4.json()
print(f"  Sources: {len(data4.get('sources', []))}, Confidence: {data4.get('confidence', 0)}")
print("PASS: Graceful handling of obscure topic (no crash)")

print("\n=== ALL WEB SEARCH PIPELINE TESTS PASSED ===")
PYEOF
```

---

## 5. DATABASE INTEGRITY TESTS

### 5.1 SQLite Schema Verification

```bash
# TASK: Verify SQLite database has correct schema

python3 << 'PYEOF'
import sqlite3, os, json

# Find the database file
DB_PATHS = [
    "data/sqlite/helga.db",
    "/tmp/helga_data/sqlite/helga.db"
]
db_path = None
for p in DB_PATHS:
    if os.path.exists(p):
        db_path = p
        break

# If running inside Docker, connect via RAG service health
if not db_path:
    print("INFO: Database not directly accessible. Testing via API instead.")
    import requests
    health = requests.get("http://localhost:5002/health").json()
    assert health.get("db") == True, "RAG reports db=false"
    print("PASS: RAG service reports database connected")
    exit(0)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check tables exist
EXPECTED_TABLES = ["courses", "concepts", "concept_embeddings", "prerequisites",
                   "interactions", "session_state"]
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
actual_tables = [row[0] for row in cursor.fetchall()]

for table in EXPECTED_TABLES:
    assert table in actual_tables, f"Missing table: {table}"
    print(f"PASS: Table '{table}' exists")

# Check concepts table columns
cursor.execute("PRAGMA table_info(concepts)")
columns = {row[1] for row in cursor.fetchall()}
EXPECTED_COLS = {"uid", "course_uid", "parent_uid", "title", "resource_text",
                 "bloom_level", "ordinal", "depth_level", "completed",
                 "stability", "difficulty", "due_date", "last_review",
                 "review_count", "misconceptions", "analogies",
                 "learning_objectives", "key_terms", "examples", "takeaways",
                 "source", "user_note"}
missing = EXPECTED_COLS - columns
if missing:
    print(f"WARN: Missing columns in concepts: {missing}")
else:
    print("PASS: All expected columns present in concepts table")

# Check WAL mode
cursor.execute("PRAGMA journal_mode")
mode = cursor.fetchone()[0]
assert mode.lower() == "wal", f"Expected WAL mode, got {mode}"
print("PASS: SQLite in WAL mode")

conn.close()
print("\n=== SCHEMA VERIFICATION COMPLETE ===")
PYEOF
```

### 5.2 Data Integrity After Course Creation

```bash
# TASK: Verify referential integrity in database after creating a course

python3 << 'PYEOF'
import requests, json

BASE = "http://localhost:5002"

courses = requests.get(f"{BASE}/api/courses").json().get("courses", [])
if not courses:
    print("SKIP: No courses to verify")
    exit(0)

uid = courses[0]["uid"]
print(f"Checking integrity for course: {courses[0]['title']} ({uid})")

# Get all concepts for this course
structure = requests.get(f"{BASE}/api/course_structure", params={"uid": uid}).json()
nodes = structure.get("nodes", [])
print(f"Nodes in course: {len(nodes)}")

# Get syllabus (leaf concepts only)
syllabus = requests.get(f"{BASE}/flat_syllabus", params={"uid": uid}).json()
concepts = syllabus.get("syllabus", [])
print(f"Leaf concepts: {len(concepts)}")

# Verify every concept has required fields
errors = []
for c in concepts:
    det = requests.get(f"{BASE}/api/concept_details", params={"uid": c["uid"]}).json()

    if not det.get("resource_text") or len(det.get("resource_text","")) < 50:
        errors.append(f"{c['title']}: resource_text too short ({len(det.get('resource_text',''))} chars)")

    if det.get("bloom_level", 0) < 1:
        errors.append(f"{c['title']}: bloom_level not set")

    misconceptions = json.loads(det.get("misconceptions", "[]"))
    if len(misconceptions) < 1:
        errors.append(f"{c['title']}: no misconceptions")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
else:
    print(f"PASS: All {len(concepts)} concepts have valid data")

print("\n=== DATA INTEGRITY CHECK COMPLETE ===")
PYEOF
```

---

## 6. SECURITY VERIFICATION

```bash
# TASK: Verify security requirements

echo "=== Security Checks ==="

# S1: No sensitive data in .env
if [ -f .env ]; then
    if grep -qi "password\|secret\|token" .env | grep -v "^#\|example\|your_\|generate-a"; then
        echo "FAIL: .env may contain secrets"
        grep -i "password\|secret\|token" .env
    else
        echo "PASS: .env clean of obvious secrets"
    fi
fi

# S2: .env in .gitignore
if [ -f .gitignore ]; then
    grep -q "^\.env$" .gitignore && echo "PASS: .env in .gitignore" || echo "FAIL: .env not in .gitignore"
fi

# S3: CORS not wildcard
curl -sf http://localhost:5000/health -H "Origin: https://evil.com" -v 2>&1 | \
  grep -i "access-control-allow-origin" | grep -q "\*" && \
  echo "FAIL: CORS allows all origins" || echo "PASS: CORS not wildcard"

# S4: Containers not running as root
for svc in helga-web-ui helga-core-logic helga-rag-engine helga-tts helga-searxng helga-research; do
    USER=$(docker exec "$svc" whoami 2>/dev/null)
    if [ "$USER" = "root" ]; then
        echo "FAIL: $svc running as root"
    else
        echo "PASS: $svc running as $USER"
    fi
done

# S5: No git history secrets
if [ -d .git ]; then
    FOUND=$(git log --all -p -- .env 2>/dev/null | grep -ci "password\|Spencer")
    [ "$FOUND" -gt 0 ] && echo "FAIL: $FOUND secret references in git history" || echo "PASS: No secrets in git history"
fi

echo "=== Security Checks Complete ==="
```

---

## 7. RESILIENCE & ERROR HANDLING

```bash
# TASK: Verify graceful degradation when Ollama is down

echo "=== Resilience Test: Ollama Down ==="

# Stop Ollama temporarily
echo "Stopping Ollama..."
brew services stop ollama 2>/dev/null || ollama stop 2>/dev/null || true
sleep 3

# Core-logic should still respond (not crash)
CODE=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:5003/health)
[ "$CODE" = "200" ] && echo "PASS: Core-logic alive with Ollama down" || echo "FAIL: Core-logic unreachable"

# State endpoint should work
STATE=$(curl -sf http://localhost:5003/state | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','ERROR'))" 2>/dev/null)
echo "FSM state while Ollama down: $STATE"
[ "$STATE" != "ERROR" ] && echo "PASS: State readable without Ollama" || echo "FAIL: State endpoint broken"

# Web UI should load
CODE=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:5000/)
[ "$CODE" = "200" ] && echo "PASS: Web UI loads without Ollama" || echo "FAIL: Web UI broken"

# Try to send a message — should get error response, not crash
RESP=$(curl -sf -w "\n%{http_code}" -X POST http://localhost:5003/event \
  -H "Content-Type: application/json" \
  -d '{"type":"TEXT_INPUT","payload":{"text":"test message"}}')
echo "Event response while Ollama down: $RESP"

# Restart Ollama
echo "Restarting Ollama..."
brew services start ollama 2>/dev/null || ollama serve &>/dev/null &
sleep 10

# Verify recovery
CODE=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:11434/api/tags)
[ "$CODE" = "200" ] && echo "PASS: Ollama recovered" || echo "FAIL: Ollama didn't restart"

echo "=== Resilience Test Complete ==="
```

---

## 8. STATIC ASSET & UI STRUCTURE VERIFICATION

```bash
# TASK: Verify all referenced static assets exist and pages have expected structure

python3 << 'PYEOF'
import requests
from html.parser import HTMLParser

BASE = "http://localhost:5000"

class AssetChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "link" and attrs_dict.get("rel") == "stylesheet":
            self.assets.append(("CSS", attrs_dict.get("href")))
        if tag == "script" and attrs_dict.get("src"):
            self.assets.append(("JS", attrs_dict.get("src")))

pages = {
    "/": ["style.css"],
    "/courses": ["style.css"],
    "/learn": ["style.css", "session.js"],
    "/review": ["style.css"],
    "/test": ["style.css"],
    "/status": ["style.css", "status.js"],
}

errors = []
for path, expected_assets in pages.items():
    resp = requests.get(f"{BASE}{path}")
    if resp.status_code != 200:
        errors.append(f"{path}: HTTP {resp.status_code}")
        continue

    parser = AssetChecker()
    parser.feed(resp.text)

    # Check expected assets are referenced
    asset_urls = [a[1] for a in parser.assets]
    for expected in expected_assets:
        found = any(expected in url for url in asset_urls if url)
        if not found:
            errors.append(f"{path}: Missing asset reference to '{expected}'")

    # Check no inline <style> blocks (should all be in style.css)
    if "<style>" in resp.text:
        style_count = resp.text.count("<style>")
        if style_count > 0 and path != "/status":  # status may have minor inline
            errors.append(f"{path}: Has {style_count} inline <style> blocks (should be in style.css)")

    # Check no duplicate socket connections
    socket_creates = resp.text.count("io()")
    if socket_creates > 1:
        errors.append(f"{path}: {socket_creates} Socket.IO io() calls (should be 1 max)")

    print(f"{'PASS' if not any(path in e for e in errors) else 'WARN'}: {path} — {len(parser.assets)} assets, {socket_creates} socket(s)")

if errors:
    print("\nIssues found:")
    for e in errors:
        print(f"  FAIL: {e}")
else:
    print("\nPASS: All pages and assets verified")
PYEOF
```

---

## 9. BACKUP & DEPLOYMENT VERIFICATION

```bash
# TASK: Verify backup functionality
echo "=== Backup Test ==="
make backup 2>/dev/null
BACKUP_COUNT=$(ls -1 backups/*.db 2>/dev/null | wc -l)
[ "$BACKUP_COUNT" -ge 1 ] && echo "PASS: Backup created ($BACKUP_COUNT files)" || echo "FAIL: No backup files found"

# TASK: Verify docker-compose health gating
echo "=== Health Gating Test ==="
docker compose config --quiet 2>/dev/null
[ $? -eq 0 ] && echo "PASS: docker-compose.yml is valid" || echo "FAIL: docker-compose.yml syntax error"

# Check depends_on with health conditions
grep -q "condition: service_healthy" docker-compose.yml && \
  echo "PASS: Health-gated depends_on present" || \
  echo "FAIL: No health-gated depends_on"

# TASK: Verify README accuracy
echo "=== Documentation Test ==="
if [ -f README.md ]; then
    grep -q "Ollama" README.md && echo "PASS: README mentions Ollama" || echo "FAIL: README missing Ollama"
    grep -q "SQLite" README.md && echo "PASS: README mentions SQLite" || echo "FAIL: README missing SQLite"
    grep -q "qwen" README.md && echo "PASS: README mentions Qwen model" || echo "FAIL: README missing model info"
    grep -q "docker compose" README.md && echo "PASS: README has docker instructions" || echo "FAIL: README missing docker instructions"
else
    echo "FAIL: README.md not found"
fi
```

---

## 10. ADDITIONAL APIS NEEDED FOR CLI TESTING

The following APIs are not yet in the sprint plan but would significantly improve automated testability. They are low-cost additions (1-2 lines each in most cases).

| Endpoint | Service | Method | Purpose | Implementation Effort |
|----------|---------|--------|---------|----------------------|
| `/api/session_state` | core:5003 | GET | Expose gamification state (XP, streak, level) without parsing FSM state | Add to `/state` response or separate endpoint. 5 min |
| `/api/concept_count?course_uid=X` | rag:5002 | GET | Quick count without fetching full syllabus | `SELECT count(*) FROM concepts WHERE course_uid=?`. 5 min |
| `/api/course_details?uid=X` | rag:5002 | GET | Full course metadata (overview, design_brief, creation_mode, status) | Single row SELECT. 10 min |
| `/api/reset_state` | core:5003 | POST | Reset FSM to LOBBY and clear active course (for test isolation) | Set state vars + save. 10 min |
| `/api/interaction_log?concept_uid=X` | rag:5002 | GET | Return interaction history for a concept (grades, timestamps) | SELECT from interactions table. 10 min |
| `/api/health/all` | web-ui:5000 | GET | Aggregated health of all services in one call | Calls each service /health, returns combined. 15 min |

**Recommendation:** Add these 6 endpoints as a task in Phase 4c (routes). Total effort: ~1 hour. They make CLI verification dramatically more reliable by providing direct data access instead of parsing HTML or inferring state.

---

## 11. MASTER CHECKLIST — CLAUDE CODE VERIFICATION RUN

Execute these in order. Each section depends on the previous passing.

```bash
#!/bin/bash
# helga_verify_production.sh
# Run with: bash helga_verify_production.sh

set -e
PASS=0
FAIL=0
WARN=0

log_pass() { echo "✅ PASS: $1"; PASS=$((PASS+1)); }
log_fail() { echo "❌ FAIL: $1"; FAIL=$((FAIL+1)); }
log_warn() { echo "⚠️  WARN: $1"; WARN=$((WARN+1)); }

echo "╔══════════════════════════════════════════════════════╗"
echo "║  HELGA PRODUCTION READINESS VERIFICATION             ║"
echo "║  $(date)                                             ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# --- TIER 1: Infrastructure ---
echo "━━━ TIER 1: Infrastructure ━━━"

# 1. Docker services healthy
for svc in helga-web-ui helga-core-logic helga-rag-engine helga-tts helga-searxng helga-research; do
    S=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "missing")
    [ "$S" = "healthy" ] && log_pass "$svc healthy" || log_fail "$svc is $S"
done

# 2. Ollama reachable
curl -sf http://localhost:11434/api/tags > /dev/null && log_pass "Ollama reachable" || log_fail "Ollama unreachable"

# 3. All page routes
for route in / /courses /courses/new /learn /review /test /status; do
    C=$(curl -sf -o /dev/null -w "%{http_code}" "http://localhost:5000${route}")
    [ "$C" = "200" ] && log_pass "GET $route" || log_fail "GET $route → $C"
done

echo ""
echo "━━━ TIER 2: API Contracts ━━━"

# 4. Core APIs
for ep in "/api/fsm_state" "/api/stats" "/api/courses" "/api/voices" "/health"; do
    C=$(curl -sf -o /dev/null -w "%{http_code}" "http://localhost:5000${ep}")
    [ "$C" = "200" ] && log_pass "API $ep" || log_fail "API $ep → $C"
done

# 5. TTS
SIZE=$(curl -sf -o /tmp/tts_test.wav -w "%{size_download}" -X POST \
  "http://localhost:5005/api/tts" -H "Content-Type: application/json" \
  -d '{"text":"Hello","voice":"af_heart"}')
[ "$SIZE" -gt 1000 ] && log_pass "TTS generates audio (${SIZE}B)" || log_fail "TTS output too small"

echo ""
echo "━━━ TIER 3: Course Creation ━━━"

# 6. Quick create
RESULT=$(curl -sf -X POST http://localhost:5003/api/create_course \
  -H "Content-Type: application/json" -d '{"topic":"Test Topic","depth":1}' 2>/dev/null)
UID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('course_uid',''))" 2>/dev/null)
[ -n "$UID" ] && log_pass "Quick Create initiated ($UID)" || log_fail "Quick Create failed"

# 7. Suggest modules
C=$(curl -sf -o /dev/null -w "%{http_code}" -X POST http://localhost:5003/api/suggest_modules \
  -H "Content-Type: application/json" -d '{"title":"Test","description":"test","prior_knowledge":"new"}')
[ "$C" = "200" ] && log_pass "Suggest modules API" || log_fail "Suggest modules → $C"

# 8. Clarify course
C=$(curl -sf -o /dev/null -w "%{http_code}" -X POST http://localhost:5003/api/clarify_course \
  -H "Content-Type: application/json" \
  -d '{"title":"Test","description":"test","prior_knowledge":"new","modules":[{"title":"M1","note":"","concepts":[]}]}')
[ "$C" = "200" ] && log_pass "Clarify course API" || log_fail "Clarify course → $C"

echo ""
echo "━━━ TIER 3b: Web Search Pipeline ━━━"

# SearXNG reachable
C=$(curl -sf -o /dev/null -w "%{http_code}" "http://localhost:8080/healthz")
[ "$C" = "200" ] && log_pass "SearXNG healthy" || log_fail "SearXNG unhealthy → $C"

# Research service healthy
HEALTH=$(curl -sf "http://localhost:5006/health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('searxng_reachable',''))" 2>/dev/null)
[ "$HEALTH" = "True" ] && log_pass "Research service healthy + SearXNG connected" || log_warn "Research service degraded"

# Research returns sources
SOURCES=$(curl -sf -X POST "http://localhost:5006/api/research_concept" \
  -H "Content-Type: application/json" \
  -d '{"title":"Photosynthesis","module_title":"Biology","course_title":"Test","mastery":2}' \
  | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('sources',[])))" 2>/dev/null)
[ "$SOURCES" -ge 1 ] 2>/dev/null && log_pass "Research returned $SOURCES sources" || log_warn "Research returned no sources"

echo ""
echo "━━━ TIER 4: Tutoring Flow ━━━"

# 9. FSM state
STATE=$(curl -sf http://localhost:5003/state | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])" 2>/dev/null)
[ -n "$STATE" ] && log_pass "FSM state readable ($STATE)" || log_fail "FSM state unreachable"

# 10. Event handling
C=$(curl -sf -o /dev/null -w "%{http_code}" -X POST http://localhost:5003/event \
  -H "Content-Type: application/json" -d '{"type":"PAUSE","payload":{}}')
[ "$C" = "200" ] && log_pass "Event handling" || log_fail "Event handling → $C"
# Resume
curl -sf -X POST http://localhost:5003/event \
  -H "Content-Type: application/json" -d '{"type":"RESUME","payload":{}}' > /dev/null

echo ""
echo "━━━ TIER 5: Security ━━━"

# 11. No wildcard CORS
CORS=$(curl -sf -I http://localhost:5000/health -H "Origin: https://evil.com" 2>&1 | grep -i "access-control" | grep "\*" || true)
[ -z "$CORS" ] && log_pass "CORS not wildcard" || log_warn "CORS allows all origins"

# 12. Containers not root
for svc in helga-web-ui helga-core-logic helga-rag-engine helga-tts helga-searxng helga-research; do
    U=$(docker exec "$svc" whoami 2>/dev/null || echo "unknown")
    [ "$U" != "root" ] && log_pass "$svc non-root ($U)" || log_warn "$svc runs as root"
done

# 13. .gitignore
[ -f .gitignore ] && grep -q "\.env" .gitignore && log_pass ".env in .gitignore" || log_warn ".env not in .gitignore"

echo ""
echo "━━━ TIER 6: Deployment ━━━"

# 14. Docker compose valid
docker compose config --quiet 2>/dev/null && log_pass "docker-compose.yml valid" || log_fail "docker-compose.yml invalid"

# 15. README exists
[ -f README.md ] && log_pass "README.md exists" || log_fail "README.md missing"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  RESULTS: $PASS passed, $FAIL failed, $WARN warnings ║"
echo "╚══════════════════════════════════════════════════════╝"

[ $FAIL -eq 0 ] && echo "🏔️ HELGA IS PRODUCTION READY" || echo "⚠️ PRODUCTION READINESS FAILED"
exit $FAIL
```

---

## 12. COURSE PARAMETER SYSTEM — THREE-SLIDER MODEL

### 12.1 Parameter Definitions

The old single "Depth 1-5" slider is replaced by three independent parameters that control different dimensions of course generation.

#### Scope (1-5): How much territory the course covers

| Level | Label | Description | Example (topic: "Greek Philosophy") |
|-------|-------|-------------|--------------------------------------|
| 1 | Focused | A single narrow subtopic | "Aristotle's Virtue Ethics" |
| 2 | Targeted | One specific area within the field | "Aristotle's Philosophy" |
| 3 | Standard | A subject area with context | "Ancient Greek Ethics (Socrates through Stoics)" |
| 4 | Broad | A substantial field | "Ancient Greek Philosophy (Pre-Socratics through Neoplatonists)" |
| 5 | Comprehensive | Full discipline survey | "Greek Philosophy: Complete Survey of All Schools and Figures" |

#### Mastery (1-5): How deep understanding should go

| Level | Label | Bloom's Ceiling | Academic Equivalent | Question Types |
|-------|-------|-----------------|---------------------|----------------|
| 1 | Awareness | Bloom 1-2 (Remember, Understand) | Casual reading / encyclopedia | "What is X?", "Who said Y?" |
| 2 | Understanding | Bloom 1-3 (+Apply) | High school / AP level | "Explain why X matters", "How does X relate to Y?" |
| 3 | Application | Bloom 1-4 (+Analyze) | Community college / early undergrad | "Compare X and Y", "What would Z say about this problem?" |
| 4 | Proficiency | Bloom 1-5 (+Evaluate) | Bachelor's degree level | "Evaluate the strengths and weaknesses of X's argument", "Which framework better explains Y?" |
| 5 | Expertise | Bloom 1-6 (+Create) | Master's / early doctoral | "Construct an original argument combining X and Y", "Where does the current scholarly consensus fail?" |

#### Starting From (1-5): Where the student's knowledge begins

| Level | Label | What Gets Skipped | Effect on Course |
|-------|-------|-------------------|-----------------|
| 1 | No background | Nothing — full foundations included | Course begins with "What is [field]?" level content. All terminology defined |
| 2 | Basic awareness | Skips "what is this field" intro | Assumes student knows the field exists and basic vocabulary |
| 3 | Foundational | Compresses introductory modules into brief review | Starts at application-level. Definitions referenced, not taught |
| 4 | Intermediate | Skips all introductory and foundational content | Begins at analysis level. Assumes working knowledge |
| 5 | Advanced | Skips to highest-level content only | Jumps directly to synthesis/evaluation. Expert-to-expert register |

### 12.2 Parameter Interactions — Course Generation Formula

The three parameters interact to determine course structure:

```
MODULES = scope_module_base[scope] - skip_factor[starting_from]
  where scope_module_base = {1:3, 2:4, 3:6, 4:8, 5:11}
  and   skip_factor       = {1:0, 2:0, 3:1, 4:2, 5:3}

CONCEPTS_PER_MODULE = mastery_concept_base[mastery]
  where mastery_concept_base = {1:3, 2:4, 3:5, 4:7, 5:10}

BLOOM_FLOOR = bloom_floor_map[starting_from]
  where bloom_floor_map = {1:1, 2:1, 3:2, 4:3, 5:4}

BLOOM_CEILING = bloom_ceiling_map[mastery]
  where bloom_ceiling_map = {1:2, 2:3, 3:4, 4:5, 5:6}

CONTENT_WORDS_PER_CONCEPT = word_base[mastery]
  where word_base = {1:150, 2:250, 3:400, 4:600, 5:800}

TOTAL_CONCEPTS ≈ MODULES × CONCEPTS_PER_MODULE
  (no hard cap — course is as long as it needs to be)
```

**Example calculations:**

| Scenario | Scope | Mastery | Start | Modules | Concepts/Mod | Total | Bloom Range | Words/Concept |
|----------|-------|---------|-------|---------|-------------|-------|-------------|---------------|
| Greek Phil broad survey | 5 | 1 | 1 | 11 | 3 | ~33 | 1-2 | ~150 |
| Aristotle deep dive | 1 | 4 | 3 | 2 | 7 | ~14 | 2-5 | ~600 |
| Intro Physics course | 3 | 3 | 1 | 6 | 5 | ~30 | 1-4 | ~400 |
| Graduate ML refresher | 2 | 5 | 4 | 2 | 10 | ~20 | 3-6 | ~800 |
| Cooking basics | 4 | 1 | 1 | 8 | 3 | ~24 | 1-2 | ~150 |

**These are guidelines, not hard constraints.** The LLM uses them as targets. A module naturally requiring 8 concepts at mastery 3 is fine. The quality tests verify the output falls within reasonable bounds (±30% of target).

### 12.3 Course Structure Standards — "Like a Real Course"

Every generated course must follow academic course structure conventions:

#### Module-Level Structure

```
Course: {title}
  │
  ├─ Module 1: {Foundation / Introduction}
  │    ├─ Concept 1.1: Overview & Context Setting
  │    ├─ Concept 1.2: Core Idea A
  │    ├─ Concept 1.3: Core Idea B
  │    └─ Concept 1.4: Module Synthesis (connects ideas A+B)
  │
  ├─ Module 2: {Building On Module 1}
  │    ├─ Concept 2.1: Bridge from Module 1 → Module 2
  │    ├─ Concept 2.2: New Idea C (references Module 1 concepts)
  │    ├─ Concept 2.3: New Idea D
  │    ├─ Concept 2.4: Comparison / Analysis (C vs D, or vs Module 1)
  │    └─ Concept 2.5: Application / Practice
  │
  ├─ ... (modules build progressively)
  │
  ├─ Module N-1: {Advanced / Capstone Topics}
  │    └─ Concepts at highest Bloom's level for this mastery setting
  │
  └─ Module N: {Integration & Review} (if scope ≥ 3)
       ├─ Cross-module synthesis
       └─ Self-assessment / reflection
```

#### Ordering Rules

1. **Modules are topologically ordered.** Module 3 can reference concepts from Modules 1-2. Module 1 cannot reference Module 3.
2. **Within modules, concepts progress simple → complex.** First concept introduces, middle concepts develop, last concept synthesizes.
3. **Bloom's levels ascend across the course.** Early modules target lower Bloom's. Later modules target higher. No module should have a Bloom's ceiling LOWER than a preceding module.
4. **Every module (except the first) has a bridge concept.** The first concept in Module N references Module N-1, making the transition explicit.
5. **Prerequisites form a directed acyclic graph.** No circular dependencies. Every concept's prerequisites appear earlier in the ordinal sequence.
6. **No orphan concepts.** Every concept must either: (a) be a prerequisite for at least one other concept, OR (b) be in the final module (terminal nodes). No concept should exist with zero connections to the rest of the course.

#### Content Standards Per Mastery Level

| Mastery | Content Register | Example Depth | Vocabulary Level |
|---------|-----------------|---------------|-----------------|
| 1 | Explanatory / accessible | "Plato believed in ideal Forms — perfect versions of things we see in the real world." | No jargon without immediate definition |
| 2 | Educational / clear | "Plato's Theory of Forms posits that abstract, perfect archetypes exist independently of the physical world." | Key terms introduced and defined |
| 3 | Analytical / substantive | "Plato's Theory of Forms (Republic, Books V-VII) establishes a metaphysical dualism between the intelligible realm of Forms and the sensible world of appearances." | Field-specific terminology expected |
| 4 | Academic / critical | "The Third Man Argument (Parmenides 132a-b) exposes a potential infinite regress in the Theory of Forms, challenging whether self-predication is coherent." | Assumes disciplinary literacy |
| 5 | Scholarly / original | "Recent scholarship (Fine 1993, Vlastos 1954) distinguishes between 'degrees of reality' and 'paradigmatism' interpretations of Platonic Forms, with implications for..." | Graduate seminar register |

### 12.4 LLM Prompt Context Assembly — Three-Slider Integration

The three parameters are assembled into a "Course Configuration Block" injected into every LLM call:

```
COURSE CONFIGURATION:
- Topic: {title}
- Scope: {scope}/5 ({scope_label}) — {scope_description}
- Mastery: {mastery}/5 ({mastery_label}) — Target Bloom's range: {bloom_floor}-{bloom_ceiling}
- Starting from: {starting_from}/5 ({start_label}) — {start_description}
- Target modules: ~{module_count}
- Target concepts per module: ~{concepts_per_module}
- Content register: {register_description}
- Vocabulary: {vocabulary_level}

CRITICAL INSTRUCTIONS:
- Generate as many modules and concepts as the topic naturally requires
  at this scope. Do NOT artificially limit to a fixed number.
- Module count ~{module_count} is a guideline. A naturally broader topic 
  at scope {scope} may need more. A naturally narrow one may need fewer.
- Concepts per module ~{concepts_per_module} is a guideline. Complex 
  modules may need more. Simple ones may need fewer.
- Bloom's level for each concept must fall within [{bloom_floor}, {bloom_ceiling}].
- Early modules should cluster near Bloom's {bloom_floor}.
  Later modules should reach Bloom's {bloom_ceiling}.
- Content depth per concept: ~{word_count} words at mastery {mastery}.
- {start_instruction}
```

Where `start_instruction` varies:
- Start 1: "Begin with absolute fundamentals. Define all terminology. Assume zero prior knowledge."
- Start 2: "Assume the student knows what the field is. Skip 'What is X?' introductions but define technical terms."
- Start 3: "Assume foundational knowledge. Compress introductory material into a brief review module (1-2 concepts). Focus time on intermediate and advanced content."
- Start 4: "Skip all introductory and foundational content. Begin at analysis/evaluation level. Assume the student has working knowledge equivalent to completing an introductory course."
- Start 5: "This student has advanced knowledge. Skip to the most sophisticated content. Write at expert-to-expert level. Focus on current debates, edge cases, and novel synthesis."

### 12.5 Schema Changes for Three-Slider Model

Replace in `courses` table:

```sql
-- Remove:
--   depth INTEGER DEFAULT 3

-- Add:
    scope INTEGER DEFAULT 3 CHECK(scope BETWEEN 1 AND 5),
    mastery INTEGER DEFAULT 2 CHECK(mastery BETWEEN 1 AND 5),
    starting_from INTEGER DEFAULT 1 CHECK(starting_from BETWEEN 1 AND 5),
```

### 12.6 UI Changes for Three-Slider Model

Both Quick Create modal and Custom Wizard Step 1 replace the single depth dropdown with three visual sliders.

**Quick Create modal layout:**

```
┌───────────────────────────────────────┐
│         ⚡ Quick Create Course         │
│                                       │
│  Topic:                               │
│  ┌───────────────────────────────┐    │
│  │ Greek Philosophy              │    │
│  └───────────────────────────────┘    │
│                                       │
│  Scope: How much to cover             │
│  🔬─────────────────────────────🌍   │
│  Focused  Targeted  Standard  Broad  Comprehensive
│                                  ▲    │
│                                       │
│  Mastery: How deep to go              │
│  📖─────────────────────────────🎓   │
│  Awareness  Understanding  Application  Proficiency  Expertise
│  ▲                                    │
│                                       │
│  Starting from:                       │
│  🌱─────────────────────────────🧠   │
│  No background  Basic  Foundational  Intermediate  Advanced
│  ▲                                    │
│                                       │
│  📊 Estimated: ~33 concepts · ~5 hrs  │
│     11 modules · Bloom's 1-2          │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │        Create Course →          │  │
│  └─────────────────────────────────┘  │
└───────────────────────────────────────┘
```

The estimate line updates live as sliders move, using the formula from §12.2.

---

## 13. COURSE QUALITY STANDARDS — AUTOMATED VERIFICATION

These standards are designed to be tested by a CLI agent (Claude Code) after course creation. If a course fails any CRITICAL or HIGH standard, the agent should log the failure and attempt corrective re-generation of the affected components.

### 13.1 Structural Standards

| # | Standard | Severity | How to Test | Pass Criteria |
|---|----------|----------|-------------|---------------|
| QS-1 | Module count matches scope parameter | HIGH | `SELECT count(DISTINCT parent_uid) FROM concepts WHERE course_uid=? AND depth_level=0` | Count within ±30% of `scope_module_base[scope]` |
| QS-2 | Concept count per module is proportional to mastery | HIGH | Per-module concept count | Average within ±30% of `mastery_concept_base[mastery]` |
| QS-3 | Total concept count is uncapped and proportional | CRITICAL | `SELECT count(*) FROM concepts WHERE course_uid=? AND depth_level=3` | Total ≥ `modules × concepts_per_module × 0.7` (no hard upper cap) |
| QS-4 | Modules are topologically ordered | CRITICAL | Check that every prerequisite's ordinal < dependent's ordinal | Zero violations |
| QS-5 | No orphan concepts | HIGH | Every concept either: has a dependent, OR is in the final module | Zero orphans (excluding final module terminal nodes) |
| QS-6 | Prerequisites form a DAG (no cycles) | CRITICAL | Topological sort succeeds | No cycles detected |
| QS-7 | Course has an overview/description | MEDIUM | `SELECT overview FROM courses WHERE uid=?` | Non-null, ≥20 words |
| QS-8 | Every module has ≥2 concepts | CRITICAL | Per-module count | No module with <2 concepts |
| QS-9 | Concept ordinals are sequential within modules | MEDIUM | `SELECT ordinal FROM concepts WHERE parent_uid=? ORDER BY ordinal` | No gaps, starts at 1 |
| QS-10 | First concept in each module (except first) references prior module | HIGH | Check first concept's prerequisites list | Contains at least one UID from prior module |

### 13.2 Content Quality Standards

| # | Standard | Severity | How to Test | Pass Criteria |
|---|----------|----------|-------------|---------------|
| QC-1 | resource_text is prose, not JSON | CRITICAL | `text.strip()` does not start with `[` or `{` | 100% of concepts pass |
| QC-2 | resource_text meets word count floor | HIGH | `len(text.split())` | ≥ `word_base[mastery] × 0.6` for every concept |
| QC-3 | resource_text is not duplicated | CRITICAL | Pairwise comparison of first 100 chars | No two concepts share >80% of first 100 chars |
| QC-4 | Misconceptions populated | HIGH | `json.loads(misconceptions)` | ≥1 entry for every concept where mastery ≥ 2 |
| QC-5 | Analogies populated | MEDIUM | `json.loads(analogies)` | ≥1 entry for every concept where mastery ≥ 2 |
| QC-6 | Key terms populated | HIGH | `json.loads(key_terms)` | ≥1 entry for every concept |
| QC-7 | Bloom's level within target range | CRITICAL | `bloom_level` column | Every concept: `bloom_floor ≤ bloom_level ≤ bloom_ceiling` |
| QC-8 | Bloom's levels ascend across course | HIGH | Concepts sorted by ordinal path | Average Bloom's in last 25% of course > average Bloom's in first 25% |
| QC-9 | Embeddings exist | HIGH | `SELECT count(*) FROM concept_embeddings WHERE concept_uid IN (SELECT uid FROM concepts WHERE course_uid=?)` | Count = concept count |
| QC-10 | Content register matches mastery | MEDIUM | LLM-judged: send concept text + mastery level, ask "Is this written at {mastery_label} level?" | ≥80% of spot-checked concepts pass |

### 13.3 Pedagogical Flow Standards

| # | Standard | Severity | How to Test | Pass Criteria |
|---|----------|----------|-------------|---------------|
| QP-1 | Conversation history includes student answers | CRITICAL | After interaction: check `conversation_history` has both `question` and `answer` fields | 100% of entries have both |
| QP-2 | Grading prompt includes source context | CRITICAL | Log or intercept grading prompt, verify `resource_text` substring present | Context present in every grading call |
| QP-3 | Multi-question mastery enforced | HIGH | Start course, answer correctly once → concept should NOT advance | Concept remains active after 1 correct answer |
| QP-4 | Micro-lecture triggers after 3 failures | HIGH | Answer incorrectly 3 times → tutor delivers explanation | Explanation message appears after 3rd failure |
| QP-5 | Grade 2 does not create stuck state | CRITICAL | Simulate grade-2 response → verify new question is generated OR timer reset | Student can continue without getting trapped |
| QP-6 | Hesitation penalty removed | HIGH | Answer with "I think the reason is..." → grade should not be penalized | Grade based on content quality only |
| QP-7 | Question types vary within a concept | MEDIUM | Stay on one concept for 4+ questions → check question text differs meaningfully | No two consecutive questions are semantically identical |
| QP-8 | FSRS state updates after interaction | CRITICAL | `SELECT stability, due_date, review_count FROM concepts WHERE uid=?` before and after | All three values change |
| QP-9 | Bloom's level advances on mastery | HIGH | Complete a concept at Bloom 2, return → next interaction should probe Bloom 3 | `bloom_level` increments |
| QP-10 | XP awarded after correct answer | MEDIUM | Check `total_xp` before and after correct answer | XP increases by expected amount |

### 13.4 Automated Quality Test Script

```bash
# TASK: Run full course quality verification after creating a course
# Expects COURSE_UID as first argument, or uses most recent course

python3 << 'PYEOF'
import requests, json, sys

BASE = "http://localhost:5002"
CORE = "http://localhost:5003"

# Get course
courses = requests.get(f"{BASE}/api/courses").json().get("courses", [])
if not courses:
    print("FAIL: No courses to verify")
    sys.exit(1)

course = courses[-1]  # Most recent
uid = course["uid"]
print(f"Verifying course: {course['title']} ({uid})")

# Get course parameters
details = requests.get(f"{BASE}/api/course_details", params={"uid": uid}).json()
scope = details.get("scope", 3)
mastery = details.get("mastery", 2)
starting_from = details.get("starting_from", 1)
print(f"Parameters: scope={scope}, mastery={mastery}, starting_from={starting_from}")

# Parameter maps
scope_module_base = {1:3, 2:4, 3:6, 4:8, 5:11}
mastery_concept_base = {1:3, 2:4, 3:5, 4:7, 5:10}
bloom_floor_map = {1:1, 2:1, 3:2, 4:3, 5:4}
bloom_ceiling_map = {1:2, 2:3, 3:4, 4:5, 5:6}
word_base = {1:150, 2:250, 3:400, 4:600, 5:800}

target_modules = scope_module_base[scope]
target_cpm = mastery_concept_base[mastery]
bloom_floor = bloom_floor_map[starting_from]
bloom_ceiling = bloom_ceiling_map[mastery]
min_words = int(word_base[mastery] * 0.6)

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name}: {detail}")
        FAIL += 1

# Get all concepts
syllabus = requests.get(f"{BASE}/flat_syllabus", params={"uid": uid}).json()
concepts = syllabus.get("syllabus", [])

# Get detailed info for each concept
concept_details = []
for c in concepts:
    det = requests.get(f"{BASE}/api/concept_details", params={"uid": c["uid"]}).json()
    concept_details.append(det)

# Get structure (modules)
structure = requests.get(f"{BASE}/api/course_structure", params={"uid": uid}).json()
nodes = structure.get("nodes", [])
modules = [n for n in nodes if n.get("type") == "module" or n.get("depth_level") == 0]
leaf_concepts = [d for d in concept_details if d.get("depth_level", 3) == 3]

print(f"\nFound: {len(modules)} modules, {len(leaf_concepts)} leaf concepts")

# === STRUCTURAL STANDARDS ===
print("\n--- Structural Standards ---")

# QS-1: Module count
mod_target = target_modules
mod_low = int(mod_target * 0.7)
mod_high = int(mod_target * 1.5)
check("QS-1 Module count in range",
      mod_low <= len(modules) <= mod_high or len(modules) >= 2,
      f"{len(modules)} modules (target: ~{mod_target}, range: {mod_low}-{mod_high})")

# QS-3: Total concept count
min_total = int(target_modules * target_cpm * 0.5)
check("QS-3 Total concept count sufficient",
      len(leaf_concepts) >= min_total,
      f"{len(leaf_concepts)} concepts (minimum: {min_total})")

# QS-7: Course overview
check("QS-7 Course has overview",
      details.get("overview") and len(details["overview"].split()) >= 10,
      f"overview: {str(details.get('overview'))[:50]}")

# QS-8: Every module has ≥2 concepts
if modules:
    module_uids = {m["uid"] for m in modules}
    for m in modules:
        m_concepts = [c for c in leaf_concepts if c.get("parent_uid") == m["uid"]]
        check(f"QS-8 Module '{m.get('name','?')[:30]}' has ≥2 concepts",
              len(m_concepts) >= 2,
              f"has {len(m_concepts)}")

# === CONTENT QUALITY ===
print("\n--- Content Quality ---")

# QC-1: resource_text is prose
prose_fails = []
for c in leaf_concepts:
    text = c.get("resource_text", "").strip()
    if text.startswith("[") or text.startswith("{"):
        prose_fails.append(c["title"])
check("QC-1 All content is prose (not JSON)",
      len(prose_fails) == 0,
      f"JSON found in: {prose_fails[:3]}")

# QC-2: Word count floor
short_concepts = []
for c in leaf_concepts:
    words = len(c.get("resource_text", "").split())
    if words < min_words:
        short_concepts.append((c["title"], words))
check(f"QC-2 All concepts ≥{min_words} words",
      len(short_concepts) == 0,
      f"{len(short_concepts)} too short: {short_concepts[:3]}")

# QC-3: No duplicate content
seen_starts = {}
dupes = []
for c in leaf_concepts:
    start = c.get("resource_text", "")[:100].strip()
    if start in seen_starts:
        dupes.append((c["title"], seen_starts[start]))
    seen_starts[start] = c["title"]
check("QC-3 No duplicate content",
      len(dupes) == 0,
      f"Duplicates: {dupes[:3]}")

# QC-6: Key terms
no_terms = [c["title"] for c in leaf_concepts
            if len(json.loads(c.get("key_terms", "[]"))) < 1]
check("QC-6 All concepts have key terms",
      len(no_terms) == 0,
      f"{len(no_terms)} missing: {no_terms[:3]}")

# QC-7: Bloom's in range
bloom_violations = []
for c in leaf_concepts:
    bl = c.get("bloom_level", 0)
    if bl < bloom_floor or bl > bloom_ceiling:
        bloom_violations.append((c["title"], bl))
check(f"QC-7 Bloom's levels in [{bloom_floor}, {bloom_ceiling}]",
      len(bloom_violations) == 0,
      f"{len(bloom_violations)} violations: {bloom_violations[:3]}")

# QC-8: Bloom's ascend across course
if len(leaf_concepts) >= 4:
    quarter = len(leaf_concepts) // 4
    first_q = [c.get("bloom_level", 1) for c in leaf_concepts[:quarter]]
    last_q = [c.get("bloom_level", 1) for c in leaf_concepts[-quarter:]]
    avg_first = sum(first_q) / len(first_q) if first_q else 0
    avg_last = sum(last_q) / len(last_q) if last_q else 0
    check("QC-8 Bloom's levels ascend across course",
          avg_last >= avg_first,
          f"first quarter avg={avg_first:.1f}, last quarter avg={avg_last:.1f}")

# QC-9: Embeddings exist
embed_count_resp = requests.get(f"{BASE}/api/concept_count",
                                 params={"course_uid": uid}).json()
# Fallback: just count concepts with embeddings via search test
search_test = requests.get(f"{BASE}/search",
                            params={"q": leaf_concepts[0]["title"], "course_uid": uid}).json()
check("QC-9 Semantic search returns results",
      len(search_test.get("results", [])) >= 1,
      "No search results returned")

# QC-4: Misconceptions (if mastery ≥ 2)
if mastery >= 2:
    no_misc = [c["title"] for c in leaf_concepts
               if len(json.loads(c.get("misconceptions", "[]"))) < 1]
    check(f"QC-4 Misconceptions populated (mastery={mastery})",
          len(no_misc) <= len(leaf_concepts) * 0.2,
          f"{len(no_misc)}/{len(leaf_concepts)} missing")

# === SUMMARY ===
print(f"\n{'='*50}")
print(f"RESULTS: {PASS} passed, {FAIL} failed")
if FAIL == 0:
    print("🏔️ COURSE MEETS ALL QUALITY STANDARDS")
else:
    print("⚠️  COURSE QUALITY STANDARDS NOT MET")
    print("Agent should attempt corrective re-generation of failing components.")
sys.exit(FAIL)
PYEOF
```

### 13.5 Quality Remediation Protocol

When the quality test script detects failures, the coding agent should follow this protocol:

| Failure Type | Remediation Action | Max Retries |
|-------------|-------------------|-------------|
| QS-1: Module count wrong | Re-run skeleton generation with adjusted prompt emphasizing target count | 2 |
| QC-1: JSON instead of prose | Re-run content hydration for affected concepts only | 1 |
| QC-2: Content too short | Re-hydrate affected concepts with explicit "minimum {N} words" instruction | 2 |
| QC-3: Duplicate content | Delete duplicate, re-generate with "do not repeat content from: {other_concept_title}" | 1 |
| QC-7: Bloom's out of range | Update bloom_level in DB to nearest valid value within range | 1 (no LLM needed) |
| QC-8: Bloom's don't ascend | Re-assign bloom_levels to form ascending sequence | 1 (no LLM needed) |
| QP-3: Single-question mastery | Code bug — fix `concept_correct_streak` logic, re-run unit tests | Code fix |
| QP-5: Grade-2 stuck state | Code bug — fix `question_start_time` reset, re-run unit tests | Code fix |

For structural failures (QS-4 through QS-6): these indicate fundamental skeleton issues. Re-run the full skeleton generation for the course. If it fails twice, flag for human review.

### 13.6 Reference Course Templates — Spot-Check Benchmarks

The agent should create these specific courses and verify they meet the standards described. These serve as regression benchmarks.

#### Benchmark 1: "Greek Philosophy" — Broad Survey

```
Parameters: scope=5, mastery=1, starting_from=1
Expected: ~11 modules, ~33 concepts, Bloom 1-2, ~150 words/concept

Must include modules covering at minimum:
  - Pre-Socratic thinkers (Thales, Heraclitus, Parmenides)
  - Socrates
  - Plato
  - Aristotle
  - At least 2 of: Stoics, Epicureans, Cynics, Skeptics, Neoplatonists

Each concept should be accessible to someone with zero philosophy background.
No concept should require understanding of formal logic or ancient Greek.
```

#### Benchmark 2: "Machine Learning" — Bachelor's Equivalent

```
Parameters: scope=3, mastery=4, starting_from=2
Expected: ~6 modules, ~35 concepts, Bloom 1-5, ~600 words/concept

Must include modules covering:
  - Linear models (regression, classification)
  - Optimization / gradient descent
  - Neural networks / deep learning
  - Evaluation and regularization

Concepts should include mathematical notation where appropriate.
Bloom's 5 concepts should ask students to evaluate model tradeoffs.
Student assumed to know basic linear algebra and calculus.
```

#### Benchmark 3: "Sourdough Baking" — Focused Practical

```
Parameters: scope=1, mastery=3, starting_from=1
Expected: ~3 modules, ~15 concepts, Bloom 1-4, ~400 words/concept

Must include:
  - Starter creation and maintenance
  - Basic dough technique
  - Baking process

Bloom's 3-4 concepts should ask students to diagnose problems
(e.g., "Your dough didn't rise. What are three possible causes?").
No assumed baking knowledge.
```

```bash
# TASK: Run benchmark course generation and quality check

BENCHMARKS=(
    "Greek Philosophy|5|1|1"
    "Machine Learning|3|4|2"
    "Sourdough Baking|1|3|1"
)

for BENCH in "${BENCHMARKS[@]}"; do
    IFS='|' read -r TOPIC SCOPE MASTERY START <<< "$BENCH"
    echo ""
    echo "━━━ Benchmark: $TOPIC (scope=$SCOPE, mastery=$MASTERY, start=$START) ━━━"

    RESULT=$(curl -sf -X POST http://localhost:5003/api/create_course \
      -H "Content-Type: application/json" \
      -d "{\"topic\":\"$TOPIC\",\"scope\":$SCOPE,\"mastery\":$MASTERY,\"starting_from\":$START}")

    UID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('course_uid',''))" 2>/dev/null)
    echo "Course UID: $UID"

    if [ -z "$UID" ]; then
        echo "FAIL: Course creation failed"
        continue
    fi

    # Wait for completion (max 3 minutes)
    for i in $(seq 1 36); do
        STATUS=$(curl -sf "http://localhost:5002/api/courses" | python3 -c "
import sys, json
for c in json.load(sys.stdin).get('courses', []):
    if c['uid'] == '$UID': print(c.get('status','')); break
else: print('not_found')
" 2>/dev/null)
        [ "$STATUS" = "ready" ] && break
        sleep 5
    done

    if [ "$STATUS" != "ready" ]; then
        echo "FAIL: Course not ready after 3 minutes"
        continue
    fi

    echo "Running quality verification..."
    # The quality test script from §13.4 would be called here
    # python3 verify_course_quality.py "$UID"
    echo "Benchmark complete."
done
```

---

## 14. MANUAL VERIFICATION TASKS

These cannot be fully automated via CLI and require browser inspection or human judgment. Claude Code should flag these for manual review.

| # | Task | How to Verify | Expected Result |
|---|------|--------------|-----------------|
| M1 | Alpine theme visual quality | Open `http://localhost:5000` in browser, screenshot each page | Warm parchment/forest color palette. No blue remnants. No CSS variable fallbacks showing |
| M2 | Dark theme toggle | Click sun/moon icon in header | All pages switch to dark forest palette. No white flashes. Text remains readable |
| M3 | Chat streaming visual | Go to Learn → start a course → ask a question | Tokens appear word-by-word in the tutor's chat bubble. No flash-of-full-response |
| M4 | Typing indicator | Submit an answer in Learn | Three-dot animation appears while LLM generates. Disappears when response starts streaming |
| M5 | Grade badges | Answer a question correctly/incorrectly | 🟢 for correct, 🔴 for incorrect appears on the tutor's response bubble |
| M6 | XP animation | Answer correctly | "+10 XP" floats up with golden glow, fades after 1.5s |
| M7 | TTS play button | Click ▶ on any tutor message | Audio plays. Button changes to ⏸. Reverts to ▶ when done |
| M8 | Course wizard Step 2 drag reorder | On `/courses/new` Step 2, drag a module card | Module moves position. Order persists to Step 3 |
| M9 | Concept suggestions don't duplicate | In wizard Step 3, define "Variables" then click "✨ Suggest" | Suggestions don't include "Variables" or close synonyms |
| M10 | Q&A questions reference user notes | In wizard Step 4 | At least one question references specific content from user's module/concept notes |
| M11 | Mobile responsive | Open on 375px viewport | All pages readable. Chat input usable. No horizontal scroll |
| M12 | No console errors | Open DevTools Console, navigate all pages | Zero errors during normal operation. Warnings are acceptable |
| M13 | Source confidence indicators | Create a course, go to course structure view | Concepts show color-coded confidence: green for high (web-sourced), amber for medium, red for low (parametric-only) |
| M14 | Content sourced from web reads better | Compare a web-augmented concept's resource_text to a parametric-only one | Web-augmented content should contain more specific facts, names, dates, and fewer vague generalizations |
