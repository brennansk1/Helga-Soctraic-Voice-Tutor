#!/usr/bin/env python3
import sys
import os
import logging
import time

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.core.service_manager import ServiceManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_service_lifecycle")

def verify_lifecycle():
    print("=== Verifying Service Lifecycle Logic ===")
    
    # Initialize ServiceManager (will attempt to connect to Docker)
    try:
        sm = ServiceManager()
    except Exception as e:
        print(f"FAILED to initialize ServiceManager: {e}")
        return

    print(f"ServiceManager initialized. Using Docker SDK: {sm.use_docker_sdk}")

    # We might not have 'helga-rag-engine' running in this dev environment.
    # But we can verify that the methods *run* without syntax errors.
    
    service_name = 'helga-rag-engine'
    
    # 1. STOP Service
    print(f"\n[Action] Stopping {service_name}...")
    stopped = sm.stop_service(service_name, timeout=2, retries=1)
    print(f"Stop Result: {stopped}")
    
    # 2. Verify Stopped
    print(f"\n[Action] Verifying {service_name} is stopped...")
    is_stopped = sm.verify_service_stopped(service_name, timeout=2, retries=1)
    print(f"Is Stopped: {is_stopped}")
    
    # 3. START Service
    # Only try to start if we successfully stopped it or if it was already stopped (implied by is_stopped)
    # However, if the container doesn't exist, this will fail.
    print(f"\n[Action] Starting {service_name}...")
    started = sm.start_service(service_name, timeout=5, retries=1)
    print(f"Start Result: {started}")

    # 4. Verify Healthy
    if started:
        print(f"\n[Action] Verifying {service_name} health...")
        # Give it a moment to come up
        time.sleep(2)
        healthy = sm.verify_service_healthy(service_name)
        print(f"Is Healthy: {healthy}")
    else:
        print("\n[Skip] Skipping health check as start failed (expected if container missing)")

    print("\n=== Verification Complete ===")

if __name__ == "__main__":
    verify_lifecycle()
