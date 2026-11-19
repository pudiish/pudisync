"use strict";
const POLL_MS = 1500, OFFLINE_MS = 5000, HOLD_MS = 2000, FETCH_TIMEOUT_MS = 4000, PIN_KEY = "ambilight-pin";
const BOARD_KEY = "ambilight-board";
const TABS = ["home", "tune", "room", "settings"];
// Wide screens show one of these sections at a time; a phone shows the three tabs that hold them.
const SECTIONS = {overview: "Overview", monitor: "Monitor strip", bed: "Bed strip", mouse: "Mouse", looks: "Looks",
  room: "Room Studio", calibration: "Calibration", setup: "Setup"};
const FIRST = {home: "overview", tune: "monitor", room: "room", settings: "setup"};
// The phone tab that holds each section's own controls.
const TAB_OF = {overview: "home", monitor: "tune", bed: "home", mouse: "home", looks: "tune", room: "room",
  calibration: "settings", setup: "settings"};
// Old links and Home Screen bookmarks still land on the screen that now holds them.
const MOVED = {lights: "overview", effects: "overview", picture: "monitor", override: "monitor", advanced: "setup"};
const WIDE = matchMedia("(min-width:700px)");
const EDGE_KEYS = ["edge_bottom", "edge_right", "edge_top", "edge_left"];
const COLOUR_KEYS = ["saturation", "colour_match", "white_kelvin", "wb_r", "wb_g", "wb_b",
  "led_gamma", "contrast", "black_level", "colour_weight", "spread"];
const SWATCHES = [["Warm white", "#ffb46b"], ["Cool white", "#ffffff"], ["Red", "#ff2010"],
  ["Amber", "#ff8c00"], ["Green", "#20e050"], ["Cyan", "#00c8ff"], ["Blue", "#2040ff"], ["Magenta", "#e030ff"]];
const HUE_NAMES = [[18, "Red"], [40, "Orange"], [66, "Yellow"], [100, "Lime"], [155, "Green"], [195, "Cyan"],
  [220, "Sky blue"], [255, "Blue"], [285, "Purple"], [320, "Magenta"], [345, "Pink"], [360, "Red"]];
const WARMEST_K = 2200, COOLEST_K = 6500, RECENT_KEY = "pudisync-recent-colours", RECENT_MAX = 6;
const STYLES = {vivid: "Vivid has deep shadows and full highlights, and still lights the room in dark scenes.",
  immersive: "Immersive fills the room with the scene's colour, glides in calm scenes and keeps up in action.",
  pulse: "Pulse is Vivid moving with the bass: each beat sends a wave of colour out from the screen.",
  cinematic: "Cinematic glides slowly with a faint drift of light, for films. The sound only ever breathes it.",
  trippy: "Trippy folds and warps the screen's own colours into flowing patterns, sound or no sound."};
const PART_NAMES = {sync: "Scene", warm: "Lamp", solid: "Colour", off: "Off"};
const PART_TINT = {warm: "#ffb46b"};
const BAUDS = [115200, 230400, 460800, 500000, 921600, 1000000, 2000000];
const MANUAL_SSID = "\u0000manual";

const settle = blend => 0.06 * Math.pow(0.8 / 0.06, 1 - blend);
const pct = v => `${Math.round(v * 100)}%`;
const two = v => v.toFixed(2);
const offOr = v => v ? pct(v) : "Off";
/* Each fader writes the setting of its own name, unless it has `apply`; `local`
   ones belong to the page; `read` takes the value from a status other than by key. */
const FADERS = {
  master: {label: "Brightness", min: 1, max: 255, fmt: v => `${Math.max(1, Math.round(v / 2.55))}%`,
    read: s => s.wled_bri, apply: v => act("/api/wled/brightness", {value: v})},
  brightness: {label: "Sync brightness", min: 0, max: 100, scale: 100, fmt: pct},
  saturation: {label: "Colour", min: 0, max: 300, step: 5, scale: 100, fmt: pct},
  white_kelvin: {label: "Warmth", min: 2700, max: 6500, step: 100, fmt: v => `${v} K`},
  immersion: {label: "Immersion", min: 0, max: 100, scale: 100, fmt: offOr},
  adaptive: {label: "Adaptive brightness", min: 0, max: 100, scale: 100, fmt: offOr},
  hue_balance: {label: "Colour balance", min: 0, max: 100, scale: 100, fmt: offOr},
  dark_boost: {label: "Dark scenes", min: 0, max: 100, scale: 100, fmt: offOr},
  blend: {label: "Response", min: 1, max: 100, scale: 100, fmt: v => `${two(settle(v))} s`,
    say: v => `settles in ${two(settle(v))} seconds`},
  contrast: {label: "Contrast", min: 50, max: 250, step: 5, scale: 100, fmt: v => `${two(v)}×`},
  spread: {label: "Spread", min: 0, max: 80, scale: 100, fmt: pct},
  colour_weight: {label: "Colour weight", min: 0, max: 100, scale: 100, fmt: pct},
  wb_r: {label: "Red", min: 20, max: 100, scale: 100, fmt: two},
  wb_g: {label: "Green", min: 20, max: 100, scale: 100, fmt: two},
  wb_b: {label: "Blue", min: 20, max: 100, scale: 100, fmt: two},
  fps: {label: "Frame rate", min: 5, max: 120, step: 5, fmt: v => `${v} fps`},
  fade_leds: {label: "Spill past the screen", min: 0, max: 120, fmt: v => v ? `${v} LEDs` : "Hard edge"},
  bed_brightness: {label: "Bed brightness", min: 0, max: 100, scale: 100, fmt: pct},
  bed_speed: {label: "Speed", min: 0, max: 255, fmt: v => pct(v / 255)},
  fx_speed: {label: "Speed", min: 0, max: 255, local: true, fmt: v => pct(v / 255), apply: () => rerunEffect()},
  fx_intensity: {label: "Intensity", min: 0, max: 255, local: true, fmt: v => pct(v / 255), apply: () => rerunEffect()},
  part_brightness: {label: "Brightness", min: 0, max: 100, scale: 100, local: true, fmt: pct, apply: () => drawPaint()},
  trippy_speed: {label: "Motion speed", min: 0, max: 200, scale: 100, fmt: pct},
  trippy_warp: {label: "Warp", min: 0, max: 100, scale: 100, fmt: offOr},
  trippy_colour: {label: "Colour intensity", min: 0, max: 100, scale: 100, fmt: pct},
  trippy_texture: {label: "Texture", min: 0, max: 100, scale: 100, fmt: offOr},
  cinematic_motion: {label: "Drift", min: 0, max: 100, scale: 100, fmt: offOr},
};

const $ = id => document.getElementById(id);
const $$ = sel => [...document.querySelectorAll(sel)];
const faders = {}, editedAt = {}, pending = {};
let state = null, pin = readPin(), offline = false, pinDismissed = false, currentTab = "", currentSection = "", lastSeen = 0;
let boardUrls = readBoardUrls(), reqSeq = 0;
let wizardLive = false, markerAt = null, effects = [], boardEffects = [], metaLoadedAt = 0;
let lastColour = SWATCHES[0][1], lastFx = null, sceneView = "", lastScene = "";
let zones = [], zonesJson = "", previewKey = "", urlsKey = "", flashLogSize = -1, profilesKey = "";
let selection = null, dragFrom = null, partMode = "solid", partKeep = true, webcamTimer = 0;

/* ---------- plumbing ---------- */
class HubError extends Error {
  constructor(kind, message) { super(message); this.kind = kind; }
}

function readPin() {
  try { return localStorage.getItem(PIN_KEY) || ""; }
  catch (err) { console.warn("PIN storage unavailable", err); return ""; }
}

function storePin(value) {
  pin = value;
  try { value ? localStorage.setItem(PIN_KEY, value) : localStorage.removeItem(PIN_KEY); }
  catch (err) { console.warn("PIN kept for this visit only", err); }
}

const pinHeaders = () => pin ? {"X-Ambilight-Pin": pin} : {};

/* Appearance: this device's pick ("" follows the device), resolved to light or dark as index.html does at load. */
const DEVICE_LIGHT = matchMedia("(prefers-color-scheme: light)"), THEME_KEY = "pudisync-theme";
let themeChoice = "";
try { themeChoice = localStorage.getItem(THEME_KEY) || ""; }
catch (err) { console.warn("appearance storage unavailable", err); }

function pickTheme(choice) {
  themeChoice = choice;
  try { choice ? localStorage.setItem(THEME_KEY, choice) : localStorage.removeItem(THEME_KEY); }
  catch (err) { console.warn("appearance kept for this visit only", err); }
  showTheme();
}

function showTheme() {
  document.documentElement.dataset.theme = themeChoice || (DEVICE_LIGHT.matches ? "light" : "dark");
  $$("[data-action=theme]").forEach(b => press(b, b.dataset.value === themeChoice));
  previewKey = "";
  renderPreview();
  drawPaint();
}

/* Where the board's own page is, kept from the last good status so it can be
   offered when the hub itself is out of reach. */
function readBoardUrls() {
  try { return JSON.parse(localStorage.getItem(BOARD_KEY) || "[]"); }
  catch (err) { console.warn("board address unavailable", err); return []; }
}

function rememberBoardUrls(urls) {
  if (!Array.isArray(urls) || !urls.length) return;
  try { localStorage.setItem(BOARD_KEY, JSON.stringify(urls)); }
  catch (err) { console.warn("board address kept for this visit only", err); }
  boardUrls = urls;
}

/* A sleeping Mac, or a phone that has moved to mobile data, never refuses the
   connection: without a deadline the page shows stale lights for a minute. */
async function timedFetch(url, options) {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, Object.assign({}, options, {signal: abort.signal}));
  } finally {
    clearTimeout(timer);
  }
}

/* A `file` (a backup's text) goes as a JSON POST; everything else is a plain GET. */
async function api(path, params, file) {
  const query = new URLSearchParams(params || {}).toString();
  const init = file == null ? {cache: "no-store", headers: pinHeaders()}
    : {method: "POST", cache: "no-store", headers: {...pinHeaders(), "Content-Type": "application/json"}, body: file};
  let res;
  try {
    res = await timedFetch(path + (query ? `?${query}` : ""), init);
  } catch (err) {
    throw new HubError("offline", "Can't reach the hub. Is this device on the home Wi-Fi?");
  }
  const body = await res.json().catch(() => ({}));
  showLocked(res.status === 403);
  if (res.status === 403) throw new HubError("locked", "Enter the hub's PIN to continue.");
  if (res.status === 429) throw new HubError("throttled", body.error || "Too many wrong PINs; wait a minute.");
  if (!res.ok || body.error) throw new HubError("failed", body.error || `The hub answered ${res.status}.`);
  if (body.ok === false) throw new HubError("failed", "The board didn't respond. Is it powered and on Wi-Fi?");
  return body;
}

/* Replies can overtake each other: a poll sent before a tap may land after the
   tap's own reply. Only the newest request's status is drawn. */
function newerGate() {
  let drawn = 0;
  return seq => (seq > drawn ? (drawn = seq, true) : false);
}
const fresh = newerGate();

function accept(body, seq) {
  if (body && "running" in body && fresh(seq)) render(body);
}

function reportError(err) {
  if (err.kind !== "locked") toast(err.message, "error");
}

async function act(path, params, okMessage) {
  const seq = ++reqSeq;
  try {
    const body = await api(path, params);
    accept(body, seq);
    if (okMessage) toast(okMessage);
    return body;
  } catch (err) {
    reportError(err);
    return null;
  }
}

async function load(path, show) {
  try { show(await api(path)); }
  catch (err) { reportError(err); }
}

const setKey = (key, value, okMessage) => act("/api/set", {[key]: value}, okMessage);
const recent = time => Date.now() - (time || 0) < HOLD_MS;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const press = (el, on, attr = "aria-pressed") => el.setAttribute(attr, String(!!on));

