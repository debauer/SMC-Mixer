import { el, KIND, pad } from "./dom.js";

const percent = (raw) => Math.round((raw * 100) / 127);

export function createStrips(container, onSelect) {
  const cards = [];
  const hosts = [];
  for (let channel = 1; channel <= 8; channel++) {
    const fill = el("div", { class: "fill" });
    const cap = el("div", { class: "cap" });
    const parts = {
      fill,
      cap,
      value: el("div", { class: "value mono" }),
      name: el("div", { class: "name" }),
      kind: el("div", { class: "kind" }),
      mute: el("span", { class: "led-tag mono" }, el("i", { class: "led" }), "M"),
      solo: el("span", { class: "led-tag mono" }, el("i", { class: "led" }), "SEL"),
    };
    const card = el(
      "button",
      { type: "button", class: "strip", onclick: () => onSelect(channel) },
      el("div", { class: "num mono" }, pad(channel)),
      el("div", { class: "track", "aria-hidden": "true" }, fill, cap),
      parts.value,
      parts.name,
      parts.kind,
      el("div", { class: "leds" }, parts.mute, parts.solo),
    );
    const keys = el("div", { class: "channel-keys" });
    container.append(el("div", { class: "channel" }, keys, card));
    hosts.push(keys);
    cards.push({ card, parts });
  }

  const update = ({ draft, status, selected }) => {
    const live = new Map((status.strips || []).map((strip) => [strip.channel, strip]));
    cards.forEach(({ card, parts }, index) => {
      const channel = index + 1;
      const slot = draft.strips[index];
      const kind = slot && slot.name ? KIND[slot.kind] : null;
      const strip = live.get(channel);
      const raw = status.faders ? status.faders[index] : null;
      const same = strip && slot && strip.kind === slot.kind && strip.name === slot.name;
      const device = same && strip.available ? strip : null;
      const level = device ? device.volume : raw == null ? 0 : percent(raw);
      card.className = `strip ${kind ? kind.cls : "free"}${(selected.kind === "strip" && selected.id === channel) ? " selected" : ""}${kind && !device ? " offline" : ""}`;
      card.setAttribute("aria-pressed", String((selected.kind === "strip" && selected.id === channel)));
      card.setAttribute("aria-label", `Kanal ${channel}: ${slot && slot.name ? slot.name : "frei"}`);
      parts.fill.style.height = `${kind ? Math.min(100, level) : 0}%`;
      parts.cap.style.bottom = `calc(${raw == null ? level : percent(raw)}% - 8px)`;
      parts.value.textContent = kind && device ? `${device.volume}%` : raw == null ? "-" : `${percent(raw)}%`;
      parts.name.textContent = slot && slot.name ? (device ? device.description : slot.name) : "Frei";
      parts.kind.textContent = kind ? (device && device.owned === false ? "FADER ANGLEICHEN" : kind.label) : "FREI";
      parts.mute.classList.toggle("on", Boolean(device && device.muted));
      parts.solo.classList.toggle("on", Boolean(device && device.default));
    });
  };
  return { update, hosts };
}
