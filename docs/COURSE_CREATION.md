# Course Creation Flow - Implementation Guide

**Last Updated:** January 21, 2026  
**Status:** ✅ PRODUCTION READY  
**System:** Helga Socratic Voice Tutor

---

## Overview

The course creation flow implements a 6-step process with comprehensive service management to handle KuzuDB's file-level locking constraints. This document serves as the primary reference for understanding, maintaining, and extending the course creation system.

---

## 6-Step Progress Flow

### Step 1: Prepare Database (0%)
**Duration:** 5-10 seconds  
**Actions:**
- Stop rag-engine service (primary lock holder)
- Stop night_audit service (secondary lock holder)
- Verify services are stopped
- Log pre-condition state

**Code Location:** [`services/core/service_manager.py:313-356`](services/core/service_manager.py:313-356)

### Step 2: Scraping ZIM Files (20%)
**Duration:** 30-120 seconds  
**Actions:**
- Run ingestion subprocess: `python tools/ingest.py --topic <topic>`
- Monitor stdout for "scraping" or "processing" keywords
- Track progress with timestamps
- Update UI with real-time status

**Code Location:** [`services/core/fsm_logic.py:814-916`](services/core/fsm_logic.py:814-916)

### Step 3: Vectorizing Content (40%)
**Duration:** 60-300 seconds  
**Actions:**
- Detect "embedding" or "vector" keywords in subprocess output
- Create semantic embeddings for all content
- Store vectors in database
- Track stage duration

### Step 4: Building Graph (60%)
**Duration:** 30-120 seconds  
**Actions:**
- Detect "inserting" or "merge" keywords in subprocess output
- Build knowledge graph relationships
- Create concept connections
- Organize hierarchical structure

### Step 5: Finalizing Course (80%)
**Duration:** 10-30 seconds  
**Actions:**
- Detect "complete" or "success" keywords
- Finalize database transactions
- Verify data integrity
- Prepare for service restart

### Step 6: Restarting Services (100%)
**Duration:** 10-20 seconds  
**Actions:**
- Start rag-engine service (15s timeout, 3 retries)
- Start night_audit service (10s timeout, 2 retries)
- Verify services are healthy via HTTP health checks
- Confirm database connectivity

**Code Location:** [`services/core/service_manager.py:358-394`](services/core/service_manager.py:358-394)

---

## Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                    Web UI (Browser)                         │
│  - 6-step progress modal                                    │
│  - Real-time status updates via Socket.IO                  │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              Web UI Service (Flask)                         │
│  - /api/event endpoint (receives course creation request)  │
│  - /api/update_thinking_status (broadcasts progress)       │
│  - Socket.IO for real-time updates                         │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│           Core Logic Service (FSM)                          │
│  - start_creation() - initiates course creation            │
│  - run_ingestion_with_service_management() - orchestrates  │
│  - run_ingestion() - monitors subprocess with timeouts     │
└────────────────────┬────────────────────────────────────────┘
                     │
         ┌───────────┼───────────┐
         ▼           ▼           ▼
     ┌────────┐  ┌──────────┐  ┌──────────┐
     │Service │  │Ingestion │  │Database  │
     │Manager │  │Subprocess│  │(KuzuDB) │
     └────────┘  └──────────┘  └──────────┘
