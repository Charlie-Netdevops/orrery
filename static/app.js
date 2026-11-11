/* Orrery - chat UI. Plain browser JavaScript, no build step.
   Chats live in this browser's localStorage; full-size images are kept in memory only. */
"use strict";
const $ = (id) => document.getElementById(id);
const STORE_KEY = "orrery.v1";
const HINT = "Enter to send · Shift+Enter for new line";
let state = { chats: [], activeId: null, model: null };
let controller = null;

/* ---------- storage ---------- */
function load() {
  try { const s = JSON.parse(localStorage.getItem(STORE_KEY)); if (s && Array.isArray(s.chats)) state = s; } catch (e) {}
}
function save() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); }
  catch (e) { notify("Couldn't save chats: browser storage is full or blocked. Delete old chats or they'll be lost on reload."); }
}
let hintTimer;
function notify(msg) {
  const h = $("hint"); clearTimeout(hintTimer);
  h.textContent = msg; h.classList.add("warn");
  hintTimer = setTimeout(() => { h.textContent = HINT; h.classList.remove("warn"); }, 5000);
}
const activeChat = () => state.chats.find(c => c.id === state.activeId) || null;

/* ---------- model catalogue ---------- */
// Friendly names, "best for" lines, groups and order come from the server (config/models.json).
let GROUP_ORDER = ["", "Everyday", "Coding", "Reasoning", "Vision-capable", "Local", "Other"];
function modelInfo(m) {
  return { name: m.name || prettyModel(m.model), desc: m.desc || "", group: m.group ?? "Other", rank: m.rank ?? 1e9 };
}
function prettyModel(id) {
  if (!id) return "Assistant";
  const known = modelList.find(m => m.model === id && m.info?.name);
  if (known) return known.info.name;
  const base = id.split("/").pop().replace(/[-:]latest$/i, "");
  return base.split(/[-_:\s]+/).filter(Boolean).map(w =>
    /^\d+(\.\d+)?[bkmt]$/i.test(w) || /^[a-z]\d/i.test(w) || /^(glm|vl|ai|llm|gpt)$/i.test(w)
      ? w.toUpperCase()
      : w[0].toUpperCase() + w.slice(1)
  ).join(" ");
}
function showModelId() {
  let id = ""; try { id = JSON.parse(state.model).model; } catch (e) {}
  $("modelId").textContent = id; $("modelId").title = id;
}

/* ---------- markdown ---------- */
function render(md) {
  if (window.marked && window.DOMPurify) {
    return DOMPurify.sanitize(marked.parse(md, { breaks: true, gfm: true }));
  }
  const d = document.createElement("div"); d.textContent = md;      // offline fallback
  return `<p class="plain">${d.innerHTML}</p>`;
}
function copyText(text, btn) {
  const label = btn.dataset.label || (btn.dataset.label = btn.textContent);
  if (!navigator.clipboard) return notify("Couldn't copy: clipboard isn't available here.");
  navigator.clipboard.writeText(text).then(
    () => { btn.textContent = "Copied"; setTimeout(() => btn.textContent = label, 1200); },
    () => notify("Couldn't copy: clipboard access was blocked."));
}
function addCopyButtons(el) {
  el.querySelectorAll("pre").forEach(pre => {
    if (pre.querySelector(".copy-btn")) return;
    const b = document.createElement("button");
    b.className = "copy-btn"; b.textContent = "Copy"; b.setAttribute("aria-label", "Copy code");
    const code = (pre.querySelector("code") || pre).textContent;   // captured before the button is added
    b.onclick = () => copyText(code, b);
    pre.appendChild(b);
  });
}

/* ---------- sidebar ---------- */
const isMobile = () => matchMedia("(max-width: 760px)").matches;
function setOverlay(open) {
  $("sidebar").classList.toggle("open", open);
  $("backdrop").classList.toggle("show", open);
}
function renderList() {
  const list = $("chatList");
  list.innerHTML = state.chats.length ? '<div class="label">Recents</div>' : '<div class="list-empty">No chats yet</div>';
  for (const c of state.chats) {
    const title = c.title || "New chat";
    const item = document.createElement("div");
    item.className = "chat-item" + (c.id === state.activeId ? " active" : "");
    const open = document.createElement("button"); open.className = "chat-open"; open.textContent = title; open.title = title;
    if (c.id === state.activeId) open.setAttribute("aria-current", "page");
    const del = document.createElement("button"); del.className = "del"; del.textContent = "✕";
    del.title = "Delete chat"; del.setAttribute("aria-label", "Delete chat: " + title);
    del.onclick = (e) => {
      e.stopPropagation();
      state.chats = state.chats.filter(x => x.id !== c.id);
      if (state.activeId === c.id) { if (controller) controller.abort(); state.activeId = null; renderThread(); }
      save(); renderList();
    };
    open.onclick = () => {
      if (controller) return notify("Stop the current reply (■) before switching chats.");
      state.activeId = c.id; save(); renderList(); renderThread(); setOverlay(false);
    };
    item.append(open, del);
    list.appendChild(item);
  }
}

