# Custom AI Chatbot RAG Berbasis Role (RBAC) & ChatGPT Interface

Aplikasi website **AI Chatbot RAG Berbasis Role (RBAC)** dengan antarmuka bergaya **ChatGPT** yang dilengkapi fitur registrasi & login pengguna, riwayat percakapan tersimpan (*conversation history*), panel admin pengelolaan dokumen & role, ekstraksi dokumen multi-format (PDF, DOCX, XLSX, CSV, TXT, MD), serta integrasi **Groq Cloud LLM** dengan pemantauan kuota API secara real-time.

---

## 🌟 Fitur Utama

1. **Antarmuka Bergaya ChatGPT (Modern Sleek UI)**:
   - Sidebar di sisi kiri dengan daftar riwayat percakapan (*Conversation History*) yang dikelompokkan.
   - Tombol **+ Chat Baru** (shortcut: `Ctrl+K`).
   - Welcome screen dengan kartu rekomendasi pertanyaan (Ketentuan TAK 2025, Jam Operasional SSC, Alur Kontak SSC, dll).
   - Format jawaban Markdown aman (tabel responsif, link klik, daftar bernomor, kode, dan rumus matematika bersih).
   - Kartu sumber dokumen referensi interaktif dengan skor relevansi (%), dan saat diklik membuka modal pratinjau isi dokumen asli beserta tombol unduh.

2. **Sistem Autentikasi & Registrasi Multi-Role (RBAC)**:
   - **Registrasi Akun Baru**: Pengguna dapat mendaftarkan akun baru dengan memilih role (`Mahasiswa` atau `Dosen`).
   - **Login Akun**: Mendukung login Mahasiswa, Dosen, dan Administrator menggunakan JWT (JSON Web Token) dan hashing PBKDF2-SHA256.
   - **Tamu / Pengunjung**: Tetap dapat mencoba bertanya langsung tanpa login (mode Mahasiswa).
   - **Akun Uji Coba Default**:
     - Mahasiswa: `mahasiswa` / `mahasiswa123`
     - Dosen: `dosen` / `dosen123`
     - Administrator: `admin` / `admin123`

3. **Mesin RAG Berbasis Role (Role-Based Access Control)**:
   - Dokumen dikaitkan dengan satu atau beberapa role pada database SQLite (`document_roles`).
   - Pertanyaan diproses dengan memfilter dokumen aktif (`is_active = 1`) yang hanya berhak diakses oleh role pengguna.
   - *Table-aware chunking* menjaga keutuhan format tabel Markdown.
   - *Feature hashing embedding* 384 dimensi + *Cosine Similarity* + *Lexical & Phrase Bonus*.
   - Dokumen sensitif khusus dosen (seperti pedoman internal dosen) **tidak akan pernah muncul** saat ditanyakan oleh role mahasiswa.

4. **Panel Administrator (`/admin`)**:
   - **Knowledge Base**: Unggah dokumen baru (PDF, DOCX, XLSX, CSV, TXT, MD) dengan memilih role hak akses dokumen.
   - **Status Dokumen**: Aktifkan atau nonaktifkan dokumen secara instan tanpa menghapus data chunks.
   - **Lihat Chunks**: Popup untuk memeriksa potongan teks dokumen dan indeks chunking.
   - **Manajemen Role**: Tambah dan kelola role pengguna baru (memenuhi Tugas Praktikum #1).
   - **Uji Coba Chatbot**: Modal pengujian chatbot multi-role langsung dari panel admin.
   - **Tracking Kuota Groq API**: Pemantauan sisa kuota RPD (Request Per Day) dan TPM (Token Per Minute) dari header Groq, statistik harian, dan 10 log request terakhir.

---

## 📁 Struktur Direktori

```text
modul2ai/
├── app/
│   ├── __init__.py
│   ├── auth.py          # Autentikasi JWT, PBKDF2 password hashing & permission check
│   ├── database.py      # Skema SQLite, inisialisasi tabel RBAC, users, & conversations
│   ├── extractor.py     # Ekstraksi teks & tabel dari PDF, DOCX, XLSX, CSV, TXT, MD
│   ├── main.py          # API FastAPI, routing chat, percakapan, auth, & panel admin
│   └── rag.py           # Mesin RAG: chunking, embedding, cosine similarity, lexical bonus & Groq LLM
├── dataset/
│   ├── Alur_Menghubungi_SSC.txt
│   ├── SSC_Operating_Hours.txt
│   ├── SK-TAK-2022.pdf
│   ├── SK TAK TUNC 2025.pdf
│   └── Panduan_Internal_Dosen_Penilaian_TAK.txt
├── static/
│   ├── index.html       # Antarmuka Chatbot Utama gaya ChatGPT (Registrasi, Chat, History)
│   └── admin.html       # Dashboard Administrator (Knowledge Base, Roles, Quota Tracking)
├── uploads/             # Folder penyimpanan file dokumen asli yang diunggah
├── .env                 # Konfigurasi GROQ_API_KEY, JWT_SECRET, dan kredensial admin
├── requirements.txt     # Dependensi Python
├── run.bat              # Script sekali klik untuk menjalankan server di Windows
└── README.md
```

---

## 🚀 Cara Menjalankan Aplikasi

1. Buka folder `modul2ai` di terminal atau VS Code:
   ```bash
   cd "C:\Users\Raka Aditya\.gemini\antigravity\scratch\modul2ai"
   ```

2. Aktifkan virtual environment:
   ```bash
   .\venv\Scripts\activate
   ```

3. Jalankan server Uvicorn:
   ```bash
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```
   *(Atau cukup klik dua kali file `run.bat`)*

4. Akses melalui browser:
   - **Halaman Chatbot (Gaya ChatGPT)**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
   - **Dashboard Admin**: [http://127.0.0.1:8000/admin](http://127.0.0.1:8000/admin)
