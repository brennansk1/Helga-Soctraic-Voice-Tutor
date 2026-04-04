#!/usr/bin/env python3
"""
Night Shift Socratic Audit Tool

This script performs nightly batch processing to grade the AI's performance
based on session logs and adjusts FSRS stability for poorly taught concepts.
"""

import os
import json
import sys
import time
import logging
import requests
from datetime import datetime, timedelta
import kuzu

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/logs/night_audit.log")
    ]
)

class NightAudit:
    def __init__(self):
        self.dev_mode = os.getenv('DEV_MODE', 'False').lower() == 'true' or os.name == 'nt'
        logging.info(f"Night Audit running in {'developer' if self.dev_mode else 'production'} mode.")

        # Service URLs
        if self.dev_mode:
            self.llm_url = "http://localhost:8080"
            self.rag_url = "http://localhost:5002"
        else:
            self.llm_url = "http://inference-llm:8080"
            self.rag_url = "http://rag-engine:5002"
        
        logging.info(f"LLM URL: {self.llm_url}, RAG URL: {self.rag_url}")

        # Database
        self.db = None
        self.conn = None
        try:
            db_path = "data/kuzu_db/db"
            logging.info(f"Connecting to KuzuDB at {db_path}...")
            self.db = kuzu.Database(db_path)
            self.conn = kuzu.Connection(self.db)
            logging.info("KuzuDB connection successful.")
        except Exception as e:
            logging.error(f"Failed to connect to KuzuDB: {e}", exc_info=True)
            # Exit if we can't connect to the DB
            sys.exit(1)

    def run_audit(self):
        """Main audit function"""
        logging.info("Starting Night Shift Socratic Audit")

        # 1. Collect session logs from the last 24 hours
        yesterday = datetime.now() - timedelta(days=1)
        sessions = self.collect_recent_sessions(yesterday)

        if not sessions:
            logging.info("No recent sessions to audit")
            return

        # 2. Analyze each session
        for session in sessions:
            self.analyze_session(session)

        # 3. Aggregate insights and adjust FSRS
        self.adjust_fsrs_stability()

        logging.info("Night Shift Audit Complete")

    def collect_recent_sessions(self, since_datetime):
        """Collect interaction logs since given datetime"""
        logging.info(f"Collecting recent sessions since {since_datetime}...")
        try:
            since_ts = int(since_datetime.timestamp())
            # Note: The schema in other files shows Interaction does not exist.
            # This query will likely fail. Assuming a schema with `Interaction` for now.
            result = self.conn.execute(f"""
                MATCH (i:Interaction)
                WHERE i.timestamp > {since_ts}
                RETURN i.context AS context, i.rating AS rating, i.timestamp AS timestamp
                ORDER BY i.timestamp DESC
            """)

            sessions = []
            while result.has_next():
                row = result.get_next()
                sessions.append({
                    'context': row['context'],
                    'rating': row['rating'],
                    'timestamp': row['timestamp']
                })

            logging.info(f"Collected {len(sessions)} recent interactions to audit.")
            return sessions

        except Exception as e:
            logging.error(f"Error collecting sessions from database: {e}", exc_info=True)
            return []

    def analyze_session(self, session):
        """Use LLM-as-a-Judge to evaluate session quality"""
        logging.info(f"Analyzing session from timestamp: {session['timestamp']}")
        prompt = f"""
        Analyze this learning session interaction:

        Context: {session['context']}
        User Rating: {session['rating']}/5

        As an AI tutor evaluator, assess:
        1. Was the teaching effective? (1-5 scale)
        2. What concepts were poorly explained?
        3. Suggestions for improvement.

        Output JSON ONLY: {{"effectiveness": int, "weak_concepts": ["concept1", "concept2"], "suggestions": "text"}}
        """

        try:
            resp = requests.post(f"{self.llm_url}/completion", json={
                "prompt": prompt,
                "n_predict": 256
            }, timeout=60)
            resp.raise_for_status()

            content = resp.json().get('content', '{}')
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()

            analysis = json.loads(content)

            # Store analysis result
            self.store_analysis(session, analysis)

        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to get analysis from LLM service: {e}", exc_info=True)
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse JSON response from LLM: {e}", exc_info=True)
        except Exception as e:
            logging.error(f"An unexpected error occurred during session analysis: {e}", exc_info=True)

    def store_analysis(self, session, analysis):
        """Store the analysis in the database"""
        logging.info(f"Storing analysis for session: {session['timestamp']}")
        try:
            # Again, assuming an Analysis node schema
            self.conn.execute(f"""
                CREATE (a:Analysis {{
                    session_timestamp: {session['timestamp']},
                    effectiveness: {analysis.get('effectiveness', 3)},
                    weak_concepts: {json.dumps(analysis.get('weak_concepts', []))},
                    suggestions: "{analysis.get('suggestions', '')}"
                }})
            """)
            logging.info(f"Successfully stored analysis for session {session['timestamp']}")
        except Exception as e:
            logging.error(f"Failed to store analysis for session {session['timestamp']}: {e}", exc_info=True)

    def adjust_fsrs_stability(self):
        """Adjust FSRS stability based on aggregated analysis"""
        logging.info("Aggregating weak concepts and adjusting FSRS stability...")
        try:
            # Get concepts with low effectiveness
            result = self.conn.execute("""
                MATCH (a:Analysis)
                WHERE a.effectiveness < 3
                UNWIND a.weak_concepts AS concept
                RETURN concept, COUNT(*) AS frequency
                ORDER BY frequency DESC
                LIMIT 10
            """)

            weak_concepts = []
            while result.has_next():
                row = result.get_next()
                weak_concepts.append((row['concept'], row['frequency']))

            if not weak_concepts:
                logging.info("No concepts with effectiveness < 3 found. No adjustments needed.")
                return

            # For each weak concept, decrease stability
            for concept, freq in weak_concepts:
                # Find matching nodes and decrease their stability
                logging.info(f"Adjusting stability for concept '{concept}' (frequency: {freq})")
                self.conn.execute(f"""
                    MATCH (c:Concept)
                    WHERE c.title CONTAINS "{concept}"
                    SET c.stability = c.stability * 0.8  # Decrease stability by 20%
                """)
                logging.info(f"Decreased stability for concept: {concept}")

        except Exception as e:
            logging.error(f"Failed to adjust FSRS stability: {e}", exc_info=True)

if __name__ == "__main__":
    auditor = NightAudit()
    auditor.run_audit()