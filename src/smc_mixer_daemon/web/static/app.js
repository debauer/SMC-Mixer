import { createButtons, hasLed } from "./buttons.js";
import { createKnobs } from "./knobs.js";
import { el } from "./dom.js";
import { renderEditor } from "./editor.js";
import { createStrips } from "./strips.js";

const $ = (id) => document.getElementById(id);
const model = {
  status: { mixer: {}, strips: [], log: [] },
  saved: null,
  draft: null,
  devices: { devices: [], players: [], player_error: null },
  selected: { kind: "strip", id: 1 },
  offline: false,
  mqtt: { enabled: false, prefix: "smc" },
  token: "",
  tokenFile: null,
};

function storeToken(text) {
  try {
    sessionStorage.setItem("smc-token", text);
  } catch {
    // Storage can be unavailable (private window); the token then stays only in memory.
  }
}

function loadToken() {
  try {
    return sessionStorage.getItem("smc-token") || "";
  } catch {
    return "";
  }
}

async function request(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

const fromConfig = (config) => ({
  soft_takeover: config.soft_takeover,
  player: config.player,
  mqtt_ignore: config.mqtt_ignore,
  strips: Array.from({ length: 8 }, (_, i) => {
    const row = config.strips.find((strip) => strip.channel === i + 1);
    return row ? { kind: row.kind, name: row.name, label: row.label || "" } : null;
  }),
  knobs: Object.fromEntries(config.knobs.map((k) => [k.knob, { action: k.action, channel: k.channel, step: k.step }])),
  buttons: Object.fromEntries(
    config.buttons.map((b) => [
      b.button,
      { action: b.action, channel: b.channel, led: b.led, command: b.command, release_command: b.release_command },
    ]),
  ),
});

const payload = (draft) => ({
  soft_takeover: draft.soft_takeover,
  player: draft.player,
  mqtt_ignore: [...draft.mqtt_ignore].sort(),
  strips: draft.strips.flatMap((slot, i) => (slot && slot.name ? [{ channel: i + 1, kind: slot.kind, name: slot.name, label: slot.label.trim() || null }] : [])),
  knobs: Object.entries(draft.knobs)
    .sort(([a], [b]) => a - b)
    .map(([knob, k]) => ({ knob: Number(knob), action: k.action, channel: k.channel, step: k.step })),
  buttons: Object.entries(draft.buttons)
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([button, b]) => ({
      button,
      action: b.action,
      channel: b.channel,
      led: b.led,
      command: b.action === "shell" ? b.command || "" : null,
      release_command: b.action === "shell" && (b.release_command || "").trim() ? b.release_command : null,
    })),
});

const isDirty = () => JSON.stringify(payload(model.draft)) !== JSON.stringify(payload(model.saved));
const clone = (value) => JSON.parse(JSON.stringify(value));

let updateStrips = null;
let updateButtons = null;
let updateKnobs = null;
let loggedKey = "";

function banner(text) {
  $("banner").hidden = !text;
  $("banner").textContent = text || "";
}

function renderHeader() {
  const { mixer } = model.status;
  const connected = Boolean(mixer.connected) && !model.offline;
  $("device-dot").className = `dot${connected ? " ok" : ""}`;
  $("device-text").textContent = model.offline ? "Daemon offline" : connected ? "USB-MIDI connected" : "not connected";
  $("mode-text").textContent = mixer.mode || "-";
  $("mode-pill").className = `pill mode-${(mixer.mode || "none").toLowerCase()}`;
  $("control-text").textContent = { active: "Control active", suspended: "paused (DAW mode required)", waiting: "" }[mixer.control] || "";
  const dirty = model.draft && isDirty();
  $("dirty").hidden = !dirty;
  $("save").disabled = !dirty;
  $("discard").disabled = !dirty;
}

const PLAYBACK = { Playing: "playing", Paused: "paused", Stopped: "stopped" };

function renderMedia() {
  const { media } = model.status;
  const text = media && media.player ? `${media.player}: ${PLAYBACK[media.state] || "unreachable"}` : "";
  $("media-state").textContent = text;
}

function renderLog() {
  const key = JSON.stringify(model.status.log);
  if (key === loggedKey) return;
  loggedKey = key;
  $("log").replaceChildren(
    ...model.status.log.map((row) => el("li", { class: row.level }, el("span", { class: "time" }, row.t), el("span", {}, row.msg))),
  );
}

function renderLive() {
  if (!model.draft) return;
  updateStrips(model);
  updateButtons(model);
  updateKnobs(model);
  renderHeader();
  renderMedia();
  renderLog();
}

function renderAll() {
  renderLive();
  if (model.draft) renderEditor($("editor"), model, handlers);
}

const edit = (fn) => {
  fn(model.draft);
  renderAll();
};

const stripButtons = (channel) => [`MUTE_${channel}`, `SELECT_${channel}`];

