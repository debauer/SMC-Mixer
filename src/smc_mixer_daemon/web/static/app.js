import { createButtons, hasLed } from "./buttons.js";
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
};

async function request(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

const fromConfig = (config) => ({
  soft_takeover: config.soft_takeover,
  player: config.player,
  strips: Array.from({ length: 8 }, (_, i) => {
    const row = config.strips.find((strip) => strip.channel === i + 1);
    return row ? { kind: row.kind, name: row.name, label: row.label || "" } : null;
  }),
  buttons: Object.fromEntries(config.buttons.map((b) => [b.button, { action: b.action, channel: b.channel, led: b.led }])),
});

const payload = (draft) => ({
  soft_takeover: draft.soft_takeover,
  player: draft.player,
  strips: draft.strips.flatMap((slot, i) => (slot && slot.name ? [{ channel: i + 1, kind: slot.kind, name: slot.name, label: slot.label.trim() || null }] : [])),
  buttons: Object.entries(draft.buttons)
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([button, b]) => ({ button, action: b.action, channel: b.channel, led: b.led })),
});

const isDirty = () => JSON.stringify(payload(model.draft)) !== JSON.stringify(payload(model.saved));
const clone = (value) => JSON.parse(JSON.stringify(value));

let updateStrips = null;
let updateButtons = null;
let loggedKey = "";

function banner(text) {
  $("banner").hidden = !text;
  $("banner").textContent = text || "";
}

function renderHeader() {
  const { mixer } = model.status;
  const connected = Boolean(mixer.connected) && !model.offline;
  $("device-dot").className = `dot${connected ? " ok" : ""}`;
  $("device-text").textContent = model.offline ? "Daemon offline" : connected ? "USB-MIDI verbunden" : "nicht verbunden";
  $("mode-text").textContent = mixer.mode || "-";
  $("mode-pill").className = `pill mode-${(mixer.mode || "none").toLowerCase()}`;
  $("control-text").textContent = { active: "Steuerung aktiv", suspended: "pausiert (DAW-Modus nötig)", waiting: "" }[mixer.control] || "";
  const dirty = model.draft && isDirty();
  $("dirty").hidden = !dirty;
  $("save").disabled = !dirty;
  $("discard").disabled = !dirty;
}

const PLAYBACK = { Playing: "spielt", Paused: "pausiert", Stopped: "gestoppt" };

function renderMedia() {
  const { media } = model.status;
  const text = media && media.player ? `${media.player}: ${PLAYBACK[media.state] || "nicht erreichbar"}` : "";
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
      }
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
      draft.buttons[name] = next;
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
    model.saved = fromConfig(await request("/api/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload(model.draft)) }));
    model.draft = clone(model.saved);
    banner("");
  } catch (error) {
    banner(`Speichern fehlgeschlagen: ${error.message}`);
  }
  renderAll();
}

async function start() {
  const strips = createStrips($("strips"), (channel) => {
    model.selected = { kind: "strip", id: channel };
    renderAll();
  });
  updateStrips = strips.update;
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
    model.saved = fromConfig(await request("/api/config"));
    model.draft = clone(model.saved);
  } catch (error) {
    banner(`Konfiguration konnte nicht geladen werden: ${error.message}`);
    return;
  }
  await Promise.all([poll(), refreshDevices()]);
  renderAll();
  setInterval(poll, 200);
  setInterval(refreshDevices, 5000);
}

start();
