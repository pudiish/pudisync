/* Room Studio: the scene cards, your own scenes and their editor. Fetched the
   first time Room Studio shows, so the page everyone loads stays light. It runs
   in the page's own scope and builds its controls from the page's helpers. */
"use strict";
(() => {
  const EDIT = [["motion_speed", "Motion speed", 0, 200], ["colour_intensity", "Colour intensity", 0, 100],
    ["warp_strength", "Warp", 0, 100], ["texture_amount", "Texture", 0, 100], ["brightness", "Scene brightness", 0, 100]];
  const LEVELS = [["off", "Off"], ["subtle", "Subtle"], ["balanced", "Balanced"], ["expressive", "Expressive"]];
  let builtins = [], drawn = "", editing = "";

  FADERS.room_brightness = {label: "Room brightness", min: 0, max: 100, scale: 100, fmt: pct};
  FADERS.room_speed = {label: "Motion speed", min: 0, max: 300, step: 5, scale: 100, fmt: pct};
  FADERS.room_intensity = {label: "Colour intensity", min: 0, max: 100, scale: 100, fmt: pct};

  function controls() {
    const faders = ["room_brightness", "room_speed", "room_intensity"].map(key => {
      const host = make("div", {className: "fader"});
      host.dataset.fader = key;
      buildFader(host);
      if (state) setFader(key, state[key], true);
      return host;
    });
    const label = make("p", {className: "label", id: "room-audio-label", textContent: "Follow the sound"});
    const seg = make("div", {className: "seg quad", id: "room-audio"}, ...LEVELS.map(([value, text]) => {
      const b = button(text, "", () => setKey("room_audio", value));
      b.dataset.level = value;
      return b;
    }));
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-labelledby", label.id);
    const reset = button("Reset to default", "btn", () =>
      act("/api/room/reset", {id: (state && state.room_scene) || ""}, "Room controls reset."));
    $("room-controls").replaceChildren(make("h2", {textContent: "Controls"}), ...faders, label, seg,
      make("p", {className: "hint", textContent: "The sound only ever adds to the scene; with it off the scene runs on its own."}),
      reset);
  }
  controls();

  const gradient = scene => `linear-gradient(90deg, ${scene.palette.map(s => `${s.hex} ${Math.round(s.position * 100)}%`).join(", ")})`;
  const scenesOf = s => [...builtins, ...Object.values(s.room_custom || {}).map(c => ({...c, custom: true}))];

  function card(scene, active) {
    const bar = make("span", {className: "scene-bar"});
    bar.style.background = gradient(scene);
    const b = make("button", {type: "button", className: "tile scene"}, bar, make("b", {textContent: scene.name}),
      make("span", {className: "about", textContent: scene.description || "One of your scenes."}));
    press(b, scene.id === active);
    b.addEventListener("click", () => act("/api/room/start", {scene: scene.id}));
    return b;
  }

  function editor(scene) {
    const box = make("div", {className: "stack"});
    if (!scene.custom) {
      box.append(make("p", {className: "hint", textContent: "Built-in scenes stay as they are: duplicate one to change its colours and motion."}));
      return box;
    }
    const stops = scene.palette.slice(0, -1);
    const colours = make("div", {className: "row"}, ...stops.map((stop, i) => {
      const input = make("input", {type: "color", value: stop.hex.toLowerCase(), ariaLabel: `Colour ${i + 1}`});
      input.addEventListener("change", () => sendPalette(scene.id, colours));
      return input;
    }));
    box.append(make("p", {className: "label", textContent: "Colours"}), colours);
    for (const [key, label, min, max] of EDIT) {
      const name = `scene_${key}`;
      FADERS[name] = {label, min, max, scale: 100, local: true, fmt: pct,
        apply: v => act("/api/room/edit", {id: scene.id, [key]: v})};
      const host = make("div", {className: "fader"});
      host.dataset.fader = name;
      buildFader(host);
      setFader(name, scene.parameters[key], true);
      box.append(host);
    }
    return box;
  }

  function sendPalette(id, row) {
    const hexes = [...row.querySelectorAll("input")].map(i => i.value.slice(1));
    return act("/api/room/edit", {id, palette: hexes.join(",")});
  }

  async function rename(scene) {
    const name = prompt("Name for this scene", scene.name);
    if (name && name.trim()) await act("/api/room/edit", {id: scene.id, name: name.trim()}, "Renamed.");
  }

  async function remove(scene) {
    if (confirm(`Delete ${scene.name}? This cannot be undone.`)) await act("/api/room/delete", {id: scene.id}, `Deleted ${scene.name}.`);
  }

  async function exportScene(scene) {
    try {
      const body = await api("/api/room/export", {id: scene.id});
      const link = make("a", {download: `${scene.id}.json`,
        href: URL.createObjectURL(new Blob([JSON.stringify(body, null, 2)], {type: "application/json"}))});
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    } catch (err) { reportError(err); }
  }

  async function importScene(file) {
    if (!file) return;
    try {
      const body = await api("/api/room/import", {}, await file.text());
      toast("Scene added to your scenes.");
      render(body);
    } catch (err) { reportError(err); }
  }

  function mine(s, scene) {
    const picker = make("input", {type: "file", accept: "application/json,.json", hidden: true});
    picker.addEventListener("change", () => importScene(picker.files[0]));
    const actions = make("div", {className: "row"},
      button("Duplicate", "btn", () => act("/api/room/duplicate", {id: scene.id}, "Copied to your scenes.")),
      button("Export", "btn", () => exportScene(scene)),
      button("Import", "btn", () => picker.click()));
    if (scene.custom) {
      actions.append(button("Rename", "btn", () => rename(scene)), button("Delete", "btn", () => remove(scene)));
    }
    const yours = Object.values(s.room_custom || {}).length;
    return [make("h2", {textContent: "My scenes"}),
      make("p", {className: "hint", textContent: `${scene.name}${scene.custom ? ", one of yours" : ", built in"}. `
        + `${yours} of your own. Files hold colours and motion only: never code or addresses.`}),
      actions, picker, make("p", {className: "label", textContent: "Advanced"}), editor(scene)];
  }

  window.renderRoom = s => {
    $$("#room-audio button").forEach(b => press(b, b.dataset.level === s.room_audio));
    if (!builtins.length) return;
    const all = scenesOf(s), active = all.find(c => c.id === s.room_scene) || all[0];
    const key = JSON.stringify([s.room_scene, s.room_custom]);
    if (key === drawn) return;
    drawn = key;
    $("room-cards").replaceChildren(...all.map(c => card(c, active.id)));
    // Rebuild the editor only for a different scene, so a fader being dragged is not replaced under the finger.
    if (editing !== active.id || !document.activeElement || !$("room-mine").contains(document.activeElement)) {
      editing = active.id;
      $("room-mine").replaceChildren(...mine(s, active));
    }
  };

  load("/api/room/scenes", body => {
    builtins = body.scenes.filter(c => !c.custom);
    drawn = "";
    if (state) window.renderRoom(state);
  });
})();