function debounce(fn, ms) {
  let timer = 0;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

/* First call at once, then at most one per `ms`, and always the last: a drag the lights follow live. */
function throttle(fn, ms) {
  let last = 0, timer = 0;
  return (...args) => {
    clearTimeout(timer);
    const run = () => { last = Date.now(); fn(...args); };
    const wait = last + ms - Date.now();
    if (wait <= 0) run();
    else timer = setTimeout(run, wait);
  };
}

function setText(el, text) { if (el.textContent !== text) el.textContent = text; }

function make(tag, props, ...children) {
  const el = Object.assign(document.createElement(tag), props);
  el.append(...children);
  return el;
}

function button(label, className, onClick, ariaLabel) {
  const b = make("button", {type: "button", className, textContent: label});
  b.addEventListener("click", onClick);
  if (ariaLabel) b.setAttribute("aria-label", ariaLabel);
  return b;
}

function fillInput(el, value) {
  if (value != null && el !== document.activeElement && !el.dataset.dirty && el.value !== String(value))
    el.value = String(value);
}

function toast(message, kind = "info", action) {
  const box = make("div", {className: `toast ${kind}`}, make("p", {textContent: message}));
  if (action) box.append(button(action.label, "btn primary small", () => { box.remove(); action.run(); }));
  box.append(button("×", "toast-close", () => box.remove(), "Dismiss"));
  const area = $("toasts");
  while (area.children.length >= 3) area.firstElementChild.remove();
  area.append(box);
  setTimeout(() => box.remove(), action ? 12000 : kind === "error" ? 8000 : 4000);
}

function friendly(error) {
  if (/screen recording|not permitted|permission/i.test(error))
    return "macOS is blocking screen capture. Open System Settings › Privacy & Security › Screen Recording, allow the hub, then restart it.";
  if (/udp send failed|could not reach the lights|host is down|unreachable|timed out|no route/i.test(error))
    return "Can't reach the LED controller. Check it's powered on and joined to Wi-Fi.";
  if (/serial/i.test(error))
    return "The USB link to the controller failed. Check the cable, or switch output to Wi-Fi in Settings.";
  if (/asleep|lid is closed/i.test(error))
    return "Your Mac's screen is asleep or its lid is closed. Sync resumes on its own when it wakes.";
  if (/display|monitor/i.test(error))
    return "The display being captured isn't available. Pick another display in Settings.";
  return `The hub reported a problem: ${error}`;
}

/* ---------- polling and the link ---------- */
let pollTimer = 0, pollBusy = false;

async function refresh() {
  const seq = ++reqSeq;
  try {
    const s = await api("/api/status");
    accept(s, seq);
    rememberBoardUrls(s.board_urls);
    lastSeen = Date.now();
    if (!effects.length) loadEffects();
    if (!Object.keys(builtinLooks).length) loadLooks();
    showLink("ok");
    return true;
  } catch (err) {
    showLink(err.kind);
    return false;
  }
}

function ago(ms) {
  const s = Math.round(ms / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

/* Everything the page says about its links to the hub and the board. Pure, so
   the tests can run it for each situation. While syncing, a lost board shows
   in the hero and the engine's own error instead. */
function linkView({kind, lastSeen, now, wledReachable, syncing, boardUrls}) {
  const view = {offline: null, throttled: null, board: null, boardUrl: null};
  if (kind === "offline") {
    // The last address is the one the board answered on most recently.
    view.boardUrl = (boardUrls && boardUrls.length) ? boardUrls[boardUrls.length - 1] : null;
    view.offline = `Can't reach the hub${lastSeen ? ` · last seen ${ago(now - lastSeen)}` : ""}. Retrying. ` +
      "If your Mac is asleep or the hub is stopped, wake or start it" +
      (view.boardUrl ? ", or use the strip's own page, which works without the Mac. " : ". ") +
      "Away from home? The hub only answers on your home Wi-Fi.";
  } else if (kind === "throttled") {
    view.throttled = "Too many wrong PINs from this device. Wait a minute, then try again.";
  } else if (kind === "ok" && wledReachable === false && !syncing) {
    view.board = "The LED board isn't answering. It may be restarting or off Wi-Fi; the hub keeps trying.";
  }
  return view;
}

function showLink(kind) {
  const view = linkView({kind, lastSeen, now: Date.now(), wledReachable: state && state.wled_reachable,
    syncing: !!state && modeOf(state) === "sync", boardUrls});
  setOffline(kind === "offline");
  setText($("offline-text"), view.offline || "");
  $("board-link").hidden = !view.boardUrl;
  if (view.boardUrl) $("board-link").href = view.boardUrl;
  $("throttled-banner").hidden = !view.throttled;
  setText($("throttled-text"), view.throttled || "");
  $("board-banner").hidden = !view.board;
  setText($("board-text"), view.board || "");
  if (kind !== "ok") setText($("link-word"), kind === "offline" ? "Offline" : "Locked");
  else if (state) renderHero(state);
}

async function poll() {
  clearTimeout(pollTimer);
  if (pollBusy) return;
  pollBusy = true;
  const ok = await refresh();
  pollBusy = false;
  if (!document.hidden) pollTimer = setTimeout(poll, ok ? POLL_MS : OFFLINE_MS);
}

function setOffline(on) {
  $("offline-banner").hidden = !on;
  $("screens").inert = on || !state;
  if (offline === on) return;
  offline = on;
  if (state) renderHero(state);
}

function showLocked(on) {
  $("locked-banner").hidden = !on;
  if (on && !pinDismissed) askPin();
}

function askPin() {
  if ($("unlock").open) return;
  setText($("unlock-msg"), pin ? "That PIN wasn't accepted. Try again." : "This hub is protected. Enter its PIN.");
  $("unlock-pin").value = "";
  $("unlock").showModal();
}

/* ---------- what the lights are doing ---------- */
const modeOf = s => s.mode || (s.running ? "sync" : s.wled_on ? "effect" : "off");

/* The scene the board shows: screen sync, a plain colour (effect 0) or an effect; or off. */
function sceneOf(s) {
  const mode = modeOf(s);
  if (mode === "sync") return s.source === "room" ? "room" : "screen";
  if (mode === "off") return "off";
  return s.wled_fx && s.wled_fx !== 0 ? "effect" : "colour";
}

/* A tap shows at once and holds until the hub's reply, or HOLD_MS, catches up. */
function held(key, actual) {
  const p = pending[key];
  if (!p) return actual;
  if (p.value === actual || !recent(p.at)) { delete pending[key]; return actual; }
  return p.value;
}

function hold(key, value) { pending[key] = {value, at: Date.now()}; }

/* An action whose taps show at once: on failure the page goes straight back to the truth. */
async function actHeld(keys, path, params) {
  const body = await act(path, params);
  if (!body) {
    keys.forEach(k => { delete pending[k]; });
    if (state) render(state);
  }
  return body;
}

function colourName(hex) {
  const swatch = SWATCHES.find(([, h]) => h === hex);
  if (swatch) return swatch[0];
  const k = kelvinOf(hex);
  if (k) return k < 3300 ? "Warm white" : k < 4800 ? "Natural white" : k < 5800 ? "Cool white" : "Daylight";
  const {h, s} = hexToHsv(hex);
  if (s < 0.12) return "White";
  const name = HUE_NAMES.find(([upTo]) => h < upTo)[1];
  return s < 0.45 ? `Pale ${name.toLowerCase()}` : name;
}

function effectName(fx) {
  const curated = effects.find(e => e.fx === fx);
  return curated ? curated.name : boardEffects[fx] || `Effect ${fx}`;
}

/* The hero's words for a status. Pure, so the tests can run every case. */
function heroView(s, {offline, scene, effectName}) {
  if (offline) return {lamp: "off", word: "Offline", state: "Hub offline", hint: "Waiting for the hub to answer."};
  const fault = s.wled_reachable === false;
  if (scene === "screen") return fault
    ? {lamp: "fault", word: "No link", state: "Syncing, board not answering", hint: "The hub keeps sending; the board may be restarting."}
    : {lamp: "on", word: "Syncing", state: "Following your screen", hint: "Tap to turn every light off."};
  if (scene === "room") return fault
    ? {lamp: "fault", word: "No link", state: "Room scene, board not answering", hint: "The hub keeps sending; the board may be restarting."}
    : {lamp: "effect", word: "Room", state: s.room_name || "Room Studio", hint: "Tap to turn every light off."};
  if (fault) return {lamp: "fault", word: "No board", state: "Can't reach the board", hint: "Tap to start screen sync once it's back."};
  if (scene === "colour") return {lamp: "effect", word: "Colour", state: s.wled_col ? colourName(`#${s.wled_col}`) : "Colour",
    hint: "Tap to turn every light off."};
  if (scene === "effect") return {lamp: "effect", word: "Effect", state: effectName(s.wled_fx), hint: "Tap to turn every light off."};
  return {lamp: "off", word: "Off", state: "Off", hint: "Tap to start screen sync."};
}

function render(s) {
  state = s;
  $("screens").inert = offline;
  renderHero(s);
  renderScene(s);
  renderPreview();
  for (const key in faders) {
    const spec = FADERS[key];
    if (!spec.local) setFader(key, spec.read ? spec.read(s) : s[key]);
  }
  faders.master.input.disabled = s.wled_bri == null;
  renderBed(s);
  renderMouse(s);
  $$("[data-switch]").forEach(b => { if (!recent(editedAt[b.dataset.switch])) press(b, s[b.dataset.switch], "aria-checked"); });
  $("engine-banner").hidden = !s.last_error;
  if (s.last_error) setText($("engine-error"), friendly(s.last_error));
  renderProfiles(s);
  renderSound(s);
  if (window.renderRoom) window.renderRoom(s);
  renderZonesFromStatus(s);
  renderSettings(s);
  renderTiming(s);
  if ($("parts").open) drawPaint();
}

function renderHero(s) {
  const scene = held("scene", sceneOf(s));
  const view = heroView(s, {offline, scene, effectName});
  $("head-lamp").dataset.state = view.lamp;
  if (!offline) setText($("link-word"), view.word);
  setText($("power-state"), view.state);
  setText($("power-hint"), view.hint);
  press($("power"), scene !== "off" && !offline, "aria-checked");
  setText($("fps"), s.running ? `${Number(s.output_fps ?? s.measured_fps ?? 0).toFixed(0)} fps` : "—");
  const safety = s.safety || {};
  setText($("amps"), s.running && safety.power_ma != null ? `${(safety.power_ma / 1000).toFixed(2)} A` : "—");
  $$("[data-flag]").forEach(chip => { chip.hidden = !s.running || !safety[chip.dataset.flag]; });
  $("meters").hidden = !s.running;
  document.documentElement.style.setProperty("--glow", glowOf(s, scene));
}

function glowOf(s, scene) {
  if (offline || scene === "off") return "transparent";
  if (scene === "colour" && s.wled_col) return `#${s.wled_col}`;
  if (scene === "screen" && (s.preview || []).length) return average(s.preview);
  return "var(--accent)";
}

function average(list) {
  const sum = [0, 0, 0];
  for (const hex of list) { const n = parseInt(hex.slice(1), 16); sum[0] += n >> 16 & 255; sum[1] += n >> 8 & 255; sum[2] += n & 255; }
  return `rgb(${sum.map(v => Math.round(v / list.length)).join(" ")})`;
}

function renderScene(s) {
  const scene = held("scene", sceneOf(s));
  if (scene !== lastScene) sceneView = "";   // the lights changed, perhaps from another device: show that
  lastScene = scene;
  const pane = sceneView || (scene === "off" ? "screen" : scene);
  $$("[data-action=scene]").forEach(b => press(b, b.dataset.value === scene));
  $("pane-screen").hidden = pane !== "screen";
  $("pane-colour").hidden = pane !== "colour";
  $("pane-effect").hidden = pane !== "effect";
  $("pane-room").hidden = pane !== "room";
  $("fx-serial").hidden = s.output !== "serial" || pane === "screen";
  const colour = held("colour", scene === "colour" && s.wled_col ? `#${s.wled_col}` : null);
  if (colour) lastColour = colour;
  $$("#swatches .swatch").forEach(b => press(b, b.dataset.hex === colour));
  fillInput($("colour"), colour);
  const fx = scene === "effect" ? held("fx", s.wled_fx) : null;
  if (fx != null) lastFx = fx;
  $$("#fx-tiles .tile").forEach(t => press(t, Number(t.dataset.fx) === fx));
}

/* ---------- live preview ---------- */
function colourAt(index, count, list) {
  return list.length ? list[Math.min(list.length - 1, Math.floor(index * list.length / count))] : null;
}

function isLit(hex) {
  const n = parseInt(hex.slice(1), 16);
  return (n >> 16 & 255) * 0.299 + (n >> 8 & 255) * 0.587 + (n & 255) * 0.114 > 14;
}

function frameGeometry(s, w, h) {
  const counts = EDGE_KEYS.map(k => Math.max(0, s[k] | 0));
  const x0 = 12, y0 = 12, x1 = w - 12, y1 = h - 12;
  const size = clamp(Math.min((x1 - x0) / Math.max(1, counts[0], counts[2]),
    (y1 - y0) / Math.max(1, counts[1], counts[3])) * 0.72, 2, 8);
  const sides = [t => [x0 + t * (x1 - x0), y1], t => [x1, y1 - t * (y1 - y0)],
    t => [x1 - t * (x1 - x0), y0], t => [x0, y0 + t * (y1 - y0)]];
  const leds = [];
  counts.forEach((count, side) => {
    for (let k = 0; k < count; k++) leds.push({index: leds.length, at: sides[side]((k + 0.5) / count)});
  });
  return {leds, size, screen: [x0 + 12, y0 + 12, x1 - x0 - 24, y1 - y0 - 24]};
}

function stripGeometry(s, w, h) {
  const n = Math.max(1, s.led_count | 0), cells = Math.min(n, 120), span = w - 24;
  const xOf = i => 12 + (i + 0.5) / n * span;
  const leds = [];
  for (let k = 0; k < cells; k++) {
    const index = Math.floor((k + 0.5) * n / cells);
    leds.push({index, at: [xOf(index), h - 12]});
  }
  const a = xOf(Math.min(s.left_led, s.right_led)), b = xOf(Math.max(s.left_led, s.right_led));
  return {leds, size: clamp(span / cells * 0.72, 2, 8), screen: [a, 8, Math.max(8, b - a), h - 34]};
}

function canvasFor(canvas, s) {
  const w = canvas.clientWidth;
  if (!s || !w) return null;
  const frame = s.layout === "frame";
  const h = Math.round(w * (frame ? clamp((s.screen_mm_h || 336) / (s.screen_mm_w || 597), 0.35, 0.6) : 0.28));
  return {canvas, w, h, geo: (frame ? frameGeometry : stripGeometry)(s, w, h)};
}

function paintCanvas(g, label, colourOf) {
  const {canvas, w, h, geo} = g, dpr = window.devicePixelRatio || 1, css = getComputedStyle(document.documentElement);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  canvas.style.height = `${h}px`;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  Object.assign(ctx, {fillStyle: css.getPropertyValue("--well"), textAlign: "center", textBaseline: "middle",
    font: "600 11px -apple-system, system-ui, sans-serif"});
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(...geo.screen, 6) : ctx.rect(...geo.screen);
  ctx.fill();
  ctx.fillStyle = css.getPropertyValue("--faint");
  ctx.fillText(label, geo.screen[0] + geo.screen[2] / 2, geo.screen[1] + geo.screen[3] / 2);
  const off = css.getPropertyValue("--led-off");
  for (const led of geo.leds) {
    const {colour, glow, big} = colourOf(led.index);
    const size = big ? geo.size * 1.5 : geo.size;
    Object.assign(ctx, {shadowColor: glow ? colour : "transparent", shadowBlur: glow ? geo.size * 2.2 : 0,
      fillStyle: colour || off});
    ctx.beginPath();
    ctx.arc(led.at[0], led.at[1], size / 2, 0, Math.PI * 2);
    ctx.fill();
  }
}

function renderPreview() {
  const s = state, g = canvasFor($("preview"), s);
  if (!g) return;
  const list = s.running ? s.preview || [] : [];
  const key = [g.w, s.layout, s.led_count, ...EDGE_KEYS.map(k => s[k]), s.left_led, s.right_led,
    s.screen_mm_w, s.screen_mm_h, sceneOf(s), s.wled_col, s.wled_fx, effects.length, list.join("")].join("|");
  if (key === previewKey) return;
  previewKey = key;
  const scene = sceneOf(s), n = Math.max(1, s.led_count), solid = scene === "colour" && s.wled_col ? `#${s.wled_col}` : null;
  const label = scene === "screen" ? "" : scene === "off" ? "Lights off" : scene === "effect" ? effectName(s.wled_fx) : "";
  paintCanvas(g, label, i => {
    const live = solid || colourAt(i, n, list), lit = live && isLit(live);
    return {colour: lit ? live : null, glow: lit};
  });
}

/* ---------- faders ---------- */
function faderValue(key) {
  const f = faders[key];
  return Number(f.input.value) / (f.spec.scale || 1);
}

function showFader(key) {
  const f = faders[key], v = faderValue(key), text = f.spec.fmt(v);
  setText(f.out, text);
  f.input.setAttribute("aria-valuetext", f.spec.say ? f.spec.say(v) : text);
  f.input.style.setProperty("--fill", `${(f.input.value - f.input.min) / (f.input.max - f.input.min) * 100}%`);
}

function setFader(key, value, force) {
  const f = faders[key];
  if (!f || value == null || (!force && (document.activeElement === f.input || recent(editedAt[key])))) return;
  const ui = String(Math.round(value * (f.spec.scale || 1) * 1000) / 1000);
  if (f.input.value !== ui) f.input.value = ui;
  showFader(key);
}

function buildFader(host) {
  const key = host.dataset.fader, spec = FADERS[key], id = `f-${key}`;
  const out = make("output");
  out.setAttribute("for", id);
  const input = make("input", {type: "range", id, min: spec.min, max: spec.max, step: spec.step || 1});
  host.append(make("label", {htmlFor: id, textContent: spec.label}), out, input);
  if (host.dataset.hint) {
    host.append(make("p", {className: "hint", id: `${id}-hint`, textContent: host.dataset.hint}));
    input.setAttribute("aria-describedby", `${id}-hint`);
  }
  faders[key] = {input, out, spec};
  const send = () => spec.apply ? spec.apply(faderValue(key)) : setKey(key, faderValue(key));
  const soon = debounce(send, 150);
  input.addEventListener("input", () => { editedAt[key] = Date.now(); showFader(key); soon(); });
  // Letting go sends the final value at once, and holds it against polls still in flight.
  input.addEventListener("change", () => { editedAt[key] = Date.now(); });
  showFader(key);
}

/* ---------- scenes: one choice for every light that matches ---------- */
let effectsLoading = false;
async function loadEffects() {
  if (effectsLoading) return;
  effectsLoading = true;
  try {
    const body = await api("/api/effects");
    effects = Array.isArray(body) ? body : body.effects || [];
    $("fx-tiles").replaceChildren(...effects.map(effectTile));
    if (state) { renderScene(state); renderHero(state); renderBed(state); }
  } catch (err) {
    $("fx-tiles").replaceChildren(make("p", {className: "hint", textContent: `Couldn't load effects. ${err.message}`}));
  } finally {
    effectsLoading = false;
  }
}

function effectTile(fx) {
  const tile = button("", "tile", () => pickEffect(fx));
  tile.dataset.fx = fx.fx;
  tile.setAttribute("aria-pressed", "false");
  tile.append(make("b", {textContent: fx.name}), make("span", {textContent: fx.about || ""}));
  return tile;
}

function pickEffect(fx) {
  if (lastFx !== fx.fx) {
    setFader("fx_speed", fx.speed, true);
    setFader("fx_intensity", fx.intensity, true);
  }
  runEffect(fx.fx);
}

function runEffect(fx) {
  lastFx = fx;
  sceneView = "";
  hold("scene", "effect");
  hold("fx", fx);
  if (state) renderScene(state);
  return actHeld(["scene", "fx"], "/api/effect/run", {fx, speed: faderValue("fx_speed"),
    intensity: faderValue("fx_intensity"), color: lastColour.slice(1)});
}

function rerunEffect() {
  if (state && sceneOf(state) === "effect" && lastFx != null) runEffect(lastFx);
}

function runColour(hex) {
  const colour = lastColour = hex.toLowerCase();
  sceneView = "";
  hold("scene", "colour");
  hold("colour", colour);
  if (state) { renderScene(state); renderHero(state); }
  return actHeld(["scene", "colour"], "/api/effect/run", {fx: 0, color: colour.slice(1)});
}

function startRoom() {
  hold("scene", "room");
  if (state) { renderScene(state); renderHero(state); }
  return actHeld(["scene"], "/api/room/start");
}

function startSync() {
  hold("scene", "screen");
  if (state) { renderScene(state); renderHero(state); }
  return actHeld(["scene"], "/api/sync/on");
}

/* Every scene button acts at once: no pane that only changes the view. */
function pickScene(scene) {
  if (scene === "screen") return startSync();
  if (scene === "room") return startRoom();
  if (scene === "colour") return runColour(lastColour);
  const first = effects[0];
  if (lastFx == null && first) pickEffect(first);
  else if (lastFx != null) runEffect(lastFx);
  else toast("Effects are still loading.", "error");
}

const powerBusy = {on: false};
async function togglePower() {
  if (powerBusy.on || !state) return;
  powerBusy.on = true;
  const btn = $("power");
  btn.setAttribute("aria-busy", "true");
  const turnOff = sceneOf(state) !== "off";
  hold("scene", turnOff ? "off" : "screen");
  renderHero(state);
  renderScene(state);
  try { await actHeld(["scene"], turnOff ? "/api/lights/off" : "/api/sync/on"); }
  finally { powerBusy.on = false; btn.removeAttribute("aria-busy"); if (state) render(state); }
}

/* Trying a look is safe: Undo puts back every setting it changed, and the look that was in use. */
async function pickStyle(name) {
  const look = lookOf(name), before = state && look && {profile: state.profile};
  if (before) Object.keys(look).forEach(k => { before[k] = state[k]; });
  hold("profile", name);
  renderProfiles(state);
  const body = await actHeld(["profile"], `/api/profile/${encodeURIComponent(name)}`);
  if (!body) return;
  if (before) toast(`Now using ${lookTitle(name)}.`, "info", {label: "Undo", run: () => act("/api/set", before)});
  if (!body.running) startSync();   // a style is a way of following the screen
}

function buildSwatches(host, pick) {
  host.replaceChildren(...SWATCHES.map(([name, hex]) => {
    const b = button("", "swatch", () => pick(hex), name);
    b.dataset.hex = hex;
    b.style.background = hex;
    b.setAttribute("aria-pressed", "false");
    return b;
  }));
}

/* ---------- colour wheel: red at the top and round clockwise, white at the centre ---------- */
function hexToHsv(hex) {
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
  const v = Math.max(r, g, b), d = v - Math.min(r, g, b);
  const h = d === 0 ? 0 : v === r ? ((g - b) / d + 6) % 6 : v === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return {h: h * 60, s: v ? d / v : 0, v};
}

function hsvToHex(h, s, v = 1) {
  const f = n => { const k = (n + h / 60) % 6; return v - v * s * Math.max(0, Math.min(k, 4 - k, 1)); };
  return "#" + [f(5), f(3), f(1)].map(c => Math.round(c * 255).toString(16).padStart(2, "0")).join("");
}

/* A white of that colour temperature as an RGB strip makes it (Tanner Helland's fit to the blackbody curve). */
function kelvinToHex(kelvin) {
  const t = kelvin / 100;
  const r = t <= 66 ? 255 : 329.698727446 * (t - 60) ** -0.1332047592;
  const g = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * (t - 60) ** -0.0755148492;
  const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  return "#" + [r, g, b].map(c => Math.round(Math.max(0, Math.min(255, c))).toString(16).padStart(2, "0")).join("");
}

/* The temperature a colour is the white of, or null when it is a colour rather than a white. */
function kelvinOf(hex) {
  const channels = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const want = channels(hex);
  let best = null, gap = 7;
  for (let k = WARMEST_K; k <= COOLEST_K; k += 50) {
    const d = channels(kelvinToHex(k)).reduce((sum, c, i) => sum + Math.abs(c - want[i]), 0);
    if (d < gap) { best = k; gap = d; }
  }
  return best;
}

const WHITES = `linear-gradient(${[COOLEST_K, 5500, 4500, 3500, 2800, WARMEST_K].map(kelvinToHex).join(",")})`;

function readRecent() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").filter(h => /^#[0-9a-f]{6}$/.test(h)); }
  catch (err) { console.warn("recent colours unavailable", err); return []; }
}

let recentColours = readRecent();

function rememberColour(hex) {
  recentColours = [hex, ...recentColours.filter(h => h !== hex)].slice(0, RECENT_MAX);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(recentColours)); }
  catch (err) { console.warn("recent colours kept for this visit only", err); }
  $$(".wheel").forEach(w => w.showRecent());
}

