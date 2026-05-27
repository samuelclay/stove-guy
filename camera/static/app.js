"use strict";

// --------------------------------------------------------------------------
// tiny helpers
// --------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

let toastTimer;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
}

const deckPath = (deckId) => `/api/decks/${encodeURIComponent(deckId)}`;
const thumbUrl = (deckId, slideId) => `${deckPath(deckId)}/thumb/${encodeURIComponent(slideId)}`;
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString();

// --------------------------------------------------------------------------
// view router
// --------------------------------------------------------------------------
let view = "library";
function showView(name) {
  view = name;
  $$(".view").forEach((v) => v.classList.remove("active"));
  $(`#view-${name}`).classList.add("active");
  if (name !== "presenter") stopPreview();
}

// --------------------------------------------------------------------------
// global websocket: drives camera-status pill + presenter live state
// --------------------------------------------------------------------------
let ws;
function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const state = JSON.parse(ev.data);
    updateCamStatus(state.camera);
    if (view === "presenter") updatePresenter(state.presentation);
  };
  ws.onclose = () => setTimeout(connectWS, 1000);
  // keepalive
  ws.onopen = () => setInterval(() => { try { ws.send("ping"); } catch (e) {} }, 15000);
}

function updateCamStatus(cam) {
  const el = $("#camStatus");
  if (!cam) return;
  if (cam.deviceName && !cam.deviceError) {
    el.className = "cam-status ok";
    $(".label", el).textContent = cam.deviceName;
    el.title = `${cam.deviceName} · ${cam.width}×${cam.height}@${cam.fps}`;
  } else if (cam.deviceError) {
    el.className = "cam-status err";
    $(".label", el).textContent = "camera offline";
    el.title = cam.deviceError;
  } else {
    el.className = "cam-status";
    $(".label", el).textContent = "camera…";
  }
}

// ==========================================================================
// LIBRARY
// ==========================================================================
async function loadLibrary() {
  const deckSummaries = await api("GET", "/api/decks");
  const decks = await Promise.all(deckSummaries.map(async (summary) => {
    try {
      return { ...summary, deck: await api("GET", deckPath(summary.id)) };
    } catch (e) {
      return { ...summary, deck: null, loadError: e.message };
    }
  }));
  const grid = $("#deckGrid");
  grid.innerHTML = "";
  $("#libraryEmpty").classList.toggle("hidden", decks.length > 0);
  for (const d of decks) {
    const row = document.createElement("section");
    row.className = "recipe-row";
    row.innerHTML = `
      <div class="recipe-head">
        <div>
          <h2></h2>
          <div class="meta"></div>
        </div>
        <div class="actions">
          <button class="btn primary" data-a="present">▶ Present</button>
          <button class="btn" data-a="edit">Edit</button>
          <button class="btn" data-a="dup">Duplicate</button>
          <button class="btn danger" data-a="del">Delete</button>
        </div>
      </div>
      <div class="recipe-rail"></div>`;
    $("h2", row).textContent = d.name;
    $(".meta", row).textContent = d.loadError
      ? `Could not load recipe · ${d.loadError}`
      : `${d.slideCount} frame${d.slideCount === 1 ? "" : "s"} · ${fmtDate(d.updated)}`;
    $('[data-a="present"]', row).onclick = () => openPresenter(d.id);
    $('[data-a="edit"]', row).onclick = () => openEditor(d.id);
    $('[data-a="dup"]', row).onclick = async () => { await api("POST", `${deckPath(d.id)}/duplicate`); loadLibrary(); };
    $('[data-a="del"]', row).onclick = async () => {
      if (!confirm(`Delete "${d.name}"? This removes its images too.`)) return;
      await api("DELETE", deckPath(d.id)); loadLibrary();
    };

    const rail = $(".recipe-rail", row);
    if (d.deck && d.deck.slides.length) {
      d.deck.slides.forEach((s, i) => {
        const thumb = document.createElement("button");
        thumb.className = "recipe-thumb";
        thumb.type = "button";
        thumb.title = s.label || `Frame ${i + 1}`;
        thumb.innerHTML = `
          <img src="${thumbUrl(d.id, s.id)}" alt="" />
          <span class="thumb-index">${String(i + 1).padStart(3, "0")}</span>
          <span class="thumb-label"></span>`;
        $(".thumb-label", thumb).textContent = s.label || `Frame ${i + 1}`;
        thumb.onclick = () => openPresenter(d.id, i);
        rail.appendChild(thumb);
      });
    } else {
      const empty = document.createElement("div");
      empty.className = "rail-empty";
      empty.textContent = "No frames yet";
      rail.appendChild(empty);
    }

    grid.appendChild(row);
  }
}

