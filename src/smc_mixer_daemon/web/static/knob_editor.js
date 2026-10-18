import { mqttGroup } from "./mqtt_editor.js";
import { el } from "./dom.js";
import { knobOf, KNOB_ACTIONS } from "./knobs.js";

const segment = (options, current, pick) =>
  el(
    "div",
    { class: "segment", role: "group" },
    ...options.map(([id, label]) =>
      el("button", { type: "button", class: id === current ? "on" : "", "aria-pressed": String(id === current), onclick: () => pick(id) }, label),
    ),
  );

export function renderKnobEditor(container, model, handlers, general) {
  const { draft } = model;
  const n = model.selected.id;
  const binding = knobOf(draft, n);
  const action = KNOB_ACTIONS.find((a) => a.id === binding.action);
  const assigned = draft.strips.flatMap((slot, i) => (slot && slot.name ? [i + 1] : []));
  const parts = [
    el("div", { class: "editor-head" }, el("h2", { class: "label sink" }, "ENCODER"), el("div", { class: "title" }, `Encoder ${n}`)),
    el("p", { class: "note" }, "The encoder only sends steps (−/+), no position. Each step changes the volume by the chosen step size; muting is left untouched."),
    el(
      "div",
      { class: "group" },
      el("h3", { class: "label" }, "ACTION"),
      el(
        "div",
        { class: "actions-grid" },
        ...KNOB_ACTIONS.map((option) => {
          const on = option.id === binding.action;
          const blocked = option.channel && !assigned.length;
          return el(
            "button",
            { type: "button", class: `target sink${on ? " on" : ""}`, disabled: Boolean(blocked), "aria-pressed": String(on), onclick: () => handlers.knob(n, { action: option.id, channel: option.channel ? assigned[0] : null }) },
            el("span", { class: "target-text" }, el("b", {}, option.label), el("span", { class: "muted small" }, blocked ? "needs an assigned channel" : option.hint)),
          );
        }),
      ),
    ),
  ];
  if (action && action.channel) {
    parts.push(el("div", { class: "group" }, el("h3", { class: "label" }, "CHANNEL"), segment(assigned.map((c) => [c, String(c)]), binding.channel, (c) => handlers.knob(n, { channel: c }))));
  }
  if (action && action.id !== "none") {
    parts.push(el("div", { class: "group" }, el("h3", { class: "label" }, "STEP SIZE PER DETENT"), segment([1, 2, 5, 10].map((s) => [s, `${s} %`]), binding.step, (s) => handlers.knob(n, { step: s }))));
  }
  parts.push(mqttGroup(model, handlers, `KNOB_${n}`, `knob/${n}`), general(model, handlers.global));
  container.replaceChildren(...parts.filter(Boolean));
}
