"""Smart Agronomist AI Assistant & Voice Intelligence API.

Merges the conversational LLM agronomy features from crop_hackathon (Groq LLM,
DuckDuckGo live agricultural search, Soil Health Card PDF/CSV parsing, dynamic
chart & confidence parsing) with SAMPADA's farmer & farm context.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..core.config import BACKEND_DIR, settings
from ..core.db import get_conn

logger = logging.getLogger("sampada.ai")

router = APIRouter(prefix="/ai", tags=["ai_assistant"])

# Resolve GROQ API key from env or config
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND_DIR / ".env")
        GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
    except Exception:
        pass

# Ensure chat tables exist in sampada.db
def _ensure_ai_tables():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_chats (
                id TEXT PRIMARY KEY,
                farmer_id INTEGER REFERENCES farmers(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                chat_stage TEXT NOT NULL DEFAULT 'gathering',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_messages (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL REFERENCES ai_chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                charts_json TEXT,
                metrics_json TEXT,
                created_at TEXT NOT NULL
            );
        """)

_ensure_ai_tables()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ChatCreateRequest(BaseModel):
    title: Optional[str] = "New Chat"
    farmer_id: Optional[int] = None
    language: Optional[str] = "en"

class ChatMessageRequest(BaseModel):
    chat_id: str
    prompt: str
    farmer_id: Optional[int] = None
    enable_web_search: bool = True
    location_override: Optional[str] = None
    soil_card_text: Optional[str] = None

class GeocodeRequest(BaseModel):
    latitude: float
    longitude: float


# ---------------------------------------------------------------------------
# Helpers: Live Web Search & Geocoding & Soil Parser
# ---------------------------------------------------------------------------

def perform_ddg_search(query: str, location: str) -> str:
    """Live search for agricultural weather, mandi prices, and crop alerts."""
    search_term = f"{query} {location} agriculture weather crop mandi price"
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(search_term, max_results=3))
            if results:
                return "\n".join([f"- {r.get('title')}: {r.get('body')}" for r in results])
    except Exception as exc:
        logger.warning("DuckDuckGo search error: %s", exc)
    return ""


def reverse_geocode(lat: float, lon: float) -> str:
    """Reverse geocode lat/lon into readable locality string."""
    try:
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent="sampada-agronomist-ai")
        loc_data = geolocator.reverse(f"{lat}, {lon}", timeout=5)
        if loc_data and loc_data.raw and loc_data.raw.get("address"):
            addr = loc_data.raw["address"]
            city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county") or "Unknown Area"
            state = addr.get("state", "India")
            district = addr.get("state_district") or addr.get("district")
            parts = [p for p in [city, district, state] if p]
            return ", ".join(parts)
    except Exception as exc:
        logger.warning("Geocoding failed for (%s, %s): %s", lat, lon, exc)
    return f"Lat: {lat:.4f}, Lon: {lon:.4f}"


def get_farmer_context(farmer_id: Optional[int]) -> dict[str, Any]:
    """Retrieve farmer profile & farm records from sampada.db if logged in."""
    if not farmer_id:
        return {}
    with get_conn() as conn:
        farmer_row = conn.execute(
            "SELECT id, name, language FROM farmers WHERE id = ?", (farmer_id,)
        ).fetchone()
        if not farmer_row:
            return {}
        
        farm_row = conn.execute(
            "SELECT * FROM farms WHERE farmer_id = ? ORDER BY id DESC LIMIT 1", (farmer_id,)
        ).fetchone()
        
        soil_row = None
        if farm_row:
            try:
                soil_row = conn.execute(
                    "SELECT * FROM soil_estimates WHERE farm_id = ? ORDER BY id DESC LIMIT 1", (farm_row["id"],)
                ).fetchone()
            except Exception:
                pass

        return {
            "name": farmer_row["name"],
            "language": farmer_row["language"],
            "farm": dict(farm_row) if farm_row else None,
            "soil": dict(soil_row) if soil_row else None,
        }