```

### Key Files

| File | Purpose | Lines |
|------|---------|-------|
| [`services/core/fsm_logic.py`](services/core/fsm_logic.py) | Course creation orchestration | 737-916 |
| [`services/core/service_manager.py`](services/core/service_manager.py) | Service lifecycle management | 1-412 |
| [`services/web-ui/static/js/session.js`](services/web-ui/static/js/session.js) | Progress UI and Socket.IO | 163-256 |
| [`services/web-ui/templates/learn.html`](services/web-ui/templates/learn.html) | Progress modal HTML | 141-190 |
| [`services/web-ui/static/css/style.css`](services/web-ui/static/css/style.css) | Progress styling | Progress section |
| [`tests/post_clean_slate/test_create_course.py`](tests/post_clean_slate/test_create_course.py) | Integration tests | 1-485 |
| [`tests/post_ingestion/test_course_creation_e2e.py`](tests/post_ingestion/test_course_creation_e2e.py) | End-to-end integration test | 1-350+ |

---

## Timeout Handling

### Overall Ingestion Timeout
- **Duration:** 1 hour (3600 seconds)
- **Trigger:** If ingestion subprocess runs longer than 1 hour
- **Action:** Terminate process, restart services, report error

### Stall Timeout
- **Duration:** 5 minutes (300 seconds) without output
- **Trigger:** If subprocess produces no output for 5 minutes
- **Action:** Terminate process, restart services, report error

### Service Operation Timeouts

| Service | Operation | Timeout | Retries |
|---------|-----------|---------|---------|
| rag-engine | Stop | 5s | 2 |
| rag-engine | Start | 15s | 3 |
| rag-engine | Health Check | 30s | 5 |
| night_audit | Stop | 3s | 1 |
| night_audit | Start | 10s | 2 |
| night_audit | Health Check | 5s | 2 |

**Code Location:** [`services/core/service_manager.py:39-54`](services/core/service_manager.py:39-54)

---

## Database Lock Management

### Problem
KuzuDB uses **file-level locking** that prevents concurrent write access. When core-logic or rag-engine services have the database open, ingestion cannot acquire the write lock.

### Solution
1. **Stop services** before ingestion (releases locks)
2. **Run ingestion** against main database (exclusive access)
3. **Restart services** after ingestion (re-acquire locks)

### Database Paths
- **Main Database:** `/app/data/kuzu_db/db` (persistent)
- **Temporary Database:** `/tmp/kuzu_db_ingest` (fallback, cleaned up)

**Code Location:** [`services/core/fsm_logic.py:737-792`](services/core/fsm_logic.py:737-792)

---

## Sudo Password Handling

### Problem
Docker commands may require sudo if user is not in docker group.

### Solution
1. **Auto-detect** sudo requirement by attempting `docker ps`
2. **Cache password** to `/tmp/.helga_sudo_cache` (0600 permissions)
3. **Pass via environment** variable `HELGA_SUDO_PASSWORD`
4. **Clean up** after setup completes

### Implementation
```python
# Auto-detect sudo requirement
use_sudo = False
try:
    subprocess.run(['docker', 'ps'], capture_output=True, timeout=2, check=True)
except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
    use_sudo = True
    
# Check if password available
sudo_password = os.getenv('HELGA_SUDO_PASSWORD')
if not sudo_password:
    raise Error("Docker requires sudo but no password provided")
```

**Code Location:** [`services/core/fsm_logic.py:740-757`](services/core/fsm_logic.py:740-757)

---

## Error Handling & Recovery

### Service Stop Failure
```
Scenario: rag-engine fails to stop
├─ Attempt 1: 5s timeout → Fail
├─ Wait 1s (exponential backoff)
├─ Attempt 2: 5s timeout → Fail
└─ Log error, continue with ingestion (may fail due to lock)
```

### Service Start Failure
```
Scenario: rag-engine fails to start after ingestion
├─ Attempt 1: 15s timeout → Fail
├─ Wait 1s (exponential backoff)
├─ Attempt 2: 15s timeout → Fail
├─ Wait 2s (exponential backoff)
├─ Attempt 3: 15s timeout → Fail
└─ Report error to user, services remain stopped
```

### Ingestion Subprocess Failure
```
Scenario: Ingestion subprocess crashes
├─ Detect process exit with non-zero code
├─ Attempt to restart services
├─ Report error to user
└─ User can retry course creation
```

### Automatic Recovery
```
Scenario: Any error during ingestion
├─ Catch exception in run_ingestion_with_service_management()
├─ Attempt to restart services (even on failure)
├─ Report error to user
└─ Services restored to running state
```

**Code Location:** [`services/core/fsm_logic.py:802-813`](services/core/fsm_logic.py:802-813)

---

## Web UI Progress Visualization

### 6-Step Progress Modal

```
┌─────────────────────────────────────────┐
│  Creating Course: Python Basics         │
├─────────────────────────────────────────┤
│                                         │
│  📥 Prepare Database      [████░░░░░░]  │
│  📥 Scraping ZIM Files    [████░░░░░░]  │
│  🔢 Vectorizing Content   [░░░░░░░░░░]  │
│  🏗️  Building Graph        [░░░░░░░░░░]  │
│  ✨ Finalizing Course     [░░░░░░░░░░]  │
│  🚀 Restarting Services   [░░░░░░░░░░]  │
│                                         │
│  Progress: 20%                          │
│  Status: Downloading educational...     │
│                                         │
│  Activity Log:                          │
│  > Stopping rag-engine...               │
│  > Stopping night_audit...              │
│  > Starting ingestion subprocess...     │
│                                         │
└─────────────────────────────────────────┘
```

### UI Components

| Component | File | Purpose |
|-----------|------|---------|
| Modal Container | [`learn.html:141-190`](services/web-ui/templates/learn.html:141-190) | Progress modal HTML |
| Progress Bar | [`style.css`](services/web-ui/static/css/style.css) | Animated progress bar |
| Step Indicators | [`session.js:224-245`](services/web-ui/static/js/session.js:224-245) | 6 step icons with states |
| Activity Log | [`session.js:247-256`](services/web-ui/static/js/session.js:247-256) | Real-time log entries |
| Socket.IO Updates | [`session.js:163-222`](services/web-ui/static/js/session.js:163-222) | Progress updates |

### Progress Step States

```javascript
const progressSteps = {
    'Preparing database': { step: 'prepare', percent: 0, icon: '📥' },
    'Scraping ZIM files': { step: 'scrape', percent: 20, icon: '📥' },
    'Vectorizing content': { step: 'vectorize', percent: 40, icon: '🔢' },
    'Building graph': { step: 'graph', percent: 60, icon: '🏗️' },
    'Finalizing course': { step: 'finalize', percent: 80, icon: '✨' },
    'Restarting services': { step: 'restart', percent: 100, icon: '🚀' }
};
```

**Code Location:** [`services/web-ui/static/js/session.js:15-22`](services/web-ui/static/js/session.js:15-22)

---

## Testing

### Test Coverage

| Test | File | Purpose |
|------|------|---------|
| Service Management Integration | [`test_create_course.py:30-61`](tests/post_clean_slate/test_create_course.py:30-61) | Verify ServiceManager exists and has required methods |
| Database Path Configuration | [`test_create_course.py:63-86`](tests/post_clean_slate/test_create_course.py:63-86) | Verify database paths are correct |
| Service Stop Verification | [`test_create_course.py:88-123`](tests/post_clean_slate/test_create_course.py:88-123) | Mock test of service stop sequence |
| Service Restart Verification | [`test_create_course.py:125-165`](tests/post_clean_slate/test_create_course.py:125-165) | Mock test of service restart sequence |
| Main Database Ingestion | [`test_create_course.py:167-200`](tests/post_clean_slate/test_create_course.py:167-200) | Verify data stored in main database |
| Temporary Database Cleanup | [`test_create_course.py:202-217`](tests/post_clean_slate/test_create_course.py:202-217) | Verify temp database cleaned up |
| Health Checks | [`test_create_course.py:416-444`](tests/post_clean_slate/test_create_course.py:416-444) | Verify services are healthy |
| Course Structure | [`test_create_course.py:321-414`](tests/post_clean_slate/test_create_course.py:321-414) | Verify course structure is valid |
| End-to-End Integration | [`test_course_creation_e2e.py`](tests/post_ingestion/test_course_creation_e2e.py) | Full flow with running services |

### Running Tests

```bash
# Run all course creation tests
python -m pytest tests/post_clean_slate/test_create_course.py -v

