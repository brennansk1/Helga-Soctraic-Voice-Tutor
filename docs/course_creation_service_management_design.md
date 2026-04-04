# Course Creation Flow with Service Management Design

**Document Version:** 1.0  
**Date:** January 21, 2026  
**Status:** Design Phase (Ready for Implementation)

---

## Executive Summary

This document outlines a new course creation flow that implements proper service lifecycle management to resolve KuzuDB file locking issues during ingestion. The current implementation uses a fallback temporary database that isolates ingested data, preventing it from being accessible to the RAG service. The new flow will stop the RAG service before ingestion, ensuring atomic database operations and data consistency.

---

## 1. Current Flow Analysis

### 1.1 Current Architecture

```
User Request (Web UI)
    ↓
Web-UI Service (port 5000)
    ↓
Core Service (port 5003) - fsm_logic.py
    ↓
Subprocess: tools/ingest.py
    ↓
KuzuDB (data/kuzu_db/db)
    ↑
RAG Service (port 5002) - librarian.py [CONCURRENT ACCESS]
```

### 1.2 Current Implementation Details

**File:** [`services/core/fsm_logic.py`](services/core/fsm_logic.py:726-780)

```python
def start_creation(self, text):
    # Line 734: Spawns async ingestion thread
    threading.Thread(target=self.run_ingestion, args=(topic,)).start()

def run_ingestion(self, topic):
    # Line 738-743: Subprocess call to ingest.py
    process = subprocess.Popen(
        ["python", "tools/ingest.py", "--topic", topic],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
```

**File:** [`tools/ingest.py`](tools/ingest.py:65-101)

```python
# Lines 68-72: Always uses temporary database
use_temp_db = True  # Always use temp database during ingestion to avoid locks

if use_temp_db:
    db_dir = "/tmp/kuzu_db_ingest"
    logger.info("Using temporary database for ingestion to avoid lock conflicts")
```

### 1.3 Identified Problems

| Problem | Impact | Severity |
|---------|--------|----------|
| **Data Isolation** | Ingested courses stored in `/tmp/kuzu_db_ingest`, not accessible to RAG service | 🔴 Critical |
| **No Atomic Operations** | Failed ingestions leave partial data in temporary database | 🔴 Critical |
| **Race Conditions** | Multiple concurrent ingestions could conflict in `/tmp` | 🟠 High |
| **Incomplete Solution** | DEVLOG marks as "PARTIALLY FIXED" and "NEEDS TESTING" | 🟠 High |
| **Database Lock Scope** | KuzuDB file-level locking prevents concurrent write access | 🟠 High |

### 1.4 Database Lock Details

**Lock Type:** File-level exclusive lock (KuzuDB behavior)

**Affected Operations:**
- ✅ Read operations (RAG service queries) - NOT blocked
- ❌ Write operations (MERGE, INSERT, UPDATE) - BLOCKED when another process has DB open
- ❌ Schema initialization - BLOCKED

**Lock Holders:**
1. **RAG Service** (`services/rag/librarian.py` line 69-73)
   - Opens database at startup
   - Maintains persistent connection
   - Holds read/write connections

2. **Ingest Script** (`tools/ingest.py` line 88-101)
   - Attempts to open same database
   - Fails with "Could not set lock on file" error
   - Falls back to `/tmp/kuzu_db_ingest`

---

## 2. Identified Database Locking Issues

### 2.1 Lock Conflict Scenario

```
Timeline:
T0: RAG Service starts → Opens data/kuzu_db/db (acquires lock)
T1: User requests course creation
T2: Core Service spawns ingest.py subprocess
T3: ingest.py attempts to open data/kuzu_db/db
T4: ❌ LOCK CONFLICT - "Could not set lock on file"
T5: ingest.py falls back to /tmp/kuzu_db_ingest
T6: Ingestion proceeds in isolated temporary database
T7: ❌ Data never reaches main database
T8: RAG Service cannot see ingested courses
```

### 2.2 Why Fallback Doesn't Work