$("#newDeckBtn").onclick = async () => {
  const name = prompt("Deck name", "Untitled deck");
  if (!name) return;
  const deck = await api("POST", "/api/decks", { name });
  openEditor(deck.id);
};

$("#brandHome").onclick = () => { showView("library"); loadLibrary(); };
$("#editorBack").onclick = () => { showView("library"); loadLibrary(); };
$("#presenterBack").onclick = () => { showView("library"); loadLibrary(); };

// ==========================================================================
// EDITOR
// ==========================================================================
let deck = null;          // currently edited deck object
let saveTimer = null;

async function openEditor(deckId) {
  deck = await api("GET", deckPath(deckId));
  showView("editor");
  $("#deckName").value = deck.name;
  $("#defDuration").value = Math.round(deck.defaults.durationSec);
  $("#defFit").value = deck.defaults.fit;
  $("#defFade").value = deck.defaults.transition.durationMs;
  $("#bgColor").value = deck.background || "#000000";
  $("#mirrorChk").checked = !!deck.mirror;
  deck.thermal = deck.thermal || { enabled: false, unit: "F" };
  const th = deck.thermal;
  $("#thermalOn").checked = !!th.enabled;
  $("#thColdT").value = th.coldThreshold ?? "";
  $("#thBurnT").value = th.burnThreshold ?? "";
  $("#thColdL").value = th.coldLabel ?? "";
  $("#thBurnL").value = th.burnLabel ?? "";
  $("#thMin").value = th.minTemp ?? "";
  $("#thMax").value = th.maxTemp ?? "";
  renderSlides();
}

function setSaveState(s) {
  const el = $("#saveState");
  el.className = "save-state " + s;
  el.textContent = s === "saving" ? "saving…" : "saved";
}

function scheduleSave() {
  setSaveState("saving");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 500);
}

async function saveNow() {
  clearTimeout(saveTimer);
  try {
    await api("PUT", deckPath(deck.id), deck);
    setSaveState("saved");
  } catch (e) {
    toast("Save failed: " + e.message, true);
  }
}

// settings handlers
$("#deckName").oninput = (e) => { deck.name = e.target.value; scheduleSave(); };
$("#defDuration").onchange = (e) => { deck.defaults.durationSec = Math.round(parseFloat(e.target.value)) || 5; scheduleSave(); };
$("#defFit").onchange = (e) => { deck.defaults.fit = e.target.value; scheduleSave(); };
$("#defFade").onchange = (e) => { deck.defaults.transition.durationMs = parseInt(e.target.value) || 0; scheduleSave(); };
$("#bgColor").onchange = (e) => { deck.background = e.target.value; scheduleSave(); };
$("#mirrorChk").onchange = (e) => { deck.mirror = e.target.checked; scheduleSave(); };
const _thNum = (v) => (v === "" ? null : parseFloat(v));
$("#thermalOn").onchange = (e) => { deck.thermal.enabled = e.target.checked; scheduleSave(); };
$("#thColdT").onchange = (e) => { deck.thermal.coldThreshold = _thNum(e.target.value); scheduleSave(); };
$("#thBurnT").onchange = (e) => { deck.thermal.burnThreshold = _thNum(e.target.value); scheduleSave(); };
$("#thColdL").oninput = (e) => { deck.thermal.coldLabel = e.target.value; scheduleSave(); };
$("#thBurnL").oninput = (e) => { deck.thermal.burnLabel = e.target.value; scheduleSave(); };
$("#thMin").onchange = (e) => { deck.thermal.minTemp = _thNum(e.target.value); scheduleSave(); };
$("#thMax").onchange = (e) => { deck.thermal.maxTemp = _thNum(e.target.value); scheduleSave(); };
$("#presentBtn").onclick = async () => { await saveNow(); openPresenter(deck.id); };

