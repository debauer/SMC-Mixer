import { el } from "./dom.js";

const CH = [1, 2, 3, 4, 5, 6, 7, 8];
export const ROWS = [
  ["M", "MUTE"],
  ["S", "SOLO"],
  ["R", "REC"],
  ["SEL", "SELECT"],
];
export const TRANSPORT = [
  ["PLAY", "Play"],
  ["STOP", "Pause"],
  ["RECORD", "Rec"],
  ["REWIND", "<<"],
  ["FAST_FORWARD", ">>"],
  ["BANK_LEFT", "Bank <"],
  ["BANK_RIGHT", "Bank >"],
  ["UP", "Hoch"],
  ["DOWN", "Runter"],
  ["LEFT", "Links"],
  ["RIGHT", "Rechts"],
];

export const ACTIONS = [
  { id: "none", label: "Nicht belegt", hint: "Taste wird ignoriert" },
  { id: "mute_ch", label: "Kanal stumm", hint: "Ziel eines Kanals", channel: true, state: true },
  { id: "select_ch", label: "Kanal wählen", hint: "Gerät als Standard", channel: true, state: true },
  { id: "mic_mute", label: "Mikro stumm", hint: "alle Eingänge", state: true },
  { id: "out_mute", label: "Ausgänge stumm", hint: "alle Ausgänge", state: true },
  { id: "ptt", label: "Push-to-Talk", hint: "Mikro nur beim Halten", state: true },
  { id: "out_cycle", label: "Ausgang wechseln", hint: "Ausgangs-Kanäle rotieren" },
  { id: "play_pause", label: "Play / Pause", hint: "MPRIS umschalten", state: true },
  { id: "play", label: "Play", hint: "MPRIS", state: true },
  { id: "pause", label: "Pause", hint: "MPRIS", state: true },
  { id: "next", label: "Nächster Titel", hint: "MPRIS" },
  { id: "previous", label: "Vorheriger Titel", hint: "MPRIS" },
];
const LABEL = Object.fromEntries(ACTIONS.map((a) => [a.id, a.label]));

export const hasLed = (name) => /^(REC|SOLO|MUTE|SELECT)_/.test(name) || ["REWIND", "FAST_FORWARD", "STOP", "PLAY", "RECORD"].includes(name);

const stripOf = (name) => {
  const match = /^(MUTE|SELECT)_(\d)$/.exec(name);
  return match ? { channel: Number(match[2]), action: match[1] === "MUTE" ? "mute_ch" : "select_ch" } : null;
};

/** Channel whose strip owns this button, when that strip is assigned in the draft. */
export function fixedBy(draft, name) {
  const strip = stripOf(name);
  const slot = strip && draft.strips[strip.channel - 1];
  return slot && slot.name ? strip : null;
}

export function effective(draft, name) {
  const fixed = fixedBy(draft, name);
  if (fixed) return { action: fixed.action, channel: fixed.channel, led: "active" };
  if (draft.buttons[name]) return draft.buttons[name];
  if (name === "PLAY") return { action: "play", channel: null, led: "off", standard: true };
  if (name === "STOP") return { action: "pause", channel: null, led: "off", standard: true };
  return { action: "none", channel: null, led: "off" };
}

/** `hosts[i]` holds the keys of channel i+1 (inside its channel column); `transport` the keys below. */
export function createButtons(hosts, transport, onSelect) {
  const cells = new Map();
  const make = (name, tag, spoken = tag) => {
    const led = el("i", { class: "led" });
    const label = el("span", { class: "btn-label" });
    const card = el("button", { type: "button", class: "key", onclick: () => onSelect(name) }, el("span", { class: "key-tag mono" }, tag, led), label);
    cells.set(name, { card, led, label, tag: spoken });
    return card;
  };
  CH.forEach((channel, index) => {
    for (const [tag, row] of ROWS) hosts[index].append(make(`${row}_${channel}`, tag, `${tag} Kanal ${channel}`));
  });
  for (const [name, tag] of TRANSPORT) transport.append(make(name, tag));

  let seen = null;
  return function update({ draft, status, selected }) {
    const keys = status.keys || { held: [], last: "", presses: 0 };
    const flash = seen !== null && keys.presses !== seen && cells.get(keys.last);
    seen = keys.presses;
    for (const [name, { card, led, label, tag }] of cells) {
      const binding = effective(draft, name);
      const fixed = fixedBy(draft, name);
      const used = binding.action !== "none";
      let text = used ? LABEL[binding.action] : "Frei";
      if (binding.channel) text += ` ${binding.channel}`;
      label.textContent = text;
      card.className = `key${card.classList.contains("flash") ? " flash" : ""}${used ? " used" : ""}${fixed ? " fixed" : ""}${selected.kind === "button" && selected.id === name ? " selected" : ""}`;
      card.setAttribute("aria-pressed", String(selected.kind === "button" && selected.id === name));
      card.setAttribute("aria-label", `Taste ${tag}: ${text}`);
      led.classList.toggle("on", Boolean(status.leds && status.leds[name]));
      card.classList.toggle("pressed", keys.held.includes(name));
    }
    if (flash) {
      // Restart the animation so quick repeated presses flash again.
      flash.card.classList.remove("flash");
      void flash.card.offsetWidth;
      flash.card.classList.add("flash");
    }
  };
}
