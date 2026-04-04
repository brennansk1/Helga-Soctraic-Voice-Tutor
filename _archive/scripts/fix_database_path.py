#!/usr/bin/env python3
"""
Fix corrupted KuzuDB database path.

If /app/data/kuzu_db exists as a FILE instead of DIRECTORY,
this script removes it and recreates the proper directory structure.

This is a recovery script for when database initialization goes wrong.
"""

import os
import sys
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def fix_database_path():
    """Fix corrupted database path."""
    db_path = "data/kuzu_db"
    
    logger.info(f"Checking database path: {db_path}")
    
    if os.path.exists(db_path):
        if os.path.isdir(db_path):
            logger.info(f"{db_path} is a valid directory.")
            
            # Check if subdirectory 'db' exists
            db_instance = os.path.join(db_path, "db")
            if os.path.exists(db_instance):
                if os.path.isdir(db_instance):
                    logger.info(f"Database instance directory {db_instance} exists and is valid.")
                else:
                    logger.warning(f"{db_instance} exists but is a FILE. This will cause errors.")
                    logger.info(f"Removing corrupted file: {db_instance}")
                    try:
                        os.remove(db_instance)
                        logger.info(f"Successfully removed {db_instance}")
                    except Exception as e:
                        logger.error(f"Failed to remove {db_instance}: {e}")
                        return False
            else:
                logger.info(f"Database instance directory {db_instance} does not exist yet. This is normal.")
        else:
            # db_path exists but is a FILE
            logger.error(f"{db_path} exists but is a FILE, not a directory!")
            logger.info(f"Removing corrupted file: {db_path}")
            try:
                os.remove(db_path)
                logger.info(f"Successfully removed corrupted file: {db_path}")
            except Exception as e:
                logger.error(f"Failed to remove {db_path}: {e}")
                return False
            
            # Create proper directory
            logger.info(f"Creating proper directory structure: {db_path}")
            try:
                os.makedirs(db_path, exist_ok=True)
                logger.info(f"Successfully created directory: {db_path}")
            except Exception as e:
                logger.error(f"Failed to create directory {db_path}: {e}")
                return False
    else:
        logger.info(f"{db_path} does not exist. Creating directory structure.")
        try:
            os.makedirs(db_path, exist_ok=True)
            logger.info(f"Successfully created directory: {db_path}")
        except Exception as e:
            logger.error(f"Failed to create directory {db_path}: {e}")
            return False
    
    logger.info("Database path check complete.")
    return True

if __name__ == "__main__":
    success = fix_database_path()
    sys.exit(0 if success else 1)