/* Turns <div class="wheel"> into a picker that behaves like an input: .value is "#rrggbb", "input" fires while
   it moves and "change" when it is let go. Colour is hue round the rim; White is cool at the top, warm below. */
function buildWheel(host) {
  const thumb = make("span", {className: "wheel-thumb"});
  const disc = make("div", {className: "wheel-disc", tabIndex: 0}, thumb);
  const said = make("output", {className: "wheel-said"});
  const recent = make("div", {className: "recent"});
  const modes = make("div", {className: "seg"});
  modes.setAttribute("role", "group");
  modes.setAttribute("aria-label", "Colour or white");
  for (const [name, label] of [["colour", "Any colour"], ["white", "Whites"]]) {
    const b = button(label, "", () => pickMode(name));
    b.dataset.wheelMode = name;
    modes.append(b);
  }
  disc.style.setProperty("--whites", WHITES);
  disc.setAttribute("role", "slider");
  disc.setAttribute("aria-roledescription", "colour wheel");
  disc.setAttribute("aria-label", host.dataset.label);
  host.append(modes, disc, said, recent);

  let hex = "", mode = "colour", pos = {x: 0, y: 0}, dragging = false, calm = 0;

  function draw() {
    disc.dataset.mode = mode;
    [...modes.children].forEach(b => press(b, b.dataset.wheelMode === mode));
    thumb.style.left = `${50 + 50 * pos.x}%`;
    thumb.style.top = `${50 + 50 * pos.y}%`;
    thumb.style.background = hex;
    const k = mode === "white" && kelvinOf(hex);
    const words = `${colourName(hex)} · ${k ? `${k} K` : hex.toUpperCase()}`;
    setText(said, words);
    disc.setAttribute("aria-valuetext", words);
  }

  /* Where a colour sits: round the rim by hue in Colour, top to bottom by temperature in White. */
  function place(next) {
    hex = next;
    const k = kelvinOf(hex);
    if (mode === "white" && !k) mode = "colour";
    if (mode === "white") {
      pos = {x: 0, y: -1 + 2 * (COOLEST_K - k) / (COOLEST_K - WARMEST_K)};
    } else {
      const {h, s} = hexToHsv(hex), a = h * Math.PI / 180;
      pos = {x: s * Math.sin(a), y: -s * Math.cos(a)};
    }
    draw();
  }

  /* Status polls leave the wheel alone while it is held and for a moment after. */
  function settle() {
    clearTimeout(calm);
    calm = setTimeout(() => { if (!dragging) delete host.dataset.dirty; }, HOLD_MS);
  }

  function edit(next, at, kind) {
    hex = next;
    pos = at;
    host.dataset.dirty = "1";
    settle();
    draw();
    host.dispatchEvent(new Event(kind));
  }

  const done = () => { rememberColour(hex); host.dispatchEvent(new Event("change")); };

  /* The outer few pixels count as the edge, so a fingertip still reaches full colour. */
  function at(event) {
    const box = disc.getBoundingClientRect(), r = box.width / 2 - 6;
    let x = (event.clientX - box.left - box.width / 2) / r, y = (event.clientY - box.top - box.height / 2) / r;
    const far = Math.hypot(x, y);
    if (far > 1) { x /= far; y /= far; }
    if (mode === "white") {
      const k = Math.round((COOLEST_K - (y + 1) / 2 * (COOLEST_K - WARMEST_K)) / 50) * 50;
      return [kelvinToHex(k), {x, y}];
    }
    const h = (Math.round(Math.atan2(x, -y) * 180 / Math.PI) + 360) % 360, s = Math.min(1, Math.hypot(x, y));
    const a = h * Math.PI / 180;
    return [hsvToHex(h, s), {x: s * Math.sin(a), y: -s * Math.cos(a)}];
  }

  disc.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    event.preventDefault();
    disc.focus({preventScroll: true});
    disc.setPointerCapture(event.pointerId);
    dragging = true;
    disc.dataset.dragging = "";
    edit(...at(event), "input");
  });
  disc.addEventListener("pointermove", event => { if (dragging) edit(...at(event), "input"); });
  const letGo = () => {
    if (!dragging) return;
    dragging = false;
    delete disc.dataset.dragging;
    settle();
    done();
  };
  disc.addEventListener("pointerup", letGo);
  disc.addEventListener("pointercancel", letGo);
  disc.addEventListener("lostpointercapture", letGo);

  const STEPS = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1]};
  disc.addEventListener("keydown", event => {
    const step = STEPS[event.key];
    if (!step) return;
    event.preventDefault();
    const n = event.shiftKey ? 3 : 1;
    if (mode === "white") {
      place(kelvinToHex(clamp((kelvinOf(hex) || 4000) + (step[0] || step[1]) * 100 * n, WARMEST_K, COOLEST_K)));
    } else {
      const {h, s} = hexToHsv(hex);
      place(hsvToHex((Math.round(h) + step[0] * 5 * n + 360) % 360, clamp(s + step[1] * 0.05 * n, 0, 1)));
    }
    edit(hex, pos, "input");
    done();
  });

  function pickMode(next) {
    if (next === mode) return;
    mode = next;
    if (mode === "colour") return draw();
    place(kelvinToHex(kelvinOf(hex) || 2700));
    edit(hex, pos, "input");
    host.dispatchEvent(new Event("change"));   // lit at once, but not a pick worth keeping under Recent
  }

  function pickRecent(h) {
    if (!kelvinOf(h)) mode = "colour";
    place(h);
    edit(hex, pos, "input");
    done();
  }

  host.showRecent = () => {
    const tiles = recentColours.map(h => {
      const b = button("", "swatch", () => pickRecent(h), `Recent: ${colourName(h)}`);
      b.dataset.hex = h;
      b.style.background = h;
      return b;
    });
    recent.hidden = !tiles.length;
    recent.replaceChildren(make("span", {textContent: "Recent"}), ...tiles);
  };

  Object.defineProperty(host, "value", {
    get: () => hex,
    set: value => {
      if (dragging) return;
      const next = `#${String(value).replace("#", "").toLowerCase()}`;
      if (!/^#[0-9a-f]{6}$/.test(next)) throw new Error(`not a colour: ${value}`);
      if (next !== hex) place(next);
    },
  });
  host.value = host.dataset.value;
  host.showRecent();
}

