# Speech to Text: real-time multilingual transcription and text-to-speech

A web app that transcribes speech **live, as you talk**, including code-mixed
**Hindi + English (Hinglish)** and other Indian languages. Every
transcription is stored in **PostgreSQL**, and a dashboard shows the history
with full **CRUD** (create, read, update, delete), search, filters and stats.

| Live transcription | Dashboard (CRUD) |
| --- | --- |
| Speak and see grey interim text firm up into final text in real time. The detected language is shown, Hindi stays in Devanagari and English stays in Latin script. | Search, filter by engine, edit, delete, create manually, copy, listen (text-to-speech), and see totals for words and audio minutes. |

## Speech engines

Pick an engine in the UI. All three use the same transcript view and storage.

| Engine | Where it runs | Notes |
| --- | --- | --- |
| **Gemini Live** (default) | Google cloud, free API key | Uses the Live API's dedicated `gemini-3.5-transcribe-live` model, which streams interim and final transcripts with automatic language detection. Any other Live model (for example `gemini-3.1-flash-live-preview`, named in the brief) can be chosen in Settings or with `GEMINI_LIVE_MODEL`. |
| **Whisper** (self-hosted, open source) | This server's CPU | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) `small` (int8). Made "live" with energy-based voice activity detection: the open utterance is re-decoded about every second for interim text, then decoded with beam search when the speaker pauses. No data leaves the server. |
| **Browser** | The user's browser (Chrome) | Web Speech API. A zero-setup fallback that needs no key or server compute. |

## Text-to-speech

Every transcript can be read aloud with 🔊 **Listen** or downloaded as a WAV
file (⬇). The voice is chosen in **Settings (⚙)**.

| Voice engine | Where it runs | Notes |
| --- | --- | --- |
| **Gemini TTS** (default) | Google cloud, same key | `gemini-3.8-flash-tts` with 30 voices. Detects Hindi, English and mixed text automatically. |
| **Piper** (self-hosted, open source) | This server's CPU | [Piper](https://github.com/OHF-Voice/piper1-gpl) Hindi voices *Priyamvada* (female) and *Pratham* (male). English-only text uses an English voice. Very fast (about 0.1 s per sentence on CPU). |
| **Browser** | The user's device | `speechSynthesis`. Free, and used automatically if the server voices fail. |

All voices were verified by sending their audio back through the app's own
speech recognition: each sentence was heard back exactly as written.

## Architecture

```
 Browser                                   FastAPI server                         Engines
┌──────────────────────────┐   WebSocket  ┌──────────────────────────────┐      ┌──────────────────────┐
│ mic → AudioWorklet        │  /ws/        │ live.py                      │      │ Gemini Live API      │
│ (48 kHz float → 16 kHz    │ transcribe   │  start msg → create_engine() │ ───► │  (cloud, streaming)  │
│  PCM16, 100 ms chunks)    │ ───────────► │  audio  → asyncio.Queue      │      ├──────────────────────┤
│                           │ ◄─────────── │  events ← engine.run(emit)   │ ───► │ faster-whisper       │
│ interim/final rendering   │  JSON events │                              │      │  (self-hosted, CPU)  │
│ auto-save ────────────────┼─── REST ───► │ transcriptions.py (CRUD)     │      └──────────────────────┘
│ dashboard (CRUD UI)       │  /api/...    │  SQLAlchemy 2 ──► PostgreSQL │
└──────────────────────────┘              └──────────────────────────────┘
```

* **Backend:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL (psycopg 3).
  Without `DATABASE_URL` it falls back to SQLite, so the app runs with no
  setup at all.
* **Frontend:** plain HTML, CSS and JS with no build step. Audio capture uses
  an `AudioWorklet` that resamples to 16 kHz mono PCM16, the format both
  engines expect. Audio captured while the engine is still connecting is
  buffered, so the first words are not lost.
* **Engine interface** (`app/engines/base.py`): an engine reads PCM chunks
  from a queue (`None` ends the stream) and emits `ready`, `interim`, `final`
  and `error` events. Adding a new provider means adding one class.

## Run it

### Option A: Docker (PostgreSQL included)

```bash
GEMINI_API_KEY=your-key docker compose up --build    # key is optional
# open http://localhost:8000
```

### Option B: Python

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # optional: set DATABASE_URL / GEMINI_API_KEY
uvicorn app.main:app --reload
# open http://localhost:8000
```

The first Whisper start downloads the model (about 480 MB for `small`).
Browsers only allow the microphone on `https://` or `localhost`.

