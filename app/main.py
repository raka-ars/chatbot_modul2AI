import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.auth import (
    create_access_token,
    get_current_admin,
    get_current_user,
    get_current_user_optional,
    hash_password,
    verify_password,
)
from app.database import get_connection, init_db
from app.extractor import extract_text_from_bytes
from app.rag import chunk_text, generate_answer, generate_embedding, search_chunks_by_role

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATASET_DIR = BASE_DIR / "dataset"
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Custom AI Chatbot RAG Berbasis Role (RBAC) & ChatGPT Interface",
    version="2.0.0",
)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def seed_default_documents() -> None:
    if not DATASET_DIR.exists():
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM documents")
    if cur.fetchone()["cnt"] > 0:
        conn.close()
        return

    # Ambil role IDs
    cur.execute("SELECT id, name FROM roles")
    roles_map = {row["name"].lower(): row["id"] for row in cur.fetchall()}
    mhs_id = roles_map.get("mahasiswa")
    dsn_id = roles_map.get("dosen")

    if not mhs_id:
        conn.close()
        return

    for file_path in sorted(DATASET_DIR.iterdir()):
        if not file_path.is_file() or file_path.suffix.lower() not in {".txt", ".md", ".pdf", ".docx", ".csv", ".xlsx"}:
            continue

        file_bytes = file_path.read_bytes()
        try:
            extracted_text = extract_text_from_bytes(file_bytes, file_path.name)
        except Exception:
            continue
        if not extracted_text:
            continue

        chunks = chunk_text(extracted_text)
        if not chunks:
            continue

        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file_path.name)
        stored_filename = f"default_{safe_name}"
        (UPLOAD_DIR / stored_filename).write_bytes(file_bytes)

        doc_title = file_path.stem.replace("_", " ")
        ext = file_path.suffix.lstrip(".").lower() or "txt"
        now = datetime.now(timezone.utc).isoformat()

        cur.execute(
            "INSERT INTO documents (title, filename, file_type, total_chunks, is_active, uploaded_at) VALUES (?, ?, ?, ?, 1, ?)",
            (doc_title, stored_filename, ext, len(chunks), now),
        )
        doc_id = cur.lastrowid

        # RBAC Mapping:
        # Jika dokumen mengandung "Dosen" -> Khusus Dosen
        # Selain itu -> Mahasiswa dan Dosen
        if "dosen" in file_path.name.lower():
            if dsn_id:
                cur.execute(
                    "INSERT OR IGNORE INTO document_roles (document_id, role_id) VALUES (?, ?)",
                    (doc_id, dsn_id),
                )
        else:
            cur.execute(
                "INSERT OR IGNORE INTO document_roles (document_id, role_id) VALUES (?, ?)",
                (doc_id, mhs_id),
            )
            if dsn_id:
                cur.execute(
                    "INSERT OR IGNORE INTO document_roles (document_id, role_id) VALUES (?, ?)",
                    (doc_id, dsn_id),
                )

        for idx, ch in enumerate(chunks):
            emb = generate_embedding(ch)
            cur.execute(
                "INSERT INTO document_chunks (document_id, chunk_index, text, embedding_json) VALUES (?, ?, ?, ?)",
                (doc_id, idx, ch, json.dumps(emb)),
            )

    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup():
    init_db()
    seed_default_documents()


# ---------- Schemas ----------

class RegisterRequest(BaseModel):
    username: str
    full_name: str
    password: str
    role: str = "mahasiswa"


class LoginRequest(BaseModel):
    username: str
    password: str


class RoleCreateRequest(BaseModel):
    name: str
    description: str = ""


class RoleUpdateRequest(BaseModel):
    name: str
    description: str = ""


class ChatRequest(BaseModel):
    question: str
    role: Optional[str] = None


class ConversationCreateRequest(BaseModel):
    title: Optional[str] = "Percakapan Baru"
    role: Optional[str] = "mahasiswa"


class ConversationRenameRequest(BaseModel):
    title: str


class SendMessageRequest(BaseModel):
    message: str


# ---------- Page Routes ----------

@app.get("/", include_in_schema=False)
def serve_chat_page():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/admin", include_in_schema=False)
def serve_admin_page():
    return FileResponse(str(STATIC_DIR / "admin.html"))


