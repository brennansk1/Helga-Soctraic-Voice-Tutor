-- Helga Socratic Tutor — SQLite Schema
-- Replaces KuzuDB graph database with relational SQLite + sqlite-vec

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER DEFAULT (unixepoch()),
    description TEXT
);

-- Core hierarchy
CREATE TABLE IF NOT EXISTS courses (
    uid TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    overview TEXT,
    description TEXT,
    teaching_style TEXT DEFAULT 'socratic',
    status TEXT DEFAULT 'building',
    creation_mode TEXT DEFAULT 'quick',
    design_brief TEXT,
    prior_knowledge TEXT DEFAULT 'new',
    total_concepts INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT (unixepoch()),
    updated_at INTEGER DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS concepts (
    uid TEXT PRIMARY KEY,
    course_uid TEXT NOT NULL,
    parent_uid TEXT,
    title TEXT NOT NULL,
    resource_text TEXT,
    bloom_level INTEGER DEFAULT 1,
    ordinal INTEGER DEFAULT 0,
    depth_level INTEGER DEFAULT 0,
    completed BOOLEAN DEFAULT 0,
    stability REAL DEFAULT 0.0,
    difficulty REAL DEFAULT 5.0,
    due_date INTEGER DEFAULT 0,
    last_review INTEGER DEFAULT 0,
    review_count INTEGER DEFAULT 0,
    correct_streak INTEGER DEFAULT 0,
    question_count INTEGER DEFAULT 0,
    misconceptions TEXT DEFAULT '[]',
    analogies TEXT DEFAULT '[]',
    learning_objectives TEXT DEFAULT '[]',
    key_terms TEXT DEFAULT '[]',
    examples TEXT DEFAULT '[]',
    takeaways TEXT DEFAULT '[]',
    estimated_minutes INTEGER DEFAULT 10,
    source TEXT DEFAULT 'generated',
    user_note TEXT DEFAULT '',
    created_at INTEGER DEFAULT (unixepoch()),
    FOREIGN KEY (course_uid) REFERENCES courses(uid) ON DELETE CASCADE,
    FOREIGN KEY (parent_uid) REFERENCES concepts(uid) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS concept_embeddings (
    concept_uid TEXT PRIMARY KEY,
    embedding BLOB,
    FOREIGN KEY (concept_uid) REFERENCES concepts(uid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS prerequisites (
    from_uid TEXT NOT NULL,
    to_uid TEXT NOT NULL,
    strength REAL DEFAULT 1.0,
    PRIMARY KEY (from_uid, to_uid),
    FOREIGN KEY (from_uid) REFERENCES concepts(uid) ON DELETE CASCADE,
    FOREIGN KEY (to_uid) REFERENCES concepts(uid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_uid TEXT,
    course_uid TEXT,
    user_input TEXT,
    tutor_response TEXT,
    grade INTEGER,
    bloom_level INTEGER,
    response_time_ms INTEGER,
    xp_earned INTEGER DEFAULT 0,
    timestamp INTEGER DEFAULT (unixepoch()),
    FOREIGN KEY (concept_uid) REFERENCES concepts(uid) ON DELETE SET NULL,
    FOREIGN KEY (course_uid) REFERENCES courses(uid) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS session_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER DEFAULT (unixepoch())
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_concepts_course ON concepts(course_uid);
CREATE INDEX IF NOT EXISTS idx_concepts_parent ON concepts(parent_uid);
CREATE INDEX IF NOT EXISTS idx_concepts_due ON concepts(due_date);
CREATE INDEX IF NOT EXISTS idx_concepts_depth ON concepts(course_uid, depth_level, ordinal);
CREATE INDEX IF NOT EXISTS idx_interactions_concept ON interactions(concept_uid);
CREATE INDEX IF NOT EXISTS idx_interactions_course ON interactions(course_uid);
CREATE INDEX IF NOT EXISTS idx_interactions_timestamp ON interactions(timestamp);

-- User profile
CREATE TABLE IF NOT EXISTS user_profile (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER DEFAULT (unixepoch())
);

-- Default profile values
INSERT OR IGNORE INTO user_profile (key, value) VALUES
    ('display_name', ''),
    ('theme', 'light'),
    ('font_scale', '1.0'),
    ('default_voice', 'af_heart'),
    ('gamification_enabled', 'true'),
    ('daily_goal', '5');

-- Gamification state
CREATE TABLE IF NOT EXISTS gamification (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER DEFAULT (unixepoch())
);

-- Default gamification values
INSERT OR IGNORE INTO gamification (key, value) VALUES
    ('total_xp', '0'),
    ('level', '1'),
    ('streak_days', '0'),
    ('streak_last_date', ''),
    ('daily_xp', '0'),
    ('daily_date', ''),
    ('achievements_unlocked', '[]');

-- Achievement definitions (16 achievements)
CREATE TABLE IF NOT EXISTS achievements (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    xp_reward INTEGER DEFAULT 50,
    unlocked INTEGER DEFAULT 0,
    unlocked_at INTEGER
);

INSERT OR IGNORE INTO achievements (id, name, description, xp_reward) VALUES
    ('first_course', 'First Steps', 'Create your first course', 50),
    ('first_answer', 'Curious Mind', 'Answer your first Socratic question', 25),
    ('perfect_concept', 'Ace', 'Get grade 4 on a concept', 75),
    ('streak_3', 'On a Roll', 'Maintain a 3-day streak', 100),
    ('streak_7', 'Week Warrior', 'Maintain a 7-day streak', 200),
    ('streak_30', 'Monthly Master', 'Maintain a 30-day streak', 500),
    ('concepts_10', 'Explorer', 'Complete 10 concepts', 150),
    ('concepts_50', 'Scholar', 'Complete 50 concepts', 300),
    ('concepts_100', 'Sage', 'Complete 100 concepts', 500),
    ('bloom_3', 'Analyst', 'Reach Bloom level 3 on any concept', 100),
    ('bloom_5', 'Evaluator', 'Reach Bloom level 5 on any concept', 250),
    ('bloom_6', 'Creator', 'Reach Bloom level 6 on any concept', 500),
    ('courses_3', 'Polymath', 'Create 3 courses', 200),
    ('courses_5', 'Renaissance', 'Create 5 courses', 400),
    ('review_10', 'Reviewer', 'Complete 10 spaced reviews', 100),
    ('daily_goal', 'Goal Getter', 'Meet your daily goal', 50);

-- Record initial schema version
INSERT OR IGNORE INTO schema_version (version, description)
VALUES (1, 'Initial schema — courses, concepts, embeddings, prerequisites, interactions, session_state, gamification');