**Current Fallback Code** (`tools/ingest.py` lines 88-101):

```python
try:
    db = kuzu.Database(db_path, buffer_pool_size=128 * 1024 * 1024)
    conn = kuzu.Connection(db)
except (PermissionError, RuntimeError) as e:
    error_str = str(e).lower()
    if "permission denied" in error_str or "lock" in error_str:
        logger.warning(f"Cannot access {db_path} (lock or permission issue), using temporary database")
        db_dir = "/tmp/kuzu_db_ingest"
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "db")
        db = kuzu.Database(db_path, buffer_pool_size=128 * 1024 * 1024)
        conn = kuzu.Connection(db)
```

**Problems:**
1. Data written to `/tmp/kuzu_db_ingest` is isolated
2. No mechanism to merge `/tmp` data back to main database
3. RAG service never sees the ingested courses
4. Next ingestion creates new `/tmp` database, losing previous data

### 2.3 Services That Need Management

| Service | Port | Database Access | Lock Holder | Action Needed |
|---------|------|-----------------|-------------|---------------|
| **rag-engine** | 5002 | Read/Write | ✅ Yes | **STOP** |
| **core-logic** | 5003 | Read | ❌ No | Keep running |
| **web-ui** | 5000 | None | ❌ No | Keep running |
| **qwen-engine** | 8080 | None | ❌ No | Keep running |
| **input-node** | 5004 | None | ❌ No | Keep running |
| **tts-engine** | 5005 | None | ❌ No | Keep running |
| **night_audit** | N/A | Read | ⚠️ Maybe | **STOP** (if running) |

---

## 3. New Proposed Flow with Service Management

### 3.1 High-Level Flow Diagram

```
User Request: "Create course on [topic]"
    ↓
[1] Web-UI sends request to Core Service
    ↓
[2] Core Service initiates creation sequence
    ↓
[3] ⏹️ STOP rag-engine service (release database lock)
    ↓
[4] ⏹️ STOP night_audit service (if running)
    ↓
[5] ✅ Verify services stopped (health check)
    ↓
[6] 🔄 Run ingest.py against main database (data/kuzu_db/db)
    ↓
[7] 📊 Monitor ingestion progress
    ↓
[8] ✅ Verify ingestion success
    ↓
[9] 🚀 START rag-engine service
    ↓
[10] 🚀 START night_audit service
    ↓
[11] ✅ Verify services healthy
    ↓
[12] ✅ Notify user: "Course ready!"
```

### 3.2 Detailed Service Stop/Restart Sequence

#### Phase 1: Pre-Ingestion (Stop Services)

**Step 1.1: Stop RAG Engine**
```bash
docker compose stop rag-engine
```
- Closes database connections
- Releases file lock on data/kuzu_db/db
- Timeout: 10 seconds
- Retry: 3 attempts

**Step 1.2: Stop Night Audit (if running)**
```bash
docker compose stop night_audit
```
- Prevents concurrent database access
- Timeout: 5 seconds
- Retry: 2 attempts

**Step 1.3: Verify Services Stopped**
```bash
# Check that services are not running
docker compose ps | grep -E "rag-engine|night_audit"
# Should return empty or "Exit" status
```
- Timeout: 5 seconds
- Retry: 3 attempts with 1-second delay

#### Phase 2: Ingestion (Main Operation)

**Step 2.1: Modify ingest.py Configuration**
- Set `use_temp_db = False` (use main database)
- Connect to `data/kuzu_db/db` directly
- No fallback to `/tmp` database

**Step 2.2: Run Ingestion**
```bash
python tools/ingest.py --topic "[topic]"
```
- Timeout: 3600 seconds (1 hour)
- Progress monitoring: Parse stdout for keywords
- Error handling: Capture stderr

**Step 2.3: Monitor Progress**
- Parse output for keywords:
  - "Scraping ZIM files..." → 25% progress
  - "Vectorizing content..." → 50% progress
  - "Building graph..." → 75% progress
  - "Finalizing course..." → 100% progress
