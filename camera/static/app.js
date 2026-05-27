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

const thumbUrl = (deckId, slideId) => `/api/decks/${deckId}/thumb/${slideId}`;
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
  const decks = await api("GET", "/api/decks");
  const grid = $("#deckGrid");
  grid.innerHTML = "";
  $("#libraryEmpty").classList.toggle("hidden", decks.length > 0);
  for (const d of decks) {
    const card = document.createElement("div");
    card.className = "deck-card";
    card.innerHTML = `
      <h3></h3>
      <div class="meta"></div>
      <div class="actions">
        <button class="btn primary" data-a="present">▶ Present</button>
        <button class="btn" data-a="edit">Edit</button>
        <button class="btn" data-a="dup">Duplicate</button>
        <button class="btn danger" data-a="del">Delete</button>
      </div>`;
    $("h3", card).textContent = d.name;
    $(".meta", card).textContent = `${d.slideCount} slide${d.slideCount === 1 ? "" : "s"} · ${fmtDate(d.updated)}`;
    $('[data-a="present"]', card).onclick = () => openPresenter(d.id);
    $('[data-a="edit"]', card).onclick = () => openEditor(d.id);
    $('[data-a="dup"]', card).onclick = async () => { await api("POST", `/api/decks/${d.id}/duplicate`); loadLibrary(); };
    $('[data-a="del"]', card).onclick = async () => {
      if (!confirm(`Delete "${d.name}"? This removes its images too.`)) return;
      await api("DELETE", `/api/decks/${d.id}`); loadLibrary();
    };
    grid.appendChild(card);
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
  deck = await api("GET", `/api/decks/${deckId}`);
  showView("editor");
  $("#deckName").value = deck.name;
  $("#defDuration").value = deck.defaults.durationSec;
  $("#defFit").value = deck.defaults.fit;
  $("#defFade").value = deck.defaults.transition.durationMs;
  $("#bgColor").value = deck.background || "#000000";
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
    await api("PUT", `/api/decks/${deck.id}`, deck);
    setSaveState("saved");
  } catch (e) {
    toast("Save failed: " + e.message, true);
  }
}

// settings handlers
$("#deckName").oninput = (e) => { deck.name = e.target.value; scheduleSave(); };
$("#defDuration").onchange = (e) => { deck.defaults.durationSec = parseFloat(e.target.value) || 5; scheduleSave(); };
$("#defFit").onchange = (e) => { deck.defaults.fit = e.target.value; scheduleSave(); };
$("#defFade").onchange = (e) => { deck.defaults.transition.durationMs = parseInt(e.target.value) || 0; scheduleSave(); };
$("#bgColor").onchange = (e) => { deck.background = e.target.value; scheduleSave(); };
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
        <input type="number" class="dur-input ${isManual ? "hidden" : ""}" min="0.1" step="0.5"
               value="${s.durationSec ?? deck.defaults.durationSec}" title="seconds" />
        <select class="mini-select fit">
          <option value="">fit: default</option>
          <option value="cover">cover</option>
          <option value="contain">contain</option>
        </select>
        <button class="del-slide" title="Remove">🗑</button>
      </div>`;

    $(".fit", row).value = s.fit || "";
    $(".lbl", row).oninput = (e) => { s.label = e.target.value; scheduleSave(); };
    $(".fit", row).onchange = (e) => { s.fit = e.target.value || null; scheduleSave(); };
    const durEl = $(".dur-input", row);
    durEl.onchange = (e) => { s.durationSec = parseFloat(e.target.value) || deck.defaults.durationSec; scheduleSave(); };
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
    const res = await fetch(`/api/decks/${deck.id}/images`, { method: "POST", body: fd });
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
    deck = await api("POST", `/api/decks/${deck.id}/images/from-path`, { path });
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

async function openPresenter(deckId) {
  presentDeck = await api("GET", `/api/decks/${deckId}`);
  await api("POST", "/api/present/load", { deckId });
  showView("presenter");
  $("#presenterDeckName").textContent = presentDeck.name;
  lastIndex = -1;
  renderFilmstrip();
  startPreview();
}

function effDur(s) { return s.durationSec ?? presentDeck.defaults.durationSec; }

function renderFilmstrip() {
  const strip = $("#filmstrip");
  strip.innerHTML = "";
  presentDeck.slides.forEach((s, i) => {
    const film = document.createElement("div");
    film.className = "film";
    film.dataset.idx = i;
    const badge = s.mode === "manual"
      ? `<span class="badge manual">MANUAL</span>`
      : `<span class="badge">${effDur(s)}s</span>`;
    film.innerHTML = `
      <span class="liveflag">LIVE</span>
      <img src="${thumbUrl(presentDeck.id, s.id)}" alt="" />
      <div class="cap"><span class="n">${i + 1}</span>${badge}</div>
      <div class="progress"></div>`;
    film.onclick = () => present("jump", { index: i });
    strip.appendChild(film);
  });
}

async function present(action, body) {
  try { await api("POST", `/api/present/${action}`, body); }
  catch (e) { toast(e.message, true); }
}

// transport buttons
$$("#view-presenter .transport [data-act]").forEach((b) => {
  b.onclick = () => present(b.dataset.act);
});

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