# ---------- Auth Routes (Register & Login gaya ChatGPT) ----------

@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest):
    username = payload.username.strip().lower()
    full_name = payload.full_name.strip()
    password = payload.password
    role_name = payload.role.strip().lower() or "mahasiswa"

    if not username or len(username) < 3:
        raise HTTPException(status_code=400, detail="Username minimal 3 karakter.")
    if not full_name:
        raise HTTPException(status_code=400, detail="Nama lengkap wajib diisi.")
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail="Password minimal 6 karakter.")

    conn = get_connection()
    cur = conn.cursor()

    # Cek duplikat username di tabel users dan admins
    cur.execute("SELECT id FROM users WHERE LOWER(username) = ?", (username,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail=f"Username '{username}' sudah terdaftar.")

    cur.execute("SELECT id FROM admins WHERE LOWER(username) = ?", (username,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail=f"Username '{username}' sudah digunakan oleh Admin.")

    # Cari role id
    cur.execute("SELECT id, name FROM roles WHERE LOWER(name) = ?", (role_name,))
    role_row = cur.fetchone()
    if not role_row:
        # Buat role jika belum ada
        now = datetime.now(timezone.utc).isoformat()
        cur.execute("INSERT INTO roles (name, description, created_at) VALUES (?, ?, ?)", (role_name, f"Role {role_name}", now))
        role_id = cur.lastrowid
        actual_role_name = role_name
    else:
        role_id = role_row["id"]
        actual_role_name = role_row["name"]

    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        "INSERT INTO users (username, full_name, password_hash, role_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (username, full_name, hash_password(password), role_id, now),
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()

    token = create_access_token(
        user_id=user_id,
        username=username,
        full_name=full_name,
        role=actual_role_name,
        is_admin=False,
    )

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user_id,
            "username": username,
            "full_name": full_name,
            "role": actual_role_name,
            "is_admin": False,
        },
    }


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    username = payload.username.strip().lower()
    password = payload.password

    conn = get_connection()
    cur = conn.cursor()

    # 1. Cek Admin terlebih dahulu
    cur.execute("SELECT id, username, password_hash FROM admins WHERE LOWER(username) = ?", (username,))
    admin = cur.fetchone()
    if admin and verify_password(password, admin["password_hash"]):
        conn.close()
        token = create_access_token(
            user_id=admin["id"],
            username=admin["username"],
            full_name="Administrator",
            role="admin",
            is_admin=True,
        )
        return {
            "success": True,
            "token": token,
            "user": {
                "id": admin["id"],
                "username": admin["username"],
                "full_name": "Administrator",
                "role": "admin",
                "is_admin": True,
            },
        }

    # 2. Cek User Reguler (Mahasiswa/Dosen)
    cur.execute(
        """
        SELECT u.id, u.username, u.full_name, u.password_hash, r.name AS role_name
        FROM users u
        JOIN roles r ON r.id = u.role_id
        WHERE LOWER(u.username) = ?
        """,
        (username,),
    )
    user = cur.fetchone()
    conn.close()

    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau password salah.",
        )

    token = create_access_token(
        user_id=user["id"],
        username=user["username"],
        full_name=user["full_name"],
        role=user["role_name"],
        is_admin=False,
    )

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role_name"],
            "is_admin": False,
        },
    }


@app.get("/api/auth/me")
def get_me(user: dict = Depends(get_current_user)):
    return {"success": True, "user": user}


# ---------- Role Routes (Admin Only) ----------

