#!/usr/bin/env python3
"""
Clean Slate script for the Helga Socratic Tutor project.

This script performs the following actions to reset the project state:
1. Stops and removes all running Docker containers defined in docker-compose.yml.
2. Removes any orphaned Docker containers.
3. Prunes the Docker build cache.
4. Prunes unused Docker images, networks, and volumes.
5. Deletes the KuzuDB database directory.
6. Deletes all log files.
"""

import os
import subprocess
import sys
import logging
import shutil

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

def is_tool(name):
    """Check whether `name` is on PATH and marked as executable."""
    return shutil.which(name) is not None

def run_command(command, use_sudo=False, sudo_password=None):
    """
    Runs a command in the shell, streaming its output.
    Returns True on success, False on failure.
    """
    if use_sudo:
        if sudo_password:
            # Use sudo -S to read password from stdin
            command = ['sudo', '-S'] + command
        else:
            command.insert(0, 'sudo')
    
    logger.info(f"Running command: {' '.join(command)}")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if (use_sudo and sudo_password) else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        if use_sudo and sudo_password:
            # Send password to stdin and close it
            process.stdin.write(sudo_password + '\n')
            process.stdin.close()
        
        for line in iter(process.stdout.readline, ''):
            sys.stdout.write(line)
        process.stdout.close()
        return_code = process.wait()
        if return_code:
            raise subprocess.CalledProcessError(return_code, command)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"Command failed: {e}")
        return False

def main():
    """Main cleanup function."""
    logger.info("--- Starting Clean Slate Protocol ---")

    # 1. Force use_sudo to False (determined that docker works without it)
    use_sudo = False

    docker_compose_cmd = ['docker', 'compose']
    if not is_tool('docker-compose'):
        pass
    else:
        try:
            subprocess.check_call(['docker', 'compose', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, FileNotFoundError):
            docker_compose_cmd = ['docker-compose']
    
    # 2. Stop and remove Docker containers
    logger.info("Stopping and removing Docker containers...")
    compose_down_cmd = docker_compose_cmd + ['down', '--volumes'] # --volumes removes named volumes
    
    # Get sudo password if needed
    sudo_password = None
    if use_sudo:
        sudo_password = os.getenv('HELGA_SUDO_PASSWORD')
    
    if not run_command(compose_down_cmd, use_sudo=use_sudo, sudo_password=sudo_password):
        logger.warning("Could not stop Docker containers. They may not have been running.")

    # 3. Remove orphaned containers
    logger.info("Removing orphaned Docker containers...")
    container_prune_cmd = ['docker', 'container', 'prune', '-f']
    if not run_command(container_prune_cmd, use_sudo=use_sudo, sudo_password=sudo_password):
        logger.warning("Could not prune orphaned containers.")

    # REMOVED: Aggressive pruning that deletes cache and images
    # This was causing slow rebuild times.
    # builder_prune_cmd = ['docker', 'builder', 'prune', '-f']
    # system_prune_cmd = ['docker', 'system', 'prune', '-af']

    # 6. Delete storage (SQLite + courses)
    sqlite_path = "data/helga.db"
    if os.path.exists(sqlite_path):
        logger.info(f"Deleting SQLite database at {sqlite_path}...")
        try:
            os.unlink(sqlite_path)
            logger.info("SQLite database deleted successfully.")
        except OSError as e:
            logger.error(f"Failed to delete SQLite database {sqlite_path}: {e}", exc_info=True)
    else:
        logger.info("SQLite database not found, skipping.")
    
    courses_path = "data/courses"
    if os.path.exists(courses_path):
        logger.info(f"Deleting courses directory at {courses_path}...")
        try:
            shutil.rmtree(courses_path)
            logger.info("Courses directory deleted successfully.")
        except OSError as e:
            logger.error(f"Failed to delete courses directory {courses_path}: {e}", exc_info=True)
    else:
        logger.info("Courses directory not found, skipping.")
    
    # Legacy: also clean up old KuzuDB if present
    legacy_db_path = "data/kuzu_db"
    if os.path.exists(legacy_db_path):
        logger.info(f"Deleting legacy KuzuDB at {legacy_db_path}...")
        try:
            shutil.rmtree(legacy_db_path)
            logger.info("Legacy database deleted.")
        except OSError as e:
            logger.error(f"Failed to delete legacy database: {e}", exc_info=True)

    # Clean up orphaned test / debug databases
    orphan_patterns = ['kuzu_test_db', 'test_conflict_db']
    for entry in os.listdir("data"):
        if any(entry.startswith(p) for p in orphan_patterns):
            path = os.path.join("data", entry)
            logger.info(f"Deleting orphaned test DB: {path}")
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
            except OSError as e:
                logger.warning(f"Could not delete {path}: {e}")

    # 7. Delete log files
    logs_path = "data/logs"
    if os.path.exists(logs_path):
        logger.info(f"Deleting log files in {logs_path}...")
        try:
            for filename in os.listdir(logs_path):
                file_path = os.path.join(logs_path, filename)
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            logger.info("Log files deleted successfully.")
        except OSError as e:
            logger.error(f"Failed to delete log files: {e}", exc_info=True)
    else:
        logger.info("Logs directory not found, skipping.")

    logger.info("--- Clean Slate Protocol Complete ---")
    logger.info("The project is now in a clean state. Run 'python main.py' to start again.")

if __name__ == "__main__":
    main()
