import kuzu
import os
import logging
import sys
import shutil

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

def initialize_database(db_path="data/kuzu_db/db", schema_path='schema.cypher'):

    try:
        # Create the parent directory if it doesn't exist
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        logger.info(f"Connecting to KuzuDB database at {db_path}...")
        # Limit buffer pool to 256MB to prevent OOM
        db = kuzu.Database(db_path, buffer_pool_size=256 * 1024 * 1024)
        conn = kuzu.Connection(db)
        logger.info("Database connection successful.")

        logger.info(f"Reading schema from {schema_path}...")
        with open(schema_path, 'r') as f:
            schema_content = f.read()

        # Split schema by semicolon to get individual statements
        statements = [s.strip() for s in schema_content.split(';') if s.strip()]

        logger.info("Executing schema statements...")
        for stmt in statements:
            # Remove comment lines
            lines = [line for line in stmt.split('\n') if not line.strip().startswith('//')]
            clean_stmt = '\n'.join(lines).strip()
            
            if not clean_stmt:
                continue
                
            try:
                conn.execute(clean_stmt)
                logger.info(f"Successfully executed statement: {clean_stmt[:50]}...")
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "already exists" in error_msg:
                    logger.info(f"Object already exists, skipping: {stmt[:50]}...")
                else:
                    logger.warning(f"Failed to execute statement: {stmt[:50]}... Error: {e}")

        # --- MIGRATION LOGIC ---
        logger.info("Checking for necessary schema migrations...")
        
        # Check if new columns exist in Concept table
        # We can't easily check for column existence, so we just try to add them.
        # If they exist, it will fail, which we catch.
        new_columns = [
            ("source_id", "STRING"),
            ("source_type", "STRING"),
            ("content_hash", "STRING")
        ]

        for col_name, col_type in new_columns:
            try:
                logger.info(f"Attempting to add column {col_name} to Concept table...")
                conn.execute(f"ALTER TABLE Concept ADD {col_name} {col_type}")
                logger.info(f"Added column {col_name}.")
            except RuntimeError as e:
                if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                     logger.info(f"Column {col_name} likely already exists or other error. Skipping.")
                else:
                     logger.warning(f"Could not add column {col_name}: {e}")

        logger.info("Database initialization/update complete.")

    except FileNotFoundError:
        logger.error(f"Schema file not found at '{schema_path}'. Cannot initialize database.")
        sys.exit(1)
    except IOError as e:
        logger.error(f"Error reading schema file: {e}", exc_info=True)
        sys.exit(1)
    except Exception as e:
        logger.error(f"An error occurred during database initialization: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Initialize KuzuDB schema.')
    parser.add_argument('--db_path', type=str, default="data/kuzu_db/db", help='Path to the KuzuDB database directory')
    parser.add_argument('--schema_path', type=str, default="schema.cypher", help='Path to the schema cypher file')
    args = parser.parse_args()
    
    initialize_database(args.db_path, args.schema_path)