/* ---------- thread ---------- */
function actBtn(label, aria) {
  const b = document.createElement("button"); b.className = "act"; b.textContent = label; b.setAttribute("aria-label", aria);
  return b;
}
function msgEl(m, isLast) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + m.role;
  if (m.role === "user") {
    const b = document.createElement("div"); b.className = "bubble";
    if (m.images?.length) {                      // small saved thumbnails only; full images are never stored
      const g = document.createElement("div"); g.className = "bubble-imgs";
      m.images.forEach((src, i) => { const im = document.createElement("img"); im.src = src; im.alt = `Attached image ${i + 1}`; im.loading = "lazy"; g.appendChild(im); });
      b.appendChild(g);
    }
    if (m.content) { const t = document.createElement("span"); t.className = "bubble-text"; t.textContent = m.content; b.appendChild(t); }
    wrap.appendChild(b);
  } else {
    const who = document.createElement("div"); who.className = "who";
    const dot = document.createElement("span"); dot.className = "dot";
    const name = document.createElement("span"); name.className = "name"; name.textContent = prettyModel(m.model);
    who.append(dot, name);
    if (m.model) {
      const id = document.createElement("span"); id.className = "model-id"; id.textContent = m.model;
      who.title = m.model; who.appendChild(id);
    }
    const think = document.createElement("details"); think.className = "thinking";
    think.innerHTML = '<summary>Thinking</summary><div class="tbody"></div>';
    think.style.display = m.reasoning ? "" : "none";
    think.querySelector(".tbody").textContent = m.reasoning || "";
    const content = document.createElement("div"); content.className = "content";
    content.innerHTML = render(m.content || ""); addCopyButtons(content);
    wrap.append(who, think, content);
    if (m.error) { const e = document.createElement("div"); e.className = "error"; e.textContent = m.error; wrap.appendChild(e); }
    if (m.stopped) { const s = document.createElement("div"); s.className = "note"; s.textContent = "Stopped by you."; wrap.appendChild(s); }

    const actions = document.createElement("div"); actions.className = "actions";
    if (m.content) {
      const copy = actBtn("Copy", "Copy reply");
      copy.onclick = () => copyText(m.content, copy);
      actions.appendChild(copy);
      if (window.speechSynthesis) {
        const sp = actBtn(speakTarget === m ? "Stop reading" : "Read aloud", "Read this reply aloud");
        sp.classList.add("speak"); sp.msg = m;
        sp.onclick = () => speakTarget === m ? stopSpeaking() : readAloud(m);
        actions.appendChild(sp);
      }
    }
    if (isLast) {
      const regen = actBtn("Regenerate", "Regenerate reply with the selected model");
      regen.onclick = regenerate;
      actions.appendChild(regen);
    }
    if (actions.children.length) wrap.appendChild(actions);
  }
  return wrap;
}
function renderThread() {
  const chat = activeChat(), thread = $("thread");
  thread.innerHTML = "";
  const has = chat && chat.messages.length;
  $("empty").style.display = has ? "none" : "flex";
  thread.style.display = has ? "block" : "none";
  if (has) chat.messages.forEach((m, i) => thread.appendChild(msgEl(m, i === chat.messages.length - 1)));
  scrollDown(true);
}
function scrollDown(force) {
  const s = $("scroll");
  if (force || s.scrollHeight - s.scrollTop - s.clientHeight < 140) s.scrollTop = s.scrollHeight;
}

/* ---------- models ---------- */
let modelList = [];          // [{ provider, label, model, value, info }]
let visibleOpts = [];        // options currently shown in the picker (after filtering)
let activeIdx = -1;          // keyboard-highlighted option

const groupRank = (g) => { const i = GROUP_ORDER.indexOf(g); return i < 0 ? GROUP_ORDER.length : i; };
// Provider -> sub-group -> models, in display order. Optional search filter.
function groupedModels(q) {
  const provs = new Map();
  for (const m of modelList) {
    if (q && !`${m.info.name} ${m.info.desc} ${m.model} ${m.vision ? "vision images" : ""}`.toLowerCase().includes(q)) continue;
    if (!provs.has(m.label)) provs.set(m.label, new Map());
    const groups = provs.get(m.label);
    if (!groups.has(m.info.group)) groups.set(m.info.group, []);
    groups.get(m.info.group).push(m);
  }
  return [...provs].map(([label, groups]) => ({
    label,
    groups: [...groups]
      .sort((a, b) => groupRank(a[0]) - groupRank(b[0]))
      .map(([name, items]) => ({ name, items: items.sort((a, b) =>
        a.info.rank - b.info.rank || a.info.name.localeCompare(b.info.name)) })),
  }));
}

