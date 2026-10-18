import { ACTIONS, effective, fixedBy, hasLed, ROWS, TRANSPORT } from "./buttons.js";
import { mqttGroup } from "./mqtt_editor.js";
import { el } from "./dom.js";
import { tokenField } from "./token_field.js";

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
  return `${row} channel ${match[2]}`;
}

const field = (id, title, rows, value, placeholder, oninput) =>
  el(
    "label",
    { class: "field", for: id },
    el("span", { class: "label" }, title),
    el("textarea", { id, class: "code", rows: String(rows), spellcheck: "false", placeholder, oninput: (event) => oninput(event.target.value) }, value || ""),
  );

function shellEditor(model, name, binding, handlers) {
  return el(
    "div",
    { class: "group" },
    field("command", "COMMAND (/bin/sh -c)", 4, binding.command, "e.g. notify-send Hello", (text) => handlers.command(name, "command", text)),
    field("release", "COMMAND ON RELEASE (OPTIONAL)", 2, binding.release_command, "optional", (text) => handlers.command(name, "release_command", text)),
    tokenField(model, handlers, "Required to save changed commands, so other programs on this computer cannot inject commands. Find it in"),
  );
}

export function renderButtonEditor(container, model, handlers, general) {
  const { draft } = model;
  const name = model.selected.id;
  const binding = effective(draft, name);
  const fixed = fixedBy(draft, name);
  const action = ACTIONS.find((a) => a.id === binding.action);
  const parts = [
    el("div", { class: "editor-head" }, el("h2", { class: "label sink" }, "KEY"), el("div", { class: "title" }, buttonTitle(name))),
  ];
  if (fixed) {
    parts.push(el("p", { class: "note" }, `Fixed by channel ${fixed.channel}. Free the channel to assign this key differently.`));
  } else {
    const assigned = draft.strips.flatMap((slot, i) => (slot && slot.name ? [i + 1] : []));
    parts.push(
      el(
        "div",
        { class: "group" },
        el("h3", { class: "label" }, "ACTION"),
        binding.standard && el("p", { class: "muted small" }, "Default assignment"),
        el(
          "div",
          { class: "actions-grid" },
          ...ACTIONS.map((option) => {
            const on = option.id === binding.action;
            const blocked = option.channel && !assigned.length;
            return el(
              "button",
              { type: "button", class: `target sink${on ? " on" : ""}`, disabled: Boolean(blocked), "aria-pressed": String(on), onclick: () => handlers.binding(name, { action: option.id, channel: option.channel ? assigned[0] : null }) },
              el("span", { class: "target-text" }, el("b", {}, option.label), el("span", { class: "muted small" }, blocked ? "needs an assigned channel" : option.hint)),
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
          el("h3", { class: "label" }, "CHANNEL"),
          segment(assigned.map((n) => [n, String(n)]), binding.channel, (n) => handlers.binding(name, { channel: n })),
        ),
      );
    }
    if (action && action.id !== "none" && hasLed(name)) {
      const leds = [["off", "Off"], ...(action.state ? [["active", "When active"]] : []), ["on", "Always on"]];
      parts.push(el("div", { class: "group" }, el("h3", { class: "label" }, "LED"), segment(leds, binding.led, (led) => handlers.binding(name, { led }))));
    }
  }
  if (!fixed && action && action.shell) parts.push(shellEditor(model, name, binding, handlers));
  parts.push(mqttGroup(model, handlers, name, `button/${name}`), general(model, handlers.global));
  container.replaceChildren(...parts.filter(Boolean));
}
