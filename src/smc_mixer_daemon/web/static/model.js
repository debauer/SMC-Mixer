// Draft/saved configuration model shared by the page: conversion from and to the API shape.
export const fromConfig = (config) => ({
  soft_takeover: config.soft_takeover,
  player: config.player,
  mqtt_ignore: config.mqtt_ignore,
  mqtt: { ...config.mqtt, username: config.mqtt.username || "", password: "", clear_password: false },
  strips: Array.from({ length: 8 }, (_, i) => {
    const row = config.strips.find((strip) => strip.channel === i + 1);
    return row ? { kind: row.kind, name: row.name, label: row.label || "" } : null;
  }),
  knobs: Object.fromEntries(config.knobs.map((k) => [k.knob, { action: k.action, channel: k.channel, step: k.step }])),
  buttons: Object.fromEntries(
    config.buttons.map((b) => [
      b.button,
      { action: b.action, channel: b.channel, led: b.led, command: b.command, release_command: b.release_command },
    ]),
  ),
});

// A password is only sent when typed or removed; without a username none can be stored.
function mqttPayload(mqtt) {
  const username = mqtt.username.trim() || null;
  const body = { enabled: mqtt.enabled, host: mqtt.host.trim(), port: mqtt.port, username, tls: mqtt.tls, prefix: mqtt.prefix.trim() };
  if (!username || mqtt.clear_password) body.password = null;
  else if (mqtt.password !== "") body.password = mqtt.password;
  return body;
}

export const payload = (draft) => ({
  soft_takeover: draft.soft_takeover,
  player: draft.player,
  mqtt_ignore: [...draft.mqtt_ignore].sort(),
  mqtt: mqttPayload(draft.mqtt),
  strips: draft.strips.flatMap((slot, i) => (slot && slot.name ? [{ channel: i + 1, kind: slot.kind, name: slot.name, label: slot.label.trim() || null }] : [])),
  knobs: Object.entries(draft.knobs)
    .sort(([a], [b]) => a - b)
    .map(([knob, k]) => ({ knob: Number(knob), action: k.action, channel: k.channel, step: k.step })),
  buttons: Object.entries(draft.buttons)
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([button, b]) => ({
      button,
      action: b.action,
      channel: b.channel,
      led: b.led,
      command: b.action === "shell" ? b.command || "" : null,
      release_command: b.action === "shell" && (b.release_command || "").trim() ? b.release_command : null,
    })),
});

export const clone = (value) => JSON.parse(JSON.stringify(value));