- Send real-time updates to Web-UI via Socket.IO

#### Phase 3: Post-Ingestion (Restart Services)

**Step 3.1: Verify Ingestion Success**
```python
if process.returncode == 0:
    # Success
else:
    # Failure - log error and proceed to restart
```

**Step 3.2: Start RAG Engine**
```bash
docker compose start rag-engine
```
- Timeout: 15 seconds
- Retry: 3 attempts

**Step 3.3: Start Night Audit**
```bash
docker compose start night_audit
```
- Timeout: 10 seconds
- Retry: 2 attempts

**Step 3.4: Verify Services Healthy**
```bash
# Health check RAG service
curl http://localhost:5002/health
# Expected: {"status": "healthy", "db_status": "connected"}
```
- Timeout: 30 seconds
- Retry: 5 attempts with 2-second delay

**Step 3.5: Verify Data Accessibility**
```bash
# Query RAG service for newly ingested course
curl "http://localhost:5002/api/courses"
# Should include new course in response
```
- Timeout: 10 seconds
- Retry: 3 attempts

### 3.3 Service Management Implementation

**New File:** `services/core/service_manager.py`

```python
class ServiceManager:
    """Manages Docker Compose services for course creation."""
    
    def __init__(self, compose_cmd=['docker', 'compose']):
        self.compose_cmd = compose_cmd
        self.logger = logging.getLogger(__name__)
    
    def stop_service(self, service_name, timeout=10, retries=3):
        """Stop a Docker Compose service with retry logic."""
        # Implementation details in code phase
    
    def start_service(self, service_name, timeout=15, retries=3):
        """Start a Docker Compose service with retry logic."""
        # Implementation details in code phase
    
    def verify_service_stopped(self, service_name, timeout=5, retries=3):
        """Verify a service is stopped."""
        # Implementation details in code phase
    
    def verify_service_healthy(self, service_name, health_url, timeout=30, retries=5):
        """Verify a service is healthy via health check endpoint."""
        # Implementation details in code phase
    
    def stop_for_ingestion(self):
        """Stop all services that hold database locks."""
        # Stop rag-engine
        # Stop night_audit
        # Verify both stopped
    
    def restart_after_ingestion(self):
        """Restart services after ingestion completes."""
        # Start rag-engine
        # Start night_audit
        # Verify both healthy
```

**Modified File:** `services/core/fsm_logic.py`

```python
def start_creation(self, text):
    """Initiate course creation with service management."""
    topic = text.replace("create course on", "").replace("create", "").strip()
    if not topic:
        self.speak("What topic should I research?")
        return
    
    self.send_status_update(f"Creating course on {topic}")
    self.speak(f"I am researching {topic}. This may take a moment.")
    
    # Async ingestion with service management
    threading.Thread(
        target=self.run_ingestion_with_service_management,
        args=(topic,)
    ).start()

def run_ingestion_with_service_management(self, topic):
    """Run ingestion with proper service lifecycle management."""
    service_mgr = ServiceManager()
    
    try:
        # Phase 1: Stop services
        self.send_status_update("Preparing database...")
        service_mgr.stop_for_ingestion()
        
        # Phase 2: Run ingestion
        self.send_status_update("Scraping ZIM files...")
        success = self.run_ingestion(topic)
        
        # Phase 3: Restart services
        self.send_status_update("Restarting services...")
        service_mgr.restart_after_ingestion()
        
        if success:
            self.send_status_update("Course ready to start!")
            self.speak(f"Course on {topic} is ready. Say 'start course {topic}' to begin.")
        else:
            self.send_status_update("Ingestion failed.")
            self.speak("Failed to create the course. Please try again.")
    
    except Exception as e:
        logging.error(f"Ingestion with service management failed: {e}")
        self.send_status_update("Ingestion error.")
        self.speak("An error occurred while creating the course.")
        # Attempt to restart services even on failure
        try:
            service_mgr.restart_after_ingestion()
        except:
            pass
```

**Modified File:** `tools/ingest.py`