async function loadMeta(force) {
  if (!force && Date.now() - metaLoadedAt < 30000) return;
  try {
    const meta = await api("/api/wled/meta");
    metaLoadedAt = Date.now();
    boardEffects = meta.effects || [];
    const select = $("wled-fx"), first = new Option("Choose an effect…", "");
    first.disabled = true;
    const options = [first];
    boardEffects.forEach((name, i) => { if (name && name !== "RSVD" && name !== "-") options.push(new Option(name, i)); });
    select.replaceChildren(...options);
    select.value = "";
    select.disabled = options.length < 2;
    const presets = meta.presets || [];
    $("presets").replaceChildren(...presets.map(p => button(p.name, "chip", () => runPreset(p.id))));
    setText($("presets-hint"), meta.offline ? "The board isn't answering, so its lists are empty."
      : presets.length ? "" : "No saved looks yet. Save one in the WLED app and it appears here.");
  } catch (err) {
    setText($("presets-hint"), `Couldn't load the board's lists. ${err.message}`);
  }
}

function runPreset(id) {
  sceneView = "";
  return act("/api/wled/preset", {id});
}

/* ---------- the other lights ---------- */
const BED_LOOKS = {
  blend: "The screen's bottom corner next to the bed, easing into the scene's colour along the strip.",
  mood: "One colour for the whole strip: the scene's strongest.",
  side: "One part of the screen stretched along the strip.",
};

/* What the bed row says, and whether its brightness slider means anything right now. */
function bedView(s, scene) {
  if (!(s.bed_leds > 0)) return {state: "Not set up", lit: false, brightness: false, hint: ""};
  const mode = s.bed_mode;
  if (mode === "off") return {state: "Off", lit: false, brightness: false, hint: ""};
  if (mode === "colour") return {state: "Its own colour", lit: scene !== "off", brightness: true, hint: ""};
  if (mode === "effect") return {state: `Its own effect · ${effectName(s.bed_effect)}`, lit: scene !== "off", brightness: true, hint: ""};
  if (scene === "screen") return {state: "Matches · follows the screen", lit: true, brightness: true, hint: BED_LOOKS[s.bed_look] || ""};
  if (scene === "off") return {state: "Matches · off with everything", lit: false, brightness: false, hint: ""};
  return {state: `Matches · ${scene === "colour" ? "same colour" : "same effect"}`, lit: true, brightness: false,
    hint: "Shows the monitor strip's colour or effect, at the master brightness."};
}

function renderBed(s) {
  if (s.bed_leds == null) return;
  const has = s.bed_leds > 0, scene = held("scene", sceneOf(s)), view = bedView(s, scene);
  const mode = held("bed_mode", s.bed_mode);
  $("bed-controls").hidden = !has;
  $("bed-setup-link").hidden = has;
  setText($("bed-state"), view.state);
  $("bed-icon").dataset.lit = String(view.lit);
  $$("[data-action=bed-mode]").forEach(b => press(b, b.dataset.value === mode));
  $$("[data-action=bed-look]").forEach(b => press(b, b.dataset.value === s.bed_look));
  $$("[data-action=bed-side]").forEach(b => press(b, b.dataset.value === s.bed_side));
  $("bed-screen").hidden = mode !== "screen" || scene !== "screen";
  $("bed-sides").hidden = s.bed_look !== "side";
  $("bed-effect-box").hidden = mode !== "effect";
  $("bed-colour-box").hidden = mode !== "colour";
  $("bed-bri").hidden = !view.brightness;
  setText($("bed-hint"), view.hint);
  $("bed-hint").hidden = !view.hint;
  const colour = `#${String(s.bed_colour).replace("#", "").toLowerCase()}`;
  fillInput($("bed-colour"), colour);
  $$("#bed-swatches .swatch").forEach(b => press(b, b.dataset.hex === colour));
  bedEffectOptions(s.bed_effect);
}

