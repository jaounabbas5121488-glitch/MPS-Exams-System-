import sqlite3
from database import get_db

def init_test_generation_db():
    conn = get_db()
    cur = conn.cursor()

    # Syllabus Blocks
    cur.execute("""
        CREATE TABLE IF NOT EXISTS syllabus_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            block_name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (class_id) REFERENCES classes(id),
            FOREIGN KEY (subject_id) REFERENCES subjects(id),
            UNIQUE(class_id, subject_id, block_name)
        )
    """)

    # Subject Test Configuration (flexible)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subject_test_config (
            subject_id INTEGER PRIMARY KEY,
            has_mcq INTEGER DEFAULT 1,
            has_short INTEGER DEFAULT 0,
            has_long INTEGER DEFAULT 0,
            has_comprehension INTEGER DEFAULT 0,
            FOREIGN KEY (subject_id) REFERENCES subjects(id)
        )
    """)

    # Question Bank
    cur.execute("""
        CREATE TABLE IF NOT EXISTS question_bank (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            syllabus_block_id INTEGER NOT NULL,
            question_type TEXT NOT NULL,
            question_text TEXT NOT NULL,
            option_a TEXT,
            option_b TEXT,
            option_c TEXT,
            option_d TEXT,
            correct_answer TEXT,
            answer_text TEXT,
            comprehension_passage TEXT,
            parent_comprehension_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (syllabus_block_id) REFERENCES syllabus_blocks(id),
            FOREIGN KEY (parent_comprehension_id) REFERENCES question_bank(id)
        )
    """)

    # Test Generation Permissions
    cur.execute("""
        CREATE TABLE IF NOT EXISTS test_permissions (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            allow_teachers INTEGER DEFAULT 0
        )
    """)

    # Generated Tests History
    cur.execute("""
        CREATE TABLE IF NOT EXISTS generated_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            syllabus_block_ids TEXT,
            total_marks REAL,
            mcq_count INTEGER,
            short_count INTEGER,
            long_count INTEGER,
            comprehension_count INTEGER,
            mcq_marks_each REAL,
            short_marks_each REAL,
            long_marks_each REAL,
            questions_json TEXT,
            answer_key_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (class_id) REFERENCES classes(id),
            FOREIGN KEY (subject_id) REFERENCES subjects(id)
        )
    """)

    # Ensure default permission row
    cur.execute("INSERT OR IGNORE INTO test_permissions (id, allow_teachers) VALUES (1, 0)")

    conn.commit()
    conn.close()