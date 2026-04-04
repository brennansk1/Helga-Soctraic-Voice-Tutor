import kuzu

db_path = "data/kuzu_db/kuzu_db"
db = kuzu.Database(db_path)
conn = kuzu.Connection(db)

# Query to count Concept nodes
result = conn.execute("MATCH (c:Concept) RETURN count(c)")

if result.has_next():
    row = result.get_next()
    node_count = row[0]
    print(f"Number of Concept nodes: {node_count}")
else:
    print("No Concept nodes found.")
    node_count = 0

# Query to count DEPENDS_ON relationships
result = conn.execute("MATCH (c1:Concept)-[r:DEPENDS_ON]->(c2:Concept) RETURN count(r)")

if result.has_next():
    row = result.get_next()
    rel_count = row[0]
    print(f"Number of DEPENDS_ON relationships: {rel_count}")
else:
    rel_count = 0

if node_count > 0:
    print("Concept nodes are present.")
else:
    print("No Concept nodes found.")

if rel_count > 0:
    print("DEPENDS_ON relationships are present and properly structured.")
else:
    print("No DEPENDS_ON relationships found.")