function bedEffectOptions(current) {
  const pick = $("bed-effect"), want = effects.map(fx => [fx.fx, fx.name]);
  if (!want.some(([fx]) => fx === current)) want.push([current, `Effect ${current}`]);
  const key = want.map(w => w.join(":")).join(",");
  if (pick.dataset.options !== key) {
    pick.replaceChildren(...want.map(([fx, name]) => make("option", {value: fx, textContent: name})));
    pick.dataset.options = key;
  }
  if (pick !== document.activeElement) pick.value = String(current);
}

function setBedMode(mode) {
  hold("bed_mode", mode);
  if (state) renderBed(state);
  return actHeld(["bed_mode"], "/api/set", {bed_mode: mode});
}

function renderMouse(s) {
  const m = s.mouse || {};
  setText($("mouse-hint"), !m.connected ? "No Logitech LIGHTSYNC mouse is plugged in."
    : m.permission && m.permission !== "granted"
      ? "Allow Input Monitoring for Python: System Settings › Privacy & Security › Input Monitoring."
      : `${m.name}: takes the colours along the bottom of the screen while it syncs.`);
  $("mouse-icon").dataset.lit = String(!!(m.connected && s.mouse_sync && s.running));
}

function renderMonitorRow(s) {
  const parts = zones.length;
  setText($("monitor-state"), s.wled_reachable === false ? "The board isn't answering"
    : `${s.led_count} LEDs ${s.layout === "frame" ? "around the monitor" : "along the bottom"}` +
      (parts ? ` · ${parts} part${parts === 1 ? "" : "s"} set` : ""));
  $("monitor-state").dataset.tone = s.wled_reachable === false ? "bad" : "";
  $("monitor-icon").dataset.lit = String(sceneOf(s) !== "off" && s.wled_reachable !== false);
}

/* ---------- profiles ---------- */
function renderProfiles(s) {
  if (!s) return;
  const names = Object.keys(s.user_profiles || {}), key = names.join("|");
  if (key !== profilesKey) {
    profilesKey = key;
    $$("#styles [data-mine]").forEach(b => b.remove());
    $("styles").append(...names.map(name => {
      const b = make("button", {type: "button", className: "chip", textContent: name});
      b.dataset.action = "profile";
      b.dataset.value = name;
      b.dataset.mine = "1";
      return b;
    }));
  }
  const active = held("profile", s.profile);
  $$("[data-action=profile]").forEach(b => press(b, b.dataset.value === active));
  const mine = names.includes(s.profile);
  setText($("style-hint"), STYLES[active] || (mine ? `${s.profile}: your saved look. Tune changes it; save to keep.` : "A look of your own. Save it in Tune to keep it."));
  renderLooks(s);
}

/* The sound, as an add-on to the chosen style: its level, the live bass meter and Calm. */
function renderSound(s) {
  const style = s.style || "none", level = held("audio", (s.audio_react || {})[style] || "off");
  $$("[data-action=audio]").forEach(b => press(b, b.dataset.value === level));
  const audio = s.audio || {};
  setText($("audio-label"), s.calm ? "Follow the sound (Calm is on)" : "Follow the sound");
  $("bass-bar").style.width = pct(audio.hearing ? audio.bass_level : 0);
  setText($("bass-word"), !audio.wanted ? "Not listening" : !audio.hearing ? "Listening: no sound"
    : audio.bpm ? `${audio.bpm} BPM, ${audio.beats} beats` : `${audio.beats} beats`);
  const motion = s.style === "trippy" || s.style === "cinematic" ? s.style : "";
  $("motion-trippy").hidden = motion !== "trippy";
  $("motion-cinematic").hidden = motion !== "cinematic";
  setText($("motion-hint"), motion ? `${lookTitle(motion)}: how it moves.`
    : "Pick Cinematic or Trippy on Home to move the screen's own colours along the strip.");
}

function pickAudio(level) {
  const style = (state && state.style) || "none";
  hold("audio", level);
  if (state) renderSound(state);
  return actHeld(["audio"], "/api/set", {audio_react: `${style}:${level}`});
}

/* Room Studio's controls load the first time it is shown, so the page everyone loads stays light. */
let roomLoaded = false;
function loadRoom() {
  if (roomLoaded) return;
  roomLoaded = true;
  document.body.append(make("script", {src: "/room.js",
    onerror: () => { roomLoaded = false; toast("Couldn't load Room Studio. Try again.", "error"); }}));
}

function showProfileForm() {
  const form = $("form-profile");
  form.hidden = !form.hidden;
  if (!form.hidden) { $("profile-name").value = ""; $("profile-name").focus(); }
}

async function saveProfile() {
  const name = $("profile-name").value.trim();
  if (!name) return false;
  const body = await act("/api/profiles/save", {name}, `Saved as ${name}. It's on Home under Style.`);
  if (body) $("form-profile").hidden = true;
  return body;
}

async function deleteProfile(name) {
  if (!name || !confirm(`Delete the look ${name}? The lights keep looking as they do now.`)) return;
  await act("/api/profiles/delete", {name}, `Deleted ${name}.`);
}

/* ---------- the looks library: pick one, then use, compare, copy, rename or delete it ---------- */
let builtinLooks = {}, lookPick = "", lookForm = "", looksKey = "", expertLoaded = false;
const lookTitle = name => name in STYLES ? name[0].toUpperCase() + name.slice(1) : name;
const lookOf = name => builtinLooks[name] || (state && (state.user_profiles || {})[name]);

function loadLooks() { return load("/api/profiles", b => { builtinLooks = b.builtin_looks; looksKey = ""; renderLooks(state); }); }

function renderLooks(s) {
  if (!s) return;
  const names = [...Object.keys(builtinLooks), ...Object.keys(s.user_profiles || {})];
  if (!names.includes(lookPick)) lookPick = names.includes(s.profile) ? s.profile : names[0] || "";
  const key = [names.join("|"), s.profile, lookPick].join("/");
  if (key === looksKey) return;
  looksKey = key;
  $("look-list").replaceChildren(...names.map(name => {
    const b = button(lookTitle(name) + (name === s.profile ? " · in use" : ""), "chip", () => {
      lookPick = name;
      hideLookExtras();
      renderLooks(state);
    });
    press(b, name === lookPick);
    return b;
  }));
  $("look-rename").disabled = $("look-delete").disabled = lookPick in builtinLooks;
  setText($("looks-sum"), `${names.length - Object.keys(builtinLooks).length} of yours`);
}

function hideLookExtras() { $("form-look").hidden = $("look-diff").hidden = $("look-diff-title").hidden = true; }

function compareLook() {
  const look = lookOf(lookPick) || {}, label = key => FADERS[key]?.label || key;
  const rows = Object.entries(look).filter(([k, v]) => state && String(state[k]) !== String(v))
    .map(([k, v]) => [label(k), `${state[k]} → ${v}`]);
  hideLookExtras();
  $("look-diff-title").hidden = $("look-diff").hidden = false;
  setText($("look-diff-title"), rows.length ? `What ${lookTitle(lookPick)} changes from the lights now:`
    : `${lookTitle(lookPick)} matches the lights now.`);
  fillKv($("look-diff"), rows);
}

function showLookForm(mode) {
  lookForm = mode;
  hideLookExtras();
  $("form-look").hidden = false;
  $("look-name").value = mode === "rename" ? lookPick : `${lookTitle(lookPick)} copy`;
  $("look-name").focus();
}

async function submitLook() {
  const to = $("look-name").value.trim();
  if (!to) return false;
  const body = lookForm === "rename"
    ? await act("/api/profiles/rename", {name: lookPick, to}, `Renamed to ${to}.`)
    : await act("/api/profiles/duplicate", {name: lookPick, to}, `Saved a copy as ${to}.`);
  if (body) { lookPick = to; hideLookExtras(); renderLooks(state); }
  return body;
}

async function resetColours() {
  if (!confirm("Reset warmth, contrast, colour, strip balance and the other colour settings to their defaults? " +
    "Brightness, layout and parts of the strip stay as they are.")) return;
  await load("/api/defaults", defaults => {
    const values = {};
    for (const key of COLOUR_KEYS.filter(k => k in defaults)) {
      values[key] = typeof defaults[key] === "boolean" ? Number(defaults[key]) : defaults[key];
      delete editedAt[key];
    }
    act("/api/set", values, "Colour settings are back to their defaults.");
  });
}

/* ---------- parts of the strip (zones) ---------- */
const partTint = z => z.mode === "solid" ? z.color : PART_TINT[z.mode] || null;
const ledTotal = () => Math.max(1, state ? state.led_count : 1);

/* Takes LEDs a..b out of every part, splitting a part that spans them. Pure. */
function cutOut(list, a, b) {
  const out = [];
  for (const z of list) {
    if (z.end < a || z.start > b) { out.push(z); continue; }
    if (z.start < a) out.push({...z, end: a - 1});
    if (z.end > b) out.push({...z, start: b + 1});
  }
  return out;
}

/* The parts after giving LEDs a..b a mode; "sync" hands them back to the scene. Pure. */
function withPart(list, a, b, part) {
  const rest = cutOut(list, a, b);
  if (part.mode !== "sync") rest.push({...part, start: a, end: b});
  return rest.sort((x, y) => x.start - y.start);
}

function renderZonesFromStatus(s) {
  const json = JSON.stringify(s.zones || []);
  if (json !== zonesJson && !recent(editedAt.zones)) {
    zonesJson = json;
    zones = JSON.parse(json);
    buildParts();
  }
  renderMonitorRow(s);
}

function buildParts() {
  $("zone-list").replaceChildren(...zones.map((z, i) => {
    const dot = make("span", {className: "swatch-dot"});
    dot.style.background = partTint(z) || "var(--well)";
    const text = make("p", {textContent: `LEDs ${z.start}–${z.end} · ${PART_NAMES[z.mode]}` +
      (z.mode === "off" ? "" : ` · ${pct(z.brightness)}`)});
    const badge = make("span", {className: "badge", textContent: z.keep !== false ? "Kept" : "Until restart"});
    return make("li", {}, dot, text, badge,
      button("Edit", "btn small quiet", () => editPart(i), `Edit LEDs ${z.start} to ${z.end}`),
      button("Remove", "btn small", () => { zones.splice(i, 1); partsChanged(); }, `Remove LEDs ${z.start} to ${z.end}`));
  }));
  $("parts-empty").hidden = zones.length > 0;
  $("parts-clear").hidden = zones.length === 0;
  setText($("parts-sum"), zones.length ? `${zones.length} set` : "");
  if (state) renderMonitorRow(state);
  drawPaint();
}

function partsChanged() {
  editedAt.zones = Date.now();
  buildParts();
  pushZones();
}

const pushZones = debounce(async () => {
  const body = await setKey("zones", JSON.stringify(zones));
  if (!body || !body.zones) return;
  zonesJson = JSON.stringify(body.zones);
  if (zonesJson !== JSON.stringify(zones)) { zones = JSON.parse(zonesJson); buildParts(); }
}, 300);

function setPartMode(mode) {
  partMode = mode;
  $$("[data-action=part-mode]").forEach(b => press(b, b.dataset.value === mode));
  $("part-colour-box").hidden = mode !== "solid";
  $("part-bri").hidden = mode === "off" || mode === "sync";
  drawPaint();
}

function setPartKeep(on) {
  partKeep = on;
  press($("part-keep"), on, "aria-checked");
}

function editPart(i) {
  const z = zones[i];
  setPartMode(z.mode);
  if (z.mode === "solid") $("part-colour").value = z.color;
  setFader("part_brightness", z.brightness, true);
  setPartKeep(z.keep !== false);
  select(z.start, z.end);
  $("paint").scrollIntoView({block: "nearest", behavior: "smooth"});
}

