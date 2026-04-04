import os
import sys
import logging
import subprocess

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("verifier")

def run_pytest_tests():
    """
    Execute pytest on tests/post_ingestion/ directory with appropriate options.
    Returns True if all tests pass, False otherwise.
    """
    logger.info("--- Running Post-Ingestion Tests ---")
    
    test_dir = "tests/post_ingestion"
    
    if not os.path.exists(test_dir):
        logger.error(f"Test directory not found: {test_dir}")
        return False
    
    try:
        # Run pytest with specified options
        cmd = [
            "pytest",
            test_dir,
            "--tb=short",
            "--disable-warnings",
            "-v"
        ]
        
        logger.info(f"Executing: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # Log pytest output
        if result.stdout:
            logger.info("PYTEST OUTPUT:\n" + result.stdout)
        if result.stderr:
            logger.warning("PYTEST STDERR:\n" + result.stderr)
        
        # Check exit code
        if result.returncode == 0:
            logger.info("All post-ingestion tests passed successfully.")
            return True
        else:
            logger.error(f"Post-ingestion tests failed with exit code: {result.returncode}")
            return False
            
    except FileNotFoundError:
        logger.error("pytest not found. Please ensure pytest is installed.")
        return False
    except Exception as e:
        logger.error(f"Error running pytest: {e}")
        return False

def run_checks():
    logger.info("Starting System Verification...")
    logger.info("Executing post-ingestion test suite...")
    
    tests_ok = run_pytest_tests()
    
    if tests_ok:
        logger.info("\nSystem Verification Complete: All tests passed.")
        return 0
    else:
        logger.error("\nSystem Verification Failed: Some tests failed.")
        return 1

if __name__ == "__main__":
    # Ensure run from root context or adjust paths
    if not os.path.exists("tests/post_ingestion"):
        # Try to navigate up if run from tools/
        if os.path.exists("../tests/post_ingestion"):
            os.chdir("..")
    
    exit_code = run_checks()
    sys.exit(exit_code)