function renderSlides() {
  const list = $("#slideList");
  list.innerHTML = "";
  $("#slideCount").textContent = deck.slides.length ? `(${deck.slides.length})` : "";
  $("#slidesEmpty").classList.toggle("hidden", deck.slides.length > 0);

  deck.slides.forEach((s, i) => {
    const isManual = s.mode === "manual";
    const row = document.createElement("div");
    row.className = "slide-row";
    row.draggable = true;
    row.dataset.idx = i;
    const isAbs = s.image.startsWith("/");
    row.innerHTML = `
      <span class="handle">⠿</span>
      <span class="idx">${i + 1}</span>
      <img src="${thumbUrl(deck.id, s.id)}" alt="" />
      <div class="slabel">
        <input class="lbl" value="${(s.label || "").replace(/"/g, "&quot;")}" placeholder="label" />
        ${isAbs ? `<div class="path" title="${s.image}">${s.image}</div>` : ""}
      </div>
      <div class="controls">
        <span class="seg mode">
          <button data-m="auto" class="${isManual ? "" : "on"}">Auto</button>
          <button data-m="manual" class="${isManual ? "on" : ""}">Manual</button>
        </span>
        <input type="number" class="dur-input ${isManual ? "hidden" : ""}" min="1" step="1"
               value="${Math.round(s.durationSec ?? deck.defaults.durationSec)}" title="seconds" />
        <select class="mini-select fit">
          <option value="">fit: default</option>
          <option value="cover">cover</option>
          <option value="contain">contain</option>
        </select>
        <select class="mini-select trans">
          <option value="">fade: default</option>
          <option value="cut">cut</option>
          <option value="crossfade">crossfade</option>
        </select>
        <input type="number" class="trans-ms hidden" min="0" step="50" title="crossfade ms" />
        <input type="number" class="slide-temp" step="1" placeholder="temp°" title="target temperature" />
        <button class="del-slide" title="Remove">🗑</button>
      </div>`;

    $(".fit", row).value = s.fit || "";
    $(".slide-temp", row).value = s.temperature == null ? "" : Math.round(s.temperature);
    $(".slide-temp", row).onchange = (e) => { s.temperature = e.target.value === "" ? null : Math.round(parseFloat(e.target.value)); scheduleSave(); };

    // per-slide transition controls
    const transSel = $(".trans", row);
    const transMs = $(".trans-ms", row);
    const syncTransUI = () => {
      if (!s.transition) { transSel.value = ""; transMs.classList.add("hidden"); }
      else if (s.transition.type === "cut") { transSel.value = "cut"; transMs.classList.add("hidden"); }
      else {
        transSel.value = "crossfade";
        transMs.classList.remove("hidden");
        transMs.value = s.transition.durationMs ?? deck.defaults.transition.durationMs;
      }
    };
    syncTransUI();
    transSel.onchange = (e) => {
      const v = e.target.value;
      if (v === "") s.transition = null;
      else if (v === "cut") s.transition = { type: "cut", durationMs: 0 };
      else s.transition = { type: "crossfade", durationMs: deck.defaults.transition.durationMs };
      syncTransUI();
      scheduleSave();
    };
    transMs.onchange = (e) => {
      if (s.transition && s.transition.type === "crossfade") {
        s.transition.durationMs = parseInt(e.target.value) || 0;
        scheduleSave();
      }
    };
    $(".lbl", row).oninput = (e) => { s.label = e.target.value; scheduleSave(); };
    $(".fit", row).onchange = (e) => { s.fit = e.target.value || null; scheduleSave(); };
    const durEl = $(".dur-input", row);
    durEl.onchange = (e) => { s.durationSec = Math.round(parseFloat(e.target.value)) || Math.round(deck.defaults.durationSec); scheduleSave(); };
    $$(".mode button", row).forEach((b) => {
      b.onclick = () => {
        s.mode = b.dataset.m;
        if (s.mode === "auto" && (s.durationSec == null)) s.durationSec = deck.defaults.durationSec;
        renderSlides();
        scheduleSave();
      };
    });
    $(".del-slide", row).onclick = () => {
      deck.slides.splice(i, 1);
      renderSlides();
      scheduleSave();
    };
    attachDrag(row);
    list.appendChild(row);
  });
}