# Run end-to-end integration test
python -m pytest tests/post_ingestion/test_course_creation_e2e.py -v

# Run specific test
pytest tests/post_clean_slate/test_create_course.py::test_service_management_integration -v

# Run with coverage
pytest tests/post_clean_slate/test_create_course.py --cov=services/core/service_manager --cov=services/core/fsm_logic
```

---

## Logging

### Log Files

| Service | Log File | Purpose |
|---------|----------|---------|
| Core Logic | `/app/data/logs/core.log` | FSM state transitions, course creation |
| Service Manager | `/app/data/logs/service_manager.log` | Service stop/start operations |
| Ingestion | `/app/data/logs/ingest.log` | ZIM file processing, vectorization |
| Input STT | `/app/data/logs/input-stt.log` | Speech-to-text operations |
| RAG Engine | `/app/data/logs/rag.log` | Knowledge graph queries |

### Log Levels

- **INFO:** Normal operations, state transitions, progress updates
- **WARNING:** Recoverable errors, degraded health, retries
- **ERROR:** Critical failures, service crashes, ingestion failures

### Example Log Output

```
2026-01-21 03:20:15 [INFO] Starting course creation for topic: Python Basics
2026-01-21 03:20:15 [INFO] Starting pre-ingestion service stop sequence
2026-01-21 03:20:15 [INFO] Attempting to stop rag-engine (primary lock holder)
2026-01-21 03:20:16 [INFO] Service rag-engine stopped successfully
2026-01-21 03:20:16 [INFO] Attempting to stop night_audit (secondary lock holder)
2026-01-21 03:20:17 [INFO] Pre-ingestion service stop sequence completed successfully
2026-01-21 03:20:17 [INFO] Services stopped successfully, starting ingestion
2026-01-21 03:20:17 [INFO] Running ingestion via temporary container (with --no-deps)...
2026-01-21 03:20:45 [INFO] INGESTION_STAGE: scraping started at 1705816845.123
2026-01-21 03:21:15 [INFO] INGESTION_STAGE: scraping completed in 30.0s, merging started
2026-01-21 03:21:45 [INFO] INGESTION_STAGE: merging completed in 30.0s, embedding started
2026-01-21 03:22:45 [INFO] INGESTION_STAGE: embedding completed in 60.0s
2026-01-21 03:22:45 [INFO] INGESTION_COMPLETE: total_duration=120.0s | stages={'scraping': True, 'merging': True, 'embedding': True, 'complete': True}
2026-01-21 03:22:45 [INFO] Ingestion process completed successfully for topic: Python Basics
2026-01-21 03:22:45 [INFO] Starting post-ingestion service restart sequence
2026-01-21 03:22:50 [INFO] Service rag-engine started successfully
2026-01-21 03:22:50 [INFO] Health checking service: rag-engine at http://localhost:5002/health
2026-01-21 03:22:51 [INFO] Service rag-engine health check passed
2026-01-21 03:22:51 [INFO] Post-ingestion service restart sequence completed successfully
2026-01-21 03:22:51 [INFO] Course creation completed successfully for topic: Python Basics
```

---

## Performance Metrics

### Typical Course Creation Timeline

| Stage | Duration | Notes |
|-------|----------|-------|
| Prepare Database | 5-10s | Stop services, verify stopped |
| Scrape ZIM Files | 30-120s | Depends on file size (3000 articles/sec) |
| Vectorizing Content | 60-300s | Depends on content size |
| Building Graph | 30-120s | Create relationships |
| Finalizing Course | 10-30s | Verify integrity |
| Restarting Services | 10-20s | Start services, health checks |
| **Total** | **145-680s** | **~2.5-11 minutes** |

### Resource Usage

- **CPU:** 80-100% during vectorization
- **Memory:** 256MB buffer pool (KuzuDB limit)
- **Disk I/O:** High during scraping and graph building
- **Network:** Minimal (local operations)

---

## Troubleshooting

### Issue: Course Creation Hangs

**Symptoms:** Progress modal shows but doesn't advance

**Causes:**
1. Services failed to stop (database still locked)
2. Ingestion subprocess crashed silently
3. Stall timeout triggered (5 minutes without output)

**Solutions:**
1. Check logs: `docker logs helga-soctraic-voice-tutor-core-logic-1`
2. Verify services: `docker compose ps`
3. Restart services: `docker compose restart rag-engine core-logic`
4. Retry course creation

### Issue: Services Don't Restart

**Symptoms:** Progress reaches 100% but services don't come back online

**Causes:**
1. Service start command failed
2. Health check failed
3. Port already in use

**Solutions:**
1. Check service logs: `docker logs helga-soctraic-voice-tutor-rag-engine-1`
2. Verify ports: `netstat -tlnp | grep 5002`
3. Manually restart: `docker compose up -d rag-engine core-logic`

### Issue: Database Lock Error

**Symptoms:** Ingestion fails with "Could not set lock on file"

**Causes:**
1. Services didn't stop properly
2. Another process holding lock
3. Database file corrupted

**Solutions:**
1. Stop all services: `docker compose stop`
2. Check for stray processes: `lsof /app/data/kuzu_db/db`
3. Rebuild database: `python clean_slate.py`

---

## Future Improvements

### Short Term
- [ ] Add progress persistence (resume interrupted ingestions)
- [ ] Implement database transaction rollback on failure
- [ ] Add progress estimation based on file size

### Medium Term
- [ ] Parallel ingestion for multiple courses
- [ ] Incremental ingestion (add to existing course)
- [ ] Course versioning and rollback

### Long Term
- [ ] Distributed ingestion across multiple workers
- [ ] Real-time ingestion without service stop
- [ ] Streaming progress updates with WebSocket

---

## References

- **KuzuDB Documentation:** https://kuzudb.com/docs/
- **Docker Compose:** https://docs.docker.com/compose/
- **Flask-SocketIO:** https://flask-socketio.readthedocs.io/
- **Project README:** [`README.md`](README.md)
- **Development Log:** [`DEVLOG.md`](DEVLOG.md)
- **Architecture Design:** [`Coding Architecture.md`](Coding Architecture.md)

---

## Verification Checklist

- [x] Service Manager implemented with stop/start/health check
- [x] Course creation flow orchestrates service management
- [x] Timeout handling for overall ingestion (1 hour) and stalls (5 minutes)
- [x] Web UI shows 6-step progress with real-time updates
- [x] Tests verify service management integration
- [x] Error handling with automatic service recovery
- [x] Sudo password caching for docker commands
- [x] Comprehensive logging across all stages
- [x] Documentation complete
- [x] End-to-end integration test implemented

---

**Status:** ✅ READY FOR PRODUCTION

All components are implemented, tested, and documented. The course creation flow is production-ready with comprehensive error handling and recovery mechanisms.
