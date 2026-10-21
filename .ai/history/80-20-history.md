## 2026-10-06 — Started SAMPADA AI Backend and Voice Frontend Servers

**Changed:**
Started the FastAPI backend server on port 8000 and the static voice frontend server on port 5173 in the background.

**Why:**
To enable live voice agent testing against the grounded backend APIs.

**How:**
- Backend: Ran Uvicorn app via `.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000`
- Frontend: Served static voice frontend using `npx serve -l 5173 .`

**Problem solved:**
Both backend API (`http://127.0.0.1:8000`) and voice assistant frontend UI (`http://localhost:5173`) are now live and accepting requests.

**Key concept:**
*Parallel Service Execution:* Independent frontend and backend micro-services running concurrently on local ports.

**Remember:**
Ensure backend APIs are active before testing grounded voice assistants.

## 2026-10-06 — Fixed Voice Response Pipeline Bugs & Barge-in Self-Interruption

**Changed:**
Fixed `app.js` sentence buffer flushing, barge-in self-interruption threshold, settings UI drawer toggle, and added TTS voice fallback in `tts.js`.

**Why:**

1. Grounded backend responses (which flag `final: true`) were causing `runTurn()` to call `buffer.flush()` without pushing the sentence first, discarding all spoken responses.
2. Barge-in monitor was triggering on the speaker's own voice output immediately (~80ms), instantly cancelling TTS playback.
3. Settings button listened to invalid `'toggle'` event instead of `'click'`.
4. Browser TTS failed when native Marathi voice pack was absent.

**How:**

- **Sentence Buffer:** Updated `runTurn` to preserve `chunk.sentence` on final chunks (`[...buffer.push(chunk.sentence), ...buffer.flush()]`).
- **Barge-in Threshold:** Increased RMS threshold to 0.25 and consecutive frames to 10.
- **Settings UI:** Fixed event listener to `'click'`.
- **TTS Fallback:** Added Hindi/English fallback voice selection in `pickVoice()`.

**Problem solved:**
The voice agent now correctly processes, logs, and speaks responses aloud in browser fallback and grounded backend modes.

**Key concept:**
*Buffer Preservation in Async Generators:* Ensure yielded final chunks are pushed into sentence buffers before flushing to prevent dropping streamed outputs.

**Remember:**
Always push new stream items to the sentence buffer before calling flush on final stream signals.

