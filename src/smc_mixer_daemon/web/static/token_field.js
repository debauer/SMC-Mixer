import { el } from "./dom.js";

// The access token guards changes that run code or reroute data (shell commands, MQTT broker).
export function tokenField(model, handlers, why) {
  const hint = model.tokenFile ? `contents of ${model.tokenFile}` : "daemon token";
  return el(
    "label",
    { class: "field", for: "token" },
    el("span", { class: "label" }, "ACCESS TOKEN"),
    el("input", { id: "token", type: "password", autocomplete: "off", value: model.token, placeholder: hint, oninput: (event) => handlers.token(event.target.value) }),
    el("span", { class: "muted small" }, `${why} ${hint}.`),
  );
}
