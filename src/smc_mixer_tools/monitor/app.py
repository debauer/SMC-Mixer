"""Curses monitor. No LED or other device-state writes are performed."""

from __future__ import annotations

import curses
import logging
import math
import time
from itertools import islice

from smc_mixer.controls import Button, DeviceMode
from smc_mixer.errors import SmcMixerError
from smc_mixer.events import ButtonEvent, KnobEvent, Layer
from smc_mixer.mixer import SmcMixer
from smc_mixer_tools.monitor.colors import color_spans, init_colors
from smc_mixer_tools.monitor.state import MonitorState
from smc_mixer_tools.monitor.view import WIDTH, render


class HistoryHandler(logging.Handler):
    """Keep backend warnings out of the terminal's full-screen display."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        # Logging may originate from the reconnect thread; drain under the handler lock.
        self.messages.append(self.format(record))

    def drain(self, state: MonitorState) -> None:
        self.acquire()
        try:
            for message in self.messages:
                state.record(message)
            self.messages.clear()
        finally:
            self.release()


def demo_step(state: MonitorState, step: int) -> None:
    mode = DeviceMode.DAW if step % 64 < 32 else DeviceMode.USER
    changed = mode != state.mode
    state.set_mode(mode)
    if changed and mode == DeviceMode.USER:
        state.knobs = [KnobEvent(knob, Layer.CC, value=(step + knob * 13) % 128) for knob in range(1, 9)]
    for channel in range(1, 9):
        value = round((math.sin(step / 8 + channel / 2) + 1) * 63.5)
        state.faders[channel - 1] = value
    layer = Layer.DAW if mode == DeviceMode.DAW else Layer.CC
    state.apply(ButtonEvent(Button((step // 2) % 32), step % 2 == 0, layer))
    if mode == DeviceMode.DAW:
        state.apply(KnobEvent(step % 8 + 1, layer, delta=1 if step % 16 < 8 else -1))
    else:
        state.apply(KnobEvent(step % 8 + 1, layer, value=step % 128))


def draw(screen: curses.window, state: MonitorState, palette: dict[str, int] | None = None) -> None:
    lines = render(state)
    height, width = screen.getmaxyx()
    screen.erase()
    if height < len(lines) + 1 or width < WIDTH + 1:
        lines = [
            f"Terminal too small: need at least {WIDTH + 1} columns x {len(lines) + 1} rows.",
            f"Current size: {width} x {height}. Resize or press q to quit.",
        ]
    for y, text in enumerate(lines[: max(0, height - 1)]):
        screen.addnstr(y, 0, text, max(0, width - 1))
        if palette:
            for x, token, role in color_spans(text):
                if x < width - 1:
                    screen.addnstr(y, x, token, width - 1 - x, palette[role])
    screen.refresh()


def run_screen(screen: curses.window, state: MonitorState, mixer: SmcMixer | None, logs: HistoryHandler) -> None:
    screen.timeout(33)
    screen.keypad(True)
    palette = init_colors()
    next_demo = time.monotonic()
    step = 0
    while True:
        if mixer is not None:
            # Bound each batch so heavy MIDI traffic cannot starve rendering or quit input.
            for event in islice(mixer.events(timeout=0), 256):
                state.apply(event)
            state.sync(mixer.mode, mixer.faders, mixer.user_knobs)
            state.connected = mixer.connected
        elif time.monotonic() >= next_demo:
            demo_step(state, step)
            step += 1
            next_demo = time.monotonic() + 0.2
        logs.drain(state)
        draw(screen, state, palette)
        if screen.getch() in (ord("q"), ord("Q"), 3):
            return


def run_monitor(demo: bool = False) -> None:
    state = MonitorState(demo=demo)
    handler = HistoryHandler()
    logger = logging.getLogger("smc_mixer")
    old_handlers, old_propagate = logger.handlers[:], logger.propagate
    logger.handlers, logger.propagate = [handler], False
    try:
        if demo:
            curses.wrapper(run_screen, state, None, handler)
        else:
            with SmcMixer.open() as mixer:
                try:
                    mixer.refresh_state(timeout=0.2)
                except SmcMixerError as exc:
                    state.record(f"Initial values unavailable: {exc}")
                state.connected = mixer.connected
                curses.wrapper(run_screen, state, mixer, handler)
    finally:
        logger.handlers, logger.propagate = old_handlers, old_propagate
        handler.close()
