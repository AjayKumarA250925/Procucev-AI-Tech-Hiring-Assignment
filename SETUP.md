# Setup guide

How to run the Speech to Text app on your own machine and how to deploy it
online. For what the app does and how it is built, see [README.md](README.md).

**Contents**

1. [What you need](#1-what-you-need)
2. [Get the code](#2-get-the-code)
3. [Get a free Gemini API key (optional)](#3-get-a-free-gemini-api-key-optional)
4. [Run it, option A: Docker (recommended)](#4-run-it-option-a-docker-recommended)
5. [Run it, option B: Python](#5-run-it-option-b-python)
6. [Try it out](#6-try-it-out)
7. [Run the tests](#7-run-the-tests)
8. [Deploy online for free (Render)](#8-deploy-online-for-free-render)
9. [Settings reference](#9-settings-reference)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What you need

| | Option A: Docker | Option B: Python |
| --- | --- | --- |
| Software | [Docker](https://docs.docker.com/get-docker/) with Docker Compose | **Python 3.12 or 3.13** (3.11 and older will not install), `git` |
| Database | Included (PostgreSQL container) | SQLite file by default; PostgreSQL optional |
| Disk | About 2 GB (image with the speech models) | About 1.5 GB (packages and models) |
| Memory | 2 GB free recommended | 2 GB free recommended |

Also needed:

* A recent browser: Chrome, Edge or Firefox.
* A microphone (only for speech-to-text).
* Internet access on first start, to download the models. The Gemini engines
  also need internet while you use them.

> The microphone only works on `http://localhost` or on an `https://` address.
> Opening the app from another device by IP address (`http://192.168.x.x:8000`)
> blocks the microphone. Text-to-speech still works there.

## 2. Get the code

```bash
git clone https://github.com/AjayKumarA250925/Procucev-AI-Tech-Hiring-Assignment.git
cd Procucev-AI-Tech-Hiring-Assignment
```

## 3. Get a free Gemini API key (optional)

The app works **without a key**:

* Speech-to-text uses the self-hosted Whisper engine.
* Text-to-speech uses the self-hosted Piper voices.

A key adds the Gemini engines. Gemini speech-to-text is the most accurate for
Hindi and mixed Hindi-English, and Gemini adds 30 extra voices.

1. Open <https://aistudio.google.com/apikey> and sign in with a Google account.
2. Click **Create API key** and copy it.
3. Create a file named `.env` in the project folder (next to `README.md`)
   containing:

   ```
   GEMINI_API_KEY=paste-your-key-here
   ```

`.env` is listed in `.gitignore`, so the key is never committed. Never put
the key in any other file. You can also skip this step and paste a key later
in the app under **Settings (⚙)**. A key entered there is stored only in that
browser.

> **Free tier limit:** Gemini voices (text-to-speech) allow only **10 requests
> per day per model**. When the limit is reached, the app says so and switches
> to the Piper voice automatically.

## 4. Run it, option A: Docker (recommended)

This runs the app and a PostgreSQL database together.

```bash
docker compose up -d --build
```

* The first build takes a few minutes, because it downloads Python packages,
  the Whisper model and the Piper voices into the image.
* Later starts take a few seconds.
* Docker Compose reads `GEMINI_API_KEY` from the `.env` file automatically.

Then open **<http://localhost:8000>**.

Useful commands:

```bash
docker compose ps                 # is it running?
docker compose logs -f app        # follow the app logs (Ctrl+C to stop following)
docker compose down               # stop (saved transcriptions are kept)
docker compose down -v            # stop AND delete the database
docker compose up -d --build      # rebuild after pulling new code
```

Port 8000 already in use? Change `"8000:8000"` to, for example,
`"8080:8000"` in `docker-compose.yml`, then open <http://localhost:8080>.

## 5. Run it, option B: Python

```bash
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Then open **<http://localhost:8000>**.

What happens on the first start:

* Saved transcriptions go to a local SQLite file, `transcriptions.db`.
* The Whisper model (about 480 MB) and the Piper voices (about 60 MB each)
  download automatically into `~/.cache/huggingface` and `./.cache/piper`.
  Most download in the background at startup; the rest on first use.
* The first transcription or voice may wait until those downloads finish.

**Using PostgreSQL instead of SQLite:** start a database, then add its
address to `.env`. The app creates the tables on startup.

```bash
docker run -d --name stt-postgres -e POSTGRES_USER=stt -e POSTGRES_PASSWORD=stt \
  -e POSTGRES_DB=stt -p 5432:5432 postgres:16-alpine
echo "DATABASE_URL=postgresql://stt:stt@localhost:5432/stt" >> .env
```

**Lighter install:** on a small machine you can skip the self-hosted engines.
Set these in `.env`:

```
WHISPER_ENABLED=false
PIPER_ENABLED=false
```

## 6. Try it out

The app has three tabs. **Settings (⚙)** holds the Gemini key and the default
engines and voices.

| Tab | What to do |
| --- | --- |
| **Speech to Text** | Pick an **Engine** (Gemini Live, Whisper or Browser) and a **Language** ("Hindi + English" for Hinglish). Click the mic, allow microphone access, and speak. Grey text is the live guess; it turns black when final. Click the mic again to stop. The text is saved automatically and can be played back with 🔊 **Listen**. |
| **Text to Speech** | Type or paste text, or click an example chip. Pick a **Voice engine** and **Voice**, then press 🔊 **Speak** (or Ctrl+Enter). Press **⏹ Stop** to stop. **⬇ Download** saves a `.wav` file, and **Save to history** adds the text to the Dashboard. |
| **Dashboard** | Saved transcriptions with search, an engine filter and paging. Each entry has **Edit**, **Delete**, **Copy**, 🔊 **Listen** and ⬇ **Download audio**. **+ New** adds one by hand. |

The REST API and its interactive documentation are at
<http://localhost:8000/docs>.

## 7. Run the tests

These are for option B, or any Python environment with
`requirements-dev.txt` installed:

```bash
pytest -q
```

The tests use a temporary SQLite database and simulate Gemini, so they need
no key and no internet. The Piper test runs once the voices exist in
`./.cache/piper`; download them with
`python scripts/download_voices.py .cache/piper`.

## 8. Deploy online for free (Render)

[Render](https://render.com) builds the Docker image straight from GitHub and
provides an `https://` address, which the microphone needs.

1. Open
   <https://render.com/deploy?repo=https://github.com/AjayKumarA250925/Procucev-AI-Tech-Hiring-Assignment>,
   or use the **Deploy to Render** button in the README.
2. Sign in with GitHub and allow access to the repository.
3. When asked for `GEMINI_API_KEY`, paste your key. You can also leave it
   empty and enter a key later in the app's Settings.
4. Click **Apply**. The blueprint (`render.yaml`) creates a free PostgreSQL
   database and the web service. The first build takes about 10 minutes.
5. Open the `https://<name>.onrender.com` address Render shows.

Limits of Render's free plan:

| Limit | Effect on this app |
| --- | --- |
| 512 MB memory, small CPU | Whisper is switched off. Speech-to-text uses Gemini, and voices use Piper and Gemini. Piper keeps one voice loaded at a time. To enable Whisper on a bigger plan, set `WHISPER_ENABLED=true` and `WHISPER_MODEL=small`. |
| Sleeps after 15 minutes without visitors | The next visit takes about a minute to wake up. |
| The free database expires after 30 days | Create a new one, or use a free [Neon](https://neon.tech) database and set `DATABASE_URL` to it. |

**Other hosts.** Any service that runs a Docker image works: Hugging Face
Spaces, Railway, Fly.io or a VM.

* Set `DATABASE_URL` to a PostgreSQL database.
* Set `GEMINI_API_KEY` if you want the Gemini engines.
* The container listens on the port given in `$PORT`, or 8000 if it isn't set.

**Netlify and Vercel's static hosting cannot run this app.** It needs a
long-running server for the live microphone connection and the speech models.

## 9. Settings reference

All settings are optional. Put them in `.env` (Python, Docker Compose) or in
the host's environment variables (Render).

| Variable | Default | What it does |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./transcriptions.db` | Database address, e.g. `postgresql://user:pass@host:5432/db` |
| `GEMINI_API_KEY` | *(empty)* | Server-side Gemini key. Without it, users can enter their own key in Settings |
| `GEMINI_LIVE_MODEL` | `gemini-3.5-transcribe-live` | Gemini speech-to-text model (`gemini-3.1-flash-live-preview` also works) |
| `DEFAULT_ENGINE` | `gemini` | Speech-to-text engine selected for first-time visitors |
| `WHISPER_ENABLED` | `true` | Self-hosted Whisper speech-to-text |
| `WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium` or `large-v3`. Use `small` or larger for Hindi |
| `WHISPER_PRELOAD` | `true` | Load Whisper at startup instead of on first use |
| `DEFAULT_TTS_ENGINE` | `piper` | Voice engine for first-time visitors |
| `GEMINI_TTS_MODEL` | `gemini-3.8-flash-lite-tts` | Gemini voice model. The other one is used as a fallback |
| `PIPER_ENABLED` | `true` | Self-hosted Piper voices |
| `PIPER_VOICES_DIR` | `./.cache/piper` | Where Piper voices are stored |
| `PIPER_PRELOAD` | `true` | Load the voices at startup |
| `PIPER_MAX_LOADED` | `3` | Voices kept in memory at once, about 100 to 150 MB each. Use `1` on 512 MB servers |

## 10. Troubleshooting

| Problem | Fix |
| --- | --- |
| **The mic button does nothing, or "Microphone needs HTTPS"** | Open the app on `http://localhost:8000`, not an IP address, or deploy with HTTPS. Check the browser's site permissions (the 🔒 or ⓘ icon in the address bar) and allow the microphone. |
| **"Gemini needs an API key"** | Add `GEMINI_API_KEY` to `.env` and restart, or paste a key in Settings (⚙). You can also pick the Whisper or Browser engine instead. |
| **"Gemini rejected the API key"** | The key is wrong or was deleted. Create a new one at <https://aistudio.google.com/apikey>. |
| **"Gemini's free voice limit is used up … resets in Nh"** | The free tier allows 10 voice requests per day per model. The app already switched to Piper. Wait for the reset, or use a paid key. |
| **The first Whisper or Piper use is slow** | The models are still downloading or loading (Python option only; the Docker image already contains them). Watch the logs, then try again. |
| **Speech stops after a word or two** | Press **Ctrl+Shift+R** to reload the latest version of the page. If you chose the **Browser voice** engine, switch to **Piper**: browser voices on Linux often cannot read Hindi script. |
| **No sound at all** | Check the system volume and output device. Click Speak again, because browsers only allow audio after you have interacted with the page. |
| **Hindi comes out in English letters, or the other way round** | Both are valid ways to write Hinglish. Try the language option "Hindi + English" or "Auto-detect", or switch between Gemini models in Settings. |
| **Port 8000 is already in use** | Docker: change the port mapping (see section 4). Python: `uvicorn app.main:app --port 8080`. |
| **`docker compose` is not found** | Older Docker installs use `docker-compose` (with a hyphen) instead. |
| **Out of memory on a small server** | Set `WHISPER_ENABLED=false` and `PIPER_MAX_LOADED=1`, or `PIPER_ENABLED=false`. |
