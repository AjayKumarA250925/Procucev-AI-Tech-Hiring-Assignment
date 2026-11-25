// Speech to Text - front end (no build step, plain ES modules).
const $ = (id) => document.getElementById(id);
const store = {
  get(k, d = "") { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

let config = { engines: {}, languages: { auto: "Auto-detect" } };

// ---------------------------------------------------------------- utilities
function toast(msg, isError = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.className = "toast"), isError ? 5000 : 2500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : j.detail?.[0]?.msg || detail;
    } catch { /* not json */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

const escapeHtml = (s) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const fmtClock = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

// ---------------------------------------------------------------- text-to-speech
// Server voices are streamed: raw PCM chunks are scheduled on a Web Audio
// clock as they arrive, so playback starts after the first chunk instead of
// after the whole file has been generated.
const tts = { button: null, abort: null, ctx: null, firstAudioMs: null };

function ttsSettings() {
  return {
    engine: store.get("tts_engine", config.tts?.default || "piper"),
    voice: store.get("tts_voice"),
  };
}

function stopSpeaking() {
  if (tts.abort) { tts.abort.abort(); tts.abort = null; }
  if (tts.ctx) { tts.ctx.close().catch(() => {}); tts.ctx = null; }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  if (tts.button) { tts.button.textContent = tts.button.dataset.label; tts.button = null; }
}

function setSpeakingButton(btn, label) {
  if (!btn) return;
  btn.dataset.label ??= btn.textContent;
  btn.textContent = label;
  tts.button = btn;
}

function browserSpeak(text, btn) {
  if (!("speechSynthesis" in window)) throw new Error("This browser cannot speak text.");
  const u = new SpeechSynthesisUtterance(text);
  // Devanagari script -> Hindi voice, otherwise Indian English (reads Hinglish well).
  u.lang = /[\u0900-\u097F]/.test(text) ? "hi-IN" : "en-IN";
  const voice = speechSynthesis.getVoices().find((v) => v.lang.replace("_", "-") === u.lang);
  if (voice) u.voice = voice;
  u.onend = u.onerror = () => { if (tts.button === btn) stopSpeaking(); };
  setSpeakingButton(btn, "⏹ Stop");
  speechSynthesis.speak(u);
}

async function errorDetail(res) {
  let detail = res.statusText || "Speech failed";
  try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
  return typeof detail === "string" ? detail : "Speech failed";
}

// Whole WAV file (used for downloads). Served from the server cache after a stream.
async function fetchSpeech(text) {
  const { engine, voice } = ttsSettings();
  const res = await fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, engine, voice, api_key: store.get("gemini_api_key") || null }),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.blob();
}

async function streamSpeech(text, engine, voice, btn) {
  const started = performance.now();
  const ctrl = new AbortController();
  tts.abort = ctrl;
  const res = await fetch("/api/tts/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, engine, voice, api_key: store.get("gemini_api_key") || null }),
    signal: ctrl.signal,
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  const rate = Number(res.headers.get("X-Sample-Rate")) || 24000;
  const ctx = new AudioContext();
  tts.ctx = ctx;
  if (ctx.state === "suspended") await ctx.resume();

  const minBytes = Math.round(rate * 0.12) * 2; // schedule >= 120 ms at a time (no clicks)
  let pending = new Uint8Array(0);
  let at = 0;
  let last = null;
  const play = (bytes) => {
    const n = bytes.length >> 1;
    const view = new DataView(bytes.buffer, bytes.byteOffset, n * 2);
    const buffer = ctx.createBuffer(1, n, rate);
    const out = buffer.getChannelData(0);
    for (let i = 0; i < n; i++) out[i] = view.getInt16(i * 2, true) / 32768;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    at = Math.max(at, ctx.currentTime + 0.03);
    src.start(at);
    at += buffer.duration;
    if (!last) {
      tts.firstAudioMs = Math.round(performance.now() - started);
      setSpeakingButton(btn, "⏹ Stop");
    }
    last = src;
  };

  const reader = res.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (tts.ctx !== ctx) return; // stopped by the user
    if (value) {
      const merged = new Uint8Array(pending.length + value.length);
      merged.set(pending);
      merged.set(value, pending.length);
      pending = merged;
    }
    if (pending.length >= minBytes || (done && pending.length >= 2)) {
      const even = pending.length & ~1;
      play(pending.subarray(0, even));
      pending = pending.slice(even);
    }
    if (done) break;
  }
  tts.abort = null;
  const finish = () => { if (tts.ctx === ctx) stopSpeaking(); };
  if (!last || at <= ctx.currentTime) finish();
  else last.onended = finish;
}

