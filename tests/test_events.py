from __future__ import annotations

from smc_mixer import Button, ButtonEvent, FaderEvent, KnobEvent, Layer, MidiProtocol, UnknownEvent
from smc_mixer.events import Decoder


def decode_all(*messages: list[int]) -> list[object]:
    decoder = Decoder()
    return [decoder.decode(bytes(m)) for m in messages]


def test_daw_buttons() -> None:
    assert decode_all([0x90, 16, 127], [0x90, 16, 0], [0x80, 94, 0]) == [
        ButtonEvent(Button.MUTE_1, True, Layer.DAW),
        ButtonEvent(Button.MUTE_1, False, Layer.DAW),
        ButtonEvent(Button.PLAY, False, Layer.DAW),
    ]


def test_daw_knob_steps() -> None:
    assert decode_all([0xB0, 16, 1], [0xB0, 23, 65], [0xB0, 18, 67]) == [
        KnobEvent(1, Layer.DAW, delta=1),
        KnobEvent(8, Layer.DAW, delta=-1),
        KnobEvent(3, Layer.DAW, delta=-3),
    ]


def test_daw_fader_pitch_bend() -> None:
    assert decode_all([0xE0, 0x00, 0x7F], [0xE7, 0x00, 0x00]) == [
        FaderEvent(1, 127, Layer.DAW),
        FaderEvent(8, 0, Layer.DAW),
    ]


def test_cc_buttons_including_shared_ccs() -> None:
    # CC 20 is MUTE 1, CC 30 is SOLO 3 but also knob 1, CC 47 is SELECT 4 but also fader 8
    events = decode_all([0xB0, 20, 127], [0xB0, 20, 0], [0xB0, 30, 127], [0xB0, 30, 0], [0xB0, 47, 127], [0xB0, 60, 0])
    assert events == [
        ButtonEvent(Button.MUTE_1, True, Layer.CC),
        ButtonEvent(Button.MUTE_1, False, Layer.CC),
        ButtonEvent(Button.SOLO_3, True, Layer.CC, ambiguous=True),
        ButtonEvent(Button.SOLO_3, False, Layer.CC, ambiguous=True),
        ButtonEvent(Button.SELECT_4, True, Layer.CC, ambiguous=True),
        ButtonEvent(Button.RIGHT, False, Layer.CC),
    ]


def test_cc_knob_absolute() -> None:
    assert decode_all([0xB0, 30, 1], [0xB0, 30, 2], [0xB0, 37, 5]) == [
        KnobEvent(1, Layer.CC, value=1),
        KnobEvent(1, Layer.CC, value=2),
        KnobEvent(8, Layer.CC, value=5),
    ]


def test_cc_fader_reaching_ends_is_marked_ambiguous() -> None:
    # captured: fader 2 moved up to 127 and back to 0 in small steps
    seq = [87, 99, 113, 124, 127, 125, 30, 7, 1, 0]
    events = decode_all(*[[0xB0, 41, v] for v in seq])
    assert events == [FaderEvent(2, v, Layer.CC, ambiguous=v in (0, 127)) for v in seq]


def test_protocol_is_explicit_and_legacy_layer_is_still_readable() -> None:
    assert KnobEvent(1, Layer.DAW, delta=1).protocol == MidiProtocol.MCU
    assert KnobEvent(1, Layer.CC, value=1).protocol == MidiProtocol.CC
    assert KnobEvent(1, Layer.SHIFT, value=1).protocol == MidiProtocol.CC
    assert Layer("shift") == Layer.SHIFT
    assert Decoder().decode(bytes([0xB0, 30, 50])).layer == Layer.CC


def test_unknown_messages() -> None:
    assert decode_all([0x91, 16, 127], [0xB0, 70, 1], [0xF8]) == [
        UnknownEvent(bytes([0x91, 16, 127])),
        UnknownEvent(bytes([0xB0, 70, 1])),
        UnknownEvent(bytes([0xF8])),
    ]
