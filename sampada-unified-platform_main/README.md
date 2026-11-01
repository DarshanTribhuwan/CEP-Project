# 🌾 SAMPADA Unified Agricultural Platform

> **A Community-Aware AI Agricultural Operating System** combining scientific agronomy rules, live weather & mandi market dynamics, Groq LLM intelligence, and multilingual voice advisory into one modern, production-grade web application.

---

## 🔍 Comparative Analysis: The Two Original Projects

| Feature / Dimension | Project 1: `CEP-Project-main` (SAMPADA AI) | Project 2: `crop_hackathon` (Smart Agronomist) | **Unified Platform (`sampada-unified-platform`)** |
| :--- | :--- | :--- | :--- |
| **Primary Architecture** | Modular FastAPI backend with SQLite persistence (`sampada.db`) | Single-file Streamlit script (`app.py`) | **High-performance FastAPI backend + Modern Single-Page Web Portal** |
| **User Interface** | Split across isolated static HTML files (`appui/index.html` & `voice/index.html`) | Monolithic vertical Streamlit layout | **Cohesive Single-Page App (SPA)** with Landing Page, Farmer Portal, Voice Agent & Chatbot tabs |
| **AI / LLM Integration** | Rules-based heuristics and offline model matrices | Groq API (`llama-3.3-70b-versatile`) with two-stage prompt workflow | **Groq Llama-3.3-70B** with streaming, automatic context injection from registered farm profile |
| **Voice Agent** | Standalone Marathi speech interface (Web Speech + Bhashini) | None (text-only) | **Integrated Multilingual Voice Agent** (मराठी, हिन्दी, English) with speech recognition & audio synthesis |
| **Soil Intelligence** | Behavioral soil questionnaire & soil degradation modeling | PDF/CSV Soil Health Card parser (`PyPDF2` / `pandas`) | **Both**: Questionnaire + automated Soil Health Card document extraction (pH, N, P, K) |
| **Live Web Search** | Weather & Mandi API providers | DuckDuckGo search (`DDGS`) for real-time agricultural news & weather | **Integrated**: Dual-mode weather/market radar + DDG live search toggle |
| **Data Visualization** | Raw key-value tables and status banners | Streamlit st.bar_chart & metrics | **Modern interactive Chart.js bar graphs & confidence meters** rendered inside chat bubbles |
| **Database** | Structured SQLite database (`farmers`, `farms`, `recommendations`, `outcomes`) | Flat JSON file (`chat_history.json`) | **SQLite (`sampada.db`)**: Farmer profiles, farm land, soil tests, outcomes, plus AI chat sessions |

---

## 🌟 Key Features of the Unified Platform

1. **Modern Responsive Landing Page**:
   - Hero banner with quick-action launch buttons.
   - Core feature cards with hover animations.
   - Honesty guarantee notice (confidence levels & estimates, never misleading promises).

2. **Farmer Onboarding & Decision Portal (6-Step Wizard)**:
   - **Step 1 (Farmer Account)**: Registration and PIN login with JWT storage.
   - **Step 2 (Farm Land & GPS)**: Acreage, irrigation type, water source, village, taluka, district, and **One-Click GPS Auto-Detection**.
   - **Step 3 (Soil Profile)**: Selection of soil types (Black cotton, Red loam, etc.) and drainage behavior.
   - **Step 4 (Weather & Mandi Radar)**: Real-time temperature, rainfall probability, monsoon stage, and commodity arrival rates.
   - **Step 5 (Ranked Recommendations)**: Suitability score (%), estimated profit/acre (₹), and confidence band for top crops.
   - **Step 6 (Harvest Outcome Feedback)**: Post-harvest yields and sale prices feeding the system's learning loop.

3. **Multilingual Voice Agent (मराठी / हिन्दी / English)**:
   - Circular microphone button with pulsating wave animation.
   - Web Speech API speech-to-text with rural accent resilience.
   - Spoken audio playback (Text-to-Speech) and preset Marathi/Hindi prompt chips.

4. **Smart Agronomist AI Chatbot (ChatGPT-Style)**:
   - Sidebar with chat session history and "➕ New Consultation" button.
   - Groq Llama 3.3 70B conversational reasoning.
   - **Automatic Farmer Context Injection**: Automatically knows the farmer's registered crops, land area, and district without re-typing.
   - Soil Health Card drag-and-drop file uploader (PDF/CSV).
   - Live DuckDuckGo agricultural search toggle.
   - Inline Chart.js bar graphs and confidence badges.

---

## 📂 Project Structure

```
sampada-unified-platform/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── ai_assistant.py     # Groq LLM, Soil card parser, DDG search, geocoding
│   │   │   ├── auth.py             # Farmer auth & PIN login
│   │   │   ├── farmer.py           # Farm registration & GPS
│   │   │   ├── soil.py             # Soil tests & behavior
│   │   │   ├── weather.py          # Weather forecasts
│   │   │   ├── market.py           # Mandi prices & MSP
│   │   │   ├── recommendation.py   # Crop suitability models
│   │   │   ├── outcomes.py         # Harvest feedback
│   │   │   └── health.py           # Provider health probes
│   │   ├── core/                   # DB, config, encryption, i18n
│   │   ├── services/               # Recommendation and soil engines
│   │   └── main.py                 # FastAPI app mounting unified frontend
│   ├── sampada.db                  # SQLite database (all existing tables & data)
│   ├── config.toml                 # Backend configuration
│   └── .env                        # GROQ_API_KEY & encryption keys
├── frontend/
│   ├── index.html                  # Single-page application
│   ├── css/
│   │   └── style.css               # Design system & dark/light accents
│   └── js/
│       ├── app.js                  # Master router & tab switcher
│       ├── farmer.js               # Onboarding wizard & API calls
│       ├── ai_chat.js              # ChatGPT interface & Chart.js renderer
│       ├── voice.js                # Speech recognition & TTS agent
│       └── i18n.js                 # English, Marathi, Hindi dictionaries
├── run.py                          # Zero-setup launcher
└── README.md                       # Documentation
```

---

## 🚀 Quick Start Guide

### 1. Requirements
Ensure Python 3.10+ is installed on your system.

### 2. Launch the Platform
From the project directory:

```bash
python3 run.py
```

This will automatically:
- Start the unified FastAPI backend on `http://localhost:8000`.
- Load the SQLite database (`sampada.db`).
- Open your default web browser to the modern platform.
- Serve interactive API documentation at `http://localhost:8000/docs`.