async function speak(text, btn = null) {
  const playing = tts.button === btn && btn !== null;
  stopSpeaking();
  if (playing || !text) return; // second click = stop
  const { engine, voice } = ttsSettings();
  try {
    if (engine === "browser") return browserSpeak(text, btn);
    setSpeakingButton(btn, "⏳");
    await streamSpeech(text, engine, voice, btn);
  } catch (err) {
    if (err.name === "AbortError" || (btn && tts.button !== btn)) return; // user stopped
    stopSpeaking();
    const usePiper = engine === "gemini" && config.tts?.piper?.enabled;
    toast(`${err.message} ${usePiper ? "Using the Piper voice instead." : "Using the browser voice instead."}`, true);
    try {
      if (!usePiper) return browserSpeak(text, btn);
      setSpeakingButton(btn, "⏳");
      await streamSpeech(text, "piper", "", btn);
    } catch (err2) {
      if (err2.name === "AbortError" || (btn && tts.button !== btn)) return;
      stopSpeaking();
      try { browserSpeak(text, btn); } catch { /* nothing else to try */ }
    }
  }
}

async function downloadSpeech(text, title, btn) {
  if (ttsSettings().engine === "browser") {
    return toast("Pick the Gemini or Piper voice in Settings to download audio.", true);
  }
  const label = btn.textContent;
  btn.textContent = "⏳";
  btn.disabled = true;
  try {
    const blob = await fetchSpeech(text);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (title || "speech").replace(/[^\p{L}\p{M}\p{N} _-]+/gu, "").trim().slice(0, 60) + ".wav";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (err) {
    toast("Could not create audio: " + err.message, true);
  } finally {
    btn.textContent = label;
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- navigation
document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
    $(`view-${tab.dataset.view}`).classList.remove("hidden");
    stopSpeaking();
    if (tab.dataset.view === "history") loadHistory();
    if (tab.dataset.view === "speak") $("speakText").focus();
  })
);

// ---------------------------------------------------------------- live transcription
const live = {
  recording: false,
  finals: [],          // settled segments
  interim: "",
  languages: new Set(),
  startedAt: 0,
  seconds: 0,
  engineUsed: "",
  ws: null, ctx: null, stream: null, recognition: null, tick: null,
};

function renderTranscript() {
  const finalText = live.finals.join(" ").replace(/\s+([,.!?।])/g, "$1");
  $("finalText").textContent = finalText;
  $("interimText").textContent = (finalText && live.interim ? " " : "") + live.interim;
  const has = Boolean(finalText || live.interim);
  $("placeholder").classList.toggle("hidden", has);
  const box = $("transcript");
  box.scrollTop = box.scrollHeight;
  const done = Boolean(finalText) && !live.recording;
  ["speakBtn", "copyBtn", "clearBtn", "saveBtn"].forEach((id) => ($(id).disabled = !done));
  const langs = [...live.languages].filter(Boolean);
  $("langBadge").textContent = langs.length ? "Detected: " + langs.join(" + ") : "";
  $("langBadge").classList.toggle("hidden", !langs.length);
}

function setStatus(text, state = "") {
  $("statusText").textContent = text;
  $("statusDot").className = "dot " + state;
}