```python
# Lines 68-72: Remove fallback logic
use_temp_db = False  # Use main database directly

db_dir = "data/kuzu_db"
if os.path.exists(db_dir):
    if not os.path.isdir(db_dir):
        logger.info(f"Removing file {db_dir} and creating directory...")
        try:
            os.remove(db_dir)
        except PermissionError:
            logger.error(f"Cannot remove {db_dir}")
            sys.exit(1)

os.makedirs(db_dir, exist_ok=True)
db_path = os.path.join(db_dir, "db")

# Direct connection without fallback
try:
    db = kuzu.Database(db_path, buffer_pool_size=128 * 1024 * 1024)
    conn = kuzu.Connection(db)
except Exception as e:
    logger.error(f"Failed to connect to database: {e}")
    logger.error("Ensure RAG service is stopped before running ingestion.")
    sys.exit(1)
```

---

## 4. Impact Analysis

### 4.1 Tests Affected

| Test File | Impact | Action Required |
|-----------|--------|-----------------|
| `tests/post_ingestion/test_db_ingestion.py` | ✅ Improved | Update to verify data in main DB, not `/tmp` |
| `tests/post_ingestion/test_end_to_end.py` | ✅ Improved | Add service management verification |
| `tests/post_clean_slate/test_ingest_logic.py` | ⚠️ Modified | Update to handle service stop/start |
| `test_create_course.py` | ⚠️ Modified | Add service health checks |
| `tests/post_ingestion/test_rag.py` | ✅ Improved | Verify ingested data is accessible |

**Test Updates Needed:**

1. **test_db_ingestion.py**
   - Verify ingested data is in `data/kuzu_db/db`, not `/tmp`
   - Query RAG service to confirm data accessibility
   - Add timeout handling for service restarts

2. **test_end_to_end.py**
   - Add service state verification before/after ingestion
   - Verify RAG service health after restart
   - Test concurrent ingestion prevention

3. **test_create_course.py**
   - Add health check for RAG service
   - Verify course appears in `/api/courses` after creation
   - Add timeout for service restart operations

### 4.2 UI Updates Required

| Component | Change | Reason |
|-----------|--------|--------|
| **Progress Modal** | Add "Preparing database..." step | Show service stop phase |
| **Progress Modal** | Add "Restarting services..." step | Show service restart phase |
| **Status Messages** | Update timing expectations | Service stop/restart adds ~5-10 seconds |
| **Error Handling** | Add service restart failure messages | Inform user if services don't restart |
| **Concurrent Creation** | Prevent multiple simultaneous creations | Only one ingestion at a time |

**File:** `services/web-ui/templates/learn.html`

```html
<!-- Update progress steps from 4 to 6 -->
<div class="progress-step" id="step-prepare">
  📥 Preparing Database (0%)
</div>
<div class="progress-step" id="step-scrape">
  📥 Scraping ZIM Files (20%)
</div>
<div class="progress-step" id="step-vectorize">
  🔢 Vectorizing Content (40%)
</div>
<div class="progress-step" id="step-graph">
  🏗️ Building Knowledge Graph (60%)
</div>
<div class="progress-step" id="step-finalize">
  ✨ Finalizing Course (80%)
</div>
<div class="progress-step" id="step-restart">
  🚀 Restarting Services (100%)
</div>
```

**File:** `services/web-ui/static/js/session.js`

```javascript
// Update progress step mapping
const progressSteps = {
  'Preparing database': { step: 'prepare', percent: 0 },
  'Scraping ZIM files': { step: 'scrape', percent: 20 },
  'Vectorizing content': { step: 'vectorize', percent: 40 },
  'Building graph': { step: 'graph', percent: 60 },
  'Finalizing course': { step: 'finalize', percent: 80 },
  'Restarting services': { step: 'restart', percent: 100 }
};

// Add concurrent creation prevention
let isCreatingCourse = false;

function startCourseCreation(topic) {
  if (isCreatingCourse) {
    showToast('Course creation already in progress. Please wait.', 'warning');
    return;
  }
  isCreatingCourse = true;
  // ... rest of creation logic
}
```

