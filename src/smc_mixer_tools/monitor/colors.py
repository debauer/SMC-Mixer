"""Semantic colors for the terminal front panel."""

import curses
import re

TOKENS = re.compile(r"[┏┓┗┛┣┫━┃]+|█|\*[^*]+\*|\b(?:ONLINE|OFFLINE|DEMO|DAW|USER)\b")


def init_colors() -> dict[str, int]:
    if not curses.has_colors():
        return {}
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        # Some color terminals cannot preserve the user's default background.
        background = curses.COLOR_BLACK
    colors = [
        ("frame", curses.COLOR_CYAN),
        ("fader", curses.COLOR_GREEN),
        ("online", curses.COLOR_GREEN),
        ("offline", curses.COLOR_RED),
        ("demo", curses.COLOR_YELLOW),
        ("mute", curses.COLOR_YELLOW),
        ("solo", curses.COLOR_CYAN),
        ("record", curses.COLOR_RED),
        ("select", curses.COLOR_MAGENTA),
        ("pressed", curses.COLOR_BLUE),
        ("daw", curses.COLOR_CYAN),
        ("user", curses.COLOR_MAGENTA),
    ]
    # Reuse color pairs so the display also works on basic eight-color terminals.
    pairs: dict[int, int] = {}
    palette = {}
    for role, foreground in colors:
        if foreground not in pairs:
            pair = len(pairs) + 1
            curses.init_pair(pair, foreground, background)
            pairs[foreground] = pair
        palette[role] = curses.color_pair(pairs[foreground]) | (0 if role == "frame" else curses.A_BOLD)
    return palette


def color_spans(text: str) -> list[tuple[int, str, str]]:
    spans = []
    for match in TOKENS.finditer(text):
        token = match.group()
        if token == "█":
            role = "fader"
        elif token.startswith("*"):
            label = token.strip("* ")
            role = {
                "M": "mute",
                "S": "solo",
                "R": "record",
                "REC": "record",
                "SEL": "select",
                ">": "online",
                "||": "mute",
            }.get(label, "pressed")
        elif token in ("ONLINE", "OFFLINE", "DEMO", "DAW", "USER"):
            role = token.lower()
        else:
            role = "frame"
        spans.append((match.start(), token, role))
    return spans
