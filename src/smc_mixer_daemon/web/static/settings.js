import { el } from "./dom.js";

const check = (title, note, checked, onchange) =>
  el(
    "label",
    { class: "check" },
    el("input", { type: "checkbox", checked, onchange: (event) => onchange(event.target.checked) }),
    el("span", {}, el("b", {}, title), note && el("span", { class: "muted small" }, note)),
  );

const text = (id, title, value, type, placeholder, oninput) =>
  el(
    "label",
    { class: "field", for: id },
    el("span", { class: "label" }, title),
    el("input", { id, type, value, placeholder, autocomplete: "off", spellcheck: "false", oninput: (event) => oninput(event.target.value) }),
  );

export function mqttStatusText(mqtt) {
  if (!mqtt || !mqtt.enabled) return { text: "off", ok: false };
  if (mqtt.connected) return { text: "connected", ok: true };
  return { text: mqtt.error || "connecting", ok: false };
}

export function renderSettings(container, model, handlers) {
  const mqtt = model.draft.mqtt;
  const set = (patch) => handlers.mqttSettings(patch, false);
  const reset = (patch) => handlers.mqttSettings(patch, true);
  const status = mqttStatusText(model.status.mqtt);
  const passwordHint = mqtt.password_set && !mqtt.clear_password ? "unchanged" : "not set";
  container.replaceChildren(
    el("div", { class: "editor-head" }, el("h2", { class: "label sink" }, "SETTINGS"), el("div", { class: "title" }, "MQTT client")),
    el("p", { class: "note" }, "Publishes keys, encoders and faders and accepts LED, volume and mute commands. Changes apply on Save without restarting the daemon."),
    el("p", { class: "mono small", id: "mqtt-detail" }, `Status: ${status.text}`),
    el(
      "div",
      { class: "group" },
      check("Enable MQTT", null, mqtt.enabled, (on) => reset({ enabled: on })),
      text("mqtt-host", "BROKER HOST", mqtt.host, "text", "localhost", (value) => set({ host: value })),
      text("mqtt-port", "PORT", String(mqtt.port), "number", "1883", (value) => set({ port: Number(value) })),
      check("TLS", "Encrypted connection using the system's CA certificates.", mqtt.tls, (on) => reset({ tls: on })),
      text("mqtt-user", "USERNAME (OPTIONAL)", mqtt.username, "text", "none", (value) => set({ username: value })),
      text("mqtt-password", "PASSWORD", mqtt.password, "password", passwordHint, (value) => set({ password: value })),
      mqtt.password_set && check("Remove the stored password", null, mqtt.clear_password, (on) => reset({ clear_password: on })),
      text("mqtt-prefix", "TOPIC PREFIX", mqtt.prefix, "text", "smc", (value) => set({ prefix: value })),
    ),
  );
}