### 4.3 User Experience Changes

**Before (Current):**
- User creates course
- Ingestion runs in background
- Data isolated in `/tmp`
- Course not accessible
- User confused

**After (New):**
- User creates course
- "Preparing database..." (services stop)
- "Scraping ZIM files..." (ingestion starts)
- Progress updates in real-time
- "Restarting services..." (services restart)
- Course immediately accessible
- User satisfied

**Timing:**
- Service stop: ~2-3 seconds
- Ingestion: ~5-30 minutes (depends on ZIM file size)
- Service restart: ~3-5 seconds
- **Total overhead:** ~5-8 seconds (acceptable)

---

## 5. Error Handling & Recovery

### 5.1 Failure Scenarios

| Scenario | Handling | Recovery |
|----------|----------|----------|
| **Service stop timeout** | Log error, continue anyway | Ingestion may fail with lock error |
| **Ingestion failure** | Capture stderr, log error | Restart services, notify user |
| **Service restart timeout** | Log error, alert user | Manual restart required |
| **Data verification failure** | Query RAG service | Retry verification up to 3 times |
| **Concurrent creation attempt** | Reject with message | User must wait for current creation |

### 5.2 Rollback Strategy

If ingestion fails after services are stopped:

```python
def handle_ingestion_failure(self, error):
    """Handle ingestion failure and restore system state."""
    logging.error(f"Ingestion failed: {error}")
    
    # Always attempt to restart services
    try:
        service_mgr.restart_after_ingestion()
    except Exception as restart_error:
        logging.error(f"Failed to restart services: {restart_error}")
        self.speak("Critical error: Services failed to restart. Manual intervention required.")
        return False
    
    # Notify user
    self.speak("Failed to create the course. Please try again.")
    return True
```

### 5.3 Monitoring & Logging

**New Log File:** `data/logs/ingestion_service_management.log`

```json
{
  "timestamp": "2026-01-21T02:35:00Z",
  "event": "ingestion_start",
  "topic": "quantum_mechanics",
  "phase": "pre_ingestion"
}
{
  "timestamp": "2026-01-21T02:35:02Z",
  "event": "service_stop",
  "service": "rag-engine",
  "duration_seconds": 2.1,
  "status": "success"
}
{
  "timestamp": "2026-01-21T02:35:04Z",
  "event": "service_stop",
  "service": "night_audit",
  "duration_seconds": 1.8,
  "status": "success"
}
{
  "timestamp": "2026-01-21T02:35:05Z",
  "event": "ingestion_start",
  "phase": "ingestion",
  "database": "data/kuzu_db/db"
}
{
  "timestamp": "2026-01-21T02:40:00Z",
  "event": "ingestion_complete",
  "duration_seconds": 295,
  "articles_ingested": 1250,
  "status": "success"
}
{
  "timestamp": "2026-01-21T02:40:03Z",
  "event": "service_start",
  "service": "rag-engine",
  "duration_seconds": 3.2,
  "status": "success"
}
{
  "timestamp": "2026-01-21T02:40:05Z",
  "event": "data_verification",
  "query": "courses",
  "courses_found": 1,
  "status": "success"
}
{
  "timestamp": "2026-01-21T02:40:05Z",
  "event": "ingestion_complete",
  "total_duration_seconds": 305,
  "status": "success"
}
```

---

## 6. Implementation Roadmap

### Phase 1: Service Manager Module (Week 1)
- [ ] Create `services/core/service_manager.py`
- [ ] Implement service stop/start logic
- [ ] Add health check verification
- [ ] Add retry logic with exponential backoff
- [ ] Add comprehensive logging

### Phase 2: Core Service Integration (Week 1)
- [ ] Modify `services/core/fsm_logic.py`
- [ ] Integrate ServiceManager into course creation
- [ ] Add error handling and recovery
- [ ] Add progress status updates