@app.get("/api/roles")
def list_roles():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT r.id, r.name, r.description, r.created_at,
               COUNT(dr.document_id) AS document_count
        FROM roles r
        LEFT JOIN document_roles dr ON dr.role_id = r.id
        GROUP BY r.id
        ORDER BY r.id ASC
        """
    )
    roles = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"success": True, "roles": roles}


@app.post("/api/roles", status_code=status.HTTP_201_CREATED)
def create_role(payload: RoleCreateRequest, _admin: dict = Depends(get_current_admin)):
    role_name = payload.name.strip().lower()
    if not role_name:
        raise HTTPException(status_code=400, detail="Nama role tidak boleh kosong.")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM roles WHERE LOWER(name) = LOWER(?)", (role_name,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail=f"Role '{role_name}' sudah ada.")

    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        "INSERT INTO roles (name, description, created_at) VALUES (?, ?, ?)",
        (role_name, payload.description.strip(), now),
    )
    role_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "success": True,
        "role": {"id": role_id, "name": role_name, "description": payload.description.strip()},
    }


@app.put("/api/roles/{role_id}")
def update_role(role_id: int, payload: RoleUpdateRequest, _admin: dict = Depends(get_current_admin)):
    role_name = payload.name.strip().lower()
    if not role_name:
        raise HTTPException(status_code=400, detail="Nama role tidak boleh kosong.")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM roles WHERE id = ?", (role_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Role tidak ditemukan.")

    cur.execute("SELECT id FROM roles WHERE LOWER(name) = LOWER(?) AND id != ?", (role_name, role_id))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail=f"Nama role '{role_name}' sudah digunakan.")

    cur.execute(
        "UPDATE roles SET name = ?, description = ? WHERE id = ?",
        (role_name, payload.description.strip(), role_id),
    )
    conn.commit()
    conn.close()
    return {"success": True, "message": "Role berhasil diperbarui."}


@app.delete("/api/roles/{role_id}")
def delete_role(role_id: int, _admin: dict = Depends(get_current_admin)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM roles WHERE id = ?", (role_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Role tidak ditemukan.")

    if row["name"].lower() in {"mahasiswa", "admin"}:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Role dasar '{row['name']}' tidak boleh dihapus.")

    cur.execute("DELETE FROM roles WHERE id = ?", (role_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": f"Role '{row['name']}' berhasil dihapus."}


# ---------- Document Routes (Admin Only) ----------

@app.get("/api/documents")
def list_documents(_admin: dict = Depends(get_current_admin)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM documents ORDER BY id DESC")
    docs = [dict(r) for r in cur.fetchall()]

    for doc in docs:
        cur.execute(
            """
            SELECT r.id, r.name
            FROM roles r
            JOIN document_roles dr ON dr.role_id = r.id
            WHERE dr.document_id = ?
            ORDER BY r.name ASC
            """,
            (doc["id"],),
        )
        doc["roles"] = [dict(r) for r in cur.fetchall()]

    conn.close()
    return {"success": True, "documents": docs}


@app.get("/api/documents/{doc_id}/chunks")
def get_document_chunks(doc_id: int, _admin: dict = Depends(get_current_admin)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, filename, file_type, total_chunks FROM documents WHERE id = ?", (doc_id,))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

    cur.execute(
        "SELECT chunk_index, text FROM document_chunks WHERE document_id = ? ORDER BY chunk_index ASC",
        (doc_id,),
    )
    chunks = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"success": True, "document": dict(doc), "chunks": chunks}


@app.post("/api/documents/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(""),
    role_ids: str = Form(...),
    _admin: dict = Depends(get_current_admin),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="File wajib dipilih.")

    try:
        parsed_ids: List[int] = (
            json.loads(role_ids) if role_ids.strip().startswith("[")
            else [int(x.strip()) for x in role_ids.split(",") if x.strip()]
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Format role_ids tidak valid.")

    if not parsed_ids:
        raise HTTPException(status_code=400, detail="Pilih minimal satu role untuk dokumen ini.")

    file_bytes = await file.read()
    try:
        extracted_text = extract_text_from_bytes(file_bytes, file.filename)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))

    if not extracted_text:
        raise HTTPException(status_code=400, detail="Teks tidak dapat diekstrak dari dokumen ini.")

    chunks = chunk_text(extracted_text)
    if not chunks:
        raise HTTPException(status_code=400, detail="Dokumen tidak menghasilkan chunk teks.")

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename)
    stored_filename = f"{int(time.time())}_{safe_name}"
    (UPLOAD_DIR / stored_filename).write_bytes(file_bytes)

    doc_title = title.strip() or Path(file.filename).stem
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "txt"
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO documents (title, filename, file_type, total_chunks, is_active, uploaded_at) VALUES (?, ?, ?, ?, 1, ?)",
        (doc_title, stored_filename, ext, len(chunks), now),
    )
    doc_id = cur.lastrowid

    for rid in parsed_ids:
        cur.execute("SELECT id FROM roles WHERE id = ?", (rid,))
        if cur.fetchone():
            cur.execute(
                "INSERT OR IGNORE INTO document_roles (document_id, role_id) VALUES (?, ?)",
                (doc_id, rid),
            )

    for idx, ch in enumerate(chunks):
        emb = generate_embedding(ch)
        cur.execute(
            "INSERT INTO document_chunks (document_id, chunk_index, text, embedding_json) VALUES (?, ?, ?, ?)",
            (doc_id, idx, ch, json.dumps(emb)),
        )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": f"Dokumen '{doc_title}' berhasil diunggah ({len(chunks)} chunks).",
        "document_id": doc_id,
    }


@app.patch("/api/documents/{doc_id}/toggle")
def toggle_document_status(doc_id: int, _admin: dict = Depends(get_current_admin)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, is_active FROM documents WHERE id = ?", (doc_id,))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

    new_status = 0 if doc["is_active"] else 1
    cur.execute("UPDATE documents SET is_active = ? WHERE id = ?", (new_status, doc_id))
    conn.commit()
    conn.close()

    state_label = "diaktifkan" if new_status == 1 else "dinonaktifkan"
    return {
        "success": True,
        "is_active": bool(new_status),
        "message": f"Dokumen '{doc['title']}' berhasil {state_label}.",
    }


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int, _admin: dict = Depends(get_current_admin)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, filename, title FROM documents WHERE id = ?", (doc_id,))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

    cur.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()

    file_path = UPLOAD_DIR / doc["filename"]
    if file_path.exists():
        file_path.unlink(missing_ok=True)

    return {"success": True, "message": f"Dokumen '{doc['title']}' berhasil dihapus."}


# ---------- ChatGPT-Style Conversation Routes ----------

@app.get("/api/conversations")
def get_user_conversations(current_user: Optional[dict] = Depends(get_current_user_optional)):
    user_id = current_user.get("user_id") if current_user else None

    conn = get_connection()
    cur = conn.cursor()
    if user_id:
        cur.execute(
            """
            SELECT id, title, role_name, created_at, updated_at
            FROM chat_conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        )
    else:
        # Untuk pengguna tanpa login (tamu), ambil percakapan anonim terbaru
        cur.execute(
            """
            SELECT id, title, role_name, created_at, updated_at
            FROM chat_conversations
            WHERE user_id IS NULL
            ORDER BY updated_at DESC
            LIMIT 20
            """
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"success": True, "conversations": rows}