function applyPart() {
  if (!selection) return;
  const [a, b] = selection;
  zones = withPart(zones, a, b, {mode: partMode, color: $("part-colour").value,
    brightness: faderValue("part_brightness"), keep: partKeep});
  select(null);
  partsChanged();
  toast(partMode === "sync" ? `LEDs ${a}–${b} follow the scene again.` : `LEDs ${a}–${b} set to ${PART_NAMES[partMode]}.`);
}

function clearParts() {
  if (!confirm("Clear every part? The whole strip will follow the scene.")) return;
  zones = [];
  partsChanged();
}

function drawPaint() {
  const g = canvasFor($("paint"), state);
  if (!g) return;
  const n = ledTotal(), owner = new Array(n).fill(null);
  for (const z of zones) for (let k = z.start; k <= z.end && k < n; k++) owner[k] = z;
  const pick = partMode === "solid" ? $("part-colour").value : PART_TINT[partMode] || null;
  paintCanvas(g, selection ? `LEDs ${selection[0]}–${selection[1]}` : "Drag across the LEDs", i => {
    const picked = selection && i >= selection[0] && i <= selection[1];
    if (picked) return {colour: pick || $("part-colour").value, glow: true, big: true};
    const z = owner[i];
    if (z) return {colour: partTint(z), glow: !!partTint(z)};
    const live = colourAt(i, n, state.running ? state.preview || [] : []);
    return {colour: live && isLit(live) ? live : null, glow: false};
  });
}

function ledAt(event) {
  const g = canvasFor($("paint"), state);
  if (!g) return null;
  const box = g.canvas.getBoundingClientRect(), x = event.clientX - box.left, y = event.clientY - box.top;
  let best = null, bestD = Infinity;
  for (const led of g.geo.leds) {
    const d = Math.hypot(led.at[0] - x, led.at[1] - y);
    if (d < bestD) { best = led.index; bestD = d; }
  }
  return bestD <= Math.max(24, g.geo.size * 4) ? best : null;
}

function select(a, b) {
  selection = a == null ? null : [Math.min(a, b), Math.max(a, b)];
  setText($("paint-sel"), selection ? `LEDs ${selection[0]}–${selection[1]} · ${selection[1] - selection[0] + 1} selected`
    : "Nothing selected");
  $("part-go").disabled = !selection;
  drawPaint();
}

function wirePaint() {
  const canvas = $("paint");
  canvas.addEventListener("pointerdown", event => {
    const led = ledAt(event);
    if (led == null) return;
    dragFrom = led;
    canvas.setPointerCapture(event.pointerId);
    select(led, led);
  });
  canvas.addEventListener("pointermove", event => {
    if (dragFrom == null) return;
    const led = ledAt(event);
    if (led != null) select(dragFrom, led);
  });
  const end = () => { dragFrom = null; };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  $("part-colour").addEventListener("input", drawPaint);
}

/* ---------- settings ---------- */
const OUTPUT_NAMES = {udp: "Wi-Fi", ddp: "Wi-Fi DDP", serial: "USB"};

function renderSettings(s) {
  fillInput($("wled_ip"), s.wled_ip);
  setText($("ip-state"), s.wled_reachable == null ? "" : s.wled_reachable
    ? "The board is answering." : "No answer from a board at this address.");
  $("ip-state").dataset.tone = s.wled_reachable ? "ok" : "bad";
  setText($("sum-controller"), `${s.wled_ip}${s.wled_reachable === false ? " · not answering" : ""}`);
  setText($("led-count"), String(s.led_count));
  fillInput($("led_count"), s.led_count);
  const frame = s.layout === "frame";
  setText($("sum-strip"), `${s.led_count} LEDs · ${frame ? "around the monitor" : "bottom only"}`);
  $$("[data-action=layout]").forEach(b => press(b, b.dataset.value === s.layout));
  $("form-frame").hidden = !frame;
  $("strip-fields").hidden = frame;
  [...EDGE_KEYS, "box_mm_w", "box_mm_h"].forEach(k => fillInput($(k), s[k]));
  renderEdgeSum();
  setText($("test-hint"), frame
    ? "Each side lights its own colour: bottom red, right green, top blue, left white, brightening in the strip's direction. The start LED glows amber. A colour on the wrong side? Fix the counts above."
    : "Lights red at the screen's left edge fading to blue at the right. Backwards? Swap the edges above.");
  setText($("sum-bed"), s.bed_leds > 0 ? `${s.bed_leds} LEDs · up to ${(s.bed_power_ma / 1000).toFixed(1)} A` : "Not set up");
  fillInput($("bed-leds"), s.bed_leds);
  fillInput($("bed-pin"), s.bed_pin);
  fillInput($("bed-amps"), s.bed_power_ma / 1000);
  const urls = (s.urls || [location.origin]).map(u => `${u.replace(/\/$/, "")}/calibrate`);
  if (urls.join() !== urlsKey) {
    urlsKey = urls.join();
    $("cal-urls").replaceChildren(...urls.map(u => make("li", {}, make("code", {textContent: u}))));
  }
  $$("[data-action=output]").forEach(b => press(b, b.dataset.value === s.output));
  $("serial-fields").hidden = s.output !== "serial";
  setText($("sum-capture"), `${OUTPUT_NAMES[s.output] || s.output} · ${s.fps} fps`);
  const monitor = $("monitor_index");
  if (monitor !== document.activeElement && [...monitor.options].some(o => o.value === String(s.monitor_index)))
    monitor.value = String(s.monitor_index);
  fillInput($("http_port"), s.http_port);
  setText($("pin-state"), s.has_pin ? "A PIN is set. Each device asks for it once."
    : "No PIN: anyone on your Wi-Fi can control the lights.");
  setText($("sum-access"), s.has_pin ? "PIN on" : "No PIN");
  $("pin-remove").hidden = !s.has_pin;
  const stable = (s.urls || []).find(u => u.includes(".local"));
  const hint = ipOrigin(location.hostname) && stable
    ? `You opened the hub by its IP address, which can change. Bookmark ${stable} instead, or add that to the Home Screen.`
    : "";
  $("origin-hint").hidden = !hint;
  setText($("origin-hint"), hint);
}

const ipOrigin = host => /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.startsWith("[");

function renderEdgeSum() {
  const n = state ? state.led_count : 0;
  const sum = EDGE_KEYS.reduce((total, k) => total + (parseInt($(k).value, 10) || 0), 0);
  const bad = sum > n || !sum;
  setText($("edge-sum"), sum > n ? `Sides add up to ${sum}, more than the ${n} LEDs on the strip.`
    : sum === n ? `Sides use all ${n} LEDs.` : `Sides use ${sum} of ${n} LEDs; the last ${n - sum} stay dark.`);
  $("edge-sum").dataset.tone = bad ? "bad" : "ok";
  $("frame-save").disabled = bad;
}

async function countStep(path, params) {
  const probe = await act(path, params);
  if (!probe) return;
  wizardLive = !probe.done;
  $("count-ask").hidden = probe.done;
  $("count-start").hidden = !probe.done;
  setText($("count-msg"), probe.message || "Is the very last LED on the strip lit?");
  setText($("count-range"), probe.done ? "" : `Somewhere between ${probe.lo} and ${probe.hi} LEDs.`);
  if (probe.done) {
    toast(probe.message || `LED count set to ${probe.k}.`);
    refresh();
  }
}

async function showMarker(index) {
  markerAt = clamp(Math.round(index), 0, (state ? state.led_count : 1) - 1);
  setText($("marker-at"), String(markerAt).padStart(3, "0"));
  $("marker-box").hidden = false;
  $("marker-start").hidden = true;
  if (await act("/api/wizard/marker", {index: markerAt, led: markerAt})) wizardLive = true;
}

async function stopWizard({force = false, offer = false} = {}) {
  const live = wizardLive;
  wizardLive = false;
  markerAt = null;
  $("marker-box").hidden = $("count-ask").hidden = true;
  $("marker-start").hidden = $("count-start").hidden = false;
  if ((!live && !force) || !await act("/api/wizard/stop")) return;
  await refresh();
  if (offer && state && !state.running)
    toast("Test patterns are off.", "info", {label: "Start sync", run: startSync});
}

/* ---------- board flashing ---------- */
function flashCheck() {
  return load("/api/flash/check", check => {
    const ports = check.ports || [];
    $("flash-port").replaceChildren(...(ports.length ? ports.map(p => new Option(p.replace("/dev/cu.", ""), p))
      : [new Option("No board found: plug it into the Mac by USB", "")]));
    const missing = [!check.esptool && "esptool", !check.pyserial && "pyserial"].filter(Boolean);
    setText($("flash-tools"), missing.length
      ? `The Mac needs ${missing.join(" and ")} first: make venv EXTRAS=flash` : "");
    $("flash-go").disabled = missing.length > 0 || !ports.length;
  });
}

function loadNetworks() {
  return load("/api/flash/networks", list => {
    const options = (list.networks || []).map(n =>
      new Option(n.ssid + (n.current ? " (connected)" : n.likely_5ghz ? " (5 GHz?)" : ""), n.ssid));
    options.push(new Option("Type it in…", MANUAL_SSID));
    $("flash-ssid").replaceChildren(...options);
    if (list.current) $("flash-ssid").value = list.current;
    ssidChanged();
  });
}

function chosenSsid() {
  const value = $("flash-ssid").value;
  return (value === MANUAL_SSID ? $("flash-ssid-manual").value : value).trim();
}

function ssidChanged() {
  const manual = $("flash-ssid").value === MANUAL_SSID;
  $("flash-ssid-manual").hidden = !manual;
  setText($("ssid-hint"), /(^|[^a-z0-9])5\s*-?\s*(g|ghz)([^a-z0-9]|$)/i.test(chosenSsid())
    ? "That looks like a 5 GHz network: pick the 2.4 GHz one."
    : manual ? "Type the exact name, including capitals." : "The board only joins 2.4 GHz networks.");
}

async function startFlash(event) {
  event.preventDefault();
  const port = $("flash-port").value, ssid = chosenSsid(), erase = $("flash-erase").checked;
  if (!port) return toast("Pick the board first.", "error");
  if (!ssid) return toast("Choose your Wi-Fi network.", "error");
  if (erase && !confirm("Erase this board completely and install WLED?\n\nIts firmware, Wi-Fi settings, " +
    "LED count, segments and presets will be lost. This cannot be undone.")) return;
  if (!await act("/api/flash/start", {port, ssid, password: $("flash-pass").value, flash: erase ? 1 : 0})) return;
  $("flash-form").hidden = $("flash-close").hidden = true;
  $("flash-run").hidden = false;
  setText($("flash-step"), "Starting…");
  flashLogSize = -1;
  pollFlash();
}

async function pollFlash() {
  let st;
  try { st = await api("/api/flash/status"); }
  catch (err) {
    // The flash carries on inside the hub, so a dropped connection only delays the readout.
    if (err.kind !== "failed") { setTimeout(pollFlash, 3000); return; }
    reportError(err);
    $("flash-close").hidden = false;
    return;
  }
  $("flash-progress").value = st.progress || 0;
  setText($("flash-step"), st.step || "Working…");
  const log = st.log || [];
  if (log.length !== flashLogSize) {
    flashLogSize = log.length;
    $("flash-log").replaceChildren(...log.map(line => make("div", {className: /fail|error/i.test(line) ? "fail" : "", textContent: line})));
    $("flash-log").scrollTop = $("flash-log").scrollHeight;
  }
  if (st.state === "working") { setTimeout(pollFlash, 1000); return; }
  $("flash-close").hidden = false;
  if (st.state === "done") {
    $("flash-progress").value = 100;
    setText($("flash-step"), `Done: the board joined your Wi-Fi at ${st.ip}.`);
    if (await act("/api/flash/adopt", null, "The hub now uses the new board.")) metaLoadedAt = 0;
  } else if (st.state === "error") {
    setText($("flash-step"), st.error || "Board setup failed.");
    toast(st.error || "Board setup failed.", "error");
  }
}