function renderPicker() {
  const q = $("pickerSearch").value.trim().toLowerCase();
  const list = $("pickerList"); list.innerHTML = ""; visibleOpts = [];
  for (const p of groupedModels(q)) {
    const ph = document.createElement("div"); ph.className = "pk-prov"; ph.textContent = p.label; ph.setAttribute("role", "presentation");
    list.appendChild(ph);
    for (const g of p.groups) {
      if (g.name) { const gh = document.createElement("div"); gh.className = "pk-group"; gh.textContent = g.name; gh.setAttribute("role", "presentation"); list.appendChild(gh); }
      for (const m of g.items) {
        const i = visibleOpts.length;
        const o = document.createElement("div");
        o.className = "pk-opt"; o.id = "pk-opt-" + i; o.title = m.model;
        o.setAttribute("role", "option"); o.setAttribute("aria-selected", String(m.value === state.model));
        const n = document.createElement("span"); n.className = "pk-name"; n.textContent = m.info.name;
        if (m.vision) { const bd = document.createElement("span"); bd.className = "pk-badge"; bd.textContent = "IMAGES"; bd.title = "Can read images you attach"; n.appendChild(bd); }
        o.appendChild(n);
        if (m.info.desc) { const d = document.createElement("span"); d.className = "pk-desc"; d.textContent = m.info.desc; o.appendChild(d); }
        o.onclick = () => chooseModel(m.value);
        o.onmousemove = () => { if (activeIdx !== i) setActive(i, false); };
        list.appendChild(o); visibleOpts.push({ el: o, m });
      }
    }
  }
  if (!visibleOpts.length) {
    const e = document.createElement("div"); e.className = "pk-empty"; e.textContent = `No models match “${q}”`; list.appendChild(e);
  }
  const sel = visibleOpts.findIndex(v => v.m.value === state.model);
  setActive(q ? 0 : Math.max(sel, 0), true);
}
function setActive(i, scroll) {
  visibleOpts[activeIdx]?.el.classList.remove("active");
  activeIdx = visibleOpts.length ? Math.max(0, Math.min(i, visibleOpts.length - 1)) : -1;
  const cur = visibleOpts[activeIdx];
  if (cur) { cur.el.classList.add("active"); if (scroll) cur.el.scrollIntoView({ block: "nearest" }); }
  $("pickerSearch").setAttribute("aria-activedescendant", cur ? cur.el.id : "");
}
const pickerOpen = () => !$("pickerPop").hidden;
function openPicker() {
  if ($("pickerBtn").disabled || pickerOpen()) return;
  $("pickerPop").hidden = false; $("pickerBtn").setAttribute("aria-expanded", "true");
  $("pickerBackdrop").classList.add("show");
  $("pickerSearch").value = ""; renderPicker();
  if (!isMobile()) $("pickerSearch").focus({ preventScroll: true });   // on phones, don't pop the keyboard over the sheet
}
function closePicker(returnFocus) {
  if (!pickerOpen()) return;
  $("pickerPop").hidden = true; $("pickerBtn").setAttribute("aria-expanded", "false");
  $("pickerBackdrop").classList.remove("show");
  if (returnFocus) $("pickerBtn").focus();
}
function chooseModel(value) {
  state.model = value; save(); updateSend(); showModelId(); updatePickerBtn(); updateAttachUI(); closePicker(true);
  if (attachments.length && !visionOn()) notify(`${currentModel()?.info.name || "This model"} can't read images — your ${attachments.length} attached image(s) won't be sent. Switch back or remove them.`);
}
function updatePickerBtn() {
  const cur = modelList.find(m => m.value === state.model);
  $("pickerName").textContent = cur ? cur.info.name : "Choose a model";
  $("pickerBtn").title = cur ? `${cur.info.name}${cur.info.desc ? " — " + cur.info.desc : ""} (${cur.model})` : "";
}
function pickerError(msg) {
  modelList = []; $("pickerName").textContent = msg; $("pickerBtn").disabled = true; $("modelId").textContent = "";
  updateAttachUI();
}

async function loadModels() {
  try {
    const r = await fetch("/api/models");
    if (r.status === 401) return location.assign("/login");
    if (!r.ok) throw new Error();
    const { models, groups, auth, version } = await r.json();
    $("logoutBtn").hidden = !auth;
    if (version) $("appVersion").textContent = "v" + version;
    if (Array.isArray(groups) && groups.length) GROUP_ORDER = [...groups, ...(groups.includes("Other") ? [] : ["Other"])];
    if (!models.length) return pickerError("No models — check ORRERY_PROVIDERS in .env");
    modelList = models.map(m => ({ ...m, value: JSON.stringify({ provider: m.provider, model: m.model }), info: modelInfo(m) }));
    if (!modelList.some(m => m.value === state.model)) {
      state.model = groupedModels("")[0].groups[0].items[0].value;      // first model in display order
    }
    $("pickerBtn").disabled = false;
    save(); updateSend(); showModelId(); updatePickerBtn(); updateAttachUI();
  } catch (e) { pickerError("Can't load models — check: docker logs orrery"); }
}

