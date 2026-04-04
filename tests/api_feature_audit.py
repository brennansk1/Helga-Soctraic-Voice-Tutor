import urllib.request
import urllib.error
import json
import time

BASE_URL = "http://localhost:5006"

def log(msg, status="INFO"):
    print(f"[{status}] {msg}")

def check_endpoint(url, method="GET", expected_code=200, required_keys=None):
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=5) as response:
            status_code = response.getcode()
            if status_code != expected_code:
                log(f"Failed {url}: Expected {expected_code}, got {status_code}", "FAIL")
                return None
            
            data = json.loads(response.read().decode('utf-8'))
            if required_keys:
                missing = [k for k in required_keys if k not in data]
                if missing:
                    log(f"Failed {url}: Missing keys {missing}", "FAIL")
                    return None
            
            log(f"Verified {url}", "PASS")
            return data
    except urllib.error.HTTPError as e:
        log(f"Failed {url}: HTTP {e.code}", "FAIL")
        return None
    except urllib.error.URLError as e:
        log(f"Error checking {url}: {e.reason}", "FAIL")
        return None
    except Exception as e:
        log(f"Error checking {url}: {e}", "FAIL")
        return None

def main():
    print("Starting API Feature Audit (urllib)...")
    
    # 1. Home Tab - Stats
    print("\n--- Phase 1: Home Tab ---")
    stats = check_endpoint(f"{BASE_URL}/api/stats", required_keys=['courses', 'concepts'])
    if stats:
        log(f"Stats: {stats}", "INFO")
    
    # 2. Courses Tab - List
    # Hitting RAG directly since web-ui proxy might not be exposed as /api/courses
    print("\n--- Phase 2: Courses Tab ---")
    rag_url = "http://localhost:5002"
    courses = check_endpoint(f"{rag_url}/api/courses", required_keys=['courses'])
    if courses:
        log(f"Courses: {len(courses['courses'])} found", "INFO")

    # 3. Learn Tab - FSM State
    print("\n--- Phase 3: Learn Tab ---")
    state = check_endpoint(f"{BASE_URL}/api/fsm_state", required_keys=['state', 'current_lesson_uid'])
    if state:
        log(f"Current State: {state.get('state')}", "INFO")

if __name__ == "__main__":
    main()