A free Gemini key is available at <https://aistudio.google.com/apikey>. Put
it in `GEMINI_API_KEY`, or paste it into the app's **Settings (⚙)**, where it
stays in that browser only.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./transcriptions.db` | `postgresql://user:pass@host:5432/db` in production |
| `GEMINI_API_KEY` | *(empty)* | Server-side key; if empty, users can supply their own |
| `GEMINI_LIVE_MODEL` | `gemini-3.5-transcribe-live` | Any Gemini Live model |
| `WHISPER_ENABLED` | `true` | Turn the self-hosted engine on or off |
| `WHISPER_MODEL` | `small` | `tiny` / `base` / `small` / `medium` / `large-v3` (use `small`+ for Hindi) |
| `WHISPER_COMPUTE_TYPE` | `int8` | CTranslate2 quantisation |
| `WHISPER_PRELOAD` | `true` | Load the model at startup |
| `DEFAULT_ENGINE` | `gemini` | Engine selected for first-time visitors |
| `GEMINI_TTS_MODEL` | `gemini-3.8-flash-tts` | Gemini voice model (`gemini-3.8-flash-lite-tts` is cheaper) |
| `PIPER_ENABLED` | `true` | Turn the open-source voices on or off |
| `PIPER_VOICES_DIR` | `./.cache/piper` | Where voices are stored (downloaded on first use; built into the Docker image) |
| `PIPER_PRELOAD` | `true` | Load the voices at startup |
| `PIPER_MAX_LOADED` | `3` | Voices kept in memory at once (about 100 to 150 MB each; use `1` on 512 MB servers) |

## API

Interactive docs are served at **`/docs`** (Swagger UI).

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/transcriptions?q=&engine=&page=&page_size=` | List with search, filter and pagination (newest first) |
| `POST` | `/api/transcriptions` | Create (`text` required; `title` defaults to the first words) |
| `GET` | `/api/transcriptions/{id}` | Read one |
| `PATCH` | `/api/transcriptions/{id}` | Update `title`, `text` or `language` |
| `DELETE` | `/api/transcriptions/{id}` | Delete |
| `GET` | `/api/transcriptions/stats` | Totals for the dashboard |
| `POST` | `/api/tts` | Text-to-speech: `{"text", "engine": "gemini"\|"piper", "voice"}` returns `audio/wav` |
| `GET` | `/api/config` | Available engines, languages and voices |
| `WS` | `/ws/transcribe` | Real-time stream (protocol below) |

**WebSocket protocol**

1. Client sends `{"type":"start","engine":"gemini|whisper","language":"auto|hi-en|hi|en|…","api_key":"(optional)","model":"(optional)"}`.
2. Server sends `{"type":"ready"}` once the engine is listening.
3. Client sends binary frames of 16-bit little-endian PCM at 16 kHz mono.
4. Server streams `{"type":"interim","text":…}` (replace the current partial)
   and `{"type":"final","text":…,"language":…}` (append).
5. Client sends `{"type":"stop"}`. The server flushes the last results, then
   sends `{"type":"done"}`.

### Data model

`transcriptions(id, title, text, language, engine, duration_seconds, created_at, updated_at)`.
Tables are created automatically on startup.

## Tests

```bash
pytest -q
```

The tests cover the CRUD endpoints (validation, search, filter, pagination,
stats), the WebSocket protocol with a fake engine, and the text-to-speech
endpoint (Gemini mocked, Piper for real). The Gemini config
builder is also tested. During development the full stack was additionally
tested end to end: synthetic Hindi + English speech was streamed in real time
through the Docker image (PostgreSQL + Whisper), and the UI was driven in
headless Chromium with a fake microphone, covering record, auto-save, create,
edit, search and delete, and mobile layout.

## Deploying

* **Render:** [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/AjayKumarA250925/Procucev-AI-Tech-Hiring-Assignment)
  `render.yaml` is a Blueprint that creates a free PostgreSQL
  database and the Docker web service. Set `GEMINI_API_KEY` when asked. The
  free plan (512 MB RAM, a fraction of a CPU) runs Gemini speech recognition
  plus Gemini and Piper voices. Whisper is switched off there because it is
  too slow on that CPU; enable it on a larger plan.
* **Any Docker host** (Hugging Face Spaces, Railway, Fly.io, a VM): build the
  `Dockerfile` (listens on `$PORT`, default 8000) and set `DATABASE_URL` to a
  PostgreSQL instance (Neon, Supabase and Render all have free tiers). The
  image already contains the Whisper model, so the first transcription starts
  fast.

## Limitations and next steps

* Gemini Live transcription sessions are capped at 10 minutes, and the
  server stops a session at that point.
* Whisper on a small CPU trades accuracy for speed. A GPU, or the `medium`
  model, gives noticeably better Hindi.
* Next steps: user accounts (transcripts are currently shared by everyone
  using the instance), storing the audio next to the text, Alembic
  migrations, and speaker diarisation using Gemini's non-streaming
  transcription.