// drag-to-reorder
let dragIdx = null;
function attachDrag(row) {
  row.addEventListener("dragstart", () => { dragIdx = +row.dataset.idx; row.classList.add("dragging"); });
  row.addEventListener("dragend", () => { dragIdx = null; row.classList.remove("dragging"); $$(".slide-row").forEach((r) => r.classList.remove("drop-target")); });
  row.addEventListener("dragover", (e) => { e.preventDefault(); row.classList.add("drop-target"); });
  row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
  row.addEventListener("drop", (e) => {
    e.preventDefault();
    const to = +row.dataset.idx;
    if (dragIdx === null || dragIdx === to) return;
    const [moved] = deck.slides.splice(dragIdx, 1);
    deck.slides.splice(to, 0, moved);
    renderSlides();
    scheduleSave();
  });
}

// image adding
const dz = $("#dropZone");
$("#browseBtn").onclick = () => $("#fileInput").click();
$("#fileInput").onchange = (e) => uploadFiles(e.target.files);
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));

async function uploadFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  setSaveState("saving");
  try {
    const res = await fetch(`${deckPath(deck.id)}/images`, { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    deck = await res.json();
    renderSlides();
    setSaveState("saved");
    toast(`Added ${files.length} image${files.length === 1 ? "" : "s"}`);
  } catch (e) {
    toast("Upload failed: " + e.message, true);
    setSaveState("saved");
  }
  $("#fileInput").value = "";
}

$("#pathBtn").onclick = async () => {
  const path = $("#pathInput").value.trim();
  if (!path) return;
  try {
    deck = await api("POST", `${deckPath(deck.id)}/images/from-path`, { path });
    $("#pathInput").value = "";
    renderSlides();
    toast("Added from path");
  } catch (e) {
    toast(e.message, true);
  }
};
$("#pathInput").onkeydown = (e) => { if (e.key === "Enter") $("#pathBtn").click(); };

// ==========================================================================
// PRESENTER
// ==========================================================================
let presentDeck = null;     // deck with slides, for filmstrip
let lastIndex = -1;

async function openPresenter(deckId, startIndex = 0) {
  presentDeck = await api("GET", deckPath(deckId));
  await api("POST", "/api/present/load", { deckId });
  showView("presenter");
  $("#presenterDeckName").textContent = presentDeck.name;
  lastIndex = -1;
  renderFilmstrip();
  await renderRecipeSwitch(deckId);
  startPreview();
  if (startIndex > 0) await present("jump", { index: startIndex });
}

async function renderRecipeSwitch(activeDeckId) {
  const decks = await api("GET", "/api/decks");
  const sel = $("#recipeSwitch");
  sel.innerHTML = "";
  decks.forEach((d) => {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.textContent = d.name;
    opt.selected = d.id === activeDeckId;
    sel.appendChild(opt);
  });
  sel.onchange = () => openPresenter(sel.value);
}

function effDur(s) { return s.durationSec ?? presentDeck.defaults.durationSec; }

function renderFilmstrip() {
  const strip = $("#filmstrip");
  strip.innerHTML = "";
  presentDeck.slides.forEach((s, i) => {
    const film = document.createElement("div");
    film.className = "film";
    film.dataset.idx = i;
    film.innerHTML = `
      <span class="liveflag">LIVE</span>
      <img src="${thumbUrl(presentDeck.id, s.id)}" alt="" />
      <div class="cap">
        <div class="cap-row"><span class="n">${i + 1}</span><span class="timing"></span></div>
        <div class="cap-row thermo"></div>
      </div>
      <div class="progress"></div>`;
    film.onclick = (e) => {
      if (!e.target.closest(".timing") && !e.target.closest(".thermo")) present("jump", { index: i });
    };
    strip.appendChild(film);
    renderFilmTiming(film, s, i);
    renderFilmThermo(film, s, i);
  });
}

// editable per-slide temperature in the filmstrip (live, saved to the JSON)
function renderFilmThermo(film, s, i) {
  const span = $(".thermo", film);
  if (!span) return;
  const th = presentDeck.thermal;
  if (!th || !th.enabled) { span.style.display = "none"; return; }
  span.style.display = "";
  span.innerHTML =
    `<span class="thermo-ico">◎</span>` +
    `<input class="film-temp" type="number" step="1" value="${s.temperature == null ? "" : Math.round(s.temperature)}" placeholder="—" title="target temperature" />` +
    `<span class="su">°${th.unit || "F"}</span>`;
  $(".film-temp", span).onchange = (e) =>
    saveTiming(s, i, { temperature: e.target.value === "" ? null : Math.round(parseFloat(e.target.value)) });
}

// inline per-slide timing editing in the presenter — saved straight to the
// deck JSON, live, without disrupting the current position
function renderFilmTiming(film, s, i) {
  const span = $(".timing", film);
  if (!span) return;
  if (s.mode === "manual") {
    span.innerHTML = `<button class="film-mode" title="Switch to timed (auto)">MANUAL</button>`;
  } else {
    span.innerHTML =
      `<input class="film-dur" type="number" min="1" step="1" value="${Math.round(effDur(s))}" title="seconds" />` +
      `<span class="su">s</span>` +
      `<button class="film-mode" title="Switch to manual hold">✋</button>`;
  }
  $(".film-mode", span).onclick = () => saveTiming(s, i, { mode: s.mode === "manual" ? "auto" : "manual" });
  const durEl = $(".film-dur", span);
  if (durEl) durEl.onchange = (e) => saveTiming(s, i, { durationSec: Math.round(parseFloat(e.target.value)) || Math.round(effDur(s)) });
}

async function saveTiming(s, i, changes) {
  if (changes.mode === "auto" && s.durationSec == null) s.durationSec = presentDeck.defaults.durationSec;
  Object.assign(s, changes);
  try {
    await api("POST", "/api/present/timing", { slideId: s.id, durationSec: s.durationSec ?? null, mode: s.mode, temperature: s.temperature ?? null });
    const film = $(`#filmstrip .film[data-idx="${i}"]`);
    if (film) { renderFilmTiming(film, s, i); renderFilmThermo(film, s, i); }
  } catch (e) {
    toast("Save failed: " + e.message, true);
  }
}

async function present(action, body) {
  try { await api("POST", `/api/present/${action}`, body); }
  catch (e) { toast(e.message, true); }
}

// transport buttons
$$("#view-presenter .transport [data-act]").forEach((b) => {
  b.onclick = () => present(b.dataset.act);
});
$("#mirrorBtn").onclick = () => present("mirror");

function updatePresenter(p) {
  if (!p || p.deckId !== (presentDeck && presentDeck.id)) return;

  // status pill
  const pill = $("#statusPill");
  let label = p.status, cls = p.status;
  if (p.awaitingManual) { label = "MANUAL — waiting"; cls = "manual"; }
  else if (p.status === "playing") label = "Playing";
  else if (p.status === "paused") label = "Paused";
  else if (p.status === "standby") label = "Standby";
  else if (p.status === "ended") label = "End — holding";
  pill.className = "status-pill " + cls;
  pill.textContent = label;

  // play/pause button glyph
  $("#playBtn").textContent = p.status === "playing" ? "⏸" : "▶";

  // mirror toggle reflects current state
  $("#mirrorBtn").classList.toggle("on", !!p.mirror);

  // counter
  $("#counter").textContent = `${p.slideCount ? p.index + 1 : 0} / ${p.slideCount}`;

  // manual overlay on the stage
  $("#manualOverlay").classList.toggle("hidden", !p.awaitingManual);

  // filmstrip cursor + progress
  $$("#filmstrip .film").forEach((film) => {
    const i = +film.dataset.idx;
    const isLive = i === p.index;
    film.classList.toggle("live", isLive);
    const prog = $(".progress", film);
    if (isLive && p.duration && p.remaining != null) {
      prog.style.width = `${Math.max(0, Math.min(100, (1 - p.remaining / p.duration) * 100))}%`;
    } else {
      prog.style.width = isLive && p.mode === "manual" ? "100%" : "0";
    }
  });

  // keep the live thumbnail in view
  if (p.index !== lastIndex) {
    lastIndex = p.index;
    const live = $(`#filmstrip .film[data-idx="${p.index}"]`);
    if (live) live.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }
}

// MJPEG preview stream control
function startPreview() { $("#previewImg").src = `/api/preview?t=${Date.now()}`; }
function stopPreview() { $("#previewImg").src = ""; }

// keyboard controls (presenter only, not while typing)
document.addEventListener("keydown", (e) => {
  if (view !== "presenter") return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  const map = {
    " ": "toggle", ArrowRight: "next", ArrowLeft: "prev",
    p: "toggle", P: "toggle", r: "replay", R: "replay",
  };
  if (e.key === "Escape") { e.preventDefault(); return present("stop"); }
  const act = map[e.key];
  if (act) { e.preventDefault(); present(act); }
});

// --------------------------------------------------------------------------
// boot
// --------------------------------------------------------------------------
connectWS();
loadLibrary();
