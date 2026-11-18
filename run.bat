@echo off
cd /d "%~dp0"
title Chatbot RAG Berbasis RBAC - FastAPI
echo ===================================================================
echo Custom AI Chatbot RAG Berbasis Role (RBAC) & ChatGPT Interface
echo ===================================================================
echo Halaman Chatbot (Gaya ChatGPT) : http://127.0.0.1:8000
echo Halaman Dashboard Admin        : http://127.0.0.1:8000/admin
echo.
echo Akun Default:
echo   - Mahasiswa : mahasiswa / mahasiswa123
echo   - Dosen     : dosen / dosen123
echo   - Admin     : admin / admin123
echo ===================================================================
echo Menjalankan Uvicorn...
call .\venv\Scripts\activate.bat
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
pause
