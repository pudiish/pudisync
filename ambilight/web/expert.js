/* Expert settings: every setting no other screen shows. Fetched the first time
   Setup › Expert settings opens, so the page everyone loads stays light. It runs
   in the page's own scope and builds its controls from the page's helpers. */
"use strict";
(() => {
  const POWER_WARNING = "Power limits protect the strips and their adapter. Set above what the adapter is " +
    "rated for, the strip can overheat or the adapter can fail. Save these limits?";
  const inputs = [];

  FADERS.enrich = {label: "Tone", min: 0, max: 100, scale: 100, fmt: offOr};
  FADERS.adaptive_pace = {label: "Adaptive pace", min: 0, max: 100, scale: 100, fmt: offOr};

  function fader(key, hint) {
    const host = make("div", {className: "fader"});
    host.dataset.fader = key;
    host.dataset.hint = hint;
    buildFader(host);
    if (state) setFader(key, state[key], true);
    return host;
  }

  function numbers(title, hint, fields, warning) {
    const form = make("form", {className: "stack"});
    const grid = make("div", {className: "grid2"}, ...fields.map(([key, label, min, max, step]) => {
      const input = make("input", {type: "number", name: key, id: `x-${key}`, min, max, step: step || 1,
        inputMode: step ? "decimal" : "numeric"});
      inputs.push([key, input]);
      return make("div", {className: "field"}, make("label", {htmlFor: input.id, textContent: label}), input);
    }));
    form.append(make("h3", {textContent: title}), make("p", {className: "hint", textContent: hint}), grid,
      make("button", {className: "btn primary", type: "submit", textContent: `Save ${title.toLowerCase()}`}));
    form.addEventListener("submit", event => { event.preventDefault(); save(form, fields.map(f => f[0]), warning); });
    return form;
  }

  async function save(form, keys, warning) {
    const values = Object.fromEntries(keys.map(k => [k, form.elements[k].value]));
    if (warning && !confirm(warning)) return;
    const body = await act("/api/set", values);
    if (!body) return;
    keys.forEach(k => delete form.elements[k].dataset.dirty);
    toast("Saved.");
    fill();
  }

  const matrix = make("dl", {className: "kv"});
  const reset = button("Remove the matrix", "btn", async () => {
    if (!confirm("Remove the webcam-measured colour matrix? Colour match then uses the strip's datasheet model.")) return;
    if (await act("/api/set", {led_matrix: "[]"}, "Colour matrix removed.")) fill();
  });
  const lock = make("button", {type: "button", className: "switch", textContent: "Protected mode"},
    make("span", {className: "track"}));
  lock.setAttribute("role", "switch");
  lock.lastChild.setAttribute("aria-hidden", "true");
  lock.addEventListener("click", async () => {
    const on = state.unprotected;
    if (await act("/api/set", {unprotected: on ? 0 : 1},
      on ? "Protected: every device now needs the PIN." : "Unprotected: devices on your Wi-Fi need no PIN.")) fill();
  });

  function fill() {
    if (!state) return;
    inputs.forEach(([key, input]) => fillInput(input, state[key]));
    press(lock, !state.unprotected, "aria-checked");
    const m = state.led_matrix || [];
    fillKv(matrix, m.length ? [0, 1, 2].map(r => [`Row ${r + 1}`, m.slice(r * 3, r * 3 + 3).map(v => v.toFixed(3)).join("  ")])
      : [["Matrix", "None: colour match uses the datasheet model"]]);
    reset.hidden = !m.length;
  }

  $("expert").replaceChildren(
    make("p", {className: "hint", textContent: "Everything the other screens leave out. The hub checks every value " +
      "and keeps it within its safe range."}),
    make("h3", {textContent: "Picture engine"}),
    fader("enrich", "How far darks sink and brights lift, without changing any colour. Part of every look."),
    fader("adaptive_pace", "How far the light's speed follows the picture: slower in calm scenes, faster in action."),
    numbers("Mapping", "Where the hub reads the screen. Change these only if the light at the edges looks wrong.", [
      ["band_height", "Edge band (px)", 10, 1000], ["edge_skip", "Edge skipped (px)", 0, 100],
      ["screen_mm_w", "Screen width (mm)", 50, 5000, "any"], ["screen_mm_h", "Screen height (mm)", 50, 5000, "any"],
      ["led_pitch_mm", "LED spacing (mm)", 5, 100, "any"]]),
    numbers("Board wiring", "Change these only if you changed them on the board itself.", [
      ["sync_preset", "Sync preset", 1, 250], ["bed_start", "Bed's first LED", 0, 1500],
      ["wled_port", "Realtime port", 1, 65535], ["ddp_port", "DDP port", 1, 65535],
      ["wled_http_port", "Board web port", 1, 65535]]),
    make("h3", {textContent: "Colour matrix"}),
    make("p", {className: "hint", textContent: "Measured by the webcam calibration and used when Colour match is on."}),
    matrix, reset,
    make("h3", {textContent: "Access"}), lock,
    make("p", {className: "hint", textContent: "On, every device needs the PIN, even at home. Set a PIN first, in Access and devices."}),
    numbers("Power limits", "The monitor strip's peak and long-run current, and the rating of the adapter both strips " +
      "share (0: each strip has its own). The board's own limiter stays on as a backstop.", [
      ["power_limit_ma", "Peak (mA)", 200, 20000], ["sustained_ma", "Long-run (mA)", 200, 20000],
      ["supply_ma", "Adapter (mA)", 0, 50000]], POWER_WARNING));
  fill();
  onOpen("set-expert", fill);
})();