/* ---------- image attachments ---------- */
// Which models accept images comes from the server (VISION_MODELS in app.py -> "vision" flag in /api/models).
const IMG_TYPES = ["image/png", "image/jpeg", "image/webp"];
const MAX_IMAGES = 3, MAX_SIDE = 1600, TARGET_BYTES = 1024 * 1024, THUMB_SIDE = 256;
let attachments = [];                  // [{ full, thumb, busy }] for the message being written (never saved)
const fullImages = new WeakMap();      // sent user message -> full-size images, in memory only (never in localStorage)
const currentModel = () => modelList.find(m => m.value === state.model) || null;
const visionOn = () => !!currentModel()?.vision;
const modelName = () => currentModel()?.info.name || "This model";

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file), img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("decode")); };
    img.src = url;
  });
}
function drawScaled(img, maxSide) {
  const s = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(img.naturalWidth * s)); c.height = Math.max(1, Math.round(img.naturalHeight * s));
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);      // transparent PNGs get white, not black
  g.drawImage(img, 0, 0, c.width, c.height);
  return c;
}
const dataUrlBytes = (u) => Math.floor((u.length - u.indexOf(",") - 1) * 3 / 4);
// Resize to max 1600px on the long side, then JPEG-compress until it's ~1 MB or less.
async function compressImage(file) {
  const img = await loadImage(file);
  let canvas = drawScaled(img, MAX_SIDE), q = 0.85, full = canvas.toDataURL("image/jpeg", q);
  for (let i = 0; i < 12 && dataUrlBytes(full) > TARGET_BYTES; i++) {
    if (q > 0.55) q -= 0.1;
    else canvas = drawScaled(img, Math.round(Math.max(canvas.width, canvas.height) * 0.8));
    full = canvas.toDataURL("image/jpeg", q);
  }
  return { full, thumb: drawScaled(img, THUMB_SIDE).toDataURL("image/jpeg", 0.7) };
}
async function addFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  if (!visionOn()) return notify(`${modelName()} can't read images. Pick a model marked IMAGES in the model list to attach them.`);
  for (const f of files) {
    if (!IMG_TYPES.includes(f.type)) { notify(`“${f.name || "That file"}” isn't supported. Use PNG, JPG or WEBP.`); continue; }
    if (attachments.length >= MAX_IMAGES) { notify(`You can attach up to ${MAX_IMAGES} images per message.`); break; }
    const slot = { busy: true };       // reserve the slot now so fast repeated drops can't exceed the limit
    attachments.push(slot); renderAttachments(); updateSend();
    try { Object.assign(slot, await compressImage(f), { busy: false }); }
    catch (e) { attachments = attachments.filter(a => a !== slot); notify(`Couldn't read “${f.name || "that image"}”. Is it a valid PNG, JPG or WEBP?`); }
    renderAttachments(); updateSend();
  }
}
function renderAttachments() {
  const box = $("attachments"), blocked = attachments.length > 0 && !visionOn();
  box.innerHTML = ""; box.classList.toggle("blocked", blocked);
  attachments.forEach((a, i) => {
    const t = document.createElement("div"); t.className = "thumb" + (a.busy ? " busy" : "");
    if (a.thumb) { const im = document.createElement("img"); im.src = a.thumb; im.alt = `Attached image ${i + 1}`; t.appendChild(im); }
    const rm = document.createElement("button"); rm.className = "rm"; rm.textContent = "✕";
    rm.title = "Remove image"; rm.setAttribute("aria-label", `Remove image ${i + 1}`);
    rm.onclick = () => { attachments = attachments.filter(x => x !== a); renderAttachments(); updateSend(); $("input").focus(); };
    t.appendChild(rm); box.appendChild(t);
  });
  if (blocked) {
    const w = document.createElement("div"); w.className = "attach-warn"; w.setAttribute("role", "alert");
    w.textContent = `${modelName()} can't read images. Switch back to a model marked IMAGES, or remove the images to send text only.`;
    box.appendChild(w);
  }
}
function updateAttachUI() { $("attachBtn").hidden = !visionOn(); renderAttachments(); }

// What the server gets: text for every message; full images only on the newest user message,
// and only for a vision model. Older images become "[image]".
function apiMessages(chat, vision) {
  const msgs = chat.messages.slice(0, -1).filter(m => m.content || m.images?.length);
  return msgs.map((m, i) => {
    const full = i === msgs.length - 1 && vision ? fullImages.get(m) : null;
    if (full?.length) return { role: "user", content: [
      ...(m.content ? [{ type: "text", text: m.content }] : []),
      ...full.map(url => ({ type: "image_url", image_url: { url } })) ] };
    const note = (m.images || []).map(() => "[image]").join(" ");
    return { role: m.role, content: [m.content, note].filter(Boolean).join("\n") };
  });
}

/* ---------- sending ---------- */
function updateSend() {
  const btn = $("sendBtn");
  updateVoiceUI();
  if (controller) { btn.disabled = false; btn.classList.add("stop"); btn.innerHTML = "&#9632;"; btn.title = "Stop"; btn.setAttribute("aria-label", "Stop"); return; }
  btn.classList.remove("stop"); btn.innerHTML = "&#8593;"; btn.title = "Send"; btn.setAttribute("aria-label", "Send");
  btn.disabled = (!$("input").value.trim() && !attachments.length) || !state.model;
}

async function send() {
  if (recMode === "dictate") stopListening(true);   // sending while dictating: stop the mic first
  const text = $("input").value.trim();
  if ((!text && !attachments.length) || controller || !state.model) return;
  if (attachments.length && !visionOn()) return notify(`${modelName()} can't read images. Switch to a model marked IMAGES, or remove the images to send text only.`);
  if (attachments.some(a => a.busy)) return notify("Still preparing your images — try again in a moment.");
  let chat = activeChat();
  if (!chat) {
    chat = { id: Date.now().toString(36), title: (text || "Image").slice(0, 48), messages: [] };
    state.chats.unshift(chat); state.activeId = chat.id;
  }
  const msg = { role: "user", content: text };
  if (attachments.length) {
    msg.images = attachments.map(a => a.thumb);                 // saved: small thumbnails only
    fullImages.set(msg, attachments.map(a => a.full));          // memory only: sent with this message
  }
  chat.messages.push(msg);
  attachments = []; renderAttachments();
  $("input").value = ""; autoGrow();
  await streamReply(chat);
}

async function regenerate() {
  const chat = activeChat();
  if (controller || !chat || !state.model) return;
  const n = chat.messages.length;
  if (chat.messages[n - 1]?.role !== "assistant" || chat.messages[n - 2]?.role !== "user") return;
  const user = chat.messages[n - 2];
  if (user.images?.length && !(visionOn() && fullImages.has(user))) {
    notify(visionOn()
      ? "The original images aren't kept after a reload, so this regenerates from your text only."
      : `${modelName()} can't read images, so this regenerates from your text only.`);
  }
  chat.messages.pop();                       // replace the last reply
  await streamReply(chat);
}

