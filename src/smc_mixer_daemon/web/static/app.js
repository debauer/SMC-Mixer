import { el } from "./dom.js";
import { renderEditor } from "./editor.js";
import { createStrips } from "./strips.js";

const $ = (id) => document.getElementById(id);
const model = {
  status: { mixer: {}, strips: [], log: [] },
  saved: null,
  draft: null,
  devices: { devices: [], players: [], player_error: null },
  selected: 1,
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
    return row ? { kind: row.kind, name: row.name } : null;
  }),
});

const payload = (draft) => ({
  soft_takeover: draft.soft_takeover,
  player: draft.player,
  strips: draft.strips.flatMap((slot, i) => (slot && slot.name ? [{ channel: i + 1, kind: slot.kind, name: slot.name }] : [])),
});

const isDirty = () => JSON.stringify(payload(model.draft)) !== JSON.stringify(payload(model.saved));
const clone = (value) => JSON.parse(JSON.stringify(value));

let updateStrips = null;
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
  renderHeader();
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

const handlers = {
  strip: (channel, fn) => edit((draft) => fn(draft.strips[channel - 1])),
  kind: (channel, kind) =>
    edit((draft) => {
      const slot = draft.strips[channel - 1];
      draft.strips[channel - 1] = kind ? { kind, name: slot && slot.kind === kind ? slot.name : "" } : null;
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
  updateStrips = createStrips($("strips"), (channel) => {
    model.selected = channel;
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
  setInterval(poll, 500);
  setInterval(refreshDevices, 5000);
}

start();
