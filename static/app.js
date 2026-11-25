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

function speak(text) {
  if (!("speechSynthesis" in window)) return toast("Text-to-speech is not supported in this browser", true);
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  // Devanagari script -> Hindi voice, otherwise Indian English (reads Hinglish well).
  u.lang = /[ऀ-ॿ]/.test(text) ? "hi-IN" : "en-IN";
  const voice = speechSynthesis.getVoices().find((v) => v.lang.replace("_", "-") === u.lang);
  if (voice) u.voice = voice;
  speechSynthesis.speak(u);
}

// ---------------------------------------------------------------- navigation
document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
    $(`view-${tab.dataset.view}`).classList.remove("hidden");
    if (tab.dataset.view === "history") loadHistory();
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
$("speakBtn").addEventListener("click", () => speak(currentText()));
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
          <button class="btn ghost" data-act="speak">🔊</button>
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
    case "speak": speak(item.text); break;
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
  $("keyHint").textContent = config.engines.gemini?.server_key
    ? "This server already has a Gemini key, so this is optional."
    : "This server has no Gemini key. Add your own to use the Gemini engine.";
  $("settingsDialog").showModal();
});
$("settingsSave").addEventListener("click", () => {
  store.set("gemini_api_key", $("apiKeyInput").value.trim());
  store.set("gemini_model", $("modelInput").value.trim());
  toast("Settings saved");
  setTimeout(updateEngineHint, 0);
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
  if ("speechSynthesis" in window) speechSynthesis.getVoices(); // warm up voice list
})();