function bedSetup() {
  return act("/api/bed/setup", {leds: $("bed-leds").value, amps: $("bed-amps").value, pin: $("bed-pin").value},
    "Bed output saved. The board restarts for a few seconds.");
}

/* ---------- pairing another device ---------- */
let pairTargets = [];

async function openPair() {
  const body = await act("/api/pair");
  if (!body) return;
  pairTargets = body.targets;
  $("pair-target").replaceChildren(...pairTargets.map((t, i) =>
    make("option", {value: String(i), textContent: t.label})));
  showPairTarget(0);
  $("pair").showModal();
}

function showPairTarget(index) {
  const target = pairTargets[index];
  setText($("pair-url"), target.url);
  drawQr($("pair-code"), target.modules);
}

/* Whole-pixel modules, centred, with the four-module light margin scanners need. */
function qrLayout(size, count, quiet = 4) {
  const cell = Math.floor(size / (count + 2 * quiet));
  return {cell, offset: Math.floor((size - cell * count) / 2)};
}

function drawQr(canvas, modules) {
  const {cell, offset} = qrLayout(canvas.width, modules.length);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#000";
  modules.forEach((row, y) => [...row].forEach((bit, x) => {
    if (bit === "1") ctx.fillRect(offset + x * cell, offset + y * cell, cell, cell);
  }));
}

async function setPin() {
  const value = $("new-pin").value.trim();
  if (value.length < 4) return toast("Use at least 4 digits.", "error");
  const body = await act("/api/set", {new_pin: value}, "PIN set. Other devices will be asked for it.");
  if (body) { storePin(value); $("new-pin").value = ""; }
  return body;
}

async function removePin() {
  if (confirm("Remove the PIN? Anyone on your Wi-Fi will be able to control the lights.")
    && await act("/api/set", {new_pin: ""}, "PIN removed.")) storePin("");
}

/* ---------- diagnostics ---------- */
const TIMING_STAGES = [["capture", "Screen capture"], ["process", "Edges to LEDs"], ["colour", "Colour"],
  ["smooth", "Smoothing"], ["send", "Send"]];

function renderTiming(s) {
  if (!$("set-diag").open) return;
  const t = s.running ? s.telemetry : null;
  const ms = v => `${Number(v).toFixed(1)} ms`;
  const spread = p => p && p.p50 != null ? `${ms(p.p50)} · ${ms(p.p95)}` : "—";
  const rows = [
    ["Fresh frames", t ? `${Number(s.measured_fps).toFixed(1)} fps` : "—"],
    ["Delivered", t ? `${Number(t.delivered_fps).toFixed(1)} fps` : "—"],
    ["Dropped", t ? String(t.dropped) : "—"],
    ["Screen to send", t ? spread(t.latency_ms) : "—"],
    ...TIMING_STAGES.map(([key, name]) => [name, t ? spread(t.stages_ms[key]) : "—"]),
  ];
  $("timing").replaceChildren(...rows.flatMap(([name, value]) =>
    [make("dt", {textContent: name}), make("dd", {textContent: value})]));
}

function loadMonitors() {
  return load("/api/monitors", body => {
    const list = Array.isArray(body) ? body : body.monitors || [];
    const options = [new Option("Automatic: the external monitor when connected", 0)];
    list.forEach(m => options.push(new Option(`Display ${m.index}: ${m.width}×${m.height}${m.primary ? " (main)" : ""}`, m.index)));
    if (state && state.monitor_index !== 0 && !list.some(m => m.index === state.monitor_index))
      options.push(new Option(`Display ${state.monitor_index} (not connected)`, state.monitor_index));
    $("monitor_index").replaceChildren(...options);
    if (state) $("monitor_index").value = String(state.monitor_index);
  });
}

function loadPorts() {
  return load("/api/serial/ports", info => {
    const ports = info.ports || [], name = p => p.replace("/dev/cu.", "");
    const options = !info.pyserial ? [new Option("USB support missing (make venv EXTRAS=serial)", "")]
      : ports.length ? ports.map(p => new Option(name(p), p)) : [new Option("No USB board found", "")];
    if (info.current && !ports.includes(info.current)) options.push(new Option(`${name(info.current)} (not connected)`, info.current));
    $("serial_port").replaceChildren(...options);
    if (info.current) $("serial_port").value = info.current;
    const bauds = BAUDS.includes(info.baud) ? BAUDS : [...BAUDS, info.baud].sort((a, b) => a - b);
    $("serial_baud").replaceChildren(...bauds.map(b => new Option(b.toLocaleString(), b)));
    $("serial_baud").value = String(info.baud);
    setText($("baud-hint"), info.baud < info.recommended_baud
      ? `Too slow for this many LEDs: use at least ${info.recommended_baud.toLocaleString()}, and set the same in WLED.`
      : "Must match the speed set in WLED › Config › Sync Interfaces.");
  });
}

function loadHealth() {
  return load("/api/diagnostics", d => {
    const rows = [
      ["Screen sync", d.running ? "Running" : "Stopped"],
      ["Capture rate", d.running && d.fps != null ? `${Number(d.fps).toFixed(1)} fps` : "—"],
      ["Board", d.wled_reachable ? "Answering" : "Not answering", d.wled_reachable ? "ok" : "bad"],
      ["LEDs on the board", d.wled_led_count ?? "Unknown"],
      ["Capture size", [].concat(d.capture_size || "—").join("×")],
      ["Last issue", d.last_error ? friendly(d.last_error) : "None", d.last_error ? "bad" : "ok"],
    ];
    fillKv($("health"), rows);
  });
}

function fillKv(list, rows) {
  list.replaceChildren(...rows.flatMap(([name, value, tone]) => {
    const dd = make("dd", {textContent: String(value)});
    if (tone) dd.dataset.tone = tone;
    return [make("dt", {textContent: name}), dd];
  }));
}

/* ---------- backup and restore ---------- */
const BACKUP_GROUPS = {looks: "Looks and picture", layout: "Strip layout and parts",
  calibration: "Colour calibration", connection: "Board, display and output"};
const LOOK_STATES = {new: "new", replaces: "replaces yours", full: "skipped: you have 20 looks"};
const when = iso => new Date(iso).toLocaleString([], {dateStyle: "medium", timeStyle: "short"});
let backupPlan = null;