### Phase 3: Ingest Script Updates (Week 1)
- [ ] Remove fallback to `/tmp` database
- [ ] Add error handling for locked database
- [ ] Add clear error messages
- [ ] Add logging for service management context

### Phase 4: UI Updates (Week 2)
- [ ] Update progress modal with new steps
- [ ] Update progress bar timing
- [ ] Add concurrent creation prevention
- [ ] Update status messages

### Phase 5: Testing (Week 2)
- [ ] Update existing tests
- [ ] Add service management tests
- [ ] Add integration tests
- [ ] Add failure scenario tests
- [ ] Performance testing

### Phase 6: Documentation (Week 2)
- [ ] Update DEVLOG.md
- [ ] Update README.md
- [ ] Add troubleshooting guide
- [ ] Add architecture diagrams

---

## 7. Success Criteria

### 7.1 Functional Requirements
- ✅ Ingested data is stored in main database (`data/kuzu_db/db`)
- ✅ Ingested courses are immediately accessible via RAG service
- ✅ No data isolation or loss
- ✅ Atomic ingestion (all-or-nothing)
- ✅ Service restart is automatic and reliable
- ✅ User receives real-time progress updates

### 7.2 Non-Functional Requirements
- ✅ Service stop/restart overhead < 10 seconds
- ✅ Ingestion success rate > 99%
- ✅ Service restart success rate > 99%
- ✅ Error recovery time < 30 seconds
- ✅ Comprehensive logging for debugging

### 7.3 Testing Requirements
- ✅ All existing tests pass
- ✅ New service management tests pass
- ✅ Integration tests pass
- ✅ Failure scenario tests pass
- ✅ Performance tests pass

---

## 8. Risks & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| **Service restart fails** | Medium | High | Implement retry logic, manual restart instructions |
| **Data corruption during ingestion** | Low | Critical | Atomic transactions, backup before ingestion |
| **User confusion about timing** | Medium | Low | Clear UI messaging, progress updates |
| **Concurrent ingestion attempts** | Medium | Medium | Prevent multiple simultaneous creations |
| **Service dependency issues** | Low | High | Health checks, dependency verification |

---

## 9. Conclusion

This design document provides a comprehensive approach to resolving KuzuDB file locking issues during course creation by implementing proper service lifecycle management. The new flow ensures:

1. **Data Consistency:** Ingested data is stored in the main database, not isolated in `/tmp`
2. **Atomic Operations:** All-or-nothing ingestion with proper error handling
3. **User Experience:** Real-time progress updates and immediate course accessibility
4. **System Reliability:** Automatic service restart with retry logic and health verification
5. **Maintainability:** Clear logging, error handling, and recovery procedures

The implementation is straightforward, with minimal changes to existing code and maximum benefit to system reliability and user experience.

---

## Appendix A: Service Dependencies

```
web-ui (5000)
  ├─ depends_on: core-logic
  └─ no database access

core-logic (5003)
  ├─ depends_on: rag-engine, qwen-engine
  ├─ database: READ ONLY
  └─ spawns: ingest.py (during course creation)

rag-engine (5002)
  ├─ database: READ/WRITE (LOCK HOLDER)
  └─ no dependencies

qwen-engine (8080)
   ├─ no database access
  └─ no dependencies

input-node (5004)
  ├─ no database access
  └─ no dependencies

tts-engine (5005)
  ├─ no database access
  └─ no dependencies

night_audit
  ├─ depends_on: rag-engine, qwen-engine
  ├─ database: READ ONLY (LOCK HOLDER)
  └─ runs periodically
```

---

## Appendix B: Configuration Changes

**File:** `docker-compose.yml` (No changes needed)

The service management is handled at the application level via `docker compose` CLI commands, not through compose file modifications.

**Environment Variables (Optional):**

```bash
# Add to .env for customization
SERVICE_STOP_TIMEOUT=10
SERVICE_START_TIMEOUT=15
SERVICE_HEALTH_CHECK_TIMEOUT=30
SERVICE_HEALTH_CHECK_RETRIES=5
INGESTION_TIMEOUT=3600
```

---

**Document End**
