import sqlite3
import os
import json
import time

DB_PATH = os.path.expanduser("~/.cheetahclaws/audit_log.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            session_id TEXT,
            role TEXT,
            content TEXT
        )
    ''')
    conn.commit()
    conn.close()

def log_turn(session_id: str, role: str, content: str):
    """Log a single conversational turn."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO audit_logs (timestamp, session_id, role, content)
        VALUES (?, ?, ?, ?)
    ''', (time.time(), session_id, role, content))
    conn.commit()
    conn.close()