function setMicState(state) {
  const btn = $("micBtn");
  btn.classList.toggle("recording", state === "recording");
  btn.classList.toggle("busy", state === "busy");
  btn.setAttribute("aria-label", state === "recording" ? "Stop recording" : "Start recording");
  ["engineSelect", "languageSelect"].forEach((id) => ($(id).disabled = state !== "idle"));
}

function addFinal(text, language) {
  text = (text || "").trim();
  if (text) live.finals.push(text);
  (language || "").split(",").forEach((l) => l && live.languages.add(l.trim().split("-")[0]));
  live.interim = "";
  renderTranscript();
}

function startTimer() {
  live.startedAt = performance.now();
  live.tick = setInterval(() => {
    live.seconds = (performance.now() - live.startedAt) / 1000;
    $("timer").textContent = fmtClock(live.seconds);
  }, 250);
}

async function startRecording() {
  const engine = $("engineSelect").value;
  live.finals = []; live.interim = ""; live.languages = new Set(); live.seconds = 0;
  live.engineUsed = engine;
  $("timer").textContent = "00:00";
  renderTranscript();
  setMicState("busy");
  try {
    if (engine === "browser") startBrowserRecognition();
    else await startStreaming(engine);
  } catch (err) {
    cleanup();
    setMicState("idle");
    setStatus(err.message || String(err), "err");
    toast(err.message || String(err), true);
  }
}