async function saveBackup(kind) {
  try {
    const file = await api("/api/backup/export", {kind});
    const blob = new Blob([JSON.stringify(file, null, 2)], {type: "application/json"});
    const link = make("a", {href: URL.createObjectURL(blob),
      download: `pudisync-${kind === "looks" ? "looks" : "backup"}-${file.exported_at.slice(0, 10)}.json`});
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (err) { reportError(err); }
}

async function previewBackup(file) {
  $("backup-file").value = "";     // the same file can be opened again
  if (!file) return;
  if (file.size > 256 * 1024) { toast("That file is too big to be a PudiSync backup.", "error"); return; }
  try { showBackupPlan(await api("/api/backup/preview", null, await file.text())); }
  catch (err) { reportError(err); }
}

function showBackupPlan(plan) {
  backupPlan = plan;
  const name = key => FADERS[key]?.label || key;
  const show = v => Array.isArray(v) ? `${v.length} items` : String(v);
  const changing = plan.rows.filter(r => r.status === "change" || r.status === "adjusted");
  const looks = plan.looks.filter(l => l.status === "new" || l.status === "replaces");
  const counts = {looks: looks.length};
  changing.forEach(r => { counts[r.group] = (counts[r.group] || 0) + 1; });
  $("backup-groups").replaceChildren(...Object.keys(BACKUP_GROUPS).filter(g => counts[g]).map(g =>
    make("label", {className: "check"},
      make("input", {type: "checkbox", value: g, checked: plan.default_groups.includes(g)}),
      `${BACKUP_GROUPS[g]} (${counts[g]})`)));
  const of = status => plan.rows.filter(r => r.status === status);
  fillKv($("backup-rows"), [
    ...changing.map(r => [name(r.key), `${show(r.current)} → ${show(r.stored)}` +
      (r.status === "adjusted" ? ` (file says ${show(r.asked)})` : "")]),
    ...plan.looks.filter(l => l.status !== "same").map(l => [`Look: ${l.name}`,
      LOOK_STATES[l.status] || `refused: ${l.reason}`, l.status in LOOK_STATES && l.status !== "full" ? "" : "bad"]),
    ...of("rejected").map(r => [name(r.key), `refused: ${r.reason}`, "bad"]),
    ...of("local").map(r => [r.key, "kept from this hub"]),
    ...of("unknown").map(r => [r.key, "not a setting this hub has"]),
  ]);
  const total = changing.length + looks.length;
  const same = of("same").length + plan.looks.filter(l => l.status === "same").length;
  setText($("backup-sum"), total ? `${total} changes; ${same} already match. Pick what to restore:`
    : `Nothing to change: ${same} already match.`);
  $("backup-apply").disabled = !total;
  $("backup-plan").hidden = false;
}

async function applyBackup() {
  const groups = $$("#backup-groups input:checked").map(i => i.value);
  if (!groups.length) { toast("Tick at least one part to restore.", "error"); return; }
  const body = await act("/api/backup/apply", {id: backupPlan.import_id, groups: groups.join(",")});
  if (!body) return;
  closeBackupPlan();
  settled(body, `Applied ${body.changed.length} settings.`);
}

async function restorePoint(file) {
  const body = await act("/api/backup/restore", {file});
  if (body) settled(body, body.changed.length ? `Restored ${body.changed.length} settings.` : "Those settings were already in place.");
}

function settled(body, message) {
  loadRestorePoints();
  if (body.mismatched.length) toast(`Saved, but ${body.mismatched.join(", ")} did not store as planned.`, "error");
  else toast(message, "info", body.restore_point && {label: "Undo", run: () => restorePoint(body.restore_point)});
}

function closeBackupPlan() { $("backup-plan").hidden = true; backupPlan = null; }

function loadRestorePoints() {
  return load("/api/backup/list", b => $("backup-points").replaceChildren(...b.restore_points.length
    ? b.restore_points.map(p => make("li", {}, make("p", {textContent: when(p.saved_at)}),
        button("Restore", "btn small", () => restorePoint(p.file), `Restore the settings saved ${when(p.saved_at)}`)))
    : [make("li", {}, make("p", {className: "hint", textContent: "None yet."}))]));
}

/* ---------- webcam calibration ---------- */
function loadWebcams() { return load("/api/camera/list", showWebcams); }

function showWebcams(body) {
  // A phone's camera only when picked by hand, as the hub's own default (camrun.pick_camera).
  const pick = $("webcam-pick"), usable = body.cameras.filter(c => !c.builtin);
  const preferred = usable.find(c => !/iphone/i.test(c.name));
  pick.replaceChildren(...usable.map(c => make("option", {value: c.name, textContent: c.name, selected: c === preferred})),
    ...body.cameras.filter(c => c.builtin).map(c =>
      make("option", {value: "", disabled: true, textContent: `${c.name}: built-in, never used`})));
  if (!preferred) pick.prepend(make("option", {value: "", selected: true, textContent: "No webcam plugged in"}));
  $$("[data-action=webcam]").forEach(b => { b.disabled = !pick.value || body.state === "working"; });
  showWebcam(body);
}

function showWebcam(st) {
  const words = {idle: "", working: st.task === "check" ? "Checking: play a video…" : "Calibrating: keep the webcam still…",
    done: st.task === "check" ? "Check finished." : "Calibration finished.", error: "Stopped."};
  setText($("webcam-state"), words[st.state] || "");
  $("webcam-state").dataset.tone = st.state === "done" ? "ok" : st.state === "error" ? "bad" : "";
  $("webcam-log").hidden = !st.log.length;
  setText($("webcam-log"), st.log.join("\n"));
  $("webcam-log").scrollTop = $("webcam-log").scrollHeight;
  clearTimeout(webcamTimer);
  if (st.state === "working") webcamTimer = setTimeout(pollWebcam, 1000);
  else $$("[data-action=webcam]").forEach(b => { b.disabled = !$("webcam-pick").value; });
}

function pollWebcam() { return load("/api/camera/status", showWebcam); }

async function startWebcam(task) {
  const camera = $("webcam-pick").value;
  if (!camera) return;
  $$("[data-action=webcam]").forEach(b => { b.disabled = true; });
  const st = await act("/api/camera/start", {task, camera, seconds: 60});
  if (st) showWebcam(st); else $$("[data-action=webcam]").forEach(b => { b.disabled = false; });
}

/* ---------- navigation and wiring ---------- */
/* `name` is a section (wide screens), a tab (phones) or an old link; either way both are known after. */
function showTab(name) {
  name = MOVED[name] || name;
  const section = name in SECTIONS ? name : FIRST[name] || "overview";
  const tab = name in SECTIONS ? TAB_OF[section] : FIRST[name] ? name : "home";
  const leaving = WIDE.matches ? currentSection === "setup" && section !== "setup" : currentTab === "settings" && tab !== "settings";
  if (leaving && wizardLive) stopWizard({offer: true});
  const moved = section !== currentSection || tab !== currentTab;
  currentSection = section;
  currentTab = tab;
  layout();
  if (!moved) return;
  window.scrollTo(0, 0);
  if (!WIDE.matches && name in SECTIONS && section !== FIRST[tab]) {
    $$(`#tab-${tab} [data-section~="${section}"]`)[0].scrollIntoView({block: "start"});
  }
}

/* Phones: one tab's screen, every card in it. Wide: every screen, only the chosen section's cards. */
function layout() {
  const wide = WIDE.matches;
  if (wide && !currentSection) currentSection = FIRST[currentTab] || "overview";
  TABS.forEach(t => { $(`tab-${t}`).hidden = !wide && t !== currentTab; });
  $$("#screens [data-section]").forEach(el =>
    el.toggleAttribute("data-off", wide && !el.dataset.section.split(" ").includes(currentSection)));
  $$("[data-action=tab]").forEach(b => b.setAttribute("aria-current", b.dataset.value === currentTab ? "page" : "false"));
  $$("[data-action=section]").forEach(b => b.setAttribute("aria-current", b.dataset.value === currentSection ? "page" : "false"));
  setText($("page-title"), wide ? SECTIONS[currentSection] : currentTab[0].toUpperCase() + currentTab.slice(1));
  history.replaceState(null, "", `#${wide ? currentSection : currentTab}`);
  const shown = id => !$(id).closest("[data-off],[hidden]");
  if (shown("preview")) { previewKey = ""; renderPreview(); }
  if (shown("parts") && $("parts").open) drawPaint();
  if (shown("room-cards")) loadRoom();
}

function goTo(target) {
  const [section, id] = target.split(":");
  showTab(WIDE.matches ? section : $(id).closest(".screen").id.slice(4));
  const box = $(id);
  box.open = true;
  box.scrollIntoView({block: "start", behavior: "smooth"});
}

const ACTIONS = {
  "tab": b => showTab(b.dataset.value),
  "section": b => showTab(b.dataset.value),
  "goto": b => goTo(b.dataset.value),
  "power": togglePower,
  "scene": b => pickScene(b.dataset.value),
  "profile": b => pickStyle(b.dataset.value),
  "profile-save": showProfileForm,
  "look-use": () => pickStyle(lookPick),
  "look-compare": compareLook,
  "look-form": b => showLookForm(b.dataset.value),
  "look-delete": () => deleteProfile(lookPick),
  "bed-mode": b => setBedMode(b.dataset.value),
  "bed-look": b => setKey("bed_look", b.dataset.value),
  "bed-side": b => setKey("bed_side", b.dataset.value),
  "part-mode": b => setPartMode(b.dataset.value),
  "part-keep": () => setPartKeep(!partKeep),
  "part-all": () => select(0, ledTotal() - 1),
  "part-apply": applyPart,
  "parts-clear": clearParts,
  "reset-colours": resetColours,
  "webcam": b => startWebcam(b.dataset.value),
  "layout": b => setKey("layout", b.dataset.value),
  "count-start": () => countStep("/api/wizard/count/start"),
  "count-yes": () => countStep("/api/wizard/count/answer", {lit: 1}),
  "count-no": () => countStep("/api/wizard/count/answer", {lit: 0}),
  "wizard-stop": () => stopWizard({force: true, offer: true}),
  "marker-start": () => showMarker(state ? state.left_led : 0),
  "nudge": b => showMarker((markerAt ?? 0) + Number(b.dataset.value)),
  "set-edge": b => setKey(b.dataset.value, markerAt, b.dataset.value === "left_led" ? "Left edge saved." : "Right edge saved."),
  "test-start": async () => { if (await act("/api/wizard/gradient")) wizardLive = true; },
  "open-calibrate": () => window.open(`/calibrate${pin ? `?pin=${encodeURIComponent(pin)}` : ""}`, "_blank", "noopener"),
  "flash-ports": flashCheck,
  "flash-networks": loadNetworks,
  "flash-close": () => { $("flash-run").hidden = true; $("flash-form").hidden = false; flashCheck(); },
  "output": async b => { if (await setKey("output", b.dataset.value)) loadPorts(); },
  "ports": loadPorts,
  "pin-remove": removePin,
  "health": loadHealth,
  "backup-save": b => saveBackup(b.dataset.value),
  "backup-open": () => $("backup-file").click(),
  "backup-apply": applyBackup,
  "backup-cancel": closeBackupPlan,
  "unlock":() => { pinDismissed = false; askPin(); },
  "unlock-cancel": () => { pinDismissed = true; $("unlock").close(); },
  "pair": openPair,
  "theme": b => pickTheme(b.dataset.value),
  "audio": b => pickAudio(b.dataset.value),
  "beat-test": () => act("/api/beat/test"),
  "room-start": startRoom,
  "room-stop": () => act("/api/room/stop"),
  "open-room": () => showTab("room"),
};

function onSwitch(btn) {
  const key = btn.dataset.switch, on = btn.getAttribute("aria-checked") !== "true";
  press(btn, on, "aria-checked");
  editedAt[key] = Date.now();
  setKey(key, on ? 1 : 0);
}

function onSubmit(id, handler) {
  $(id).addEventListener("submit", async event => {
    event.preventDefault();
    if (await handler()) $(id).querySelectorAll("input").forEach(i => delete i.dataset.dirty);
  });
}

function onChange(id, handler) { $(id).addEventListener("change", () => handler($(id).value)); }

function onOpen(id, handler) { $(id).addEventListener("toggle", () => { if ($(id).open) handler(); }); }

function wire() {
  document.addEventListener("click", event => {
    const target = event.target.closest("[data-action],[data-switch]");
    if (!target || target.disabled) return;
    if (target.dataset.switch) onSwitch(target);
    else ACTIONS[target.dataset.action](target);
  });
  document.addEventListener("input", event => {
    if (event.target.matches("form input")) event.target.dataset.dirty = "1";
  });
  EDGE_KEYS.forEach(k => $(k).addEventListener("input", renderEdgeSum));
  onSubmit("form-ip", async () => {
    const body = await setKey("wled_ip", $("wled_ip").value.trim(), "Board address saved.");
    if (body) metaLoadedAt = 0;
    return body;
  });
  onSubmit("form-count", () => setKey("led_count", $("led_count").value, "LED count saved."));
  onSubmit("form-frame", () => act("/api/set", Object.fromEntries(
    [...EDGE_KEYS, "box_mm_w", "box_mm_h"].map(k => [k, Number($(k).value) || 0])), "Layout saved."));
  onSubmit("form-port", () => setKey("http_port", $("http_port").value, `Saved. Restart the hub to use port ${$("http_port").value}.`));
  onSubmit("form-pin", setPin);
  onSubmit("form-profile", saveProfile);
  onSubmit("form-look", submitLook);
  onSubmit("form-bed", bedSetup);
  wirePaint();
  $("colour").addEventListener("input", throttle(() => runColour($("colour").value), 150));
  $("bed-colour").addEventListener("change", () => setKey("bed_colour", $("bed-colour").value.slice(1)));
  onChange("bed-effect", v => setKey("bed_effect", v));
  onChange("wled-fx", v => runEffect(Number(v)));
  onChange("monitor_index", v => setKey("monitor_index", v));
  onChange("serial_port", v => setKey("serial_port", v));
  onChange("pair-target", v => showPairTarget(Number(v)));
  onChange("serial_baud", async v => { if (await setKey("serial_baud", v)) loadPorts(); });
  onOpen("board-more", () => loadMeta());
  onOpen("parts", drawPaint);
  onOpen("webcam", loadWebcams);
  onOpen("flash", () => { flashCheck(); loadNetworks(); });
  onOpen("set-capture", () => { loadMonitors(); loadPorts(); });
  onOpen("set-diag", () => { loadHealth(); if (state) renderTiming(state); });
  onOpen("set-backup", loadRestorePoints);
  // Most people never open these, so their script is fetched on the first open, not with the page.
  onOpen("set-expert", () => {
    if (!expertLoaded) document.body.append(make("script", {src: "/expert.js",
      onerror: () => { expertLoaded = false; toast("Couldn't load the expert settings. Try again.", "error"); }}));
    expertLoaded = true;
  });
  $("backup-file").addEventListener("change", () => previewBackup($("backup-file").files[0]));
  $("set-strip").addEventListener("toggle", () => { if (!$("set-strip").open && wizardLive) stopWizard({offer: true}); });
  $("flash-form").addEventListener("submit", startFlash);
  $("flash-ssid").addEventListener("change", ssidChanged);
  $("flash-ssid-manual").addEventListener("input", ssidChanged);
  $("flash-erase").addEventListener("change", () => {
    $("erase-warn").hidden = !$("flash-erase").checked;
    $("flash-go").textContent = $("flash-erase").checked ? "Erase and install WLED" : "Connect to Wi-Fi";
  });
  $("unlock-form").addEventListener("submit", event => {
    event.preventDefault();
    storePin($("unlock-pin").value.trim());
    $("unlock").close();
    pinDismissed = false;
    poll();
  });
  $("unlock").addEventListener("cancel", () => { pinDismissed = true; });
  window.addEventListener("resize", debounce(() => { previewKey = ""; renderPreview(); drawPaint(); }, 150));
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
  WIDE.addEventListener("change", () => { if (!WIDE.matches) currentTab = TAB_OF[currentSection]; layout(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  window.addEventListener("online", poll);   // Wi-Fi is back: don't wait for the slow offline retry
  DEVICE_LIGHT.addEventListener("change", showTheme);   // on Auto, follow the device switching at sunset
  window.addEventListener("pageshow", event => { if (event.persisted) poll(); });
  window.addEventListener("pagehide", () => {
    if (wizardLive) fetch("/api/wizard/stop", {keepalive: true, cache: "no-store", headers: pinHeaders()});
  });
}

$$("[data-fader]").forEach(buildFader);
$$(".wheel").forEach(buildWheel);
["fx_speed", "fx_intensity"].forEach(k => setFader(k, 128, true));
setFader("part_brightness", 1, true);
buildSwatches($("swatches"), runColour);
buildSwatches($("bed-swatches"), hex => setKey("bed_colour", hex.slice(1)));
buildSwatches($("part-swatches"), hex => { $("part-colour").value = hex; drawPaint(); });
wire();
showTheme();
showTab(location.hash.slice(1));
poll();