@app.post("/api/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreateRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    user_id = current_user.get("user_id") if current_user else None
    user_role = (current_user.get("role") if current_user else payload.role) or "mahasiswa"
    conv_id = f"chat_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    title = payload.title or "Percakapan Baru"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chat_conversations (id, user_id, title, role_name, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (conv_id, user_id, title, user_role, now, now),
    )
    conn.commit()
    conn.close()

    return {
        "success": True,
        "conversation": {
            "id": conv_id,
            "title": title,
            "role_name": user_role,
            "created_at": now,
            "updated_at": now,
        },
    }


@app.get("/api/conversations/{conv_id}")
def get_conversation_detail(conv_id: str, current_user: Optional[dict] = Depends(get_current_user_optional)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, title, role_name, created_at, updated_at FROM chat_conversations WHERE id = ?", (conv_id,))
    conv = cur.fetchone()
    if not conv:
        conn.close()
        raise HTTPException(status_code=404, detail="Percakapan tidak ditemukan.")

    cur.execute(
        """
        SELECT id, role, content, sources_json, created_at
        FROM chat_messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conv_id,),
    )
    messages = []
    for row in cur.fetchall():
        item = dict(row)
        try:
            item["sources"] = json.loads(item.get("sources_json") or "[]")
        except Exception:
            item["sources"] = []
        messages.append(item)

    conn.close()
    return {"success": True, "conversation": dict(conv), "messages": messages}


@app.patch("/api/conversations/{conv_id}")
def rename_conversation(
    conv_id: str,
    payload: ConversationRenameRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id FROM chat_conversations WHERE id = ?", (conv_id,))
    conv = cur.fetchone()
    if not conv:
        conn.close()
        raise HTTPException(status_code=404, detail="Percakapan tidak ditemukan.")

    now = datetime.now(timezone.utc).isoformat()
    cur.execute("UPDATE chat_conversations SET title = ?, updated_at = ? WHERE id = ?", (payload.title.strip(), now, conv_id))
    conn.commit()
    conn.close()
    return {"success": True, "message": "Judul percakapan berhasil diubah."}


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: str, current_user: Optional[dict] = Depends(get_current_user_optional)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_conversations WHERE id = ?", (conv_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": "Percakapan berhasil dihapus."}


@app.post("/api/conversations/{conv_id}/messages")
def send_conversation_message(
    conv_id: str,
    payload: SendMessageRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    question = payload.message.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Pesan tidak boleh kosong.")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, title, role_name FROM chat_conversations WHERE id = ?", (conv_id,))
    conv = cur.fetchone()
    if not conv:
        conn.close()
        raise HTTPException(status_code=404, detail="Percakapan tidak ditemukan.")

    role_name = (current_user.get("role") if current_user else conv["role_name"]) or "mahasiswa"
    now = datetime.now(timezone.utc).isoformat()

    # 1. Simpan User Message
    cur.execute(
        "INSERT INTO chat_messages (conversation_id, role, content, sources_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (conv_id, "user", question, "[]", now),
    )

    # 2. Update judul jika masih "Percakapan Baru"
    if conv["title"] == "Percakapan Baru":
        short_title = question[:45] + ("..." if len(question) > 45 else "")
        cur.execute("UPDATE chat_conversations SET title = ?, updated_at = ? WHERE id = ?", (short_title, now, conv_id))
    else:
        cur.execute("UPDATE chat_conversations SET updated_at = ? WHERE id = ?", (now, conv_id))

    conn.commit()
    conn.close()

    # 3. Jalankan RAG
    relevant_chunks = search_chunks_by_role(question, role_name, top_k=7, min_score=0.16)
    rag_result = generate_answer(question, role_name, relevant_chunks)
    answer = rag_result["answer"]
    sources = rag_result.get("sources", [])

    # 4. Simpan Assistant Message
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO chat_messages (conversation_id, role, content, sources_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (conv_id, "assistant", answer, json.dumps(sources), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    return {
        "success": True,
        "role": role_name,
        "answer": answer,
        "sources": sources,
    }


# ---------- Chat Route (Public / Standalone, Role-Filtered) ----------

@app.post("/api/chat")
def chat_with_rag(payload: ChatRequest, current_user: Optional[dict] = Depends(get_current_user_optional)):
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Pertanyaan tidak boleh kosong.")

    # Role ditentukan oleh user login jika ada, atau payload role, default 'mahasiswa'
    if current_user and current_user.get("role"):
        role_name = current_user["role"].strip().lower()
    else:
        role_name = (payload.role or "mahasiswa").strip().lower()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM roles WHERE LOWER(name) = LOWER(?)", (role_name,))
    role_row = cur.fetchone()
    conn.close()

    if not role_row:
        raise HTTPException(status_code=404, detail=f"Role '{role_name}' tidak ditemukan.")

    relevant_chunks = search_chunks_by_role(question, role_row["name"], top_k=7, min_score=0.16)
    result = generate_answer(question, role_row["name"], relevant_chunks)

    return {
        "success": True,
        "role": role_row["name"],
        "answer": result["answer"],
        "sources": result["sources"],
    }


def _parse_duration_seconds(dur: str) -> float:
    if not dur:
        return 0.0
    total = 0.0
    for val, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", dur.lower()):
        num = float(val)
        if unit == "h":
            total += num * 3600
        elif unit == "m":
            total += num * 60
        elif unit == "s":
            total += num
        elif unit == "ms":
            total += num / 1000.0
    return total


def _format_remaining_duration(seconds: float) -> str:
    if seconds <= 0:
        return "Sudah reset"
    secs = int(round(seconds))
    if secs < 60:
        return f"{secs}d"
    mins, rem_s = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m {rem_s}d" if rem_s else f"{mins}m"
    hours, rem_m = divmod(mins, 60)
    return f"{hours}j {rem_m}m"


@app.get("/api/admin/groq-quota")
def get_groq_quota(_admin: dict = Depends(get_current_admin)):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    configured_model = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    has_api_key = bool(api_key and api_key != "your_groq_api_key_here")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT model, limit_requests, remaining_requests, reset_requests,
               limit_tokens, remaining_tokens, reset_tokens, created_at
        FROM groq_usage_logs
        WHERE limit_requests IS NOT NULL OR limit_tokens IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """
    )
    latest_row = cur.fetchone()
    latest = dict(latest_row) if latest_row else None

    if latest:
        try:
            recorded_at = datetime.fromisoformat(latest["created_at"])
            elapsed = max(0.0, (datetime.now(timezone.utc) - recorded_at).total_seconds())
        except Exception:
            elapsed = 0.0

        req_reset_sec = _parse_duration_seconds(latest.get("reset_requests", ""))
        req_rem_sec = max(0.0, req_reset_sec - elapsed)
        if latest.get("limit_requests") and req_reset_sec > 0 and req_rem_sec <= 0:
            latest["remaining_requests"] = latest["limit_requests"]
            latest["reset_requests_label"] = "Sudah reset"
        else:
            latest["reset_requests_label"] = _format_remaining_duration(req_rem_sec) if req_reset_sec > 0 else "-"

        tok_reset_sec = _parse_duration_seconds(latest.get("reset_tokens", ""))
        tok_rem_sec = max(0.0, tok_reset_sec - elapsed)
        if latest.get("limit_tokens") and tok_reset_sec > 0 and tok_rem_sec <= 0:
            latest["remaining_tokens"] = latest["limit_tokens"]
            latest["reset_tokens_label"] = "Sudah reset"
        else:
            latest["reset_tokens_label"] = _format_remaining_duration(tok_rem_sec) if tok_reset_sec > 0 else "-"

    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cur.execute(
        """
        SELECT COUNT(*) AS requests_today,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens_today,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens_today,
               COALESCE(SUM(total_tokens), 0) AS total_tokens_today
        FROM groq_usage_logs
        WHERE created_at LIKE ?
        """,
        (f"{today_prefix}%",),
    )
    today_row = dict(cur.fetchone())

    cur.execute(
        """
        SELECT COUNT(*) AS total_requests,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM groq_usage_logs
        """
    )
    total_row = dict(cur.fetchone())

    cur.execute(
        """
        SELECT id, model, prompt_tokens, completion_tokens, total_tokens, status, created_at
        FROM groq_usage_logs
        ORDER BY id DESC
        LIMIT 10
        """
    )
    recent_logs = [dict(r) for r in cur.fetchall()]
    conn.close()

    return {
        "success": True,
        "has_api_key": has_api_key,
        "configured_model": configured_model,
        "latest": latest,
        "today": today_row,
        "total": total_row,
        "recent_logs": recent_logs,
    }


@app.get("/api/public/documents/{doc_id}")
def get_public_document(doc_id: int, current_user: Optional[dict] = Depends(get_current_user_optional)):
    role_name = (current_user.get("role") if current_user else "mahasiswa").lower()

    conn = get_connection()
    cur = conn.cursor()
    # Pastikan user yang meminta dokumen memiliki hak akses ke dokumen tersebut via document_roles
    cur.execute(
        """
        SELECT d.id, d.title, d.filename, d.file_type, d.total_chunks
        FROM documents d
        JOIN document_roles dr ON dr.document_id = d.id
        JOIN roles r ON r.id = dr.role_id
        WHERE d.id = ? AND (LOWER(r.name) = ? OR ? = 'admin') AND d.is_active = 1
        """,
        (doc_id, role_name, role_name),
    )
    doc = cur.fetchone()
    if not doc:
        conn.close()
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan atau tidak memiliki akses.")

    file_path = UPLOAD_DIR / doc["filename"]
    full_text = ""
    if file_path.exists():
        try:
            full_text = extract_text_from_bytes(file_path.read_bytes(), doc["filename"])
        except Exception:
            full_text = ""

    if not full_text:
        cur.execute(
            "SELECT text FROM document_chunks WHERE document_id = ? ORDER BY chunk_index ASC",
            (doc_id,),
        )
        full_text = "\n\n".join(r["text"] for r in cur.fetchall())

    conn.close()

    return {
        "success": True,
        "document": dict(doc),
        "url": f"/uploads/{doc['filename']}",
        "content": full_text,
    }
