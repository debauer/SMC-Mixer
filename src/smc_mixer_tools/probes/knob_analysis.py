"""Offline comparison of repeated RAM samples against absolute knob CC values."""

from dataclasses import dataclass


@dataclass
class Sample:
    name: str
    first: bytes
    second: bytes
    knob: int | None = None
    value: int | None = None
    source: tuple[int, int] | None = None  # One-based MIDI channel, CC number.


def summarize(samples: list[Sample], address: int) -> str:
    baseline = samples[0]
    noise = {i for i, (a, b) in enumerate(zip(baseline.first, baseline.second, strict=True)) if a != b}
    lines = [
        "Knob RAM comparison (candidates, addresses not verified)",
        f"Start address: 0x{address:X}; {len(baseline.first)} bytes; {len(noise)} unstable baseline bytes.",
        "",
        "Changes relative to the first sample; unstable bytes are counted separately:",
    ]
    for sample in samples[1:]:
        changes = []
        unstable = 0
        for offset, (old, new, repeat) in enumerate(zip(baseline.first, sample.first, sample.second, strict=True)):
            if offset in noise or new != repeat:
                unstable += 1
            elif old != new:
                changes.append(f"0x{address + offset:04X}:{old:02X}->{new:02X}")
        lines.append(f"{sample.name}: {len(changes)} stable changes, {unstable} unstable bytes")
        lines.append("  " + (" ".join(changes) or "no stable changes"))
    lines.extend(["", "Candidates for directly stored absolute 7-bit knob values:"])
    for knob in range(1, 9):
        measurements = [s for s in samples if s.knob == knob and s.value is not None]
        if not measurements:
            lines.append(f"Knob {knob}: not measured")
            continue
        if len({s.value for s in measurements}) < 2:
            lines.append(f"Knob {knob}: too few distinct reference values")
            continue
        candidates = []
        for offset in range(len(baseline.first)):
            absolute = address + offset
            if offset in noise or 0x326 <= absolute < 0x33E:
                continue  # Known fader table, not a knob-value table (firmware 011).
            expected = None
            matches = True
            for sample in samples:
                if sample.knob == knob and sample.value is not None:
                    expected = sample.value
                if expected is not None and not sample.first[offset] == sample.second[offset] == expected:
                    matches = False
                    break
            if matches:
                candidates.append(f"0x{absolute:04X}")
        lines.append(f"Knob {knob}: {', '.join(candidates) or 'no candidate in this range'}")
    lines.extend(
        [
            "",
            "Candidates should remain unchanged in later measurements until that knob is moved again.",
            "For confirmation, compare a fresh script run and a device restart. A match alone does",
            "not prove the field's meaning. No match does not rule out other memory areas or formats.",
        ]
    )
    return "\n".join(lines) + "\n"
