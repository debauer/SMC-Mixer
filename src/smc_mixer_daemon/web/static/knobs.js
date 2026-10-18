import { el } from "./dom.js";

export const KNOB_ACTIONS = [
  { id: "none", label: "Nicht belegt", hint: "Encoder wird ignoriert" },
  { id: "vol_ch", label: "Kanal-Lautstärke", hint: "relativ, Ziel eines Kanals", channel: true },
  { id: "vol_out", label: "Ausgang laut/leise", hint: "Standard-Ausgang" },
  { id: "vol_in", label: "Eingang laut/leise", hint: "Standard-Eingang" },
];
const LABEL = Object.fromEntries(KNOB_ACTIONS.map((a) => [a.id, a.label]));

export const knobOf = (draft, n) => draft.knobs[n] || { action: "none", channel: null, step: 2 };

export function createKnobs(hosts, onSelect) {
  const cells = hosts.map((host, index) => {
    const label = el("span", { class: "knob-label" });
    const dial = el("span", { class: "dial mono", "aria-hidden": "true" }, "\u00b1");
    const card = el("button", { type: "button", class: "knob", onclick: () => onSelect(index + 1) }, dial, label);
    host.append(card);
    return { card, dial, label };
  });
  let seen = null;
  return ({ draft, status, selected }) => {
    const turn = status.knob || { last: 0, delta: 0, turns: 0 };
    const flash = seen !== null && turn.turns !== seen && cells[turn.last - 1];
    seen = turn.turns;
    cells.forEach(({ card, label }, index) => {
      const binding = knobOf(draft, index + 1);
      const used = binding.action !== "none";
      const on = selected.kind === "knob" && selected.id === index + 1;
      label.textContent = used ? `${LABEL[binding.action]}${binding.channel ? ` ${binding.channel}` : ""}` : "Frei";
      card.className = `knob${card.classList.contains("flash") ? " flash" : ""}${used ? " used" : ""}${on ? " selected" : ""}`;
      card.setAttribute("aria-pressed", String(on));
      card.setAttribute("aria-label", `Encoder ${index + 1}: ${label.textContent}`);
    });
    if (flash) {
      flash.dial.textContent = turn.delta > 0 ? "\u21bb" : "\u21ba";
      flash.card.classList.remove("flash");
      void flash.card.offsetWidth;
      flash.card.classList.add("flash");
    }
  };
}
