import { renderButtonEditor } from "./button_editor.js";
import { renderKnobEditor } from "./knob_editor.js";
import { el, KIND, pad } from "./dom.js";

const segment = (options, current, pick) =>
  el(
    "div",
    { class: "segment", role: "group" },
    ...options.map(([id, label]) =>
      el("button", { type: "button", class: id === current ? "on" : "", "aria-pressed": String(id === current), onclick: () => pick(id) }, label),
    ),
  );

function targets({ draft, devices, selected }, slot, change) {
  const taken = new Map();
  draft.strips.forEach((other, index) => {
    if (other && other.name && index + 1 !== selected) taken.set(`${other.kind}\0${other.name}`, index + 1);
  });
  const rows = devices.devices.filter((device) => device.kind === slot.kind);
  if (slot.name && !rows.some((device) => device.name === slot.name)) {
    rows.unshift({ kind: slot.kind, name: slot.name, description: "unavailable", missing: true });
  }
  if (!rows.length) return el("p", { class: "muted small" }, "No devices found.");
  return el(
    "div",
    { class: "targets" },
    ...rows.map((device) => {
      const owner = taken.get(`${device.kind}\0${device.name}`);
      const on = device.name === slot.name;
      const badge = owner ? `Channel ${owner}` : device.default ? "Default" : device.missing ? "missing" : "";
      return el(
        "button",
        { type: "button", class: `target ${KIND[slot.kind].cls}${on ? " on" : ""}`, disabled: Boolean(owner), "aria-pressed": String(on), onclick: () => change((s) => (s.name = device.name)) },
        el("i", { class: "dot" }),
        el("span", { class: "target-text" }, el("b", {}, device.description), el("span", { class: "mono muted small" }, device.name)),
        badge && el("span", { class: "mono small badge" }, badge),
      );
    }),
  );
}

export function general({ draft, devices }, update) {
  const players = [...devices.players];
  if (draft.player && !players.includes(draft.player)) players.unshift(draft.player);
  const select = el(
    "select",
    { id: "player", onchange: (event) => update((d) => (d.player = event.target.value || null)) },
    el("option", { value: "" }, "None (media keys off)"),
    ...players.map((name) => el("option", { value: name, selected: name === draft.player }, name)),
  );
  return el(
    "div",
    { class: "group" },
    el("h3", { class: "label" }, "GENERAL"),
    el(
      "label",
      { class: "check" },
      el("input", { type: "checkbox", checked: draft.soft_takeover, onchange: (event) => update((d) => (d.soft_takeover = event.target.checked)) }),
      el("span", {}, el("b", {}, "Soft-Takeover"), el("span", { class: "muted small" }, "The fader only takes over once it reaches the current volume.")),
    ),
    el("label", { class: "field", for: "player" }, el("span", { class: "label" }, "MEDIA PLAYER (PLAY / PAUSE)"), select),
    devices.player_error && el("p", { class: "muted small" }, `Player list unavailable: ${devices.player_error}`),
  );
}

export function renderEditor(container, model, handlers) {
  if (model.selected.kind === "knob") return renderKnobEditor(container, model, handlers, general);
  if (model.selected.kind === "button") return renderButtonEditor(container, model, handlers, general);
  const { draft, devices } = model;
  const selected = model.selected.id;
  const slot = draft.strips[selected - 1];
  const kind = slot ? KIND[slot.kind] : null;
  const device = slot && devices.devices.find((row) => row.kind === slot.kind && row.name === slot.name);
  const title = slot && slot.name ? slot.label.trim() || (device ? device.description : slot.name) : "Free";
  const change = (fn) => handlers.strip(selected, fn);
  const parts = [
    el("div", { class: "editor-head" }, el("h2", { class: `label ${kind ? kind.cls : ""}` }, `CHANNEL ${pad(selected)}`), el("div", { class: "title" }, title)),
    el(
      "div",
      { class: "group" },
      el("h3", { class: "label" }, "SOURCE TYPE"),
      segment([["sink", "Output"], ["source", "Input"], ["none", "Free"]], slot ? slot.kind : "none", (id) =>
        handlers.kind(selected, id === "none" ? null : id),
      ),
    ),
  ];
  if (slot) {
    parts.push(
      el(
        "label",
        { class: "field", for: "label" },
        el("span", { class: "label" }, "LABEL"),
        el("input", {
          id: "label",
          type: "text",
          maxlength: "40",
          value: slot.label,
          placeholder: device ? device.description : "automatic from target",
          oninput: (event) => handlers.label(selected, event.target.value),
        }),
      ),
    );
    parts.push(el("div", { class: "group" }, el("h3", { class: "label" }, "TARGET"), targets(model, slot, change)));
  }
  parts.push(general(model, handlers.global));
  container.replaceChildren(...parts);
}
