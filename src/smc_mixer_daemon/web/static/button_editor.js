import { ACTIONS, effective, fixedBy, hasLed, ROWS, TRANSPORT } from "./buttons.js";
import { el } from "./dom.js";

const segment = (options, current, pick) =>
  el(
    "div",
    { class: "segment", role: "group" },
    ...options.map(([id, label]) =>
      el("button", { type: "button", class: id === current ? "on" : "", "aria-pressed": String(id === current), onclick: () => pick(id) }, label),
    ),
  );

export function buttonTitle(name) {
  const match = /^(MUTE|REC|SELECT|SOLO)_(\d)$/.exec(name);
  if (!match) return TRANSPORT.find(([id]) => id === name)[1];
  const row = ROWS.find(([, id]) => id === match[1])[0];
  return `${row} Kanal ${match[2]}`;
}

export function renderButtonEditor(container, model, handlers, general) {
  const { draft } = model;
  const name = model.selected.id;
  const binding = effective(draft, name);
  const fixed = fixedBy(draft, name);
  const action = ACTIONS.find((a) => a.id === binding.action);
  const parts = [
    el("div", { class: "editor-head" }, el("h2", { class: "label sink" }, "TASTE"), el("div", { class: "title" }, buttonTitle(name))),
  ];
  if (fixed) {
    parts.push(el("p", { class: "note" }, `Fest belegt durch Kanal ${fixed.channel}. Gib den Kanal frei, um die Taste anders zu belegen.`));
  } else {
    const assigned = draft.strips.flatMap((slot, i) => (slot && slot.name ? [i + 1] : []));
    parts.push(
      el(
        "div",
        { class: "group" },
        el("h3", { class: "label" }, "AKTION"),
        binding.standard && el("p", { class: "muted small" }, "Standard-Belegung"),
        el(
          "div",
          { class: "actions-grid" },
          ...ACTIONS.map((option) => {
            const on = option.id === binding.action;
            const blocked = option.channel && !assigned.length;
            return el(
              "button",
              { type: "button", class: `target sink${on ? " on" : ""}`, disabled: Boolean(blocked), "aria-pressed": String(on), onclick: () => handlers.binding(name, { action: option.id, channel: option.channel ? assigned[0] : null }) },
              el("span", { class: "target-text" }, el("b", {}, option.label), el("span", { class: "muted small" }, blocked ? "braucht belegten Kanal" : option.hint)),
            );
          }),
        ),
      ),
    );
    if (action && action.channel) {
      parts.push(
        el(
          "div",
          { class: "group" },
          el("h3", { class: "label" }, "KANAL"),
          segment(assigned.map((n) => [n, String(n)]), binding.channel, (n) => handlers.binding(name, { channel: n })),
        ),
      );
    }
    if (action && action.id !== "none" && hasLed(name)) {
      const leds = [["off", "Aus"], ...(action.state ? [["active", "Wenn aktiv"]] : []), ["on", "Immer an"]];
      parts.push(el("div", { class: "group" }, el("h3", { class: "label" }, "LED"), segment(leds, binding.led, (led) => handlers.binding(name, { led }))));
    }
  }
  parts.push(general(model, handlers.global));
  container.replaceChildren(...parts);
}
