import sqlite3
import os
import uuid

def create_mock_kolibri_db(db_path="tools/mock_channel.sqlite3"):
    """
    Creates a mock Kolibri channel database for testing content providers.
    """
    if os.path.exists(db_path):
        os.remove(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create the minimal schema required by KolibriProvider
    # content_contentnode table
    cursor.execute("""
    CREATE TABLE content_contentnode (
        id TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        kind TEXT,
        available BOOLEAN,
        parent_id TEXT
    );
    """)

    # Data Generators
    folders = [
        ("root", "Root Topic", "The root of the channel", "topic", None),
        ("chem_topic", "Chemistry", "All about atoms", "topic", "root"),
        ("phys_topic", "Physics", "Forces and motion", "topic", "root")
    ]

    videos = [
        (str(uuid.uuid4()), "Atomic Structure", "Protons, neutrons, electrons.", "video", "chem_topic"),
        (str(uuid.uuid4()), "Covalent Bonding", "Sharing electrons.", "video", "chem_topic"),
        (str(uuid.uuid4()), "Newton's First Law", "Inertia explained.", "video", "phys_topic"),
        (str(uuid.uuid4()), "Thermodynamics", "Heat transfer.", "video", "phys_topic")
    ]

    print(f"Inserting {len(folders)} topics and {len(videos)} videos...")
    
    for uid, title, desc, kind, parent in folders:
        cursor.execute("INSERT INTO content_contentnode (id, title, description, kind, available, parent_id) VALUES (?, ?, ?, ?, ?, ?)",
                       (uid, title, desc, kind, True, parent))

    for uid, title, desc, kind, parent in videos:
        cursor.execute("INSERT INTO content_contentnode (id, title, description, kind, available, parent_id) VALUES (?, ?, ?, ?, ?, ?)",
                       (uid, title, desc, kind, True, parent))

    conn.commit()
    conn.close()
    print(f"Mock database created at {db_path}")

if __name__ == "__main__":
    create_mock_kolibri_db()
