import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "modul2ai.db")


def get_connection() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    from app.auth import hash_password

    conn = get_connection()
    cur = conn.cursor()

    # 1. Tabel Roles
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
    """)

    # 2. Tabel Admins (Modul Standar)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)

    # 3. Tabel Users (Registrasi Mahasiswa & Dosen seperti ChatGPT)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE RESTRICT
        );
    """)

    # 4. Tabel Documents
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_type TEXT NOT NULL,
            total_chunks INTEGER DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            uploaded_at TEXT NOT NULL
        );
    """)

    cur.execute("PRAGMA table_info(documents)")
    doc_cols = {row["name"] for row in cur.fetchall()}
    if "is_active" not in doc_cols:
        cur.execute("ALTER TABLE documents ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

    # 5. Tabel Relasi Dokumen-Role (RBAC)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_roles (
            document_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (document_id, role_id),
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
            FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
        );
    """)

    # 6. Tabel Document Chunks
    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        );
    """)

    # 7. Tabel Groq Usage Logs (Monitoring Kuota RPD/TPM)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS groq_usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            limit_requests INTEGER,
            remaining_requests INTEGER,
            reset_requests TEXT DEFAULT '',
            limit_tokens INTEGER,
            remaining_tokens INTEGER,
            reset_tokens TEXT DEFAULT '',
            status TEXT DEFAULT 'success',
            created_at TEXT NOT NULL
        );
    """)

    # 8. Tabel Chat Conversations (Riwayat Chat multi-sesi gaya ChatGPT)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            title TEXT NOT NULL,
            role_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)

    # 9. Tabel Chat Messages
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sources_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE
        );
    """)

    now = datetime.now(timezone.utc).isoformat()

    # Seed Default Roles
    default_roles = [
        ("mahasiswa", "Akses informasi akademik & layanan SSC untuk mahasiswa"),
        ("dosen", "Akses informasi akademik, layanan SSC, & pedoman internal dosen"),
    ]
    for role_name, desc in default_roles:
        cur.execute(
            "INSERT OR IGNORE INTO roles (name, description, created_at) VALUES (?, ?, ?)",
            (role_name, desc, now),
        )

    # Seed Default Admin
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    cur.execute("SELECT id FROM admins WHERE username = ?", (admin_username,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)",
            (admin_username, hash_password(admin_password), now),
        )

    # Ambil role IDs untuk sample users
    cur.execute("SELECT id FROM roles WHERE LOWER(name) = 'mahasiswa'")
    mhs_role = cur.fetchone()
    cur.execute("SELECT id FROM roles WHERE LOWER(name) = 'dosen'")
    dsn_role = cur.fetchone()

    # Seed Sample Users (sehingga langsung bisa diuji tanpa harus register terlebih dahulu)
    if mhs_role:
        cur.execute("SELECT id FROM users WHERE username = 'mahasiswa'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (username, full_name, password_hash, role_id, created_at) VALUES (?, ?, ?, ?, ?)",
                ("mahasiswa", "Mahasiswa Tel-U", hash_password("mahasiswa123"), mhs_role["id"], now),
            )

    if dsn_role:
        cur.execute("SELECT id FROM users WHERE username = 'dosen'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (username, full_name, password_hash, role_id, created_at) VALUES (?, ?, ?, ?, ?)",
                ("dosen", "Dosen Pembimbing Akademik", hash_password("dosen123"), dsn_role["id"], now),
            )

    conn.commit()
    conn.close()
