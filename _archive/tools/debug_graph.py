
import sys
import os
import kuzu
import shutil
from unittest.mock import MagicMock

# Add project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock sentence_transformers if missing
try:
    import sentence_transformers
except ImportError:
    sys.modules['sentence_transformers'] = MagicMock()

from services.rag import librarian


DB_DIR = "./debug_kuzu_db"
if os.path.exists(DB_DIR):
    shutil.rmtree(DB_DIR)

db = kuzu.Database(DB_DIR)
conn = kuzu.Connection(db)

# Schema
try:
    conn.execute("CREATE NODE TABLE Course(uid STRING, title STRING, PRIMARY KEY(uid))")
    conn.execute("CREATE NODE TABLE Module(uid STRING, title STRING, PRIMARY KEY(uid))")
    conn.execute("CREATE NODE TABLE Concept(uid STRING, title STRING, resource_text STRING, summary_vector FLOAT[384], due_date INT64, completed BOOLEAN, PRIMARY KEY(uid))")
    
    conn.execute("CREATE REL TABLE HAS_MODULE(FROM Course TO Module)")
    conn.execute("CREATE REL TABLE HAS_LESSON(FROM Module TO Concept)")
except RuntimeError as e:
    print(f"Schema error (ignored): {e}")

# Data
print("Creating data...")
conn.execute("CREATE (:Course {uid: 'course_A', title: 'Physics'})")
conn.execute("CREATE (:Module {uid: 'mod_A1', title: 'Mechanics'})")
conn.execute("MATCH (c:Course {uid: 'course_A'}), (m:Module {uid: 'mod_A1'}) CREATE (c)-[:HAS_MODULE]->(m)")

vec_A = [1.0] * 384
conn.execute("CREATE (:Concept {uid: 'con_A1', title: 'Atom', resource_text: 'An atom is small.', summary_vector: $v})", {"v": vec_A})
conn.execute("MATCH (m:Module {uid: 'mod_A1'}), (c:Concept {uid: 'con_A1'}) CREATE (m)-[:HAS_LESSON]->(c)")

# Verify Nodes
print("Nodes:")
res = conn.execute("MATCH (n) RETURN labels(n), n.uid")
while res.has_next():
    print(res.get_next())

# Verify Relationships
print("Relationships:")
res = conn.execute("MATCH (a)-[r]->(b) RETURN a.uid, labels(a), b.uid, labels(b)")
while res.has_next():
    print(res.get_next())

# Verify Path Query
print("Path Query:")
cid = 'course_A'
query = """
MATCH (co:Course {uid: $cid})
MATCH (co)-[:HAS_MODULE|HAS_LESSON*1..3]->(con:Concept)
RETURN con.uid
"""
try:
    res = conn.execute(query, {"cid": cid})
    while res.has_next():
        print(f"PATH FOUND: {res.get_next()}")
except Exception as e:
    print(f"Query Failed: {e}")

# Verify Due Date Update
print("Updating Due Date...")
conn.execute("MATCH (c:Concept) SET c.due_date = 0")
res = conn.execute("MATCH (c:Concept) RETURN c.uid, c.due_date")
while res.has_next():
    print(f"DUE DATE: {res.get_next()}")