async function streamReply(chat) {
  const { provider, model } = JSON.parse(state.model);
  const reply = { role: "assistant", content: "", reasoning: "", model };
  chat.messages.push(reply);
  save(); renderList(); renderThread();

  const el = $("thread").lastElementChild;
  el.classList.add("streaming");
  const contentEl = el.querySelector(".content"), think = el.querySelector(".thinking"), tbody = el.querySelector(".tbody");
  controller = new AbortController(); updateSend();
  const voiceTurn = voiceMode ? beginVoiceReply(reply) : null;     // Voice mode: speak the reply as it streams

  try {
    const r = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
      body: JSON.stringify({ provider, model, messages: apiMessages(chat, visionOn()) }),
    });
    if (r.status === 401) { location.assign("/login"); throw new Error("Your login expired — please log in again."); }
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `Server error ${r.status}`); }
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "", pending = false, finished = false;
    const paint = () => { pending = false; contentEl.innerHTML = render(reply.content); addCopyButtons(contentEl); scrollDown(); };
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n"); buf = parts.pop();
      for (const p of parts) {
        if (!p.startsWith("data:")) continue;
        const data = p.slice(5).trim();
        if (data === "[DONE]") { finished = true; continue; }
        const j = JSON.parse(data);
        if (j.error) reply.error = j.error;
        if (j.r) { reply.reasoning += j.r; think.style.display = ""; tbody.textContent = reply.reasoning; }
        if (j.c) { reply.content += j.c; if (!pending) { pending = true; requestAnimationFrame(paint); } voiceTurn?.feed(false); }
      }
    }
    if (!finished && !reply.error) reply.error = "Connection lost before the reply finished — the answer may be incomplete.";
    else if (!reply.content && !reply.reasoning && !reply.error) reply.error = "The model returned an empty reply. Try again.";
  } catch (e) {
    if (e.name === "AbortError") reply.stopped = true;
    else reply.error = e instanceof TypeError ? "Network error — can't reach the Orrery server. Is the container running?" : e.message;
  } finally {
    controller = null; save(); renderThread(); updateSend();
    voiceTurn?.feed(true);
    if (!isMobile()) $("input").focus();
  }
}

