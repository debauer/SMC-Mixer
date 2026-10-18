import { el } from "./dom.js";

// One MQTT switch per physical input: `id` is the ignore-list name (REC_1, KNOB_2, FADER_3), `topic` is shown as a hint.
export function mqttGroup(model, handlers, id, topic) {
  if (!model.draft.mqtt.enabled) return null;
  const on = !model.draft.mqtt_ignore.includes(id);
  return el(
    "div",
    { class: "group" },
    el("h3", { class: "label" }, "MQTT"),
    el(
      "label",
      { class: "check" },
      el("input", { type: "checkbox", checked: on, onchange: (event) => handlers.mqtt(id, event.target.checked) }),
      el("span", {}, el("b", {}, "Publish and accept commands"), el("span", { class: "muted small mono" }, `${model.draft.mqtt.prefix}/${topic}`)),
    ),
  );
}