const handlers = {
  strip: (channel, fn) => edit((draft) => fn(draft.strips[channel - 1])),
  kind: (channel, kind) =>
    edit((draft) => {
      const slot = draft.strips[channel - 1];
      draft.strips[channel - 1] = kind ? { kind, name: slot && slot.kind === kind ? slot.name : "", label: slot && slot.kind === kind ? slot.label : "" } : null;
      // Bindings on a strip's own M/S keys conflict while it is assigned; actions on a freed channel dangle.
      if (draft.strips[channel - 1] && draft.strips[channel - 1].name) for (const key of stripButtons(channel)) delete draft.buttons[key];
      if (!draft.strips[channel - 1] || !draft.strips[channel - 1].name) {
        for (const [key, b] of Object.entries(draft.buttons)) if (b.channel === channel) delete draft.buttons[key];
        for (const [key, k] of Object.entries(draft.knobs)) if (k.channel === channel) delete draft.knobs[key];
      }
    }),
  command: (name, key, text) => {
    // Typing must not rebuild the editor (focus), so only the draft and the cards update.
    model.draft.buttons[name][key] = text;
    renderLive();
  },
  token: (text) => {
    model.token = text;
    storeToken(text);
  },
  knob: (n, patch) =>
    edit((draft) => {
      const next = { action: "none", channel: null, step: 2, ...draft.knobs[n], ...patch };
      if (next.action === "none") return void delete draft.knobs[n];
      draft.knobs[n] = next;
    }),
  label: (channel, text) => {
    // Live typing must not rebuild the editor (it would drop focus), so update only the draft and the cards.
    const slot = model.draft.strips[channel - 1];
    slot.label = text;
    renderLive();
    const device = model.devices.devices.find((row) => row.kind === slot.kind && row.name === slot.name);
    document.querySelector(".editor .title").textContent = text.trim() || (device ? device.description : slot.name);
  },
  binding: (name, patch) =>
    edit((draft) => {
      const next = { action: "none", channel: null, led: "off", ...draft.buttons[name], ...patch };
      if (next.action === "none" && !["PLAY", "STOP"].includes(name)) return void delete draft.buttons[name];
      // A newly chosen stateful action shows its state by default; the user can still switch the LED off.
      if (patch.action && patch.action !== (draft.buttons[name] || {}).action) next.led = "active";
      if (next.action === "none" || !hasLed(name)) next.led = "off";
      if (next.led === "active" && !["mute_ch", "select_ch", "mic_mute", "ptt", "out_mute", "play", "pause", "play_pause"].includes(next.action)) next.led = "off";
      if (next.action === "shell") next.command = next.command ?? "";
      else Object.assign(next, { command: null, release_command: null });
      draft.buttons[name] = next;
    }),
  mqtt: (id, on) =>
    edit((draft) => {
      const rest = draft.mqtt_ignore.filter((item) => item !== id);
      draft.mqtt_ignore = on ? rest : [...rest, id];
    }),
  global: (fn) => edit(fn),
};

async function poll() {
  try {
    model.status = await request("/api/status");
    model.offline = false;
  } catch {
    model.offline = true;
  }
  renderLive();
}

async function refreshDevices() {
  try {
    const devices = await request("/api/devices");
    if (JSON.stringify(devices) !== JSON.stringify(model.devices)) {
      model.devices = devices;
      if (model.draft) renderAll();
    }
  } catch (error) {
    model.devices = { devices: [], players: [], player_error: error.message };
  }
}

async function save() {
  try {
    model.saved = fromConfig(await request("/api/config", { method: "PUT", headers: { "Content-Type": "application/json", "X-SMC-Token": model.token }, body: JSON.stringify(payload(model.draft)) }));
    model.draft = clone(model.saved);
    banner("");
  } catch (error) {
    banner(`Saving failed: ${error.message}`);
  }
  renderAll();
}

async function start() {
  const strips = createStrips($("strips"), (channel) => {
    model.selected = { kind: "strip", id: channel };
    renderAll();
  });
  updateStrips = strips.update;
  const knobHosts = Array.from({ length: 8 }, () => $("knobs").appendChild(el("div", { class: "channel-knob" })));
  updateKnobs = createKnobs(knobHosts, (n) => {
    model.selected = { kind: "knob", id: n };
    renderAll();
  });
  updateButtons = createButtons(strips.hosts, $("buttons"), (name) => {
    model.selected = { kind: "button", id: name };
    renderAll();
  });
  $("save").addEventListener("click", save);
  $("discard").addEventListener("click", () => {
    model.draft = clone(model.saved);
    banner("");
    renderAll();
  });
  try {
    const config = await request("/api/config");
    model.tokenFile = config.token_file;
    model.mqtt = config.mqtt;
    model.token = loadToken();
    model.saved = fromConfig(config);
    model.draft = clone(model.saved);
  } catch (error) {
    banner(`Could not load the configuration: ${error.message}`);
    return;
  }
  await Promise.all([poll(), refreshDevices()]);
  renderAll();
  setInterval(poll, 200);
  setInterval(refreshDevices, 5000);
}

start();