/* ---------- voice: speech-to-text, read aloud, voice mode (browser APIs only) ---------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const synth = window.speechSynthesis || null;
const VOICE_KEY = "orrery.voice.v1";
const NO_SR_MSG = "Voice input needs Chrome or Edge — this browser doesn't support speech recognition.";
const REC_ERRORS = {
  "not-allowed": "Microphone access is blocked. Click the icon at the left of the address bar, allow Microphone for this site, then try again.",
  "service-not-allowed": "Microphone access is blocked. Click the icon at the left of the address bar, allow Microphone for this site, then try again.",
  "audio-capture": "No microphone found. Check that one is connected and not being used by another app.",
  "network": "Speech recognition couldn't reach the browser's speech service (it needs internet). Check your connection and try again.",
  "language-not-supported": "This browser's speech recognition doesn't support English (US).",
};
const SYNTH_ERRORS = {
  "not-allowed": "The browser blocked speech playback. Click anywhere on the page, then try again.",
  "audio-busy": "Your audio output is busy. Close other apps using it, then try again.",
  "audio-hardware": "No audio output found. Check your speakers or headphones.",
  "voice-unavailable": "That voice isn't available right now. Pick another in Voice settings.",
  "synthesis-unavailable": "Speech isn't available in this browser right now.",
  "network": "The online voice couldn't be reached. Check your connection or pick a local voice in Voice settings.",
};
let synthErrorSession = -1;
let voicePrefs = { voiceURI: "", rate: 1, privacyAck: false };
try { Object.assign(voicePrefs, JSON.parse(localStorage.getItem(VOICE_KEY)) || {}); } catch (e) {}
voicePrefs.rate = Math.min(1.5, Math.max(0.8, +voicePrefs.rate || 1));
function saveVoicePrefs() { try { localStorage.setItem(VOICE_KEY, JSON.stringify(voicePrefs)); } catch (e) {} }

let voiceMode = false;
// Recognition. recMode: "dictate" (fill the input) | "voice" (voice-mode turn) | "barge" (listening for an interruption while speaking)
let rec = null, recActive = false, recMode = null, recBase = "", recOffset = 0, recSendPending = false, recFatal = false,
    silenceTimer = null, pendingStart = null, recStartedAt = 0, quickEnds = 0;
// Speech output
let speakSession = 0, pendingUtts = 0, speaking = false, speakTarget = null, speechStreamOpen = false, voiceSpeech = false, spokenWords = new Set();

function withPrivacyAck(then) {
  if (voicePrefs.privacyAck) return then();
  $("privacyNote").hidden = false; $("privacyOk").focus();
  $("privacyOk").onclick = () => { voicePrefs.privacyAck = true; saveVoicePrefs(); $("privacyNote").hidden = true; then(); };
}

function startListening(mode) {
  if (!SR) return notify(NO_SR_MSG);
  if (recActive) { pendingStart = mode; rec.abort(); return; }      // restart cleanly once the old session has ended
  const r = new SR(); rec = r;
  r.lang = "en-US"; r.interimResults = true; r.continuous = true;
  recMode = mode; recOffset = 0; recSendPending = false; recFatal = false; recStartedAt = Date.now();
  const typed = $("input").value;
  recBase = mode === "barge" ? "" : (typed.trim() ? typed.replace(/\s*$/, " ") : "");
  r.onresult = (e) => { if (rec === r) onRecResult(e); };
  r.onerror = (e) => { if (rec === r) onRecError(e.error); };
  r.onend = () => { if (rec === r) onRecEnd(); };
  recActive = true;                                                     // set now so a double click can't start two sessions
  try { r.start(); } catch (err) { recActive = false; rec = null; recMode = null; notify("Couldn't start the microphone: " + err.message); }
  updateVoiceUI();
}
function stopListening(abort) {
  if (!rec || !recActive) return;
  recSendPending = false; abort ? rec.abort() : rec.stop();
}
function onRecResult(e) {
  const all = [...e.results];
  if (recMode === "barge") {
    // Skip finished results that are just our own voice coming back through the speakers.
    while (recOffset < all.length && all[recOffset].isFinal && isEcho(all[recOffset][0].transcript)) recOffset++;
    const heard = all.slice(recOffset).map(r => r[0].transcript).join(" ").trim();
    if (!heard || isEcho(heard)) return;
    recMode = "voice"; recBase = "";                                    // barge-in: the user is talking
    stopSpeaking(); if (controller) controller.abort();
  }
  const said = all.slice(recOffset).map(r => r[0].transcript.trim()).filter(Boolean).join(" ");
  $("input").value = (recBase + said).replace(/\s+/g, " ").trimStart();
  autoGrow(); updateSend();
  if (recMode === "voice") { clearTimeout(silenceTimer); silenceTimer = setTimeout(finishVoiceTurn, 1500); }   // ~1.5 s pause = done
}
function finishVoiceTurn() {
  if (recMode !== "voice" || !recActive || !$("input").value.trim()) return;
  recSendPending = true; rec.stop();
}
function onRecError(err) {
  if (err === "aborted") return;
  if (err === "no-speech") {                                            // voice mode just keeps listening
    if (recMode === "dictate") notify("No speech detected — try again and speak a little closer to the mic.");
    return;
  }
  recFatal = true;
  notify(REC_ERRORS[err] || `Voice input error: ${err}`);
  if (voiceMode) setVoiceMode(false);
}
function onRecEnd() {
  const mode = recMode, sendNow = recSendPending;
  rec = null; recActive = false; recMode = null; recSendPending = false; clearTimeout(silenceTimer);
  quickEnds = Date.now() - recStartedAt < 1000 ? quickEnds + 1 : 0;
  updateVoiceUI();
  if (pendingStart) { const m = pendingStart; pendingStart = null; return startListening(m); }
  if (mode === "dictate") { $("input").focus(); return; }
  if (!voiceMode || recFatal) return;
  if (sendNow) return send();                                           // streamReply() speaks the answer, then we listen again
  if (mode === "voice" && !controller && !speaking) {
    if (quickEnds >= 3) return notify("The microphone keeps stopping, so voice mode is paused. Press the mic to try again.");
    startListening("voice");                                            // the browser ends sessions after long silence: keep going
  }
}
const words = (s) => s.toLowerCase().replace(/[^a-z0-9'\s]/g, " ").split(/\s+/).filter(Boolean);
function isEcho(heard) {                                               // mostly words we just spoke = speaker echo, not the user
  const all = words(heard), w = all.filter(x => x.length > 2);        // ignore tiny words like "a", "is", "on"
  const pool = w.length ? w : all;
  return !pool.length || pool.filter(x => spokenWords.has(x)).length / pool.length >= 0.6;
}

// Turn markdown into something worth hearing: no code, no URLs, no symbols.
function speakable(md) {
  return md
    .replace(/```[\s\S]*?(```|$)/g, " Code block omitted. ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<?\b(?:https?:\/\/|www\.)[^\s<>)]+>?/gi, "")
    .replace(/<\/?[a-z][^>]*>/gi, " ")
    .replace(/^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/gm, "")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, "")
    .replace(/(\*\*|\*|~~)(\S(?:[\s\S]*?\S)?)\1/g, "$2")
    .replace(/\|/g, ", ")
    .replace(/[*#_~`>\\]+/g, " ")
    .replace(/\s+([,.!?;:])/g, "$1")
    .replace(/\s+/g, " ").trim();
}
// Where can we cut the streamed text so far? After the last finished sentence/line, never inside a code block.
function findCut(raw, from) {
  const rest = raw.slice(from), re = /[.!?:](?=\s)|\n/g;
  let cut = -1, m;
  while ((m = re.exec(rest))) cut = m.index + 1;
  if (cut < 0) return from;
  let end = from + cut;
  if ((raw.slice(0, end).match(/```/g) || []).length % 2) { const start = raw.lastIndexOf("```", end - 1); end = start > from ? start : from; }
  return end;
}
function chunkText(text) {                                             // short utterances: Chrome cuts off long ones
  const out = []; let cur = "";
  for (const p of text.match(/[^.!?]+[.!?]+["')\]]*\s*|[^.!?]+$/g) || []) {
    if (cur && (cur + p).length > 220) { out.push(cur.trim()); cur = ""; }
    cur += p;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}
function sortedVoices(vs) {                                            // your language first, then "Natural"/"Online" voices
  const lang = (navigator.language || "en-US").toLowerCase();
  const score = (v) => (v.lang.toLowerCase() === lang ? 0 : v.lang.toLowerCase().startsWith("en") ? 2 : 10) + (/natural|online/i.test(v.name) ? 0 : 1);
  return [...vs].sort((a, b) => score(a) - score(b) || a.name.localeCompare(b.name));
}
function pickVoice() {
  if (!synth) return null;
  const vs = synth.getVoices();
  return vs.find(v => v.voiceURI === voicePrefs.voiceURI) || sortedVoices(vs)[0] || null;
}
function queueSpeech(text, session) {
  if (!synth || !text || session !== speakSession) return;
  const v = pickVoice();
  for (const chunk of chunkText(text)) {
    const u = new SpeechSynthesisUtterance(chunk);
    if (v) { u.voice = v; u.lang = v.lang; } else u.lang = "en-US";
    u.rate = voicePrefs.rate;
    u.onstart = () => { if (session === speakSession && voiceMode && voiceSpeech && !recActive) startListening("barge"); };
    u.onend = () => { if (session !== speakSession) return; pendingUtts--; speechDone(session); };
    u.onerror = (ev) => {
      if (session !== speakSession) return;
      if (!["interrupted", "canceled"].includes(ev?.error) && synthErrorSession !== session) {   // one message per reading
        synthErrorSession = session;
        notify(SYNTH_ERRORS[ev?.error] || `Couldn't read aloud (${ev?.error || "unknown error"}).`);
      }
      pendingUtts--; speechDone(session);
    };
    words(chunk).forEach(w => spokenWords.add(w));
    pendingUtts++; speaking = true; synth.speak(u);
  }
  updateVoiceUI();
}
function speechDone(session) {
  if (session !== speakSession || pendingUtts > 0 || speechStreamOpen) return;
  const wasVoice = voiceSpeech;
  speaking = false; speakTarget = null; voiceSpeech = false; updateVoiceUI();
  if (wasVoice && voiceMode) setTimeout(() => { if (voiceMode && !speaking && !controller) startListening("voice"); }, 300);
}
function stopSpeaking() {
  speakSession++; pendingUtts = 0; speechStreamOpen = false;
  speaking = false; speakTarget = null; voiceSpeech = false;
  if (synth) synth.cancel();
  if (recMode === "barge") stopListening(true);
  updateVoiceUI();
}
function readAloud(m) {
  if (!synth) return;
  stopSpeaking();
  const text = speakable(m.content || "");
  if (!text) return notify("There's nothing to read aloud in this reply.");
  speakTarget = m; spokenWords = new Set();
  queueSpeech(text, speakSession);
}
// Voice mode: speak the reply sentence by sentence while it streams.
function beginVoiceReply(reply) {
  stopSpeaking();
  const session = speakSession; let upTo = 0;
  speakTarget = reply; voiceSpeech = true; speechStreamOpen = true; spokenWords = new Set();
  return {
    feed(done) {
      if (session !== speakSession) return;
      if (done && reply.stopped) { stopSpeaking(); if (voiceMode) startListening("voice"); return; }
      const raw = reply.content, cut = done ? raw.length : findCut(raw, upTo);
      if (cut > upTo) { queueSpeech(speakable(raw.slice(upTo, cut)), session); upTo = cut; }
      if (done) {
        if (reply.error) queueSpeech("Sorry, that didn't work. " + reply.error.split(/(?<=\.)\s/)[0], session);
        speechStreamOpen = false; speechDone(session);
      }
    },
  };
}
function setVoiceMode(on) {
  if (on && !SR) return notify(NO_SR_MSG);
  if (on) return withPrivacyAck(() => {
    voiceMode = true; recFatal = false; quickEnds = 0; updateVoiceUI();
    if (!speaking && !controller) startListening("voice");
  });
  voiceMode = false; clearTimeout(silenceTimer); pendingStart = null;
  stopListening(true); stopSpeaking();
  if (controller) controller.abort();
  updateVoiceUI();
}
function bargeIn() { stopSpeaking(); if (controller) controller.abort(); startListening("voice"); }

function updateVoiceUI() {
  const listening = recActive && (recMode === "dictate" || recMode === "voice");
  const mic = $("micBtn");
  mic.classList.toggle("listening", listening); mic.setAttribute("aria-pressed", String(listening));
  mic.title = listening ? (voiceMode ? "Listening — click to send now" : "Listening — click to stop")
    : voiceMode && (speaking || controller) ? "Interrupt and talk" : "Voice input — click to start, click again to stop";
  $("voiceModeBtn").setAttribute("aria-pressed", String(voiceMode));
  let status = "";
  if (voiceMode) status = listening ? "Listening…" : speaking ? "Speaking… talk or press the mic to interrupt" : controller ? "Thinking…" : "Voice mode on — press the mic to talk";
  else if (listening) status = "Listening… click the mic when you're done";
  else if (speaking) status = "Speaking… press Esc to stop";
  $("voiceStatus").hidden = !status; $("voiceStatusText").textContent = status;
  $("voiceStatus").classList.toggle("idle", voiceMode && !listening && !speaking && !controller);
  document.querySelectorAll(".act.speak").forEach(b => b.textContent = b.msg === speakTarget ? "Stop reading" : "Read aloud");
}

function populateVoices() {
  const sel = $("voiceSelect"), vs = sortedVoices(synth.getVoices());
  sel.innerHTML = "";
  if (!vs.length) { sel.innerHTML = "<option>No voices available yet</option>"; sel.disabled = true; return; }
  sel.disabled = false;
  for (const v of vs) {
    const o = document.createElement("option"); o.value = v.voiceURI;
    o.textContent = `${/natural|online/i.test(v.name) ? "★ " : ""}${v.name} (${v.lang})`;
    sel.appendChild(o);
  }
  sel.value = pickVoice()?.voiceURI || vs[0].voiceURI;
}
const showRate = () => { $("rateRange").value = voicePrefs.rate; $("rateVal").textContent = voicePrefs.rate.toFixed(2).replace(/0$/, "") + "×"; };
function openVoicePop() {
  if (synth) populateVoices();
  $("voicePop").hidden = false; $("voiceSettingsBtn").setAttribute("aria-expanded", "true"); $("voiceSelect").focus();
}
function closeVoicePop(focusBtn) {
  if ($("voicePop").hidden) return;
  $("voicePop").hidden = true; $("voiceSettingsBtn").setAttribute("aria-expanded", "false");
  if (focusBtn) $("voiceSettingsBtn").focus();
}

function initVoice() {
  if (!SR) {                                                            // e.g. Firefox: no speech recognition
    $("micBtn").hidden = true;
    const vm = $("voiceModeBtn"); vm.classList.add("unsupported"); vm.setAttribute("aria-disabled", "true"); vm.title = NO_SR_MSG;
  }
  if (!synth) $("voiceSettingsBtn").hidden = true;
  else synth.addEventListener?.("voiceschanged", () => { if (!$("voicePop").hidden) populateVoices(); });
  showRate();
  $("micBtn").onclick = () => {
    if (!SR) return notify(NO_SR_MSG);
    if (voiceMode) {
      if (speaking || controller) return bargeIn();
      if (recActive && recMode === "voice") return finishVoiceTurn();
      return startListening("voice");
    }
    if (recActive) return stopListening();
    withPrivacyAck(() => startListening("dictate"));
  };
  $("voiceModeBtn").onclick = () => setVoiceMode(!voiceMode);
  $("voiceSettingsBtn").onclick = () => $("voicePop").hidden ? openVoicePop() : closeVoicePop(true);
  $("voiceSelect").onchange = (e) => { voicePrefs.voiceURI = e.target.value; saveVoicePrefs(); };
  $("rateRange").oninput = (e) => { voicePrefs.rate = +e.target.value; showRate(); saveVoicePrefs(); };
  $("voiceTest").onclick = () => { stopSpeaking(); spokenWords = new Set(); queueSpeech("Hi! This is how replies will sound.", speakSession); };
  document.addEventListener("pointerdown", (e) => { if (!$("voicePop").hidden && !e.target.closest(".voice-settings")) closeVoicePop(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("voicePop").hidden) return closeVoicePop(true);
    if (voiceMode) return setVoiceMode(false);
    if (recActive) return stopListening();
    if (speaking) stopSpeaking();
  });
  window.addEventListener("pagehide", () => synth?.cancel());
}
initVoice();

/* ---------- composer & controls ---------- */
function autoGrow() { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 220) + "px"; }
$("input").addEventListener("input", () => { autoGrow(); updateSend(); });
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
});
$("sendBtn").onclick = () => controller ? controller.abort() : send();
// Images: click to choose, drag & drop onto the composer, or paste a screenshot.
$("attachBtn").onclick = () => $("fileInput").click();
$("fileInput").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
$("input").addEventListener("paste", (e) => {
  const files = [...(e.clipboardData?.items || [])].filter(i => i.kind === "file" && i.type.startsWith("image/")).map(i => i.getAsFile()).filter(Boolean);
  if (files.length) { e.preventDefault(); addFiles(files); }
});
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
$("composer").addEventListener("dragover", (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = "copy"; $("composer").classList.add("dragging"); });
$("composer").addEventListener("dragleave", (e) => { if (!$("composer").contains(e.relatedTarget)) $("composer").classList.remove("dragging"); });
$("composer").addEventListener("drop", (e) => { if (!hasFiles(e)) return; e.preventDefault(); $("composer").classList.remove("dragging"); addFiles(e.dataTransfer.files); });
// A file dropped anywhere else shouldn't make the browser navigate away from the chat.
window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("drop", (e) => { if (hasFiles(e) && !e.defaultPrevented) { e.preventDefault(); notify("Drop images onto the message box to attach them."); } });
$("newChat").onclick = () => {
  if (controller) return notify("Stop the current reply (■) before starting a new chat.");
  state.activeId = null; save(); renderList(); renderThread(); setOverlay(false); $("input").focus();
};
$("pickerBtn").onclick = () => pickerOpen() ? closePicker(true) : openPicker();
$("pickerBtn").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); openPicker(); }
});
$("pickerSearch").addEventListener("input", renderPicker);
$("pickerSearch").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); setActive(activeIdx + 1, true); }
  else if (e.key === "ArrowUp") { e.preventDefault(); setActive(activeIdx - 1, true); }
  else if (e.key === "Home" && !e.target.value) { e.preventDefault(); setActive(0, true); }
  else if (e.key === "End" && !e.target.value) { e.preventDefault(); setActive(visibleOpts.length - 1, true); }
  else if (e.key === "Enter") { e.preventDefault(); const cur = visibleOpts[activeIdx]; if (cur) chooseModel(cur.m.value); }
  else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePicker(true); }
  else if (e.key === "Tab") { closePicker(false); }
});
document.addEventListener("pointerdown", (e) => { if (pickerOpen() && !$("picker").contains(e.target)) closePicker(false); });
$("toggleSidebar").onclick = () => isMobile()
  ? setOverlay(!$("sidebar").classList.contains("open"))
  : $("sidebar").classList.toggle("collapsed");
$("backdrop").onclick = () => setOverlay(false);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("sidebar").classList.contains("open")) setOverlay(false); });
document.querySelectorAll(".starter").forEach(b => b.onclick = () => {
  const t = $("input"); t.value = b.dataset.prompt; autoGrow(); updateSend();
  t.focus(); t.setSelectionRange(t.value.length, t.value.length);
});

$("logoutBtn").onclick = async () => {
  try { await fetch("/api/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch (e) {}
  location.assign("/login");
};

load(); renderList(); renderThread(); loadModels();
if (!isMobile()) $("input").focus();
