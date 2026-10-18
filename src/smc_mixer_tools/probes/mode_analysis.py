"""Offline search for bits distinguishing independently confirmed device modes."""

from dataclasses import dataclass


@dataclass
class ModeSample:
    name: str
    mode: str
    stage: int
    first: bytes
    second: bytes
    verified: bool = False


def candidates(samples: list[ModeSample]) -> list[tuple[int, int, int, int]]:
    """Return (offset, mask, DAW bit value, USER bit value), requiring two stages per mode."""
    groups = {mode: [s for s in samples if s.verified and s.mode == mode] for mode in ("DAW", "USER")}

    def complete_stages(group: list[ModeSample]) -> set[int]:
        return {
            s.stage
            for s in group
            if {"before", "after", "idle"} <= {item.name.rsplit("_", 1)[-1] for item in group if item.stage == s.stage}
        }

    if any(len(complete_stages(group)) < 2 for group in groups.values()):
        return []
    length = len(groups["DAW"][0].first)
    if any(len(data) != length for group in groups.values() for s in group for data in (s.first, s.second)):
        raise ValueError("Snapshot sizes differ")
    found = []
    for offset in range(length):
        for bit in range(8):
            mask = 1 << bit
            values = {
                mode: {data[offset] & mask for s in group for data in (s.first, s.second)}
                for mode, group in groups.items()
            }
            if len(values["DAW"]) == len(values["USER"]) == 1 and values["DAW"] != values["USER"]:
                found.append((offset, mask, next(iter(values["DAW"])), next(iter(values["USER"]))))
    return found


def report(samples: list[ModeSample], kind: int, address: int) -> str:
    lines = [
        "DAW/USER mode: bit candidates (mode address not yet verified)",
        f"Memory type: {kind}; start address: 0x{address:X}",
        "",
    ]
    for mode in ("DAW", "USER"):
        stages = {s.stage for s in samples if s.verified and s.mode == mode}
        lines.append(f"{mode}: {len(stages)} runs confirmed by the MUTE-1 protocol")
    found = candidates(samples)
    if found:
        lines.append("\nAddress  Mask   DAW     USER    Bit")
        for offset, mask, daw, user in found:
            lines.append(
                f"0x{address + offset:04X}   0x{mask:02X}   0x{daw:02X}    0x{user:02X}    {mask.bit_length() - 1}"
            )
        lines.append("\nBoth paired snapshots and all before/after/idle samples must match within each mode.")
    else:
        lines.append("\nNo reliable bit candidates in the confirmed measurements so far.")
    lines.extend(
        [
            "At least two complete, confirmed runs per mode are required.",
            "A match may be a mirrored value or other mode-dependent firmware data.",
            "Confirm candidates with further mode changes and after a device restart.",
            "No match does not rule out other memory areas or encodings.",
        ]
    )
    return "\n".join(lines) + "\n"
