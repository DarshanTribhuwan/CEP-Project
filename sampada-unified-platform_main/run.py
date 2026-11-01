#!/usr/bin/env python3
"""Zero-Setup Single Command Launcher for SAMPADA Unified Agricultural Platform.

Runs the complete system:
- FastAPI Backend on port 8000
- Groq AI Agronomist + DuckDuckGo Web Search API
- Multilingual Voice Agent (Marathi/Hindi/English)
- Farmer Onboarding & Decision Support System
- Modern Single-Page Web Interface at http://localhost:8000
"""

import os
import sys
import webbrowser
from pathlib import Path

# Add backend directory to sys.path
PROJECT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Change current working directory to backend so relative paths (e.g., sampada.db, config.toml) match
os.chdir(BACKEND_DIR)

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass

import uvicorn

def main():
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")

    print("=" * 70)
    print("🌾  SAMPADA UNIFIED AGRICULTURAL PLATFORM  🌾")
    print("=" * 70)
    print(f"🚀  FastAPI Backend Core:      http://127.0.0.1:{port}")
    print(f"🌐  Unified Web Interface:     http://localhost:{port}")
    print(f"📚  Interactive API Docs:      http://localhost:{port}/docs")
    print(f"🤖  AI Agronomist (Groq LLM):  Active with Groq LLM & DDG Search")
    print(f"🎙️   Multilingual Voice Agent:  Active (Browser-Native Web Speech API)")
    print(f"🗄️   Database Persistence:      sampada.db (SQLite)")
    print("=" * 70)
    print("Opening browser...")

    try:
        # Delay open slightly or open directly
        webbrowser.open(f"http://localhost:{port}")
    except Exception:
        pass

    # Start FastAPI server
    uvicorn.run("app.main:app", host=host, port=port, reload=False)

if __name__ == "__main__":
    main()