def build_system_prompt(
    location: str,
    stage: str,
    farmer_ctx: dict[str, Any],
    soil_card_context: str = "",
) -> str:
    farmer_str = ""
    if farmer_ctx:
        name = farmer_ctx.get("name", "Farmer")
        farm = farmer_ctx.get("farm") or {}
        soil = farmer_ctx.get("soil") or {}
        farm_label = farm.get('label') or 'Main Farm'
        acres = farm.get('land_area_input') or (round(farm.get('land_area_ha', 0) / 0.404686, 1) if farm.get('land_area_ha') else 'N/A')
        farmer_str = f"""
REGISTERED FARMER PROFILE:
- Farmer: {name}
- Farm: {farm_label} (Area: {acres} acres)
- District/Taluka/Village: {farm.get('district', '')}, {farm.get('taluka', '')}, {farm.get('village', '')}
- Water & Irrigation: {farm.get('irrigation_type', 'N/A')}, Water: {farm.get('water_source', 'N/A')}
- Soil Type Recorded: {soil.get('top_soil_type', 'Unspecified')}
"""

    base_prompt = f"""You are SAMPADA Smart Agronomist, a world-class AI agricultural advisor.
CRITICAL INSTRUCTION: DO NOT USE EMOJIS IN YOUR RESPONSES. Be professional, analytical, scientific, and structured.
The user is located in: {location}.
{farmer_str}
"""

    if stage == "gathering":
        stage_prompt = """
STAGE 1: DIRECT AGRICULTURAL CONSULTATION
- If the user asks a specific question (such as market prices, crop choice, fertilizer dosage, weather, or pest management), ANSWER IT DIRECTLY, CLEARLY, AND IMMEDIATELY.
- Do NOT refuse to answer or endlessly repeat questions asking about past plantation history.
- When answering market price or agronomic ranges, use clear natural language (e.g. "30 to 35 rupees" or "30 ते 35 रुपये") without confusing symbols or non-breaking characters.
- If the user asks for a comprehensive season plan and you have their profile, provide recommendations directly.
"""
    else:
        stage_prompt = """
STAGE 2: RECOMMENDATIONS AND ANALYSIS
Provide a detailed agricultural advisory:
1. Weather Analysis (current season, rainfall expectations, temperature).
2. Soil Health Assessment (nutrient deficiencies, organic matter suggestions).
3. Final Crop Recommendations with expected yield, market viability, and risk factors.

CRITICAL INSTRUCTION FOR VISUALS:
You MUST generate AT LEAST ONE chart and AT LEAST ONE confidence level metric in EVERY recommendation response.
Use the following exact syntax blocks hidden in your text (the frontend parses them into interactive UI charts):

For a bar chart:
[CHART: {"title": "Crop Suitability Index (%)", "data": {"Soybean": 88, "Cotton": 82, "Tur (Pigeon Pea)": 75, "Maize": 68}}]

For a confidence level metric:
[CONFIDENCE: {"label": "Recommendation Confidence", "value": "94%"}]

Ensure the JSON inside the tags is strictly valid.
"""

    data_context = f"""
[SOIL HEALTH CARD CONTEXT]
{soil_card_context if soil_card_context else "None uploaded."}
"""
    return base_prompt + stage_prompt + data_context


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@router.get("/chats", summary="List chat sessions")
def list_chats(farmer_id: Optional[int] = Query(None)):
    with get_conn() as conn:
        if farmer_id:
            rows = conn.execute(
                "SELECT * FROM ai_chats WHERE farmer_id = ? ORDER BY updated_at DESC", (farmer_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ai_chats ORDER BY updated_at DESC LIMIT 50"
            ).fetchall()
        return [dict(r) for r in rows]


@router.post("/chats", summary="Create new chat session")
def create_chat(payload: ChatCreateRequest):
    chat_id = str(uuid.uuid4())
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ai_chats (id, farmer_id, title, chat_stage, created_at, updated_at)
               VALUES (?, ?, ?, 'gathering', ?, ?)""",
            (chat_id, payload.farmer_id, payload.title or "New Chat", now, now)
        )
        # Seed initial assistant welcome message
        welcome_id = str(uuid.uuid4())
        initial_msg = (
            "Namaskar. I am SAMPADA Smart Agronomist AI.\n\n"
            "To give you accurate, tailored recommendations for this season, please share:\n"
            "1. Your recent plantation history (what crops have you grown previously?)\n"
            "2. Your soil type (or upload your Soil Health Card / let me infer from your location)\n\n"
            "You can also ask about weather forecasts, disease management, and mandi market prices!"
        )
        conn.execute(
            """INSERT INTO ai_messages (id, chat_id, role, content, charts_json, metrics_json, created_at)
               VALUES (?, ?, 'assistant', ?, '[]', '[]', ?)""",
            (welcome_id, chat_id, initial_msg, now)
        )
    return {"id": chat_id, "title": payload.title, "chat_stage": "gathering"}


@router.get("/chats/{chat_id}", summary="Get chat history")
def get_chat(chat_id: str):
    with get_conn() as conn:
        chat = conn.execute("SELECT * FROM ai_chats WHERE id = ?", (chat_id,)).fetchone()
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        messages = conn.execute(
            "SELECT * FROM ai_messages WHERE chat_id = ? ORDER BY created_at ASC", (chat_id,)
        ).fetchall()
        
        parsed_msgs = []
        for m in messages:
            msg_dict = dict(m)
            try:
                msg_dict["charts"] = json.loads(msg_dict.get("charts_json") or "[]")
            except Exception:
                msg_dict["charts"] = []
            try:
                msg_dict["metrics"] = json.loads(msg_dict.get("metrics_json") or "[]")
            except Exception:
                msg_dict["metrics"] = []
            parsed_msgs.append(msg_dict)

        return {"chat": dict(chat), "messages": parsed_msgs}


@router.delete("/chats/{chat_id}", summary="Delete chat session")
def delete_chat(chat_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM ai_chats WHERE id = ?", (chat_id,))
    return {"status": "deleted"}


@router.post("/chat", summary="Send message to Groq Agronomist LLM")
async def chat_message(payload: ChatMessageRequest):
    """Processes user prompt with Groq Llama-3.3-70B, live web search, and chart parsing."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    
    with get_conn() as conn:
        chat = conn.execute("SELECT * FROM ai_chats WHERE id = ?", (payload.chat_id,)).fetchone()
        if not chat:
            # Auto-create if not exists
            conn.execute(
                """INSERT INTO ai_chats (id, farmer_id, title, chat_stage, created_at, updated_at)
                   VALUES (?, ?, 'Agri Consultation', 'gathering', ?, ?)""",
                (payload.chat_id, payload.farmer_id, now, now)
            )
            chat = conn.execute("SELECT * FROM ai_chats WHERE id = ?", (payload.chat_id,)).fetchone()
        
        chat_stage = chat["chat_stage"]

        # Fetch previous messages for conversation context
        history_rows = conn.execute(
            "SELECT role, content FROM ai_messages WHERE chat_id = ? ORDER BY created_at ASC",
            (payload.chat_id,)
        ).fetchall()

    # Determine location context
    farmer_ctx = get_farmer_context(payload.farmer_id)
    user_location = payload.location_override
    if not user_location and farmer_ctx and farmer_ctx.get("farm"):
        farm = farmer_ctx["farm"]
        user_location = f"{farm.get('village', '')}, {farm.get('taluka', '')}, {farm.get('district', '')}, Maharashtra"
    if not user_location:
        user_location = "Maharashtra, India"

    # Construct prompt messages
    sys_prompt = build_system_prompt(
        location=user_location,
        stage=chat_stage,
        farmer_ctx=farmer_ctx,
        soil_card_context=payload.soil_card_text or "",
    )
    
    api_messages = [{"role": "system", "content": sys_prompt}]
    for h in history_rows:
        api_messages.append({"role": h["role"], "content": h["content"]})
    
    # Save user message to database
    user_msg_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ai_messages (id, chat_id, role, content, charts_json, metrics_json, created_at)
               VALUES (?, ?, 'user', ?, '[]', '[]', ?)""",
            (user_msg_id, payload.chat_id, payload.prompt, now)
        )
        if chat["title"] == "New Chat":
            short_title = payload.prompt[:30] + ("..." if len(payload.prompt) > 30 else "")
            conn.execute("UPDATE ai_chats SET title = ? WHERE id = ?", (short_title, payload.chat_id))

    # Append current user prompt
    api_messages.append({"role": "user", "content": payload.prompt})

    # Optional Live Web Search
    search_context = ""
    if payload.enable_web_search and (chat_stage == "recommending" or "weather" in payload.prompt.lower() or "price" in payload.prompt.lower() or "mandi" in payload.prompt.lower()):
        web_results = perform_ddg_search(payload.prompt, user_location)
        if web_results:
            search_context = f"\n\n[LIVE WEB SEARCH RESULTS]\n{web_results}"
            api_messages[-1]["content"] += search_context

    # Query Groq API
    api_key = GROQ_API_KEY
    if not api_key:
        api_key = os.environ.get("GROQ_API_KEY", "")

    if not api_key:
        fallback_reply = (
            "Groq API Key is not configured yet. Please configure GROQ_API_KEY in your .env file.\n\n"
            "In offline mode, SAMPADA recommends: For Maharashtra conditions, Soybean, Cotton, and Maize "
            "are currently the most viable crops based on seasonal soil moisture and MSP pricing."
        )
        return {"content": fallback_reply, "charts": [], "metrics": [], "chat_stage": chat_stage}

    groq_model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": groq_model,
                    "messages": api_messages,
                    "temperature": 0.7,
                    "max_tokens": 2048,
                },
            )
            
            if resp.status_code != 200:
                logger.error("Groq API returned error %s: %s", resp.status_code, resp.text)
                raise HTTPException(status_code=502, detail=f"Groq API error: {resp.text}")

            result = resp.json()
            full_response = result["choices"][0]["message"]["content"]

    except Exception as exc:
        logger.exception("Groq communication failure: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Agronomist LLM: {exc}")

    # Parse inline charts and metrics
    charts: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []

    chart_matches = re.findall(r'\[CHART:\s*({.*?})\s*\]', full_response, re.DOTALL)
    for match in chart_matches:
        try:
            charts.append(json.loads(match))
        except Exception:
            pass

    metric_matches = re.findall(r'\[CONFIDENCE:\s*({.*?})\s*\]', full_response, re.DOTALL)
    for match in metric_matches:
        try:
            metrics.append(json.loads(match))
        except Exception:
            pass

    # Clean display text
    clean_text = re.sub(r'\[CHART:\s*{.*?}\s*\]', '', full_response, flags=re.DOTALL)
    clean_text = re.sub(r'\[CONFIDENCE:\s*{.*?}\s*\]', '', clean_text, flags=re.DOTALL)
    clean_text = clean_text.strip()

    # Stage transition check
    new_stage = chat_stage
    if chat_stage == "gathering" and "I have enough information" in full_response:
        new_stage = "recommending"
        clean_text += "\n\n*(SAMPADA has sufficient data and has transitioned to Stage 2: In-Depth Recommendations & Visual Charts)*"

    # Save assistant response to DB
    assistant_msg_id = str(uuid.uuid4())
    now_resp = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ai_messages (id, chat_id, role, content, charts_json, metrics_json, created_at)
               VALUES (?, ?, 'assistant', ?, ?, ?, ?)""",
            (assistant_msg_id, payload.chat_id, clean_text, json.dumps(charts), json.dumps(metrics), now_resp)
        )
        conn.execute(
            "UPDATE ai_chats SET chat_stage = ?, updated_at = ? WHERE id = ?",
            (new_stage, now_resp, payload.chat_id)
        )

    return {
        "content": clean_text,
        "charts": charts,
        "metrics": metrics,
        "chat_stage": new_stage,
        "chat_id": payload.chat_id,
    }