async function startStreaming(engine) {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("Microphone needs HTTPS (or localhost).");
  setStatus(engine === "whisper" ? "Loading Whisper model…" : "Connecting to Gemini Live…");
  live.stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  live.ctx = new AudioContext();
  await live.ctx.audioWorklet.addModule("/static/pcm-worklet.js");
  const source = live.ctx.createMediaStreamSource(live.stream);
  const node = new AudioWorkletNode(live.ctx, "pcm-worklet");
  const mute = live.ctx.createGain();
  mute.gain.value = 0;
  source.connect(node).connect(mute).connect(live.ctx.destination); // keeps the graph running

  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/transcribe`);
  ws.binaryType = "arraybuffer";
  live.ws = ws;
  let ready = false;
  const pending = []; // audio captured while the engine connects - not lost

  node.port.onmessage = ({ data }) => {
    $("ring").style.transform = `scale(${1 + Math.min(data.level * 2.5, 0.6)})`;
    if (ws.readyState !== WebSocket.OPEN) return;
    if (ready) ws.send(data.pcm);
    else if (pending.length < 300) pending.push(data.pcm);
  };

  ws.onopen = () =>
    ws.send(JSON.stringify({
      type: "start",
      engine,
      language: $("languageSelect").value,
      api_key: store.get("gemini_api_key"),
      model: store.get("gemini_model"),
    }));

  ws.onmessage = ({ data }) => {
    const ev = JSON.parse(data);
    if (ev.type === "ready") {
      ready = true;
      pending.splice(0).forEach((c) => ws.send(c));
      live.recording = true;
      setMicState("recording");
      setStatus(`Listening… (${ev.engine} · ${ev.model})`, "live");
      startTimer();
    } else if (ev.type === "interim") {
      live.interim = ev.text;
      renderTranscript();
    } else if (ev.type === "final") {
      addFinal(ev.text, ev.language);
    } else if (ev.type === "error") {
      toast(ev.message, true);
      setStatus(ev.message, "err");
      live.failed = true;
    } else if (ev.type === "done") {
      finishSession();
    }
  };
  ws.onclose = () => { if (live.ws === ws) finishSession(); };
}

function startBrowserRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) throw new Error("This browser has no built-in speech recognition. Use Chrome or another engine.");
  const map = { auto: navigator.language || "en-IN", "hi-en": "hi-IN", en: "en-IN", hi: "hi-IN", bn: "bn-IN",
                ta: "ta-IN", te: "te-IN", mr: "mr-IN", gu: "gu-IN", kn: "kn-IN" };
  const rec = new SR();
  rec.lang = map[$("languageSelect").value] || "en-IN";
  rec.continuous = true;
  rec.interimResults = true;
  rec.onstart = () => {
    if (live.recording) return;
    live.recording = true;
    setMicState("recording");
    setStatus(`Listening… (browser · ${rec.lang})`, "live");
    startTimer();
  };
  rec.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) addFinal(r[0].transcript, rec.lang);
      else interim += r[0].transcript;
    }
    live.interim = interim;
    renderTranscript();
  };
  rec.onerror = (e) => {
    if (e.error === "no-speech" || e.error === "aborted") return;
    toast(`Speech recognition error: ${e.error}`, true);
    live.failed = true;
  };
  rec.onend = () => {
    // Chrome stops after a pause; restart until the user presses stop.
    if (live.recognition === rec && live.recording && !live.stopping) rec.start();
    else finishSession();
  };
  live.recognition = rec;
  rec.start();
}

function stopRecording() {
  live.stopping = true;
  setMicState("busy");
  setStatus("Finishing…");
  if (live.stream) live.stream.getTracks().forEach((t) => t.stop());
  if (live.ctx) live.ctx.close();
  live.stream = live.ctx = null;
  if (live.ws?.readyState === WebSocket.OPEN) live.ws.send(JSON.stringify({ type: "stop" }));
  else if (live.recognition) live.recognition.stop();
  else finishSession();
}

function cleanup() {
  clearInterval(live.tick);
  if (live.stream) live.stream.getTracks().forEach((t) => t.stop());
  if (live.ctx && live.ctx.state !== "closed") live.ctx.close();
  const ws = live.ws;
  live.ws = live.recognition = live.stream = live.ctx = null;
  if (ws && ws.readyState <= WebSocket.OPEN) ws.close();
  $("ring").style.transform = "scale(1)";
}

let finishing = false;
async function finishSession() {
  if (finishing) return;
  finishing = true;
  const failed = live.failed;
  cleanup();
  live.recording = false; live.stopping = false; live.failed = false;
  if (live.interim) addFinal(live.interim); // keep the last words if no final arrived
  setMicState("idle");
  renderTranscript();
  const hasText = live.finals.length > 0;
  if (!failed) setStatus(hasText ? "Done. Edit in the dashboard any time." : "No speech detected. Try again.", hasText ? "ok" : "");
  if (hasText && $("autoSave").checked) await saveCurrent();
  finishing = false;
}

function currentText() {
  return $("finalText").textContent.trim();
}

async function saveCurrent() {
  const text = currentText();
  if (!text) return;
  try {
    const row = await api("/api/transcriptions", {
      method: "POST",
      body: {
        text,
        engine: live.engineUsed || "manual",
        language: [...live.languages].filter(Boolean).join(",") || null,
        duration_seconds: Math.round(live.seconds * 10) / 10,
      },
    });
    $("saveBtn").disabled = true;
    toast(`Saved “${row.title}”`);
  } catch (err) {
    toast("Could not save: " + err.message, true);
  }
}

$("micBtn").addEventListener("click", () => {
  if ($("micBtn").classList.contains("busy")) return;
  live.recording ? stopRecording() : startRecording();
});
$("saveBtn").addEventListener("click", saveCurrent);
$("speakBtn").addEventListener("click", (e) => speak(currentText(), e.currentTarget));
$("copyBtn").addEventListener("click", async () => {
  await navigator.clipboard.writeText(currentText());
  toast("Copied");
});
$("clearBtn").addEventListener("click", () => {
  live.finals = []; live.interim = ""; live.languages = new Set();
  $("timer").textContent = "00:00";
  setStatus("Tap the mic and start speaking — Hindi, English or both.");
  renderTranscript();
});
$("engineSelect").addEventListener("change", (e) => {
  store.set("engine", e.target.value);
  updateEngineHint();
});
$("languageSelect").addEventListener("change", (e) => store.set("language", e.target.value));

function updateEngineHint() {
  const engine = $("engineSelect").value;
  if (engine === "gemini" && !config.engines.gemini?.server_key && !store.get("gemini_api_key")) {
    setStatus("Gemini needs an API key — add one in Settings (⚙), or pick another engine.", "err");
  } else if (!live.recording) {
    setStatus("Tap the mic and start speaking — Hindi, English or both.");
  }
}

// ---------------------------------------------------------------- dashboard (CRUD)
const hist = { page: 1, pageSize: 10, total: 0, q: "", engine: "", items: [] };

async function loadHistory() {
  try {
    const params = new URLSearchParams({ page: hist.page, page_size: hist.pageSize });
    if (hist.q) params.set("q", hist.q);
    if (hist.engine) params.set("engine", hist.engine);
    const [data, stats] = await Promise.all([
      api(`/api/transcriptions?${params}`),
      api("/api/transcriptions/stats"),
    ]);
    Object.assign(hist, { items: data.items, total: data.total });
    renderStats(stats);
    renderList();
  } catch (err) {
    toast("Could not load history: " + err.message, true);
  }
}

function renderStats(s) {
  $("statTotal").textContent = s.total.toLocaleString();
  $("statWords").textContent = s.total_words.toLocaleString();
  const m = s.total_audio_seconds / 60;
  $("statAudio").textContent = m < 1 ? `${Math.round(s.total_audio_seconds)}s` : `${m.toFixed(1)}m`;
  const parts = Object.entries(s.by_engine).map(([k, v]) => `${k} ${v}`);
  $("statEngines").textContent = parts.join(" · ") || "—";
}

function renderList() {
  const list = $("list");
  if (!hist.items.length) {
    list.innerHTML = `<div class="empty card">${hist.q || hist.engine
      ? "No transcriptions match your search."
      : "No transcriptions yet. Record one on the Live tab, or add one with + New."}</div>`;
  } else {
    list.innerHTML = hist.items.map((t) => `
      <article class="item card" data-id="${t.id}">
        <div class="item-head">
          <span class="item-title">${escapeHtml(t.title)}</span>
          <span class="item-meta">${new Date(t.created_at).toLocaleString()}</span>
        </div>
        <p class="item-text">${escapeHtml(t.text)}</p>
        <div class="item-foot">
          <span class="chip">${escapeHtml(t.engine)}</span>
          ${t.language ? `<span class="chip">${escapeHtml(t.language)}</span>` : ""}
          <span class="chip">${t.word_count} words</span>
          ${t.duration_seconds ? `<span class="chip">${fmtClock(t.duration_seconds)}</span>` : ""}
          <span class="spacer"></span>
          <button class="btn ghost" data-act="expand">More</button>
          <button class="btn ghost" data-act="speak" title="Listen">🔊</button>
          <button class="btn ghost" data-act="download" title="Download audio">⬇</button>
          <button class="btn ghost" data-act="copy">Copy</button>
          <button class="btn ghost" data-act="edit">Edit</button>
          <button class="btn ghost danger" data-act="delete">Delete</button>
        </div>
      </article>`).join("");
  }
  const pages = Math.max(1, Math.ceil(hist.total / hist.pageSize));
  $("pageInfo").textContent = `Page ${hist.page} of ${pages} · ${hist.total} total`;
  $("prevBtn").disabled = hist.page <= 1;
  $("nextBtn").disabled = hist.page >= pages;
}

$("list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const card = btn.closest(".item");
  const item = hist.items.find((t) => t.id === Number(card.dataset.id));
  switch (btn.dataset.act) {
    case "expand": {
      const p = card.querySelector(".item-text");
      p.classList.toggle("expanded");
      btn.textContent = p.classList.contains("expanded") ? "Less" : "More";
      break;
    }
    case "speak": speak(item.text, btn); break;
    case "download": downloadSpeech(item.text, item.title, btn); break;
    case "copy": await navigator.clipboard.writeText(item.text); toast("Copied"); break;
    case "edit": openEditor(item); break;
    case "delete":
      if (!confirm(`Delete “${item.title}”? This cannot be undone.`)) return;
      try {
        await api(`/api/transcriptions/${item.id}`, { method: "DELETE" });
        toast("Deleted");
        if (hist.items.length === 1 && hist.page > 1) hist.page--;
        loadHistory();
      } catch (err) { toast("Delete failed: " + err.message, true); }
      break;
  }
});

let editing = null;
function openEditor(item = null) {
  editing = item;
  $("editTitle").textContent = item ? "Edit transcription" : "New transcription";
  $("editName").value = item?.title || "";
  $("editText").value = item?.text || "";
  $("editLang").value = item?.language || "";
  $("editDialog").showModal();
  $("editText").focus();
}

$("editForm").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault();
  const body = {
    title: $("editName").value.trim() || null,
    text: $("editText").value,
    language: $("editLang").value.trim() || null,
  };
  try {
    if (editing) {
      if (!body.title) delete body.title;
      await api(`/api/transcriptions/${editing.id}`, { method: "PATCH", body });
      toast("Updated");
    } else {
      await api("/api/transcriptions", { method: "POST", body: { ...body, engine: "manual" } });
      toast("Created");
    }
    $("editDialog").close();
    loadHistory();
  } catch (err) {
    toast(err.message, true);
  }
});

$("newBtn").addEventListener("click", () => openEditor());
$("prevBtn").addEventListener("click", () => { hist.page--; loadHistory(); });
$("nextBtn").addEventListener("click", () => { hist.page++; loadHistory(); });
let searchTimer;
$("searchInput").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { hist.q = e.target.value.trim(); hist.page = 1; loadHistory(); }, 250);
});
$("engineFilter").addEventListener("change", (e) => { hist.engine = e.target.value; hist.page = 1; loadHistory(); });

// ---------------------------------------------------------------- settings
$("settingsBtn").addEventListener("click", () => {
  $("apiKeyInput").value = store.get("gemini_api_key");
  $("modelInput").value = store.get("gemini_model");
  $("modelInput").placeholder = config.engines.gemini?.model || "gemini-3.5-transcribe-live";
  fillVoiceOptions(ttsSettings().engine, ttsSettings().voice);
  $("keyHint").textContent = config.engines.gemini?.server_key
    ? "This server already has a Gemini key, so this is optional."
    : "This server has no Gemini key. Add your own to use the Gemini engine.";
  $("settingsDialog").showModal();
});
$("settingsSave").addEventListener("click", () => {
  store.set("gemini_api_key", $("apiKeyInput").value.trim());
  store.set("gemini_model", $("modelInput").value.trim());
  store.set("tts_engine", $("ttsEngineSelect").value);
  store.set("tts_voice", $("ttsVoiceSelect").value);
  syncSpeakPickers();
  toast("Settings saved");
  setTimeout(updateEngineHint, 0);
});

const VOICE_HINTS = {
  gemini: "Most natural cloud voices; Hindi, English and mixed text. Free tier: 10 plays a day per model, then the Piper voice is used automatically.",
  piper: "Open-source voices running on this server (Piper). Hindi text uses the voice you pick; English-only text uses an English voice.",
  browser: "Free voices built into this device. Quality depends on the device and browser.",
};

// Fill an engine + voice picker pair (used by Settings and the Text to Speech tab).
function fillVoiceOptions(engine, selected = "", ids = ["ttsEngineSelect", "ttsVoiceSelect", "ttsHint"]) {
  const [engineId, voiceId, hintId] = ids;
  const tts = config.tts || {};
  const engines = $(engineId);
  if (!tts.piper?.enabled) engines.querySelector('[value="piper"]')?.remove();
  if (![...engines.options].some((o) => o.value === engine)) engine = "browser";
  engines.value = engine;
  const voices = engine === "browser" ? { auto: "Device voice (picked by language)" } : (tts[engine]?.voices || {});
  $(voiceId).innerHTML = Object.entries(voices)
    .map(([k, v]) => `<option value="${escapeHtml(k)}">${escapeHtml(v)}</option>`).join("");
  if (selected && voices[selected]) $(voiceId).value = selected;
  $(hintId).textContent = VOICE_HINTS[engine];
}
$("ttsEngineSelect").addEventListener("change", (e) => fillVoiceOptions(e.target.value));
$("ttsTestBtn").addEventListener("click", (e) => {
  store.set("tts_engine", $("ttsEngineSelect").value);
  store.set("tts_voice", $("ttsVoiceSelect").value);
  syncSpeakPickers();
  speak("नमस्ते! Kal ki meeting दस बजे है, please late मत होना.", e.currentTarget);
});

// ---------------------------------------------------------------- text to speech tab
const SPEAK_IDS = ["speakEngine", "speakVoice", "speakHint"];

function syncSpeakPickers() {
  fillVoiceOptions(ttsSettings().engine, ttsSettings().voice, SPEAK_IDS);
}

function speakTextValue() {
  return $("speakText").value.trim();
}

function updateSpeakButtons() {
  const text = speakTextValue();
  $("speakCount").textContent = `${$("speakText").value.length} / 4000`;
  ["speakPlay", "speakDownload", "speakSave", "speakClear"].forEach((id) => ($(id).disabled = !text));
}

$("speakEngine").addEventListener("change", (e) => {
  fillVoiceOptions(e.target.value, "", SPEAK_IDS);
  store.set("tts_engine", $("speakEngine").value);
  store.set("tts_voice", $("speakVoice").value);
});
$("speakVoice").addEventListener("change", (e) => store.set("tts_voice", e.target.value));
$("speakText").addEventListener("input", updateSpeakButtons);
$("speakText").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && speakTextValue()) speak(speakTextValue(), $("speakPlay"));
});
document.querySelectorAll("[data-example]").forEach((b) =>
  b.addEventListener("click", () => {
    $("speakText").value = b.dataset.example;
    updateSpeakButtons();
    $("speakText").focus();
  })
);
$("speakPlay").addEventListener("click", (e) => speak(speakTextValue(), e.currentTarget));
$("speakDownload").addEventListener("click", (e) => {
  const text = speakTextValue();
  downloadSpeech(text, text.split(/\s+/).slice(0, 6).join(" "), e.currentTarget);
});
$("speakClear").addEventListener("click", () => {
  stopSpeaking();
  $("speakText").value = "";
  updateSpeakButtons();
  $("speakText").focus();
});
$("speakSave").addEventListener("click", async () => {
  try {
    const text = speakTextValue();
    const row = await api("/api/transcriptions", {
      method: "POST",
      body: { text, engine: "manual", language: /[\u0900-\u097F]/.test(text) ? "hi" : null },
    });
    toast(`Saved “${row.title}”`);
  } catch (err) {
    toast("Could not save: " + err.message, true);
  }
});

// ---------------------------------------------------------------- boot
(async function init() {
  try { config = await api("/api/config"); } catch { /* use defaults */ }
  $("languageSelect").innerHTML = Object.entries(config.languages)
    .map(([k, v]) => `<option value="${k}">${escapeHtml(v)}</option>`).join("");
  if (!config.engines.whisper?.enabled) $("engineSelect").querySelector('[value="whisper"]').remove();
  const savedEngine = store.get("engine");
  const engineOk = (e) => [...$("engineSelect").options].some((o) => o.value === e);
  $("engineSelect").value = engineOk(savedEngine) ? savedEngine
    : (config.engines.gemini?.server_key ? "gemini" : (config.engines.whisper?.enabled ? "whisper" : config.default_engine));
  const savedLang = store.get("language");
  if (savedLang && config.languages[savedLang]) $("languageSelect").value = savedLang;
  setMicState("idle");
  updateEngineHint();
  renderTranscript();
  syncSpeakPickers();
  updateSpeakButtons();
  if ("speechSynthesis" in window) speechSynthesis.getVoices(); // warm up voice list
})();