@router.post("/upload-soil", summary="Upload and extract Soil Health Card PDF/CSV")
async def upload_soil(file: UploadFile = File(...)):
    """Extracts text, NPK values, and pH from uploaded Soil Health Cards."""
    filename = file.filename or "unknown"
    content = await file.read()
    extracted_text = ""
    parsed_nutrients = {}

    try:
        if filename.lower().endswith(".csv"):
            import pandas as pd
            df = pd.read_csv(io.BytesIO(content))
            extracted_text = df.to_string()
        elif filename.lower().endswith(".pdf"):
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(content))
            for page in reader.pages:
                extracted_text += page.extract_text() + "\n"
        else:
            extracted_text = content.decode("utf-8", errors="ignore")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {exc}")

    # Basic regex heuristic for N, P, K, pH
    ph_match = re.search(r'pH\s*[:=]?\s*([0-9\.]+)', extracted_text, re.IGNORECASE)
    if ph_match:
        parsed_nutrients["pH"] = float(ph_match.group(1))

    n_match = re.search(r'(?:Nitrogen|N)\s*[:=]?\s*([0-9\.]+)', extracted_text, re.IGNORECASE)
    if n_match:
        parsed_nutrients["Nitrogen"] = float(n_match.group(1))

    p_match = re.search(r'(?:Phosphorus|P)\s*[:=]?\s*([0-9\.]+)', extracted_text, re.IGNORECASE)
    if p_match:
        parsed_nutrients["Phosphorus"] = float(p_match.group(1))

    k_match = re.search(r'(?:Potassium|Potash|K)\s*[:=]?\s*([0-9\.]+)', extracted_text, re.IGNORECASE)
    if k_match:
        parsed_nutrients["Potassium"] = float(k_match.group(1))

    return {
        "filename": filename,
        "text_preview": extracted_text[:1000],
        "extracted_text": extracted_text,
        "nutrients": parsed_nutrients,
    }


@router.post("/geocode", summary="Reverse geocode coordinates")
def geocode_location(payload: GeocodeRequest):
    readable = reverse_geocode(payload.latitude, payload.longitude)
    return {"latitude": payload.latitude, "longitude": payload.longitude, "formatted_address": readable}


